-- Add local_path column for projects that use local filesystem source
ALTER TABLE projects ADD COLUMN IF NOT EXISTS local_path TEXT;
