"""Tests for GitHub client -- list_commits, create_github_repo."""

import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from app.clients.github_client import list_commits, create_github_repo


def _mock_response(data, status_code=200):
    """Create a mock httpx response."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = data
    resp.raise_for_status = MagicMock()
    return resp


@pytest.mark.asyncio
@patch("app.clients.github_client.httpx.AsyncClient")
async def test_list_commits_returns_commits(mock_client_cls):
    """list_commits parses commit data from GitHub API."""
    mock_client = AsyncMock()
    mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)

    mock_client.get.return_value = _mock_response([
        {
            "sha": "abc123",
            "commit": {
                "message": "Initial commit",
                "author": {"name": "Alice", "date": "2026-01-01T00:00:00Z"},
            },
        },
        {
            "sha": "def456",
            "commit": {
                "message": "Add feature",
                "author": {"name": "Bob", "date": "2026-01-02T00:00:00Z"},
            },
        },
    ])

    result = await list_commits("token", "owner/repo", branch="main")

    assert len(result) == 2
    assert result[0]["sha"] == "abc123"
    assert result[0]["message"] == "Initial commit"
    assert result[0]["author"] == "Alice"
    assert result[1]["sha"] == "def456"


@pytest.mark.asyncio
@patch("app.clients.github_client.httpx.AsyncClient")
async def test_list_commits_empty_repo(mock_client_cls):
    """list_commits returns empty list for repo with no commits."""
    mock_client = AsyncMock()
    mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)

    mock_client.get.return_value = _mock_response([])

    result = await list_commits("token", "owner/repo")
    assert result == []


@pytest.mark.asyncio
@patch("app.clients.github_client.httpx.AsyncClient")
async def test_list_commits_passes_since_param(mock_client_cls):
    """list_commits passes since parameter to API."""
    mock_client = AsyncMock()
    mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)

    mock_client.get.return_value = _mock_response([])

    await list_commits("token", "owner/repo", since="2026-01-01T00:00:00Z")

    call_kwargs = mock_client.get.call_args
    assert "since" in call_kwargs.kwargs.get("params", call_kwargs[1].get("params", {}))


# ---------- create_github_repo ----------


@pytest.mark.asyncio
@patch("app.clients.github_client.httpx.AsyncClient")
async def test_create_github_repo_returns_repo_info(mock_client_cls):
    """create_github_repo POSTs to /user/repos and returns parsed data."""
    mock_client = AsyncMock()
    mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)

    mock_client.post.return_value = _mock_response({
        "id": 98765,
        "full_name": "octocat/new-project",
        "default_branch": "main",
        "private": True,
    })

    result = await create_github_repo("token", "new-project", description="test repo", private=True)

    assert result["github_repo_id"] == 98765
    assert result["full_name"] == "octocat/new-project"
    assert result["default_branch"] == "main"
    assert result["private"] is True
    mock_client.post.assert_called_once()
