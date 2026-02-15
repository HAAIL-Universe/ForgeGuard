-- 007: Add audit_llm_enabled toggle per user (default true)
ALTER TABLE users ADD COLUMN IF NOT EXISTS audit_llm_enabled BOOLEAN NOT NULL DEFAULT true;
