"""Bounded offline route planning and evidence retrieval."""

import hashlib
import json
import re
import sqlite3
import time
from dataclasses import asdict, dataclass
from datetime import date

from . import fulltext, store
from .agent import estimate_tokens
from .atlas import timestamp


@dataclass(frozen=True)
class Query:
    text: str
    domain: str
    as_of: str
    conditions: tuple = ()
    feature: str = ""

    def __post_init__(self):
        date.fromisoformat(self.as_of)
        if not self.domain or not self.text.strip():
            raise ValueError("Domain and task required")


def analyze(q):
    text = q.text.strip()
    fields = dict(re.findall(r"(人物|项目|事件|关键词|起点)=([^\s，。]+)", text))
    for quoted, key in re.findall(r"[“「]([^”」]+)[”」](项目|事件)", text):
        fields.setdefault(key, quoted)
    if "起点" not in fields:
        anchor = re.search(r"(?:依据链|因果链)\s*[:：]?\s*([a-zA-Z][\w-]*)", text)
        if anchor:
            fields["起点"] = anchor[1]
    dates = re.findall(r"(?<!\d)\d{4}-\d\d-\d\d(?!\d)", text)
    for d in dates:
        date.fromisoformat(d)
    term = fields.get("关键词", text)
    if "关键词" not in fields:
        similar = re.search(r"(?:类似|相似)的?[“「]([^”」]+)[”」]", text)
        if similar:
            term = similar[1]
    if text.strip("！!。 ") in ("你好", "早上好", "谢谢", "hello"):
        task, routes = "social", ["NO_RETRIEVAL"]
    elif fields.get("起点") or any(s in text for s in ("因果", "依据链")):
        task, routes = "linked", ["graph", "lexical"]
    elif dates:
        task, routes = "time", ["time", "lexical", "facets"]
    elif "类似" in text or "相似" in text:
        task, routes = "similar", ["alias", "lexical", "facets"]
    elif fields.get("项目") or fields.get("人物"):
        task, routes = "entity", ["facets", "lexical", "alias"]
    else:
        task, routes = "fact", ["lexical", "alias"]
    constraints = dict(
        domain=q.domain,
        as_of=q.as_of,
        conditions=q.conditions,
        feature=q.feature,
        term=term,
        dates=dates,
        fields=fields,
    )
    return task, routes, constraints


