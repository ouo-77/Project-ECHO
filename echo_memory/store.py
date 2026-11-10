"""Local SQLite memory. No network, API, or automatic transcript ingestion."""

from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator
from uuid import uuid4

KINDS = {"fact", "experience", "decision", "reflection"}
VISIBILITIES = {"private", "public"}
DEFAULT_SCORES = {"fact": 3, "experience": 2, "decision": 5, "reflection": 4}


class MemoryConflict(ValueError):
    """The supplied claim differs from the active version for its key."""

    def __init__(self, memory_id: str):
        self.memory_id = memory_id
        super().__init__(f"Conflicting active memory: {memory_id}")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def database_path() -> Path:
    return Path(os.environ.get("ECHO_MEMORY_DB", "data/echo_memory.sqlite"))


@contextmanager
def connection(path: Path | str) -> Iterator[sqlite3.Connection]:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(path), timeout=10)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    con.execute("PRAGMA secure_delete = ON")
    try:
        yield con
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


def init(path: Path | str) -> None:
    with connection(path) as con:
        con.executescript("""
        CREATE TABLE IF NOT EXISTS memories (
          id TEXT PRIMARY KEY,
          occurred_at TEXT NOT NULL,
          kind TEXT NOT NULL CHECK(kind IN ('fact','experience','decision','reflection')),
          content TEXT NOT NULL CHECK(length(content) > 0),
          importance INTEGER NOT NULL CHECK(importance BETWEEN 1 AND 5),
          tags TEXT NOT NULL DEFAULT '[]',
          origin TEXT NOT NULL,
          visibility TEXT NOT NULL DEFAULT 'private' CHECK(visibility IN ('private','public')),
          supersedes TEXT REFERENCES memories(id) ON DELETE SET NULL
        );
        CREATE INDEX IF NOT EXISTS ix_memory_kind_time ON memories(kind, occurred_at DESC);
        CREATE INDEX IF NOT EXISTS ix_memory_visibility ON memories(visibility);
        CREATE INDEX IF NOT EXISTS ix_memory_supersedes ON memories(supersedes);
        CREATE TABLE IF NOT EXISTS claims (
          memory_id TEXT PRIMARY KEY REFERENCES memories(id) ON DELETE CASCADE,
          claim_key TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS ix_claim_key ON claims(claim_key);
        CREATE TABLE IF NOT EXISTS reflection_sources (
          reflection_id TEXT PRIMARY KEY REFERENCES memories(id) ON DELETE CASCADE,
          experience_id TEXT REFERENCES memories(id) ON DELETE SET NULL
        );
        """)


def _validated_record(
    *, kind: str, content: str, importance: int, tags: list[str] | None,
    visibility: str, origin: str, supersedes: str | None = None,
    occurred_at: str | None = None, memory_id: str | None = None,
) -> tuple:
    if kind not in KINDS:
        raise ValueError(f"Invalid kind: {kind}")
    if visibility not in VISIBILITIES:
        raise ValueError(f"Invalid visibility: {visibility}")
    if not isinstance(content, str) or not content.strip():
        raise ValueError("Content must be nonempty")
    if len(content) > 100_000:
        raise ValueError("Content too long")
    if type(importance) is not int or importance not in range(1, 6):
        raise ValueError("Importance must be 1 to 5")
    tags = [] if tags is None else tags
    if not isinstance(tags, list) or not all(isinstance(t, str) for t in tags):
        raise ValueError("Tags must be a list of strings")
    if not isinstance(origin, str) or not origin.strip():
        raise ValueError("Origin must be nonempty")
    return (memory_id or str(uuid4()), occurred_at or utc_now(), kind, content.strip(),
            importance, json.dumps(tags, ensure_ascii=False), origin.strip(), visibility, supersedes)


def add(
    path: Path | str, *, kind: str, content: str, importance: int = 3,
    tags: list[str] | None = None, visibility: str = "private", origin: str = "manual",
    supersedes: str | None = None, occurred_at: str | None = None,
    memory_id: str | None = None,
    claim_key: str | None = None, experience_id: str | None = None,
) -> str:
    init(path)
    record = _validated_record(kind=kind, content=content, importance=importance,
                              tags=tags, visibility=visibility, origin=origin,
                              supersedes=supersedes, occurred_at=occurred_at,
                              memory_id=memory_id)
    with connection(path) as con:
        con.execute("BEGIN IMMEDIATE")
        if claim_key is not None:
            claim_key = claim_key.strip().casefold()
            if not claim_key or kind != "fact":
                raise ValueError("A claim key requires a fact and a nonempty key")
        if supersedes is not None:
            previous = con.execute("SELECT id FROM memories WHERE id = ?", (supersedes,)).fetchone()
            if previous is None:
                raise ValueError("Cannot correct a memory that does not exist")
            if con.execute("SELECT 1 FROM memories WHERE supersedes = ?", (supersedes,)).fetchone():
                raise ValueError("Memory was already superseded; correct its newest successor")
            prior = con.execute("SELECT claim_key FROM claims WHERE memory_id = ?", (supersedes,)).fetchone()
            if prior:
                if kind != "fact" or claim_key not in (None, prior[0]):
                    raise ValueError("A correction must preserve the fact key")
                claim_key = prior[0]
        if claim_key:
            active = con.execute("""SELECT m.id, m.content FROM claims c
                JOIN memories m ON c.memory_id = m.id WHERE c.claim_key = ?
                AND NOT EXISTS (SELECT 1 FROM memories n WHERE n.supersedes = m.id)""",
                (claim_key,)).fetchone()
            if active and active["id"] != supersedes:
                if active["content"] == record[3] and supersedes is None:
                    return active["id"]
                raise MemoryConflict(active["id"])
        if experience_id is not None:
            experience = con.execute("SELECT kind FROM memories WHERE id = ?", (experience_id,)).fetchone()
            if kind != "reflection" or not experience or experience[0] != "experience":
                raise ValueError("A reflection requires an existing experience")
        con.execute("""INSERT INTO memories
            (id, occurred_at, kind, content, importance, tags, origin, visibility, supersedes)
            VALUES (?,?,?,?,?,?,?,?,?)""", record)
        if claim_key:
            con.execute("INSERT INTO claims VALUES (?, ?)", (record[0], claim_key))
        if experience_id is not None:
            con.execute("INSERT INTO reflection_sources VALUES (?, ?)", (record[0], experience_id))
    return record[0]


