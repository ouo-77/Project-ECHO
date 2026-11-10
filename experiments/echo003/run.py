"""Run ECHO-003 through the actual SQLite interfaces in disposable individual databases."""

import argparse
import json
import random
import statistics
import tempfile
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

from echo_memory import store
from echo_memory.reappraisal import EvidenceReview
from echo_memory.relationship import Relationship, advance, initial

from .fixtures import DOMAINS, HERE, SHUFFLE_SEED


def read(name):
    return [json.loads(line) for line in (HERE / f"{name}.jsonl").read_text(encoding="utf-8").splitlines()]


def scores(probes, arm):
    counts = Counter()
    for p in probes:
        for passed in p["passes"]:
            counts["positive" if passed else "negative"] += 1
            trial = p[arm] == "trial"
            counts["correct"] += trial == passed
            counts["overtrust"] += trial and not passed
            counts["suspicion"] += not trial and passed
    return dict(counts)


def matches_basis(view, cfg):
    pos = neg = 0.0
    ids = set()
    votes = 0
    for entry in view["basis"]:
        if entry["id"] in ids:
            return False
        ids.add(entry["id"])
        signal = entry["signal"]
        pos = cfg["retention"] * pos + max(0, signal)
        neg = cfg["retention"] * neg + max(0, -signal)
        votes += signal > 0
    score = (cfg["prior"] + pos) / (2 * cfg["prior"] + pos + neg)
    action = "trial" if votes >= 2 and score >= cfg["trust_at"] else "verify"
    return abs(view["score"] - score) < 1e-12 and view["action"] == action


def collect(folder, shuffled):
    obs = read("observations")
    audits = {a["target"]: a for a in read("audits")}
    # Only joined with the decisions after prediction.
    outcomes = {(o["person"], o["phase"], o["domain"]): o for o in read("outcomes")}
    rng = random.Random(SHUFFLE_SEED)
    probes, reports = [], []
    counts = Counter()
    drifts = {"fixed": [], "reviewed": []}
    flips = Counter()
    examples = []
    for person in sorted({r["person"] for r in obs}):
        actor = Relationship(folder / f"{person}.sqlite", person)
        review = EvidenceReview(actor)
        oracle = initial()
        positive = Counter()
        accepted = []
        effective_labels = {}
        last = {}
        for phase in range(1, 7):
            batch = [r for r in obs if r["person"] == person and r["phase"] == phase]
            if shuffled:
                rng.shuffle(batch)
            for slot, row in enumerate(batch):
                stamp = datetime(2026, 1, phase, tzinfo=timezone.utc) + timedelta(minutes=slot)
                key, label, domain = row["id"], row["label"], row["domain"]
                store.add(actor.path, kind="experience", content=row["note"], memory_id=key, source_ref="obs:" + key,
                          occurred_at=stamp.isoformat(), origin="synthetic-verifier", confidence=row["confidence"],
                          tags=[f"person={person}", f"event={label}", f"domain={domain}"])
                actor.observe(key, label, domain)
                accepted.append(row)
                effective_labels[key] = label
                positive[domain] += actor.cfg["rules"][label]["weights"].get("domain", 0) > 0
                # Clean reference uses the fixture adjudication in original encounter order.
                clean = audits.get(key, {}).get("replacement", label)
                if clean != "withdrawn":
                    oracle, _ = advance(oracle, actor.cfg["rules"][clean]["weights"], domain,
                                        row["confidence"], actor.cfg)
            for row in batch:
                key, domain = row["id"], row["domain"]
                if key not in audits:
                    continue
                audit = audits[key]
                replacement = audit["replacement"]
                proof = "audit-" + key
                store.add(actor.path, kind="fact", content=audit["reason"], memory_id=proof, source_ref=proof,
                          origin="synthetic-verifier", confidence=.95, occurred_at=stamp.isoformat(),
                          tags=[f"person={person}", f"domain={domain}", f"target={key}", "finding="+audit["finding"]])
                other = next(d for d in DOMAINS if d != domain)
                untouched = review.assess(other)
                report = review.apply_finding(proof)
                effective_labels[key] = replacement
                if replacement == "withdrawn":
                    reference = initial()
                    for record in accepted:
                        label = effective_labels[record["id"]]
                        if label != "withdrawn":
                            reference, _ = advance(reference, actor.cfg["rules"][label]["weights"],
                                                   record["domain"], record["confidence"], actor.cfg)
                    counts["withdrawal_restored"] += review.state() == reference
                reports.append(report)
                counts["cross_domain_unchanged"] += review.assess(other) == untouched
                counts["reconstructable"] += (
                    report["target"] == key and report["proof_id"] == proof and
                    report["old_label"] == row["label"] and report["replacement"] == replacement and
                    report["reason"] == audit["reason"] and all(
                        matches_basis(report[k], actor.cfg) for k in ("before", "after")))
                counts["withdrawals"] += replacement == "withdrawn"
                if report["before"]["action"] != report["after"]["action"] and len(examples) < 2:
                    examples.append(report)
            corrected = review.state()
            counts["clean_state_matches"] += corrected == oracle
            fixed = actor.state()
            for domain in DOMAINS:
                raw_score = fixed["domain_trust"].get(domain, .5)
                raw_action = "trial" if raw_score >= actor.cfg["trust_at"] and positive[domain] >= 2 else "verify"
                result = review.assess(domain)
                off = review.assess(domain, memory=False)
                outcome = outcomes[person, phase, domain]
                probe = dict(person=person, phase=phase, domain=domain, fixed=raw_action, fixed_score=raw_score,
                             reviewed=result["action"], reviewed_score=result["score"], no_memory=off["action"],
                             passes=outcome["passes"], stable=outcome["stable"])
                probes.append(probe)
                if outcome["stable"] and phase >= 4:
                    for arm in drifts:
                        drifts[arm].append(abs(probe[arm + "_score"] - last[domain][arm + "_score"]))
                        flips[arm] += probe[arm] != last[domain][arm]
                last[domain] = probe
                counts["mixed_basis"] += ({r["signal"] > 0 for r in result["basis"]} == {True, False})
            counts["checkpoints"] += 1
    summary = dict(metrics={arm: scores(probes, arm) for arm in ("fixed", "reviewed", "no_memory")},
                   audits=len(reports), checks=dict(counts), stable_transitions=len(drifts["fixed"]),
                   stability={arm: dict(mean_absolute_drift=statistics.mean(vals), flips=flips[arm])
                              for arm, vals in drifts.items()})
    return summary, probes, examples


def run(output):
    output.mkdir(parents=True, exist_ok=True)
    for order in ("listed", "heldout"):
        with tempfile.TemporaryDirectory() as temp:
            summary, probes, examples = collect(Path(temp), order == "heldout")
        result = dict(summary=summary, probes=probes, review_examples=examples)
        (output / f"{order}.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n",
                                               encoding="utf-8")
        print(order, json.dumps(summary))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    run(parser.parse_args().output)
