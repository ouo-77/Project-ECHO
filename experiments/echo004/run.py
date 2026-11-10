"""Synthetic scaling study. Run from the repository root with python -m experiments.echo004.run."""

import argparse
import json
import math
import statistics
import tempfile
import time
from pathlib import Path

from echo_memory import fulltext, store
from echo_memory.atlas import Atlas
from echo_memory.navigator import Navigator, Query
from echo_memory.reappraisal import EvidenceReview
from echo_memory.relationship import Relationship

HERE = Path(__file__).resolve().parent
AS_OF = "2026-03-01"
SPECIAL = {
    1: "蓝芯刻度为毫米。",
    2: "早班采样。",
    3: "晚班采样。",
    4: "赤塔协作交付。",
    5: "阀组释压至零位。",
    6: "转台偏移报告。",
    7: "复测发现支座松动。",
    8: "位移测量原始样本。",
    9: "操作章程采用旧流程。",
    10: "操作章程采用新流程。",
    11: "虹片读数偏低。",
    12: "标记为错误的观察。",
    13: "相位校核，西站记录。",
    14: "启动时先预热轴承。",
    15: "暖机一词的排版说明。",
    16: "限定样品为干态。",
    17: "实验猜测有待核验。",
    18: "保温处理。",
    19: "旧称回温的印刷标签。",
    20: "温控准备。",
    21: "旧称热启的印刷标签。",
}


def build(folder, n):
    src, dest = folder / "source.sqlite", folder / "atlas.sqlite"
    actor = Relationship(src, "synthetic")
    review = EvidenceReview(actor)
    with store.connection(src) as con:
        for i in range(1, n - 1):
            key = f"m{i:03}"
            body = SPECIAL.get(i, f"相位校核模拟样本 {i}。")
            kind = (
                "reflection" if i == 17 else "experience" if i in (11, 12) else "fact"
            )
            day = {2: "03", 3: "04"}.get(i, "01")
            tags = (
                ["person=synthetic", "event=error", "domain=met"]
                if i in (11, 12)
                else []
            )
            con.execute(
                """INSERT INTO memories(id,occurred_at,kind,content,importance,origin,tags)
                VALUES(?,?,?,?,?,?,?)""",
                (
                    key,
                    f"2026-01-{day}T00:00:00+00:00",
                    kind,
                    body,
                    3,
                    "synthetic-verifier",
                    json.dumps(tags),
                ),
            )
            if tags:
                con.execute(
                    "INSERT INTO evidence VALUES(?,?,?)", (key, "obs:" + key, 1)
                )
    for i in (11, 12):
        actor.observe(f"m{i:03}", "error", "met")
    for i, finding, note in (
        (11, "wrong_asset", "复核批号不符。"),
        (12, "replicated_result", "重审结论为合格。"),
    ):
        key = f"m{i:03}"
        proof = "proof" + str(i)
        store.add(
            src,
            kind="fact",
            content=note,
            memory_id=proof,
            source_ref=proof,
            origin="synthetic-verifier",
            occurred_at="2026-02-01T00:00:00+00:00",
            confidence=1,
            tags=[
                "person=synthetic",
                "domain=met",
                "target=" + key,
                "finding=" + finding,
            ],
        )
        review.apply_finding(proof)
    start = time.perf_counter()
    fulltext.enable(src)
    fts_ms = (time.perf_counter() - start) * 1000
    start = time.perf_counter()
    atlas = Atlas.create(src, dest, "synthetic", "bench-oracle")
    with store.connection(dest) as con:
        for i in range(1, n - 1):
            facets = dict(
                domain=["met"], project=["北站"], person=["桐舟"], event=["观测"]
            )
            if i == 4:
                facets["project"] = ["赤塔", "协作"]
            if i == 13:
                facets["project"] = ["西站"]
            alias = {5: ["卸压", "泄压"], 14: ["暖机"], 18: ["回温"], 20: ["热启"]}.get(
                i
            )
            if alias:
                facets["alias"] = alias
            atlas.enroll(
                con,
                f"m{i:03}",
                facets,
                consent="synthetic-protocol",
                until="2026-02-01" if i == 9 else None,
                conditions=["wet"] if i == 16 else [],
            )
        for proof in ("proof11", "proof12"):
            atlas.enroll(con, proof, {"domain": ["met"]}, consent="synthetic-protocol")
        for a, b in (("m006", "m007"), ("m007", "m008")):
            atlas.link(con, a, b, "derived_from", "合成报告引用该观察", "fixture:chain")
    build_ms = (time.perf_counter() - start) * 1000

    def update(db, enriched):
        times = []
        for _ in range(5):
            start = time.perf_counter()
            with store.connection(db) as con:
                con.execute("UPDATE memories SET content=content||'。' WHERE id='m001'")
                con.execute(
                    "UPDATE memories SET content=? WHERE id='m001'", (SPECIAL[1],)
                )
                if enriched:
                    con.execute(
                        "INSERT INTO atlas_facets VALUES('m001','project','临时')"
                    )
                    con.execute(
                        "DELETE FROM atlas_facets WHERE id='m001' AND axis='project' AND value='临时'"
                    )
            times.append((time.perf_counter() - start) * 1000)
        return statistics.median(times)

    return atlas, dict(
        fts_ms=fts_ms,
        copy_and_atlas_ms=build_ms,
        fts_bytes=src.stat().st_size,
        atlas_bytes=dest.stat().st_size,
        fts_update_ms=update(src, False),
        atlas_update_ms=update(dest, True),
    )


