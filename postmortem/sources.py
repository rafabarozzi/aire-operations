"""Read-only boundaries. Transports and identities are bound by the trusted host."""
import json
import re
from datetime import datetime, timedelta, timezone
from typing import Protocol
from uuid import UUID


def instant(value):
    if value is None:
        return None
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.utcoffset() is None:
        raise ValueError("Timezone required")
    return dt.astimezone(timezone.utc).isoformat()


def evidence(key, source, text, fact_at=None, recorded_at=None, author=None):
    if not isinstance(text, str) or not text.strip():
        raise ValueError("Empty evidence")
    return dict(id=key, source=source, text=text, fact_at=instant(fact_at),
                recorded_at=instant(recorded_at), author=author)


def operations(get, tenant, incident_id):
    path = f"/v1/incidents/{UUID(incident_id)}"
    incident = get(path, {})
    if (incident["tenant"], incident["id"]) != (tenant, incident_id):
        raise PermissionError("Incident scope mismatch")
    version = incident["version"]
    fields = ("title", "origin", "state", "business_impact", "affected_public",
              "extent", "journeys", "references", "severity")
    snapshot = {k: incident.get(k) for k in fields}
    snapshot["origin"] = incident.get("origin", "manual")
    snapshot["journeys"] = incident.get("journeys", [])
    rows = [evidence(f"incident:{version}", "operations.snapshot",
                     json.dumps(snapshot, ensure_ascii=False))]
    cursor = 0
    while cursor < version:
        events = get(path + "/timeline", {"after_version": cursor, "limit": 100})
        if not events:
            raise ValueError("Incomplete timeline")
        for event in events:
            if cursor == version:
                break
            if event["incident_id"] != incident_id or event["version"] != cursor + 1:
                raise ValueError("Timeline scope or sequence mismatch")
            rows.append(evidence("event:" + event["id"], "operations." + event["kind"],
                                 json.dumps(event["data"], ensure_ascii=False), event["fact_at"],
                                 event["recorded_at"], event["author"]))
            cursor += 1
    return rows


class RoomCollector(Protocol):
    """Authorized installation must verify tenant, incident and room binding."""
    def messages(self, tenant: str, incident_id: str, provider: str, room: str): ...


def messages(payload, tenant, incident_id, provider, room):
    if (payload["tenant"], payload["incident_id"], payload["provider"], payload["room"]) != (
            tenant, incident_id, provider, room) or provider not in {"teams", "slack"}:
        raise PermissionError("Room scope mismatch")
    return [evidence(json.dumps([provider, room, m["id"]]), provider, m["text"],
                     m.get("fact_at"), m.get("recorded_at"), m.get("author"))
            for m in payload["messages"]]


STAMP = r"(?:(\d{2,}):)?([0-5]\d):([0-5]\d)\.(\d{3})"


def offset(value):
    match = re.fullmatch(STAMP, value)
    if not match:
        raise ValueError("Invalid WebVTT timestamp")
    h, m, s, ms = (int(x or 0) for x in match.groups())
    return timedelta(hours=h, minutes=m, seconds=s, milliseconds=ms)


def transcript(content, file_id, started_at=None, recorded_at=None):
    blocks = re.split(r"\n\s*\n", content.lstrip("\ufeff").replace("\r\n", "\n").strip())
    if not re.fullmatch(r"WEBVTT(?:[ \t].*)?", blocks[0].split("\n")[0]):
        raise ValueError("Expected WEBVTT")
    start = datetime.fromisoformat(instant(started_at)) if started_at else None
    rows = []
    for block in blocks[1:]:
        lines = block.splitlines()
        if lines[0].split()[0] in {"NOTE", "STYLE", "REGION"}:
            continue
        pos = 0 if "-->" in lines[0] else 1
        if pos >= len(lines) or "-->" not in lines[pos]:
            raise ValueError("Missing cue timing")
        begin, end = lines[pos].split("-->", 1)
        delta = offset(begin.strip())
        if offset(end.split()[0]) < delta:
            raise ValueError("Reversed cue")
        text = "\n".join(lines[pos + 1:])
        speaker = re.fullmatch(r"<v ([^>]+)>(.*?)(?:</v>)?", text, re.S)
        item = evidence(json.dumps(["vtt", file_id, len(rows)+1]), "transcript",
                        speaker[2] if speaker else text,
                        (start + delta).isoformat() if start else None, recorded_at,
                        speaker[1] if speaker else None)
        item["cue"] = lines[pos]
        rows.append(item)
    if not rows:
        raise ValueError("Empty transcript")
    return rows
