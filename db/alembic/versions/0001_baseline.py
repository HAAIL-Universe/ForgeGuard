"""Baseline — consolidates migrations 001-019.

Revision ID: 0001_baseline
Revises: None
Create Date: 2026-02-17

This migration is idempotent (uses IF NOT EXISTS / IF NOT EXISTS patterns)
so it is safe to run on both fresh databases and databases that already
have the schema from the 19 raw SQL files.
"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0001_baseline"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # -- 001 initial schema ---------------------------------------------------
    op.execute('CREATE EXTENSION IF NOT EXISTS "pgcrypto"')

    op.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            github_id       BIGINT NOT NULL UNIQUE,
            github_login    VARCHAR(255) NOT NULL,
            avatar_url      TEXT,
            access_token    TEXT NOT NULL,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_users_github_id ON users(github_id)"
    )

    op.execute("""
        CREATE TABLE IF NOT EXISTS repos (
            id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id         UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            github_repo_id  BIGINT NOT NULL,
            full_name       VARCHAR(500) NOT NULL,
            default_branch  VARCHAR(255) NOT NULL DEFAULT 'main',
            webhook_id      BIGINT,
            webhook_active  BOOLEAN NOT NULL DEFAULT false,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_repos_github_repo_id ON repos(github_repo_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_repos_user_id ON repos(user_id)"
    )

    op.execute("""
        CREATE TABLE IF NOT EXISTS audit_runs (
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
        )
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_audit_runs_repo_id ON audit_runs(repo_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_audit_runs_repo_id_created "
        "ON audit_runs(repo_id, created_at DESC)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_audit_runs_commit_sha ON audit_runs(commit_sha)"
    )

    op.execute("""
        CREATE TABLE IF NOT EXISTS audit_checks (
            id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            audit_run_id    UUID NOT NULL REFERENCES audit_runs(id) ON DELETE CASCADE,
            check_code      VARCHAR(10) NOT NULL,
            check_name      VARCHAR(100) NOT NULL,
            result          VARCHAR(10) NOT NULL,
            detail          TEXT,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_audit_checks_audit_run_id "
        "ON audit_checks(audit_run_id)"
    )

    # -- 002 projects ----------------------------------------------------------
    op.execute("""
        CREATE TABLE IF NOT EXISTS projects (
            id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id               UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            name                  VARCHAR(255) NOT NULL,
            description           TEXT,
            status                VARCHAR(20) NOT NULL DEFAULT 'draft',
            repo_id               UUID REFERENCES repos(id) ON DELETE SET NULL,
            questionnaire_state   JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at            TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_projects_user_id ON projects(user_id)"
    )

    op.execute("""
        CREATE TABLE IF NOT EXISTS project_contracts (
            id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            project_id      UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            contract_type   VARCHAR(50) NOT NULL,
            content         TEXT NOT NULL,
            version         INTEGER NOT NULL DEFAULT 1,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_project_contracts_project_type "
        "ON project_contracts(project_id, contract_type)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_project_contracts_project_id "
        "ON project_contracts(project_id)"
    )

    # -- 003 builds ------------------------------------------------------------
    op.execute("""
        CREATE TABLE IF NOT EXISTS builds (
            id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            project_id      UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            phase           VARCHAR(100) NOT NULL DEFAULT 'Phase 0',
            status          VARCHAR(20) NOT NULL DEFAULT 'pending',
            started_at      TIMESTAMPTZ,
            completed_at    TIMESTAMPTZ,
            loop_count      INTEGER NOT NULL DEFAULT 0,
            error_detail    TEXT,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_builds_project_id ON builds(project_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_builds_project_id_created "
        "ON builds(project_id, created_at DESC)"
    )

    op.execute("""
        CREATE TABLE IF NOT EXISTS build_logs (
            id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            build_id        UUID NOT NULL REFERENCES builds(id) ON DELETE CASCADE,
            timestamp       TIMESTAMPTZ NOT NULL DEFAULT now(),
            source          VARCHAR(50) NOT NULL DEFAULT 'builder',
            level           VARCHAR(20) NOT NULL DEFAULT 'info',
            message         TEXT NOT NULL,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_build_logs_build_id "
        "ON build_logs(build_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_build_logs_build_id_timestamp "
        "ON build_logs(build_id, timestamp)"
    )

    # -- 004 build costs -------------------------------------------------------
    op.execute("""
        CREATE TABLE IF NOT EXISTS build_costs (
            id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            build_id            UUID NOT NULL REFERENCES builds(id) ON DELETE CASCADE,
            phase               VARCHAR(100) NOT NULL,
            input_tokens        INTEGER NOT NULL DEFAULT 0,
            output_tokens       INTEGER NOT NULL DEFAULT 0,
            model               VARCHAR(100) NOT NULL,
            estimated_cost_usd  NUMERIC(10, 6) NOT NULL DEFAULT 0,
            created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_build_costs_build_id "
        "ON build_costs(build_id)"
    )

    # -- 005 project local_path ------------------------------------------------
    op.execute(
        "ALTER TABLE projects ADD COLUMN IF NOT EXISTS local_path TEXT"
    )

    # -- 006 user API key ------------------------------------------------------
    op.execute(
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS anthropic_api_key TEXT"
    )

    # -- 007 audit LLM toggle -------------------------------------------------
    op.execute(
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS "
        "audit_llm_enabled BOOLEAN NOT NULL DEFAULT true"
    )

    # -- 008 build targets -----------------------------------------------------
    op.execute(
        "ALTER TABLE builds ADD COLUMN IF NOT EXISTS target_type VARCHAR(20)"
    )
    op.execute(
        "ALTER TABLE builds ADD COLUMN IF NOT EXISTS target_ref TEXT"
    )
    op.execute(
        "ALTER TABLE builds ADD COLUMN IF NOT EXISTS working_dir TEXT"
    )

    # -- 009 build pause -------------------------------------------------------
    op.execute(
        "ALTER TABLE builds ADD COLUMN IF NOT EXISTS paused_at TIMESTAMPTZ"
    )
    op.execute(
        "ALTER TABLE builds ADD COLUMN IF NOT EXISTS pause_reason TEXT"
    )
    op.execute(
        "ALTER TABLE builds ADD COLUMN IF NOT EXISTS pause_phase TEXT"
    )

    # -- 010 build branch ------------------------------------------------------
    op.execute(
        "ALTER TABLE builds ADD COLUMN IF NOT EXISTS "
        "branch VARCHAR(100) DEFAULT 'main'"
    )

    # -- 011 user API key 2 ----------------------------------------------------
    op.execute(
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS anthropic_api_key_2 TEXT"
    )

    # -- 012 build mode --------------------------------------------------------
    op.execute(
        "ALTER TABLE builds ADD COLUMN IF NOT EXISTS "
        "build_mode VARCHAR(20) DEFAULT 'plan_execute'"
    )

    # -- 013 contract snapshots ------------------------------------------------
    op.execute("""
        CREATE TABLE IF NOT EXISTS contract_snapshots (
            id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            project_id      UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            batch           INTEGER NOT NULL,
            contract_type   VARCHAR(50) NOT NULL,
            content         TEXT NOT NULL,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_contract_snapshots_project_batch "
        "ON contract_snapshots(project_id, batch)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_contract_snapshots_project_id "
        "ON contract_snapshots(project_id)"
    )

    # -- 014 build contract batch ----------------------------------------------
    op.execute(
        "ALTER TABLE builds ADD COLUMN IF NOT EXISTS "
        "contract_batch INTEGER DEFAULT NULL"
    )

    # -- 015 scout runs --------------------------------------------------------
    op.execute("""
        CREATE TABLE IF NOT EXISTS scout_runs (
            id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            repo_id         UUID NOT NULL REFERENCES repos(id) ON DELETE CASCADE,
            user_id         UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            status          VARCHAR(20) NOT NULL DEFAULT 'pending',
            hypothesis      TEXT,
            results         JSONB,
            checks_passed   INTEGER NOT NULL DEFAULT 0,
            checks_failed   INTEGER NOT NULL DEFAULT 0,
            checks_warned   INTEGER NOT NULL DEFAULT 0,
            started_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            completed_at    TIMESTAMPTZ
        )
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_scout_runs_repo "
        "ON scout_runs(repo_id, started_at DESC)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_scout_runs_user "
        "ON scout_runs(user_id, started_at DESC)"
    )

    # -- 016 build completed phases --------------------------------------------
    op.execute(
        "ALTER TABLE builds ADD COLUMN IF NOT EXISTS "
        "completed_phases INTEGER NOT NULL DEFAULT -1"
    )

    # -- 017 user spend cap ----------------------------------------------------
    op.execute(
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS "
        "build_spend_cap NUMERIC(10,2) DEFAULT NULL"
    )

    # -- 018 scout scan type ---------------------------------------------------
    op.execute(
        "ALTER TABLE scout_runs ADD COLUMN IF NOT EXISTS "
        "scan_type VARCHAR(10) NOT NULL DEFAULT 'quick'"
    )

    # -- 019 scout computed score ----------------------------------------------
    op.execute(
        "ALTER TABLE scout_runs ADD COLUMN IF NOT EXISTS computed_score INTEGER"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_scout_runs_score "
        "ON scout_runs(repo_id, computed_score) "
        "WHERE computed_score IS NOT NULL AND scan_type = 'deep'"
    )


def downgrade() -> None:
    """Drop all application tables in reverse dependency order."""
    op.execute("DROP TABLE IF EXISTS contract_snapshots CASCADE")
    op.execute("DROP TABLE IF EXISTS build_costs CASCADE")
    op.execute("DROP TABLE IF EXISTS build_logs CASCADE")
    op.execute("DROP TABLE IF EXISTS builds CASCADE")
    op.execute("DROP TABLE IF EXISTS project_contracts CASCADE")
    op.execute("DROP TABLE IF EXISTS projects CASCADE")
    op.execute("DROP TABLE IF EXISTS scout_runs CASCADE")
    op.execute("DROP TABLE IF EXISTS audit_checks CASCADE")
    op.execute("DROP TABLE IF EXISTS audit_runs CASCADE")
    op.execute("DROP TABLE IF EXISTS repos CASCADE")
    op.execute("DROP TABLE IF EXISTS users CASCADE")
