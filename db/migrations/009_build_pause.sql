-- Phase 14: Build Pause, Resume & User Interjection
ALTER TABLE builds ADD COLUMN IF NOT EXISTS paused_at TIMESTAMPTZ;
ALTER TABLE builds ADD COLUMN IF NOT EXISTS pause_reason TEXT;
ALTER TABLE builds ADD COLUMN IF NOT EXISTS pause_phase TEXT;
