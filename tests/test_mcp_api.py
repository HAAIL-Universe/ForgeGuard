"""Tests for Phase E: MCP context-broker API endpoints.

Covers:
- GET /mcp/context/{project_id}          — project manifest
- GET /mcp/context/{project_id}/{type}   — single contract
- GET /mcp/build/{build_id}/contracts    — pinned snapshot
- Auth / ownership checks
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from app.api.forge_auth import get_forge_user
from app.main import app
from tests.conftest import BUILD_ID, MOCK_USER, PROJECT_ID, USER_ID

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

MOCK_FORGE_USER = {
    "user_id": UUID(USER_ID),
    "github_login": "octocat",
    "scopes": ["read:contracts"],
}

MOCK_PROJECT = {
    "id": PROJECT_ID,
    "user_id": UUID(USER_ID),
    "name": "TestProject",
    "status": "ready",
}

MOCK_OTHER_PROJECT = {
    "id": UUID("99999999-9999-9999-9999-999999999999"),
    "user_id": UUID("eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee"),
    "name": "OtherUserProject",
    "status": "ready",
}

MOCK_CONTRACT_MANIFESTO = {
    "contract_type": "manifesto",
    "content": "# Project Manifesto\nThis is a test.",
    "version": 2,
    "updated_at": "2025-01-15T10:00:00+00:00",
}

MOCK_CONTRACT_STACK = {
    "contract_type": "stack",
    "content": "# Stack\nPython 3.12, FastAPI",
    "version": 1,
    "updated_at": "2025-01-14T09:00:00+00:00",
}

MOCK_BUILD = {
    "id": BUILD_ID,
    "project_id": PROJECT_ID,
    "contract_batch": 3,
    "started_at": "2025-01-15T12:00:00+00:00",
}

MOCK_BUILD_NO_BATCH = {
    "id": BUILD_ID,
    "project_id": PROJECT_ID,
    "contract_batch": None,
    "started_at": "2025-01-15T12:00:00+00:00",
}


@pytest.fixture
def client() -> TestClient:
    """TestClient with forge auth overridden."""
    app.dependency_overrides[get_forge_user] = lambda: MOCK_FORGE_USER
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture
def unauthed_client() -> TestClient:
    """TestClient with NO auth overrides — endpoints should fail."""
    app.dependency_overrides.clear()
    yield TestClient(app)
    app.dependency_overrides.clear()


# ═══════════════════════════════════════════════════════════════════════════
# GET /mcp/context/{project_id} — project manifest
# ═══════════════════════════════════════════════════════════════════════════


class TestGetProjectContext:
    ENDPOINT = f"/mcp/context/{PROJECT_ID}"

    def test_success(self, client: TestClient):
        with (
            patch("app.api.routers.mcp.get_project_by_id", new_callable=AsyncMock, return_value=MOCK_PROJECT),
            patch("app.api.routers.mcp.get_contracts_by_project", new_callable=AsyncMock, return_value=[MOCK_CONTRACT_MANIFESTO, MOCK_CONTRACT_STACK]),
            patch("app.api.routers.mcp.get_snapshot_batches", new_callable=AsyncMock, return_value=[{"batch": 5}]),
            patch("app.api.routers.mcp.get_builds_for_project", new_callable=AsyncMock, return_value=[{"id": "b1"}, {"id": "b2"}]),
        ):
            resp = client.get(self.ENDPOINT)
        assert resp.status_code == 200
        data = resp.json()
        assert data["project"]["id"] == str(PROJECT_ID)
        assert data["project"]["name"] == "TestProject"
        assert len(data["contracts"]) == 2
        assert data["contracts"][0]["contract_type"] == "manifesto"
        assert data["contracts"][0]["version"] == 2
        assert "size_chars" in data["contracts"][0]
        assert data["latest_batch"] == 5
        assert data["build_count"] == 2

    def test_no_contracts_or_builds(self, client: TestClient):
        with (
            patch("app.api.routers.mcp.get_project_by_id", new_callable=AsyncMock, return_value=MOCK_PROJECT),
            patch("app.api.routers.mcp.get_contracts_by_project", new_callable=AsyncMock, return_value=[]),
            patch("app.api.routers.mcp.get_snapshot_batches", new_callable=AsyncMock, return_value=[]),
            patch("app.api.routers.mcp.get_builds_for_project", new_callable=AsyncMock, return_value=[]),
        ):
            resp = client.get(self.ENDPOINT)
        assert resp.status_code == 200
        data = resp.json()
        assert data["contracts"] == []
        assert data["latest_batch"] is None
        assert data["build_count"] == 0

    def test_project_not_found(self, client: TestClient):
        with patch("app.api.routers.mcp.get_project_by_id", new_callable=AsyncMock, return_value=None):
            resp = client.get(self.ENDPOINT)
        assert resp.status_code == 404

    def test_wrong_owner(self, client: TestClient):
        """User does not own the project — 403."""
        with patch("app.api.routers.mcp.get_project_by_id", new_callable=AsyncMock, return_value=MOCK_OTHER_PROJECT):
            resp = client.get(self.ENDPOINT)
        assert resp.status_code == 403


# ═══════════════════════════════════════════════════════════════════════════
# GET /mcp/context/{project_id}/{contract_type} — single contract
# ═══════════════════════════════════════════════════════════════════════════


class TestGetProjectContract:
    ENDPOINT = f"/mcp/context/{PROJECT_ID}/manifesto"

    def test_success(self, client: TestClient):
        with (
            patch("app.api.routers.mcp.get_project_by_id", new_callable=AsyncMock, return_value=MOCK_PROJECT),
            patch("app.api.routers.mcp.get_contract_by_type", new_callable=AsyncMock, return_value=MOCK_CONTRACT_MANIFESTO),
        ):
            resp = client.get(self.ENDPOINT)
        assert resp.status_code == 200
        data = resp.json()
        assert data["contract_type"] == "manifesto"
        assert data["content"].startswith("# Project Manifesto")
        assert data["version"] == 2
        assert data["source"] == "project_db"
        assert data["project_id"] == str(PROJECT_ID)

    def test_contract_not_found(self, client: TestClient):
        with (
            patch("app.api.routers.mcp.get_project_by_id", new_callable=AsyncMock, return_value=MOCK_PROJECT),
            patch("app.api.routers.mcp.get_contract_by_type", new_callable=AsyncMock, return_value=None),
        ):
            resp = client.get(f"/mcp/context/{PROJECT_ID}/nonexistent")
        assert resp.status_code == 404
        assert "nonexistent" in resp.json()["detail"]

    def test_wrong_owner(self, client: TestClient):
        with patch("app.api.routers.mcp.get_project_by_id", new_callable=AsyncMock, return_value=MOCK_OTHER_PROJECT):
            resp = client.get(self.ENDPOINT)
        assert resp.status_code == 403


# ═══════════════════════════════════════════════════════════════════════════
# GET /mcp/build/{build_id}/contracts — pinned snapshot
# ═══════════════════════════════════════════════════════════════════════════


class TestGetBuildContracts:
    ENDPOINT = f"/mcp/build/{BUILD_ID}/contracts"

    def test_success(self, client: TestClient):
        snapshot = [
            {"contract_type": "manifesto", "content": "# M"},
            {"contract_type": "stack", "content": "# S"},
        ]
        with (
            patch("app.api.routers.mcp.get_build_by_id", new_callable=AsyncMock, return_value=MOCK_BUILD),
            patch("app.api.routers.mcp.get_project_by_id", new_callable=AsyncMock, return_value=MOCK_PROJECT),
            patch("app.api.routers.mcp.get_snapshot_contracts", new_callable=AsyncMock, return_value=snapshot),
        ):
            resp = client.get(self.ENDPOINT)
        assert resp.status_code == 200
        data = resp.json()
        assert data["build_id"] == str(BUILD_ID)
        assert data["batch"] == 3
        assert len(data["contracts"]) == 2
        assert data["contracts"][0]["contract_type"] == "manifesto"

    def test_no_batch_pinned(self, client: TestClient):
        with (
            patch("app.api.routers.mcp.get_build_by_id", new_callable=AsyncMock, return_value=MOCK_BUILD_NO_BATCH),
            patch("app.api.routers.mcp.get_project_by_id", new_callable=AsyncMock, return_value=MOCK_PROJECT),
        ):
            resp = client.get(self.ENDPOINT)
        assert resp.status_code == 200
        data = resp.json()
        assert data["batch"] is None
        assert data["contracts"] == []
        assert "No contract batch" in data["detail"]

    def test_build_not_found(self, client: TestClient):
        with patch("app.api.routers.mcp.get_build_by_id", new_callable=AsyncMock, return_value=None):
            resp = client.get(self.ENDPOINT)
        assert resp.status_code == 404

    def test_wrong_owner(self, client: TestClient):
        """User does not own the project the build belongs to."""
        with (
            patch("app.api.routers.mcp.get_build_by_id", new_callable=AsyncMock, return_value=MOCK_BUILD),
            patch("app.api.routers.mcp.get_project_by_id", new_callable=AsyncMock, return_value=MOCK_OTHER_PROJECT),
        ):
            resp = client.get(self.ENDPOINT)
        assert resp.status_code == 403


# ═══════════════════════════════════════════════════════════════════════════
# Auth guard — unauthed requests
# ═══════════════════════════════════════════════════════════════════════════


class TestAuthGuard:
    """Without auth override, endpoints should reject."""

    def test_context_requires_auth(self, unauthed_client: TestClient):
        resp = unauthed_client.get(f"/mcp/context/{PROJECT_ID}")
        assert resp.status_code in (401, 403)

    def test_contract_requires_auth(self, unauthed_client: TestClient):
        resp = unauthed_client.get(f"/mcp/context/{PROJECT_ID}/manifesto")
        assert resp.status_code in (401, 403)

    def test_build_requires_auth(self, unauthed_client: TestClient):
        resp = unauthed_client.get(f"/mcp/build/{BUILD_ID}/contracts")
        assert resp.status_code in (401, 403)
