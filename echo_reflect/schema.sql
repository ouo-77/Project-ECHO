CREATE TABLE IF NOT EXISTS reflect_settings (
    id INTEGER PRIMARY KEY CHECK(id=1), enabled INTEGER NOT NULL, paused INTEGER NOT NULL, config TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS reflect_versions (
    id INTEGER PRIMARY KEY, domain TEXT NOT NULL, feature TEXT NOT NULL, previous INTEGER REFERENCES reflect_versions,
    created TEXT NOT NULL, method TEXT NOT NULL, digest TEXT NOT NULL, proposal TEXT NOT NULL,
    status TEXT NOT NULL, confidence REAL NOT NULL, stats TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS reflect_heads (
    domain TEXT, feature TEXT, version INTEGER REFERENCES reflect_versions, dirty TEXT,
    PRIMARY KEY(domain,feature));
CREATE TABLE IF NOT EXISTS reflect_deps (
    version INTEGER REFERENCES reflect_versions, event TEXT, source_ref TEXT NOT NULL,
    proof TEXT, partition TEXT NOT NULL, sign INTEGER NOT NULL, PRIMARY KEY(version,event));
CREATE INDEX IF NOT EXISTS ix_reflect_event ON reflect_deps(event);
CREATE VIRTUAL TABLE IF NOT EXISTS reflect_search USING fts5(domain UNINDEXED, feature);
CREATE TABLE IF NOT EXISTS reflect_log (
    seq INTEGER PRIMARY KEY, at TEXT NOT NULL, operation TEXT NOT NULL, version INTEGER, reason TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS reflect_clock (mode TEXT PRIMARY KEY, at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS reflect_approvals (
    version INTEGER PRIMARY KEY REFERENCES reflect_versions, proof TEXT UNIQUE NOT NULL, at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS reflect_budget (day TEXT PRIMARY KEY, calls INTEGER NOT NULL, tokens INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS reflect_model_candidates (
    id INTEGER PRIMARY KEY, domain TEXT NOT NULL, feature TEXT NOT NULL, created TEXT NOT NULL,
    version INTEGER REFERENCES reflect_versions, text TEXT NOT NULL);
CREATE TRIGGER IF NOT EXISTS reflect_review AFTER INSERT ON evidence_reviews BEGIN
    UPDATE reflect_heads SET dirty='review:' || NEW.proof_id
    WHERE version IN (SELECT version FROM reflect_deps WHERE event=NEW.target);
END;
CREATE TRIGGER IF NOT EXISTS reflect_removed AFTER DELETE ON memories BEGIN
    UPDATE reflect_heads SET dirty='deleted:' || OLD.id
    WHERE version IN (SELECT version FROM reflect_deps WHERE event=OLD.id)
       OR version IN (SELECT version FROM reflect_approvals WHERE proof=OLD.id);
END;
CREATE TRIGGER IF NOT EXISTS reflect_superseded AFTER INSERT ON memories WHEN NEW.supersedes IS NOT NULL BEGIN
    UPDATE reflect_heads SET dirty='superseded:' || NEW.supersedes
    WHERE version IN (SELECT version FROM reflect_deps WHERE event=NEW.supersedes)
       OR version IN (SELECT version FROM reflect_approvals WHERE proof=NEW.supersedes);
END;
CREATE TRIGGER IF NOT EXISTS reflect_incoming AFTER INSERT ON relation_events BEGIN
    UPDATE reflect_heads SET dirty='incoming:' || NEW.memory_id
    WHERE domain=NEW.domain AND EXISTS (
        SELECT 1 FROM memories m,json_each(m.tags) j WHERE m.id=NEW.memory_id AND j.value='feature=' || feature
        AND EXISTS (SELECT 1 FROM json_each(m.tags) a WHERE a.value='reflect=allow'));
END;
CREATE TRIGGER IF NOT EXISTS reflect_invalidation AFTER UPDATE OF dirty ON reflect_heads
WHEN NEW.dirty IS NOT NULL BEGIN
    INSERT INTO reflect_log(at,operation,version,reason)
    VALUES(strftime('%Y-%m-%dT%H:%M:%fZ','now'),'pending_revalidation',NEW.version,NEW.dirty);
END;
