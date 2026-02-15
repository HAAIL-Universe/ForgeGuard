"""Tests for app.audit.runner -- one test per check (A1-A9, W1-W3).

Uses temporary directories and git repos for isolation.
Each check has a known-good and known-bad fixture.
"""

import json
import os
import subprocess
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from app.audit.runner import (
    AuditResult,
    GovernanceCheckResult,
    check_a1_scope_compliance,
    check_a2_minimal_diff,
    check_a3_evidence_completeness,
    check_a4_boundary_compliance,
    check_a5_diff_log_gate,
    check_a6_authorization_gate,
    check_a7_verification_order,
    check_a8_test_gate,
    check_a9_dependency_gate,
    check_w1_secrets_in_diff,
    check_w2_audit_ledger_integrity,
    check_w3_physics_route_coverage,
    run_audit,
)


# -- Fixtures ---------------------------------------------------------------


@pytest.fixture
def tmp_project(tmp_path: Path):
    """Create a temporary git project with Forge governance structure."""
    project = tmp_path / "project"
    project.mkdir()

    # Initialize git repo
    subprocess.run(["git", "init"], cwd=str(project), capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=str(project), capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=str(project), capture_output=True,
    )

    # Create Forge governance structure
    forge = project / "Forge"
    contracts = forge / "Contracts"
    evidence = forge / "evidence"
    contracts.mkdir(parents=True)
    evidence.mkdir(parents=True)

    # Create forge.json
    forge_json = {
        "project_name": "TestProject",
        "backend": {
            "language": "python",
            "entry_module": "app.main",
            "test_framework": "pytest",
            "test_dir": "tests",
            "dependency_file": "requirements.txt",
            "venv_path": ".venv",
        },
    }
    (project / "forge.json").write_text(json.dumps(forge_json))

    # Create requirements.txt
    (project / "requirements.txt").write_text("fastapi==0.115.6\npydantic==2.10.6\n")

    # Create app directory structure
    app_dir = project / "app"
    app_dir.mkdir()
    (app_dir / "__init__.py").write_text("")
    routers_dir = app_dir / "api" / "routers"
    routers_dir.mkdir(parents=True)
    (app_dir / "api" / "__init__.py").write_text("")
    (routers_dir / "__init__.py").write_text("")
    (routers_dir / "health.py").write_text('"""Health router."""\n')

    # Initial commit
    subprocess.run(["git", "add", "."], cwd=str(project), capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "Initial commit"],
        cwd=str(project), capture_output=True,
    )

    return project


def _write_evidence(project: Path, test_pass: bool = True, diff_log_ok: bool = True):
    """Write standard evidence files for a test project."""
    evidence = project / "Forge" / "evidence"
    evidence.mkdir(parents=True, exist_ok=True)

    status = "PASS" if test_pass else "FAIL"
    (evidence / "test_runs_latest.md").write_text(f"Status: {status}\n")

    if diff_log_ok:
        (evidence / "updatedifflog.md").write_text(
            "# Diff Log\n\n## Verification\n"
            "- Static: PASS\n"
            "- Runtime: PASS\n"
            "- Behavior: PASS\n"
            "- Contract: PASS\n"
        )
    else:
        (evidence / "updatedifflog.md").write_text("")


def _stage_file(project: Path, rel_path: str, content: str):
    """Create a file and stage it in git."""
    full = project / rel_path
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_text(content)
    subprocess.run(["git", "add", rel_path], cwd=str(project), capture_output=True)


# -- A1: Scope compliance --------------------------------------------------


class TestA1ScopeCompliance:
    def test_pass_exact_match(self, tmp_project: Path):
        _stage_file(tmp_project, "app/new.py", "# new file\n")
        result = check_a1_scope_compliance(["app/new.py"], str(tmp_project))
        assert result["result"] == "PASS"
        assert result["code"] == "A1"

    def test_fail_unclaimed_file(self, tmp_project: Path):
        _stage_file(tmp_project, "app/new.py", "# new file\n")
        _stage_file(tmp_project, "app/extra.py", "# extra file\n")
        result = check_a1_scope_compliance(["app/new.py"], str(tmp_project))
        assert result["result"] == "FAIL"
        assert "Unclaimed" in (result["detail"] or "")

    def test_fail_phantom_file(self, tmp_project: Path):
        _stage_file(tmp_project, "app/new.py", "# new file\n")
        result = check_a1_scope_compliance(
            ["app/new.py", "app/ghost.py"], str(tmp_project)
        )
        assert result["result"] == "FAIL"
        assert "Claimed but not in diff" in (result["detail"] or "")


