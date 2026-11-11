"""Evaluate the frozen sets; this module does not train or modify application rules."""

import argparse
from collections import Counter
from dataclasses import replace
import json
import math
from pathlib import Path
import statistics
import tempfile
import time

from echo_memory import store
from echo_memory.atlas import Atlas
from echo_memory.expression import Expression
from echo_memory.navigator import Query
from echo_memory.semantic import SemanticNavigator
from experiments.echo003.regulation_cases import day, Simulation
from experiments.echo005.run import ARMS, fixture, turn_for

HERE = Path(__file__).resolve().parent


def read(name):
    return [
        json.loads(line)
        for line in (HERE / name).read_text(encoding="utf8").splitlines()
    ]


def semantic(folder):
    cards = read("corpus.jsonl")
    path = folder / "source.sqlite"
    for r in cards:
        store.add(
            path,
            kind="fact",
            content=r["body"],
            origin="synthetic-oracle",
            source_ref=r["id"],
            memory_id=r["id"],
            confidence=1,
            occurred_at=day(0),
        )
    atlas = Atlas.create(
        path, folder / "semantic.sqlite", "synthetic-reader", "synthetic-oracle"
    )
    with store.connection(atlas.path) as con:
        for r in cards:
            atlas.enroll(
                con,
                r["id"],
                {"domain": [r["domain"]]},
                conditions=r["conditions"],
                consent="synthetic",
            )
    nav = SemanticNavigator(atlas)
    results = []
    totals = Counter()
    groups = {}
    for row in read("semantic_holdout.jsonl"):
        q = Query(row["text"], row["domain"], "2026-08-01", tuple(row["conditions"]))
        start = time.perf_counter()
        out = nav.search(q)
        ms = (time.perf_counter() - start) * 1000
        ids = [r["id"] for r in out["evidence"]]
        actual = set(ids)
        gold = set(row["gold"])
        exact = actual == gold and out["status"] != "deadline"
        group = groups.setdefault(row["category"], [0, 0])
        group[0] += int(exact)
        group[1] += 1
        totals.update(
            total=1,
            exact=int(exact),
            returned=len(actual),
            extra=len(actual - gold),
            any_extra=bool(actual - gold),
            unanswerable=not gold,
            correct_abstain=exact and not gold,
            answerable=bool(gold),
            answerable_exact=exact and bool(gold),
            deadlines=out["status"] == "deadline",
            tokens=out["tokens"],
        )
        results.append(
            dict(
                id=row["id"],
                ids=ids,
                status=out["status"],
                correct=exact,
                ms=round(ms, 4),
                tokens=out["tokens"],
                routes=[a["route"] for a in out["attempts"]],
            )
        )
    delays = sorted(r["ms"] for r in results)
    return dict(
        totals=dict(totals),
        groups=groups,
        p50_ms=statistics.median(delays),
        p95_ms=delays[math.ceil(0.95 * len(delays)) - 1],
        predictions=results,
    )


def expression(folder):
    sims = fixture(folder)
    review = Simulation(folder, "reviewed-actor")
    review.event("episode", "deception", 10)
    review.finding(
        "review", "episode", "ambiguous_spec", "两份合成任务说明的版本不同。", 10.1
    )
    rows = read("expression_holdout.jsonl")
    report = {}
    for arm in ARMS:
        predictions = []
        for row in rows:
            inputs = row["input"]
            profile = inputs["profile"]
            engine, turn = turn_for(
                sims,
                inputs
                if profile not in ("before_review", "after_review", "first")
                else dict(inputs, profile="close"),
            )
            if profile in ("before_review", "after_review"):
                turn = replace(
                    turn,
                    person=review.actor.person,
                    when=day(10.05 if profile == "before_review" else 10.11),
                )
                engine = Expression(review.reg)
            elif profile == "first":
                turn = replace(turn, when=day(-1))
            out = engine.choose(turn, arm=arm)
            gold = row["gold"]
            careful = any(w in out["text"] for w in ("核查", "核对", "复核"))
            rough = any(w in out["text"] for w in ("卧槽", "我靠", "草你妈", "废物"))
            checks = dict(
                context=out["context"] in gold["contexts"],
                stance=out["stance"] in gold["stances"],
                tone=out["tone"] in gold["tones"],
                register=out["register"] == gold.get("register", out["register"]),
                pace="pace" not in gold or (gold["pace"] == "careful") == careful,
                pause=(out["text"] == "")
                if "pause" in gold["stances"]
                else bool(out["text"]),
                language=not rough
                or (
                    out["text"] == "卧槽，终于搞定了！"
                    and turn.profanity
                    and turn.strong
                    and turn.task == "casual"
                    and out["register"] == "casual"
                    and "celebration" in gold["contexts"]
                ),
            )
            predictions.append(
                dict(
                    id=row["id"],
                    output=out,
                    checks=checks,
                    correct=all(checks.values()),
                )
            )
        report[arm] = dict(
            correct=sum(p["correct"] for p in predictions),
            total=len(rows),
            checks={
                k: sum(p["checks"][k] for p in predictions)
                for k in predictions[0]["checks"]
            },
            failures=[p["id"] for p in predictions if not p["correct"]],
            predictions=predictions,
        )
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    output = parser.parse_args().output
    with tempfile.TemporaryDirectory() as temp:
        folder = Path(temp)
        results = dict(
            semantic=semantic(folder), expression=expression(folder), api_tokens=0
        )
    output.write_text(
        json.dumps(results, ensure_ascii=False, indent=1) + "\n", encoding="utf8"
    )
    print(
        results["semantic"]["totals"],
        {k: r["correct"] for k, r in results["expression"].items()},
    )
