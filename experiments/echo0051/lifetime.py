"""Stress the existing owner-bound APIs; bulk SQL is only used for synthetic source rows."""

import argparse
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import statistics
import subprocess
import sys
import tempfile
import time

from echo_memory import store
from echo_memory.atlas import Atlas
from echo_memory.consolidation import Consolidation
from echo_memory.lifecycle import Lifecycle
from echo_memory.navigator import Query
from echo_memory.semantic import SemanticNavigator

DAY = "2026-08-01"
OWNER = "synthetic-basin"
DOM = "synthetic-ops"
STAMP = "2026-01-01T00:00:00Z"


def body(i):
    return f"样本{i:05}，合成轨道台账的定位校验记录，凭据标志为锚点{i:05}。"


def state(path):
    with store.connection(path) as con:
        digest = hashlib.sha256()
        for r in con.execute("SELECT * FROM life_policy ORDER BY id"):
            digest.update(json.dumps(tuple(r)).encode())
        return dict(
            policy_hash=digest.hexdigest(),
            counts=[
                list(x)
                for x in con.execute(
                    "SELECT level,archived,count(*) FROM life_policy GROUP BY level,archived ORDER BY level,archived"
                )
            ],
            receipts=con.execute("SELECT count(*) FROM life_receipts").fetchone()[0],
            integrity=con.execute("PRAGMA integrity_check").fetchone()[0],
        )


def size(path):
    with store.connection(path) as con:
        return dict(
            file_bytes=path.stat().st_size,
            reusable_bytes=con.execute("PRAGMA freelist_count").fetchone()[0]
            * con.execute("PRAGMA page_size").fetchone()[0],
            body_bytes=con.execute(
                "SELECT sum(length(CAST(content AS BLOB))) FROM memories"
            ).fetchone()[0],
        )


def retrieval(atlas, count):
    nav = SemanticNavigator(atlas)
    samples = []
    for i in range(0, min(count, 100), 5):
        start = time.perf_counter()
        result = nav.search(Query(f"关键词=锚点{i:05}", DOM, DAY))
        samples.append(
            (
                (time.perf_counter() - start) * 1000,
                result["tokens"],
                [r["id"] for r in result["evidence"]] == [f"r{i}"],
            )
        )
    return dict(
        median_ms=statistics.median(r[0] for r in samples),
        tokens=sum(r[1] for r in samples),
        correct=sum(r[2] for r in samples),
        queries=len(samples),
    )


