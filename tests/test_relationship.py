import csv
import json
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from echo_memory import store
from echo_memory.relationship import DEFAULTS, Relationship
from experiments.echo002.run import replay


class RelationshipTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.path = self.root / "synthetic.sqlite"
        self.actor = Relationship(self.path, "actor")

    def memory(self, key, kind, domain="lab", **overrides):
        fields = dict(kind="experience", content="合成核查摘要", memory_id=key, source_ref=key,
                      confidence=.9, origin="synthetic-verifier", occurred_at="2026-01-01T00:00:00Z",
                      tags=["person=actor", "event=" + kind, "domain=" + domain])
        store.add(self.path, **(fields | overrides))
        return key

    def test_scope_attestation_and_order(self):
        self.assertEqual(self.actor.state()["relationship_type"], "stranger")
        for key, change in (("rumor", {"origin": "unknown"}), ("weak", {"confidence": .1}),
                            ("other", {"tags": ["person=other", "event=help", "domain=lab"]})):
            self.memory(key, "help", **change)
            with self.assertRaises(ValueError):
                self.actor.observe(key, "help", "lab")
        self.actor.observe(self.memory("ok", "help"), "help", "lab")
        self.memory("past", "help", occurred_at="2025-01-01T00:00:00+00:00")
        with self.assertRaises(ValueError):
            self.actor.observe("past", "help", "lab")
        self.memory("copy", "help", source_ref="ok")
        with self.assertRaises(sqlite3.IntegrityError):
            self.actor.observe("copy", "help", "lab")
        self.assertEqual(len(self.actor.history()), 1)

    def test_concurrent_replay_restart_and_isolation(self):
        self.memory("one", "contact")
        with ThreadPoolExecutor(2) as pool:
            states = list(pool.map(lambda _: self.actor.observe("one", "contact", "lab"), range(2)))
        self.assertEqual(states[0], states[1])
        reopened = Relationship(self.path, "actor")
        self.assertEqual(reopened.state()["trust"], .5)
        self.assertEqual(reopened.state()["shared_experiences"], 1)
        with self.assertRaises(ValueError):
            Relationship(self.path, "someone-else")
        other = Relationship(self.root / "other.sqlite", "other")
        with self.assertRaises(ValueError):
            other.observe("one", "contact", "lab")

    def test_conflict_repair_and_mixed_effect(self):
        first = self.actor.observe(self.memory("first", "criticism"), "criticism", "lab")
        self.assertGreater(first["trust"], .5)
        self.assertLess(first["closeness"], .5)
        self.actor.observe(self.memory("bad", "deception"), "deception", "lab")
        explanation = self.actor.explain("lab")
        self.assertTrue(explanation["basis"][-1]["opposed_prior"])
        self.assertTrue(self.actor.explain()["basis"][-1]["opposed_prior"])
        for i in range(5):
            self.actor.observe(self.memory(str(i), "repair"), "repair", "lab")
        self.assertGreater(self.actor.explain("lab")["score"], .5)
        self.assertEqual(self.actor.explain("unseen")["score"], .5)
        self.assertEqual(self.actor.explain("lab")["basis"][1]["id"], "bad")

    def test_consent_boundaries_and_withdrawal(self):
        self.actor.observe(self.memory("r", "advice"), "advice", "lab")
        before = self.actor.decide("lab")
        with self.assertRaises(ValueError):
            self.actor.set_policy(role="family", person_agrees=True)
        self.actor.set_policy(role="family", person_agrees=True, agent_agrees=True)
        self.assertEqual(self.actor.state()["relationship_type"], "family")
        self.assertEqual(self.actor.decide("lab"), before)
        self.actor.set_policy(recall=False)
        self.assertEqual(self.actor.decide("lab")["evidence"], [])
        self.assertNotEqual(self.actor.state()["relationship_type"], "family")
        with self.assertRaises(PermissionError):
            self.actor.explain("lab")
        self.actor.set_policy(blocked_domains=["lab"])
        self.assertEqual(self.actor.decide("lab")["strategy"], "outside_scope")
        with self.assertRaises(PermissionError):
            self.actor.observe("r", "advice", "lab")

    def test_paired_recall_and_live_memory(self):
        self.actor.observe(self.memory("a", "advice"), "advice", "lab")
        before = self.actor.state()
        self.assertEqual(self.actor.decide("lab")["strategy"], "compare_with_verified_example")
        self.assertEqual(self.actor.decide("lab", retrieve=False)["strategy"], "request_method")
        self.assertEqual(self.actor.state(), before)
        store.delete(self.path, "a")
        self.assertEqual(self.actor.decide("lab")["evidence"], [])

    def test_config_pinning(self):
        cfg = json.loads(DEFAULTS.read_text(encoding="utf-8"))
        cfg["prior"] = 4
        custom = self.root / "custom.json"
        custom.write_text(json.dumps(cfg), encoding="utf-8")
        with self.assertRaises(ValueError):
            Relationship(self.path, "actor", custom)
        new = Relationship(self.root / "custom.sqlite", "actor", custom)
        self.assertEqual(new.state()["trust"], .5)

    def test_all_scenarios_repeat_exactly(self):
        first, second = self.root / "run1", self.root / "run2"
        result = replay(first)
        self.assertEqual(result, replay(second))
        for filename in ("trajectory.csv", "probes.csv", "summary.json"):
            self.assertEqual((first / filename).read_bytes(), (second / filename).read_bytes())
        self.assertEqual((result["people"], result["events"]), (10, 120))
        self.assertGreater(result["recall_changes"], 0)
        self.assertGreater(result["state_changes"], 0)
        self.assertEqual(result["roles"]["p07"], "acquaintance")
        with (first / "trajectory.csv").open(encoding="utf-8") as stream:
            trace = {(r["person"], int(r["step"])): r for r in csv.DictReader(stream)}
        self.assertGreater(float(trace["p01", 12]["trust"]), .5)
        self.assertGreater(float(trace["p03", 6]["metrology"]), float(trace["p03", 8]["metrology"]))
        self.assertEqual(trace["p03", 6]["logistics"], trace["p03", 8]["logistics"])
        self.assertLess(float(trace["p05", 5]["metrology"]), .5)
        self.assertGreater(float(trace["p05", 12]["metrology"]), .5)
        self.assertGreater(float(trace["p07", 12]["closeness"]), .8)
        self.assertLess(float(trace["p07", 12]["metrology"]), .5)
        with (first / "probes.csv").open(encoding="utf-8") as stream:
            probes = {(r["person"], int(r["step"])): r for r in csv.DictReader(stream)}
        stranger, familiar = probes["p10", 1], probes["p09", 12]
        self.assertEqual(stranger["memory_on"], familiar["memory_on"])
        self.assertNotEqual(stranger["style"], familiar["style"])
