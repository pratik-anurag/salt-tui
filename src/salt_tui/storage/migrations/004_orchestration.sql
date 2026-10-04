ALTER TABLE runs ADD COLUMN parent_run_id INTEGER REFERENCES runs(id);
CREATE INDEX IF NOT EXISTS runs_parent_idx ON runs(parent_run_id, id DESC);
CREATE TABLE IF NOT EXISTS run_relationships (
 parent_run_id INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
 child_jid TEXT NOT NULL, relation TEXT NOT NULL,
 PRIMARY KEY(parent_run_id, child_jid)
);
CREATE INDEX IF NOT EXISTS relationships_child_idx ON run_relationships(child_jid);
