"""Shared test fixtures — reduces boilerplate across test modules.

Provides:
- ``set_test_config`` — autouse fixture that patches common settings
- ``MOCK_USER`` / ``USER_ID`` / ``REPO_ID`` / ``PROJECT_ID`` — reusable IDs
- ``auth_header`` — helper to generate JWT auth headers
- ``test_client`` — pre-built TestClient against the app
"""

from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from app.auth import create_token
from app.main import app

# ---------------------------------------------------------------------------
# Canonical test identifiers (used by most test modules)
# ---------------------------------------------------------------------------

USER_ID = "22222222-2222-2222-2222-222222222222"
REPO_ID = UUID("33333333-3333-3333-3333-333333333333")
PROJECT_ID = UUID("44444444-4444-4444-4444-444444444444")
BUILD_ID = UUID("55555555-5555-5555-5555-555555555555")
RUN_ID = UUID("44444444-4444-4444-4444-444444444444")

MOCK_USER: dict = {
    "id": UUID(USER_ID),
    "github_id": 99999,
    "github_login": "octocat",
    "avatar_url": "https://example.com/avatar.png",
    "access_token": "gho_testtoken123",
}

# ---------------------------------------------------------------------------
# Environment patching
# ---------------------------------------------------------------------------

_SETTINGS_PATCHES: dict[str, str] = {
    "app.config.settings.JWT_SECRET": "test-secret-key-for-unit-tests",
    "app.auth.settings.JWT_SECRET": "test-secret-key-for-unit-tests",
    "app.config.settings.GITHUB_CLIENT_ID": "test-client-id",
    "app.config.settings.GITHUB_CLIENT_SECRET": "test-client-secret",
    "app.config.settings.FRONTEND_URL": "http://localhost:5173",
    "app.config.settings.APP_URL": "http://localhost:8000",
    "app.config.settings.GITHUB_WEBHOOK_SECRET": "whsec_test",
    "app.config.settings.ANTHROPIC_API_KEY": "test-key",
    "app.config.settings.LLM_QUESTIONNAIRE_MODEL": "test-model",
}


@pytest.fixture(autouse=True)
def set_test_config(monkeypatch):
    """Patch common application settings for a safe test environment.

    This is ``autouse=True`` so every test automatically gets a
    deterministic, non-production configuration.
    """
    for target, value in _SETTINGS_PATCHES.items():
        monkeypatch.setattr(target, value)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def auth_header(user_id: str = USER_ID, login: str = "octocat") -> dict:
    """Return an ``Authorization`` header dict with a valid JWT."""
    token = create_token(user_id, login)
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def test_client() -> TestClient:
    """A fresh ``TestClient`` instance wrapping the FastAPI app."""
    return TestClient(app)
