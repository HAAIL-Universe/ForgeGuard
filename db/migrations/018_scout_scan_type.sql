-- Add scan_type column to scout_runs for deep scan support
ALTER TABLE scout_runs ADD COLUMN IF NOT EXISTS scan_type VARCHAR(10) NOT NULL DEFAULT 'quick';
