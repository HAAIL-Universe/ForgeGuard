-- Migration 022: Add repo health check and latest commit fields
-- These columns power the health-check background task and per-card commit info.

ALTER TABLE repos
  ADD COLUMN IF NOT EXISTS repo_status        VARCHAR(30)  NOT NULL DEFAULT 'connected',
  ADD COLUMN IF NOT EXISTS last_health_check_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS latest_commit_sha  VARCHAR(40),
  ADD COLUMN IF NOT EXISTS latest_commit_message TEXT,
  ADD COLUMN IF NOT EXISTS latest_commit_at   TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS latest_commit_author VARCHAR(255);

-- Index for querying stale health checks efficiently
CREATE INDEX IF NOT EXISTS idx_repos_last_health_check
    ON repos (user_id, last_health_check_at);
