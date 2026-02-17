-- Migration 014: Add contract_batch to builds
-- Records which contract snapshot batch was used for a build.
-- NULL means the current/live contracts were used.

ALTER TABLE builds ADD COLUMN IF NOT EXISTS contract_batch INTEGER DEFAULT NULL;
