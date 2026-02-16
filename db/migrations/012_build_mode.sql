-- Migration 012: Add build_mode column to builds table
-- Supports plan_execute vs conversation build architecture selection

ALTER TABLE builds
    ADD COLUMN IF NOT EXISTS build_mode VARCHAR(20) DEFAULT 'plan_execute';
