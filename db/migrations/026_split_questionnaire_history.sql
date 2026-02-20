-- Phase 60 — Split conversation_history out of questionnaire_state
-- The questionnaire_state JSONB column accumulated conversation_history
-- (many KB of chat turns) alongside the lightweight answers/sections data.
-- Every query — including the project list — fetched the entire blob.
--
-- This migration:
--   1. Adds a dedicated questionnaire_history JSONB column
--   2. Copies conversation_history + token_usage into it
--   3. Strips conversation_history from questionnaire_state
--
-- The new column stores: { "conversation_history": [...], "token_usage": {...} }
-- The trimmed questionnaire_state keeps: { "completed_sections": [...], "answers": {...} }

ALTER TABLE projects
    ADD COLUMN IF NOT EXISTS questionnaire_history JSONB NOT NULL DEFAULT '{}'::jsonb;

-- Migrate existing data: copy conversation_history & token_usage into the new column
UPDATE projects
SET questionnaire_history = jsonb_build_object(
        'conversation_history', COALESCE(questionnaire_state -> 'conversation_history', '[]'::jsonb),
        'token_usage',          COALESCE(questionnaire_state -> 'token_usage', '{"input_tokens": 0, "output_tokens": 0}'::jsonb)
    )
WHERE questionnaire_state != '{}'::jsonb
  AND questionnaire_state IS NOT NULL;

-- Strip the migrated keys from questionnaire_state
UPDATE projects
SET questionnaire_state = questionnaire_state - 'conversation_history' - 'token_usage'
WHERE questionnaire_state != '{}'::jsonb
  AND questionnaire_state IS NOT NULL;
