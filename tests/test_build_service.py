"""Tests for app/services/build_service.py -- build orchestration layer."""

import asyncio
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services import build_service


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_USER_ID = uuid.uuid4()
_PROJECT_ID = uuid.uuid4()
_BUILD_ID = uuid.uuid4()


def _project(**overrides):
    defaults = {
        "id": _PROJECT_ID,
        "user_id": _USER_ID,
        "name": "Test Project",
        "status": "contracts_ready",
    }
    defaults.update(overrides)
    return defaults


def _contracts():
    return [
        {"contract_type": "blueprint", "content": "# Blueprint\nTest"},
        {"contract_type": "manifesto", "content": "# Manifesto\nTest"},
    ]


def _build(**overrides):
    defaults = {
        "id": _BUILD_ID,
        "project_id": _PROJECT_ID,
        "phase": "Phase 0",
        "status": "pending",
        "started_at": None,
        "completed_at": None,
        "loop_count": 0,
        "error_detail": None,
        "created_at": datetime.now(timezone.utc),
    }
    defaults.update(overrides)
    return defaults


# ---------------------------------------------------------------------------
# Tests: start_build
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@patch("app.services.build_service.asyncio.create_task")
@patch("app.services.build_service.project_repo")
@patch("app.services.build_service.build_repo")
async def test_start_build_success(mock_build_repo, mock_project_repo, mock_create_task):
    """start_build creates a build record and spawns a background task."""
    mock_project_repo.get_project_by_id = AsyncMock(return_value=_project())
    mock_project_repo.get_contracts_by_project = AsyncMock(return_value=_contracts())
    mock_project_repo.update_project_status = AsyncMock()
    mock_build_repo.get_latest_build_for_project = AsyncMock(return_value=None)
    mock_build_repo.create_build = AsyncMock(return_value=_build())
    mock_create_task.return_value = MagicMock()

    result = await build_service.start_build(_PROJECT_ID, _USER_ID)

    assert result["status"] == "pending"
    mock_build_repo.create_build.assert_called_once_with(_PROJECT_ID)
    mock_project_repo.update_project_status.assert_called_once_with(
        _PROJECT_ID, "building"
    )
    mock_create_task.assert_called_once()


@pytest.mark.asyncio
@patch("app.services.build_service.project_repo")
@patch("app.services.build_service.build_repo")
async def test_start_build_project_not_found(mock_build_repo, mock_project_repo):
    """start_build raises ValueError if project not found."""
    mock_project_repo.get_project_by_id = AsyncMock(return_value=None)

    with pytest.raises(ValueError, match="not found"):
        await build_service.start_build(_PROJECT_ID, _USER_ID)


@pytest.mark.asyncio
@patch("app.services.build_service.project_repo")
@patch("app.services.build_service.build_repo")
async def test_start_build_wrong_owner(mock_build_repo, mock_project_repo):
    """start_build raises ValueError if user doesn't own the project."""
    mock_project_repo.get_project_by_id = AsyncMock(
        return_value=_project(user_id=uuid.uuid4())
    )

    with pytest.raises(ValueError, match="not found"):
        await build_service.start_build(_PROJECT_ID, _USER_ID)


@pytest.mark.asyncio
@patch("app.services.build_service.project_repo")
@patch("app.services.build_service.build_repo")
async def test_start_build_no_contracts(mock_build_repo, mock_project_repo):
    """start_build raises ValueError if no contracts exist."""
    mock_project_repo.get_project_by_id = AsyncMock(return_value=_project())
    mock_project_repo.get_contracts_by_project = AsyncMock(return_value=[])

    with pytest.raises(ValueError, match="No contracts"):
        await build_service.start_build(_PROJECT_ID, _USER_ID)


@pytest.mark.asyncio
@patch("app.services.build_service.project_repo")
@patch("app.services.build_service.build_repo")
async def test_start_build_already_running(mock_build_repo, mock_project_repo):
    """start_build raises ValueError if a build is already in progress."""
    mock_project_repo.get_project_by_id = AsyncMock(return_value=_project())
    mock_project_repo.get_contracts_by_project = AsyncMock(return_value=_contracts())
    mock_build_repo.get_latest_build_for_project = AsyncMock(
        return_value=_build(status="running")
    )

    with pytest.raises(ValueError, match="already in progress"):
        await build_service.start_build(_PROJECT_ID, _USER_ID)


# ---------------------------------------------------------------------------
# Tests: cancel_build
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@patch("app.services.build_service.manager")
@patch("app.services.build_service.project_repo")
@patch("app.services.build_service.build_repo")
async def test_cancel_build_success(mock_build_repo, mock_project_repo, mock_manager):
    """cancel_build cancels an active build."""
    mock_project_repo.get_project_by_id = AsyncMock(return_value=_project())
    mock_build_repo.get_latest_build_for_project = AsyncMock(
        return_value=_build(status="running")
    )
    mock_build_repo.cancel_build = AsyncMock(return_value=True)
    mock_build_repo.append_build_log = AsyncMock()
    mock_build_repo.get_build_by_id = AsyncMock(
        return_value=_build(status="cancelled")
    )
    mock_manager.send_to_user = AsyncMock()

    result = await build_service.cancel_build(_PROJECT_ID, _USER_ID)

    assert result["status"] == "cancelled"
    mock_build_repo.cancel_build.assert_called_once()


