-- 030: Add persistent gate columns to builds table.
-- Gates (plan_review, phase_review, ide_ready, clarification) were ephemeral
-- in-memory asyncio.Events. Server restart = gate lost = build skips user action.
-- These columns persist the pending gate so builds can resume after restart.

ALTER TABLE builds
    ADD COLUMN IF NOT EXISTS pending_gate        VARCHAR(100),
    ADD COLUMN IF NOT EXISTS gate_payload         JSONB,
    ADD COLUMN IF NOT EXISTS gate_registered_at   TIMESTAMPTZ;
