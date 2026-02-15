# {project_name} — Database Schema

Canonical database schema. All migrations must implement this schema.

---

## Schema Version: 0.1 (initial)

### Conventions

- Table names: snake_case, plural
- Column names: snake_case
- Primary keys: UUID (gen_random_uuid())
- Timestamps: TIMESTAMPTZ
- Soft delete: No

---

## Tables

{schema_tables}

---

## Migration Files

```
db/migrations/
  001_initial_schema.sql
```
