"""Reviewed view of the immutable ECHO-002 event ledger. All operations are offline."""

import json
from datetime import datetime, timezone

from . import store
from .relationship import advance, initial, policy


FINDINGS = {"wrong_asset": "withdrawn", "conversion_fault": "error", "ambiguous_spec": "misunderstanding",
            "knowing_fabrication": "deception", "replicated_result": "advice", "fulfilled_commitment": "repair"}


def utc(value):
    stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if stamp.tzinfo is None:
        raise ValueError("Audit timestamp must specify a timezone")
    return stamp.astimezone(timezone.utc)


def effective_events(con, as_of=None):
    """Select the accepted classifications available at the requested simulation time."""
    return con.execute("""SELECT r.*, m.tags AS admission_tags, v.replacement, v.proof_id FROM relation_events r
        JOIN memories m ON m.id=r.memory_id JOIN evidence e ON e.memory_id=m.id
        LEFT JOIN evidence_reviews v ON v.seq=(SELECT MAX(h.seq) FROM evidence_reviews h
            WHERE h.target=r.memory_id AND (? IS NULL OR h.at<=?))
        WHERE m.kind='experience' AND m.origin=r.source AND e.source_ref=r.source_ref AND (? IS NULL OR r.at<=?)
        AND (v.seq IS NULL OR EXISTS(SELECT 1 FROM memories p JOIN evidence ep ON ep.memory_id=p.id
            WHERE p.id=v.proof_id AND p.kind='fact' AND p.origin=v.verifier AND ep.source_ref=v.source_ref
            AND NOT EXISTS(SELECT 1 FROM memories newer WHERE newer.supersedes=p.id)))
        AND NOT EXISTS(SELECT 1 FROM memories n WHERE n.supersedes=m.id)
        ORDER BY r.seq""", (as_of, as_of, as_of, as_of))


def project(con, rules, as_of=None):
    """Fold active events in original encounter order using their latest accepted labels."""
    state, basis = initial(), {}
    for row in effective_events(con, as_of):
        label = row["replacement"] if row["proof_id"] else row["kind"]
        if label == "withdrawn":
            continue
        weights = rules["rules"][label]["weights"]
        state, _ = advance(state, weights, row["domain"], row["confidence"], rules)
        signal = weights.get("domain", 0) * row["confidence"]
        if signal:
            basis.setdefault(row["domain"], []).append(dict(
                id=row["memory_id"], source_ref=row["source_ref"], kind=label,
                signal=signal, proof=row["proof_id"]))
    return state, basis


def judgment(state, basis, domain, rules):
    records = basis.get(domain, [])
    score = state["domain_trust"].get(domain, .5)
    # Distinct source references are enforced when ingesting the event ledger.
    enough = sum(r["signal"] > 0 for r in records) >= 2
    action = "trial" if enough and score >= rules["trust_at"] else "verify"
    return dict(domain=domain, score=score, action=action, basis=records)


