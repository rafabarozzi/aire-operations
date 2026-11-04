from datetime import timedelta
from uuid import uuid4

from psycopg.types.json import Jsonb

from .auth import require
from .service import apply_update, fail, lookup, now, stamp, timer

TRANSITIONS = {
    "investigation": {"mitigation", "monitoring", "resolved"},
    "mitigation": {"investigation", "monitoring", "resolved"},
    "monitoring": {"investigation", "mitigation", "resolved"},
    "resolved": {"closed", "investigation"},
    "closed": {"investigation"},
}


def replace(items, data, keys):
    items[:] = [v for v in items if any(v[k] != data[k] for k in keys)]
    items.append(data)


def confirmation(db, actor, doc, data, target):
    if actor.actor_type != "person":
        fail("Human confirmation required", 403)
    row = db.execute(
        "SELECT body FROM events WHERE tenant=%s AND incident_id=%s AND id=%s AND version=%s AND body->>'kind'='closure-requests'",
        (actor.tenant, doc["id"], data["confirmation"], doc["version"]),
    ).fetchone()
    if not row or row["body"]["data"]["target"] != target:
        fail("Request closure at the current version before confirming")
    if target == "room" and any(
        row["body"]["data"][k] != data[k] for k in ("provider", "room_id")
    ):
        fail("Confirmation bound to another room")


def reschedule_policy(db, doc, policy):
    pending = db.execute(
        "SELECT pending FROM schedules WHERE tenant=%s AND incident_id=%s FOR UPDATE",
        (doc["tenant"], doc["id"]),
    ).fetchone()
    if pending and not pending["pending"]:
        timer(db, doc, now() + timedelta(minutes=policy["interval_minutes"]))


def apply(db, actor, doc, kind, data, policy, org):
    tenant, i = actor.tenant, doc["id"]
    if kind == "updates":
        apply_update(db, doc, data, policy, actor)
    elif kind == "context":
        doc.update(data)
    elif kind == "state":
        target = data["state"]
        if target not in TRANSITIONS[doc["state"]]:
            fail("Invalid incident transition")
        if target == "resolved" or (
            target == "investigation" and doc["state"] in ("resolved", "closed")
        ):
            require(actor, *policy["recovery_roles"])
        if target == "closed":
            require(actor, *policy["closure_roles"])
            confirmation(db, actor, doc, data, "incident")
        doc["state"] = target
        if target in ("resolved", "closed"):
            doc["next_update"] = None
            db.execute(
                "DELETE FROM schedules WHERE tenant=%s AND incident_id=%s", (tenant, i)
            )
        elif doc["next_update"] is None:
            timer(db, doc, now() + timedelta(minutes=policy["interval_minutes"]))
    elif kind == "rooms":
        old = next(
            (
                r
                for r in doc["rooms"]
                if r["room_id"] == data["room_id"] and r["provider"] == data["provider"]
            ),
            None,
        )
        if data["state"] == "closed":
            require(actor, *policy["closure_roles"])
            if not old or old["state"] == "closed":
                fail("Active room required")
            confirmation(db, actor, doc, data, "room")
        elif old and (old["state"], data["state"]) != ("requested", "open"):
            fail("Invalid room transition")
        replace(doc["rooms"], data, ["room_id", "provider"])
    elif kind == "closure-requests":
        if data["target"] == "incident" and doc["state"] != "resolved":
            fail("Resolve incident before requesting closure")
        if data["target"] == "room" and not any(
            r["provider"] == data["provider"]
            and r["room_id"] == data["room_id"]
            and r["state"] != "closed"
            for r in doc["rooms"]
        ):
            fail("Active room required")
    elif kind == "severity":
        require(actor, *policy["severity_roles"])
        if data["severity"] not in org["severities"]:
            fail("Unknown severity", 422)
        data["previous"] = doc["severity"]
        doc["severity"] = data["severity"]
    elif kind == "journeys":
        if data["impact"] == "recovered":
            require(actor, *policy["recovery_roles"])
        replace(doc["journeys"], data, ["id"])
    elif kind == "references":
        replace(doc["references"], data, ["provider", "identifier"])
    elif kind == "duplicate":
        principal = lookup(db, tenant, data["principal_id"])
        if (
            principal["id"] == i
            or principal["duplicate_of"]
            or db.execute(
                "SELECT 1 FROM incidents WHERE tenant=%s AND body->>'duplicate_of'=%s",
                (tenant, i),
            ).fetchone()
        ):
            fail("Duplicate must point directly to a different principal")
        doc["duplicate_of"] = principal["id"]
    elif kind == "corrections":
        if not db.execute(
            "SELECT 1 FROM events WHERE tenant=%s AND incident_id=%s AND id=%s",
            (tenant, i, data["event_id"]),
        ).fetchone():
            fail("Event unavailable", 404)
    elif kind == "pause":
        require(actor, "coordinator", "admin")
        if stamp(data["until"]) <= now():
            fail("Pause deadline must be in the future", 422)
        doc["pause"] = data
    elif kind == "policy":
        require(actor, "admin")
        doc["policy_override"] = data
        if doc["next_update"]:
            reschedule_policy(db, doc, data)
    elif kind == "communications":
        require(actor, "draft:" + data["audience"])
        data["id"] = str(uuid4())
        db.execute(
            "INSERT INTO publications VALUES (%s,%s,%s,%s,%s)",
            (tenant, i, data["id"], data["audience"], Jsonb(data)),
        )
    elif kind == "approvals":
        row = db.execute(
            "SELECT body FROM publications WHERE tenant=%s AND incident_id=%s AND id=%s",
            (tenant, i, data["publication_id"]),
        ).fetchone()
        if not row:
            fail("Draft unavailable", 404)
        draft = row["body"]
        require(actor, "approve:" + draft["audience"])
        if "approved_by" in draft:
            fail("Already published")
        draft.update(approved_by=actor.sub, published_at=now().isoformat())
        db.execute(
            "UPDATE publications SET body=%s WHERE tenant=%s AND incident_id=%s AND id=%s",
            (Jsonb(draft), tenant, i, draft["id"]),
        )
