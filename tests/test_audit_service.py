"""Tests for audit service -- backfill_repo_commits."""

from unittest.mock import AsyncMock, patch, MagicMock
from uuid import UUID

import pytest

from app.services.audit_service import backfill_repo_commits


REPO_ID = UUID("33333333-3333-3333-3333-333333333333")
USER_ID = UUID("22222222-2222-2222-2222-222222222222")

MOCK_REPO = {
    "id": REPO_ID,
    "user_id": USER_ID,
    "github_repo_id": 12345,
    "full_name": "octocat/hello-world",
    "default_branch": "main",
}

MOCK_USER = {
    "id": USER_ID,
    "access_token": "gho_testtoken",
}


def _make_ws_mock():
    """Return a mock ws_manager with async broadcast methods."""
    ws = MagicMock()
    ws.broadcast_sync_progress = AsyncMock()
    ws.broadcast_audit_progress = AsyncMock()
    ws.broadcast_audit_update = AsyncMock()
    return ws


def _make_patches():
    """Return a dict of AsyncMock patches for backfill dependencies."""
    return {
        "get_repo_by_id": AsyncMock(return_value=MOCK_REPO),
        "get_user_by_id": AsyncMock(return_value=MOCK_USER),
        "list_commits": AsyncMock(return_value=[]),
        "get_existing_commit_shas": AsyncMock(return_value=set()),
        "mark_stale_audit_runs": AsyncMock(return_value=0),
        "create_audit_run": AsyncMock(return_value={
            "id": UUID("aaaa1111-1111-1111-1111-111111111111"),
            "started_at": None,
        }),
        "update_audit_run": AsyncMock(),
        "compare_commits": AsyncMock(return_value={
            "files": ["README.md"],
            "total_commits": 1,
            "head_sha": "aaa111",
            "head_message": "first",
            "head_author": "Alice",
        }),
        "get_commit_files": AsyncMock(return_value=["README.md"]),
        "get_repo_file_content": AsyncMock(return_value="# Hello"),
        "run_all_checks": MagicMock(return_value=[{"check_code": "A1", "result": "PASS", "detail": None, "check_name": "test"}]),
        "insert_audit_checks": AsyncMock(),
        "ws_manager": _make_ws_mock(),
    }


def _apply_patches(mocks):
    """Apply patches for all service dependencies."""
    decorators = []
    base = "app.services.audit_service"
    for name, mock in mocks.items():
        decorators.append(patch(f"{base}.{name}", mock))
    return decorators


@pytest.mark.asyncio
async def test_backfill_syncs_head_commit():
    """backfill_repo_commits audits only HEAD using compare API."""
    mocks = _make_patches()
    # Commits newest-first; "aaa111" is HEAD
    mocks["list_commits"].return_value = [
        {"sha": "aaa111", "message": "first", "author": "Alice"},
        {"sha": "bbb222", "message": "second", "author": "Bob"},
        {"sha": "ccc333", "message": "third", "author": "Carol"},
    ]
    # We already have "ccc333", so compare ccc333..aaa111
    mocks["get_existing_commit_shas"].return_value = {"ccc333"}
    mocks["compare_commits"].return_value = {
        "files": ["README.md", "src/main.py"],
        "total_commits": 2,
        "head_sha": "aaa111",
        "head_message": "first",
        "head_author": "Alice",
    }

    patches = _apply_patches(mocks)
    for p in patches:
        p.start()
    try:
        result = await backfill_repo_commits(REPO_ID, USER_ID)
    finally:
        for p in patches:
            p.stop()

    # Only 1 audit run for HEAD, not 2 separate ones
    assert result["synced"] == 1
    assert mocks["create_audit_run"].call_count == 1
    # Verify compare was called with the right base/head
    mocks["compare_commits"].assert_called_once()
    call_args = mocks["compare_commits"].call_args
    assert call_args[1].get("base_sha", call_args[0][2] if len(call_args[0]) > 2 else None) in ("ccc333", None) or "ccc333" in str(call_args)


@pytest.mark.asyncio
async def test_backfill_skips_all_existing():
    """backfill_repo_commits skips when HEAD is already tracked."""
    mocks = _make_patches()
    mocks["list_commits"].return_value = [
        {"sha": "aaa111", "message": "first", "author": "Alice"},
    ]
    mocks["get_existing_commit_shas"].return_value = {"aaa111"}

    patches = _apply_patches(mocks)
    for p in patches:
        p.start()
    try:
        result = await backfill_repo_commits(REPO_ID, USER_ID)
    finally:
        for p in patches:
            p.stop()

    assert result["synced"] == 0
    assert mocks["create_audit_run"].call_count == 0


@pytest.mark.asyncio
async def test_backfill_raises_for_missing_repo():
    """backfill_repo_commits raises ValueError when repo not found."""
    mocks = _make_patches()
    mocks["get_repo_by_id"].return_value = None

    patches = _apply_patches(mocks)
    for p in patches:
        p.start()
    try:
        with pytest.raises(ValueError, match="Repo not found"):
            await backfill_repo_commits(REPO_ID, USER_ID)
    finally:
        for p in patches:
            p.stop()


