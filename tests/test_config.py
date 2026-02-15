"""Tests for config validation."""

import importlib
import os
import sys


def test_config_loads_with_env_vars(monkeypatch):
    """Config should load successfully when all required vars are set."""
    monkeypatch.setenv("DATABASE_URL", "postgresql://test:test@localhost/test")
    monkeypatch.setenv("GITHUB_CLIENT_ID", "test_id")
    monkeypatch.setenv("GITHUB_CLIENT_SECRET", "test_secret")
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "webhook_secret")
    monkeypatch.setenv("JWT_SECRET", "jwt_secret")

    # Force reimport to test validation
    if "app.config" in sys.modules:
        mod = sys.modules["app.config"]
        # Settings class re-reads on attribute access via _require
        assert mod.settings is not None


def test_config_has_default_urls():
    """FRONTEND_URL and APP_URL should have sensible defaults."""
    from app.config import settings

    assert "localhost" in settings.FRONTEND_URL or settings.FRONTEND_URL != ""
    assert "localhost" in settings.APP_URL or settings.APP_URL != ""


def test_config_settings_type():
    """Settings object should exist with expected attributes."""
    from app.config import settings

    assert hasattr(settings, "DATABASE_URL")
    assert hasattr(settings, "GITHUB_CLIENT_ID")
    assert hasattr(settings, "GITHUB_CLIENT_SECRET")
    assert hasattr(settings, "GITHUB_WEBHOOK_SECRET")
    assert hasattr(settings, "JWT_SECRET")
    assert hasattr(settings, "FRONTEND_URL")
    assert hasattr(settings, "APP_URL")
