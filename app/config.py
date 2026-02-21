"""Application configuration loaded from environment variables.

Uses ``pydantic-settings`` for automatic env-var loading, type coercion,
and ``.env`` file support.  Validates required settings on import — fails
fast if critical vars are missing outside of tests.
"""

VERSION = "0.1.0"

import sys

from pydantic import Field, model_validator
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

    # -------------------------------------------------------------------------
    # Model tier — controls which models are used across ALL agents.
    #
    #   "haiku"  — cheapest, use during flow validation / iteration
    #   "sonnet" — balanced, use once flows are proven
    #   "opus"   — highest quality, use for production builds
    #
    # Individual model overrides below still take precedence if set explicitly
    # in your .env file.  Changing BUILD_MODEL_TIER is the single knob to
    # switch the whole system.  Requires a server restart to take effect.
    # -------------------------------------------------------------------------
    BUILD_MODEL_TIER: str = "haiku"  # "haiku" | "sonnet" | "opus"

    ANTHROPIC_API_KEY: str = ""
    OPENAI_API_KEY: str = ""
    OPENAI_MODEL: str = "gpt-4o"  # voice transcription only — NOT used for builds/agents
    LLM_PROVIDER: str = ""  # "openai" | "anthropic" | auto

    # -------------------------------------------------------------------------
    # FORGE_FORCE_MODEL — hard override for ALL model calls.
    #
    # When set, every agent role (builder, planner, questionnaire, narrator)
    # uses exactly this model, regardless of BUILD_MODEL_TIER or any per-role
    # override below.  Designed for cost-safe testing.
    #
    # Usage:  FORGE_FORCE_MODEL=claude-haiku-4-5
    # To disable: leave blank (default).
    # -------------------------------------------------------------------------
    FORGE_FORCE_MODEL: str = ""

    # These default to the tier model but can be individually overridden.
    # Leave blank to use the tier default (resolved in get_model_for_role below).
    LLM_BUILDER_MODEL: str = ""
    LLM_PLANNER_MODEL: str = ""
    LLM_QUESTIONNAIRE_MODEL: str = ""
    LLM_NARRATOR_MODEL: str = "claude-haiku-4-5"  # always Haiku — narration only

    @model_validator(mode="after")
    def _apply_force_model(self) -> "Settings":
        """If FORGE_FORCE_MODEL is set, overwrite every model field with it.

        This guarantees no expensive model can be called regardless of tier,
        per-role overrides, or code that reads settings.LLM_*_MODEL directly.
        """
        if self.FORGE_FORCE_MODEL:
            self.LLM_BUILDER_MODEL = self.FORGE_FORCE_MODEL
            self.LLM_PLANNER_MODEL = self.FORGE_FORCE_MODEL
            self.LLM_QUESTIONNAIRE_MODEL = self.FORGE_FORCE_MODEL
            self.LLM_NARRATOR_MODEL = self.FORGE_FORCE_MODEL
            self.BUILD_MODEL_TIER = "haiku"  # keeps get_model_for_role consistent
        return self

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
    LLM_BUILDER_MAX_TOKENS: int = 32_768

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

    # Auto-fix loop settings — tiered escalation when push tests fail
    LLM_FIX_MAX_TIER1: int = 3       # Sonnet plan → Opus code attempts
    LLM_FIX_MAX_TIER2: int = 3       # Sonnet-with-thinking → Opus code attempts
    LLM_THINKING_BUDGET: int = 10000  # token budget for extended thinking
    # Model used for thinking calls when FORGE_FORCE_MODEL=haiku (Haiku can't think).
    # Sonnet is cheapest thinking-capable model — used only during thinking turns,
    # then the build drops back to Haiku for all other calls.
    LLM_THINKING_MODEL: str = "claude-sonnet-4-6"

    # MCP-driven builder: when True, the builder gets a lean system prompt
    # and fetches contracts on-demand via forge_* tools instead of receiving
    # a ~27K token contract dump in the first message.  (Phase 56)
    USE_MCP_CONTRACTS: bool = False

    # Auto-install dependencies when the builder writes a manifest file
    # (requirements.txt, package.json, etc.) into the project.
    AUTO_INSTALL_DEPS: bool = True

    # Builder clarification tool settings
    CLARIFICATION_TIMEOUT_MINUTES: int = 10   # how long to wait before auto-skip
    MAX_CLARIFICATIONS_PER_BUILD: int = 10    # abuse guard


settings = Settings()

# ---------------------------------------------------------------------------
# Model tier resolution
# ---------------------------------------------------------------------------
# Maps tier name → model ID for each agent role.
# "opus" tier still uses Sonnet for planning/questioning — Opus is only
# worthwhile for the code-writing step.
_TIER_MAP: dict[str, dict[str, str]] = {
    "haiku":  {
        "builder":       "claude-haiku-4-5",
        "planner":       "claude-haiku-4-5",
        "questionnaire": "claude-haiku-4-5",
    },
    "sonnet": {
        "builder":       "claude-sonnet-4-6",
        "planner":       "claude-sonnet-4-6",
        "questionnaire": "claude-sonnet-4-6",
    },
    "opus":   {
        "builder":       "claude-opus-4-6",
        "planner":       "claude-sonnet-4-6",
        "questionnaire": "claude-sonnet-4-6",
    },
}


def get_model_for_role(role: str) -> str:
    """Return the resolved model ID for an agent role.

    Resolution order:
      1. FORGE_FORCE_MODEL (absolute override — beats everything)
      2. Per-role env var (LLM_BUILDER_MODEL etc.)
      3. BUILD_MODEL_TIER tier default

    Args:
        role: "builder" | "planner" | "questionnaire"
    """
    # Absolute override — no other logic runs when this is set
    if settings.FORGE_FORCE_MODEL:
        return settings.FORGE_FORCE_MODEL

    override_attr = {
        "builder":       "LLM_BUILDER_MODEL",
        "planner":       "LLM_PLANNER_MODEL",
        "questionnaire": "LLM_QUESTIONNAIRE_MODEL",
    }.get(role)
    if override_attr:
        override = getattr(settings, override_attr, "")
        if override:
            return override
    tier = settings.BUILD_MODEL_TIER
    tier_models = _TIER_MAP.get(tier, _TIER_MAP["haiku"])
    return tier_models.get(role, "claude-haiku-4-5")


def get_model_thinking_budget(role: str) -> int:
    """Return extended thinking token budget for a role.

    Haiku does not support extended thinking — returns 0.
    Sonnet and Opus return LLM_THINKING_BUDGET from settings.

    FORGE_FORCE_MODEL=haiku disables thinking entirely — all calls stay on
    Haiku (cheap, fast for testing). Thinking only activates when the
    resolved model is Sonnet or Opus.
    """
    model = get_model_for_role(role)
    if "haiku" in model.lower():
        return 0
    return settings.LLM_THINKING_BUDGET


def get_thinking_model(role: str) -> str:
    """Return the model to use for thinking API calls.

    When the role's resolved model is Haiku (which doesn't support extended
    thinking), returns LLM_THINKING_MODEL (Sonnet) so the thinking call
    succeeds. For Sonnet and Opus, returns the role's own model unchanged.

    This means: FORGE_FORCE_MODEL=haiku keeps all regular calls on Haiku,
    but thinking turns transparently use Sonnet, then drop back to Haiku.
    """
    model = get_model_for_role(role)
    if "haiku" in model.lower():
        return settings.LLM_THINKING_MODEL
    return model


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
