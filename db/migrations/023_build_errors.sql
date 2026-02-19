-- Build error aggregation table.
-- Deduplicates errors by fingerprint; tracks occurrence count + resolution.

CREATE TABLE IF NOT EXISTS build_errors (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    build_id            UUID NOT NULL REFERENCES builds(id) ON DELETE CASCADE,
    fingerprint         TEXT NOT NULL,
    first_seen          TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen           TIMESTAMPTZ NOT NULL DEFAULT now(),
    occurrence_count    INTEGER NOT NULL DEFAULT 1,
    phase               VARCHAR(100),
    file_path           TEXT,
    source              VARCHAR(50) NOT NULL DEFAULT 'build_log',
    severity            VARCHAR(20) NOT NULL DEFAULT 'error',
    message             TEXT NOT NULL,
    resolved            BOOLEAN NOT NULL DEFAULT false,
    resolved_at         TIMESTAMPTZ,
    resolution_method   VARCHAR(30),
    resolution_summary  TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_build_errors_build_id
    ON build_errors(build_id);
CREATE INDEX IF NOT EXISTS idx_build_errors_build_id_resolved
    ON build_errors(build_id, resolved);
CREATE INDEX IF NOT EXISTS idx_build_errors_fingerprint
    ON build_errors(build_id, fingerprint);
