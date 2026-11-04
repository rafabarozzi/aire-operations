import time
from datetime import timedelta
from uuid import uuid4

from psycopg.types.json import Jsonb

from .auth import Identity
from .connectors import CONNECTORS
from .db import connect
from .service import event, get_policy, now


def tick(clock=None):
    clock = clock or now()
    with connect() as db:
        jobs = db.execute(
            """SELECT s.*,i.body FROM schedules s JOIN incidents i ON
            (s.tenant,s.incident_id)=(i.tenant,i.id)
            WHERE ((pending IS NULL AND due_at<=%s) OR
                   (pending IS NOT NULL AND NOT escalated AND escalation_at<=%s))
            AND COALESCE((i.body->'pause'->>'until')::timestamptz,'-infinity')<=%s
            AND EXISTS (SELECT 1 FROM jsonb_array_elements(i.body->'rooms') r WHERE r->>'state'='open')
            ORDER BY due_at LIMIT 100 FOR UPDATE OF i,s SKIP LOCKED""",
            (clock, clock, clock),
        ).fetchall()
        for job in jobs:
            doc = job["body"]
            policy = doc["policy_override"] or get_policy(db, job["tenant"])["policy"]
            prompt = job["pending"] or uuid4()
            kind = "escalation" if job["pending"] else "reminder"
            payload = dict(
                responder=policy["responder"] or doc["coordinator"],
                teams=policy["additional_teams"],
                sent_at=clock.isoformat(),
                simulated=True,
            )
            for room in doc["rooms"]:
                if room["state"] == "open":
                    CONNECTORS[room["provider"]].send(
                        db, doc["tenant"], doc["id"], prompt, kind, room, payload
                    )
            doc["version"] += 1
            actor = Identity(
                doc["tenant"], "aire-worker", frozenset(), "scheduler", "service"
            )
            event(
                db,
                doc,
                actor,
                kind,
                dict(payload, prompt_id=str(prompt), fact_at=clock.isoformat()),
            )
            db.execute(
                "UPDATE incidents SET body=%s WHERE tenant=%s AND id=%s",
                (Jsonb(doc), doc["tenant"], doc["id"]),
            )
            db.execute(
                "UPDATE schedules SET pending=%s,escalation_at=%s,escalated=%s WHERE tenant=%s AND incident_id=%s",
                (
                    prompt,
                    clock + timedelta(minutes=policy["escalation_minutes"]),
                    bool(job["pending"]),
                    doc["tenant"],
                    doc["id"],
                ),
            )
        return len(jobs)


if __name__ == "__main__":
    while True:
        tick()
        time.sleep(2)
