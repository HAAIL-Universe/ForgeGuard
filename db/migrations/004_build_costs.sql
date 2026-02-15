-- Phase 11: Build cost tracking
-- build_costs: token usage and cost estimation per build phase

CREATE TABLE IF NOT EXISTS build_costs (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    build_id            UUID NOT NULL REFERENCES builds(id) ON DELETE CASCADE,
    phase               VARCHAR(100) NOT NULL,
    input_tokens        INTEGER NOT NULL DEFAULT 0,
    output_tokens       INTEGER NOT NULL DEFAULT 0,
    model               VARCHAR(100) NOT NULL,
    estimated_cost_usd  NUMERIC(10, 6) NOT NULL DEFAULT 0,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_build_costs_build_id ON build_costs(build_id);
