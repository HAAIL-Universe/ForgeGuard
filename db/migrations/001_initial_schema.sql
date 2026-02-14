-- ForgeGuard initial schema (v0.1)
-- Creates all tables for the application.

CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- users: authenticated GitHub users
CREATE TABLE users (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    github_id       BIGINT NOT NULL UNIQUE,
    github_login    VARCHAR(255) NOT NULL,
    avatar_url      TEXT,
    access_token    TEXT NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX idx_users_github_id ON users(github_id);

-- repos: connected GitHub repositories being monitored
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

CREATE UNIQUE INDEX idx_repos_github_repo_id ON repos(github_repo_id);
CREATE INDEX idx_repos_user_id ON repos(user_id);

-- audit_runs: one record per push-triggered audit execution
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

CREATE INDEX idx_audit_runs_repo_id ON audit_runs(repo_id);
CREATE INDEX idx_audit_runs_repo_id_created ON audit_runs(repo_id, created_at DESC);
CREATE INDEX idx_audit_runs_commit_sha ON audit_runs(commit_sha);

-- audit_checks: individual check results within an audit run
CREATE TABLE audit_checks (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    audit_run_id    UUID NOT NULL REFERENCES audit_runs(id) ON DELETE CASCADE,
    check_code      VARCHAR(10) NOT NULL,
    check_name      VARCHAR(100) NOT NULL,
    result          VARCHAR(10) NOT NULL,
    detail          TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_audit_checks_audit_run_id ON audit_checks(audit_run_id);