def measure(folder, n):
    atlas, costs = build(folder, n)
    nav = [Navigator(atlas), Navigator(atlas), Navigator(atlas, adaptive=True)]
    # Disjoint calibration labels. Probe both routes, including unsuccessful routes.
    for term, expected in (("回温", "m018"), ("热启", "m020")):
        q = Query("关键词=" + term, "met", AS_OF)
        for route in ("alias", "lexical"):
            r = nav[1].search(q, calibration_route=route)
            nav[2].feedback(
                r["run"],
                [expected],
                verifier="bench-oracle",
                source="calibration:" + term,
            )
    costs["calibrated_bytes"] = atlas.path.stat().st_size
    cases = [
        json.loads(s)
        for s in (HERE / "queries.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    times = [[], [], []]
    rows = [[], [], []]
    for rep in range(4):  # one warm sweep for every arm, three measured sweeps
        for i, case in enumerate(cases):
            q = Query(case["text"], case["domain"], AS_OF)
            for offset in range(3):
                arm = (offset + i + rep) % 3
                start = time.perf_counter()
                r = nav[arm].search(q, baseline=arm == 0)
                elapsed = (time.perf_counter() - start) * 1000
                if rep:
                    times[arm].append(elapsed)
                    ids = [v["id"] for v in r["evidence"]]
                    rows[arm].append(
                        dict(
                            case=case["kind"],
                            ids=ids,
                            gold=case["gold"],
                            tokens=r["tokens"],
                            status=r["status"],
                            routes=[a["route"] for a in r["attempts"]],
                        )
                    )
    metrics = {}
    for name, records, ts in zip("ABC", rows, times):
        positives = [r for r in records if r["gold"]]

        def hits(r):
            return len(set(r["ids"]) & set(r["gold"]))

        returned = sum(len(r["ids"]) for r in records)
        metrics[name] = dict(
            recall=statistics.mean(hits(r) / len(r["gold"]) for r in positives),
            mrr=statistics.mean(
                next((1 / i for i, k in enumerate(r["ids"], 1) if k in r["gold"]), 0)
                for r in positives
            ),
            exact=statistics.mean(
                set(r["ids"]) == set(r["gold"])
                and r["status"] != "deadline"
                and (r["case"] != "greeting" or r["status"] == "no_retrieval")
                for r in records
            ),
            false_return=(returned - sum(map(hits, records))) / max(1, returned),
            p50=statistics.median(ts),
            p95=sorted(ts)[math.ceil(0.95 * len(ts)) - 1],
            attempts=statistics.mean(len(r["routes"]) for r in records),
            tokens=statistics.mean(r["tokens"] for r in records),
            api_tokens=0,
            deadlines=sum(r["status"] == "deadline" for r in records),
            responses=records,
        )
    return dict(
        size=n, index=costs, metrics=metrics, db_with_audit=atlas.path.stat().st_size
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    out = parser.parse_args().output
    results = []
    for n in (100, 1000, 10000):
        with tempfile.TemporaryDirectory() as tmp:
            results.append(measure(Path(tmp), n))
    out.write_text(
        json.dumps(results, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
    )
    for r in results:
        print(
            r["size"],
            r["index"],
            {
                k: {s: round(v, 3) for s, v in m.items() if s != "responses"}
                for k, m in r["metrics"].items()
            },
        )
