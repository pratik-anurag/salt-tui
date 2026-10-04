ALTER TABLE runs ADD COLUMN jid TEXT;
ALTER TABLE runs ADD COLUMN expected_minions_known INTEGER NOT NULL DEFAULT 0;
CREATE INDEX IF NOT EXISTS runs_jid_idx ON runs(jid);
CREATE TABLE IF NOT EXISTS events (
 id INTEGER PRIMARY KEY, timestamp TEXT NOT NULL, tag TEXT NOT NULL, category TEXT NOT NULL,
 kind TEXT, jid TEXT, minion_id TEXT, function TEXT, target TEXT, summary TEXT NOT NULL,
 payload_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS events_time_idx ON events(id DESC);
CREATE INDEX IF NOT EXISTS events_jid_idx ON events(jid, id DESC);
CREATE INDEX IF NOT EXISTS events_tag_idx ON events(tag, id DESC);
CREATE INDEX IF NOT EXISTS events_minion_idx ON events(minion_id, id DESC);
CREATE TABLE IF NOT EXISTS event_run_links (
 event_id INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
 run_id INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
 PRIMARY KEY(event_id, run_id)
);
CREATE TABLE IF NOT EXISTS run_minion_progress (
 run_id INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
 minion_id TEXT NOT NULL, status TEXT NOT NULL, completed_states INTEGER NOT NULL DEFAULT 0,
 total_states INTEGER, last_event_at TEXT, return_code INTEGER, raw_return_json TEXT,
 PRIMARY KEY(run_id, minion_id)
);
CREATE INDEX IF NOT EXISTS progress_status_idx ON run_minion_progress(run_id, status);
CREATE TABLE IF NOT EXISTS run_state_progress (
 run_id INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
 minion_id TEXT NOT NULL, event_tag TEXT NOT NULL, state_id TEXT,
 status TEXT, payload_json TEXT NOT NULL,
 PRIMARY KEY(run_id, event_tag)
);
