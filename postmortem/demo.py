"""Run an isolated synthetic import; the JSON report is printed with --json."""
import argparse
import json
import tempfile
import time
from pathlib import Path
from .ingest import ingest
from .report import markdown
from .store import Principal, Store


FIXTURES = Path(__file__).with_name("fixtures")


def sample():
    payload = json.loads((FIXTURES / "operations.json").read_text())
    iid = payload["incident"]["id"]

    def get(path, params):
        if path == f"/v1/incidents/{iid}":
            return payload["incident"]
        if path == f"/v1/incidents/{iid}/timeline":
            return [e for e in payload["timeline"] if e["version"] > params["after_version"]][:params["limit"]]
        raise ValueError("Unexpected Operations route")

    class FixtureCollector:
        def messages(self, tenant, incident_id, provider, room):
            return json.loads((FIXTURES / "teams.json").read_text())

    files = [dict(tenant="lab", incident_id=iid, source_id="meeting.vtt",
                  content=(FIXTURES / "meeting.vtt").read_text())]
    return iid, get, FixtureCollector(), files


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    iid, get, collector, files = sample()
    with tempfile.TemporaryDirectory() as tmp:
        store = Store(Path(tmp) / "demo.db")
        actor = Principal("lab", "gerador-local", {"operator"})
        ingest(store, actor, iid, get, time.time()+3600, collector=collector, files=files)
        report = store.write(actor, iid, 0, "generate")
        print(json.dumps(report, ensure_ascii=False, indent=2) if args.json else markdown(report))
