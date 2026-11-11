"""Time-aware research projection. Reading the clock never writes or restores trust."""

import json
import math
from contextlib import nullcontext
from datetime import datetime, timezone
from pathlib import Path

from . import store
from .reappraisal import effective_events, judgment, project, utc
from .relationship import policy

DEFAULTS = Path(__file__).with_name("regulation_rules.json")
CHOICES = {"boundary": ("open", "limited", "paused"),
           "forgiveness": ("undecided", "forgiven", "deferred"),
           "reconciliation": ("undecided", "cautious_trial", "renewed", "paused")}


class Regulation:
    def __init__(self, review, rules=DEFAULTS):
        self.review = review
        self.actor = review.actor
        self.cfg = json.loads(Path(rules).read_text(encoding="utf-8"))
        cfg = self.cfg
        for scale in ("short", "mood", "tendency"):
            half = cfg["half_life_days"][scale]
            if not math.isfinite(half) or half <= 0:
                raise ValueError("Half-life must be finite and positive")
        for key in ("gain", "baseline", "loss", "activation"):
            if not all(0 <= v <= 1 for v in cfg[key].values()):
                raise ValueError(key)
        if not 0 <= cfg["pace_threshold"] <= 1 or type(cfg["repairs_required"]) is not int or cfg["repairs_required"] < 1:
            raise ValueError("Invalid regulation policy")
        with store.connection(self.actor.path) as con:
            con.executescript("""
                CREATE TABLE IF NOT EXISTS regulation_model(singleton INTEGER PRIMARY KEY CHECK(singleton=1), config TEXT);
                CREATE TABLE IF NOT EXISTS regulation_history(
                    seq INTEGER PRIMARY KEY, proof_id TEXT UNIQUE NOT NULL, source_ref TEXT UNIQUE NOT NULL,
                    at TEXT NOT NULL, domain TEXT NOT NULL, measure TEXT NOT NULL, value TEXT NOT NULL,
                    target TEXT NOT NULL, reason TEXT NOT NULL, previous TEXT, confidence REAL NOT NULL);
            """)
            config = json.dumps(cfg, sort_keys=True)
            con.execute("INSERT OR IGNORE INTO regulation_model VALUES(1,?)", (config,))
            if con.execute("SELECT config FROM regulation_model").fetchone()[0] != config:
                raise ValueError("Different regulation parameters require a separate research database")

    def record(self, proof_id, *, previous=None):
        """Admit a verified consequence or an explicit, independently revisable choice."""
        with store.connection(self.actor.path) as con:
            con.execute("BEGIN IMMEDIATE")
            proof = con.execute("""SELECT m.*,e.source_ref,e.confidence FROM memories m JOIN evidence e
                ON m.id=e.memory_id WHERE m.id=? AND m.kind='fact' AND NOT EXISTS(
                SELECT 1 FROM memories n WHERE n.supersedes=m.id)""", (proof_id,)).fetchone()
            if (not proof or proof["origin"] not in self.actor.cfg["verifiers"] or not proof["source_ref"]
                    or not proof["confidence"] or proof["confidence"] < self.actor.cfg["min_confidence"]):
                raise ValueError("Verified policy or consequence fact required")
            tags = json.loads(proof["tags"])
            fields = dict(t.split("=", 1) for t in tags if "=" in t)
            domain, measure, value = (fields.get(k) for k in ("domain", "measure", "value"))
            if fields.get("person") != self.actor.person or not domain:
                raise ValueError("Person or domain mismatch")
            settings = policy(con)
            if domain in settings["blocked_domains"] or not settings["recall"]:
                raise PermissionError("Outside memory boundary")
            target = fields.get("target", "")
            required = {"person", "domain", "measure", "value"}
            stamp = utc(proof["occurred_at"]).isoformat(timespec="microseconds")
            if measure == "loss":
                required.add("target")
                event = con.execute("SELECT at FROM relation_events WHERE memory_id=? AND domain=?",
                                    (target, domain)).fetchone()
                if not event or stamp < event["at"] or value not in self.cfg["loss"]:
                    raise ValueError("Verified incident and loss severity required")
            elif value not in CHOICES.get(measure, ()):
                raise ValueError("Unknown choice")
            if measure == "reconciliation" and value in ("cautious_trial", "renewed"):
                required.add("consent")
                if fields.get("consent") != "both":
                    raise ValueError("Bilateral agreement required")
            if set(fields) != required or len(tags) != len(required):
                raise ValueError("Invalid attestation fields")
            duplicate = con.execute("SELECT previous FROM regulation_history WHERE proof_id=?", (proof_id,)).fetchone()
            if duplicate:
                if duplicate[0] != previous:
                    raise ValueError("Conflicting retry")
                return proof_id
            if measure == "reconciliation" and value == "renewed":
                events = [e for e in effective_events(con, stamp) if e["domain"] == domain]
                repairs = 0
                for event in events:
                    label = event["replacement"] or event["kind"]
                    if label == "deception":
                        repairs = 0
                    elif label == "repair":
                        repairs += 1
                if repairs < self.cfg["repairs_required"]:
                    raise ValueError("More verified repair episodes required")
            head = con.execute("""SELECT proof_id,at FROM regulation_history WHERE domain=? AND measure=?
                AND target=? ORDER BY seq DESC LIMIT 1""", (domain, measure, target)).fetchone()
            if previous != (head["proof_id"] if head else None) or (head and stamp < head["at"]):
                raise ValueError("Stale policy or loss revision")
            con.execute("""INSERT INTO regulation_history(proof_id,source_ref,at,domain,measure,value,
                target,reason,previous,confidence) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (proof_id, proof["source_ref"], stamp, domain, measure, value, target,
                 proof["content"], previous, proof["confidence"]))
            return proof_id

    def at(self, domain, when=None):
        """As-of projection on active source records. Defaults to the actual UTC clock."""
        return self._at(domain, when)

    def _at(self, domain, when, connection=None):
        when = utc(when) if when is not None else datetime.now(timezone.utc)
        stamp = when.isoformat(timespec="microseconds")
        cfg = self.cfg
        with (store.connection(self.actor.path) if connection is None else nullcontext(connection)) as con:
            if not con.in_transaction:
                con.execute("BEGIN")
            settings = policy(con)
            if domain in settings["blocked_domains"] or not settings["recall"]:
                raise PermissionError("Outside memory boundary")
            state, basis = project(con, self.actor.cfg, stamp)
            case = judgment(state, basis, domain, self.actor.cfg)
            impulses = []
            appraisals = []
            anchor = None
            for event in effective_events(con, stamp):
                if event["domain"] != domain:
                    continue
                label = event["replacement"] or event["kind"]
                if event["proof_id"]:
                    appraisals.append(dict(target=event["memory_id"], original=event["kind"],
                                           effective=label, proof=event["proof_id"]))
                if anchor is None and event["kind"] in ("error", "deception"):
                    anchor = (event["memory_id"], event["at"])
                magnitude = cfg["activation"].get(label, 0) * event["confidence"]
                if magnitude:
                    impulses.append((event["at"], magnitude, event["memory_id"]))
            if anchor:
                past = project(con, self.actor.cfg, anchor[1])[0]
                anchor_score = past["domain_trust"].get(domain, .5)
            else:
                anchor_score = .5
            changes = [dict(r) for r in con.execute("""SELECT * FROM regulation_history
                WHERE domain=? AND at<=? ORDER BY seq""", (domain, stamp))]
            loss = {}
            choices = dict(boundary="open", forgiveness="undecided", reconciliation="undecided")
            for change in changes:
                if change["measure"] == "loss":
                    loss[change["target"]] = change
                else:
                    choices[change["measure"]] = change["value"]
            total_loss = 0
            for event in loss.values():
                magnitude = cfg["loss"][event["value"]] * event["confidence"]
                total_loss += magnitude
                if magnitude:
                    impulses.append((event["at"], magnitude, event["proof_id"]))
            levels = {}
            for scale, half in cfg["half_life_days"].items():
                residual = sum(amount * 2 ** (-((when - utc(at)).total_seconds() / 86400) / half)
                               for at, amount, _ in impulses)
                levels[scale] = min(1.0, cfg["baseline"][scale] + cfg["gain"][scale] * residual)
            action = case["action"]
            if choices["boundary"] == "paused":
                action = "pause_interaction"
            elif choices["boundary"] == "limited":
                action = "verify"
            return dict(at=stamp, levels=levels, emotional_recovery=1-levels["short"],
                        trust=case["score"], trust_recovery=case["score"]-anchor_score,
                        trust_anchor=anchor[0] if anchor else None, basis=case["basis"], action=action,
                        pace="slow_review" if levels["short"] >= cfg["pace_threshold"] else "normal",
                        relationship_closeness=max(0, state["closeness"]-total_loss), loss=total_loss,
                        impulses=[item[2] for item in impulses], appraisals=appraisals, changes=changes, **choices)
