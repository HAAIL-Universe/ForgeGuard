"""Tests for repo health-check service functions."""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

import pytest

REPO_ID = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
USER_ID = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")

MOCK_USER = {
    "id": USER_ID,
    "github_login": "octocat",
    "access_token": "gho_test",
}

MOCK_REPO = {
    "id": REPO_ID,
    "full_name": "octocat/hello-world",
    "default_branch": "main",
    "webhook_active": True,
}


# ---------------------------------------------------------------------------
# _check_single_repo — status detection
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@patch("app.services.repo_service.update_repo_health", new_callable=AsyncMock)
@patch("app.services.repo_service.get_repo_health", new_callable=AsyncMock)
async def test_check_single_repo_marks_deleted_on_404(mock_gh_health, mock_db_health):
    """404 from GitHub → repo_status = 'deleted', health updated."""
    mock_gh_health.return_value = (404, None)

    from app.services.repo_service import _check_single_repo
    await _check_single_repo(MOCK_USER, MOCK_REPO)

    mock_db_health.assert_awaited_once()
    call_kwargs = mock_db_health.call_args[0][1]
    assert call_kwargs["repo_status"] == "deleted"


@pytest.mark.asyncio
@patch("app.services.repo_service.update_repo_health", new_callable=AsyncMock)
@patch("app.services.repo_service.get_repo_health", new_callable=AsyncMock)
async def test_check_single_repo_marks_inaccessible_on_403(mock_gh_health, mock_db_health):
    """403 from GitHub → repo_status = 'inaccessible'."""
    mock_gh_health.return_value = (403, None)

    from app.services.repo_service import _check_single_repo
    await _check_single_repo(MOCK_USER, MOCK_REPO)

    call_kwargs = mock_db_health.call_args[0][1]
    assert call_kwargs["repo_status"] == "inaccessible"


@pytest.mark.asyncio
@patch("app.services.repo_service.update_repo_health", new_callable=AsyncMock)
@patch("app.services.repo_service.list_commits", new_callable=AsyncMock)
@patch("app.services.repo_service.get_repo_health", new_callable=AsyncMock)
async def test_check_single_repo_marks_archived(mock_gh_health, mock_commits, mock_db_health):
    """archived=True in GitHub data → repo_status = 'archived'."""
    mock_gh_health.return_value = (200, {
        "archived": True,
        "full_name": "octocat/hello-world",
    })
    mock_commits.return_value = []

    from app.services.repo_service import _check_single_repo
    await _check_single_repo(MOCK_USER, MOCK_REPO)

    call_kwargs = mock_db_health.call_args[0][1]
    assert call_kwargs["repo_status"] == "archived"


@pytest.mark.asyncio
@patch("app.services.repo_service.update_repo_full_name", new_callable=AsyncMock)
@patch("app.services.repo_service.update_repo_health", new_callable=AsyncMock)
@patch("app.services.repo_service.list_commits", new_callable=AsyncMock)
@patch("app.services.repo_service.get_repo_health", new_callable=AsyncMock)
async def test_check_single_repo_updates_full_name_on_rename(
    mock_gh_health, mock_commits, mock_db_health, mock_rename
):
    """Different full_name from GitHub → update_repo_full_name called."""
    mock_gh_health.return_value = (200, {
        "archived": False,
        "full_name": "octocat/new-name",
    })
    mock_commits.return_value = []

    from app.services.repo_service import _check_single_repo
    await _check_single_repo(MOCK_USER, MOCK_REPO)

    mock_rename.assert_awaited_once_with(REPO_ID, "octocat/new-name")
    call_kwargs = mock_db_health.call_args[0][1]
    assert call_kwargs["repo_status"] == "connected"


@pytest.mark.asyncio
@patch("app.services.repo_service.update_repo_health", new_callable=AsyncMock)
@patch("app.services.repo_service.list_commits", new_callable=AsyncMock)
@patch("app.services.repo_service.get_repo_health", new_callable=AsyncMock)
async def test_check_single_repo_stores_latest_commit(mock_gh_health, mock_commits, mock_db_health):
    """Latest commit SHA/message/author stored in health update."""
    mock_gh_health.return_value = (200, {
        "archived": False,
        "full_name": "octocat/hello-world",
    })
    mock_commits.return_value = [{
        "sha": "abc123def456abc123def456abc123def456abc1",
        "message": "Fix null pointer\n\nLonger description",
        "author": "Octocat",
        "date": "2025-01-15T10:00:00Z",
    }]

    from app.services.repo_service import _check_single_repo
    await _check_single_repo(MOCK_USER, MOCK_REPO)

    call_kwargs = mock_db_health.call_args[0][1]
    assert call_kwargs["latest_commit_sha"] == "abc123def456abc123def456abc123def456abc1"
    assert call_kwargs["latest_commit_message"] == "Fix null pointer"
    assert call_kwargs["latest_commit_author"] == "Octocat"
    assert call_kwargs["repo_status"] == "connected"


# ---------------------------------------------------------------------------
# run_repo_health_check — orchestration
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@patch("app.services.repo_service.manager")
@patch("app.services.repo_service._check_single_repo", new_callable=AsyncMock)
@patch("app.services.repo_service.get_repos_by_user", new_callable=AsyncMock)
@patch("app.services.repo_service.get_user_by_id", new_callable=AsyncMock)
async def test_run_repo_health_check_runs_all_repos_and_emits_ws(
    mock_get_user, mock_get_repos, mock_check, mock_manager
):
    """All repos are checked concurrently and WS event is emitted."""
    mock_get_user.return_value = MOCK_USER
    mock_get_repos.return_value = [
        {**MOCK_REPO, "id": UUID("11111111-1111-1111-1111-111111111111")},
        {**MOCK_REPO, "id": UUID("22222222-2222-2222-2222-222222222222")},
    ]
    mock_manager.send_to_user = AsyncMock()

    from app.services.repo_service import run_repo_health_check
    await run_repo_health_check(USER_ID)

    assert mock_check.await_count == 2
    mock_manager.send_to_user.assert_awaited_once()
    call_args = mock_manager.send_to_user.call_args
    assert call_args[0][1]["type"] == "repos_health_updated"
    assert call_args[0][1]["payload"]["checked"] == 2
