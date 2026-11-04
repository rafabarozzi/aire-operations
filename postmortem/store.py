"""Local module storage. Only the integration host may construct Principal objects."""
import json
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from .report import draft, validate


@dataclass(frozen=True)
class Principal:
    tenant: str
    subject: str
    roles: frozenset
    human: bool = False


class Conflict(ValueError):
    pass


class Store:
    def __init__(self, path, clock=time.time):
        self.path, self.clock = path, clock
        with self.transaction() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS bundles(
                  tenant TEXT, incident TEXT, expires REAL NOT NULL,
                  PRIMARY KEY(tenant,incident));
                CREATE TABLE IF NOT EXISTS evidence(
                  tenant TEXT, incident TEXT, id TEXT, body TEXT NOT NULL,
                  PRIMARY KEY(tenant,incident,id),
                  FOREIGN KEY(tenant,incident) REFERENCES bundles ON DELETE CASCADE);
                CREATE TABLE IF NOT EXISTS reports(
                  tenant TEXT, incident TEXT, version INTEGER, body TEXT NOT NULL,
                  PRIMARY KEY(tenant,incident,version),
                  FOREIGN KEY(tenant,incident) REFERENCES bundles ON DELETE CASCADE);
            """)

    @contextmanager
    def transaction(self):
        db = sqlite3.connect(self.path)
        try:
            db.execute("PRAGMA foreign_keys=ON")
            db.execute("PRAGMA secure_delete=ON")
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        finally:
            db.close()

    def sweep(self):
        with self.transaction() as db:
            return db.execute("DELETE FROM bundles WHERE expires<=?", (self.clock(),)).rowcount

    def scope(self, actor, incident, roles):
        if not actor.tenant or not actor.subject or not roles.intersection(actor.roles):
            raise PermissionError("Unauthorized")
        return actor.tenant, incident

    def ingest(self, actor, incident, rows, expires):
        scope = self.scope(actor, incident, {"operator", "coordinator", "admin"})
        if not self.clock() < expires < float("inf"):
            raise ValueError("Evidence already expired or invalid deadline")
        with self.transaction() as db:
            db.execute("""INSERT INTO bundles VALUES (?,?,?) ON CONFLICT(tenant,incident)
                DO UPDATE SET expires=min(excluded.expires,bundles.expires)""", (*scope, expires))
            self.active(db, scope)
            for row in rows:
                body = json.dumps(row, sort_keys=True, ensure_ascii=False)
                existing = db.execute("SELECT body FROM evidence WHERE tenant=? AND incident=? AND id=?",
                                      (*scope, row["id"])).fetchone()
                if existing and existing[0] != body:
                    raise Conflict("Source revision requires a distinct evidence ID")
                db.execute("INSERT OR IGNORE INTO evidence VALUES (?,?,?,?)", (*scope, row["id"], body))

    def active(self, db, scope):
        if not db.execute("SELECT 1 FROM bundles WHERE tenant=? AND incident=? AND expires>?",
                          (*scope, self.clock())).fetchone():
            raise LookupError("Postmortem unavailable")

    def get(self, actor, incident, version=None):
        scope = self.scope(actor, incident, {"reader", "operator", "coordinator", "admin"})
        with self.transaction() as db:
            self.active(db, scope)
            record = db.execute("""SELECT body FROM reports WHERE tenant=? AND incident=?
                AND (? IS NULL OR version=?) ORDER BY version DESC LIMIT 1""", (*scope, version, version)).fetchone()
            if not record:
                raise LookupError("Revision unavailable")
            return json.loads(record[0])

    def write(self, actor, incident, expected, action, *, sections=None, reason=None):
        roles = {"operator", "coordinator", "admin"} if action == "generate" else {"coordinator", "admin"}
        scope = self.scope(actor, incident, roles)
        if action not in {"generate", "review", "rectify", "approve"}:
            raise ValueError("Invalid command")
        if action != "generate" and not actor.human:
            raise PermissionError("Human reviewer required")
        with self.transaction() as db:
            self.active(db, scope)
            entry = db.execute("""SELECT version,body FROM reports WHERE tenant=? AND incident=?
                ORDER BY version DESC LIMIT 1""", scope).fetchone()
            version, previous = (entry[0], json.loads(entry[1])) if entry else (0, None)
            if expected != version:
                raise Conflict("Stale base version")
            if action == "generate":
                if previous:
                    raise Conflict("Use rectification to preserve reviewed claims")
                rows = self.rows(db, scope)
                if not rows:
                    raise ValueError("No evidence")
                sections = draft(rows)
            else:
                if not previous:
                    raise LookupError("Missing draft")
                if action == "rectify" and (not reason or not reason.strip()):
                    raise ValueError("Rectification reason required")
                if action == "review" and previous["status"] == "approved":
                    raise Conflict("Rectification required")
                if action == "approve":
                    if previous["status"] != "reviewed" or sections is not None:
                        raise Conflict("Approve an unchanged reviewed revision")
                    if previous["timeline"] != self.rows(db, scope):
                        raise Conflict("New evidence requires review")
                rows = previous["timeline"] if action == "approve" else self.rows(db, scope)
                sections = previous["sections"] if sections is None else sections
            validate(sections, rows)
            report = dict(schema="postmortem.v1", tenant=actor.tenant, incident_id=incident,
                          version=version+1, parent=version or None, action=action, reason=reason,
                          author=actor.subject, written_at=datetime.fromtimestamp(self.clock(), timezone.utc).isoformat(),
                          status={"generate":"draft", "review":"reviewed", "rectify":"draft",
                                  "approve":"approved"}[action], sections=sections, timeline=rows)
            db.execute("INSERT INTO reports VALUES (?,?,?,?)", (*scope, version+1, json.dumps(report)))
            return report

    def rows(self, db, scope):
        rows = [json.loads(body) for body, in db.execute(
            "SELECT body FROM evidence WHERE tenant=? AND incident=?", scope)]
        return sorted(rows, key=lambda r: (r["fact_at"] is None, r["fact_at"] or "",
                                            r["recorded_at"] or "", r["id"]))

    def delete(self, actor, incident):
        scope = self.scope(actor, incident, {"admin"})
        with self.transaction() as db:
            db.execute("DELETE FROM bundles WHERE tenant=? AND incident=?", scope)
