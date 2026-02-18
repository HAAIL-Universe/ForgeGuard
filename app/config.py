"""Application configuration loaded from environment variables.

Uses ``pydantic-settings`` for automatic env-var loading, type coercion,
and ``.env`` file support.  Validates required settings on import — fails
fast if critical vars are missing outside of tests.
"""

VERSION = "0.1.0"

import sys

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


# ---------------------------------------------------------------------------
# Required var names — checked after instantiation (not during), so tests
# that leave them blank still work.
# ---------------------------------------------------------------------------
_REQUIRED_VARS: list[str] = [
    "DATABASE_URL",
    "GITHUB_CLIENT_ID",
    "GITHUB_CLIENT_SECRET",
    "GITHUB_WEBHOOK_SECRET",
    "JWT_SECRET",
]


class Settings(BaseSettings):
    """Application settings — sourced from environment / ``.env`` file.

    Required vars (must be set in production, may be blank in test):
      DATABASE_URL, GITHUB_CLIENT_ID, GITHUB_CLIENT_SECRET,
      GITHUB_WEBHOOK_SECRET, JWT_SECRET
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # -- required in production (default empty so tests don't fail) --
    DATABASE_URL: str = ""
    GITHUB_CLIENT_ID: str = ""
    GITHUB_CLIENT_SECRET: str = ""
    GITHUB_WEBHOOK_SECRET: str = ""
    JWT_SECRET: str = ""

    # -- optional with sensible defaults --
    FRONTEND_URL: str = "http://localhost:5174"
    APP_URL: str = "http://localhost:8000"
    DEBUG: bool = True
    LOG_LEVEL: str = "INFO"

    LLM_QUESTIONNAIRE_MODEL: str = "claude-sonnet-4-5"
    ANTHROPIC_API_KEY: str = ""
    OPENAI_API_KEY: str = ""
    OPENAI_MODEL: str = "gpt-4o"
    LLM_PROVIDER: str = ""  # "openai" | "anthropic" | auto
    LLM_BUILDER_MODEL: str = "claude-opus-4-6"
    LLM_PLANNER_MODEL: str = "claude-sonnet-4-5"
    LLM_NARRATOR_MODEL: str = "claude-haiku-4-5"

    PAUSE_THRESHOLD: int = Field(default=3, ge=1)
    BUILD_PAUSE_TIMEOUT_MINUTES: int = 30
    PHASE_TIMEOUT_MINUTES: int = 10
    LARGE_FILE_WARN_BYTES: int = 1_048_576  # 1 MiB
    GIT_PUSH_MAX_RETRIES: int = 3

    # Anthropic per-minute token limits (Build tier for Opus).
    # These cover fresh input + cache-creation tokens only (cache reads
    # are NOT rate-limited).  Set via env vars to match your API tier.
    ANTHROPIC_INPUT_TPM: int = 80_000
    ANTHROPIC_OUTPUT_TPM: int = 16_000
    LLM_BUILDER_MAX_TOKENS: int = 16_384

    # Token budget for workspace snapshot in planner context (0 = unlimited)
    RECON_TOKEN_BUDGET: int = 30_000
    # Build mode: "plan_execute" (new) or "conversation" (legacy)
    BUILD_MODE: str = "plan_execute"
    # Server-level hard cost cap (USD) applied when the user has no
    # personal spend cap configured.  Set to 0 to disable.
    BUILD_MAX_COST_USD: float = 50.00
    # Percentage of the effective spend cap at which a warning is sent
    # to the frontend via WebSocket.
    BUILD_COST_WARN_PCT: int = 80
    # How often (in seconds) the backend broadcasts a cost_ticker WS event.
    BUILD_COST_TICKER_INTERVAL: int = 5


settings = Settings()

# Validate at import time — but only when NOT running under pytest.
if "pytest" not in sys.modules:
    _missing = [v for v in _REQUIRED_VARS if not getattr(settings, v)]
    if _missing:
        print(
            f"[config] FATAL: missing required environment variables: "
            f"{', '.join(_missing)}",
            file=sys.stderr,
        )
        sys.exit(1)
