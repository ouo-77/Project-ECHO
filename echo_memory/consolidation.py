"""Incremental path-check hypothesis. Independent trials are required for promotion."""

import json
from . import store
from .navigator import Query
from .atlas import has, timestamp

LESSON = "执行文件操作前，先检查工作目录、路径及目标是否存在。"


class Consolidation:
    def __init__(self, atlas, *, enabled=True):
        self.atlas, self.enabled = atlas, enabled
        with store.connection(atlas.path) as con:
            con.executescript("""
                CREATE TABLE IF NOT EXISTS lesson_inputs(mid TEXT PRIMARY KEY,domain TEXT,feature TEXT,
                  conditions TEXT,role TEXT,source TEXT UNIQUE);
                CREATE INDEX IF NOT EXISTS lesson_scope ON lesson_inputs(domain,feature,conditions);
                CREATE TABLE IF NOT EXISTS lesson_dirty(domain TEXT,feature TEXT,conditions TEXT,
                  PRIMARY KEY(domain,feature,conditions));
                CREATE TABLE IF NOT EXISTS lesson_versions(id INTEGER PRIMARY KEY,domain TEXT,feature TEXT,
                  conditions TEXT,status TEXT,dependencies TEXT,created TEXT);
                CREATE INDEX IF NOT EXISTS lesson_history ON lesson_versions(domain,feature,conditions,id);
                CREATE TRIGGER IF NOT EXISTS lesson_add AFTER INSERT ON lesson_inputs BEGIN
                  INSERT OR IGNORE INTO lesson_dirty VALUES(NEW.domain,NEW.feature,NEW.conditions);
                END;
                CREATE TRIGGER IF NOT EXISTS lesson_unlink AFTER DELETE ON memories BEGIN
                  INSERT OR IGNORE INTO lesson_dirty SELECT domain,feature,conditions FROM lesson_inputs WHERE mid=OLD.id;
                  DELETE FROM lesson_inputs WHERE mid=OLD.id;
                END;
                CREATE TRIGGER IF NOT EXISTS lesson_revision AFTER INSERT ON memories WHEN NEW.supersedes IS NOT NULL BEGIN
                  INSERT OR IGNORE INTO lesson_dirty SELECT domain,feature,conditions FROM lesson_inputs WHERE mid=NEW.supersedes;
                END;
            """)

    def register(self, mid, domain, conditions=()):
        scope = json.dumps(sorted(set(conditions)))
        with store.connection(self.atlas.path) as con:
            con.execute("BEGIN IMMEDIATE")
            row = con.execute(
                """SELECT m.tags,m.kind,m.origin,e.confidence,e.source_ref,a.conditions FROM memories m
                JOIN evidence e ON e.memory_id=m.id JOIN atlas_items a ON a.id=m.id WHERE m.id=?""",
                (mid,),
            ).fetchone()
            if (
                not row
                or row["kind"] != "experience"
                or row["origin"] != self.atlas.verifier
                or not row["source_ref"]
                or (row["confidence"] or 0) < 0.5
            ):
                raise ValueError("Verified independent experience required")
            tags = json.loads(row["tags"])
            roles = [
                t
                for t in tags
                if t in ("path-failure", "check-prevented-error", "check-failed")
            ]
            if (
                len(roles) != 1
                or "consolidate=allow" not in tags
                or "domain=" + domain not in tags
                or scope != row["conditions"]
            ):
                raise ValueError("Attested scope or outcome missing")
            con.execute(
                "INSERT INTO lesson_inputs VALUES(?,?,?,?,?,?)",
                (mid, domain, "path-check", scope, roles[0], row["source_ref"]),
            )

    def _basis(self, con, q, scope):
        basis = []
        for r in con.execute(
            """SELECT i.mid,i.role,i.source FROM lesson_inputs i
               JOIN evidence e ON e.memory_id=i.mid AND e.source_ref=i.source
               JOIN memories m ON m.id=i.mid AND m.kind='experience' AND m.origin=?
               WHERE i.domain=? AND i.feature='path-check' AND i.conditions=? AND e.confidence>=0.5
               AND EXISTS (SELECT 1 FROM json_each(m.tags) WHERE value=i.role)
               AND EXISTS (SELECT 1 FROM json_each(m.tags) WHERE value='consolidate=allow')
               AND EXISTS (SELECT 1 FROM json_each(m.tags) WHERE value='domain='||i.domain)
               ORDER BY i.mid""",
            (self.atlas.verifier, q.domain, scope),
        ):
            if self.atlas.eligible(con, r["mid"], q):
                basis.append(dict(r))
        return basis

    def tidy(self, as_of, limit=8):
        if not self.enabled:
            return []
        if not 1 <= limit <= 100:
            raise ValueError("Invalid scope limit")
        with store.connection(self.atlas.path) as con:
            con.execute("BEGIN IMMEDIATE")
            if has(con, "evidence_reviews"):
                con.execute("""CREATE TRIGGER IF NOT EXISTS lesson_review AFTER INSERT ON evidence_reviews BEGIN
                    INSERT OR IGNORE INTO lesson_dirty SELECT domain,feature,conditions FROM lesson_inputs WHERE mid=NEW.target; END""")
            changed = []
            for domain, feature, scope in list(
                con.execute(
                    "SELECT * FROM lesson_dirty ORDER BY domain,feature,conditions LIMIT ?",
                    (limit,),
                )
            ):
                q = Query("路径检查", domain, as_of, tuple(json.loads(scope)))
                basis = self._basis(con, q, scope)
                roles = [r["role"] for r in basis]
                status = "insufficient"
                if roles.count("path-failure") >= 3:
                    status = "candidate"
                    if "check-failed" in roles:
                        status = "contradicted"
                    elif roles.count("check-prevented-error") >= 2:
                        status = "validated"
                serialized = json.dumps(basis, sort_keys=True)
                head = con.execute(
                    """SELECT status,dependencies FROM lesson_versions WHERE domain=? AND feature=?
                                AND conditions=? ORDER BY id DESC LIMIT 1""",
                    (domain, feature, scope),
                ).fetchone()
                if not head or tuple(head) != (status, serialized):
                    changed.append(
                        con.execute(
                            """INSERT INTO lesson_versions(domain,feature,conditions,status,dependencies,created)
                                VALUES(?,?,?,?,?,?)""",
                            (domain, feature, scope, status, serialized, timestamp()),
                        ).lastrowid
                    )
                con.execute(
                    "DELETE FROM lesson_dirty WHERE domain=? AND feature=? AND conditions=?",
                    (domain, feature, scope),
                )
            return changed

    def read(self, q):
        if not self.enabled or q.feature != "path-check":
            return None
        scope = json.dumps(sorted(set(q.conditions)))
        with store.connection(self.atlas.path) as con:
            con.execute("BEGIN")
            row = con.execute(
                """SELECT * FROM lesson_versions WHERE domain=? AND feature='path-check' AND conditions=?
                ORDER BY id DESC LIMIT 1""",
                (q.domain, scope),
            ).fetchone()
            if not row:
                return None
            current = self._basis(con, q, scope)
            if not current or json.loads(row["dependencies"]) != current:
                return None
            counts = {
                role: sum(r["role"] == role for r in current)
                for role in ("check-prevented-error", "check-failed")
            }
            return dict(
                kind="inference",
                status=row["status"],
                version=row["id"],
                evidence=current,
                summary={
                    "kind": "summary",
                    "observed_failures": sum(
                        r["role"] == "path-failure" for r in current
                    ),
                    **counts,
                },
                hypothesis=LESSON
                if row["status"] in ("candidate", "validated")
                else None,
                confidence=(1 + counts["check-prevented-error"])
                / (2 + sum(counts.values())),
                text=LESSON if row["status"] == "validated" else None,
                conditions=q.conditions,
            )
