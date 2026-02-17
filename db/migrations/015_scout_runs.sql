-- Scout runs: on-demand audit scans triggered by users
CREATE TABLE IF NOT EXISTS scout_runs (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    repo_id         UUID NOT NULL REFERENCES repos(id) ON DELETE CASCADE,
    user_id         UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    status          VARCHAR(20) NOT NULL DEFAULT 'pending',
    hypothesis      TEXT,
    results         JSONB,
    checks_passed   INTEGER NOT NULL DEFAULT 0,
    checks_failed   INTEGER NOT NULL DEFAULT 0,
    checks_warned   INTEGER NOT NULL DEFAULT 0,
    started_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at    TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_scout_runs_repo   ON scout_runs(repo_id, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_scout_runs_user   ON scout_runs(user_id, started_at DESC);
