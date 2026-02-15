-- Phase 8: Project Intake & Questionnaire tables

CREATE TABLE projects (
    id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id               UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name                  VARCHAR(255) NOT NULL,
    description           TEXT,
    status                VARCHAR(20) NOT NULL DEFAULT 'draft',
    repo_id               UUID REFERENCES repos(id) ON DELETE SET NULL,
    questionnaire_state   JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at            TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_projects_user_id ON projects(user_id);

CREATE TABLE project_contracts (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id      UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    contract_type   VARCHAR(50) NOT NULL,
    content         TEXT NOT NULL,
    version         INTEGER NOT NULL DEFAULT 1,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX idx_project_contracts_project_type ON project_contracts(project_id, contract_type);
CREATE INDEX idx_project_contracts_project_id ON project_contracts(project_id);