# -- A2: Minimal-diff discipline -------------------------------------------


class TestA2MinimalDiff:
    def test_pass_no_renames(self, tmp_project: Path):
        _stage_file(tmp_project, "app/new.py", "# content\n")
        result = check_a2_minimal_diff(str(tmp_project))
        assert result["result"] == "PASS"

    def test_fail_rename_detected(self, tmp_project: Path):
        # Create a file, commit it, then rename
        _stage_file(tmp_project, "app/old.py", "# old\n")
        subprocess.run(
            ["git", "commit", "-m", "add old"], cwd=str(tmp_project),
            capture_output=True,
        )
        subprocess.run(
            ["git", "mv", "app/old.py", "app/renamed.py"],
            cwd=str(tmp_project), capture_output=True,
        )
        result = check_a2_minimal_diff(str(tmp_project))
        assert result["result"] == "FAIL"
        assert "rename" in (result["detail"] or "").lower()


# -- A3: Evidence completeness ---------------------------------------------


class TestA3EvidenceCompleteness:
    def test_pass_all_present(self, tmp_project: Path):
        _write_evidence(tmp_project, test_pass=True, diff_log_ok=True)
        gov_root = str(tmp_project / "Forge")
        result = check_a3_evidence_completeness(gov_root)
        assert result["result"] == "PASS"

    def test_fail_missing_test_runs(self, tmp_project: Path):
        evidence = tmp_project / "Forge" / "evidence"
        evidence.mkdir(parents=True, exist_ok=True)
        (evidence / "updatedifflog.md").write_text("content\n")
        gov_root = str(tmp_project / "Forge")
        result = check_a3_evidence_completeness(gov_root)
        assert result["result"] == "FAIL"
        assert "test_runs_latest.md missing" in (result["detail"] or "")

    def test_fail_test_status_not_pass(self, tmp_project: Path):
        _write_evidence(tmp_project, test_pass=False, diff_log_ok=True)
        gov_root = str(tmp_project / "Forge")
        result = check_a3_evidence_completeness(gov_root)
        assert result["result"] == "FAIL"
        assert "FAIL" in (result["detail"] or "")

    def test_fail_empty_diff_log(self, tmp_project: Path):
        evidence = tmp_project / "Forge" / "evidence"
        evidence.mkdir(parents=True, exist_ok=True)
        (evidence / "test_runs_latest.md").write_text("Status: PASS\n")
        (evidence / "updatedifflog.md").write_text("")
        gov_root = str(tmp_project / "Forge")
        result = check_a3_evidence_completeness(gov_root)
        assert result["result"] == "FAIL"
        assert "empty" in (result["detail"] or "")


# -- A4: Boundary compliance -----------------------------------------------


class TestA4BoundaryCompliance:
    def test_pass_no_violations(self, tmp_project: Path):
        boundaries = {
            "layers": [
                {
                    "name": "routers",
                    "glob": "app/api/routers/*.py",
                    "forbidden": [
                        {"pattern": "asyncpg", "reason": "DB in repos only"}
                    ],
                }
            ]
        }
        (tmp_project / "Forge" / "Contracts" / "boundaries.json").write_text(
            json.dumps(boundaries)
        )
        (tmp_project / "app" / "api" / "routers" / "health.py").write_text(
            '"""Clean router."""\nfrom fastapi import APIRouter\n'
        )
        result = check_a4_boundary_compliance(
            str(tmp_project), str(tmp_project / "Forge")
        )
        assert result["result"] == "PASS"

    def test_fail_forbidden_pattern(self, tmp_project: Path):
        boundaries = {
            "layers": [
                {
                    "name": "routers",
                    "glob": "app/api/routers/*.py",
                    "forbidden": [
                        {"pattern": "asyncpg", "reason": "DB in repos only"}
                    ],
                }
            ]
        }
        (tmp_project / "Forge" / "Contracts" / "boundaries.json").write_text(
            json.dumps(boundaries)
        )
        (tmp_project / "app" / "api" / "routers" / "health.py").write_text(
            "import asyncpg\n"
        )
        result = check_a4_boundary_compliance(
            str(tmp_project), str(tmp_project / "Forge")
        )
        assert result["result"] == "FAIL"
        assert "asyncpg" in (result["detail"] or "")

    def test_pass_no_boundaries_file(self, tmp_project: Path):
        result = check_a4_boundary_compliance(
            str(tmp_project), str(tmp_project / "Forge")
        )
        assert result["result"] == "PASS"
        assert "skipped" in (result["detail"] or "").lower()


