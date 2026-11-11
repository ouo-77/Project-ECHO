"""Development regressions for audit findings. No frozen question is used here."""

from dataclasses import replace
from pathlib import Path
import sqlite3
import tempfile
import unittest

from echo_memory import store
from echo_memory.consolidation import Consolidation
from echo_memory.expression import Expression, Turn
from echo_memory.lifecycle import Lifecycle
from echo_memory.navigator import Query
from echo_memory.regulation import Regulation
from echo_memory.relationship import Relationship
from echo_memory.semantic import SemanticNavigator
from experiments.echo003.reflect.run import setup as reflection_fixture
from experiments.echo004.run import build, AS_OF
from experiments.echo0051.lifetime import measure
from experiments.echo0051.holdout import read
from experiments.echo0041.run import HERE as OLD_SEMANTIC
from experiments.echo005.run import HERE as OLD_EXPRESSION
import json


class ReliabilityAudit(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.folder = Path(temp.name)

    def test_dataset_provenance(self):
        corpus = {r["id"] for r in read("corpus.jsonl")}
        rows = read("semantic_holdout.jsonl")
        texts = {r["text"] for r in rows}
        self.assertEqual(len(texts), len(rows))
        self.assertGreaterEqual(len(texts), 200)
        previous = {
            json.loads(line)["text"]
            for line in (OLD_SEMANTIC / "questions.jsonl")
            .read_text(encoding="utf8")
            .splitlines()
        }
        self.assertFalse(texts & previous)
        self.assertTrue(all(set(r["gold"]) <= corpus for r in rows))
        previous = {
            json.loads(line)["input"]["text"]
            for line in (OLD_EXPRESSION / "scenarios.jsonl")
            .read_text(encoding="utf8")
            .splitlines()
        }
        self.assertFalse(
            {r["input"]["text"] for r in read("expression_holdout.jsonl")} & previous
        )

    def test_graph_dependency_deleted_and_reallocated(self):
        atlas, _ = build(self.folder, 100)
        q = Query("关键词=转台偏移", "met", AS_OF)
        nav = SemanticNavigator(atlas)
        self.assertTrue(nav.search(q)["evidence"])
        store.delete(atlas.path, "m008")
        store.add(
            atlas.path,
            kind="fact",
            content="另一次演练的互不相关的材料",
            memory_id="m008",
        )
        with store.connection(atlas.path) as con:
            atlas.enroll(
                con, "m008", {"domain": ["met"]}, since="2026-01-01", consent="dev"
            )
            self.assertFalse(
                con.execute("SELECT 1 FROM atlas_edges WHERE dst='m008'").fetchone()
            )
        self.assertFalse(nav.search(q)["evidence"])

    def test_proof_archive_and_revocation(self):
        atlas, _ = build(self.folder, 100)
        nav = SemanticNavigator(atlas)
        q = Query("关键词=重审结论", "met", AS_OF)
        self.assertTrue(nav.search(q)["evidence"])
        life = Lifecycle(atlas)
        life.classify("m012", core_link=True, ttl=1)
        life.sweep(AS_OF)
        self.assertFalse(nav.search(q)["evidence"])
        life.restore("m012")
        self.assertTrue(nav.search(q)["evidence"])
        with store.connection(atlas.path) as con:
            con.execute("INSERT INTO atlas_facets VALUES('m012','domain','restricted')")
        Relationship(atlas.path, atlas.owner).set_policy(blocked_domains=["restricted"])
        self.assertFalse(nav.search(q)["evidence"])

    def test_summary_dependency_is_not_an_observation(self):
        atlas, _ = build(self.folder, 100)
        conso = Consolidation(atlas)
        mid = store.add(
            atlas.path,
            kind="experience",
            content="把旧建议又写了一遍。",
            origin=atlas.verifier,
            source_ref="dev:repeated",
            confidence=1,
            tags=["consolidate=allow", "path-failure", "domain=met"],
        )
        with store.connection(atlas.path) as con:
            atlas.enroll(con, mid, {"domain": ["met"]}, consent="dev")
            atlas.link(con, mid, "m010", "derived_from", "重复转述", "dev")
        with self.assertRaises(ValueError):
            conso.register(mid, "met")

    def test_failed_batch_rolls_back_and_core_can_be_changed(self):
        atlas, _ = build(self.folder, 100)
        life = Lifecycle(atlas)
        for mid in ("m001", "m002"):
            life.classify(mid, temporary=True, ttl=0)
        with store.connection(atlas.path) as con:
            con.execute("""CREATE TRIGGER injected BEFORE DELETE ON memories WHEN OLD.id='m002'
                           BEGIN SELECT RAISE(ABORT,'injected test failure'); END""")
        with self.assertRaises(sqlite3.IntegrityError):
            life.sweep(AS_OF)
        with store.connection(atlas.path) as con:
            self.assertEqual(
                con.execute("SELECT count(*) FROM life_receipts").fetchone()[0], 0
            )
            self.assertEqual(
                con.execute(
                    "SELECT count(*) FROM memory_fts WHERE id IN ('m001','m002')"
                ).fetchone()[0],
                2,
            )
            con.execute("DROP TRIGGER injected")
        q = Query("核心标识", "met", AS_OF)
        life.classify("m001", core_link=True, ttl=0)
        life.sweep(AS_OF)
        self.assertEqual(life.status("m001", q), "ARCHIVED")
        life.classify("m001", retain=True)
        self.assertEqual(life.status("m001", q), "AVAILABLE")
        store.add(
            atlas.path,
            kind="fact",
            content="合成核心标识的明确修订",
            supersedes="m001",
            memory_id="revised",
        )
        with store.connection(atlas.path) as con:
            atlas.enroll(
                con, "revised", {"domain": ["met"]}, since="2026-01-01", consent="dev"
            )
        life.classify("revised", retain=True)
        self.assertEqual(life.status("revised", q), "AVAILABLE")
        life.erase("revised")
        self.assertNotEqual(life.status("m001", q), "AVAILABLE")
        self.assertEqual(life.status("revised", q), "UNKNOWN")

    def test_projection_after_loss_of_correction(self):
        from experiments.echo003.regulation_cases import Simulation

        sim = Simulation(self.folder, "dev-review")
        sim.event("original", "deception", 0)
        sim.finding("proof", "original", "replicated_result", "演练台账独立复核。", 1)
        self.assertGreater(sim.sample(2)["trust"], 0.5)
        store.delete(sim.actor.path, "proof")
        self.assertEqual(sim.sample(2)["trust"], 0.5)
        with self.assertRaises(sqlite3.IntegrityError):
            store.add(sim.actor.path, kind="fact", content="新内容", memory_id="proof")

    def test_removed_boundary_and_loss_proofs(self):
        from experiments.echo003.regulation_cases import Simulation

        sim = Simulation(self.folder, "dev-loss")
        sim.event("incident", "error", 0)
        sim.change("loss", "loss", "severe", 1, ["target=incident"])
        sim.change("choice", "boundary", "open", 2)
        self.assertGreater(sim.sample(3)["loss"], 0)
        store.delete(sim.actor.path, "loss")
        store.delete(sim.actor.path, "choice")
        self.assertEqual(sim.sample(3)["loss"], 0)
        self.assertEqual(sim.sample(3)["boundary"], "paused")
        sim.change("renew", "boundary", "limited", 4, previous="choice")
        self.assertEqual(sim.sample(4)["boundary"], "limited")

    def test_deleted_review_proof_retires_reflection_vote(self):
        from echo_reflect import Reflect
        from experiments.echo003.regulation_cases import Simulation, day

        sim = Simulation(self.folder, "dev-reflection")
        engine = Reflect(sim.review)
        for i, kind in enumerate(["advice"] * 4 + ["deception"]):
            mid = f"event-{i}"
            partition = "train" if i < 3 else "validation"
            sim.put(
                mid,
                "experience",
                [
                    "event=" + kind,
                    "reflect=allow",
                    "feature=test-ledger",
                    "partition=" + partition,
                    "risk=low",
                ],
                "独立模拟测量",
                i,
            )
            sim.actor.observe(mid, kind, "meter")
        sim.finding("correction", "event-4", "replicated_result", "独立复测通过", 5)
        engine.deep_reflection(now=day(6))
        self.assertEqual(engine.inspect("meter", "test-ledger")["status"], "validated")
        store.delete(sim.actor.path, "correction")
        self.assertEqual(
            engine.inspect("meter", "test-ledger")["status"], "pending_revalidation"
        )
        self.assertIsNone(engine.recommend("meter", "feature=test-ledger")["action"])
        engine.deep_reflection(now=day(14))
        self.assertEqual(
            engine.inspect("meter", "test-ledger")["status"], "insufficient"
        )

    def test_insult_cannot_change_populated_capability_model(self):
        engine = reflection_fixture(self.folder)
        engine.deep_reflection(now="2026-02-02T00:00:00Z")
        model = engine.self_model("meter")
        self.assertTrue(model)
        expression = Expression(Regulation(engine.review))
        turn = Turn(engine.actor.person, "meter", "2026-02-03T00:00:00Z", "你个废物！")
        with self.assertRaises(ValueError):
            replace(turn, profanity="false")
        for _ in range(8):
            self.assertEqual(expression.choose(turn)["stance"], "boundary")
        self.assertEqual(engine.self_model("meter"), model)

    def test_scope_validation_and_explicit_search_fields(self):
        with self.assertRaises(ValueError):
            Query("台账", "met", AS_OF, conditions="wet")
        atlas, _ = build(self.folder, 100)
        q = Query("关键词=伪标记 轴承预热", "met", AS_OF)
        self.assertFalse(SemanticNavigator(atlas).search(q)["evidence"])

    def test_lifecycle_probes_small_scale(self):
        r = measure(self.folder, 100)
        self.assertEqual(r["lost_important"], 0)
        self.assertTrue(all(r["checks"].values()), r["checks"])
        self.assertEqual(r["summary"]["recalculated"], "candidate")
        for flag in (
            "valid",
            "conditions_preserved",
            "expired_blocked",
            "repeat_stable",
            "deleted_blocked",
            "reused_id_blocked",
        ):
            self.assertTrue(r["summary"][flag], flag)
