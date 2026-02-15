-- Phase 12: Build Target & File Writing
-- Adds target columns to builds table so builds can write files
-- to a GitHub repo (new or existing) or a local directory.

ALTER TABLE builds ADD COLUMN IF NOT EXISTS target_type VARCHAR(20);
ALTER TABLE builds ADD COLUMN IF NOT EXISTS target_ref TEXT;
ALTER TABLE builds ADD COLUMN IF NOT EXISTS working_dir TEXT;
