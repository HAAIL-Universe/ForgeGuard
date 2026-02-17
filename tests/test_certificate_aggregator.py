"""Tests for certificate_aggregator -- data aggregation from all subsystems."""

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

import pytest

from app.services.certificate_aggregator import (
    aggregate_certificate_data,
    _aggregate_build,
    _aggregate_audit,
    _aggregate_governance,
    _aggregate_scout,
    _empty_audit,
    _iso,
)

PROJECT_ID = UUID("11111111-1111-1111-1111-111111111111")
USER_ID = UUID("22222222-2222-2222-2222-222222222222")
REPO_ID = UUID("33333333-3333-3333-3333-333333333333")
BUILD_ID = UUID("44444444-4444-4444-4444-444444444444")
AUDIT_RUN_ID = UUID("55555555-5555-5555-5555-555555555555")
SCOUT_RUN_ID = UUID("66666666-6666-6666-6666-666666666666")

MOCK_PROJECT = {
    "id": PROJECT_ID,
    "user_id": USER_ID,
    "name": "TestProject",
    "description": "A test project",
    "status": "active",
    "repo_id": REPO_ID,
    "repo_full_name": "user/repo",
}

MOCK_BUILD = {
    "id": BUILD_ID,
    "phase": "plan_execute",
    "status": "completed",
    "loop_count": 2,
    "started_at": "2025-01-01T00:00:00Z",
    "completed_at": "2025-01-01T01:00:00Z",
    "error_detail": None,
    "completed_phases": ["scaffold", "plan_execute"],
}


# ---------- _iso ----------

def test_iso_none():
    assert _iso(None) is None

def test_iso_str():
    assert _iso("2025-01-01") == "2025-01-01"

def test_iso_datetime():
    from datetime import datetime, timezone
    dt = datetime(2025, 1, 1, tzinfo=timezone.utc)
    assert _iso(dt) == "2025-01-01T00:00:00+00:00"


# ---------- _empty_audit ----------

def test_empty_audit():
    result = _empty_audit()
    assert result["runs_total"] == 0
    assert result["recent_runs"] == []
    assert result["pass_rate"] == 0.0
    assert result["latest_result"] is None
    assert result["latest_run_id"] is None


# ---------- _aggregate_build ----------

@pytest.mark.asyncio
@patch("app.services.certificate_aggregator.get_build_cost_summary", new_callable=AsyncMock)
@patch("app.services.certificate_aggregator.get_build_stats", new_callable=AsyncMock)
@patch("app.services.certificate_aggregator.get_latest_build_for_project", new_callable=AsyncMock)
async def test_aggregate_build_with_data(mock_latest, mock_stats, mock_cost):
    mock_latest.return_value = MOCK_BUILD
    mock_stats.return_value = {"total_turns": 10, "files_written_count": 5, "git_commits_made": 3}
    mock_cost.return_value = {
        "total_input_tokens": 50000,
        "total_output_tokens": 10000,
        "total_cost_usd": 1.23,
        "phase_count": 2,
    }

    result = await _aggregate_build(PROJECT_ID)
    assert result is not None
    assert result["id"] == str(BUILD_ID)
    assert result["status"] == "completed"
    assert result["loop_count"] == 2
    assert result["stats"]["files_written_count"] == 5
    assert result["cost"]["total_cost_usd"] == 1.23


@pytest.mark.asyncio
@patch("app.services.certificate_aggregator.get_latest_build_for_project", new_callable=AsyncMock)
async def test_aggregate_build_no_build(mock_latest):
    mock_latest.return_value = None
    result = await _aggregate_build(PROJECT_ID)
    assert result is None


# ---------- _aggregate_audit ----------

