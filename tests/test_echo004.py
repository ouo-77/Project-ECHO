import tempfile
import unittest
from pathlib import Path

from echo_memory import store
from echo_memory.atlas import Atlas
from echo_memory.navigator import Navigator, Query
from echo_memory.reappraisal import EvidenceReview
from echo_memory.relationship import Relationship
from echo_reflect import Reflect
from experiments.echo003.reflect.run import setup
from experiments.echo004.run import AS_OF, build


class AtlasTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.folder = Path(tmp.name)
        self.atlas, _ = build(self.folder, 100)
        self.nav = Navigator(self.atlas)

    def ids(self, text, **options):
        q = Query(text, "met", AS_OF, **options)
        return [v["id"] for v in self.nav.search(q)["evidence"]]

    def test_migration_owner_and_rollback(self):
        source = self.folder / "source.sqlite"
        before = source.read_bytes()
        with self.assertRaises(PermissionError):
            Atlas.create(source, self.folder / "wrong.sqlite", "wrong", "oracle")
        self.assertFalse((self.folder / "wrong.sqlite").exists())
        with self.assertRaises(FileExistsError):
            Atlas.create(source, self.atlas.path, "synthetic", "oracle")
        with self.assertRaises(PermissionError):
            Atlas(self.atlas.path, "wrong")
        with store.connection(self.atlas.path) as con:
            con.execute("UPDATE atlas_items SET live=0 WHERE id='m001'")
        self.assertEqual(self.ids("关键词=蓝芯刻度"), [])
        self.assertEqual(before, source.read_bytes())
        self.assertIsNotNone(store.get(source, "m001"))

    def test_scope_validity_and_chain(self):
        self.assertEqual(self.ids("2026-01-03到2026-01-04"), ["m002", "m003"])
        self.assertEqual(self.ids("项目=协作 人物=桐舟"), ["m004"])
        self.assertEqual(self.ids("关键词=限定样品"), [])
        self.assertEqual(self.ids("关键词=限定样品", conditions=("wet",)), ["m016"])
        self.assertEqual(self.ids("关键词=操作章程"), ["m010"])
        self.assertEqual(self.ids("关键词=虹片读数"), [])
        self.assertEqual(self.ids("关键词=重审结论"), ["proof12"])
        self.assertEqual(self.ids("依据链 起点=m006"), ["m006", "m007", "m008"])
        with store.connection(self.atlas.path) as con:
            con.execute("UPDATE atlas_items SET live=0 WHERE id='m008'")
        self.assertEqual(self.ids("依据链 起点=m006"), [])
        self.assertEqual(self.ids("关键词=转台偏移"), [])

    def test_feedback_and_budgets(self):
        nav = Navigator(self.atlas, adaptive=True)
        task = Query("关键词=暖机", "met", AS_OF)
        self.assertEqual(nav.plan(task)["selected_route"], "lexical")
        entries = []
        for term, mid in (("回温", "m018"), ("热启", "m020")):
            q = Query("关键词=" + term, "met", AS_OF)
            r = nav.search(q, calibration_route="alias")
            with self.assertRaises(PermissionError):
                nav.feedback(r["run"], [mid], verifier="self", source=term)
            v = nav.feedback(r["run"], [mid], verifier="bench-oracle", source=term)
            entries.append((r["run"], v, term))
            self.assertTrue(nav.evaluation(r["run"])["exact"])
        self.assertEqual(nav.plan(task)["selected_route"], "alias")
        for run, v, term in entries:
            nav.feedback(run, None, previous=v, verifier="bench-oracle", source=term)
            self.assertIsNone(nav.evaluation(run)["exact"])
        self.assertEqual(nav.plan(task)["selected_route"], "lexical")
        self.assertEqual(nav.search(Query("你好", "met", AS_OF))["attempts"], [])
        result = Navigator(self.atlas, attempts=1, token_budget=1).search(task)
        self.assertEqual(
            (result["status"], len(result["attempts"])), ("insufficient", 1)
        )

    def test_reflect_live_version(self):
        engine = setup(self.folder)
        engine.deep_reflection(now="2026-02-02T00:00:00Z")
        h = engine.inspect("meter", "cold-start")
        mid = store.add(
            engine.actor.path,
            kind="reflection",
            content="工序模拟待复核",
            origin="fixture",
        )
        atlas = Atlas.create(
            engine.actor.path,
            self.folder / "reflect-atlas.sqlite",
            engine.actor.person,
            "oracle",
        )
        with store.connection(atlas.path) as con:
            for dep in h["deps"]:
                atlas.enroll(
                    con, dep["event"], {"domain": ["meter"]}, consent="fixture"
                )
            atlas.enroll(
                con,
                mid,
                {"domain": ["meter"]},
                consent="fixture",
                version=h["id"],
                since="2026-01-01",
            )
        nav = Navigator(atlas)
        q = Query("关键词=工序模拟", "meter", AS_OF, feature="cold-start")
        self.assertEqual(nav.search(q)["evidence"], [dict(id=mid, text="verify")])
        self.assertFalse(
            nav.search(Query(q.text, q.domain, q.as_of, feature="different"))[
                "evidence"
            ]
        )
        store.delete(atlas.path, h["deps"][0]["event"])
        self.assertFalse(nav.search(q)["evidence"])
        reflect = Reflect(EvidenceReview(Relationship(atlas.path, engine.actor.person)))
        self.assertEqual(
            reflect.inspect("meter", "cold-start")["status"], "pending_revalidation"
        )
