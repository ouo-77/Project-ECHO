CREATE TABLE atlas_owner(owner TEXT PRIMARY KEY, verifier TEXT NOT NULL);
CREATE TABLE atlas_items(id TEXT PRIMARY KEY REFERENCES memories(id) ON DELETE CASCADE,
  day TEXT NOT NULL, since TEXT NOT NULL, until TEXT, conditions TEXT NOT NULL,
  consent TEXT NOT NULL, admitted TEXT NOT NULL, live INTEGER NOT NULL, version INTEGER);
CREATE INDEX atlas_day ON atlas_items(day,id);
CREATE TABLE atlas_facets(id TEXT REFERENCES atlas_items ON DELETE CASCADE,
  axis TEXT, value TEXT, PRIMARY KEY(axis,value,id));
CREATE INDEX atlas_by_item ON atlas_facets(id,axis,value);
CREATE TABLE atlas_edges(src TEXT REFERENCES atlas_items ON DELETE CASCADE, dst TEXT NOT NULL,
  kind TEXT NOT NULL, reason TEXT NOT NULL, source TEXT NOT NULL, PRIMARY KEY(src,dst,kind));
CREATE TABLE nav_runs(id INTEGER PRIMARY KEY, task_key TEXT, task_type TEXT, plan TEXT,
  attempts TEXT, result TEXT, status TEXT, elapsed_ms REAL, tokens INTEGER);
CREATE TABLE nav_feedback(id INTEGER PRIMARY KEY, run INTEGER REFERENCES nav_runs, previous INTEGER,
  source TEXT NOT NULL, verifier TEXT NOT NULL, expected TEXT, at TEXT NOT NULL);
CREATE INDEX nav_feedback_run ON nav_feedback(run,id);
