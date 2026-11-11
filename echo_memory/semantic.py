"""Opt-in domain lexicon. Expansions are retrieval hypotheses only."""

import json
from pathlib import Path

from .navigator import Navigator


class SemanticNavigator(Navigator):
    def __init__(self, atlas, *, enabled=True, **options):
        super().__init__(atlas, **options)
        self.enabled = enabled
        self.rules = json.loads(
            Path(__file__).with_name("semantic_rules.json").read_text(encoding="utf-8")
        )

    def plan(self, q, con=None):
        plan = super().plan(q, con)
        if not self.enabled or plan["task_type"] not in ("fact", "similar"):
            return plan
        matching = [
            rule
            for rule in self.rules
            if rule[0] == q.domain
            and all(
                any(word in q.text for word in group.split("|")) for group in rule[1]
            )
        ]
        if len(matching) != 1:
            return plan
        rule = matching[0]
        constraints = plan["search_constraints"]
        constraints.update(expressions=rule[2][:3], verify_terms=rule[3].split())
        uncertain = any(
            word in q.text
            for word in (
                "禁止",
                "不要",
                "是否不",
                "无需",
                "不能",
                "取代",
                "而非",
                "为什么",
                "多少度",
                "多少秒",
            )
        )
        routes = (
            [] if uncertain else ["semantic:" + s for s in constraints["expressions"]]
        )
        plan.update(
            selected_route=routes[0] if routes else "abstain",
            alternative_routes=routes[1:],
            task_type="semantic",
            confidence=0.65,
        )
        return plan

    def _candidates(self, con, route, c, q, deadline):
        if route == "abstain":
            return []
        if not route.startswith("semantic:"):
            return super()._candidates(con, route, c, q, deadline)
        term = route.partition(":")[2]
        ids = super()._candidates(con, "lexical", dict(c, term=term), q, deadline)
        accepted = []
        for mid in ids:
            if not self.atlas.eligible(con, mid, q, deadline=deadline):
                continue
            row = con.execute(
                "SELECT content,kind FROM memories WHERE id=?", (mid,)
            ).fetchone()
            literal = c["fields"].get("关键词")
            if literal and literal not in row["content"]:
                continue
            if row["kind"] != "fact" or not all(
                w in row["content"] for w in c["verify_terms"]
            ):
                continue
            if any(w in row["content"] for w in ("印刷", "假设", "错误示例")):
                continue
            if any(
                w in row["content"]
                for w in (
                    "禁止",
                    "不可",
                    "不要",
                    "无需",
                    "不应",
                )
            ):
                # A live, applicable prohibition blocks even a fallback expansion.
                c["semantic_conflict"] = True
                return []
            accepted.append(mid)
        return [] if c.get("semantic_conflict") else accepted
