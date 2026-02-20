"""Cost gate / circuit breaker — token tracking, spend caps, budget gates."""

import time
from decimal import Decimal
from uuid import UUID

from app.clients.agent_client import StreamUsage

from . import _state
from ._state import (
    _build_activity_status,
    _build_cost_user,
    _cancel_flags,
)

# Cost-per-token estimates (USD) keyed by model prefix
_MODEL_PRICING: dict[str, tuple[Decimal, Decimal]] = {
    # (input $/token, output $/token)
    "claude-opus-4":       (Decimal("0.000015"),  Decimal("0.000075")),
    "claude-sonnet-4":     (Decimal("0.000003"),  Decimal("0.000015")),
    "claude-3-5-sonnet":   (Decimal("0.000003"),  Decimal("0.000015")),
}
_DEFAULT_INPUT_RATE = Decimal("0.000015")
_DEFAULT_OUTPUT_RATE = Decimal("0.000075")


def _get_token_rates(model: str) -> tuple[Decimal, Decimal]:
    """Return (input_rate, output_rate) per token for the given model."""
    for prefix, rates in _MODEL_PRICING.items():
        if model.startswith(prefix):
            return rates
    return (_DEFAULT_INPUT_RATE, _DEFAULT_OUTPUT_RATE)


# ---------------------------------------------------------------------------
# In-memory running cost state (per build)
# ---------------------------------------------------------------------------

_build_running_cost: dict[str, Decimal] = {}
_build_api_calls: dict[str, int] = {}
_build_total_input_tokens: dict[str, int] = {}
_build_total_output_tokens: dict[str, int] = {}
_build_spend_caps: dict[str, float | None] = {}
_build_cost_warned: dict[str, bool] = {}
_last_cost_ticker: dict[str, float] = {}

# Per-model token tracking  {build_id: {"opus": {"in": N, "out": N}, ...}}
_build_model_tokens: dict[str, dict[str, dict[str, int]]] = {}


def _model_bucket(model: str) -> str:
    """Map a model name to a UI bucket: opus | sonnet | haiku."""
    m = model.lower()
    if "opus" in m:
        return "opus"
    if "haiku" in m:
        return "haiku"
    return "sonnet"


class CostCapExceeded(Exception):
    """Raised when a build's running cost exceeds the configured spend cap."""


async def _accumulate_cost(
    build_id: UUID,
    input_tokens: int,
    output_tokens: int,
    model: str,
    cost: Decimal,
) -> None:
    """Add cost from an LLM call to the running total and check spend cap."""
    bid = str(build_id)
    _build_running_cost[bid] = _build_running_cost.get(bid, Decimal(0)) + cost
    _build_api_calls[bid] = _build_api_calls.get(bid, 0) + 1
    _build_total_input_tokens[bid] = _build_total_input_tokens.get(bid, 0) + input_tokens
    _build_total_output_tokens[bid] = _build_total_output_tokens.get(bid, 0) + output_tokens

    # Per-model tracking
    bucket = _model_bucket(model)
    mt = _build_model_tokens.setdefault(bid, {})
    bkt = mt.setdefault(bucket, {"in": 0, "out": 0})
    bkt["in"] += input_tokens
    bkt["out"] += output_tokens

    now = time.time()
    last = _last_cost_ticker.get(bid, 0.0)
    if now - last >= _state.settings.BUILD_COST_TICKER_INTERVAL:
        _last_cost_ticker[bid] = now
        user_id = _build_cost_user.get(bid)
        if user_id:
            await _broadcast_cost_ticker(build_id, user_id)

    await _check_cost_gate(build_id)


async def _broadcast_cost_ticker(build_id: UUID, user_id: UUID) -> None:
    """Send a cost_ticker WS event with live cost data."""
    bid = str(build_id)
    running_cost = float(_build_running_cost.get(bid, Decimal(0)))
    spend_cap = _build_spend_caps.get(bid)
    effective_cap = spend_cap if spend_cap else (_state.settings.BUILD_MAX_COST_USD or None)
    pct_used = (running_cost / effective_cap * 100) if effective_cap else 0.0

    mt = _build_model_tokens.get(bid, {})
    await _state._broadcast_build_event(user_id, build_id, "cost_ticker", {
        "total_cost_usd": round(running_cost, 6),
        "api_calls": _build_api_calls.get(bid, 0),
        "tokens_in": _build_total_input_tokens.get(bid, 0),
        "tokens_out": _build_total_output_tokens.get(bid, 0),
        "spend_cap": effective_cap,
        "pct_used": round(pct_used, 1),
        "model_tokens": {
            k: {"input": v["in"], "output": v["out"], "total": v["in"] + v["out"]}
            for k, v in mt.items()
        },
    })


