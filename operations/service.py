import hashlib
import json
from datetime import UTC, datetime, timedelta, timezone
from uuid import uuid4

from fastapi import HTTPException
from psycopg.types.json import Jsonb

from .auth import require
from .models import OrgPolicy


def now():
    return datetime.now(UTC)


def stamp(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def fail(message, code=409):
    raise HTTPException(code, message)


def lookup(db, tenant, i, lock=False):
    row = db.execute(
        "SELECT body FROM incidents WHERE tenant=%s AND id=%s"
        + (" FOR UPDATE" if lock else ""),
        (tenant, i),
    ).fetchone()
    if not row:
        fail("Incident unavailable", 404)
    return row["body"]


def get_policy(db, tenant):
    row = db.execute(
        "SELECT version,policy FROM organizations WHERE tenant=%s", (tenant,)
    ).fetchone()
    return row or {"version": 0, "policy": OrgPolicy().model_dump()}


def present(doc):
    eta = doc["forecast"]
    if eta["at"]:
        eta["status"] = (
            "overdue"
            if stamp(eta["at"]) < now() and doc["state"] not in ("resolved", "closed")
            else "confirmed"
        )
    return doc


def timer(db, doc, due):
    doc["next_update"] = due.isoformat()
    db.execute(
        """INSERT INTO schedules (tenant,incident_id,due_at) VALUES (%s,%s,%s)
        ON CONFLICT (tenant,incident_id) DO UPDATE SET due_at=excluded.due_at,pending=NULL,
        escalation_at=NULL,escalated=false""",
        (doc["tenant"], doc["id"], due),
    )


def event(db, doc, actor, kind, data, source=None):
    ts = now().isoformat()
    payload = dict(
        id=str(uuid4()),
        incident_id=doc["id"],
        version=doc["version"],
        kind=kind,
        author=actor.sub,
        origin=source or actor.client,
        fact_at=data.get("fact_at", ts),
        recorded_at=ts,
        data=data,
    )
    db.execute(
        "INSERT INTO events VALUES (%s,%s,%s,%s,%s)",
        (doc["tenant"], doc["id"], payload["id"], doc["version"], Jsonb(payload)),
    )


def write(actor, key, i, version, kind, model):
    from .db import connect

    require(
        actor,
        *(["admin"] if kind == "org-policy" else ["operator", "coordinator", "admin"]),
    )
    data = model.model_dump(mode="json")
    digest = hashlib.sha256(
        json.dumps([str(i), version, kind, data, actor.client], sort_keys=True).encode()
    ).hexdigest()
    with connect() as db:
        # Serialize matching retries before reading the receipt; unrelated commands proceed.
        db.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s,1))",
            (json.dumps([actor.tenant, actor.sub, key]),),
        )
        receipt = db.execute(
            "SELECT * FROM commands WHERE tenant=%s AND actor=%s AND key=%s",
            (actor.tenant, actor.sub, key),
        ).fetchone()
        if receipt:
            if digest != receipt["digest"]:
                fail("Key already used for a different command")
            return receipt["response"]
        # Policy changes and duplicate-graph changes must serialize with other commands in this tenant.
        exclusive = kind in ("org-policy", "duplicate")
        lock = "pg_advisory_xact_lock" if exclusive else "pg_advisory_xact_lock_shared"
        db.execute(f"SELECT {lock}(hashtextextended(%s,2))", (actor.tenant,))
        org = get_policy(db, actor.tenant)
        if kind == "org-policy":
            if org["version"] != version:
                fail("Policy version changed", 412)
            result = {"version": version + 1, "policy": data}
            db.execute(
                "INSERT INTO organizations VALUES (%s,%s,%s) ON CONFLICT (tenant) DO UPDATE SET policy=excluded.policy,version=excluded.version",
                (actor.tenant, Jsonb(data), version + 1),
            )
            db.execute(
                "INSERT INTO policy_audit (tenant,version,author,body) VALUES (%s,%s,%s,%s)",
                (actor.tenant, version + 1, actor.sub, Jsonb(data)),
            )
        else:
            result = mutate(db, actor, i, version, kind, data, org["policy"])
        db.execute(
            "INSERT INTO commands VALUES (%s,%s,%s,%s,%s)",
            (actor.tenant, actor.sub, key, digest, Jsonb(result)),
        )
        return result


def mutate(db, actor, i, version, kind, data, org):
    if kind == "create":
        if data["severity"] not in org["severities"]:
            fail("Unknown severity", 422)
        if len({j["id"] for j in data["journeys"]}) != len(data["journeys"]):
            fail("Duplicate journey", 422)
        doc = dict(
            data,
            id=str(uuid4()),
            tenant=actor.tenant,
            version=1,
            state="investigation",
            duplicate_of=None,
            rooms=[],
            references=[],
            last_update=None,
            next_update=None,
            forecast=dict(at=None, author=None, recorded_at=None, status="unknown"),
            policy_override=None,
            pause=None,
        )
        db.execute(
            "INSERT INTO incidents VALUES (%s,%s,%s)",
            (actor.tenant, doc["id"], Jsonb(doc)),
        )
        timer(db, doc, now() + timedelta(minutes=org["interval_minutes"]))
    else:
        doc = lookup(db, actor.tenant, i, True)
        if version != doc["version"]:
            fail("Incident version changed", 412)
        policy = doc["policy_override"] or org
        if kind == "simulator/messages":
            require(actor, "simulator")
            room = next(
                (
                    r
                    for r in doc["rooms"]
                    if all(r[k] == data[k] for k in ("provider", "room_id"))
                ),
                None,
            )
            if not room or room["state"] != "open":
                fail("Open room required")
            if data["update"]:
                apply_update(db, doc, data["update"], policy, actor)
                data["fact_at"] = data["update"]["fact_at"]
        else:
            from .commands import apply

            apply(db, actor, doc, kind, data, policy, org)
        doc["version"] += 1
    event(
        db,
        doc,
        actor,
        kind,
        data,
        source=("simulator." + data["provider"])
        if kind == "simulator/messages"
        else None,
    )
    present(doc)
    db.execute(
        "UPDATE incidents SET body=%s WHERE tenant=%s AND id=%s",
        (Jsonb(doc), actor.tenant, doc["id"]),
    )
    return doc


def apply_update(db, doc, data, policy, actor):
    if doc["state"] in ("resolved", "closed"):
        fail("Reopen before providing an operational update")
    responder = policy["responder"] or doc["coordinator"]
    if actor.sub != responder and not actor.roles.intersection(
        ("coordinator", "admin")
    ):
        fail("Assigned responder required", 403)
    clock = now()
    if stamp(data["fact_at"]) > clock:
        fail("Fact is in the future", 422)
    due = (
        stamp(data["next_update"])
        if data["next_update"]
        else clock + timedelta(minutes=policy["interval_minutes"])
    )
    if due <= clock:
        fail("Next update must be in the future", 422)
    doc["last_update"] = clock.isoformat()
    if data["impact"]:
        doc["business_impact"] = data["impact"]
    doc["forecast"] = dict(
        at=data["recovery_at"],
        author=actor.sub,
        recorded_at=clock.isoformat(),
        status="confirmed" if data["recovery_at"] else "unknown",
    )
    timer(db, doc, due)
