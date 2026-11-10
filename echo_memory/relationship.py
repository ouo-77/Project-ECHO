"""Offline, per-person simulation. The host supplies verified ECHO experience records."""

import json
import math
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

from . import store

AFFECT = ("curiosity", "caution", "positive_valence", "uncertainty", "engagement")
ROLES = ("friend", "companion", "family")
DEFAULTS = Path(__file__).with_name("relationship_rules.json")


def load_rules(path=DEFAULTS):
    cfg = json.loads(Path(path).read_text(encoding="utf-8"))
    for key in ("prior", "retention", "familiarity_step", "affect_retention", "acquaintance_at",
                "trust_at", "caution_at", "uncertainty_at", "engagement_at", "min_confidence"):
        value = cfg[key]
        if type(value) not in (int, float) or not math.isfinite(value):
            raise ValueError(key)
        if (key == "prior" and value <= 0) or (key != "prior" and not 0 <= value <= 1):
            raise ValueError(key)
    if not cfg["verifiers"] or not isinstance(cfg["verifiers"], list):
        raise ValueError("Provide an explicit verifier allowlist")
    for rule in cfg["rules"].values():
        for dim, weight in rule["weights"].items():
            if dim not in {*AFFECT, "general", "domain", "closeness"} or not math.isfinite(weight):
                raise ValueError("Invalid rule weight")
    return cfg


def initial():
    return dict(familiarity=0.0, closeness=0.5, trust=0.5, domain_trust={}, shared_experiences=0,
                affect=dict.fromkeys(AFFECT, 0.5), general_counts=[0, 0], domain_counts={})


def clamp(value):
    return max(0.0, min(1.0, value))


def advance(state, weights, domain, confidence, cfg):
    result = deepcopy(state)
    result["shared_experiences"] += 1
    result["familiarity"] += (1 - state["familiarity"]) * cfg["familiarity_step"] * confidence
    result["closeness"] = clamp(state["closeness"] + weights.get("closeness", 0) * confidence)
    for dim in AFFECT:
        retained = (state["affect"][dim] - 0.5) * cfg["affect_retention"]
        result["affect"][dim] = clamp(0.5 + retained + weights.get(dim, 0) * confidence)
    conflicts = []
    for scope in ("general", "domain"):
        signal = weights.get(scope, 0) * confidence
        if not signal:
            continue
        if scope == "general":
            counts = result["general_counts"]
        else:
            counts = result["domain_counts"].setdefault(domain, [0, 0])
        if (counts[0] - counts[1]) * signal < 0:
            conflicts.append(scope)
        counts[:] = [counts[0] * cfg["retention"] + max(0, signal),
                     counts[1] * cfg["retention"] + max(0, -signal)]
        score = (cfg["prior"] + counts[0]) / (2 * cfg["prior"] + sum(counts))
        if scope == "general":
            result["trust"] = score
        else:
            result["domain_trust"][domain] = score
    return result, conflicts


def current(con):
    row = con.execute("SELECT snapshot FROM relation_events ORDER BY seq DESC LIMIT 1").fetchone()
    return json.loads(row[0]) if row else initial()


def policy(con):
    row = con.execute("SELECT settings FROM relation_policy ORDER BY seq DESC LIMIT 1").fetchone()
    return json.loads(row[0]) if row else dict(role=None, person_agrees=False, agent_agrees=False,
                                               recall=True, blocked_domains=[])


