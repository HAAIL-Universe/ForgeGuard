-- Phase 59 — Forge Seal Persistence
-- Creates the certificates table. Migration 024 attempted ADD COLUMN IF NOT
-- EXISTS on this table; those columns are included here so 024 re-runs are
-- idempotent via their IF NOT EXISTS guards.

CREATE TABLE IF NOT EXISTS certificates (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id       UUID NOT NULL REFERENCES projects(id)    ON DELETE CASCADE,
    build_id         UUID NOT NULL REFERENCES builds(id)      ON DELETE CASCADE,
    user_id          UUID NOT NULL REFERENCES users(id)       ON DELETE CASCADE,
    build_cycle_id   UUID REFERENCES build_cycles(id),
    verdict          VARCHAR(20)  NOT NULL,   -- CERTIFIED | CONDITIONAL | FLAGGED
    overall_score    NUMERIC(5,1) NOT NULL,
    baseline_score   INTEGER,
    scores_json      JSONB NOT NULL,           -- full CertificateScores dict
    delta_json       JSONB,                    -- per-dimension delta vs baseline
    certificate_html TEXT,                     -- rendered HTML for fast retrieval
    integrity_hash   VARCHAR(64),              -- HMAC-SHA256
    generated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_certificates_project
    ON certificates(project_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_certificates_build
    ON certificates(build_id);
CREATE INDEX IF NOT EXISTS idx_certificates_user
    ON certificates(user_id, created_at DESC);

-- Wire the FK placeholder added by migration 024
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'fk_build_cycles_seal'
    ) THEN
        ALTER TABLE build_cycles
            ADD CONSTRAINT fk_build_cycles_seal
            FOREIGN KEY (seal_id) REFERENCES certificates(id);
    END IF;
END$$;