@pytest.mark.asyncio
@patch("app.services.build_service.project_repo")
@patch("app.services.build_service.build_repo")
async def test_cancel_build_no_active(mock_build_repo, mock_project_repo):
    """cancel_build raises ValueError if no active build."""
    mock_project_repo.get_project_by_id = AsyncMock(return_value=_project())
    mock_build_repo.get_latest_build_for_project = AsyncMock(
        return_value=_build(status="completed")
    )

    with pytest.raises(ValueError, match="No active build"):
        await build_service.cancel_build(_PROJECT_ID, _USER_ID)


# ---------------------------------------------------------------------------
# Tests: get_build_status
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@patch("app.services.build_service.project_repo")
@patch("app.services.build_service.build_repo")
async def test_get_build_status(mock_build_repo, mock_project_repo):
    """get_build_status returns the latest build."""
    mock_project_repo.get_project_by_id = AsyncMock(return_value=_project())
    mock_build_repo.get_latest_build_for_project = AsyncMock(
        return_value=_build(status="running", phase="Phase 2")
    )

    result = await build_service.get_build_status(_PROJECT_ID, _USER_ID)

    assert result["status"] == "running"
    assert result["phase"] == "Phase 2"


@pytest.mark.asyncio
@patch("app.services.build_service.project_repo")
@patch("app.services.build_service.build_repo")
async def test_get_build_status_no_builds(mock_build_repo, mock_project_repo):
    """get_build_status raises ValueError if no builds exist."""
    mock_project_repo.get_project_by_id = AsyncMock(return_value=_project())
    mock_build_repo.get_latest_build_for_project = AsyncMock(return_value=None)

    with pytest.raises(ValueError, match="No builds"):
        await build_service.get_build_status(_PROJECT_ID, _USER_ID)


# ---------------------------------------------------------------------------
# Tests: get_build_logs
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@patch("app.services.build_service.project_repo")
@patch("app.services.build_service.build_repo")
async def test_get_build_logs(mock_build_repo, mock_project_repo):
    """get_build_logs returns paginated logs."""
    mock_project_repo.get_project_by_id = AsyncMock(return_value=_project())
    mock_build_repo.get_latest_build_for_project = AsyncMock(
        return_value=_build(status="running")
    )
    mock_build_repo.get_build_logs = AsyncMock(
        return_value=([{"message": "log1"}, {"message": "log2"}], 10)
    )

    logs, total = await build_service.get_build_logs(
        _PROJECT_ID, _USER_ID, limit=50, offset=0
    )

    assert total == 10
    assert len(logs) == 2


# ---------------------------------------------------------------------------
# Tests: _build_directive
# ---------------------------------------------------------------------------


def test_build_directive_format():
    """_build_directive assembles contracts in canonical order."""
    contracts = [
        {"contract_type": "manifesto", "content": "# Manifesto"},
        {"contract_type": "blueprint", "content": "# Blueprint"},
    ]

    result = build_service._build_directive(contracts)

    assert "# Project Contracts" in result
    # Blueprint should come before manifesto in canonical order
    bp_pos = result.index("blueprint")
    mf_pos = result.index("manifesto")
    assert bp_pos < mf_pos


# ---------------------------------------------------------------------------
# Tests: _run_inline_audit
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@patch("app.services.build_service.build_repo")
async def test_run_inline_audit(mock_build_repo):
    """_run_inline_audit logs the invocation and returns PASS."""
    mock_build_repo.append_build_log = AsyncMock()

    result = await build_service._run_inline_audit(_BUILD_ID, "Phase 1")

    assert result == "PASS"
    mock_build_repo.append_build_log.assert_called()


# ---------------------------------------------------------------------------
# Tests: _fail_build
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@patch("app.services.build_service.manager")
@patch("app.services.build_service.build_repo")
async def test_fail_build(mock_build_repo, mock_manager):
    """_fail_build marks the build as failed and broadcasts."""
    mock_build_repo.update_build_status = AsyncMock()
    mock_build_repo.append_build_log = AsyncMock()
    mock_manager.send_to_user = AsyncMock()

    await build_service._fail_build(_BUILD_ID, _USER_ID, "something broke")

    mock_build_repo.update_build_status.assert_called_once()
    call_kwargs = mock_build_repo.update_build_status.call_args
    assert call_kwargs[0][1] == "failed"
    mock_manager.send_to_user.assert_called_once()
