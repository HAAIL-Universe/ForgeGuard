-- Phase 29: Cache the planner's output on the project so retried builds
-- skip the $0.20 planner cost and reuse the existing plan.
--
-- cached_plan_json  — the full plan.json dict produced by the Forge Planner Agent
-- plan_cached_at   — when the plan was stored (used to decide staleness)

ALTER TABLE projects
    ADD COLUMN IF NOT EXISTS cached_plan_json  JSONB,
    ADD COLUMN IF NOT EXISTS plan_cached_at    TIMESTAMPTZ;
