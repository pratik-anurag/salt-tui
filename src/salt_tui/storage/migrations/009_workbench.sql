ALTER TABLE runs ADD COLUMN execution_context TEXT NOT NULL DEFAULT 'legacy';
ALTER TABLE runs ADD COLUMN action_kind TEXT NOT NULL DEFAULT 'command';
CREATE INDEX IF NOT EXISTS runs_action_context_idx ON runs(action_kind, execution_context, started_at DESC);
