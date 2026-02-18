-- Add build_mode column to projects table.
-- 'mini' = shortened questionnaire + 2 phases; 'full' = default 7-section build.
ALTER TABLE projects ADD COLUMN IF NOT EXISTS build_mode VARCHAR(20) NOT NULL DEFAULT 'full';
