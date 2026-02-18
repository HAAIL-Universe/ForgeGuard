"""Tests for scout service -- orchestration and check building."""

from unittest.mock import AsyncMock, patch
from uuid import UUID

import pytest

from app.services.scout_service import (
    _build_check_list,
    get_scout_detail,
    get_scout_history,
    start_scout_run,
)

USER_ID = UUID("22222222-2222-2222-2222-222222222222")
REPO_ID = UUID("33333333-3333-3333-3333-333333333333")
RUN_ID = UUID("44444444-4444-4444-4444-444444444444")


# ---------- _build_check_list ----------


def test_build_check_list_all_pass():
    """A full check list is built with 13 entries (A0-A9 + W1-W3)."""
    engine_results = [
        {"check_code": "A4", "check_name": "Boundary compliance", "result": "PASS", "detail": ""},
        {"check_code": "A9", "check_name": "Dependency gate", "result": "PASS", "detail": ""},
        {"check_code": "W1", "check_name": "Secrets scan", "result": "PASS", "detail": ""},
    ]
    changed_paths = ["app/main.py", "Forge/evidence/audit_ledger.md", "Forge/evidence/test_runs_latest.md"]
    files = {"app/main.py": "print('hello')"}

    result = _build_check_list(engine_results, changed_paths, files)
    assert len(result) == 13

    codes = [c["code"] for c in result]
    assert codes == ["A1", "A2", "A3", "A0", "A4", "A5", "A6", "A7", "A8", "A9", "W1", "W2", "W3"]


def test_build_check_list_engine_fail_propagated():
    """A4 FAIL from engine propagates into the check list."""
    engine_results = [
        {"check_code": "A4", "check_name": "Boundary compliance", "result": "FAIL", "detail": "violation found"},
        {"check_code": "A9", "check_name": "Dependency gate", "result": "PASS", "detail": ""},
        {"check_code": "W1", "check_name": "Secrets scan", "result": "WARN", "detail": "key found"},
    ]
    changed_paths = ["app/main.py"]
    files = {"app/main.py": "code"}

    result = _build_check_list(engine_results, changed_paths, files)
    a4 = next(c for c in result if c["code"] == "A4")
    assert a4["result"] == "FAIL"
    assert "violation" in a4["detail"]

    w1 = next(c for c in result if c["code"] == "W1")
    assert w1["result"] == "WARN"


def test_build_check_list_no_evidence_warns():
    """A3 warns when no evidence files are in the diff."""
    result = _build_check_list([], ["app/main.py"], {"app/main.py": ""})
    a3 = next(c for c in result if c["code"] == "A3")
    assert a3["result"] == "WARN"


def test_build_check_list_evidence_passes():
    """A3 passes when evidence files are present."""
    result = _build_check_list(
        [], ["Forge/evidence/audit_ledger.md"], {}
    )
    a3 = next(c for c in result if c["code"] == "A3")
    assert a3["result"] == "PASS"


# ---------- start_scout_run ----------


@pytest.mark.asyncio
@patch("app.services.scout.quick_scan.asyncio.create_task")
@patch("app.services.scout.quick_scan.create_scout_run", new_callable=AsyncMock)
@patch("app.services.scout.quick_scan.get_repo_by_id", new_callable=AsyncMock)
async def test_start_scout_run_success(mock_repo, mock_create, mock_task):
    """Starting a scout run creates a record and starts background task."""
    mock_repo.return_value = {
        "id": REPO_ID,
        "user_id": USER_ID,
        "full_name": "owner/repo",
        "default_branch": "main",
    }
    mock_create.return_value = {
        "id": RUN_ID,
        "status": "running",
    }

    result = await start_scout_run(USER_ID, REPO_ID, hypothesis="test hypothesis")
    assert result["status"] == "running"
    assert result["id"] == str(RUN_ID)
    mock_create.assert_called_once_with(REPO_ID, USER_ID, "test hypothesis")
    mock_task.assert_called_once()


@pytest.mark.asyncio
@patch("app.services.scout.quick_scan.get_repo_by_id", new_callable=AsyncMock)
async def test_start_scout_run_not_found(mock_repo):
    """Starting a scout run for unknown repo raises ValueError."""
    mock_repo.return_value = None

    with pytest.raises(ValueError, match="not found"):
        await start_scout_run(USER_ID, REPO_ID)


