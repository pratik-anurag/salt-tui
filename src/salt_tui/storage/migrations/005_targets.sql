CREATE TABLE IF NOT EXISTS saved_targets (
 id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, expression TEXT NOT NULL,
 target_type TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS targets_name_idx ON saved_targets(name);
