"""Explicitly enrolled, one-owner view over an isolated SQLite snapshot."""

import json
import sqlite3
import time
from datetime import date, datetime, timezone
from pathlib import Path

from . import fulltext, store
from .relationship import policy
from echo_reflect.engine import approved

AXES = {"person", "project", "domain", "event", "alias"}
EDGES = {"supports", "derived_from", "contradicts", "causal_hypothesis", "related_to"}
DEPENDENCIES = ("supports", "derived_from")


def has(con, table):
    return con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()


def timestamp():
    return datetime.now(timezone.utc).isoformat()


class Atlas:
    @classmethod
    def create(cls, source, destination, owner, verifier):
        """Leave the source unchanged. Never replace an existing destination."""
        destination = Path(destination)
        if not owner or not verifier or not Path(source).is_file():
            raise ValueError("Named owner, verifier and existing source required")
        destination.touch(exist_ok=False)
        try:
            src = sqlite3.connect(
                Path(source).resolve().as_uri() + "?mode=ro", uri=True
            )
            try:
                with store.connection(destination) as dst:
                    src.backup(dst)
                    if (
                        has(dst, "relation_model")
                        and dst.execute("SELECT person FROM relation_model").fetchone()[
                            0
                        ]
                        != owner
                    ):
                        raise PermissionError("Source owner differs")
                    dst.executescript(
                        Path(__file__).with_suffix(".sql").read_text(encoding="utf-8")
                    )
                    dst.execute(
                        "INSERT INTO atlas_owner VALUES(?,?)", (owner, verifier)
                    )
            finally:
                src.close()
            fulltext.enable(destination)
        except Exception:
            destination.unlink(missing_ok=True)
            raise
        return cls(destination, owner)

    def __init__(self, path, owner):
        self.path = Path(path)
        if not self.path.is_file():
            raise ValueError("Atlas snapshot required")
        with store.connection(path) as con:
            row = con.execute("SELECT owner,verifier FROM atlas_owner").fetchone()
            if not row or row[0] != owner:
                raise PermissionError("Owner mismatch")
            self.owner, self.verifier = row

    def enroll(
        self,
        con,
        mid,
        facets,
        *,
        consent,
        since=None,
        until=None,
        conditions=(),
        version=None,
    ):
        """Insert metadata within the caller's snapshot transaction; never duplicate the payload."""
        row = con.execute(
            "SELECT occurred_at FROM memories WHERE id=?", (mid,)
        ).fetchone()
        if (
            not row
            or not consent.strip()
            or not facets.get("domain")
            or set(facets) - AXES
        ):
            raise ValueError("Source, explicit consent and domain required")
        moment = datetime.fromisoformat(row[0].replace("Z", "+00:00"))
        if moment.tzinfo is None:
            raise ValueError("An explicit timezone is required")
        day = moment.astimezone(timezone.utc).date().isoformat()
        since = date.fromisoformat(since or day).isoformat()
        until = date.fromisoformat(until).isoformat() if until else None
        if until and until <= since:
            raise ValueError("Invalid validity interval")
        if any(
            isinstance(v, str) or not all(isinstance(x, str) and x.strip() for x in v)
            for v in facets.values()
        ):
            raise ValueError("Facets must be lists of names")
        con.execute(
            "INSERT INTO atlas_items VALUES(?,?,?,?,?,?,?,?,?)",
            (
                mid,
                day,
                since,
                until,
                json.dumps(sorted(set(conditions))),
                consent,
                timestamp(),
                1,
                version,
            ),
        )
        con.executemany(
            "INSERT INTO atlas_facets VALUES(?,?,?)",
            [(mid, k, v) for k, vs in facets.items() for v in set(vs)],
        )

    def link(self, con, src, dst, kind, reason, source):
        if src == dst or kind not in EDGES or not reason.strip() or not source.strip():
            raise ValueError("Typed, attributed edge required")
        if not con.execute("SELECT 1 FROM atlas_items WHERE id=?", (dst,)).fetchone():
            raise ValueError("Target not enrolled")
        con.execute(
            "INSERT INTO atlas_edges VALUES(?,?,?,?,?)",
            (src, dst, kind, reason, source),
        )

    def revoke(self, con, mid):
        """Withdraw admission in this snapshot, also invalidating dependent hits."""
        return (
            con.execute(
                "UPDATE atlas_items SET live=0 WHERE id=? AND live=1", (mid,)
            ).rowcount
            == 1
        )

    def eligible(self, con, mid, query, trail=(), deadline=None):
        if deadline is not None and time.perf_counter() >= deadline:
            raise TimeoutError
        if mid in trail or len(trail) >= 12:
            return False
        row = con.execute(
            """SELECT a.*,m.kind FROM atlas_items a JOIN memories m ON a.id=m.id
            WHERE a.id=? AND live=1 AND since<=? AND (until IS NULL OR until>?)
            AND NOT EXISTS(SELECT 1 FROM memories newer WHERE newer.supersedes=a.id)""",
            (mid, query.as_of, query.as_of),
        ).fetchone()
        if not row or not set(json.loads(row["conditions"])).issubset(query.conditions):
            return False
        domains = {
            x[0]
            for x in con.execute(
                "SELECT value FROM atlas_facets WHERE id=? AND axis='domain'", (mid,)
            )
        }
        if query.domain not in domains:
            return False
        if has(con, "relation_policy"):
            bounds = policy(con)
            if not bounds["recall"] or domains.intersection(bounds["blocked_domains"]):
                return False
        if (
            has(con, "evidence_reviews")
            and con.execute(
                "SELECT 1 FROM evidence_reviews WHERE target=?", (mid,)
            ).fetchone()
        ):
            # The old narrative can still carry the discredited interpretation.
            return False
        if has(con, "evidence_reviews"):
            proof = con.execute(
                "SELECT target,seq FROM evidence_reviews WHERE proof_id=?", (mid,)
            ).fetchone()
            if proof:
                if con.execute(
                    "SELECT 1 FROM evidence_reviews WHERE target=? AND seq>?",
                    tuple(proof),
                ).fetchone():
                    return False
                target = con.execute(
                    """SELECT a.conditions FROM atlas_items a JOIN atlas_facets f ON f.id=a.id
                    JOIN relation_events r ON r.memory_id=a.id
                    WHERE a.id=? AND live=1 AND since<=? AND (until IS NULL OR until>?)
                    AND NOT EXISTS(SELECT 1 FROM memories n WHERE n.supersedes=a.id)
                    AND f.axis='domain' AND f.value=r.domain AND f.value=?""",
                    (proof[0], query.as_of, query.as_of, query.domain),
                ).fetchone()
                if not target or not set(json.loads(target[0])).issubset(
                    query.conditions
                ):
                    return False
        if row["kind"] == "reflection" or row["version"] is not None:
            if row["version"] is None or not has(con, "reflect_heads"):
                return False
            h = con.execute(
                """SELECT h.dirty,v.status,v.proposal,v.feature FROM reflect_heads h
                JOIN reflect_versions v ON v.id=h.version WHERE h.version=? AND h.domain=?""",
                (row["version"], query.domain),
            ).fetchone()
            if (
                not h
                or h[0]
                or query.feature != h["feature"]
                or not (
                    h["status"] == "validated"
                    or (h["status"] == "needs_human" and approved(con, row["version"]))
                )
            ):
                return False
            if not con.execute("SELECT enabled FROM reflect_settings").fetchone()[0]:
                return False
            native = con.execute(
                "SELECT experience_id FROM reflection_sources WHERE reflection_id=?",
                (mid,),
            ).fetchone()
            if native and (
                not native[0]
                or not self.eligible(con, native[0], query, trail + (mid,), deadline)
            ):
                return False
            if has(con, "regulation_history"):
                b = con.execute(
                    """SELECT value FROM regulation_history WHERE domain=? AND measure='boundary'
                    AND at<=? ORDER BY seq DESC LIMIT 1""",
                    (query.domain, timestamp()),
                ).fetchone()
                if b and (
                    b[0] == "paused" or (b[0] == "limited" and h["proposal"] == "trial")
                ):
                    return False
            deps = con.execute(
                "SELECT event,proof FROM reflect_deps WHERE version=? AND sign!=0",
                (row["version"],),
            )
            for event, proof in deps:
                if not self.eligible(
                    con, proof or event, query, trail + (mid,), deadline
                ):
                    return False
        for (dep,) in con.execute(
            "SELECT dst FROM atlas_edges WHERE src=? AND kind IN ('supports','derived_from')",
            (mid,),
        ):
            if not self.eligible(con, dep, query, trail + (mid,), deadline):
                return False
        return True
