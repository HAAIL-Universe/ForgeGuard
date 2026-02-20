-- Store per-contract generation timing data on the project for benchmarking.
ALTER TABLE projects ADD COLUMN IF NOT EXISTS generation_metrics JSONB;
