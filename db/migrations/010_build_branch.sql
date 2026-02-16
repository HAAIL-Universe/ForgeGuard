-- 010: Add branch column to builds table for branch selection on build start.
ALTER TABLE builds ADD COLUMN IF NOT EXISTS branch VARCHAR(100) DEFAULT 'main';
