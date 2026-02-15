-- Phase 12: BYOK – user-supplied Anthropic API key for builds
-- Adds an encrypted API key column to the users table.

ALTER TABLE users ADD COLUMN IF NOT EXISTS anthropic_api_key TEXT;