@pytest.mark.asyncio
@patch("app.services.scout.quick_scan.get_repo_by_id", new_callable=AsyncMock)
async def test_start_scout_run_wrong_user(mock_repo):
    """Starting a scout run for another user's repo raises ValueError."""
    mock_repo.return_value = {
        "id": REPO_ID,
        "user_id": UUID("99999999-9999-9999-9999-999999999999"),
        "full_name": "other/repo",
    }

    with pytest.raises(ValueError, match="not found"):
        await start_scout_run(USER_ID, REPO_ID)


# ---------- get_scout_history ----------


@pytest.mark.asyncio
@patch("app.services.scout.quick_scan.get_scout_runs_by_user", new_callable=AsyncMock)
async def test_get_history_all_repos(mock_runs):
    """History without repo_id queries all repos."""
    from datetime import datetime, timezone
    mock_runs.return_value = [{
        "id": RUN_ID,
        "repo_id": REPO_ID,
        "repo_name": "owner/repo",
        "status": "completed",
        "hypothesis": None,
        "checks_passed": 10,
        "checks_failed": 1,
        "checks_warned": 1,
        "started_at": datetime(2026, 2, 16, tzinfo=timezone.utc),
        "completed_at": datetime(2026, 2, 16, tzinfo=timezone.utc),
    }]

    result = await get_scout_history(USER_ID)
    assert len(result) == 1
    assert result[0]["id"] == str(RUN_ID)
    mock_runs.assert_called_once_with(USER_ID)


@pytest.mark.asyncio
@patch("app.services.scout.quick_scan.get_scout_runs_by_repo", new_callable=AsyncMock)
async def test_get_history_single_repo(mock_runs):
    """History with repo_id queries only that repo."""
    mock_runs.return_value = []

    result = await get_scout_history(USER_ID, repo_id=REPO_ID)
    assert result == []
    mock_runs.assert_called_once_with(REPO_ID, USER_ID)


# ---------- get_scout_detail ----------


@pytest.mark.asyncio
@patch("app.services.scout.quick_scan.get_scout_run", new_callable=AsyncMock)
async def test_get_detail_success(mock_run):
    """Detail returns full results when run exists."""
    from datetime import datetime, timezone
    mock_run.return_value = {
        "id": RUN_ID,
        "repo_id": REPO_ID,
        "user_id": USER_ID,
        "repo_name": "owner/repo",
        "status": "completed",
        "hypothesis": "test",
        "checks_passed": 10,
        "checks_failed": 1,
        "checks_warned": 1,
        "started_at": datetime(2026, 2, 16, tzinfo=timezone.utc),
        "completed_at": datetime(2026, 2, 16, tzinfo=timezone.utc),
        "results": {
            "checks": [{"code": "A1", "name": "Scope", "result": "PASS", "detail": "ok"}],
            "warnings": [],
            "files_analysed": 5,
            "hypothesis": "test",
        },
    }

    result = await get_scout_detail(USER_ID, RUN_ID)
    assert result["checks_passed"] == 10
    assert len(result["checks"]) == 1
    assert result["files_analysed"] == 5


@pytest.mark.asyncio
@patch("app.services.scout.quick_scan.get_scout_run", new_callable=AsyncMock)
async def test_get_detail_not_found(mock_run):
    """Detail raises ValueError when run doesn't exist."""
    mock_run.return_value = None

    with pytest.raises(ValueError, match="not found"):
        await get_scout_detail(USER_ID, RUN_ID)


@pytest.mark.asyncio
@patch("app.services.scout.quick_scan.get_scout_run", new_callable=AsyncMock)
async def test_get_detail_wrong_user(mock_run):
    """Detail raises ValueError when user doesn't own the run."""
    from datetime import datetime, timezone
    mock_run.return_value = {
        "id": RUN_ID,
        "repo_id": REPO_ID,
        "user_id": UUID("99999999-9999-9999-9999-999999999999"),
        "repo_name": "other/repo",
        "status": "completed",
        "checks_passed": 0,
        "checks_failed": 0,
        "checks_warned": 0,
        "started_at": datetime(2026, 2, 16, tzinfo=timezone.utc),
        "completed_at": None,
        "results": None,
    }

    with pytest.raises(ValueError, match="not found"):
        await get_scout_detail(USER_ID, RUN_ID)
