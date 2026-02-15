"""Tests for app/repos/build_repo.py -- build, build_logs, and build_costs CRUD operations."""

import uuid
from decimal import Decimal
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.repos import build_repo


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fake_pool():
    pool = AsyncMock()
    return pool


def _build_row(**overrides):
    """Create a fake build DB row."""
    defaults = {
        "id": uuid.uuid4(),
        "project_id": uuid.uuid4(),
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


def _log_row(**overrides):
    """Create a fake build_log DB row."""
    defaults = {
        "id": uuid.uuid4(),
        "build_id": uuid.uuid4(),
        "timestamp": datetime.now(timezone.utc),
        "source": "builder",
        "level": "info",
        "message": "test log message",
        "created_at": datetime.now(timezone.utc),
    }
    defaults.update(overrides)
    return defaults


# ---------------------------------------------------------------------------
# Tests: builds
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@patch("app.repos.build_repo.get_pool")
async def test_create_build(mock_get_pool):
    pool = _fake_pool()
    row = _build_row()
    pool.fetchrow.return_value = row
    mock_get_pool.return_value = pool

    result = await build_repo.create_build(row["project_id"])

    pool.fetchrow.assert_called_once()
    assert result["project_id"] == row["project_id"]
    assert result["status"] == "pending"


@pytest.mark.asyncio
@patch("app.repos.build_repo.get_pool")
async def test_get_build_by_id(mock_get_pool):
    pool = _fake_pool()
    row = _build_row()
    pool.fetchrow.return_value = row
    mock_get_pool.return_value = pool

    result = await build_repo.get_build_by_id(row["id"])

    assert result is not None
    assert result["id"] == row["id"]


@pytest.mark.asyncio
@patch("app.repos.build_repo.get_pool")
async def test_get_build_by_id_not_found(mock_get_pool):
    pool = _fake_pool()
    pool.fetchrow.return_value = None
    mock_get_pool.return_value = pool

    result = await build_repo.get_build_by_id(uuid.uuid4())

    assert result is None


@pytest.mark.asyncio
@patch("app.repos.build_repo.get_pool")
async def test_get_latest_build_for_project(mock_get_pool):
    pool = _fake_pool()
    row = _build_row(status="running")
    pool.fetchrow.return_value = row
    mock_get_pool.return_value = pool

    result = await build_repo.get_latest_build_for_project(row["project_id"])

    assert result is not None
    assert result["status"] == "running"


@pytest.mark.asyncio
@patch("app.repos.build_repo.get_pool")
async def test_update_build_status(mock_get_pool):
    pool = _fake_pool()
    mock_get_pool.return_value = pool

    await build_repo.update_build_status(
        uuid.uuid4(), "running", phase="Phase 1"
    )

    pool.execute.assert_called_once()
    call_args = pool.execute.call_args
    query = call_args[0][0]
    assert "status = $2" in query
    assert "phase = $3" in query


@pytest.mark.asyncio
@patch("app.repos.build_repo.get_pool")
async def test_increment_loop_count(mock_get_pool):
    pool = _fake_pool()
    pool.fetchrow.return_value = {"loop_count": 2}
    mock_get_pool.return_value = pool

    count = await build_repo.increment_loop_count(uuid.uuid4())

    assert count == 2


@pytest.mark.asyncio
@patch("app.repos.build_repo.get_pool")
async def test_cancel_build(mock_get_pool):
    pool = _fake_pool()
    pool.execute.return_value = "UPDATE 1"
    mock_get_pool.return_value = pool

    result = await build_repo.cancel_build(uuid.uuid4())

    assert result is True


@pytest.mark.asyncio
@patch("app.repos.build_repo.get_pool")
async def test_cancel_build_not_active(mock_get_pool):
    pool = _fake_pool()
    pool.execute.return_value = "UPDATE 0"
    mock_get_pool.return_value = pool

    result = await build_repo.cancel_build(uuid.uuid4())

    assert result is False


# ---------------------------------------------------------------------------
# Tests: build_logs
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@patch("app.repos.build_repo.get_pool")
async def test_append_build_log(mock_get_pool):
    pool = _fake_pool()
    row = _log_row()
    pool.fetchrow.return_value = row
    mock_get_pool.return_value = pool

    result = await build_repo.append_build_log(
        row["build_id"], "hello", source="system", level="warn"
    )

    assert result["message"] == row["message"]


@pytest.mark.asyncio
@patch("app.repos.build_repo.get_pool")
async def test_get_build_logs(mock_get_pool):
    pool = _fake_pool()
    pool.fetchrow.return_value = {"cnt": 42}
    pool.fetch.return_value = [_log_row(), _log_row()]
    mock_get_pool.return_value = pool

    logs, total = await build_repo.get_build_logs(uuid.uuid4(), limit=10, offset=0)

    assert total == 42
    assert len(logs) == 2


# ---------------------------------------------------------------------------
# Tests: build_costs
# ---------------------------------------------------------------------------


def _cost_row(**overrides):
    """Create a fake build_cost DB row."""
    defaults = {
        "id": uuid.uuid4(),
        "build_id": uuid.uuid4(),
        "phase": "Phase 0",
        "input_tokens": 1000,
        "output_tokens": 500,
        "model": "claude-opus-4-6",
        "estimated_cost_usd": Decimal("0.052500"),
        "created_at": datetime.now(timezone.utc),
    }
    defaults.update(overrides)
    return defaults


@pytest.mark.asyncio
@patch("app.repos.build_repo.get_pool")
async def test_record_build_cost(mock_get_pool):
    pool = _fake_pool()
    row = _cost_row()
    pool.fetchrow.return_value = row
    mock_get_pool.return_value = pool

    result = await build_repo.record_build_cost(
        row["build_id"], "Phase 0", 1000, 500, "claude-opus-4-6", Decimal("0.052500")
    )

    pool.fetchrow.assert_called_once()
    assert result["phase"] == "Phase 0"
    assert result["input_tokens"] == 1000


@pytest.mark.asyncio
@patch("app.repos.build_repo.get_pool")
async def test_get_build_costs(mock_get_pool):
    pool = _fake_pool()
    pool.fetch.return_value = [_cost_row(), _cost_row(phase="Phase 1")]
    mock_get_pool.return_value = pool

    costs = await build_repo.get_build_costs(uuid.uuid4())

    assert len(costs) == 2


@pytest.mark.asyncio
@patch("app.repos.build_repo.get_pool")
async def test_get_build_cost_summary(mock_get_pool):
    pool = _fake_pool()
    pool.fetchrow.return_value = {
        "total_input_tokens": 5000,
        "total_output_tokens": 2500,
        "total_cost_usd": Decimal("0.225000"),
        "phase_count": 3,
    }
    mock_get_pool.return_value = pool

    summary = await build_repo.get_build_cost_summary(uuid.uuid4())

    assert summary["total_input_tokens"] == 5000
    assert summary["phase_count"] == 3


# ---------------------------------------------------------------------------
# Tests: get_build_file_logs
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@patch("app.repos.build_repo.get_pool")
async def test_get_build_file_logs(mock_get_pool):
    """get_build_file_logs returns parsed file entries."""
    import json
    pool = _fake_pool()
    pool.fetch.return_value = [
        {
            "message": json.dumps({"path": "src/main.py", "size_bytes": 42, "language": "python"}),
            "created_at": datetime.now(timezone.utc),
        },
        {
            "message": json.dumps({"path": "package.json", "size_bytes": 200, "language": "json"}),
            "created_at": datetime.now(timezone.utc),
        },
    ]
    mock_get_pool.return_value = pool

    files = await build_repo.get_build_file_logs(uuid.uuid4())

    assert len(files) == 2
    assert files[0]["path"] == "src/main.py"
    assert files[0]["language"] == "python"
    assert files[1]["path"] == "package.json"


@pytest.mark.asyncio
@patch("app.repos.build_repo.get_pool")
async def test_get_build_file_logs_invalid_json(mock_get_pool):
    """get_build_file_logs skips invalid JSON messages."""
    pool = _fake_pool()
    pool.fetch.return_value = [
        {
            "message": "not valid json",
            "created_at": datetime.now(timezone.utc),
        },
    ]
    mock_get_pool.return_value = pool

    files = await build_repo.get_build_file_logs(uuid.uuid4())

    assert len(files) == 0


@pytest.mark.asyncio
@patch("app.repos.build_repo.get_pool")
async def test_get_build_file_logs_empty(mock_get_pool):
    """get_build_file_logs returns empty list when no file logs."""
    pool = _fake_pool()
    pool.fetch.return_value = []
    mock_get_pool.return_value = pool

    files = await build_repo.get_build_file_logs(uuid.uuid4())

    assert files == []


# ---------------------------------------------------------------------------
# Tests: create_build with target params
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@patch("app.repos.build_repo.get_pool")
async def test_create_build_with_target(mock_get_pool):
    """create_build passes target params to the query."""
    pool = _fake_pool()
    row = _build_row()
    pool.fetchrow.return_value = row
    mock_get_pool.return_value = pool

    result = await build_repo.create_build(
        row["project_id"],
        target_type="local_path",
        target_ref="/tmp/test",
        working_dir="/tmp/test",
    )

    pool.fetchrow.assert_called_once()
    assert result["project_id"] == row["project_id"]


# ---------------------------------------------------------------------------
# Tests: pause_build / resume_build (Phase 14)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@patch("app.repos.build_repo.get_pool")
async def test_pause_build_success(mock_get_pool):
    """pause_build sets status='paused' with reason and phase."""
    pool = _fake_pool()
    pool.execute.return_value = "UPDATE 1"
    mock_get_pool.return_value = pool

    result = await build_repo.pause_build(
        uuid.uuid4(), "3 audit failures", "Phase 2"
    )

    assert result is True
    pool.execute.assert_called_once()
    query = pool.execute.call_args[0][0]
    assert "status = 'paused'" in query
    assert "pause_reason" in query


@pytest.mark.asyncio
@patch("app.repos.build_repo.get_pool")
async def test_pause_build_not_running(mock_get_pool):
    """pause_build returns False if build is not running."""
    pool = _fake_pool()
    pool.execute.return_value = "UPDATE 0"
    mock_get_pool.return_value = pool

    result = await build_repo.pause_build(
        uuid.uuid4(), "reason", "Phase 1"
    )

    assert result is False


@pytest.mark.asyncio
@patch("app.repos.build_repo.get_pool")
async def test_resume_build_success(mock_get_pool):
    """resume_build clears pause fields and sets status='running'."""
    pool = _fake_pool()
    pool.execute.return_value = "UPDATE 1"
    mock_get_pool.return_value = pool

    result = await build_repo.resume_build(uuid.uuid4())

    assert result is True
    query = pool.execute.call_args[0][0]
    assert "status = 'running'" in query
    assert "paused_at = NULL" in query


@pytest.mark.asyncio
@patch("app.repos.build_repo.get_pool")
async def test_resume_build_not_paused(mock_get_pool):
    """resume_build returns False if build is not paused."""
    pool = _fake_pool()
    pool.execute.return_value = "UPDATE 0"
    mock_get_pool.return_value = pool

    result = await build_repo.resume_build(uuid.uuid4())

    assert result is False


@pytest.mark.asyncio
@patch("app.repos.build_repo.get_pool")
async def test_cancel_build_includes_paused(mock_get_pool):
    """cancel_build can cancel a paused build."""
    pool = _fake_pool()
    pool.execute.return_value = "UPDATE 1"
    mock_get_pool.return_value = pool

    result = await build_repo.cancel_build(uuid.uuid4())

    assert result is True
    query = pool.execute.call_args[0][0]
    assert "'paused'" in query
