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


def _make_patches():
    """Return a dict of AsyncMock patches for backfill dependencies."""
    return {
        "get_repo_by_id": AsyncMock(return_value=MOCK_REPO),
        "get_user_by_id": AsyncMock(return_value=MOCK_USER),
        "list_commits": AsyncMock(return_value=[]),
        "get_existing_commit_shas": AsyncMock(return_value=set()),
        "create_audit_run": AsyncMock(return_value={"id": UUID("aaaa1111-1111-1111-1111-111111111111")}),
        "update_audit_run": AsyncMock(),
        "get_commit_files": AsyncMock(return_value=["README.md"]),
        "get_repo_file_content": AsyncMock(return_value="# Hello"),
        "run_all_checks": MagicMock(return_value=[{"check_code": "A1", "result": "PASS", "detail": None, "check_name": "test"}]),
        "insert_audit_checks": AsyncMock(),
    }


def _apply_patches(mocks):
    """Apply patches for all service dependencies."""
    decorators = []
    base = "app.services.audit_service"
    for name, mock in mocks.items():
        decorators.append(patch(f"{base}.{name}", mock))
    return decorators


@pytest.mark.asyncio
async def test_backfill_syncs_new_commits():
    """backfill_repo_commits creates audit runs for commits not yet tracked."""
    mocks = _make_patches()
    mocks["list_commits"].return_value = [
        {"sha": "aaa111", "message": "first", "author": "Alice"},
        {"sha": "bbb222", "message": "second", "author": "Bob"},
        {"sha": "ccc333", "message": "third", "author": "Carol"},
    ]
    mocks["get_existing_commit_shas"].return_value = {"bbb222"}

    patches = _apply_patches(mocks)
    for p in patches:
        p.start()
    try:
        result = await backfill_repo_commits(REPO_ID, USER_ID)
    finally:
        for p in patches:
            p.stop()

    assert result["synced"] == 2
    assert result["skipped"] == 1
    assert mocks["create_audit_run"].call_count == 2


@pytest.mark.asyncio
async def test_backfill_skips_all_existing():
    """backfill_repo_commits skips commits already tracked."""
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
    assert result["skipped"] == 1
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
async def test_backfill_handles_commit_error_gracefully():
    """backfill_repo_commits continues if a single commit fails."""
    mocks = _make_patches()
    mocks["list_commits"].return_value = [
        {"sha": "aaa111", "message": "first", "author": "Alice"},
        {"sha": "bbb222", "message": "second", "author": "Bob"},
    ]
    mocks["get_existing_commit_shas"].return_value = set()
    # First commit succeeds, second raises
    mocks["get_commit_files"].side_effect = [["README.md"], Exception("API error")]

    patches = _apply_patches(mocks)
    for p in patches:
        p.start()
    try:
        result = await backfill_repo_commits(REPO_ID, USER_ID)
    finally:
        for p in patches:
            p.stop()

    # Both count as "synced" (processed) even if one errored
    assert result["synced"] == 2
    assert result["skipped"] == 0
