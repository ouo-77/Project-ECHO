"""Opt-in owner-side retention for an Atlas working copy."""

import json
from datetime import date, timedelta

from . import store
from .atlas import has
from .relationship import policy


class Lifecycle:
    def __init__(self, atlas, *, enabled=True):
        self.atlas, self.enabled = atlas, enabled
        with store.connection(atlas.path) as con:
            con.executescript("""
                CREATE TABLE IF NOT EXISTS life_policy(
                    id TEXT PRIMARY KEY REFERENCES atlas_items ON DELETE CASCADE,level INTEGER NOT NULL,
                    due TEXT,archived INTEGER DEFAULT 0,reasons TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS life_due ON life_policy(archived,due);
                CREATE TABLE IF NOT EXISTS life_receipts(id TEXT PRIMARY KEY,domains TEXT,conditions TEXT,day TEXT);
                CREATE TRIGGER IF NOT EXISTS life_reuse AFTER INSERT ON memories BEGIN
                    DELETE FROM life_receipts WHERE id=NEW.id;
                END;
            """)

    def classify(
        self,
        mid,
        *,
        retain=False,
        identity=False,
        commitment=False,
        value=False,
        independent=False,
        core_link=False,
        sensitive=False,
        temporary=False,
        ttl=None,
    ):
        """Only an authorized host may set retention intent and evidence attestations."""
        with store.connection(self.atlas.path) as con:
            con.execute("BEGIN IMMEDIATE")
            row = con.execute(
                """SELECT m.importance,length(CAST(m.content AS BLOB)) AS bytes,a.day,a.until
                  FROM memories m JOIN atlas_items a ON m.id=a.id WHERE m.id=?""",
                (mid,),
            ).fetchone()
            if not row:
                raise ValueError("Enrolled memory required")
            if retain or identity or commitment:
                level = 3
            elif core_link or row["importance"] >= 4 or (value and independent):
                level = 2
            elif temporary or sensitive or (row["bytes"] > 4096 and not value):
                level = 0
            else:
                level = 1
            days = (7, 90, 365, None)[level] if ttl is None else ttl
            if days is not None and (type(days) is not int or days < 0):
                raise ValueError("Invalid retention period")
            due = (
                None
                if level == 3
                else (date.fromisoformat(row["day"]) + timedelta(days=days)).isoformat()
            )
            if due and row["until"]:
                due = min(due, row["until"])
            reasons = dict(
                retain=retain,
                identity=identity,
                commitment=commitment,
                value=value,
                independent=independent,
                core_link=core_link,
                sensitive=sensitive,
                temporary=temporary,
                importance=row["importance"],
                bytes=row["bytes"],
                valid_to=row["until"],
            )
            con.execute(
                """INSERT INTO life_policy(id,level,due,reasons) VALUES(?,?,?,?)
                ON CONFLICT(id) DO UPDATE SET level=excluded.level,due=excluded.due,reasons=excluded.reasons""",
                (mid, level, due, json.dumps(reasons)),
            )
            return level

    def _erase(self, con, mid, day, receipt):
        row = con.execute(
            "SELECT conditions,live FROM atlas_items WHERE id=?", (mid,)
        ).fetchone()
        if row and row["live"] and receipt:
            domains = [
                r[0]
                for r in con.execute(
                    "SELECT value FROM atlas_facets WHERE id=? AND axis='domain'",
                    (mid,),
                )
            ]
            con.execute(
                "INSERT OR REPLACE INTO life_receipts VALUES(?,?,?,?)",
                (mid, json.dumps(domains), row["conditions"], day),
            )
        else:
            con.execute("DELETE FROM life_receipts WHERE id=?", (mid,))
        # Removing a correction must not reactivate the predecessor on the Atlas path.
        con.execute(
            "UPDATE atlas_items SET live=0 WHERE id=(SELECT supersedes FROM memories WHERE id=?)",
            (mid,),
        )
        return con.execute("DELETE FROM memories WHERE id=?", (mid,)).rowcount

    def erase(self, mid):
        with store.connection(self.atlas.path) as con:
            con.execute("BEGIN IMMEDIATE")
            return self._erase(con, mid, None, False)

    def sweep(self, as_of, *, limit=100):
        day = date.fromisoformat(as_of).isoformat()
        if not 1 <= limit <= 1000:
            raise ValueError("Invalid batch size")
        if not self.enabled:
            return []
        with store.connection(self.atlas.path) as con:
            con.execute("BEGIN IMMEDIATE")
            rows = list(
                con.execute(
                    """SELECT id,level FROM life_policy WHERE archived=0 AND due<=?
                AND level<3 ORDER BY due,id LIMIT ?""",
                    (day, limit),
                )
            )
            for mid, level in rows:
                if level == 2:
                    con.execute("UPDATE life_policy SET archived=1 WHERE id=?", (mid,))
                else:
                    self._erase(con, mid, day, True)
            return [r[0] for r in rows]

    def restore(self, mid):
        with store.connection(self.atlas.path) as con:
            # The host must set a new review date to schedule another sweep.
            con.execute(
                "UPDATE life_policy SET archived=0,due=NULL WHERE id=? AND archived=1",
                (mid,),
            )

    def status(self, mid, q):
        with store.connection(self.atlas.path) as con:
            con.execute("BEGIN")
            if has(con, "relation_policy"):
                p = policy(con)
                if not p["recall"] or q.domain in p["blocked_domains"]:
                    return "INACCESSIBLE"
            rec = con.execute(
                "SELECT * FROM life_receipts WHERE id=?", (mid,)
            ).fetchone()
            if rec:
                if q.domain in json.loads(rec["domains"]) and set(
                    json.loads(rec["conditions"])
                ) <= set(q.conditions):
                    return "FORGOTTEN"
                return "UNKNOWN"
            if not con.execute(
                "SELECT 1 FROM atlas_facets WHERE id=? AND axis='domain' AND value=?",
                (mid, q.domain),
            ).fetchone():
                return "UNKNOWN"
            row = con.execute(
                "SELECT live,conditions FROM atlas_items WHERE id=?", (mid,)
            ).fetchone()
            if not row["live"] or not set(json.loads(row["conditions"])) <= set(
                q.conditions
            ):
                return "INACCESSIBLE"
            if self.atlas.eligible(con, mid, q):
                return "AVAILABLE"
            if con.execute(
                "SELECT archived FROM life_policy WHERE id=? AND archived=1", (mid,)
            ).fetchone():
                return "ARCHIVED"
            return "UNKNOWN"
