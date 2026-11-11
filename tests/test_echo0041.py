import json
from pathlib import Path
import tempfile
import unittest

from echo_memory import store
from echo_memory.atlas import Atlas
from echo_memory.lifecycle import Lifecycle
from echo_memory.relationship import Relationship
from echo_memory.consolidation import Consolidation
from echo_memory.navigator import Query
from echo_memory.semantic import SemanticNavigator
from experiments.echo0041.run import setup, measure, HERE, DAY


class SemanticAndRetention(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)

    def test_independent_questions_and_loss(self):
        qs = [
            json.loads(s)
            for s in (HERE / "questions.jsonl").read_text(encoding="utf8").splitlines()
        ]
        self.assertGreaterEqual(len(set(r["text"] for r in qs)), 100)
        result = measure(self.root)
        self.assertGreater(
            result["scores"][1]["correct"], result["scores"][0]["correct"]
        )
        self.assertEqual(result["raw"][1][0]["ids"], ["bearing"])
        for mid in ("bearing", "valve", "name", "goal"):
            self.assertEqual(result["after"][mid], "AVAILABLE")
        self.assertEqual(result["after"]["lunch"], "FORGOTTEN")
        self.assertEqual(result["after"]["archive"], "ARCHIVED")

    def test_authorization_and_erase(self):
        atlas = setup(self.root)
        contraindicated = Query(
            "冷轴承起动前如何升温？", "met", DAY, ("sealed-bearing",)
        )
        self.assertFalse(SemanticNavigator(atlas).search(contraindicated)["evidence"])
        manager = Lifecycle(atlas)
        q = Query("ECHO", "core", DAY)
        manager.classify("name", identity=True)
        manager.sweep("2099-01-01")
        self.assertEqual(manager.status("name", q), "AVAILABLE")
        manager.erase("name")
        self.assertEqual(manager.status("name", q), "UNKNOWN")
        manager.classify("lunch", temporary=True)
        manager.sweep(DAY)
        self.assertIsNone(store.get(atlas.path, "lunch"))
        self.assertEqual(manager.status("lunch", q), "UNKNOWN")
        with self.assertRaises(PermissionError):
            Atlas(atlas.path, "another")
        with store.connection(atlas.path) as con:
            atlas.revoke(con, "goal")
        self.assertEqual(manager.status("goal", q), "INACCESSIBLE")
        self.assertFalse(
            SemanticNavigator(atlas).search(Query("长期项目目标", "core", DAY))[
                "evidence"
            ]
        )

    def test_incremental_summary(self):
        atlas = setup(self.root)
        manager = Consolidation(atlas)
        q = Query("检查", "software", DAY, feature="path-check")

        def add(role, i):
            key = f"file-{i}"
            store.add(
                atlas.path,
                kind="experience",
                content=f"模拟器在第{i}张工作表报告路径检查结果。",
                tags=["domain=software", "consolidate=allow", role],
                origin=atlas.verifier,
                source_ref=key,
                confidence=0.9,
                memory_id=key,
                occurred_at="2026-06-01T00:00:00Z",
            )
            with store.connection(atlas.path) as con:
                atlas.enroll(con, key, {"domain": ["software"]}, consent="fiction")
            manager.register(key, "software")

        for i in range(5):
            add("path-failure", i)
        manager.tidy(DAY)
        self.assertEqual(manager.read(q)["status"], "candidate")
        for i in (5, 6):
            add("check-prevented-error", i)
        manager.tidy(DAY)
        verified = manager.read(q)
        self.assertEqual(verified["status"], "validated")
        self.assertNotIn("工作表", verified["text"])
        self.assertEqual(manager.tidy(DAY), [])
        self.assertEqual(manager.read(q), verified)
        add("check-failed", 7)
        self.assertIsNone(manager.read(q))
        manager.tidy(DAY)
        self.assertEqual(manager.read(q)["status"], "contradicted")
        self.assertIsNone(manager.read(q)["text"])
        store.delete(atlas.path, "file-5")
        self.assertIsNone(manager.read(q))
        # Reusing its former ID does not give a new fact the deleted trial's vote.
        store.add(
            atlas.path,
            kind="fact",
            content="无关的合成事实",
            origin="synthetic",
            memory_id="file-5",
            occurred_at="2026-06-02T00:00:00Z",
        )
        with store.connection(atlas.path) as con:
            atlas.enroll(con, "file-5", {"domain": ["software"]}, consent="fiction")
        self.assertIsNone(manager.read(q))
        manager.tidy(DAY)
        self.assertNotIn("file-5", [r["mid"] for r in manager.read(q)["evidence"]])

    def test_receipt_obeys_all_domain_grants(self):
        atlas = setup(self.root)
        actor = Relationship(atlas.path, atlas.owner)
        manager = Lifecycle(atlas)
        with store.connection(atlas.path) as con:
            con.execute(
                "INSERT INTO atlas_facets(id,axis,value) VALUES('lunch','domain','restricted')"
            )
        actor.set_policy(blocked_domains=["restricted"])
        q = Query("午餐", "daily", DAY)
        self.assertEqual(manager.status("lunch", q), "UNKNOWN")
        manager.classify("lunch", temporary=True)
        manager.sweep(DAY)
        self.assertEqual(manager.status("lunch", q), "UNKNOWN")
