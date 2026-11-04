import os
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import jwt
from fastapi.testclient import TestClient

from operations.api import app
from operations.db import connect, migrate
from operations.dev import DEFAULT_ROLES, keys, token
from operations.service import now
from operations.worker import tick


class Operations(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.folder = Path(cls.tmp.name)
        keys(cls.folder)
        os.environ.update(
            AIRE_PUBLIC_KEY=str(cls.folder / "public.pem"),
            AIRE_ISSUER="aire-local",
            AIRE_CLIENTS="portal,bot,mobile,postmortem",
        )
        migrate()
        migrate()
        cls.api = TestClient(app)

    def setUp(self):
        self.tenant = str(uuid4())
        self.auth = self.headers()
        self.create = {
            "title": "Synthetic checkout",
            "severity": "P2",
            "team": "checkout",
            "coordinator": "ana",
        }
        self.inc = self.api.post(
            "/v1/incidents", headers=self.auth, json=self.create
        ).json()
        self.path = "/v1/incidents/" + self.inc["id"]

    def tearDown(self):
        with connect() as db:
            db.execute("DELETE FROM schedules WHERE tenant=%s", (self.tenant,))

    def headers(self, roles=DEFAULT_ROLES, tenant=None, sub="ana", client="portal"):
        return {
            "Authorization": "Bearer "
            + token(self.folder, tenant or self.tenant, sub, roles, client),
            "Idempotency-Key": str(uuid4()),
        }

    def command(self, path, body, status=200, auth=None):
        h = {
            **(auth or self.auth),
            "Idempotency-Key": str(uuid4()),
            "If-Match": '"%d"' % self.inc["version"],
        }
        r = self.api.post(self.path + "/" + path, json=body, headers=h)
        self.assertEqual(r.status_code, status, r.text)
        if status == 200:
            self.inc = r.json()
        return r

    def timeline(self):
        return self.api.get(self.path + "/timeline", headers=self.auth).json()

    def test_tenant_authentication(self):
        foreign = self.headers(tenant="other")
        self.assertEqual(
            self.api.get(
                self.path, headers={**foreign, "X-Tenant": self.tenant}
            ).status_code,
            404,
        )
        self.assertEqual(self.api.get("/v1/incidents", headers=foreign).json(), [])
        self.assertEqual(self.api.get(self.path).status_code, 401)
        self.assertEqual(
            self.api.get(self.path, headers=self.headers(client="unknown")).status_code,
            401,
        )
        self.assertEqual(
            self.api.get(
                self.path, headers=self.headers(["read:customer"])
            ).status_code,
            403,
        )
        forged = jwt.encode(
            {"tenant": self.tenant, "roles": ["admin"]}, key="", algorithm="none"
        )
        self.assertEqual(
            self.api.get(
                self.path, headers={"Authorization": "Bearer " + forged}
            ).status_code,
            401,
        )

    def test_idempotency_and_concurrency(self):
        with ThreadPoolExecutor(4) as pool:
            replies = list(
                pool.map(
                    lambda _: self.api.post(
                        "/v1/incidents", headers=self.auth, json=self.create
                    ).json(),
                    range(4),
                )
            )
        self.assertEqual({x["id"] for x in replies}, {self.inc["id"]})
        self.assertEqual(
            self.api.post(
                "/v1/incidents",
                headers=self.auth,
                json=dict(self.create, title="different"),
            ).status_code,
            409,
        )

        def race(level):
            return self.api.post(
                self.path + "/severity",
                headers={
                    **self.auth,
                    "Idempotency-Key": str(uuid4()),
                    "If-Match": '"1"',
                },
                json={"severity": level, "reason": "test"},
            ).status_code

        with ThreadPoolExecutor(2) as pool:
            self.assertEqual(sorted(pool.map(race, ["P1", "P3"])), [200, 412])
        self.assertEqual([x["version"] for x in self.timeline()], [1, 2])

    def test_independent_lifecycles_and_confirmation(self):
        room = {"provider": "teams", "room_id": "war-1", "state": "open"}
        self.command("rooms", room)
        self.command(
            "references",
            dict(
                provider="servicenow",
                identifier="INC001",
                sync_state="synced",
                ticket_state="open",
            ),
        )
        self.command("state", dict(state="closed", reason="premature"), 409)
        self.command(
            "state",
            dict(state="resolved", reason="recovered"),
            403,
            self.headers(["operator"]),
        )
        self.command("state", dict(state="resolved", reason="recovered"))
        self.assertEqual(self.inc["rooms"][0]["state"], "open")
        self.assertEqual(self.inc["references"][0]["ticket_state"], "open")
        self.command("rooms", dict(room, state="closed"), 409)
        self.command(
            "closure-requests", dict(target="room", provider="teams", room_id="war-1")
        )
        self.command(
            "rooms", dict(room, state="closed", confirmation=self.timeline()[-1]["id"])
        )
        self.command("closure-requests", dict(target="incident"))
        self.command(
            "state",
            dict(
                state="closed",
                reason="confirmed",
                confirmation=self.timeline()[-1]["id"],
            ),
        )
        self.command("state", dict(state="investigation", reason="reopened"))
        self.assertEqual(self.inc["rooms"][0]["state"], "closed")

    def test_publications(self):
        for audience in ("customer", "operations"):
            self.command(
                "communications", dict(audience=audience, text=audience + " text")
            )
            draft = self.timeline()[-1]["data"]["id"]
            url = "/v1/publications/" + audience + "/" + self.inc["id"]
            reader = self.headers(["read:" + audience], client="bot")
            self.assertEqual(self.api.get(url, headers=reader).json(), [])
            self.command(
                "approvals", dict(publication_id=draft), 403, self.headers(["operator"])
            )
            self.command("approvals", dict(publication_id=draft))
            self.assertEqual(
                [r["text"] for r in self.api.get(url, headers=reader).json()],
                [audience + " text"],
            )
        self.assertEqual(
            self.api.get(url, headers=self.headers(["read:customer"])).status_code, 403
        )
        self.assertEqual(
            self.api.get(
                url, headers=self.headers(["read:operations"], tenant="elsewhere")
            ).json(),
            [],
        )

    def test_worker_and_simulated_bot(self):
        self.command(
            "policy",
            {
                "interval_minutes": 1,
                "escalation_minutes": 1,
                "additional_teams": ["ERT"],
            },
        )
        room = {"provider": "slack", "room_id": "existing"}
        self.command("rooms", dict(room, state="open"))
        clock = now() + timedelta(minutes=5)
        self.command(
            "pause",
            dict(reason="handover", until=(clock + timedelta(minutes=2)).isoformat()),
        )

        def sent():
            with connect() as db:
                return db.execute(
                    "SELECT * FROM deliveries WHERE tenant=%s ORDER BY kind",
                    (self.tenant,),
                ).fetchall()

        tick(clock)
        self.assertEqual(sent(), [])
        clock += timedelta(minutes=3)
        with ThreadPoolExecutor(3) as pool:
            list(pool.map(tick, [clock] * 3))
        self.assertEqual([d["kind"] for d in sent()], ["reminder"])
        self.inc = self.api.get(self.path, headers=self.auth).json()
        self.command("simulator/messages", dict(room, message="hello"))
        tick(clock + timedelta(minutes=2))
        self.assertEqual([d["kind"] for d in sent()], ["escalation", "reminder"])
        self.assertEqual(len({r["prompt"] for r in sent()}), 1)
        self.inc = self.api.get(self.path, headers=self.auth).json()
        self.command(
            "simulator/messages",
            dict(
                room,
                update=dict(
                    no_change=True, fact_at=now().isoformat(), recovery_at=None
                ),
            ),
        )
        with connect() as db:
            self.assertIsNone(
                db.execute(
                    "SELECT pending FROM schedules WHERE tenant=%s", (self.tenant,)
                ).fetchone()["pending"]
            )
        self.assertEqual(self.timeline()[-1]["origin"], "simulator.slack")

    def test_correction_forecast_and_duplicates(self):
        self.command(
            "updates",
            dict(
                no_change=True,
                fact_at=now().isoformat(),
                recovery_at=(now() - timedelta(minutes=2)).isoformat(),
            ),
        )
        self.assertEqual(self.inc["forecast"]["status"], "overdue")
        original = self.timeline()[-1]
        self.command(
            "corrections",
            dict(
                event_id=original["id"],
                correction="Corrected scope",
                fact_at=now().isoformat(),
            ),
        )
        self.assertEqual(self.timeline()[-2], original)
        self.command("journeys", dict(id="checkout", impact="degraded"))
        principal = self.api.post(
            "/v1/incidents", headers=self.headers(), json=self.create
        ).json()
        self.command("duplicate", dict(principal_id=principal["id"]))
        self.assertEqual(self.inc["duplicate_of"], principal["id"])
        self.assertEqual(
            self.api.post(
                "/v1/incidents/" + principal["id"] + "/duplicate",
                headers={**self.headers(), "If-Match": '"1"'},
                json={"principal_id": self.inc["id"]},
            ).status_code,
            409,
        )

    def test_org_policy_responder_and_human_confirmation(self):
        policy = {
            "severities": ["critical", "minor"],
            "responder": "rui",
            "closure_roles": ["operator"],
        }
        h = {**self.auth, "Idempotency-Key": str(uuid4()), "If-Match": '"0"'}
        self.assertEqual(
            self.api.put("/v1/policy", headers=h, json=policy).status_code, 200
        )
        self.command("severity", dict(severity="P1", reason="test"), 422)
        self.command("severity", dict(severity="critical", reason="test"))
        self.command(
            "updates",
            dict(no_change=True, fact_at=now().isoformat(), recovery_at=None),
            403,
            self.headers(["operator"]),
        )
        self.command("state", dict(state="resolved", reason="done"))
        self.command("closure-requests", dict(target="incident"))
        data = dict(
            state="closed", reason="confirm", confirmation=self.timeline()[-1]["id"]
        )
        self.command("state", data, 403, self.headers(["operator"], client="bot"))
        self.command("state", data, 200, self.headers(["operator"]))

    def test_auth_claims(self):
        raw = self.auth["Authorization"].split()[1]
        claims = jwt.decode(raw, options={"verify_signature": False})
        for change in (
            {"aud": "elsewhere"},
            {"iss": "other"},
            {"exp": 0},
            {"tenant": None},
            {"actor_type": "unknown"},
        ):
            altered = jwt.encode(
                dict(claims, **change),
                (self.folder / "issuer.pem").read_bytes(),
                algorithm="RS256",
            )
            self.assertEqual(
                self.api.get(
                    self.path, headers={"Authorization": "Bearer " + altered}
                ).status_code,
                401,
            )

    def test_transaction_rollback_after_connector_failure(self):
        from unittest.mock import patch

        from operations.connectors import CONNECTORS

        self.command("rooms", dict(room_id="sim-2", provider="teams", state="open"))
        clock = now() + timedelta(hours=1)
        with patch.object(
            CONNECTORS["teams"], "send", side_effect=RuntimeError("simulated fault")
        ):
            with self.assertRaises(RuntimeError):
                tick(clock)
        with connect() as db:
            self.assertIsNone(
                db.execute(
                    "SELECT pending FROM schedules WHERE tenant=%s", (self.tenant,)
                ).fetchone()["pending"]
            )
        tick(clock)
        self.assertEqual(self.timeline()[-1]["kind"], "reminder")
