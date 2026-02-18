-- Phase: Forge MCP -- API keys for CLI / MCP access to read-only contract endpoints.

CREATE TABLE forge_api_keys (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    key_hash    TEXT NOT NULL UNIQUE,
    prefix      VARCHAR(12) NOT NULL,       -- first 8 chars for display ("fg_ab12...")
    label       VARCHAR(100) NOT NULL DEFAULT 'default',
    scopes      TEXT[] NOT NULL DEFAULT '{read:contracts}',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_used   TIMESTAMPTZ,
    revoked_at  TIMESTAMPTZ                 -- NULL = active, set = revoked
);

CREATE INDEX idx_forge_api_keys_user_id ON forge_api_keys(user_id);
CREATE INDEX idx_forge_api_keys_key_hash ON forge_api_keys(key_hash);
