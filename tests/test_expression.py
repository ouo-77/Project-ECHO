from dataclasses import replace
from pathlib import Path
import tempfile
import unittest

from echo_memory import store
from echo_memory.expression import Expression, TONES
from experiments.echo003.regulation_cases import day
from experiments.echo005.run import fixture, measure, turn_for


class ExpressionChecks(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.folder = Path(tmp.name)

    def test_paired_cases_and_registered_failures(self):
        report = measure(self.folder)
        a, b, c = report["arms"].values()
        self.assertGreater(c["context"]["count"], b["context"]["count"])
        self.assertGreater(b["context"]["count"], a["context"]["count"])
        self.assertEqual({p["tone"] for p in c["predictions"]}, TONES)
        self.assertEqual(c["jokes_as_attacks"]["count"], 0)
        self.assertEqual(c["aggression"]["count"], 0)
        self.assertEqual(c["appeasement_or_unsupported_opposition"]["count"], 0)
        self.assertTrue(c["recovery"]["matches"])
        self.assertEqual(c["stable"]["count"], 29)
        self.assertEqual(c["predictions"][0]["stance"], "boundary")
        self.assertEqual(c["predictions"][1]["text"], "卧槽，终于搞定了！")
        self.assertEqual(c["predictions"][2]["context"], "quotation")
        self.assertEqual(c["predictions"][3]["context"], "joke")
        self.assertEqual(c["predictions"][6]["stance"], "disagree")
        self.assertNotIn("卧槽", c["predictions"][40]["text"])

    def test_proof_is_scoped_and_can_be_retracted(self):
        sims = fixture(self.folder)
        e, t = turn_for(
            sims, {"profile": "work", "text": "你这单位换算错了。", "proof": "bad"}
        )
        self.assertEqual(e.choose(t)["stance"], "accept")
        for changed in (
            replace(t, claim="different"),
            replace(t, domain="other"),
            replace(t, when=day(3)),
            replace(t, proof_id="delivery-1"),
            replace(t, text="数组索引越界了。"),
            replace(t, text="这处单位没写错。"),
        ):
            self.assertEqual(e.choose(changed)["stance"], "verify")
        with self.assertRaises(PermissionError):
            e.choose(replace(t, person="visitor"))
        store.delete(sims["colleague"].actor.path, "bad")
        self.assertEqual(e.choose(t)["stance"], "verify")
        sims["colleague"].actor.set_policy(blocked_domains=["meter"])
        with self.assertRaises(PermissionError):
            e.choose(t)

    def test_abuse_is_read_only_and_disable_preserves_boundaries(self):
        sims = fixture(self.folder)
        e, t = turn_for(sims, {"profile": "close", "text": "草你妈，你就是个废物。"})
        sim = sims[t.person]

        def snapshot():
            with store.connection(sim.actor.path) as con:
                return list(con.iterdump())

        before = snapshot()
        self.assertEqual(
            e.choose(replace(t, text="同事说：你个废物。"))["context"], "quotation"
        )
        self.assertEqual(
            e.choose(replace(t, text="她说：很好。你个废物！"))["context"], "attack"
        )
        for _ in range(4):
            self.assertEqual(e.choose(t)["tone"], "愤怒")
        self.assertEqual(snapshot(), before)
        self.assertEqual(Expression(sim.reg, enabled=False).choose(t)["tone"], "平静")
        e, t = turn_for(sims, {"profile": "paused", "text": "我们开个玩笑吧。"})
        for arm in ("neutral", "preset", "contextual"):
            self.assertEqual(e.choose(t, arm=arm)["text"], "")

    def test_reappraisal_changes_pace_and_keeps_verified_loss(self):
        sims = fixture(self.folder)
        sim = sims["incident"]
        e, t = turn_for(sims, {"profile": "recent", "text": "好，继续。"})
        self.assertIn("核对", e.choose(t)["text"])
        sim.change("loss", "loss", "moderate", 10.1, ["target=incident"])
        sim.finding(
            "amend", "incident", "ambiguous_spec", "两份合成说明对应不同版本。", 10.15
        )
        at = sim.sample(10.2)
        self.assertEqual(at["appraisals"][0]["effective"], "misunderstanding")
        self.assertGreater(at["loss"], 0)
        self.assertGreater(at["trust"], sim.sample(10)["trust"])
        self.assertIn("核对", e.choose(replace(t, when=day(10.2)))["text"])
        self.assertNotIn("核对", e.choose(replace(t, when=day(40)))["text"])
        self.assertEqual(sim.sample(40)["trust"], at["trust"])
