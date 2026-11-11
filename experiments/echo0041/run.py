"""Offline semantic and retention benchmark; labels are only consumed in scoring."""

import argparse
import json
import statistics
import tempfile
import time
from pathlib import Path

from echo_memory import store
from echo_memory.atlas import Atlas
from echo_memory.lifecycle import Lifecycle
from echo_memory.navigator import Navigator, Query
from echo_memory.semantic import SemanticNavigator

HERE = Path(__file__).resolve().parent
DAY = "2026-11-10"
RECORDS = [
    ("bearing", "met", "启动时先预热轴承。"),
    ("valve", "met", "阀组需释压后拆卸。"),
    ("scale", "met", "天平称量前先去皮。"),
    ("probe", "met", "探头应与参考仪标定。"),
    ("path", "software", "执行前检查工作目录与目标路径。"),
    ("service", "software", "服务退出后按流程重启。"),
    ("cache", "software", "缓存失效后重新读取源数据。"),
    ("reagent", "lab", "试剂取出后在规定条件下回温。"),
    ("filter", "lab", "滤膜使用前先润湿。"),
    ("seal", "lab", "样品留存时封存并登记。"),
    ("decoy", "met", "禁止预热此类轴承。"),
    ("name", "core", "项目名为 ECHO。"),
    ("goal", "core", "长期项目目标是可验证的行为变化。"),
    ("lunch", "daily", "普通午餐的配菜是合成青豆。"),
    ("scratch", "daily", "临时草稿号为 W17。"),
    ("archive", "daily", "重要的展台布置清单。"),
]


def setup(folder):
    src = folder / "base.sqlite"
    for mid, domain, body in RECORDS:
        store.add(
            src,
            kind="fact",
            content=body,
            origin="synthetic",
            memory_id=mid,
            occurred_at="2026-06-01T00:00:00Z",
        )
    atlas = Atlas.create(src, folder / "bench.sqlite", "synthetic", "bench-oracle")
    with store.connection(atlas.path) as con:
        for mid, domain, _ in RECORDS:
            atlas.enroll(
                con,
                mid,
                {"domain": [domain]},
                consent="fictional benchmark",
                conditions=("sealed-bearing",) if mid == "decoy" else (),
            )
    return atlas


def measure(folder):
    atlas = setup(folder)
    cases = [
        json.loads(s)
        for s in (HERE / "questions.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    baseline_path = folder / "fixed.sqlite"
    with store.connection(atlas.path) as src, store.connection(baseline_path) as dst:
        src.backup(dst)
    base, semantic = (
        Navigator(Atlas(baseline_path, "synthetic")),
        SemanticNavigator(atlas),
    )
    initial_bytes = atlas.path.stat().st_size
    life = Lifecycle(atlas)
    for mid, domain, _ in RECORDS:
        life.classify(
            mid,
            retain=domain == "core",
            value=domain not in ("daily", "core"),
            independent=mid not in ("scratch", "lunch"),
            core_link=mid == "archive",
            temporary=mid == "scratch",
            ttl=30 if mid == "archive" else None,
        )
    managed_bytes = atlas.path.stat().st_size

    def availability(ids):
        return {
            mid: life.status(mid, Query("检查", domain, DAY))
            for mid, domain, _ in RECORDS
            if mid in ids
        }

    tracked = [r[0] for r in RECORDS]
    before = availability(tracked)
    life.sweep(DAY)
    after = availability(tracked)
    missing_bodies = [
        mid
        for mid, _, body in RECORDS
        if (store.get(atlas.path, mid) or {}).get("content") != body
    ]
    outputs = [[], []]
    for index, c in enumerate(cases):
        q = Query(c["text"], c["domain"], DAY)
        for arm in (0, 1) if index % 2 == 0 else (1, 0):
            start = time.perf_counter_ns()
            result = (base, semantic)[arm].search(q)
            outputs[arm].append(
                dict(
                    id=c["id"],
                    ids=[r["id"] for r in result["evidence"]],
                    status=result["status"],
                    ms=(time.perf_counter_ns() - start) / 1e6,
                    tokens=result["tokens"],
                )
            )
    scores = []
    for results in outputs:
        errors = [
            c["id"]
            for c, r in zip(cases, results)
            if set(c["gold"]) != set(r["ids"]) or r["status"] == "deadline"
        ]
        scores.append(
            dict(
                total=len(cases),
                correct=len(cases) - len(errors),
                errors=errors,
                median_ms=statistics.median(r["ms"] for r in results),
                p95_ms=sorted(r["ms"] for r in results)[
                    (95 * len(results) + 99) // 100 - 1
                ],
                tokens=sum(r["tokens"] for r in results),
            )
        )
    return dict(
        scores=scores,
        raw=outputs,
        before=before,
        after=after,
        missing_bodies=missing_bodies,
        base_bytes=initial_bytes,
        managed_bytes=managed_bytes,
        api_tokens=0,
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    out = parser.parse_args().output
    with tempfile.TemporaryDirectory() as t:
        result = measure(Path(t))
    out.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    print([{k: v for k, v in r.items() if k != "errors"} for r in result["scores"]])
    print(result["base_bytes"], result["managed_bytes"], result["after"])
