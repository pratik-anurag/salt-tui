CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY);
CREATE TABLE IF NOT EXISTS runs (
 id INTEGER PRIMARY KEY, uuid TEXT NOT NULL UNIQUE, started_at TEXT NOT NULL,
 finished_at TEXT, command TEXT NOT NULL, argv_json TEXT NOT NULL, command_type TEXT,
 target_expression TEXT, target_type TEXT, saltenv TEXT, pillarenv TEXT,
 test_mode INTEGER NOT NULL DEFAULT 0, exit_code INTEGER, status TEXT NOT NULL,
 duration_ms INTEGER NOT NULL DEFAULT 0, stdout TEXT, stderr TEXT, raw_result_json TEXT
);
CREATE TABLE IF NOT EXISTS minion_results (
 id INTEGER PRIMARY KEY, run_id INTEGER NOT NULL REFERENCES runs(id), minion_id TEXT NOT NULL,
 success INTEGER, return_code INTEGER, duration_ms REAL, raw_result_json TEXT
);
CREATE TABLE IF NOT EXISTS state_results (
 id INTEGER PRIMARY KEY, run_id INTEGER NOT NULL REFERENCES runs(id), minion_id TEXT NOT NULL,
 sls TEXT, state_id TEXT NOT NULL, state_module TEXT, state_function TEXT, name TEXT,
 result INTEGER, changes_json TEXT, comment TEXT, started_at TEXT, duration_ms REAL,
 raw_result_json TEXT
);
CREATE TABLE IF NOT EXISTS logs (
 id INTEGER PRIMARY KEY, run_id INTEGER NOT NULL REFERENCES runs(id), timestamp TEXT NOT NULL,
 level TEXT NOT NULL, source TEXT, minion_id TEXT, message TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS failures (
 id INTEGER PRIMARY KEY, run_id INTEGER NOT NULL REFERENCES runs(id), state_result_id INTEGER REFERENCES state_results(id),
 minion_id TEXT, sls TEXT, state_id TEXT, error_text TEXT, created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS runs_started_idx ON runs(started_at DESC);
CREATE INDEX IF NOT EXISTS runs_status_idx ON runs(status, started_at DESC);
CREATE INDEX IF NOT EXISTS minion_id_idx ON minion_results(minion_id, run_id);
CREATE INDEX IF NOT EXISTS states_minion_idx ON state_results(minion_id, run_id);
CREATE INDEX IF NOT EXISTS states_id_idx ON state_results(state_id, run_id);
CREATE INDEX IF NOT EXISTS states_sls_idx ON state_results(sls, run_id);
CREATE INDEX IF NOT EXISTS failures_time_idx ON failures(created_at DESC);
CREATE INDEX IF NOT EXISTS failures_state_idx ON failures(state_id, minion_id);
CREATE INDEX IF NOT EXISTS logs_run_idx ON logs(run_id, id);