def get(path: Path | str, memory_id: str) -> dict | None:
    init(path)
    with connection(path) as con:
        row = con.execute("""SELECT m.*, c.claim_key, r.experience_id FROM memories m
            LEFT JOIN claims c ON c.memory_id = m.id
            LEFT JOIN reflection_sources r ON r.reflection_id = m.id
            WHERE m.id = ?""", (memory_id,)).fetchone()
        return _to_dict(row) if row else None


def _to_dict(row: sqlite3.Row) -> dict:
    item = dict(row)
    item["tags"] = json.loads(item["tags"])
    return item


def search(path: Path | str, query: str, limit: int = 5, include_history: bool = False) -> list[dict]:
    init(path)
    if not 1 <= limit <= 100:
        raise ValueError("limit must be between 1 and 100")
    where = ["instr(lower(m.content), lower(?)) > 0"]
    if not include_history:
        where.append("NOT EXISTS (SELECT 1 FROM memories newer WHERE newer.supersedes = m.id)")
    sql = f"""SELECT m.* FROM memories m WHERE {' AND '.join(where)}
              ORDER BY m.importance DESC, m.occurred_at DESC, m.id DESC LIMIT ?"""
    with connection(path) as con:
        return [_to_dict(row) for row in con.execute(sql, (query, limit)).fetchall()]


def delete(path: Path | str, memory_id: str) -> bool:
    init(path)
    with connection(path) as con:
        cursor = con.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
        deleted = cursor.rowcount > 0
    if deleted:
        # Reduces recoverability from the active database file; backups may remain.
        with connection(path) as con:
            con.execute("VACUUM")
    return deleted


def seed(path: Path | str, seed_file: Path | str) -> int:
    """Import only intentionally public example entries. Existing IDs are skipped."""
    init(path)
    records = []
    for line_number, line in enumerate(Path(seed_file).read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        row = json.loads(line)
        if row.get("visibility") != "public":
            raise ValueError(f"Line {line_number} is not a public seed")
        records.append(_validated_record(kind=row["kind"], content=row["content"],
                        importance=row["importance"], tags=row.get("tags", []),
                        visibility="public", origin=row.get("origin", "curated-public-seed"),
                        supersedes=row.get("supersedes"), occurred_at=row["occurred_at"],
                        memory_id=row["id"]))
    with connection(path) as con:
        start = con.total_changes
        con.execute("BEGIN IMMEDIATE")
        for record in records:
            if con.execute("SELECT 1 FROM memories WHERE id = ?", (record[0],)).fetchone():
                continue
            if record[-1] is not None:
                previous = con.execute("""SELECT 1 FROM memories m WHERE m.id = ?
                    AND m.visibility = 'public'
                    AND NOT EXISTS (SELECT 1 FROM memories n WHERE n.supersedes = m.id)
                    AND NOT EXISTS (SELECT 1 FROM claims c WHERE c.memory_id = m.id)""",
                    (record[-1],)).fetchone()
                if not previous:
                    raise ValueError("Seed corrections require an active, unkeyed public record")
            con.execute("""INSERT INTO memories
                (id, occurred_at, kind, content, importance, tags, origin, visibility, supersedes)
                VALUES (?,?,?,?,?,?,?,?,?)""", record)
        return con.total_changes - start


def export_public(path: Path | str, output: Path | str) -> int:
    """Explicit opt-in export of public, active entries only; no private history."""
    init(path)
    with connection(path) as con:
        rows = con.execute("""SELECT m.* FROM memories m WHERE m.visibility = 'public'
          AND NOT EXISTS (SELECT 1 FROM memories newer WHERE newer.supersedes = m.id)
          ORDER BY m.occurred_at, m.id""").fetchall()
        entries = [_to_dict(row) for row in rows]
    output = Path(output)
    if output.resolve() == Path(path).resolve():
        raise ValueError("Export cannot overwrite the database")
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8") as stream:
        for row in entries:
            # Do not export the local provenance label or correction chain.
            public = {key: row[key] for key in ("id", "occurred_at", "kind", "content", "importance", "tags")}
            stream.write(json.dumps(public, ensure_ascii=False) + "\n")
    return len(entries)


def stats(path: Path | str) -> dict:
    init(path)
    with connection(path) as con:
        rows = con.execute("SELECT visibility, COUNT(*) AS n FROM memories GROUP BY visibility").fetchall()
    return {"total": sum(r["n"] for r in rows), "by_visibility": {r["visibility"]: r["n"] for r in rows}}
