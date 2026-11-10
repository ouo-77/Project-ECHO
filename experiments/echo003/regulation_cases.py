"""Eight deterministic regulation probes plus a prospective reversal of a mistaken correction."""

import argparse
import json
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from echo_memory import store
from echo_memory.reappraisal import EvidenceReview
from echo_memory.regulation import DEFAULTS, Regulation
from echo_memory.relationship import Relationship

BASE = datetime(2026, 1, 1, tzinfo=timezone.utc)


def day(n):
    return (BASE + timedelta(days=n)).isoformat()


class Simulation:
    def __init__(self, folder, name, rules=DEFAULTS):
        self.actor = Relationship(folder / (name + ".sqlite"), name)
        self.review = EvidenceReview(self.actor)
        self.reg = Regulation(self.review, rules)
        self.inputs = []

    def put(self, key, kind, tags, note, n, domain="meter", **extra):
        fields = dict(kind=kind, tags=["person=" + self.actor.person, "domain=" + domain, *tags],
                      content=note, memory_id=key, source_ref="fixture:" + key, confidence=1.0,
                      origin="synthetic-verifier", occurred_at=day(n), **extra)
        self.inputs.append(fields)
        store.add(self.actor.path, **fields)

    def event(self, key, label, n, domain="meter"):
        self.put(key, "experience", ["event=" + label], "合成演练事件 " + label, n, domain)
        self.actor.observe(key, label, domain)

    def finding(self, key, target, finding, note, n, previous=None):
        self.put(key, "fact", ["target=" + target, "finding=" + finding], note, n)
        return self.review.apply_finding(key, previous=previous)

    def change(self, key, measure, value, n, tags=(), previous=None):
        self.put(key, "fact", ["measure=" + measure, "value=" + value, *tags],
                 "合成台账确认 " + measure + " / " + value, n)
        self.reg.record(key, previous=previous)

    def sample(self, n):
        return self.reg.at("meter", day(n))


def run_cases(folder):
    ab = Simulation(folder, "ab")
    ab.event("ab-incident", "deception", 0)
    start, week, month = (ab.sample(n) for n in (0, 7, 30))
    assert month["levels"]["short"] < week["levels"]["short"] < start["levels"]["short"]
    assert start["trust"] == week["trust"] == month["trust"]
    retained = {key: level / start["levels"][key] for key, level in week["levels"].items()}
    assert retained["short"] < retained["mood"] < retained["tendency"]

    cases = {"A": [start, week, month], "B": [start, month]}
    sims = [ab]
    for name, with_loss in (("C", False), ("D", True)):
        sim = Simulation(folder, name)
        sims.append(sim)
        sim.event("incident", "deception", 0)
        if with_loss:
            sim.change("loss-proof", "loss", "moderate", .1, ["target=incident"])
        before = sim.sample(.49)
        sim.finding("clarify", "incident", "ambiguous_spec", "复查两版任务书确认沟通口径不同。", .5)
        after = sim.sample(.5)
        assert after["trust"] == .5 and after["levels"]["short"] > 0
        assert after["levels"]["short"] < before["levels"]["short"]
        assert sim.sample(.49) == before
        cases[name] = [before, after]
    assert cases["D"][1]["loss"] > 0 and cases["D"][1]["relationship_closeness"] < cases["C"][1]["relationship_closeness"]
    # A second, independent finding supersedes the review; historical decisions remain queryable.
    sim.finding("reverse-review", "incident", "knowing_fabrication", "独立复勘证实原始台账被知情篡改。", 1, "clarify")
    rollback = sim.sample(1)
    assert rollback["trust"] == cases["D"][0]["trust"] and sim.sample(.5) == cases["D"][1]

    severe = Simulation(folder, "e")
    severe.event("severe", "deception", 0)
    severe.change("severe-loss", "loss", "severe", .1, ["target=severe"])
    severe.change("pause", "boundary", "paused", 1)
    severe.change("forgive", "forgiveness", "forgiven", 5)
    e = severe.sample(60)
    assert e["forgiveness"] == "forgiven" and e["action"] == "pause_interaction"
    assert e["reconciliation"] == "undecided" and e["trust"] < .5
    cases["E"] = [severe.sample(1), e]

    fix = Simulation(folder, "f")
    fix.event("harm", "deception", 0)
    fix.change("limit", "boundary", "limited", .1)
    for i in range(1, 11):
        fix.event(f"repair-{i}", "repair", i)
    before_choice = fix.sample(10)
    fix.change("agreement", "reconciliation", "renewed", 11, ["consent=both"])
    renewed = fix.sample(11)
    assert renewed["trust"] > .65 and before_choice["reconciliation"] == "undecided"
    assert renewed["boundary"] == "limited" and renewed["action"] == "verify"
    assert renewed["forgiveness"] == "undecided"
    cases["F"] = [fix.sample(0), before_choice, renewed]

    reactivated = Simulation(folder, "g")
    reactivated.event("first", "deception", 0)
    reactivated.event("unrelated", "error", 13, "other")
    g_before = reactivated.sample(13)
    reactivated.event("similar", "deception", 14)
    g_after = reactivated.sample(14)
    assert g_after["levels"]["short"] > g_before["levels"]["short"] and "unrelated" not in g_after["impulses"]
    cases["G"] = [g_before, g_after]

    alternate = json.loads(DEFAULTS.read_text(encoding="utf-8"))
    alternate["half_life_days"]["short"] = 4
    config = folder / "slow.json"
    config.write_text(json.dumps(alternate), encoding="utf-8")
    slow = Simulation(folder, "slow", config)
    slow.event("ab-incident", "deception", 0)
    fast_result, slow_result = ab.sample(2), slow.sample(2)
    assert fast_result["pace"] == "normal" and slow_result["pace"] == "slow_review"
    assert fast_result["trust"] == slow_result["trust"]
    cases["H"] = [fast_result, slow_result]
    sims.extend([severe, fix, reactivated, slow])
    return dict(cases=cases, reversal=rollback, inputs={s.actor.person: s.inputs for s in sims})


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    output = parser.parse_args().output
    with tempfile.TemporaryDirectory() as tmp:
        results = run_cases(Path(tmp))
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(results, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("Passed regulation cases A-H and audit reversal")