@pytest.mark.asyncio
@patch("app.services.certificate_aggregator.get_audit_runs_by_repo", new_callable=AsyncMock)
async def test_aggregate_audit_with_runs(mock_runs):
    runs = [
        {"id": AUDIT_RUN_ID, "commit_sha": "abc12345", "overall_result": "PASS", "files_checked": 10, "created_at": "2025-01-01T00:00:00Z"},
        {"id": UUID("77777777-7777-7777-7777-777777777777"), "commit_sha": "def67890", "overall_result": "FAIL", "files_checked": 8, "created_at": "2024-12-31T00:00:00Z"},
    ]
    mock_runs.return_value = (runs, 2)

    result = await _aggregate_audit(REPO_ID)
    assert result["runs_total"] == 2
    assert result["pass_rate"] == 0.5
    assert result["latest_result"] == "PASS"
    assert len(result["recent_runs"]) == 2
    assert result["latest_run_id"] == str(AUDIT_RUN_ID)


@pytest.mark.asyncio
@patch("app.services.certificate_aggregator.get_audit_runs_by_repo", new_callable=AsyncMock)
async def test_aggregate_audit_no_runs(mock_runs):
    mock_runs.return_value = ([], 0)
    result = await _aggregate_audit(REPO_ID)
    assert result == _empty_audit()


@pytest.mark.asyncio
@patch("app.services.certificate_aggregator.get_audit_runs_by_repo", new_callable=AsyncMock)
async def test_aggregate_audit_all_pass(mock_runs):
    runs = [
        {"id": AUDIT_RUN_ID, "commit_sha": "abc", "overall_result": "PASS", "files_checked": 5, "created_at": "2025-01-01"},
    ]
    mock_runs.return_value = (runs, 1)
    result = await _aggregate_audit(REPO_ID)
    assert result["pass_rate"] == 1.0


# ---------- _aggregate_governance ----------

@pytest.mark.asyncio
@patch("app.services.certificate_aggregator.get_audit_run_detail", new_callable=AsyncMock)
async def test_aggregate_governance_with_checks(mock_detail):
    mock_detail.return_value = {
        "checks": [
            {"check_code": "A0", "check_name": "Contract boundary", "result": "PASS", "detail": "ok"},
            {"check_code": "A1", "check_name": "Test baseline", "result": "FAIL", "detail": "failed"},
            {"check_code": "W1", "check_name": "Secrets scan", "result": "WARN", "detail": "warning"},
        ],
    }

    audit_data = {"latest_run_id": str(AUDIT_RUN_ID)}
    result = await _aggregate_governance(audit_data)
    assert result is not None
    assert result["pass_count"] == 1
    assert result["fail_count"] == 1
    assert result["warn_count"] == 1
    assert result["total"] == 3
    assert len(result["checks"]) == 3


@pytest.mark.asyncio
async def test_aggregate_governance_no_latest_run():
    result = await _aggregate_governance({"latest_run_id": None})
    assert result is None


# ---------- _aggregate_scout ----------

@pytest.mark.asyncio
@patch("app.services.certificate_aggregator.get_scout_run", new_callable=AsyncMock)
@patch("app.services.certificate_aggregator.get_scout_runs_by_repo", new_callable=AsyncMock)
async def test_aggregate_scout_with_dossier(mock_runs, mock_full):
    mock_runs.return_value = [
        {"id": SCOUT_RUN_ID, "scan_type": "deep", "status": "completed"},
    ]
    mock_full.return_value = {
        "id": SCOUT_RUN_ID,
        "results": {
            "dossier": {
                "quality_assessment": {"score": 85},
            },
            "renovation_plan": {
                "executive_brief": {"health_grade": "B"},
            },
            "stack_profile": {"python": "3.12"},
            "architecture": {"type": "monolith"},
            "files_analysed": 42,
            "tree_size": 100,
        },
    }

    result = await _aggregate_scout(REPO_ID, USER_ID)
    assert result is not None
    assert result["quality_score"] == 85
    assert result["health_grade"] == "B"
    assert result["dossier_available"] is True
    assert result["files_analysed"] == 42


@pytest.mark.asyncio
@patch("app.services.certificate_aggregator.get_scout_runs_by_repo", new_callable=AsyncMock)
async def test_aggregate_scout_no_deep_scan(mock_runs):
    mock_runs.return_value = [
        {"id": SCOUT_RUN_ID, "scan_type": "quick", "status": "completed"},
    ]
    result = await _aggregate_scout(REPO_ID, USER_ID)
    assert result is None