class Relationship:
    """Bind one synthetic person and a fixed parameter set to one database."""

    def __init__(self, path, person, rules=DEFAULTS):
        if not isinstance(person, str) or not person.strip():
            raise ValueError("A person ID is required")
        self.path, self.person = Path(path), person
        self.cfg = load_rules(rules)
        store.init(path)
        with store.connection(path) as con:
            con.executescript("""
                CREATE TABLE IF NOT EXISTS relation_model (
                    singleton INTEGER PRIMARY KEY CHECK(singleton=1), person TEXT, config TEXT);
                CREATE TABLE IF NOT EXISTS relation_events (
                    seq INTEGER PRIMARY KEY, memory_id TEXT UNIQUE NOT NULL, at TEXT NOT NULL,
                    source TEXT NOT NULL, source_ref TEXT NOT NULL UNIQUE, kind TEXT NOT NULL,
                    domain TEXT NOT NULL, confidence REAL NOT NULL, reason TEXT NOT NULL,
                    weights TEXT NOT NULL, conflicts TEXT NOT NULL, snapshot TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS relation_domain ON relation_events(domain, seq DESC);
                CREATE TABLE IF NOT EXISTS relation_policy (
                    seq INTEGER PRIMARY KEY, at TEXT NOT NULL, settings TEXT NOT NULL);
            """)
            con.execute("BEGIN IMMEDIATE")
            config = json.dumps(self.cfg, sort_keys=True)
            con.execute("INSERT OR IGNORE INTO relation_model VALUES (1,?,?)", (person, config))
            if tuple(con.execute("SELECT person,config FROM relation_model").fetchone()) != (person, config):
                raise ValueError("Database already bound to a different person or rule set")

    def set_policy(self, *, role=None, person_agrees=False, agent_agrees=False, recall=True,
                   blocked_domains=()):
        """Trusted host replaces the bilateral agreement. Either party may withdraw."""
        if not all(type(v) is bool for v in (person_agrees, agent_agrees, recall)):
            raise ValueError("Explicit boolean settings required")
        if role is not None and (role not in ROLES or not (person_agrees and agent_agrees)):
            raise ValueError("Explicit bilateral agreement required")
        if not isinstance(blocked_domains, (list, tuple)) or not all(
            isinstance(d, str) and d.strip() for d in blocked_domains
        ):
            raise ValueError("Invalid domain boundary")
        settings = dict(role=role, person_agrees=person_agrees, agent_agrees=agent_agrees,
                        recall=recall, blocked_domains=sorted(set(blocked_domains)))
        with store.connection(self.path) as con:
            con.execute("INSERT INTO relation_policy(at,settings) VALUES (?,?)",
                        (store.utc_now(), json.dumps(settings)))

    def state(self):
        with store.connection(self.path) as con:
            con.execute("BEGIN")
            state, settings = current(con), policy(con)
        inferred = "acquaintance" if state["familiarity"] >= self.cfg["acquaintance_at"] else "stranger"
        return dict(state, relationship_type=settings["role"] or inferred, boundaries=settings)

    def observe(self, memory_id, kind, domain):
        """Consume one attested experience idempotently; reject stale or mismatched input."""
        rule = self.cfg["rules"][kind]
        if not isinstance(domain, str) or not domain.strip():
            raise ValueError("Domain required")
        with store.connection(self.path) as con:
            con.execute("BEGIN IMMEDIATE")
            if domain in policy(con)["blocked_domains"]:
                raise PermissionError("Domain excluded")
            previous = con.execute("SELECT * FROM relation_events WHERE memory_id=?", (memory_id,)).fetchone()
            if previous:
                if (previous["kind"], previous["domain"]) != (kind, domain):
                    raise ValueError("Evidence already applied differently")
                return json.loads(previous["snapshot"])
            m = con.execute("""SELECT m.*, e.source_ref, e.confidence FROM memories m
                JOIN evidence e ON e.memory_id=m.id WHERE m.id=? AND m.kind='experience'
                AND NOT EXISTS (SELECT 1 FROM memories n WHERE n.supersedes=m.id)""", (memory_id,)).fetchone()
            if not m or m["origin"] not in self.cfg["verifiers"] or not m["source_ref"]:
                raise ValueError("Active, attested experience required")
            tags = set(json.loads(m["tags"]))
            required = {f"person={self.person}", f"event={kind}", f"domain={domain}"}
            if required != {t for t in tags if t.startswith(("person=", "event=", "domain="))}:
                raise ValueError("Attestation scope mismatch")
            confidence = m["confidence"]
            if confidence is None or confidence < self.cfg["min_confidence"] or confidence <= 0:
                raise ValueError("Insufficient evidence confidence")
            at = datetime.fromisoformat(m["occurred_at"].replace("Z", "+00:00"))
            if at.tzinfo is None:
                raise ValueError("Timezone required")
            stamp = at.astimezone(timezone.utc).isoformat(timespec="microseconds")
            last = con.execute("SELECT at FROM relation_events ORDER BY seq DESC LIMIT 1").fetchone()
            if last and stamp < last[0]:
                raise ValueError("Out-of-order event")
            state, conflicts = advance(current(con), rule["weights"], domain, confidence, self.cfg)
            con.execute("""INSERT INTO relation_events
                (memory_id,at,source,source_ref,kind,domain,confidence,reason,weights,conflicts,snapshot)
                VALUES(?,?,?,?,?,?,?,?,?,?,?)""", (memory_id, stamp, m["origin"], m["source_ref"], kind,
                domain, confidence, rule["reason"], json.dumps(rule["weights"]), json.dumps(conflicts),
                json.dumps(state)))
            return state

    def history(self):
        """Local researcher audit, separate from simulated actor recall."""
        with store.connection(self.path) as con:
            rows = [dict(r) for r in con.execute("SELECT * FROM relation_events ORDER BY seq")]
        for row in rows:
            for key in ("weights", "conflicts", "snapshot"):
                row[key] = json.loads(row[key])
        return rows

    def explain(self, domain=None):
        """Explain a domain or general estimate, respecting the retrieval boundary."""
        with store.connection(self.path) as con:
            con.execute("BEGIN")
            settings = policy(con)
            if (domain is None and settings["blocked_domains"]) or domain in settings["blocked_domains"] or not settings["recall"]:
                raise PermissionError("Evidence recall outside boundary")
            state = current(con)
            scope = "general" if domain is None else "domain"
            rows = con.execute("""SELECT memory_id,at,source_ref,reason,confidence,weights,conflicts
                FROM relation_events WHERE (? IS NULL OR domain=?) AND json_extract(weights,?) != 0
                ORDER BY seq""", (domain, domain, "$." + scope))
            basis = [dict(id=r["memory_id"], at=r["at"], source_ref=r["source_ref"], reason=r["reason"],
                          signed_evidence=r["confidence"] * json.loads(r["weights"])[scope],
                          opposed_prior=scope in json.loads(r["conflicts"])) for r in rows]
            if domain is None:
                return dict(score=state["trust"], masses=state["general_counts"], basis=basis)
            return dict(score=state["domain_trust"].get(domain, 0.5),
                        masses=state["domain_counts"].get(domain, [0, 0]), basis=basis)

    def decide(self, domain, *, retrieve=True):
        """One offline advice probe. No external actions or permission changes."""
        with store.connection(self.path) as con:
            con.execute("BEGIN")
            s, settings = current(con), policy(con)
            if domain in settings["blocked_domains"]:
                return dict(strategy="outside_scope", style="neutral", evidence=[])
            affect = s["affect"]
            score = s["domain_trust"].get(domain, 0.5)
            cautious = affect["caution"] >= self.cfg["caution_at"] or affect["uncertainty"] >= self.cfg["uncertainty_at"]
            strategy = "request_method"
            if cautious:
                strategy = "independent_check"
            elif score >= self.cfg["trust_at"]:
                strategy = "propose_sandbox_trial"
            style = "invite_joint_review" if affect["engagement"] >= self.cfg["engagement_at"] else "neutral"
            if style == "neutral" and s["familiarity"] >= self.cfg["acquaintance_at"]:
                style = "offer_followup"
            ids = []
            if retrieve and settings["recall"]:
                row = con.execute("""SELECT r.memory_id,m.tags FROM relation_events r
                    JOIN memories m ON m.id=r.memory_id JOIN evidence e ON e.memory_id=m.id
                    WHERE r.domain=? AND e.source_ref=r.source_ref
                    AND json_extract(r.weights,'$.domain') != 0
                    AND NOT EXISTS (SELECT 1 FROM memories n WHERE n.supersedes=m.id)
                    ORDER BY r.seq DESC LIMIT 1""", (domain,)).fetchone()
                if row:
                    ids.append(row["memory_id"])
                    label = next(t[6:] for t in json.loads(row["tags"]) if t.startswith("event="))
                    if self.cfg["rules"][label]["weights"]["domain"] < 0:
                        strategy = "independent_check"
                    elif strategy == "request_method":
                        strategy = "compare_with_verified_example"
            return dict(strategy=strategy, style=style, evidence=ids)
