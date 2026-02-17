-- Phase 31: Contract version history (snapshots)
-- Preserves previous contract generations as numbered batches.

CREATE TABLE contract_snapshots (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id      UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    batch           INTEGER NOT NULL,
    contract_type   VARCHAR(50) NOT NULL,
    content         TEXT NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_contract_snapshots_project_batch ON contract_snapshots(project_id, batch);
CREATE INDEX idx_contract_snapshots_project_id ON contract_snapshots(project_id);
