"""Simulated provider delivery. The sink and schedule share one DB transaction."""

from typing import Protocol

from psycopg.types.json import Jsonb


class Connector(Protocol):
    def send(self, db, tenant, incident, prompt, kind, room, payload): ...


class Simulator:
    def send(self, db, tenant, incident, prompt, kind, room, payload):
        db.execute(
            "INSERT INTO deliveries VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
            (
                tenant,
                incident,
                prompt,
                kind,
                room["provider"],
                room["room_id"],
                Jsonb(payload),
            ),
        )


CONNECTORS: dict[str, Connector] = {"teams": Simulator(), "slack": Simulator()}