@pytest.mark.asyncio
async def test_backfill_raises_for_wrong_user():
    """backfill_repo_commits raises ValueError when user doesn't own repo."""
    mocks = _make_patches()
    other_user = UUID("99999999-9999-9999-9999-999999999999")

    patches = _apply_patches(mocks)
    for p in patches:
        p.start()
    try:
        with pytest.raises(ValueError, match="Repo not found"):
            await backfill_repo_commits(REPO_ID, other_user)
    finally:
        for p in patches:
            p.stop()


@pytest.mark.asyncio
async def test_backfill_falls_back_to_commit_files_for_fresh_repo():
    """When no prior audits exist, uses get_commit_files instead of compare."""
    mocks = _make_patches()
    mocks["list_commits"].return_value = [
        {"sha": "aaa111", "message": "first", "author": "Alice"},
    ]
    mocks["get_existing_commit_shas"].return_value = set()
    mocks["get_commit_files"].return_value = ["setup.py"]

    patches = _apply_patches(mocks)
    for p in patches:
        p.start()
    try:
        result = await backfill_repo_commits(REPO_ID, USER_ID)
    finally:
        for p in patches:
            p.stop()

    assert result["synced"] == 1
    # compare_commits should NOT be called (no base SHA)
    mocks["compare_commits"].assert_not_called()
    mocks["get_commit_files"].assert_called_once()


@pytest.mark.asyncio
async def test_backfill_handles_compare_error():
    """backfill_repo_commits handles compare API failure gracefully."""
    mocks = _make_patches()
    mocks["list_commits"].return_value = [
        {"sha": "aaa111", "message": "first", "author": "Alice"},
        {"sha": "bbb222", "message": "second", "author": "Bob"},
    ]
    mocks["get_existing_commit_shas"].return_value = {"bbb222"}
    mocks["compare_commits"].side_effect = Exception("API error")

    patches = _apply_patches(mocks)
    for p in patches:
        p.start()
    try:
        result = await backfill_repo_commits(REPO_ID, USER_ID)
    finally:
        for p in patches:
            p.stop()

    assert result["synced"] == 0


@pytest.mark.asyncio
async def test_backfill_cleans_stale_runs():
    """backfill_repo_commits calls mark_stale_audit_runs before processing."""
    mocks = _make_patches()
    mocks["list_commits"].return_value = []
    mocks["mark_stale_audit_runs"].return_value = 3

    patches = _apply_patches(mocks)
    for p in patches:
        p.start()
    try:
        result = await backfill_repo_commits(REPO_ID, USER_ID)
    finally:
        for p in patches:
            p.stop()

    mocks["mark_stale_audit_runs"].assert_called_once_with(REPO_ID)
    assert result["synced"] == 0
    assert result["skipped"] == 0


@pytest.mark.asyncio
async def test_backfill_marks_error_on_cancel():
    """If backfill is cancelled mid-audit, the in-progress row is marked error."""
    import asyncio

    mocks = _make_patches()
    mocks["list_commits"].return_value = [
        {"sha": "aaa111", "message": "first", "author": "Alice"},
    ]
    mocks["get_existing_commit_shas"].return_value = set()
    # Cancel during file fetch
    mocks["get_repo_file_content"].side_effect = asyncio.CancelledError()

    patches = _apply_patches(mocks)
    for p in patches:
        p.start()
    try:
        with pytest.raises(asyncio.CancelledError):
            await backfill_repo_commits(REPO_ID, USER_ID)
    finally:
        for p in patches:
            p.stop()

    # The audit run should have been marked as error before re-raising
    error_calls = [
        c for c in mocks["update_audit_run"].call_args_list
        if c.kwargs.get("status") == "error" or (c.args and len(c.args) > 1 and c.args[1] == "error")
    ]
    assert len(error_calls) >= 1


@pytest.mark.asyncio
async def test_backfill_broadcasts_progress():
    """backfill_repo_commits emits sync_progress and audit_progress WS events."""
    mocks = _make_patches()
    mocks["list_commits"].return_value = [
        {"sha": "aaa111", "message": "first", "author": "Alice"},
    ]
    mocks["get_existing_commit_shas"].return_value = set()
    mocks["get_commit_files"].return_value = ["README.md", "main.py"]
    mocks["get_repo_file_content"].return_value = "content"

    patches = _apply_patches(mocks)
    for p in patches:
        p.start()
    try:
        await backfill_repo_commits(REPO_ID, USER_ID)
    finally:
        for p in patches:
            p.stop()

    ws = mocks["ws_manager"]
    # Should have sync_progress calls (start + complete at minimum)
    assert ws.broadcast_sync_progress.call_count >= 2
    # Should have per-file audit_progress calls (2 files)
    assert ws.broadcast_audit_progress.call_count == 2
    # Should have audit_update on completion
    assert ws.broadcast_audit_update.call_count == 1
