# ForgeGuard — Database Schema

Canonical database schema for this project. The builder contract (S1) requires reading this file before making changes. All migrations must implement this schema. No tables or columns may be added without updating this document first.

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

### users

Stores authenticated GitHub users.

```sql
CREATE TABLE users (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    github_id       BIGINT NOT NULL UNIQUE,
    github_login    VARCHAR(255) NOT NULL,
    avatar_url      TEXT,
    access_token    TEXT NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

```sql
CREATE UNIQUE INDEX idx_users_github_id ON users(github_id);
```

---

### repos

Stores connected GitHub repositories being monitored.

```sql
CREATE TABLE repos (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    github_repo_id  BIGINT NOT NULL,
    full_name       VARCHAR(500) NOT NULL,
    default_branch  VARCHAR(255) NOT NULL DEFAULT 'main',
    webhook_id      BIGINT,
    webhook_active  BOOLEAN NOT NULL DEFAULT false,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

```sql
CREATE UNIQUE INDEX idx_repos_github_repo_id ON repos(github_repo_id);
CREATE INDEX idx_repos_user_id ON repos(user_id);
```

---

### audit_runs

Stores one record per push-triggered audit execution.

```sql
CREATE TABLE audit_runs (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    repo_id         UUID NOT NULL REFERENCES repos(id) ON DELETE CASCADE,
    commit_sha      VARCHAR(40) NOT NULL,
    commit_message  TEXT,
    commit_author   VARCHAR(255),
    branch          VARCHAR(255),
    status          VARCHAR(20) NOT NULL DEFAULT 'pending',
    overall_result  VARCHAR(10),
    started_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at    TIMESTAMPTZ,
    files_checked   INTEGER DEFAULT 0,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

`status` values: `pending`, `running`, `completed`, `error`
`overall_result` values: `PASS`, `FAIL`, `ERROR`, `null` (while pending/running)

```sql
CREATE INDEX idx_audit_runs_repo_id ON audit_runs(repo_id);
CREATE INDEX idx_audit_runs_repo_id_created ON audit_runs(repo_id, created_at DESC);
CREATE INDEX idx_audit_runs_commit_sha ON audit_runs(commit_sha);
```

---

### audit_checks

Stores individual check results within an audit run.

```sql
CREATE TABLE audit_checks (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    audit_run_id    UUID NOT NULL REFERENCES audit_runs(id) ON DELETE CASCADE,
    check_code      VARCHAR(10) NOT NULL,
    check_name      VARCHAR(100) NOT NULL,
    result          VARCHAR(10) NOT NULL,
    detail          TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

`check_code` values: `A4`, `A9`, `W1` (MVP checks only)
`result` values: `PASS`, `FAIL`, `WARN`, `ERROR`

```sql
CREATE INDEX idx_audit_checks_audit_run_id ON audit_checks(audit_run_id);
```

---

### projects

Stores user projects created via the intake questionnaire.

```sql
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
```

`status` values: `draft`, `questionnaire`, `contracts_ready`, `building`, `completed`

```sql
CREATE INDEX idx_projects_user_id ON projects(user_id);
```

---

### project_contracts

Stores generated contract files for a project.

```sql
CREATE TABLE project_contracts (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id      UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    contract_type   VARCHAR(50) NOT NULL,
    content         TEXT NOT NULL,
    version         INTEGER NOT NULL DEFAULT 1,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

`contract_type` values: `blueprint`, `manifesto`, `stack`, `schema`, `physics`, `boundaries`, `phases`, `ui`, `builder_contract`, `builder_directive`

```sql
CREATE UNIQUE INDEX idx_project_contracts_project_type ON project_contracts(project_id, contract_type);
CREATE INDEX idx_project_contracts_project_id ON project_contracts(project_id);
```

---

### builds

Stores one record per build orchestration run.

```sql
CREATE TABLE builds (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id      UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    phase           VARCHAR(100) NOT NULL DEFAULT 'Phase 0',
    status          VARCHAR(20) NOT NULL DEFAULT 'pending',
    started_at      TIMESTAMPTZ,
    completed_at    TIMESTAMPTZ,
    loop_count      INTEGER NOT NULL DEFAULT 0,
    error_detail    TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

`status` values: `pending`, `running`, `completed`, `failed`, `cancelled`

```sql
CREATE INDEX idx_builds_project_id ON builds(project_id);
CREATE INDEX idx_builds_project_id_created ON builds(project_id, created_at DESC);
```

---

### build_logs

Captures streaming builder output for a build.

```sql
CREATE TABLE build_logs (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    build_id        UUID NOT NULL REFERENCES builds(id) ON DELETE CASCADE,
    timestamp       TIMESTAMPTZ NOT NULL DEFAULT now(),
    source          VARCHAR(50) NOT NULL DEFAULT 'builder',
    level           VARCHAR(20) NOT NULL DEFAULT 'info',
    message         TEXT NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

`source` values: `builder`, `audit`, `system`
`level` values: `info`, `warn`, `error`, `debug`

```sql
CREATE INDEX idx_build_logs_build_id ON build_logs(build_id);
CREATE INDEX idx_build_logs_build_id_timestamp ON build_logs(build_id, timestamp);
```

---

## Schema -> Phase Traceability

| Table | Repo Created In | Wired To Caller In | Notes |
|-------|-----------------|-------------------|-------|
| users | Phase 1 | Phase 1 | Auth flow creates user records |
| repos | Phase 2 | Phase 2 | Connect-repo flow creates repo records |
| audit_runs | Phase 3 | Phase 3 | Webhook handler creates audit runs |
| audit_checks | Phase 3 | Phase 3 | Audit engine writes check results |
| projects | Phase 8 | Phase 8 | Project intake & questionnaire |
| project_contracts | Phase 8 | Phase 8 | Generated contract files |
| builds | Phase 9 | Phase 9 | Build orchestration runs |
| build_logs | Phase 9 | Phase 9 | Streaming builder output |

---

## Migration Files

The builder creates migration files in `db/migrations/` during Phase 0.

```
db/migrations/
  001_initial_schema.sql
  002_projects.sql
  003_builds.sql
```
