"""Tests for Scout deep scan -- service, key-file selection, dossier generation."""

import json
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

import pytest

from app.services.scout_service import (
    _select_key_files,
    get_scout_dossier,
    start_deep_scan,
)

USER_ID = UUID("22222222-2222-2222-2222-222222222222")
REPO_ID = UUID("33333333-3333-3333-3333-333333333333")
RUN_ID = UUID("44444444-4444-4444-4444-444444444444")


# ---------------------------------------------------------------------------
# _select_key_files tests
# ---------------------------------------------------------------------------


def test_select_key_files_manifests_first():
    """Manifest files should be prioritised."""
    tree_paths = [
        "src/main.py",
        "requirements.txt",
        "README.md",
        "src/app.py",
        "pyproject.toml",
    ]
    tree_items = [
        {"path": p, "type": "blob", "size": 500}
        for p in tree_paths
    ]
    selected = _select_key_files(tree_paths, tree_items)
    # manifests should come first
    assert "requirements.txt" in selected
    assert "README.md" in selected
    assert "pyproject.toml" in selected
    # manifests before src files
    manifest_idx = selected.index("requirements.txt")
    src_idx = selected.index("src/main.py") if "src/main.py" in selected else len(selected)
    assert manifest_idx < src_idx


def test_select_key_files_skips_large():
    """Files > 50KB should be skipped."""
    tree_paths = ["big.py", "small.py"]
    tree_items = [
        {"path": "big.py", "type": "blob", "size": 60_000},
        {"path": "small.py", "type": "blob", "size": 500},
    ]
    selected = _select_key_files(tree_paths, tree_items)
    assert "big.py" not in selected
    assert "small.py" in selected


def test_select_key_files_entry_points():
    """Entry point files should be included early."""
    tree_paths = [
        "app/main.py",
        "app/services/foo.py",
        "app/repos/bar.py",
        "manage.py",
    ]
    tree_items = [
        {"path": p, "type": "blob", "size": 1000}
        for p in tree_paths
    ]
    selected = _select_key_files(tree_paths, tree_items)
    assert "app/main.py" in selected
    assert "manage.py" in selected


def test_select_key_files_router_files():
    """Router files should be included for architecture mapping."""
    tree_paths = [
        "app/api/routers/users.py",
        "app/api/routers/projects.py",
        "app/main.py",
    ]
    tree_items = [
        {"path": p, "type": "blob", "size": 2000}
        for p in tree_paths
    ]
    selected = _select_key_files(tree_paths, tree_items)
    assert "app/api/routers/users.py" in selected
    assert "app/api/routers/projects.py" in selected


def test_select_key_files_cap():
    """Output capped at _DEEP_SCAN_MAX_FILES."""
    from app.services.scout_service import _DEEP_SCAN_MAX_FILES

    tree_paths = [f"src/file_{i}.py" for i in range(50)]
    tree_items = [
        {"path": p, "type": "blob", "size": 500}
        for p in tree_paths
    ]
    selected = _select_key_files(tree_paths, tree_items)
    assert len(selected) <= _DEEP_SCAN_MAX_FILES


def test_select_key_files_migration_files():
    """Migration SQL files should be selected."""
    tree_paths = [
        "db/migrations/001_init.sql",
        "db/migrations/002_users.sql",
        "app/main.py",
    ]
    tree_items = [
        {"path": p, "type": "blob", "size": 1000}
        for p in tree_paths
    ]
    selected = _select_key_files(tree_paths, tree_items)
    assert "db/migrations/001_init.sql" in selected


# ---------------------------------------------------------------------------
# start_deep_scan
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_start_deep_scan_not_found():
    """Should raise ValueError when repo not found."""
    with patch("app.services.scout.deep_scan.get_repo_by_id", new_callable=AsyncMock, return_value=None):
        with pytest.raises(ValueError, match="Repo not found"):
            await start_deep_scan(USER_ID, REPO_ID)


