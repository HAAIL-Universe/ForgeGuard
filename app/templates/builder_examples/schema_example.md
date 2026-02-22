# [PROJECT NAME] — Database Schema

⚠ FORMAT SPECIFICATION ONLY.
Do NOT use table names from this skeleton. All table names must come from the user's domain.
Database syntax MUST match the engine in the canonical anchor.

## Conventions

- Table names: `snake_case`, plural
- Primary keys: `id UUID DEFAULT gen_random_uuid()`
- Timestamps: `created_at TIMESTAMPTZ NOT NULL DEFAULT now()`, `updated_at TIMESTAMPTZ NOT NULL DEFAULT now()`
- Foreign keys: `{referenced_table_singular}_id UUID NOT NULL REFERENCES {table}(id)`
- Soft deletes (if applicable): `deleted_at TIMESTAMPTZ` (NULL = active)

## Tables

```sql
-- Replace [entity_plural] with the actual domain entity name (e.g. orders, listings, reports)
CREATE TABLE [primary_entity_plural] (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    [field_name]    [TYPE] NOT NULL,
    [field_name]    [TYPE],
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_[primary_entity]_[field] ON [primary_entity_plural] ([field_name]);

CREATE TABLE [secondary_entity_plural] (
    id                              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    [primary_entity_singular]_id   UUID NOT NULL REFERENCES [primary_entity_plural](id) ON DELETE CASCADE,
    [field_name]                    [TYPE] NOT NULL,
    created_at                      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at                      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_[secondary_entity]_[fk_field] ON [secondary_entity_plural] ([primary_entity_singular]_id);
```
