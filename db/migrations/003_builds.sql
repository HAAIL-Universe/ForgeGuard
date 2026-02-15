-- Phase 9: Build Orchestrator tables
-- builds: one record per build orchestration run
-- build_logs: streaming builder output captured per build

CREATE TABLE IF NOT EXISTS builds (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id      UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    phase           VARCHAR(100) NOT NULL DEFAULT 'Phase 0',
    status          VARCHAR(20) NOT NULL DEFAULT 'pending',
    started_at      TIMESTAMPTZ,
    completed_at    TIMESTAMPTZ,
    loop_count      INTEGER NOT NULL DEFAULT 0,
    error_detail    TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_builds_project_id ON builds(project_id);
CREATE INDEX IF NOT EXISTS idx_builds_project_id_created ON builds(project_id, created_at DESC);

CREATE TABLE IF NOT EXISTS build_logs (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    build_id        UUID NOT NULL REFERENCES builds(id) ON DELETE CASCADE,
    timestamp       TIMESTAMPTZ NOT NULL DEFAULT now(),
    source          VARCHAR(50) NOT NULL DEFAULT 'builder',
    level           VARCHAR(20) NOT NULL DEFAULT 'info',
    message         TEXT NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_build_logs_build_id ON build_logs(build_id);
CREATE INDEX IF NOT EXISTS idx_build_logs_build_id_timestamp ON build_logs(build_id, timestamp);