# -- A5: Diff Log Gate ------------------------------------------------------


class TestA5DiffLogGate:
    def test_pass_no_todo(self, tmp_project: Path):
        _write_evidence(tmp_project)
        gov_root = str(tmp_project / "Forge")
        result = check_a5_diff_log_gate(gov_root)
        assert result["result"] == "PASS"

    def test_fail_has_todo(self, tmp_project: Path):
        evidence = tmp_project / "Forge" / "evidence"
        evidence.mkdir(parents=True, exist_ok=True)
        marker = "TO" + "DO:"
        (evidence / "updatedifflog.md").write_text(
            f"# Diff Log\n- {marker} fill in summary\n"
        )
        gov_root = str(tmp_project / "Forge")
        result = check_a5_diff_log_gate(gov_root)
        assert result["result"] == "FAIL"
        assert marker in (result["detail"] or "")

    def test_fail_missing_file(self, tmp_project: Path):
        gov_root = str(tmp_project / "Forge")
        result = check_a5_diff_log_gate(gov_root)
        assert result["result"] == "FAIL"
        assert "missing" in (result["detail"] or "").lower()


# -- A6: Authorization Gate -------------------------------------------------


class TestA6AuthorizationGate:
    def test_pass_no_prior_authorized(self, tmp_project: Path):
        gov_root = str(tmp_project / "Forge")
        result = check_a6_authorization_gate(str(tmp_project), gov_root)
        assert result["result"] == "PASS"
        assert "No prior AUTHORIZED" in (result["detail"] or "")

    def test_pass_no_unauthorized_commits(self, tmp_project: Path):
        # Get current HEAD hash
        proc = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(tmp_project), capture_output=True, text=True,
        )
        head_hash = proc.stdout.strip()

        # Write ledger with the current commit hash
        evidence = tmp_project / "Forge" / "evidence"
        evidence.mkdir(parents=True, exist_ok=True)
        (evidence / "audit_ledger.md").write_text(
            f"# Ledger\ncommit: {head_hash}\n"
        )
        gov_root = str(tmp_project / "Forge")
        result = check_a6_authorization_gate(str(tmp_project), gov_root)
        assert result["result"] == "PASS"


# -- A7: Verification hierarchy order --------------------------------------


class TestA7VerificationOrder:
    def test_pass_correct_order(self, tmp_project: Path):
        _write_evidence(tmp_project)
        gov_root = str(tmp_project / "Forge")
        result = check_a7_verification_order(gov_root)
        assert result["result"] == "PASS"

    def test_fail_wrong_order(self, tmp_project: Path):
        evidence = tmp_project / "Forge" / "evidence"
        evidence.mkdir(parents=True, exist_ok=True)
        (evidence / "updatedifflog.md").write_text(
            "# Diff Log\n"
            "- Contract: PASS\n"
            "- Behavior: PASS\n"
            "- Runtime: PASS\n"
            "- Static: PASS\n"
        )
        gov_root = str(tmp_project / "Forge")
        result = check_a7_verification_order(gov_root)
        assert result["result"] == "FAIL"
        assert "out of order" in (result["detail"] or "").lower()

    def test_fail_missing_keyword(self, tmp_project: Path):
        evidence = tmp_project / "Forge" / "evidence"
        evidence.mkdir(parents=True, exist_ok=True)
        (evidence / "updatedifflog.md").write_text(
            "# Diff Log\n- Static: PASS\n- Runtime: PASS\n"
        )
        gov_root = str(tmp_project / "Forge")
        result = check_a7_verification_order(gov_root)
        assert result["result"] == "FAIL"
        assert "Missing" in (result["detail"] or "")


