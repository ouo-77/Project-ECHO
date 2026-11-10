import json
import subprocess
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from echo_memory import store
from echo_memory.agent import AgentMemory


class AgentTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.db = Path(tmp.name) / "agent.sqlite"
        self.agent = AgentMemory(self.db)

    def test_conflict_and_explicit_resolution(self):
        def put(value, **kwargs):
            return self.agent.remember("fact", value, origin="fixture", claim_key="Mode", **kwargs)

        first = put("quiet")
        self.assertEqual(first, put("quiet"))
        with self.assertRaises(store.MemoryConflict) as conflict:
            put("verbose")
        self.assertEqual(conflict.exception.memory_id, first)
        current = put("verbose", supersedes=first)
        self.assertEqual(store.get(self.db, current)["claim_key"], "mode")
        self.assertEqual(store.stats(self.db)["total"], 2)
        self.assertFalse(self.agent.recall("quiet"))

    def test_concurrent_corrections_have_one_successor(self):
        first = self.agent.remember("fact", "v1", origin="fixture", claim_key="version")

        def correct(value):
            try:
                return self.agent.remember("fact", value, origin="fixture", supersedes=first)
            except ValueError:
                return None

        with ThreadPoolExecutor(2) as pool:
            results = list(pool.map(correct, ["v2", "v3"]))
        self.assertEqual(results.count(None), 1)
        self.assertEqual(store.stats(self.db)["total"], 2)

    def test_reflection_evidence_and_ranking(self):
        evidence = self.agent.remember("experience", "演练结果已记录", origin="fixture")
        lesson = self.agent.reflect(evidence, "演练前检查配置", origin="review")
        row = store.get(self.db, lesson)
        self.assertEqual((row["experience_id"], row["origin"], row["visibility"]),
                         (evidence, "review", "private"))
        self.assertTrue(row["occurred_at"].endswith("+00:00"))
        self.assertEqual(store.search(self.db, "演练")[0]["id"], lesson)
        with self.assertRaises(ValueError):
            self.agent.reflect(lesson, "invalid source", origin="review")
        self.assertEqual(store.stats(self.db)["total"], 2)

    def test_budget_and_audience_isolation(self):
        self.agent.remember("fact", "示例" * 500, origin="fixture")
        self.assertEqual(len(self.agent.recall("示例", max_chars=80)), 80)
        self.assertEqual(self.agent.recall(" "), "")
        other = AgentMemory(self.db.with_name("separate.sqlite"))
        self.assertEqual(other.recall("示例"), "")

    def test_restart_between_cli_processes(self):
        cli = [sys.executable, "-m", "echo_memory", "--db", str(self.db)]
        subprocess.run(cli + ["add", "--kind", "fact", "--content", "restart fixture"],
                       check=True, capture_output=True)
        result = subprocess.check_output(cli + ["search", "restart"], text=True)
        self.assertEqual(json.loads(result)[0]["content"], "restart fixture")
