-- 017: Add per-build spend cap to users table.
-- Users can set a maximum USD amount they're willing to spend per build.
-- NULL means no cap (unlimited).

ALTER TABLE users ADD COLUMN IF NOT EXISTS build_spend_cap NUMERIC(10,2) DEFAULT NULL;