# -- A8: Test gate ----------------------------------------------------------


class TestA8TestGate:
    def test_pass_status_pass(self, tmp_project: Path):
        _write_evidence(tmp_project, test_pass=True)
        gov_root = str(tmp_project / "Forge")
        result = check_a8_test_gate(gov_root)
        assert result["result"] == "PASS"

    def test_fail_status_fail(self, tmp_project: Path):
        _write_evidence(tmp_project, test_pass=False)
        gov_root = str(tmp_project / "Forge")
        result = check_a8_test_gate(gov_root)
        assert result["result"] == "FAIL"

    def test_fail_missing_file(self, tmp_project: Path):
        gov_root = str(tmp_project / "Forge")
        result = check_a8_test_gate(gov_root)
        assert result["result"] == "FAIL"
        assert "missing" in (result["detail"] or "").lower()


# -- A9: Dependency gate ----------------------------------------------------


class TestA9DependencyGate:
    def test_pass_declared_dependency(self, tmp_project: Path):
        _stage_file(
            tmp_project, "app/new.py", "from fastapi import APIRouter\n"
        )
        result = check_a9_dependency_gate(["app/new.py"], str(tmp_project))
        assert result["result"] == "PASS"

    def test_fail_undeclared_dependency(self, tmp_project: Path):
        _stage_file(
            tmp_project, "app/new.py", "import someunknownpackage\n"
        )
        result = check_a9_dependency_gate(["app/new.py"], str(tmp_project))
        assert result["result"] == "FAIL"
        assert "someunknownpackage" in (result["detail"] or "")

    def test_pass_stdlib_import(self, tmp_project: Path):
        _stage_file(
            tmp_project, "app/new.py", "import os\nimport sys\nimport json\n"
        )
        result = check_a9_dependency_gate(["app/new.py"], str(tmp_project))
        assert result["result"] == "PASS"

    def test_pass_local_import(self, tmp_project: Path):
        _stage_file(
            tmp_project, "app/new.py", "from app.config import Settings\n"
        )
        result = check_a9_dependency_gate(["app/new.py"], str(tmp_project))
        assert result["result"] == "PASS"

    def test_pass_no_forge_json(self, tmp_project: Path):
        # Remove forge.json
        (tmp_project / "forge.json").unlink()
        result = check_a9_dependency_gate(["app/new.py"], str(tmp_project))
        assert result["result"] == "PASS"
        assert "skipped" in (result["detail"] or "").lower()


# -- W1: No secrets in diff ------------------------------------------------


class TestW1SecretsInDiff:
    def test_pass_no_secrets(self, tmp_project: Path):
        _stage_file(tmp_project, "app/clean.py", "x = 42\n")
        result = check_w1_secrets_in_diff(str(tmp_project))
        assert result["result"] in ("PASS", "WARN")
        assert result["code"] == "W1"

    def test_warn_secret_pattern(self, tmp_project: Path):
        _stage_file(
            tmp_project, "app/bad.py", 'API_KEY = "sk-abc123secret"\n'
        )
        result = check_w1_secrets_in_diff(str(tmp_project))
        assert result["result"] == "WARN"
        assert "sk-" in (result["detail"] or "")


# -- W2: Audit ledger integrity ---------------------------------------------


class TestW2AuditLedgerIntegrity:
    def test_pass_exists_non_empty(self, tmp_project: Path):
        evidence = tmp_project / "Forge" / "evidence"
        evidence.mkdir(parents=True, exist_ok=True)
        (evidence / "audit_ledger.md").write_text("# Ledger\nEntry 1\n")
        gov_root = str(tmp_project / "Forge")
        result = check_w2_audit_ledger_integrity(gov_root)
        assert result["result"] == "PASS"

    def test_warn_missing(self, tmp_project: Path):
        gov_root = str(tmp_project / "Forge")
        result = check_w2_audit_ledger_integrity(gov_root)
        assert result["result"] == "WARN"
        assert "does not exist" in (result["detail"] or "")

    def test_warn_empty(self, tmp_project: Path):
        evidence = tmp_project / "Forge" / "evidence"
        evidence.mkdir(parents=True, exist_ok=True)
        (evidence / "audit_ledger.md").write_text("")
        gov_root = str(tmp_project / "Forge")
        result = check_w2_audit_ledger_integrity(gov_root)
        assert result["result"] == "WARN"
        assert "empty" in (result["detail"] or "")


