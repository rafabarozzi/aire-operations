"""Trusted worker entry point. The host supplies an authenticated Operations transport,
an authorized collector, the organization retention deadline and an incident-bound file.
"""
from .sources import messages, operations, transcript


def ingest(store, actor, incident_id, get, expires, *, collector=None, files=()):
    store.scope(actor, incident_id, {"operator", "coordinator", "admin"})
    snapshot = None

    def capture(path, params):
        nonlocal snapshot
        result = get(path, params)
        if not params:
            snapshot = result
        return result

    rows = operations(capture, actor.tenant, incident_id)
    if collector:
        for room in snapshot["rooms"]:
            provider, room_id = room["provider"], room["room_id"]
            payload = collector.messages(actor.tenant, incident_id, provider, room_id)
            rows.extend(messages(payload, actor.tenant, incident_id, provider, room_id))
    for attachment in files:
        if (attachment["tenant"], attachment["incident_id"]) != (actor.tenant, incident_id):
            raise PermissionError("Transcript scope mismatch")
        rows.extend(transcript(attachment["content"], attachment["source_id"],
                               attachment.get("started_at"), attachment.get("recorded_at")))
    store.ingest(actor, incident_id, rows, expires)
