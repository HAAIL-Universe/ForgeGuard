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
        "LLM_QUESTIONNAIRE_MODEL", "claude-3-5-haiku-20241022"
    )
    ANTHROPIC_API_KEY: str = os.getenv("ANTHROPIC_API_KEY", "")
    LLM_BUILDER_MODEL: str = os.getenv(
        "LLM_BUILDER_MODEL", "claude-opus-4-6"
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