# -- W3: Physics route coverage ---------------------------------------------


class TestW3PhysicsRouteCoverage:
    def test_pass_all_covered(self, tmp_project: Path):
        physics_yaml = (
            "paths:\n"
            "  /health:\n"
            "    get:\n"
            "      summary: Health\n"
        )
        (tmp_project / "Forge" / "Contracts" / "physics.yaml").write_text(
            physics_yaml
        )
        result = check_w3_physics_route_coverage(
            str(tmp_project), str(tmp_project / "Forge")
        )
        assert result["result"] == "PASS"

    def test_warn_uncovered_route(self, tmp_project: Path):
        physics_yaml = (
            "paths:\n"
            "  /health:\n"
            "    get:\n"
            "      summary: Health\n"
            "  /users:\n"
            "    get:\n"
            "      summary: List users\n"
        )
        (tmp_project / "Forge" / "Contracts" / "physics.yaml").write_text(
            physics_yaml
        )
        result = check_w3_physics_route_coverage(
            str(tmp_project), str(tmp_project / "Forge")
        )
        assert result["result"] == "WARN"
        assert "users" in (result["detail"] or "")

    def test_warn_no_physics(self, tmp_project: Path):
        gov_root = str(tmp_project / "Forge")
        result = check_w3_physics_route_coverage(str(tmp_project), gov_root)
        assert result["result"] == "WARN"
        assert "not found" in (result["detail"] or "").lower()


# -- Integration: run_audit -------------------------------------------------


class TestRunAudit:
    def test_run_audit_returns_structured_result(self, tmp_project: Path):
        _write_evidence(tmp_project)
        _stage_file(tmp_project, "app/new.py", "# content\n")
        result = run_audit(
            claimed_files=["app/new.py"],
            phase="Phase 7 Test",
            project_root=str(tmp_project),
            append_ledger=False,
        )
        assert "phase" in result
        assert "timestamp" in result
        assert "overall" in result
        assert "checks" in result
        assert "warnings" in result
        assert len(result["checks"]) == 9  # A1-A9
        assert len(result["warnings"]) == 3  # W1-W3

    def test_run_audit_appends_ledger(self, tmp_project: Path):
        _write_evidence(tmp_project)
        _stage_file(tmp_project, "app/new.py", "# content\n")
        run_audit(
            claimed_files=["app/new.py"],
            phase="Phase 7 Test",
            project_root=str(tmp_project),
            append_ledger=True,
        )
        ledger = (
            tmp_project / "Forge" / "evidence" / "audit_ledger.md"
        ).read_text()
        assert "Phase 7 Test" in ledger
        assert "Iteration" in ledger


# -- API endpoint test -------------------------------------------------------


class TestAuditRunEndpoint:
    @pytest.fixture
    def client(self):
        from unittest.mock import AsyncMock

        from fastapi.testclient import TestClient

        from app.main import create_app

        test_app = create_app()

        mock_user = {
            "id": "00000000-0000-0000-0000-000000000001",
            "github_login": "testuser",
            "avatar_url": "https://example.com/avatar.png",
        }

        async def _mock_get_current_user():
            return mock_user

        from app.api.deps import get_current_user

        test_app.dependency_overrides[get_current_user] = _mock_get_current_user
        return TestClient(test_app)

    def test_audit_run_endpoint_requires_auth(self):
        from fastapi.testclient import TestClient

        from app.main import create_app

        test_app = create_app()
        client = TestClient(test_app)
        resp = client.get("/audit/run?claimed_files=test.py")
        assert resp.status_code == 401

    def test_audit_run_endpoint_returns_result(self, client):
        with patch(
            "app.services.audit_service.run_audit"
        ) as mock_run:
            mock_run.return_value = {
                "phase": "test",
                "timestamp": "2026-01-01T00:00:00Z",
                "overall": "PASS",
                "checks": [],
                "warnings": [],
            }
            resp = client.get(
                "/audit/run?claimed_files=test.py&phase=test"
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["overall"] == "PASS"
            assert "checks" in data
