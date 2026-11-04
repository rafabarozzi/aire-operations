import io
import json
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from .ai import Gemini, OpenAI, suggest
from .demo import FIXTURES, sample
from .ingest import ingest
from .portal import application
from .report import claim, markdown
from .sources import messages, operations, transcript
from .store import Conflict, Principal, Store


class Workflow(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / "pm.db"
        self.now = 10
        self.store = Store(self.path, lambda: self.now)
        self.actor = Principal("lab", "ana", {"admin"}, True)
        self.iid, get, collector, files = sample()
        ingest(self.store, self.actor, self.iid, get, 20, collector=collector, files=files)
        self.first = self.write("generate", 0)
        self.rows = self.first["timeline"]

    def write(self, action, version, **args):
        return self.store.write(self.actor, self.iid, version, action, **args)

    def test_grounding_and_clocks(self):
        timeline = self.first["timeline"]
        self.assertLess(timeline[1]["fact_at"], timeline[1]["recorded_at"])
        sections = self.first["sections"]
        self.assertEqual(sections["cause"], [])
        self.assertTrue(sections["impact"][0]["refs"])
        self.assertIn("Ainda não determinada", markdown(self.first))
        self.assertIsNone(next(r for r in timeline if r["source"]=="transcript")["fact_at"])
        self.assertIn("aprove este relatório", markdown(self.first))
        self.assertEqual(self.first["status"], "draft")

    def test_versions(self):
        with self.assertRaises(Conflict):
            self.write("approve", 1)
        review = self.write("review", 1)
        approved = self.write("approve", 2)
        self.assertEqual(approved["status"], "approved")
        fixed = self.write("rectify", 3, reason="Retificação da interpretação")
        self.assertEqual(fixed["status"], "draft")
        self.assertEqual(self.store.get(self.actor,self.iid,2),review)
        self.assertEqual(fixed["author"], "ana")

    def test_reject_unknown_refs(self):
        sections = self.first["sections"]
        sections["facts"].append(claim("sem base","missing"))
        with self.assertRaises(ValueError):
            self.write("review",1,sections=sections)
        self.assertEqual(self.store.get(self.actor,self.iid)["version"],1)

    def test_concurrent_review(self):
        def attempt(_):
            try:
                self.write("review",1)
                return True
            except Conflict:
                return False
        with ThreadPoolExecutor(2) as pool:
            self.assertEqual(sorted(pool.map(attempt,range(2))),[False,True])

    def test_import_replay_and_atomic_conflict(self):
        self.store.ingest(self.actor,self.iid,self.rows,30)
        with self.assertRaises(Conflict):
            self.store.ingest(self.actor,self.iid,[dict(self.rows[0],id="new"),
                              dict(self.rows[1],text="changed")],30)
        self.assertEqual(len(self.write("review",1)["timeline"]),len(self.rows))

    def test_late_evidence(self):
        self.write("review",1)
        self.store.ingest(self.actor,self.iid,[dict(self.rows[-1],id="late")],20)
        with self.assertRaises(Conflict):
            self.write("approve",2)

    def test_scope_and_erasure(self):
        other = Principal("org2","ana",{"admin"},True)
        with self.assertRaises(LookupError):
            self.store.get(other,self.iid)
        self.store.delete(other,self.iid)
        self.assertEqual(self.store.get(self.actor,self.iid),self.first)
        self.store.ingest(other,self.iid,self.rows,30)
        self.now=20
        with self.assertRaises(LookupError):
            self.store.get(self.actor,self.iid)
        self.assertEqual(self.store.sweep(),1)
        self.store.delete(other,self.iid)
        with sqlite3.connect(self.path) as db:
            for table in ["bundles","reports","evidence"]:
                self.assertEqual(db.execute(f"SELECT count(*) FROM {table}").fetchone()[0],0)

    def test_contract_guards(self):
        fixture = json.loads((FIXTURES/"operations.json").read_text())
        incident = fixture["incident"]
        for tenant,events,error in [("other",[],PermissionError),("lab",[],ValueError),
                                   ("lab",fixture["timeline"][1:],ValueError)]:
            with self.assertRaises(error):
                operations(lambda p,q: events if q else incident, tenant,self.iid)
        room = json.loads((FIXTURES/"teams.json").read_text())
        with self.assertRaises(PermissionError):
            messages(room,"lab",self.iid,"teams","wrong")
        room["provider"]="slack"
        self.assertEqual(messages(room,"lab",self.iid,"slack","room1")[0]["source"],"slack")

    def test_vtt(self):
        content=(FIXTURES/"meeting.vtt").read_text()
        row=transcript(content,"abc","2026-01-01T23:59:00-03:00")[0]
        self.assertEqual(row["author"],"Operadora")
        self.assertEqual(row["fact_at"],"2026-01-02T03:01:00+00:00")
        with self.assertRaises(ValueError):
            transcript(content.replace("02:03","01:03"),"abc")

    def test_provider_boundary(self):
        output = json.dumps({"hypotheses":[claim("Possível causa",self.rows[0]["id"])]})
        responses = SimpleNamespace(create=lambda **kw: SimpleNamespace(output_text=output,
                              usage=SimpleNamespace(input_tokens=20,output_tokens=5)))
        models = SimpleNamespace(generate_content=lambda **kw: SimpleNamespace(text=output,
                    usage_metadata=SimpleNamespace(prompt_token_count=20,candidates_token_count=5)))
        audit=[]
        for provider in [OpenAI(SimpleNamespace(responses=responses),"fake"),
                         Gemini(SimpleNamespace(models=models),"fake")]:
            result=suggest(provider,self.rows,audit.append,tenant="lab",generation_id="synthetic",
                           rates=dict(currency="USD",input="1",output="2"))
            self.assertEqual(result["generation_cost"]["estimated_amount"],"0.00003")
        self.assertNotIn("text", audit[0])
        output='{"cause":"confirmed"}'
        with self.assertRaises(ValueError):
            suggest(provider,self.rows,audit.append,tenant="lab",generation_id="bad")
        self.assertEqual(len(audit),3)

    def test_portal_commands(self):
        def call(actor, endpoint, method="GET", body=b"{}", expected='"1"'):
            status=[]
            app=application(self.store,lambda env: actor)
            response=app(dict(PATH_INFO="/v1/postmortems/"+self.iid+endpoint,
                              REQUEST_METHOD=method, CONTENT_LENGTH=str(len(body)),
                              HTTP_IF_MATCH=expected, **{"wsgi.input":io.BytesIO(body)}),
                         lambda code,headers:status.append(code))
            return status[0][:3], response[0]
        self.assertEqual(call(self.actor,"")[0],"200")
        self.assertEqual(call(self.actor,"/markdown")[0],"200")
        bot=Principal("lab","bot",{"admin"})
        self.assertEqual(call(bot,"/review","POST")[0],"403")
        self.assertEqual(call(self.actor,"/review","POST",expected="")[0],"428")
        self.assertEqual(call(self.actor,"/review","POST")[0],"201")
        self.assertEqual(call(self.actor,"/approve","POST")[0],"409")
        self.assertEqual(call(self.actor,"/approve","POST",expected='"2"')[0],"201")
        self.assertEqual(json.loads(call(self.actor,"/revisions/1")[1])["status"],"draft")