@pytest.mark.asyncio
async def test_start_deep_scan_wrong_user():
    """Should raise ValueError when repo belongs to another user."""
    other_user = UUID("99999999-9999-9999-9999-999999999999")
    repo = {"id": REPO_ID, "user_id": other_user, "full_name": "org/repo"}
    with patch("app.services.scout.deep_scan.get_repo_by_id", new_callable=AsyncMock, return_value=repo):
        with pytest.raises(ValueError, match="Repo not found"):
            await start_deep_scan(USER_ID, REPO_ID)


@pytest.mark.asyncio
async def test_start_deep_scan_returns_running():
    """Should create a run and return status=running."""
    repo = {"id": REPO_ID, "user_id": USER_ID, "full_name": "org/repo", "default_branch": "main"}
    run = {"id": RUN_ID, "status": "running", "scan_type": "deep"}

    with (
        patch("app.services.scout.deep_scan.get_repo_by_id", new_callable=AsyncMock, return_value=repo),
        patch("app.services.scout.deep_scan.create_scout_run", new_callable=AsyncMock, return_value=run),
        patch("asyncio.create_task"),
    ):
        result = await start_deep_scan(USER_ID, REPO_ID, hypothesis="Check for secrets")
        assert result["status"] == "running"
        assert result["scan_type"] == "deep"
        assert result["repo_name"] == "org/repo"


# ---------------------------------------------------------------------------
# get_scout_dossier
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_scout_dossier_returns_data():
    """Should return structured dossier data from completed deep scan."""
    results = {
        "scan_type": "deep",
        "metadata": {"name": "test-repo"},
        "stack_profile": {"primary_language": "Python"},
        "architecture": {"structure_type": "layered"},
        "dossier": {"executive_summary": "A test project"},
        "checks": [],
        "warnings": [],
        "files_analysed": 10,
        "tree_size": 50,
        "head_sha": "abc123",
    }
    run = {
        "id": RUN_ID,
        "user_id": USER_ID,
        "scan_type": "deep",
        "status": "completed",
        "results": json.dumps(results),
    }

    with patch("app.services.scout.dossier_builder.get_scout_run", new_callable=AsyncMock, return_value=run):
        dossier = await get_scout_dossier(USER_ID, RUN_ID)
        assert dossier is not None
        assert dossier["metadata"]["name"] == "test-repo"
        assert dossier["files_analysed"] == 10
        assert dossier["dossier"]["executive_summary"] == "A test project"


@pytest.mark.asyncio
async def test_get_scout_dossier_not_found():
    """Should raise ValueError when run not found."""
    with patch("app.services.scout.dossier_builder.get_scout_run", new_callable=AsyncMock, return_value=None):
        with pytest.raises(ValueError, match="Scout run not found"):
            await get_scout_dossier(USER_ID, RUN_ID)


@pytest.mark.asyncio
async def test_get_scout_dossier_wrong_user():
    """Should raise ValueError when run belongs to different user."""
    other_user = UUID("99999999-9999-9999-9999-999999999999")
    run = {
        "id": RUN_ID,
        "user_id": other_user,
        "scan_type": "deep",
        "status": "completed",
        "results": "{}",
    }
    with patch("app.services.scout.dossier_builder.get_scout_run", new_callable=AsyncMock, return_value=run):
        with pytest.raises(ValueError, match="Scout run not found"):
            await get_scout_dossier(USER_ID, RUN_ID)


@pytest.mark.asyncio
async def test_get_scout_dossier_quick_scan_rejected():
    """Should raise ValueError when run is not a deep scan."""
    run = {
        "id": RUN_ID,
        "user_id": USER_ID,
        "scan_type": "quick",
        "status": "completed",
        "results": "{}",
    }
    with patch("app.services.scout.dossier_builder.get_scout_run", new_callable=AsyncMock, return_value=run):
        with pytest.raises(ValueError, match="deep scan"):
            await get_scout_dossier(USER_ID, RUN_ID)
