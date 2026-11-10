"""Only score() reads held-out bench measurements, after both prediction passes finish."""

import argparse
import hashlib
import json
import math
import statistics
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from echo_memory import store
from echo_memory.reappraisal import EvidenceReview
from echo_memory.relationship import Relationship
from echo_reflect import Reflect

HERE = Path(__file__).resolve().parent
START = datetime(2026, 2, 1, tzinfo=timezone.utc)


def rows(name):
    return [json.loads(s) for s in (HERE / (name + ".jsonl")).read_text(encoding="utf-8").splitlines()]


def setup(folder):
    actor = Relationship(folder / "bench.sqlite", "reflect-fixture")
    engine = Reflect(EvidenceReview(actor))
    for i, row in enumerate(rows("observations")):
        tags = ["person=" + actor.person, "event=" + row["kind"], "reflect=allow"]
        tags += [key + "=" + row[key] for key in ("domain", "feature", "partition", "risk")]
        store.add(actor.path, kind="experience", content="合成仪表复核 " + row["feature"],
                  memory_id=row["id"], occurred_at=(START+timedelta(minutes=i)).isoformat(),
                  origin="synthetic-verifier", source_ref="bench:" + row["id"], confidence=1, tags=tags)
        actor.observe(row["id"], row["kind"], row["domain"])
    return engine


def predict(engine):
    predictions, elapsed = [], []
    for task in rows("tasks"):
        start = time.perf_counter_ns()
        suggestion = engine.recommend(task["domain"], task["task"], max_tokens=90)
        elapsed.append((time.perf_counter_ns()-start)/1e6)
        fallback = "verify" if task["risk"] != "low" else engine.review.assess(task["domain"])["action"]
        predictions.append(dict(id=task["id"], action=suggestion["action"] or fallback, **{
            "version": suggestion["version"], "tokens": suggestion["tokens"]}))
    return predictions, elapsed


def score(predictions):
    truth = {r["id"]: "trial" if r["abs_error_um"] <= r["limit_um"] else "verify"
             for r in rows("heldout_measurements")}
    return [p["id"] for p in predictions if p["action"] != truth[p["id"]]]


def experiment(folder):
    engine = setup(folder)
    first = engine.daily_reflection(now="2026-02-02T00:00:00Z")
    engine.deep_reflection(now="2026-02-02T00:01:00Z")
    before, _ = predict(engine)
    blocked, attempts, impacts = 0, 0, []
    revisions = rows("findings")
    for i, audit in enumerate(revisions):
        target, domain = audit["target"], audit["domain"]
        proof = "review-" + target
        stamp = (START+timedelta(days=2, minutes=i)).isoformat()
        store.add(engine.actor.path, kind="fact", content=audit["reason"], occurred_at=stamp, memory_id=proof,
                  source_ref=proof, origin="synthetic-verifier", confidence=1,
                  tags=["person=reflect-fixture", "domain=" + domain, "target="+target, "finding="+audit["finding"]])
        engine.review.apply_finding(proof)
        impacted = engine.impact(target)
        current = next(r for r in impacted if r["dirty"])
        attempts += 1
        blocked += engine.recommend(domain, current["feature"])["version"] is None
        impacts.append(dict(proof=proof, target=target, version=current["id"], pending=current["dirty"]))
    start = time.perf_counter_ns()
    updated = engine.deep_reflection(now="2026-02-10T00:00:00Z")
    rebuild_ms = (time.perf_counter_ns()-start)/1e6
    unchanged = engine.deep_reflection(now="2026-02-18T00:00:00Z")
    after, _ = predict(engine)
    elapsed = []
    for _ in range(5):
        _, times = predict(engine)
        elapsed.extend(times)
    engine.control("disabled")
    disabled, _ = predict(engine)
    engine.control("enabled")
    obs = rows("observations")
    corrected = {r["target"]: r for r in revisions}
    traced = total = 0
    for domain, feature in sorted({(r["domain"], r["feature"]) for r in obs}):
        head = engine.inspect(domain, feature)
        deps = {d["event"]: d for d in head["deps"]}
        for r in [r for r in obs if r["domain"] == domain and r["feature"] == feature]:
            d = deps.get(r["id"])
            total += 1
            proof = "review-"+r["id"] if r["id"] in corrected else None
            traced += bool(d and d["source_ref"] == "bench:"+r["id"] and d["proof"] == proof
                           and d["partition"] == r["partition"])
    # This is the first read of the held-out scoring file.
    misses = {k: score(v) for k, v in (("before", before), ("reviewed", after), ("disabled", disabled))}
    return dict(heldout_sha256=hashlib.sha256((HERE/"heldout_measurements.jsonl").read_bytes()).hexdigest(),
                initial=first, updated=updated, duplicate=unchanged, total_tasks=len(after), wrong_ids=misses,
                corrected_wrong=len(set(misses["before"])-set(misses["reviewed"])),
                newly_wrong=len(set(misses["reviewed"])-set(misses["before"])),
                blocked=blocked, attempts=attempts, trace=[traced,total], impacts=impacts,
                retrieval_ms=dict(n=len(elapsed), median=statistics.median(elapsed),
                                  p95=sorted(elapsed)[math.ceil(.95*len(elapsed))-1]),
                rebuild_ms=rebuild_ms, context_tokens=sum(p["tokens"] for p in after),
                before=before, reviewed=after, disabled=disabled, self_model=engine.self_model("meter"))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    output = parser.parse_args().output
    with tempfile.TemporaryDirectory() as tmp:
        result = experiment(Path(tmp))
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    print({k:result[k] for k in ("wrong_ids","corrected_wrong","newly_wrong","blocked","attempts","trace",
                                 "retrieval_ms","rebuild_ms","context_tokens")})
