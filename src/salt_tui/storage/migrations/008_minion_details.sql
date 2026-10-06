CREATE TABLE IF NOT EXISTS minion_detail_cache (
 minion_id TEXT NOT NULL,
 detail_kind TEXT NOT NULL,
 captured_at TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'success',
 payload_json TEXT NOT NULL,
 PRIMARY KEY(minion_id, detail_kind)
);
CREATE INDEX IF NOT EXISTS minion_detail_kind_time_idx ON minion_detail_cache(detail_kind, captured_at DESC);
