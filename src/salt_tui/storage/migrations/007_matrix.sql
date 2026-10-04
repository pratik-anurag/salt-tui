CREATE INDEX IF NOT EXISTS states_run_group_idx ON state_results(run_id,sls,state_id,state_module,state_function);
CREATE INDEX IF NOT EXISTS states_run_minion_idx ON state_results(run_id,minion_id,state_id);
