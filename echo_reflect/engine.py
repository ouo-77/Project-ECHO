"""Deterministic reflection over authorized, independently attested task outcomes."""

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from echo_memory import store
from echo_memory.agent import estimate_tokens
from echo_memory.reappraisal import effective_events, utc
from echo_memory.relationship import policy

DEFAULT = dict(daily_hours=24, deep_hours=168, daily_scopes=4, min_train=3, min_validation=2,
               agreement=.75, human_reviewers=["synthetic-human"], model_calls=0, model_tokens=0)
MODES = ("daily_reflection", "event_reflection", "deep_reflection")
FEATURE = re.compile(r"[a-z][a-z0-9_-]{0,47}")


def clock(value=None):
    return utc(value).isoformat(timespec="microseconds") if value else datetime.now(timezone.utc).isoformat(timespec="microseconds")


def log(con, at, op, version, reason):
    con.execute("INSERT INTO reflect_log(at,operation,version,reason) VALUES(?,?,?,?)", (at, op, version, reason))


def heads(con, domain, feature):
    return con.execute("""SELECT h.dirty,v.* FROM reflect_heads h JOIN reflect_versions v ON v.id=h.version
        WHERE h.domain=? AND h.feature=?""", (domain, feature)).fetchone()


def sources(con, cfg, boundaries):
    grouped = {}
    for row in effective_events(con):
        if row["domain"] in boundaries["blocked_domains"]:
            continue
        tags = json.loads(row["admission_tags"])
        fields = dict(t.split("=", 1) for t in tags if "=" in t)
        feature = fields.get("feature", "")
        if (fields.get("reflect") != "allow" or not FEATURE.fullmatch(feature)
                or fields.get("partition") not in ("train", "validation")):
            continue
        kind = row["replacement"] or row["kind"]
        weight = cfg["rules"].get(kind, {}).get("weights", {}).get("domain", 0)
        grouped.setdefault((row["domain"], feature), []).append(dict(
            event=row["memory_id"], source_ref=row["source_ref"], proof=row["proof_id"], kind=kind,
            partition=fields["partition"], sign=(weight > 0) - (weight < 0), risk=fields.get("risk", "high")))
    return grouped


def evaluate(records, cfg):
    train = [r["sign"] for r in records if r["partition"] == "train" and r["sign"]]
    validation = [r["sign"] for r in records if r["partition"] == "validation" and r["sign"]]
    direction = (sum(train) > 0) - (sum(train) < 0)
    correct = validation.count(direction)
    stats = dict(train=len(train), validation=len(validation), train_agree=train.count(direction),
                 validation_agree=correct, counter=sum(x == -direction for x in train + validation) if direction else 0)
    proposal = {1: "trial", -1: "verify", 0: "undetermined"}[direction]
    status = "insufficient"
    if (direction and len(train) >= cfg["min_train"] and train.count(direction) / len(train) >= cfg["agreement"]
            and len(validation) >= cfg["min_validation"]):
        if correct / len(validation) < cfg["agreement"]:
            status = "contradicted"
        elif any(r["risk"] != "low" for r in records if r["sign"]):
            status = "needs_human"
        else:
            status = "validated"
    confidence = (correct + 1) / (len(validation) + 2) if direction else .5
    return proposal, status, confidence, stats


