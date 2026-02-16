"""Application configuration loaded from environment variables.

Validates required settings on import -- fails fast if critical vars are missing.
"""

VERSION = "0.1.0"

import os
import sys

from dotenv import load_dotenv

load_dotenv()


class _MissingVars(Exception):
    """Raised when required environment variables are absent."""


def _require(name: str) -> str:
    """Return env var value or record it as missing."""
    val = os.getenv(name, "")
    if not val:
        _missing.append(name)
    return val


_missing: list[str] = []


class Settings:
    """Application settings from environment.

    Required vars (must be set in production, may be blank in test):
      DATABASE_URL, GITHUB_CLIENT_ID, GITHUB_CLIENT_SECRET,
      GITHUB_WEBHOOK_SECRET, JWT_SECRET
    """

    DATABASE_URL: str = _require("DATABASE_URL")
    GITHUB_CLIENT_ID: str = _require("GITHUB_CLIENT_ID")
    GITHUB_CLIENT_SECRET: str = _require("GITHUB_CLIENT_SECRET")
    GITHUB_WEBHOOK_SECRET: str = _require("GITHUB_WEBHOOK_SECRET")
    JWT_SECRET: str = _require("JWT_SECRET")
    FRONTEND_URL: str = os.getenv("FRONTEND_URL", "http://localhost:5173")
    APP_URL: str = os.getenv("APP_URL", "http://localhost:8000")
    LLM_QUESTIONNAIRE_MODEL: str = os.getenv(
        "LLM_QUESTIONNAIRE_MODEL", "claude-sonnet-4-5"
    )
    ANTHROPIC_API_KEY: str = os.getenv("ANTHROPIC_API_KEY", "")
    OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
    OPENAI_MODEL: str = os.getenv("OPENAI_MODEL", "gpt-4o")
    LLM_PROVIDER: str = os.getenv("LLM_PROVIDER", "")  # "openai" | "anthropic" | auto
    LLM_BUILDER_MODEL: str = os.getenv(
        "LLM_BUILDER_MODEL", "claude-opus-4-6"
    )
    LLM_PLANNER_MODEL: str = os.getenv(
        "LLM_PLANNER_MODEL", "claude-sonnet-4-5"
    )
    PAUSE_THRESHOLD: int = int(os.getenv("PAUSE_THRESHOLD", "3"))
    BUILD_PAUSE_TIMEOUT_MINUTES: int = int(
        os.getenv("BUILD_PAUSE_TIMEOUT_MINUTES", "30")
    )
    PHASE_TIMEOUT_MINUTES: int = int(
        os.getenv("PHASE_TIMEOUT_MINUTES", "10")
    )
    LARGE_FILE_WARN_BYTES: int = int(
        os.getenv("LARGE_FILE_WARN_BYTES", str(1024 * 1024))
    )
    GIT_PUSH_MAX_RETRIES: int = int(
        os.getenv("GIT_PUSH_MAX_RETRIES", "3")
    )
    # Anthropic per-minute token limits (Build tier for Opus).
    # Set these to match your API tier to enable proactive self-throttling.
    ANTHROPIC_INPUT_TPM: int = int(
        os.getenv("ANTHROPIC_INPUT_TPM", "30000")
    )
    ANTHROPIC_OUTPUT_TPM: int = int(
        os.getenv("ANTHROPIC_OUTPUT_TPM", "8000")
    )


# Validate at import time -- but only when NOT running under pytest.
if _missing and "pytest" not in sys.modules:
    print(
        f"[config] FATAL: missing required environment variables: "
        f"{', '.join(_missing)}",
        file=sys.stderr,
    )
    sys.exit(1)

settings = Settings()