# ---------- aggregate_certificate_data ----------

@pytest.mark.asyncio
@patch("app.services.certificate_aggregator.get_contracts_by_project", new_callable=AsyncMock)
@patch("app.services.certificate_aggregator._aggregate_scout", new_callable=AsyncMock)
@patch("app.services.certificate_aggregator._aggregate_governance", new_callable=AsyncMock)
@patch("app.services.certificate_aggregator._aggregate_audit", new_callable=AsyncMock)
@patch("app.services.certificate_aggregator.get_builds_for_project", new_callable=AsyncMock)
@patch("app.services.certificate_aggregator._aggregate_build", new_callable=AsyncMock)
@patch("app.services.certificate_aggregator.get_project_by_id", new_callable=AsyncMock)
async def test_aggregate_full(mock_project, mock_build, mock_builds, mock_audit, mock_gov, mock_scout, mock_contracts):
    mock_project.return_value = MOCK_PROJECT
    mock_build.return_value = {"id": str(BUILD_ID), "status": "completed"}
    mock_builds.return_value = [{"id": BUILD_ID}, {"id": UUID("88888888-8888-8888-8888-888888888888")}]
    mock_audit.return_value = {"runs_total": 3, "pass_rate": 0.8, "latest_result": "PASS", "recent_runs": [], "latest_run_id": str(AUDIT_RUN_ID)}
    mock_gov.return_value = {"pass_count": 10, "fail_count": 0, "warn_count": 1, "total": 11, "checks": []}
    mock_scout.return_value = {"quality_score": 90}
    mock_contracts.return_value = [{"contract_type": "scope"}, {"contract_type": "testing"}]

    result = await aggregate_certificate_data(PROJECT_ID, USER_ID)
    assert result["project"]["name"] == "TestProject"
    assert result["build"]["status"] == "completed"
    assert result["builds_total"] == 2
    assert result["audit"]["pass_rate"] == 0.8
    assert result["governance"]["pass_count"] == 10
    assert result["scout"]["quality_score"] == 90
    assert result["contracts"]["count"] == 2


@pytest.mark.asyncio
@patch("app.services.certificate_aggregator.get_project_by_id", new_callable=AsyncMock)
async def test_aggregate_project_not_found(mock_project):
    mock_project.return_value = None
    with pytest.raises(ValueError, match="not found"):
        await aggregate_certificate_data(PROJECT_ID, USER_ID)


@pytest.mark.asyncio
@patch("app.services.certificate_aggregator.get_project_by_id", new_callable=AsyncMock)
async def test_aggregate_wrong_owner(mock_project):
    wrong_user = UUID("99999999-9999-9999-9999-999999999999")
    mock_project.return_value = MOCK_PROJECT
    with pytest.raises(ValueError, match="not found"):
        await aggregate_certificate_data(PROJECT_ID, wrong_user)


@pytest.mark.asyncio
@patch("app.services.certificate_aggregator.get_contracts_by_project", new_callable=AsyncMock)
@patch("app.services.certificate_aggregator.get_builds_for_project", new_callable=AsyncMock)
@patch("app.services.certificate_aggregator._aggregate_build", new_callable=AsyncMock)
@patch("app.services.certificate_aggregator.get_project_by_id", new_callable=AsyncMock)
async def test_aggregate_no_repo(mock_project, mock_build, mock_builds, mock_contracts):
    """Project without repo_id still works (no audit/scout)."""
    no_repo_project = {**MOCK_PROJECT, "repo_id": None}
    mock_project.return_value = no_repo_project
    mock_build.return_value = None
    mock_builds.return_value = []
    mock_contracts.return_value = []

    result = await aggregate_certificate_data(PROJECT_ID, USER_ID)
    assert result["audit"] == _empty_audit()
    assert result["governance"] is None  # _aggregate_governance returns None if no latest_run_id
    assert result["scout"] is None