class Reflect:
    def __init__(self, review, config=None):
        self.review, self.actor = review, review.actor
        options = DEFAULT | (config or {})
        if set(options) != set(DEFAULT):
            raise ValueError("Unknown reflection parameter")
        for key in ("daily_hours", "deep_hours", "daily_scopes", "min_train", "min_validation"):
            if type(options[key]) is not int or options[key] <= 0:
                raise ValueError(key)
        if not .5 < options["agreement"] <= 1:
            raise ValueError("agreement")
        for key in ("model_calls", "model_tokens"):
            if type(options[key]) is not int or options[key] < 0:
                raise ValueError(key)
        if not isinstance(options["human_reviewers"], list) or not all(
                isinstance(name, str) and name.strip() for name in options["human_reviewers"]):
            raise ValueError("human_reviewers")
        if set(options["human_reviewers"]) & set(self.actor.cfg["verifiers"]):
            raise ValueError("Separate human and event authority required")
        with store.connection(self.actor.path) as con:
            con.executescript(Path(__file__).with_name("schema.sql").read_text(encoding="utf-8"))
            serialized = json.dumps(options, sort_keys=True)
            con.execute("INSERT OR IGNORE INTO reflect_settings VALUES(1,1,0,?)", (serialized,))
            saved = con.execute("SELECT config FROM reflect_settings").fetchone()[0]
            if config is not None and saved != serialized:
                raise ValueError("Configuration differs from persisted research protocol")
            self.cfg = json.loads(saved)

    def control(self, state):
        flags = {"enabled": (1, 0), "paused": (1, 1), "disabled": (0, 0)}
        with store.connection(self.actor.path) as con:
            con.execute("UPDATE reflect_settings SET enabled=?,paused=?", flags[state])
            log(con, clock(), "control", None, state)

    def run(self, mode, *, event_id=None, now=None):
        if mode not in MODES or (mode == "event_reflection") != bool(event_id):
            raise ValueError("Use a supported mode; event mode requires its source event ID")
        stamp = clock(now)
        try:
            with store.connection(self.actor.path) as con:
                con.execute("BEGIN IMMEDIATE")
                flags = con.execute("SELECT enabled,paused FROM reflect_settings").fetchone()
                bounds = policy(con)
                if not flags[0] or flags[1] or not bounds["recall"]:
                    return dict(result="inactive", versions=[])
                last = con.execute("SELECT at FROM reflect_clock WHERE mode=?", (mode,)).fetchone()
                if last and mode != "event_reflection":
                    hours = self.cfg["daily_hours" if mode == "daily_reflection" else "deep_hours"]
                    if (utc(stamp)-utc(last[0])).total_seconds() < hours*3600:
                        return dict(result="not_due", versions=[])
                groups = sources(con, self.actor.cfg, bounds)
                for h in con.execute("SELECT domain,feature FROM reflect_heads WHERE dirty IS NOT NULL"):
                    if h[0] not in bounds["blocked_domains"]:
                        groups.setdefault(tuple(h), [])
                versions = []
                for (domain, feature), records in sorted(groups.items()):
                    head = heads(con, domain, feature)
                    if event_id and not any(r["event"] == event_id and (r["sign"] < 0 or r["proof"] or
                                            r["risk"] != "low" or r["kind"] == "project") for r in records):
                        continue
                    digest = hashlib.sha256(json.dumps(records, sort_keys=True).encode()).hexdigest()
                    if head and head["digest"] == digest:
                        if head["dirty"]:
                            con.execute("UPDATE reflect_heads SET dirty=NULL WHERE version=?", (head["id"],))
                            log(con, stamp, "checked", head["id"], "same evidence digest")
                        continue
                    proposal, status, conf, stats = evaluate(records, self.cfg)
                    cursor = con.execute("""INSERT INTO reflect_versions(domain,feature,previous,created,method,digest,
                        proposal,status,confidence,stats) VALUES(?,?,?,?,?,?,?,?,?,?)""", (domain, feature,
                        head["id"] if head else None, stamp, mode, digest, proposal, status, conf, json.dumps(stats)))
                    version = cursor.lastrowid
                    con.executemany("""INSERT INTO reflect_deps VALUES(?,?,?,?,?,?)""", [
                        (version, r["event"], r["source_ref"], r["proof"], r["partition"], r["sign"]) for r in records])
                    if not head:
                        con.execute("INSERT INTO reflect_search(domain,feature) VALUES(?,?)", (domain, feature))
                    con.execute("""INSERT INTO reflect_heads VALUES(?,?,?,NULL) ON CONFLICT(domain,feature)
                        DO UPDATE SET version=excluded.version,dirty=NULL""", (domain, feature, version))
                    log(con, stamp, "evaluated", version, status)
                    versions.append(version)
                    if mode == "daily_reflection" and len(versions) >= self.cfg["daily_scopes"]:
                        break
                if versions and mode != "event_reflection":
                    con.execute("INSERT OR REPLACE INTO reflect_clock VALUES(?,?)", (mode, stamp))
                return dict(result="evaluated" if versions else "idle", versions=versions)
        except Exception as exc:
            with store.connection(self.actor.path) as con:
                log(con, stamp, "failed", None, type(exc).__name__)
            raise

    def daily_reflection(self, **kwargs):
        return self.run("daily_reflection", **kwargs)

    def event_reflection(self, event_id, **kwargs):
        return self.run("event_reflection", event_id=event_id, **kwargs)

    def deep_reflection(self, **kwargs):
        return self.run("deep_reflection", **kwargs)

    def tick(self, now=None):
        """One local scheduler tick. The host controls whether and when to repeat it."""
        with store.connection(self.actor.path) as con:
            flags = con.execute("SELECT enabled,paused FROM reflect_settings").fetchone()
            bounds = policy(con)
            if not flags[0] or flags[1] or not bounds["recall"]:
                return []
            groups = sources(con, self.actor.cfg, bounds)
            triggers = []
            for (domain, feature), rows in groups.items():
                head = heads(con, domain, feature)
                if head and not head["dirty"]:
                    continue
                known = {} if not head else dict(con.execute(
                    "SELECT event,proof FROM reflect_deps WHERE version=?", (head["id"],)))
                important = [r for r in rows if (r["event"] not in known or known[r["event"]] != r["proof"])
                             and (r["sign"] < 0 or r["proof"] or r["risk"] != "low" or r["kind"] == "project")]
                if important:
                    triggers.append(important[-1]["event"])
        return [self.event_reflection(k, now=now) for k in triggers] + [
            self.daily_reflection(now=now), self.deep_reflection(now=now)]

    def inspect(self, domain, feature):
        with store.connection(self.actor.path) as con:
            con.execute("BEGIN")
            bounds = policy(con)
            if not bounds["recall"] or domain in bounds["blocked_domains"]:
                raise PermissionError("Evidence outside scope")
            head = heads(con, domain, feature)
            if not head:
                return None
            item = dict(head)
            item["deps"] = [dict(r) for r in con.execute("SELECT * FROM reflect_deps WHERE version=?", (head["id"],))]
            item["stats"] = json.loads(item["stats"])
            if item["dirty"]:
                item["status"] = "pending_revalidation"
            elif item["status"] == "needs_human" and approved(con, item["id"]):
                item["status"] = "human_validated"
            return item

    def approve(self, domain, feature, version, proof):
        with store.connection(self.actor.path) as con:
            con.execute("BEGIN IMMEDIATE")
            h = heads(con, domain, feature)
            bounds = policy(con)
            if not bounds["recall"] or domain in bounds["blocked_domains"]:
                raise PermissionError("Review outside scope")
            if not h or h["id"] != version or h["dirty"] or h["status"] != "needs_human":
                raise ValueError("Only independently validated, current high-impact versions qualify")
            row = con.execute("""SELECT m.*,e.source_ref FROM memories m JOIN evidence e ON e.memory_id=m.id
                WHERE m.id=? AND m.kind='decision' AND NOT EXISTS(SELECT 1 FROM memories n WHERE n.supersedes=m.id)""",
                (proof,)).fetchone()
            tags = {f"person={self.actor.person}", f"domain={domain}", f"approve={version}"}
            if not row or not row["source_ref"] or row["origin"] not in self.cfg["human_reviewers"] or set(json.loads(row["tags"])) != tags:
                raise ValueError("Scoped human attestation required")
            con.execute("INSERT INTO reflect_approvals VALUES(?,?,?)", (version, proof, clock()))
            log(con, clock(), "human_approved", version, proof)

    def impact(self, event_id):
        """Locate every authorized historical version that cited the same original encounter."""
        with store.connection(self.actor.path) as con:
            con.execute("BEGIN")
            bounds = policy(con)
            if not bounds["recall"]:
                raise PermissionError("Recall disabled")
            rows = con.execute("""SELECT v.id,v.domain,v.feature,h.dirty FROM reflect_deps d
                JOIN reflect_versions v ON v.id=d.version LEFT JOIN reflect_heads h ON h.version=v.id
                WHERE d.event=? ORDER BY v.id""", (event_id,))
            return [dict(r) for r in rows if r["domain"] not in bounds["blocked_domains"]]

    def recommend(self, domain, task, *, max_tokens=100):
        """Read-only suggestion for one unambiguous procedure within an explicit domain."""
        if type(max_tokens) is not int or max_tokens < 0:
            raise ValueError("Nonnegative token budget required")
        empty = dict(action=None, version=None, context="", tokens=0)
        features = re.findall(r"(?<![\w-])feature=([a-z][a-z0-9_-]*)(?![\w-])", task)
        if len(set(features)) > 1 or (features and not FEATURE.fullmatch(features[0])):
            return empty
        tokens_in_task = features or re.findall(r"(?<![a-z0-9_-])[a-z][a-z0-9_-]{0,47}(?![a-z0-9_-])", task.lower())
        if not 1 <= len(tokens_in_task) <= 40:
            return empty
        with store.connection(self.actor.path) as con:
            con.execute("BEGIN")
            bounds = policy(con)
            if not con.execute("SELECT enabled FROM reflect_settings").fetchone()[0] or not bounds["recall"] or domain in bounds["blocked_domains"]:
                return empty
            expression = " OR ".join('"' + t + '"' for t in set(tokens_in_task))
            matches = [r[0] for r in con.execute("""SELECT feature FROM reflect_search
                WHERE reflect_search MATCH ? AND domain=?""", (expression, domain)) if r[0] in tokens_in_task]
            if len(matches) != 1:
                return empty
            feature = matches[0]
            h = heads(con, domain, feature)
            if not h or h["dirty"] or not (h["status"] == "validated" or (h["status"] == "needs_human" and approved(con, h["id"]))):
                return empty
            if con.execute("SELECT 1 FROM sqlite_master WHERE name='regulation_history'").fetchone():
                boundary = con.execute("""SELECT value FROM regulation_history WHERE domain=? AND
                    measure='boundary' AND at<=? ORDER BY seq DESC LIMIT 1""", (domain, clock())).fetchone()
                if boundary and (boundary[0] == "paused" or (boundary[0] == "limited" and h["proposal"] == "trial")):
                    return empty
            stats = json.loads(h["stats"])
            text = (f"仅供核查 {domain}/{feature}，建议 {h['proposal']}。版本 {h['id']}，"
                    f"独立核验 {stats['validation_agree']}/{stats['validation']}，反例 {stats['counter']}。")
            tokens = estimate_tokens(text)
            if tokens > max_tokens:
                return empty
            return dict(action=h["proposal"], version=h["id"], context=text, tokens=tokens)

    def self_model(self, domain):
        with store.connection(self.actor.path) as con:
            con.execute("BEGIN")
            bounds = policy(con)
            if not bounds["recall"] or domain in bounds["blocked_domains"]:
                raise PermissionError("Scope blocked")
            rows = con.execute("""SELECT v.feature,v.id,v.stats,v.status FROM reflect_heads h JOIN reflect_versions v
                ON h.version=v.id WHERE h.domain=? AND h.dirty IS NULL""", (domain,))
            return [dict(feature=r["feature"], version=r["id"], validation=json.loads(r["stats"]),
                         standing=r["status"]) for r in rows]


def approved(con, version):
    return con.execute("""SELECT 1 FROM reflect_approvals a JOIN memories m ON a.proof=m.id
        WHERE a.version=? AND NOT EXISTS(SELECT 1 FROM memories n WHERE n.supersedes=m.id)""", (version,)).fetchone()
