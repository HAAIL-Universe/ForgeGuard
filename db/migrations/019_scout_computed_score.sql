-- Add deterministic computed_score column to scout_runs
ALTER TABLE scout_runs ADD COLUMN IF NOT EXISTS computed_score INTEGER;

CREATE INDEX IF NOT EXISTS idx_scout_runs_score ON scout_runs(repo_id, computed_score)
    WHERE computed_score IS NOT NULL AND scan_type = 'deep';