class EvidenceReview:
    """Use Relationship.observe for intake and this view for corrected judgments."""

    def __init__(self, relationship):
        self.actor = relationship
        with store.connection(self.actor.path) as con:
            con.executescript("""
                CREATE TABLE IF NOT EXISTS evidence_reviews (
                    seq INTEGER PRIMARY KEY, target TEXT NOT NULL REFERENCES relation_events(memory_id),
                    previous TEXT, proof_id TEXT NOT NULL UNIQUE, source_ref TEXT NOT NULL UNIQUE,
                    verifier TEXT NOT NULL, at TEXT NOT NULL, replacement TEXT NOT NULL,
                    report TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS ix_reviews_target ON evidence_reviews(target,seq);
                CREATE TRIGGER IF NOT EXISTS immutable_review_identity BEFORE INSERT ON memories
                WHEN EXISTS(SELECT 1 FROM evidence_reviews WHERE proof_id=NEW.id)
                    OR EXISTS(SELECT 1 FROM relation_events WHERE memory_id=NEW.id) BEGIN
                    SELECT RAISE(ABORT, 'Evidence ID reserved by the historical ledger');
                END;
                CREATE TRIGGER IF NOT EXISTS reviewed_event_order BEFORE INSERT ON relation_events
                WHEN EXISTS(SELECT 1 FROM evidence_reviews WHERE at>NEW.at) BEGIN
                    SELECT RAISE(ABORT, 'Encounter predates an admitted review');
                END;
            """)

    def assess(self, domain, *, memory=True):
        with store.connection(self.actor.path) as con:
            con.execute("BEGIN")
            settings = policy(con)
            if domain in settings["blocked_domains"]:
                raise PermissionError("Domain excluded")
            if not memory or not settings["recall"]:
                return judgment(initial(), {}, domain, self.actor.cfg)
            return judgment(*project(con, self.actor.cfg), domain, self.actor.cfg)

    def state(self):
        """Whole-person research state must be readable across every domain."""
        with store.connection(self.actor.path) as con:
            con.execute("BEGIN")
            settings = policy(con)
            if settings["blocked_domains"] or not settings["recall"]:
                raise PermissionError("Full projection outside memory boundary")
            return project(con, self.actor.cfg)[0]

    def revise(self, target, replacement, proof_id, *, previous=None):
        """Append a verified correction. Supply the previous proof ID for a second review."""
        cfg = self.actor.cfg
        if replacement != "withdrawn" and replacement not in cfg["rules"]:
            raise ValueError("Unknown evidence label")
        with store.connection(self.actor.path) as con:
            con.execute("BEGIN IMMEDIATE")
            event = con.execute("SELECT * FROM relation_events WHERE memory_id=?", (target,)).fetchone()
            if not event:
                raise ValueError("Unknown historical event")
            domain = event["domain"]
            settings = policy(con)
            if domain in settings["blocked_domains"] or not settings["recall"]:
                raise PermissionError("Audit outside memory boundary")
            prior = con.execute("SELECT * FROM evidence_reviews WHERE proof_id=?", (proof_id,)).fetchone()
            if prior:
                if (prior["target"], prior["replacement"], prior["previous"]) != (target, replacement, previous):
                    raise ValueError("Proof already used differently")
                return json.loads(prior["report"])
            head = con.execute("SELECT * FROM evidence_reviews WHERE target=? ORDER BY seq DESC LIMIT 1",
                               (target,)).fetchone()
            if previous != (head["proof_id"] if head else None):
                raise ValueError("Review head changed")
            old_label = head["replacement"] if head else event["kind"]
            proof = con.execute("""SELECT m.*,e.source_ref,e.confidence FROM memories m JOIN evidence e
                ON m.id=e.memory_id WHERE m.id=? AND m.kind='fact' AND NOT EXISTS
                (SELECT 1 FROM memories n WHERE n.supersedes=m.id)""", (proof_id,)).fetchone()
            if not proof or proof["origin"] not in cfg["verifiers"] or not proof["source_ref"]:
                raise ValueError("Verified review fact required")
            tags = set(json.loads(proof["tags"]))
            findings = [t[8:] for t in tags if t.startswith("finding=")]
            if len(findings) != 1 or FINDINGS.get(findings[0]) != replacement:
                raise ValueError("Independent finding does not support the new classification")
            required = {f"person={self.actor.person}", f"domain={domain}", f"target={target}", "finding="+findings[0]}
            if tags != required or not proof["confidence"] or proof["confidence"] < cfg["min_confidence"]:
                raise ValueError("Insufficient or out-of-scope proof")
            if old_label == replacement:
                raise ValueError("Finding agrees with existing classification")
            if utc(proof["occurred_at"]) < utc(head["at"] if head else event["at"]):
                raise ValueError("Review predates its evidence")
            if con.execute("SELECT 1 FROM relation_events WHERE source_ref=?", (proof["source_ref"],)).fetchone():
                raise ValueError("Review requires a separate source")
            stamp = utc(proof["occurred_at"]).isoformat(timespec="microseconds")
            if con.execute("SELECT 1 FROM relation_events WHERE at>? UNION ALL SELECT 1 FROM evidence_reviews WHERE at>? LIMIT 1",
                           (stamp, stamp)).fetchone():
                raise ValueError("Record proof at its admission time, after existing observations")
            before = judgment(*project(con, cfg), domain, cfg)
            con.execute("""INSERT INTO evidence_reviews
                (target,previous,proof_id,source_ref,verifier,at,replacement,report)
                VALUES(?,?,?,?,?,?,?,'{}')""",
                (target, previous, proof_id, proof["source_ref"], proof["origin"], stamp, replacement))
            after = judgment(*project(con, cfg), domain, cfg)
            report = dict(target=target, original_source=event["source_ref"], old_label=old_label,
                          replacement=replacement, proof_id=proof_id, source_ref=proof["source_ref"],
                          verifier=proof["origin"], confidence=proof["confidence"], at=proof["occurred_at"],
                          reason=proof["content"], finding=findings[0], previous=previous, before=before, after=after)
            con.execute("UPDATE evidence_reviews SET report=? WHERE proof_id=?", (json.dumps(report), proof_id))
            return report

    def apply_finding(self, proof_id, *, previous=None):
        proof = store.get(self.actor.path, proof_id)
        if not proof:
            raise ValueError("Unknown proof")
        fields = dict(t.split("=", 1) for t in proof["tags"] if "=" in t)
        replacement = FINDINGS.get(fields.get("finding"))
        if replacement is None or not fields.get("target"):
            raise ValueError("Missing finding or target")
        return self.revise(fields["target"], replacement, proof_id, previous=previous)

    def history(self, domain):
        with store.connection(self.actor.path) as con:
            con.execute("BEGIN")
            settings = policy(con)
            if domain in settings["blocked_domains"] or not settings["recall"]:
                raise PermissionError("Audit outside memory boundary")
            rows = con.execute("""SELECT v.report FROM evidence_reviews v JOIN relation_events r
                ON r.memory_id=v.target WHERE r.domain=? ORDER BY v.seq""", (domain,))
            return [json.loads(row[0]) for row in rows]
