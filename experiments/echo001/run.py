"""Run from a Git checkout with Python 3.10+ and SQLite FTS5 trigram support."""

import io
import json
import math
import platform
import re
import sqlite3
import statistics
import subprocess
import sys
import tempfile
import time
import uuid
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
BASE = "80d5e27f054a629c46384ccd0bfeeb81dc63a5fa"
REPEATS = 20
BUDGET = 60


def fixture(name):
    return [json.loads(s) for s in (HERE / name).read_text(encoding="utf-8").splitlines()]


def mid(label):
    return str(uuid.uuid5(uuid.NAMESPACE_URL, "echo001/synthetic/" + label))


def check(condition, label):
    if not condition:
        raise AssertionError(label)


def seed(store, dbdir, candidate):
    for row in fixture("memories.jsonl"):
        db = dbdir / (row["user"] + ".sqlite")
        fields = {k: row[k] for k in ("content", "kind", "occurred_at")}
        fields.update(memory_id=mid(row["id"]), origin="synthetic", claim_key=row.get("claim_key"),
                      importance=store.DEFAULT_SCORES[row["kind"]])
        if candidate:
            fields.update(source_ref="fixture:" + row["id"], confidence=0.8)
        for key in ("experience_id", "supersedes"):
            if key in row:
                fields[key] = mid(row[key])
        if "supersedes" in row:
            try:
                store.add(db, kind="fact", content=row["content"], claim_key="tower.light")
            except store.MemoryConflict as exc:
                check(exc.memory_id == fields["supersedes"], "wrong conflict")
            else:
                raise AssertionError("conflict was accepted")
        store.add(db, **fields)


def evaluate(store, agent_module, dbdir, candidate):
    memories = fixture("memories.jsonl")
    agents = {u: agent_module.AgentMemory(dbdir / (u + ".sqlite"), **({"use_fts": True} if candidate else {}))
              for u in ("A", "B")}
    labels = {mid(r["id"]): r["id"] for r in memories}
    for r in memories:
        db = agents[r["user"]].database
        record = store.get(db, mid(r["id"]))
        check(all(record[k] == r[k] for k in ("kind", "content", "occurred_at")), "restart")
        if candidate:
            check(record["source_ref"] == "fixture:" + r["id"] and record["confidence"] == 0.8, "source")
        other = agents["A" if r["user"] == "B" else "B"]
        check(store.get(other.database, mid(r["id"])) is None, "isolation")
    db = agents["A"].database
    check([labels[r["id"]] for r in store.search(db, "采样记录")] == ["a03", "a01", "a02"], "time order")
    check([labels[r["id"]] for r in store.search(db, "照明模式")] == ["a05"], "correction")
    check(store.get(db, mid("a05"))["supersedes"] == mid("a04"), "history")
    if candidate:
        check([labels[r["id"]] for r in agents["A"].reflections_for(mid("a06"))] == ["a08", "a07"],
              "reverse evidence")
    queries = fixture("queries.jsonl")
    def recall(q):
        options = {"max_tokens": BUDGET} if candidate else {}
        return agents[q["user"]].recall(q["query"], **options)

    for q in queries:
        recall(q)
    times = []
    rows = []
    for repeat in range(REPEATS):
        for q in queries:
            start = time.perf_counter_ns()
            context = recall(q)
            times.append((time.perf_counter_ns() - start) / 1e6)
            if repeat:
                continue
            got = [labels[i] for i in re.findall(r"^\[([^\]]+)\] ", context, re.M)]
            ascii_chars = sum(c.isascii() for c in context)
            tokens = len(context) - ascii_chars + math.ceil(ascii_chars / 4)
            if candidate:
                check(tokens <= BUDGET, "context budget")
            rows.append(dict(id=q["id"], expected=q["relevant"], got=got, tokens=tokens))
    hits = sum(bool(set(r["expected"]) & set(r["got"])) for r in rows)
    tp = sum(len(set(r["got"]) & set(r["expected"])) for r in rows)
    returned = sum(len(r["got"]) for r in rows)
    return dict(positive=sum(bool(r["expected"]) for r in rows), hits=hits, tp=tp,
                relevant=sum(len(r["expected"]) for r in rows), returned=returned,
                exact=sum(set(r["got"]) == set(r["expected"]) for r in rows),
                tokens=sum(r["tokens"] for r in rows), requests=len(times),
                median_ms=round(statistics.median(times), 3),
                p95_ms=round(sorted(times)[math.ceil(.95 * len(times)) - 1], 3), rows=rows)


def run():
    with tempfile.TemporaryDirectory() as temp:
        folder = Path(temp)
        baseline = folder / "baseline"
        archive = subprocess.check_output(["git", "archive", "--format=zip", BASE, "echo_memory"], cwd=ROOT)
        with zipfile.ZipFile(io.BytesIO(archive)) as z:
            z.extractall(baseline)
        print(f"Python {platform.python_version()}, SQLite {sqlite3.sqlite_version}, {platform.system()}")
        for mode, source in (("baseline", baseline), ("candidate", ROOT)):
            dbdir = folder / (mode + "-db")
            dbdir.mkdir()
            for action in ("write", "read"):
                subprocess.run([sys.executable, "-I", str(Path(__file__).resolve()),
                                action, str(source), str(dbdir), mode], check=True)


if __name__ == "__main__":
    if len(sys.argv) == 1:
        run()
    else:
        action, source, directory, mode = sys.argv[1:]
        sys.path.insert(0, source)
        from echo_memory import agent, store

        if action == "write":
            seed(store, Path(directory), mode == "candidate")
        elif action == "read":
            print(mode, json.dumps(evaluate(store, agent, Path(directory), mode == "candidate")))
        else:
            raise ValueError(action)
