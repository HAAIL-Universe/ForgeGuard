"""Tests for Forge read-only contract endpoints and API key management."""

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_current_user
from app.api.forge_auth import get_forge_user
from app.main import app
from tests.conftest import MOCK_USER, USER_ID, auth_header

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

MOCK_KEY_ROW = {
    "id": UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
    "user_id": UUID(USER_ID),
    "prefix": "fg_abcdefgh",
    "label": "test-key",
    "scopes": ["read:contracts"],
    "created_at": "2026-01-01T00:00:00+00:00",
    "last_used": None,
}


@pytest.fixture
def client() -> TestClient:
    """TestClient with forge auth + JWT auth overridden to return MOCK_USER."""
    app.dependency_overrides[get_forge_user] = lambda: MOCK_USER
    app.dependency_overrides[get_current_user] = lambda: MOCK_USER
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture
def unauthed_client() -> TestClient:
    """TestClient with NO dependency overrides (auth will fail)."""
    app.dependency_overrides.clear()
    yield TestClient(app)
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# /forge/contracts — list
# ---------------------------------------------------------------------------


class TestForgeListContracts:
    def test_list_contracts_authenticated(self, client: TestClient):
        resp = client.get("/forge/contracts")
        assert resp.status_code == 200
        data = resp.json()
        assert "contracts" in data
        assert "total" in data
        assert data["total"] > 0
        item = data["contracts"][0]
        assert "name" in item
        assert "filename" in item
        assert "format" in item
        assert "available" in item

    def test_list_contracts_unauthenticated(self, unauthed_client: TestClient):
        resp = unauthed_client.get("/forge/contracts")
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# /forge/contracts/{name} — single contract
# ---------------------------------------------------------------------------


class TestForgeGetContract:
    def test_get_boundaries_contract(self, client: TestClient):
        resp = client.get("/forge/contracts/boundaries")
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "boundaries"
        assert data["format"] == "json"
        assert isinstance(data["content"], dict)
        assert "layers" in data["content"]

    def test_get_markdown_contract(self, client: TestClient):
        resp = client.get("/forge/contracts/blueprint")
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "blueprint"
        assert data["format"] == "markdown"
        assert isinstance(data["content"], str)

    def test_get_unknown_contract_404(self, client: TestClient):
        resp = client.get("/forge/contracts/nonexistent")
        assert resp.status_code == 404

    def test_get_physics_yaml(self, client: TestClient):
        resp = client.get("/forge/contracts/physics")
        assert resp.status_code == 200
        data = resp.json()
        assert data["format"] == "yaml"
        assert data["content"] is not None


# ---------------------------------------------------------------------------
# /forge/invariants
# ---------------------------------------------------------------------------


class TestForgeInvariants:
    def test_get_invariants(self, client: TestClient):
        resp = client.get("/forge/invariants")
        assert resp.status_code == 200
        data = resp.json()
        assert "invariants" in data
        assert "constraint_types" in data
        assert data["total"] == 7

        inv = data["invariants"][0]
        assert "name" in inv
        assert "constraint" in inv
        assert "default_value" in inv
        assert "description" in inv

        names = {i["name"] for i in data["invariants"]}
        assert "backend_test_count" in names
        assert "syntax_errors" in names


# ---------------------------------------------------------------------------
# /forge/boundaries (convenience alias)
# ---------------------------------------------------------------------------


class TestForgeBoundaries:
    def test_get_boundaries(self, client: TestClient):
        resp = client.get("/forge/boundaries")
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "boundaries"


# ---------------------------------------------------------------------------
# /forge/summary
# ---------------------------------------------------------------------------


class TestForgeSummary:
    def test_get_summary(self, client: TestClient):
        resp = client.get("/forge/summary")
        assert resp.status_code == 200
        data = resp.json()
        assert data["framework"] == "ForgeGuard"
        assert "contracts" in data
        assert "architectural_layers" in data
        assert "invariants" in data
        assert "endpoints" in data
        assert len(data["contracts"]) > 0
        assert len(data["invariants"]) == 7


# ---------------------------------------------------------------------------
# API key management — /auth/forge-keys
# ---------------------------------------------------------------------------


class TestForgeApiKeys:
    def test_create_key(self, client: TestClient):
        with patch("app.api.routers.auth.create_api_key", new_callable=AsyncMock) as mock_create:
            mock_create.return_value = ("fg_test_raw_key_abc123", MOCK_KEY_ROW)
            resp = client.post(
                "/auth/forge-keys",
                json={"label": "test-key"},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["key"].startswith("fg_")
        assert "id" in data
        assert data["label"] == "test-key"
        assert "message" in data

    def test_list_keys(self, client: TestClient):
        with patch("app.api.routers.auth.list_api_keys", new_callable=AsyncMock) as mock_list:
            mock_list.return_value = [MOCK_KEY_ROW]
            resp = client.get("/auth/forge-keys")
        assert resp.status_code == 200
        data = resp.json()
        assert "keys" in data
        assert len(data["keys"]) == 1
        assert data["keys"][0]["prefix"] == "fg_abcdefgh"

    def test_revoke_key(self, client: TestClient):
        key_id = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
        with patch("app.api.routers.auth.revoke_api_key", new_callable=AsyncMock) as mock_revoke:
            mock_revoke.return_value = True
            resp = client.delete(f"/auth/forge-keys/{key_id}")
        assert resp.status_code == 200
        assert resp.json()["revoked"] is True

    def test_revoke_nonexistent_key(self, client: TestClient):
        key_id = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
        with patch("app.api.routers.auth.revoke_api_key", new_callable=AsyncMock) as mock_revoke:
            mock_revoke.return_value = False
            resp = client.delete(f"/auth/forge-keys/{key_id}")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# API key auth flow — forge_auth dependency
# ---------------------------------------------------------------------------


class TestForgeKeyAuth:
    def test_api_key_grants_access(self):
        """A valid fg_ key should authenticate to /forge endpoints."""
        app.dependency_overrides.clear()
        mock_result = {
            **MOCK_USER,
            "key_id": UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
            "scopes": ["read:contracts"],
            "label": "test",
        }
        with patch("app.api.forge_auth.verify_api_key", new_callable=AsyncMock) as mock_verify:
            mock_verify.return_value = mock_result
            c = TestClient(app)
            resp = c.get(
                "/forge/contracts",
                headers={"Authorization": "Bearer fg_test_key_12345"},
            )
        assert resp.status_code == 200
        mock_verify.assert_called_once_with("fg_test_key_12345")
        app.dependency_overrides.clear()

    def test_invalid_api_key_rejected(self):
        app.dependency_overrides.clear()
        with patch("app.api.forge_auth.verify_api_key", new_callable=AsyncMock) as mock_verify:
            mock_verify.return_value = None
            c = TestClient(app)
            resp = c.get(
                "/forge/contracts",
                headers={"Authorization": "Bearer fg_invalid_key"},
            )
        assert resp.status_code == 401
        app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Forge key repo unit tests
# ---------------------------------------------------------------------------


class TestForgeKeyRepo:
    def test_hash_is_deterministic(self):
        from app.repos.forge_key_repo import _hash_key
        h1 = _hash_key("fg_test123")
        h2 = _hash_key("fg_test123")
        assert h1 == h2

    def test_different_keys_different_hashes(self):
        from app.repos.forge_key_repo import _hash_key
        h1 = _hash_key("fg_key_one")
        h2 = _hash_key("fg_key_two")
        assert h1 != h2
