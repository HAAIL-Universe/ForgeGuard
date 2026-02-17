-- Track highest completed phase number for build continuation.
-- -1 means no phases completed yet.
ALTER TABLE builds ADD COLUMN IF NOT EXISTS completed_phases INTEGER NOT NULL DEFAULT -1;
