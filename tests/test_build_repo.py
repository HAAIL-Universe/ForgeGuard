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
