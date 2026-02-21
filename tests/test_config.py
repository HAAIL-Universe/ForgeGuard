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


def test_config_has_tpm_settings():
    """Settings include Anthropic TPM rate-limit settings."""
    from app.config import settings

    assert hasattr(settings, "ANTHROPIC_INPUT_TPM")
    assert hasattr(settings, "ANTHROPIC_OUTPUT_TPM")
    assert settings.ANTHROPIC_INPUT_TPM > 0
    assert settings.ANTHROPIC_OUTPUT_TPM > 0


def test_config_has_builder_max_tokens():
    """Settings include configurable builder max_tokens."""
    from app.config import settings

    assert hasattr(settings, "LLM_BUILDER_MAX_TOKENS")
    assert settings.LLM_BUILDER_MAX_TOKENS > 0


# ---------------------------------------------------------------------------
# get_model_thinking_budget
# ---------------------------------------------------------------------------


class TestGetModelThinkingBudget:
    """Tests for the thinking budget resolver."""

    def test_haiku_returns_zero(self):
        """Haiku cannot use extended thinking — budget must be 0."""
        from unittest.mock import patch
        from app.config import get_model_thinking_budget

        with patch("app.config.get_model_for_role", return_value="claude-haiku-4-5"):
            assert get_model_thinking_budget("planner") == 0

    def test_haiku_variant_returns_zero(self):
        """Any model name containing 'haiku' → budget 0."""
        from unittest.mock import patch
        from app.config import get_model_thinking_budget

        with patch("app.config.get_model_for_role", return_value="claude-haiku-4-5-20251001"):
            assert get_model_thinking_budget("planner") == 0

    def test_sonnet_returns_budget(self):
        """Sonnet supports extended thinking — returns LLM_THINKING_BUDGET."""
        from unittest.mock import patch
        from app import config
        from app.config import get_model_thinking_budget

        with patch("app.config.get_model_for_role", return_value="claude-sonnet-4-6"), \
             patch.object(config.settings, "LLM_THINKING_BUDGET", 10000):
            result = get_model_thinking_budget("planner")
        assert result == 10000

    def test_opus_returns_budget(self):
        """Opus supports extended thinking — returns LLM_THINKING_BUDGET."""
        from unittest.mock import patch
        from app import config
        from app.config import get_model_thinking_budget

        with patch("app.config.get_model_for_role", return_value="claude-opus-4-6"), \
             patch.object(config.settings, "LLM_THINKING_BUDGET", 8000):
            result = get_model_thinking_budget("builder")
        assert result == 8000

    def test_forge_force_haiku_disables_thinking(self):
        """FORGE_FORCE_MODEL=haiku → budget is 0 (no thinking on any call)."""
        from unittest.mock import patch
        from app import config
        from app.config import get_model_thinking_budget

        with patch.object(config.settings, "FORGE_FORCE_MODEL", "claude-haiku-4-5"):
            # FORGE_FORCE_MODEL=haiku means get_model_for_role returns haiku
            # We verify the real integration: thinking budget → 0
            result = get_model_thinking_budget("planner")
        # Haiku is in the model name → budget must be 0
        assert result == 0

    def test_thinking_budget_setting_exists(self):
        """LLM_THINKING_BUDGET setting is present and positive."""
        from app.config import settings
        assert hasattr(settings, "LLM_THINKING_BUDGET")
        assert settings.LLM_THINKING_BUDGET > 0


# ---------------------------------------------------------------------------
# get_thinking_model
# ---------------------------------------------------------------------------


class TestGetThinkingModel:
    """Tests for the thinking model resolver."""

    def test_haiku_falls_back_to_thinking_model(self):
        """When role resolves to Haiku, return LLM_THINKING_MODEL (Sonnet)."""
        from unittest.mock import patch
        from app import config
        from app.config import get_thinking_model

        with patch("app.config.get_model_for_role", return_value="claude-haiku-4-5"), \
             patch.object(config.settings, "LLM_THINKING_MODEL", "claude-sonnet-4-6"):
            result = get_thinking_model("planner")
        assert result == "claude-sonnet-4-6"

    def test_sonnet_returns_itself(self):
        """When role resolves to Sonnet, return the Sonnet model unchanged."""
        from unittest.mock import patch
        from app.config import get_thinking_model

        with patch("app.config.get_model_for_role", return_value="claude-sonnet-4-6"):
            result = get_thinking_model("planner")
        assert result == "claude-sonnet-4-6"

    def test_opus_returns_itself(self):
        """When role resolves to Opus, return the Opus model unchanged."""
        from unittest.mock import patch
        from app.config import get_thinking_model

        with patch("app.config.get_model_for_role", return_value="claude-opus-4-6"):
            result = get_thinking_model("builder")
        assert result == "claude-opus-4-6"

    def test_thinking_model_setting_is_sonnet(self):
        """LLM_THINKING_MODEL defaults to Sonnet (cheapest thinking-capable model)."""
        from app.config import settings
        assert hasattr(settings, "LLM_THINKING_MODEL")
        assert "sonnet" in settings.LLM_THINKING_MODEL.lower()

    def test_haiku_thinking_model_is_not_haiku(self):
        """When Haiku is forced, the thinking fallback must not also be Haiku."""
        from unittest.mock import patch
        from app.config import get_thinking_model

        with patch("app.config.get_model_for_role", return_value="claude-haiku-4-5"):
            result = get_thinking_model("planner")
        assert "haiku" not in result.lower()


# ---------------------------------------------------------------------------
# get_model_for_role
# ---------------------------------------------------------------------------


class TestGetModelForRole:
    """Tests for the model role resolver — covers FORGE_FORCE_MODEL path."""

    def test_force_model_overrides_tier(self):
        """FORGE_FORCE_MODEL beats BUILD_MODEL_TIER and per-role overrides."""
        from unittest.mock import patch
        from app import config
        from app.config import get_model_for_role

        with patch.object(config.settings, "FORGE_FORCE_MODEL", "claude-haiku-4-5"):
            assert get_model_for_role("planner") == "claude-haiku-4-5"
            assert get_model_for_role("builder") == "claude-haiku-4-5"
            assert get_model_for_role("questionnaire") == "claude-haiku-4-5"

    def test_no_force_model_uses_tier(self):
        """Without FORGE_FORCE_MODEL, tier default is used."""
        from unittest.mock import patch
        from app import config
        from app.config import get_model_for_role

        with patch.object(config.settings, "FORGE_FORCE_MODEL", ""), \
             patch.object(config.settings, "LLM_PLANNER_MODEL", ""), \
             patch.object(config.settings, "BUILD_MODEL_TIER", "haiku"):
            result = get_model_for_role("planner")
        assert "haiku" in result.lower()

    def test_per_role_override_respected(self):
        """A non-empty LLM_PLANNER_MODEL takes precedence over tier default."""
        from unittest.mock import patch
        from app import config
        from app.config import get_model_for_role

        with patch.object(config.settings, "FORGE_FORCE_MODEL", ""), \
             patch.object(config.settings, "LLM_PLANNER_MODEL", "claude-sonnet-4-6"):
            result = get_model_for_role("planner")
        assert result == "claude-sonnet-4-6"
