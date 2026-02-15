"""Tests for GitHub client -- list_commits."""

import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from app.clients.github_client import list_commits


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