def lesson_probe(atlas):
    manager = Consolidation(atlas)
    condition = ("只读演练",)
    q = Query("路径检查", "software", DAY, condition, "path-check")
    sources = []
    for i in range(7):
        key = f"trial-{i}"
        note = f"独立演练单{i}，限定只读演练。核验员记录工作目录、临时挂载路径以及回放结果，序号{i}仅为合成材料。"
        sources.append(note)
        role = "path-failure" if i < 5 else "check-prevented-error"
        store.add(
            atlas.path,
            kind="experience",
            content=note,
            memory_id=key,
            origin=atlas.verifier,
            tags=["domain=software", "consolidate=allow", role],
            source_ref="run-" + key,
            confidence=1,
            occurred_at=STAMP,
        )
        with store.connection(atlas.path) as con:
            atlas.enroll(
                con,
                key,
                {"domain": ["software"]},
                conditions=condition,
                until="2026-09-01",
                consent="synthetic",
            )
        manager.register(key, "software", condition)
    manager.tidy(DAY)
    lesson = manager.read(q)
    # Include scope and provenance in the usable compressed representation.
    wire = json.dumps(
        {
            k: lesson[k]
            for k in ("kind", "text", "conditions", "evidence", "confidence")
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
    repeated = not manager.tidy(DAY) and manager.read(q) == lesson
    with store.connection(atlas.path) as con:
        bodies = con.execute(
            "SELECT count(*) FROM memories WHERE id LIKE 'trial-%'"
        ).fetchone()[0]
    guard = dict(
        valid=lesson["status"] == "validated",
        original_sources=bodies,
        conditions_preserved=lesson["conditions"] == condition
        and manager.read(replace(q, conditions=())) is None,
        expired_blocked=manager.read(replace(q, as_of="2026-09-01")) is None,
        repeat_stable=repeated,
        original_bytes=sum(len(s.encode()) for s in sources),
        suggestion_bytes=len(lesson["text"].encode()),
        scoped_payload_bytes=len(wire.encode()),
    )
    Lifecycle(atlas).erase("trial-6")
    guard["deleted_blocked"] = manager.read(q) is None
    manager.tidy(DAY)
    guard["recalculated"] = manager.read(q)["status"]
    store.add(
        atlas.path,
        kind="fact",
        content="重用编号的合成目录卡",
        memory_id="trial-6",
        occurred_at=STAMP,
    )
    with store.connection(atlas.path) as con:
        atlas.enroll(
            con,
            "trial-6",
            {"domain": ["software"]},
            conditions=condition,
            consent="synthetic",
        )
    guard["reused_id_blocked"] = manager.read(q)["status"] == "candidate"
    return guard


def measure(folder, count=10000):
    start = time.perf_counter()
    source = folder / "source.sqlite"
    store.init(source)
    with store.connection(source) as con:
        con.executemany(
            """INSERT INTO memories(id,content,occurred_at,kind,importance,origin)
                           VALUES (?,?,?,'fact',?,'synthetic')""",
            (
                (f"r{i}", body(i), STAMP, 4 if i % 5 in (1, 2) else 2)
                for i in range(count)
            ),
        )
    original_hash = hashlib.sha256(source.read_bytes()).digest()
    atlas = Atlas.create(source, folder / "working.sqlite", OWNER, "synthetic-verifier")
    with store.connection(atlas.path) as con:
        for i in range(count):
            atlas.enroll(con, f"r{i}", {"domain": [DOM]}, consent="fictional scope")
    life = Lifecycle(atlas)
    for i in range(count):
        group = i % 5
        life.classify(
            f"r{i}",
            retain=group == 0,
            temporary=group == 3,
            ttl=30 if group == 2 else None,
        )
    backup = folder / "rollback.sqlite"
    with store.connection(atlas.path) as src, store.connection(backup) as dst:
        src.backup(dst)
    initial = state(atlas.path)
    before = size(atlas.path)
    before_cost = retrieval(atlas, count)

    def drain():
        processed = []
        while batch := life.sweep(DAY, limit=200):
            processed.extend(batch)
        return processed

    with ThreadPoolExecutor(max_workers=2) as pool:
        processed = [
            mid for batch in pool.map(lambda _: drain(), range(2)) for mid in batch
        ]
    after = size(atlas.path)
    after_cost = retrieval(atlas, count)
    current = state(atlas.path)
    restarted = json.loads(
        subprocess.check_output(
            [
                sys.executable,
                "-m",
                "experiments.echo0051.lifetime",
                "--probe",
                str(atlas.path),
            ],
            encoding="utf8",
        )
    )
    with store.connection(atlas.path) as con:
        saved = dict(con.execute("SELECT id,content FROM memories"))
        fts = {r[0] for r in con.execute("SELECT id FROM memory_fts")}
        receipts = {r[0] for r in con.execute("SELECT id FROM life_receipts")}
        archived = {
            r[0] for r in con.execute("SELECT id FROM life_policy WHERE archived=1")
        }
        fk_errors = len(list(con.execute("PRAGMA foreign_key_check")))
    important = [i for i in range(count) if i % 5 in (0, 1, 2)]
    expired = {f"r{i}" for i in range(count) if i % 5 in (3, 4)}
    q = Query("检查", DOM, DAY)
    unauthorized = replace(q, domain="foreign-domain")
    probes = {
        mid: life.status(mid, unauthorized) for mid in ("r0", "r2", "r3", "r40404")
    }
    try:
        Atlas(atlas.path, "foreign-owner")
    except PermissionError:
        owner_isolated = True
    else:
        owner_isolated = False
    rollback = Atlas(backup, OWNER)
    restorable = (
        state(backup) == initial and Lifecycle(rollback).status("r3", q) == "AVAILABLE"
    )
    summary = lesson_probe(atlas)
    checks = dict(
        fts_matches=fts == set(saved),
        receipts_match=receipts == expired,
        expired_absent=not expired.intersection(saved),
        archive_match=archived == {f"r{i}" for i in range(count) if i % 5 == 2},
        archive_suppressed=life.status("r2", q) == "ARCHIVED"
        and not SemanticNavigator(atlas).search(Query("关键词=锚点00002", DOM, DAY))[
            "evidence"
        ],
        no_duplicate_sweep=len(processed) == len(set(processed)),
        foreign_owner_blocked=owner_isolated,
        foreign_domain_hidden=set(probes.values()) == {"UNKNOWN"},
        reopened_same=current == restarted,
        rollback=restorable,
        source_unchanged=hashlib.sha256(source.read_bytes()).digest() == original_hash,
        foreign_keys_clean=fk_errors == 0,
        sqlite_integrity=current["integrity"] == "ok",
    )
    return dict(
        count=count,
        important=len(important),
        lost_important=sum(saved.get(f"r{i}") != body(i) for i in important),
        expired=len(expired),
        remaining=len(saved),
        initial=initial,
        restarted=restarted,
        checks=checks,
        before=before,
        after=after,
        before_cost=before_cost,
        after_cost=after_cost,
        summary=summary,
        seconds=time.perf_counter() - start,
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--probe", type=Path)
    mode.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.probe:
        # Reopen via the real owner binding and policy manager in a fresh interpreter.
        Lifecycle(Atlas(args.probe, OWNER))
        print(json.dumps(state(args.probe)))
    else:
        with tempfile.TemporaryDirectory() as tmp:
            result = measure(Path(tmp))
        args.output.write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf8"
        )
        print(result["checks"], result["lost_important"])
