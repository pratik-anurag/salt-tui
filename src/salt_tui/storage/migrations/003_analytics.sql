ALTER TABLE failures ADD COLUMN signature TEXT;
CREATE INDEX IF NOT EXISTS failures_signature_idx ON failures(signature, created_at DESC);
CREATE INDEX IF NOT EXISTS state_perf_idx ON state_results(sls,state_id,state_module,state_function,duration_ms);
CREATE INDEX IF NOT EXISTS runs_command_idx ON runs(command_type, started_at DESC);
