-- Phase 58.1 — Dossier lock mechanism + build cycle entity.
-- Makes dossiers immutable once created, and introduces build_cycles
-- to link dossier → branch → seal as a lifecycle triple.

-- ── scout_runs: lock fields ─────────────────────────────────────
ALTER TABLE scout_runs
    ADD COLUMN IF NOT EXISTS dossier_locked_at      TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS dossier_build_cycle_id  UUID;

-- ── build_cycles ────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS build_cycles (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id      UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    repo_id         UUID NOT NULL REFERENCES repos(id) ON DELETE CASCADE,
    user_id         UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    dossier_run_id  UUID REFERENCES scout_runs(id),
    branch_name     VARCHAR(255),
    baseline_sha    VARCHAR(40),
    seal_id         UUID,                       -- FK added after certificates table exists
    status          VARCHAR(30) NOT NULL DEFAULT 'active',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    sealed_at       TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_build_cycles_project
    ON build_cycles(project_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_build_cycles_status
    ON build_cycles(project_id, status)
    WHERE status = 'active';

-- ── certificates: link back to build cycle ──────────────────────
ALTER TABLE certificates
    ADD COLUMN IF NOT EXISTS build_cycle_id  UUID,
    ADD COLUMN IF NOT EXISTS baseline_score  INTEGER,
    ADD COLUMN IF NOT EXISTS delta_json      JSONB;
