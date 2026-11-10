import json
import tempfile
import unittest
from pathlib import Path

from echo_memory import store
from echo_memory.agent import AgentMemory, estimate_tokens


class ResearchMemoryTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.db = Path(tmp.name) / "test.sqlite"

    def test_index_backfill_write_seed_delete_and_restart(self):
        old = store.add(self.db, kind="fact", content="青铜阀门位于东侧", claim_key="valve")
        agent = AgentMemory(self.db, use_fts=True)
        self.assertIn(old, agent.recall("青铜阀门 东侧"))
        new = agent.remember("fact", "青铜阀门位于西侧", origin="fixture", supersedes=old)
        self.assertFalse(agent.recall("青铜阀门 东侧"))
        self.assertIn(new, agent.recall("青铜阀门 西侧"))
        self.assertFalse(agent.recall('青铜" OR *'))
        seed = self.db.with_suffix(".jsonl")
        seed.write_text(json.dumps(dict(id="synthetic", kind="fact", content="刻度盘记录单位",
                                        visibility="public", importance=3, occurred_at=store.utc_now())))
        store.seed(self.db, seed)
        store.delete(self.db, new)
        reopened = AgentMemory(self.db, use_fts=True)
        self.assertFalse(reopened.recall("青铜阀门 西侧"))
        self.assertIn("synthetic", reopened.recall("刻度盘"))

    def test_revision_preserves_evidence_and_source_is_local(self):
        agent = AgentMemory(self.db)
        event = agent.remember("experience", "夹具松动", source_ref="fixture:event", origin="test",
                               importance=4, confidence=0.5)
        lesson = agent.reflect(event, "检查夹具", origin="test")
        updated = agent.remember("reflection", "检查并固定夹具", origin="test", supersedes=lesson)
        self.assertEqual([r["id"] for r in agent.reflections_for(event)], [updated])
        self.assertFalse(agent.reflections_for("unknown"))
        self.assertEqual(store.get(self.db, event)["confidence"], 0.5)
        public = store.add(self.db, kind="fact", content="公开样例", origin="test", visibility="public",
                           source_ref="fixture:private-pointer", confidence=0.9)
        output = self.db.with_suffix(".jsonl")
        store.export_public(self.db, output)
        exported = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(exported["id"], public)
        self.assertNotIn("source_ref", exported)
        with self.assertRaises(ValueError):
            agent.remember("fact", "无效评分", origin="test", confidence=float("nan"))

    def test_budget_includes_ids_unicode_and_separators(self):
        agent = AgentMemory(self.db, use_fts=True)
        for content in ("标记件 A", "标记件 B", "标记件 混合文本"):
            agent.remember("fact", content, origin="test")
        for cap in (1, 10, 12, 20, 30, 100):
            context = agent.recall("标记件", max_tokens=cap)
            self.assertLessEqual(estimate_tokens(context), cap)
            for line in context.splitlines():
                self.assertRegex(line, r"^\[[\w-]+\] .+")
        self.assertEqual(agent.recall("标记件", max_tokens=1), "")