async def _check_cost_gate(build_id: UUID) -> None:
    """Check whether the running cost exceeds the spend cap.

    On breach: sets the cancel flag, fails the build, and broadcasts
    a cost_exceeded event.
    """
    bid = str(build_id)
    running = _build_running_cost.get(bid, Decimal(0))

    user_cap = _build_spend_caps.get(bid)
    server_cap = _state.settings.BUILD_MAX_COST_USD
    effective_cap: float | None = None
    if user_cap is not None and user_cap > 0:
        effective_cap = user_cap
    elif server_cap > 0:
        effective_cap = server_cap

    if effective_cap is None or effective_cap <= 0:
        return

    cost_f = float(running)
    user_id = _build_cost_user.get(bid)

    warn_pct = _state.settings.BUILD_COST_WARN_PCT / 100.0
    if cost_f >= effective_cap * warn_pct and not _build_cost_warned.get(bid):
        _build_cost_warned[bid] = True
        if user_id:
            await _state._broadcast_build_event(user_id, build_id, "cost_warning", {
                "total_cost_usd": round(cost_f, 6),
                "spend_cap": effective_cap,
                "pct_used": round(cost_f / effective_cap * 100, 1),
                "message": f"Build has reached {round(cost_f / effective_cap * 100)}% of ${effective_cap:.2f} spend cap",
            })

    if cost_f >= effective_cap:
        _cancel_flags.add(bid)
        if user_id:
            await _state._broadcast_build_event(user_id, build_id, "cost_exceeded", {
                "total_cost_usd": round(cost_f, 6),
                "spend_cap": effective_cap,
                "message": f"Build stopped — cost ${cost_f:.2f} exceeded ${effective_cap:.2f} spend cap",
            })
            await _state._fail_build(build_id, user_id,
                              f"Cost cap exceeded: ${cost_f:.2f} >= ${effective_cap:.2f}")
        raise CostCapExceeded(f"${cost_f:.2f} >= ${effective_cap:.2f}")


def _init_cost_tracking(build_id: UUID, user_id: UUID, spend_cap: float | None) -> None:
    """Initialise in-memory cost tracking for a new build."""
    bid = str(build_id)
    _build_running_cost[bid] = Decimal(0)
    _build_api_calls[bid] = 0
    _build_total_input_tokens[bid] = 0
    _build_total_output_tokens[bid] = 0
    _build_spend_caps[bid] = spend_cap
    _build_cost_warned[bid] = False
    _last_cost_ticker[bid] = 0.0
    _build_cost_user[bid] = user_id
    _build_model_tokens[bid] = {}


def _cleanup_cost_tracking(build_id: UUID) -> None:
    """Remove in-memory cost tracking for a finished build."""
    bid = str(build_id)
    _build_running_cost.pop(bid, None)
    _build_api_calls.pop(bid, None)
    _build_total_input_tokens.pop(bid, None)
    _build_total_output_tokens.pop(bid, None)
    _build_spend_caps.pop(bid, None)
    _build_cost_warned.pop(bid, None)
    _last_cost_ticker.pop(bid, None)
    _build_cost_user.pop(bid, None)
    _build_model_tokens.pop(bid, None)


def get_build_cost_live(build_id: str) -> dict:
    """Return current in-memory cost info for a build (for REST endpoints)."""
    mt = _build_model_tokens.get(build_id, {})
    return {
        "total_cost_usd": round(float(_build_running_cost.get(build_id, Decimal(0))), 6),
        "api_calls": _build_api_calls.get(build_id, 0),
        "tokens_in": _build_total_input_tokens.get(build_id, 0),
        "tokens_out": _build_total_output_tokens.get(build_id, 0),
        "spend_cap": _build_spend_caps.get(build_id),
        "model_tokens": {
            k: {"input": v["in"], "output": v["out"], "total": v["in"] + v["out"]}
            for k, v in mt.items()
        },
    }