class Navigator:
    def __init__(
        self,
        atlas,
        *,
        adaptive=False,
        attempts=3,
        k=3,
        token_budget=128,
        time_budget=150,
    ):
        if not (
            1 <= attempts <= 10
            and 1 <= k <= 20
            and token_budget > 0
            and time_budget > 0
        ):
            raise ValueError("Positive bounded resources required")
        self.atlas, self.adaptive = atlas, adaptive
        self.limit, self.k, self.tokens, self.ms = (
            attempts,
            k,
            token_budget,
            time_budget,
        )

    def plan(self, q, con=None):
        if con is None:
            with store.connection(self.atlas.path) as db:
                return self.plan(q, db)
        task, routes, constraints = analyze(q)
        confidence = 0.9 if task != "fact" else 0.6
        if self.adaptive and len(routes) > 1:
            rows = con.execute(
                """SELECT r.task_key,r.attempts,f.expected FROM nav_feedback f JOIN nav_runs r ON r.id=f.run
                WHERE r.task_type=? AND f.id=(SELECT MAX(g.id) FROM nav_feedback g WHERE g.run=r.id)
                ORDER BY f.id DESC LIMIT 128""",
                (task,),
            )
            seen, scores = set(), {}
            for key, attempts, expected in rows:
                for a in json.loads(attempts):
                    pair = (key, a["route"])
                    if pair in seen:
                        continue
                    seen.add(pair)
                    if expected is None:
                        continue
                    hit = set(a["ids"]) == set(json.loads(expected)) and bool(a["ids"])
                    scores.setdefault(a["route"], []).append(int(hit))

            def rank(route):
                values = scores.get(route, [])[:24]
                return (
                    (2 + sum(values)) / (4 + len(values)) if len(values) >= 2 else 0.5
                )

            routes.sort(key=rank, reverse=True)
            if routes[0] in scores and len(scores[routes[0]]) >= 2:
                confidence = rank(routes[0])
        return dict(
            task_type=task,
            selected_route=routes[0],
            alternative_routes=routes[1:],
            search_constraints=constraints,
            confidence=confidence,
            token_budget=self.tokens,
            time_budget=self.ms,
            stop_condition="accepted evidence, deadline, token or route limit",
        )

    def _candidates(self, con, route, c, q, deadline):
        fields, term = c["fields"], c["term"]
        cap = self.k * 12
        if route == "lexical":
            return [r["id"] for r in fulltext.search_on(con, term, min(cap, 100))]
        if route == "graph":
            root = fields.get("起点")
            if not root or not self.atlas.eligible(con, root, q, deadline=deadline):
                return []
            reached = [root]
            frontier = [root]
            for _ in range(2):
                following = []
                for node in frontier:
                    for (dst,) in con.execute(
                        """SELECT dst FROM atlas_edges WHERE src=?
                        AND kind IN ('supports','derived_from') ORDER BY dst LIMIT ?""",
                        (node, cap),
                    ):
                        if time.perf_counter() >= deadline:
                            raise TimeoutError
                        if dst not in reached and self.atlas.eligible(
                            con, dst, q, deadline=deadline
                        ):
                            reached.append(dst)
                            following.append(dst)
                        if len(reached) >= cap:
                            return reached
                frontier = following
            return reached if len(reached) > 1 else []
        if route == "time" and c["dates"]:
            return [
                r[0]
                for r in con.execute(
                    "SELECT id FROM atlas_items WHERE day BETWEEN ? AND ? ORDER BY day,id LIMIT ?",
                    (min(c["dates"]), max(c["dates"]), cap),
                )
            ]
        filters = (
            [("alias", term)]
            if route == "alias"
            else [
                (axis, fields[key])
                for key, axis in (
                    ("项目", "project"),
                    ("人物", "person"),
                    ("事件", "event"),
                )
                if key in fields
            ]
        )
        if not filters:
            return []
        sql = " INTERSECT ".join(
            "SELECT id FROM atlas_facets WHERE axis=? AND value=?" for _ in filters
        )
        return [
            r[0]
            for r in con.execute(
                sql + " ORDER BY id LIMIT ?",
                (*[x for pair in filters for x in pair], cap),
            )
        ]

    def search(self, q, *, baseline=False, calibration_route=None):
        start = time.perf_counter()
        deadline = start + self.ms / 1000
        attempts, hits = [], []
        with store.connection(self.atlas.path) as con:
            con.execute("BEGIN")
            con.set_progress_handler(lambda: int(time.perf_counter() >= deadline), 100)
            try:
                plan = self.plan(q, con)
                routes = [plan["selected_route"], *plan["alternative_routes"]]
                if baseline:
                    routes = ["lexical"]
                if calibration_route is not None:
                    if calibration_route not in routes:
                        raise ValueError("Inapplicable calibration route")
                    routes = [calibration_route]
                plan.update(selected_route=routes[0], alternative_routes=routes[1:])
                c = plan["search_constraints"]
                status = "insufficient"
                for route in routes[: self.limit]:
                    if route == "NO_RETRIEVAL":
                        status = "no_retrieval"
                        break
                    attempt = dict(route=route, ids=[], reason="no eligible match")
                    attempts.append(attempt)
                    if time.perf_counter() >= deadline:
                        raise TimeoutError
                    candidates = self._candidates(con, route, c, q, deadline)
                    for mid in candidates:
                        if time.perf_counter() >= deadline:
                            raise TimeoutError
                        if not self.atlas.eligible(con, mid, q, deadline=deadline):
                            continue
                        row = con.execute(
                            """SELECT m.content,a.day,a.version FROM memories m JOIN atlas_items a
                                            ON a.id=m.id WHERE m.id=?""",
                            (mid,),
                        ).fetchone()
                        if c["dates"] and not min(c["dates"]) <= row["day"] <= max(
                            c["dates"]
                        ):
                            continue
                        if any(
                            not con.execute(
                                "SELECT 1 FROM atlas_facets WHERE id=? AND axis=? AND value=?",
                                (mid, axis, c["fields"][key]),
                            ).fetchone()
                            for key, axis in (
                                ("项目", "project"),
                                ("人物", "person"),
                                ("事件", "event"),
                            )
                            if key in c["fields"]
                        ):
                            continue
                        body = row["content"]
                        if row["version"] is not None:
                            body = con.execute(
                                "SELECT proposal FROM reflect_versions WHERE id=?",
                                (row["version"],),
                            ).fetchone()[0]
                        if (
                            route in ("time", "facets")
                            and "关键词" in c["fields"]
                            and c["term"] not in body
                        ):
                            continue
                        item = dict(id=mid, text=body)
                        if self.cost([*hits, item]) > self.tokens:
                            attempt["reason"] = "token budget"
                            continue
                        hits.append(item)
                        if len(hits) == self.k:
                            break
                    if route == "graph" and (
                        len(hits) < 2 or len(hits) != len(candidates)
                    ):
                        hits.clear()
                        if candidates:
                            attempt["reason"] = "incomplete evidence chain"
                    if hits and plan["task_type"] == "linked" and route != "graph":
                        chain = self._candidates(con, "graph", c, q, deadline)
                        if not chain or set(chain) != {r["id"] for r in hits}:
                            hits.clear()
                            attempt["reason"] = "unsubstantiated chain"
                    if time.perf_counter() >= deadline:
                        raise TimeoutError
                    if hits:
                        status = "evidence"
                        attempt.update(reason="accepted", ids=[r["id"] for r in hits])
                        break
            except (TimeoutError, sqlite3.OperationalError) as exc:
                if (
                    isinstance(exc, sqlite3.OperationalError)
                    and str(exc) != "interrupted"
                ):
                    raise
                status, hits = "deadline", []
                if attempts:
                    attempts[-1]["reason"] = "deadline"
                if "plan" not in locals():
                    task, routes, c = analyze(q)
                    plan = dict(
                        task_type=task,
                        selected_route=routes[0],
                        alternative_routes=routes[1:],
                        search_constraints=c,
                        confidence=0,
                        token_budget=self.tokens,
                        time_budget=self.ms,
                        stop_condition="deadline",
                    )
            finally:
                con.set_progress_handler(None, 0)
            con.commit()
            elapsed = (time.perf_counter() - start) * 1000
            key = hashlib.sha256(
                json.dumps(asdict(q), sort_keys=True).encode()
            ).hexdigest()
            run = con.execute(
                "INSERT INTO nav_runs(task_key,task_type,plan,attempts,result,status,elapsed_ms,tokens) VALUES(?,?,?,?,?,?,?,?)",
                (
                    key,
                    plan["task_type"],
                    json.dumps(plan),
                    json.dumps(attempts),
                    json.dumps([r["id"] for r in hits]),
                    status,
                    elapsed,
                    self.cost(hits),
                ),
            ).lastrowid
        return dict(
            run=run,
            plan=plan,
            attempts=attempts,
            status=status,
            evidence=hits,
            tokens=self.cost(hits),
            ms=elapsed,
        )

    @staticmethod
    def cost(items):
        return estimate_tokens("\n".join(f"[{i['id']}] {i['text']}" for i in items))

    def feedback(self, run, expected, *, verifier, source, previous=None):
        """Trusted evaluator supplies reference IDs, or None to withdraw an erroneous grade."""
        if verifier != self.atlas.verifier or not source.strip():
            raise PermissionError("Independent evaluator required")
        if expected is not None and (
            not isinstance(expected, list)
            or not all(isinstance(x, str) for x in expected)
        ):
            raise ValueError("A reference ID set is required")
        with store.connection(self.atlas.path) as con:
            con.execute("BEGIN IMMEDIATE")
            task = con.execute(
                "SELECT task_key FROM nav_runs WHERE id=?", (run,)
            ).fetchone()
            if not task:
                raise ValueError("Unknown retrieval run")
            if con.execute(
                """SELECT 1 FROM nav_feedback f JOIN nav_runs r ON r.id=f.run
                WHERE f.source=? AND r.task_key!=?""",
                (source, task[0]),
            ).fetchone():
                raise ValueError(
                    "Verification source already bound to a different task"
                )
            prior = con.execute(
                "SELECT MAX(id) FROM nav_feedback WHERE run=?", (run,)
            ).fetchone()[0]
            if previous != prior:
                raise ValueError("Stale feedback head")
            return con.execute(
                "INSERT INTO nav_feedback(run,previous,source,verifier,expected,at) VALUES(?,?,?,?,?,?)",
                (
                    run,
                    prior,
                    source,
                    verifier,
                    None if expected is None else json.dumps(sorted(set(expected))),
                    timestamp(),
                ),
            ).lastrowid

    def evaluation(self, run):
        """Expose measured outcomes separately from ungraded retrievals."""
        with store.connection(self.atlas.path) as con:
            row = con.execute(
                """SELECT r.result,r.status,r.attempts,f.expected,f.source FROM nav_runs r
                LEFT JOIN nav_feedback f ON f.id=(SELECT MAX(g.id) FROM nav_feedback g WHERE g.run=r.id)
                WHERE r.id=?""",
                (run,),
            ).fetchone()
        if row is None:
            raise ValueError("Unknown retrieval run")
        result = dict(
            replanned=len(json.loads(row["attempts"])) > 1,
            exact=None,
            found_all_evidence=None,
            source=row["source"],
        )
        if row["expected"] is not None:
            gold = set(json.loads(row["expected"]))
            actual = set(json.loads(row["result"]))
            result.update(
                exact=(gold == actual and row["status"] != "deadline"),
                found_all_evidence=gold.issubset(actual) if gold else None,
            )
        return result
