"""Tests for repo service health score computation."""

import pytest
from unittest.mock import AsyncMock, patch


@pytest.mark.asyncio
@patch("app.services.repo_service.get_repos_with_health")
async def test_health_green_all_pass(mock_get):
    """All 10 audits passed -> green health, 1.0 rate."""
    mock_get.return_value = [{
        "id": "repo-1",
        "full_name": "org/repo",
        "default_branch": "main",
        "webhook_active": True,
        "total_count": 10,
        "pass_count": 10,
        "last_audit_at": None,
    }]
    from app.services.repo_service import list_connected_repos
    result = await list_connected_repos("user-1")
    assert result[0]["health_score"] == "green"
    assert result[0]["recent_pass_rate"] == 1.0


@pytest.mark.asyncio
@patch("app.services.repo_service.get_repos_with_health")
async def test_health_red_low_pass(mock_get):
    """Less than half pass -> red health."""
    mock_get.return_value = [{
        "id": "repo-1",
        "full_name": "org/repo",
        "default_branch": "main",
        "webhook_active": True,
        "total_count": 10,
        "pass_count": 3,
        "last_audit_at": None,
    }]
    from app.services.repo_service import list_connected_repos
    result = await list_connected_repos("user-1")
    assert result[0]["health_score"] == "red"
    assert result[0]["recent_pass_rate"] == 0.3


@pytest.mark.asyncio
@patch("app.services.repo_service.get_repos_with_health")
async def test_health_yellow_mixed(mock_get):
    """50-99% pass rate -> yellow health."""
    mock_get.return_value = [{
        "id": "repo-1",
        "full_name": "org/repo",
        "default_branch": "main",
        "webhook_active": True,
        "total_count": 10,
        "pass_count": 7,
        "last_audit_at": None,
    }]
    from app.services.repo_service import list_connected_repos
    result = await list_connected_repos("user-1")
    assert result[0]["health_score"] == "yellow"


@pytest.mark.asyncio
@patch("app.services.repo_service.get_repos_with_health")
async def test_health_pending_no_audits(mock_get):
    """No audit runs -> pending health, null rate."""
    mock_get.return_value = [{
        "id": "repo-1",
        "full_name": "org/repo",
        "default_branch": "main",
        "webhook_active": True,
        "total_count": 0,
        "pass_count": 0,
        "last_audit_at": None,
    }]
    from app.services.repo_service import list_connected_repos
    result = await list_connected_repos("user-1")
    assert result[0]["health_score"] == "pending"
    assert result[0]["recent_pass_rate"] is None