async def _record_phase_cost(
    build_id: UUID, phase: str, usage: StreamUsage
) -> None:
    """Persist token usage for the current phase and reset counters."""
    input_t = (usage.input_tokens
               + usage.cache_read_input_tokens
               + usage.cache_creation_input_tokens)
    output_t = usage.output_tokens
    model = usage.model or _state.settings.LLM_BUILDER_MODEL
    input_rate, output_rate = _get_token_rates(model)
    cost = (Decimal(input_t) * input_rate
            + Decimal(output_t) * output_rate)
    await _state.build_repo.record_build_cost(
        build_id, phase, input_t, output_t, model, cost
    )
    await _accumulate_cost(build_id, input_t, output_t, model, cost)
    usage.input_tokens = 0
    usage.output_tokens = 0
    usage.cache_read_input_tokens = 0
    usage.cache_creation_input_tokens = 0


def estimate_phase_cost(
    manifest: list[dict],
    chunks: list[dict],
    *,
    spent_so_far: float = 0.0,
    spend_cap: float | None = None,
) -> dict:
    """Estimate execution cost for a phase before builders run.

    Uses file count, estimated lines, and chunk count to project Opus
    CODER costs plus Sonnet planning overhead.  Returns a dict suitable
    for embedding in a ``plan_review`` WS payload.

    The estimate is intentionally conservative (overestimates) so the user
    is never surprised.
    """
    total_files = len(manifest)
    total_lines = sum(f.get("estimated_lines", 80) for f in manifest)
    num_chunks = len(chunks)

    # Average ~1.5K output tokens per file (code + file tags) at Opus rates
    # Plus ~500 input tokens per file for context assembly
    opus_in_rate, opus_out_rate = _MODEL_PRICING.get(
        "claude-opus-4", (_DEFAULT_INPUT_RATE, _DEFAULT_OUTPUT_RATE)
    )
    sonnet_in_rate, sonnet_out_rate = _MODEL_PRICING.get(
        "claude-sonnet-4", (Decimal("0.000003"), Decimal("0.000015"))
    )

    # CODER cost: ~2K input + ~1.5K output per file (single-shot mode)
    coder_input_tokens = total_files * 2000
    coder_output_tokens = int(total_lines * 1.2)  # ~1.2 tokens per line of code
    coder_cost = float(
        Decimal(coder_input_tokens) * opus_in_rate
        + Decimal(coder_output_tokens) * opus_out_rate
    )

    # Sonnet overhead: interface planning + reviews (~800 in + 400 out per chunk)
    sonnet_input_tokens = num_chunks * 800
    sonnet_output_tokens = num_chunks * 400
    sonnet_cost = float(
        Decimal(sonnet_input_tokens) * sonnet_in_rate
        + Decimal(sonnet_output_tokens) * sonnet_out_rate
    )

    # Audit cost: ~1K in + 500 out per file (Sonnet)
    audit_input_tokens = total_files * 1000
    audit_output_tokens = total_files * 500
    audit_cost = float(
        Decimal(audit_input_tokens) * sonnet_in_rate
        + Decimal(audit_output_tokens) * sonnet_out_rate
    )

    estimated_total = coder_cost + sonnet_cost + audit_cost
    # Apply 1.3x safety margin for retries / fixes
    estimated_high = estimated_total * 1.3

    effective_cap = spend_cap or (_state.settings.BUILD_MAX_COST_USD or None)
    remaining = (effective_cap - spent_so_far) if effective_cap else None

    return {
        "files": total_files,
        "estimated_lines": total_lines,
        "chunks": num_chunks,
        "estimated_cost_low_usd": round(estimated_total, 4),
        "estimated_cost_high_usd": round(estimated_high, 4),
        "spent_so_far_usd": round(spent_so_far, 4),
        "spend_cap_usd": effective_cap,
        "remaining_budget_usd": round(remaining, 4) if remaining is not None else None,
        "breakdown": {
            "coder_opus": round(coder_cost, 4),
            "planning_sonnet": round(sonnet_cost, 4),
            "audit_sonnet": round(audit_cost, 4),
        },
    }
