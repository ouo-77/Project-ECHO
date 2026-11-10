import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from echo_memory import store
from echo_memory.reappraisal import EvidenceReview
from echo_memory.relationship import Relationship
from echo_reflect import Reflect
from echo_reflect.candidates import request_candidate
from experiments.echo003.reflect.run import experiment, setup
from experiments.echo003.regulation_cases import Simulation, day, run_cases


class ResearchTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.folder = Path(temporary.name)

    def test_reclassification_retraction_and_stale_rollback(self):
        sim = Simulation(self.folder, "actor")
        sim.event("good", "advice", 0)
        sim.event("bad", "deception", 1)
        original = sim.actor.history()
        mistake = sim.finding("one", "bad", "conversion_fault", "复核发现计算失误", 2)
        self.assertEqual(mistake["replacement"], "error")
        self.assertGreater(sim.review.state()["trust"], original[-1]["snapshot"]["trust"])
        sim.finding("two", "bad", "wrong_asset", "批号不符", 3, "one")
        self.assertEqual(sim.review.state(), original[0]["snapshot"])
        self.assertEqual(sim.actor.history(), original)
        self.assertEqual(sim.review.apply_finding("two", previous="one"), sim.review.history("meter")[-1])
        with self.assertRaises(ValueError):
            sim.finding("stale", "bad", "knowing_fabrication", "陈旧审核", 4, "one")
        sim.finding("restore", "bad", "knowing_fabrication", "独立复核", 5, "two")
        self.assertEqual(sim.review.state(), original[-1]["snapshot"])
        sim.actor.set_policy(blocked_domains=["meter"])
        with self.assertRaises(PermissionError):
            sim.review.history("meter")

    def test_regulation_and_actual_restart(self):
        result = run_cases(self.folder)
        self.assertEqual(set(result["cases"]), set("ABCDEFGH"))
        code = ("from echo_memory.relationship import Relationship; from echo_memory.reappraisal import EvidenceReview; "
                "from echo_memory.regulation import Regulation; import sys,json; "
                "r=Regulation(EvidenceReview(Relationship(sys.argv[1], 'ab'))); "
                "print(json.dumps([r.at('meter', sys.argv[2]),r.at('meter')['trust']]))")
        with_time, current_trust = json.loads(subprocess.check_output(
            [sys.executable, "-c", code, str(self.folder/"ab.sqlite"), day(30)]))
        self.assertEqual(with_time, result["cases"]["A"][-1])
        self.assertEqual(current_trust, with_time["trust"])

    def test_single_anomaly_after_long_reliable_history(self):
        sim = Simulation(self.folder, "stability")
        actions = []
        for i in range(120):
            sim.event(f"e-{i}", "deception" if i == 100 else "advice", i)
            actions.append(sim.review.assess("meter")["action"])
        self.assertEqual(actions[0], "verify")
        self.assertEqual(set(actions[60:]), {"trial"})

    def test_reflect_blind_protocol_and_invalidation(self):
        r = experiment(self.folder)
        self.assertEqual(r["trace"], [53, 53])
        self.assertEqual(r["blocked"], r["attempts"])
        self.assertEqual(r["corrected_wrong"], 4)
        self.assertEqual(r["newly_wrong"], 0)
        self.assertLess(len(r["wrong_ids"]["reviewed"]), len(r["wrong_ids"]["disabled"]))
        self.assertFalse(r["duplicate"]["versions"])

    def test_scope_human_gate_and_repeated_summaries(self):
        engine = setup(self.folder)
        engine.deep_reflection(now=day(70))
        head = engine.inspect("meter", "safety-lock")
        self.assertEqual(head["status"], "needs_human")
        self.assertIsNone(engine.recommend("meter", "safety-lock")["action"])
        with self.assertRaises(ValueError):
            engine.approve("meter", "safety-lock", head["id"], head["deps"][0]["event"])
        proof = store.add(engine.actor.path, kind="decision", origin="synthetic-human", source_ref="fixture:approval",
                          content="审核通过", tags=["person=reflect-fixture", "domain=meter", f"approve={head['id']}"])
        engine.approve("meter", "safety-lock", head["id"], proof)
        self.assertEqual(engine.recommend("meter", "safety-lock")["action"], "trial")
        self.assertIsNone(engine.recommend("meter", "dry-run-v2")["action"])
        self.assertIsNone(engine.recommend("optic", "cold-start")["action"])
        self.assertIsNone(engine.recommend("meter", "dry-run", max_tokens=1)["action"])
        self.assertEqual(engine.inspect("meter", "mixed-check")["status"], "contradicted")
        self.assertEqual(engine.inspect("meter", "edge-case")["status"], "insufficient")
        with self.assertRaises(ValueError):
            engine.actor.observe(proof, "advice", "meter")
        engine.deep_reflection(now=day(80))
        now = engine.inspect("meter", "safety-lock")
        self.assertEqual((now["id"], now["confidence"]), (head["id"], head["confidence"]))
        store.delete(engine.actor.path, proof)
        self.assertIsNone(engine.recommend("meter", "safety-lock")["action"])

    def test_scheduler_failure_retry_and_process_persistence(self):
        engine = setup(self.folder)
        with patch("echo_reflect.engine.evaluate", side_effect=RuntimeError):
            with self.assertRaises(RuntimeError):
                engine.daily_reflection(now=day(70))
        result = engine.daily_reflection(now=day(70))
        self.assertTrue(result["versions"])
        code = ("from echo_memory.relationship import Relationship; from echo_memory.reappraisal import EvidenceReview; "
                "from echo_reflect import Reflect; import sys,json; e=Reflect(EvidenceReview("
                "Relationship(sys.argv[1],'reflect-fixture'))); print(json.dumps([e.daily_reflection(now=sys.argv[2]),"
                "e.inspect('meter','cold-start')['status']]))")
        response, status = json.loads(subprocess.check_output(
            [sys.executable, "-c", code, str(engine.actor.path), day(70)]))
        self.assertEqual((response["result"], status), ("not_due", "validated"))
        engine.control("paused")
        self.assertEqual(engine.tick(), [])
        engine.control("enabled")
        engine.event_reflection("pump-dry-run-train-1", now=day(70))
        self.assertEqual(engine.recommend("pump", "dry-run")["action"], "verify")
        engine.control("disabled")
        self.assertIsNone(engine.recommend("pump", "dry-run")["action"])

    def test_optional_model_is_quarantined_and_budgeted(self):
        engine = setup(self.folder)
        disabled = engine
        engine.deep_reflection()
        class OfflineStub:
            def count_input(self, prompt):
                return len(prompt.encode())
            def generate(self, prompt, max_output):
                return "未经验证的假设"
        with self.assertRaises(PermissionError):
            request_candidate(disabled, "meter", "dry-run", OfflineStub(), authorize=True)
        actor = Relationship(self.folder/"quota.sqlite", "quota")
        engine = Reflect(EvidenceReview(actor), {"model_calls":1, "model_tokens":1000})
        event = store.add(actor.path, kind="experience", content="模拟", origin="synthetic-verifier",
                          source_ref="lab:1", confidence=1, tags=["person=quota","event=error","domain=meter",
                          "reflect=allow","feature=dry-run","partition=train","risk=low"])
        actor.observe(event,"error","meter")
        engine.deep_reflection()
        request_candidate(engine,"meter","dry-run",OfflineStub(),authorize=True)
        with self.assertRaises(PermissionError):
            request_candidate(engine,"meter","dry-run",OfflineStub(),authorize=True)
        self.assertEqual(engine.inspect("meter","dry-run")["status"], "insufficient")
        self.assertIsNone(engine.recommend("meter","dry-run")["action"])
