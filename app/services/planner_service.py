"""planner_service.py — Forge Project Planner integration for ForgeGuard builds.

Bridges the standalone planner agent (Z:/ForgeCollection/planner/) into the
async build pipeline.  Called from build_service._run_build_plan_execute()
before phase execution begins.

The standalone planner is synchronous (it owns its own event loop via the
Anthropic SDK).  This module runs it in a thread-pool executor so the async
build task does not block.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path
from uuid import UUID

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Path to the standalone planner package
# ---------------------------------------------------------------------------
# __file__ = Z:/ForgeCollection/ForgeGuard/app/services/planner_service.py
# parents:   services → app → ForgeGuard
_PLANNER_DIR = Path(__file__).resolve().parent.parent.parent / "planner"

# Register the planner package on sys.path once at import time so that
# workers can `from planner_agent import run_planner` without path gymnastics.
if _PLANNER_DIR.exists() and str(_PLANNER_DIR) not in sys.path:
    sys.path.insert(0, str(_PLANNER_DIR))


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def run_project_planner(
    *,
    contracts: list[dict],
    build_id: UUID,
    user_id: UUID,
    api_key: str,
    max_phases: int | None = None,
) -> dict | None:
    """Run the Forge Project Planner Agent for a build.

    Synthesises a project-request string from the project's contracts, then
    calls the standalone planner in a thread-pool executor (it is sync).
    Streams progress logs to the build's WebSocket channel.

    Args:
        contracts:  ForgeGuard project contracts (blueprint, stack, schema…)
        build_id:   ForgeGuard build UUID (telemetry + WS).
        user_id:    Authenticated user UUID (WS target).
        api_key:    User's Anthropic API key (BYOK).
        max_phases: Optional hard cap on the number of phases the planner may
                    produce.  None = no limit (full build).  3 = mini-build.

    Returns:
        dict with keys:
            "plan_path"   (str)  — absolute path to the written plan.json
            "plan"        (dict) — parsed plan content
            "token_usage" (dict) — cumulative token counts across all turns
            "iterations"  (int)  — agentic loop turns taken
        None on any failure.
    """
    from app.services.build._state import _broadcast_build_event
    from app.repos import build_repo

    if not _PLANNER_DIR.exists():
        msg = f"Planner directory not found: {_PLANNER_DIR}"
        logger.error(msg)
        await build_repo.append_build_log(build_id, msg, source="planner", level="error")
        return None

    try:
        from planner_agent import run_planner, PlannerError  # type: ignore[import]
    except ImportError as exc:
        msg = f"Cannot import planner_agent: {exc}"
        logger.error(msg)
        await build_repo.append_build_log(build_id, msg, source="planner", level="error")
        return None

    project_request = _contracts_to_request(contracts)

    # -- helpers ----------------------------------------------------------
    async def _log(message: str, level: str = "info") -> None:
        # DB and WS failures are non-fatal — a logging error must never
        # propagate and cause a completed plan result to be discarded.
        try:
            await build_repo.append_build_log(build_id, message, source="planner", level=level)
        except Exception as _db_exc:
            logger.warning("Planner log DB write failed (non-fatal): %s", _db_exc)
        try:
            await _broadcast_build_event(user_id, build_id, "build_log", {
                "message": message, "source": "planner", "level": level,
            })
        except Exception as _ws_exc:
            logger.warning("Planner log WS broadcast failed (non-fatal): %s", _ws_exc)

    await _log("Project Planner Agent starting…")
    await _broadcast_build_event(user_id, build_id, "planner_started", {
        "build_id": str(build_id),
    })

    import threading as _threading
    from app.config import get_model_for_role, get_model_thinking_budget, get_thinking_model
    _planner_model = get_model_for_role("planner")
    _planner_thinking_budget = get_model_thinking_budget("planner")
    _planner_thinking_model = get_thinking_model("planner")
    await _log(
        f"Model: {_planner_model} | thinking_budget: {_planner_thinking_budget}"
        + (f" | thinking_model: {_planner_thinking_model}" if _planner_thinking_model != _planner_model else "")
    )

    _stop_event = _threading.Event()

    # Capture the running event loop BEFORE entering the thread so we can
    # bridge async log calls back from the synchronous planner thread.
    loop = asyncio.get_running_loop()
    turn_callback = _make_turn_callback(loop, build_id, user_id, build_repo, _broadcast_build_event)

    try:
        result = await loop.run_in_executor(
            None,
            lambda: _call_planner_sync(
                run_planner_fn=run_planner,
                PlannerError=PlannerError,
                project_request=project_request,
                api_key=api_key,
                stop_event=_stop_event,
                model=_planner_model,
                turn_callback=turn_callback,
                thinking_budget=_planner_thinking_budget,
                thinking_model=_planner_thinking_model,
                max_phases=max_phases,
            ),
        )
    except asyncio.CancelledError:
        _stop_event.set()
        raise
    except Exception as exc:
        await _log(f"Project planner crashed: {exc}", "error")
        return None

    if result is None:
        await _log("Project planner failed to produce a plan.", "error")
        return None

    # -- success -----------------------------------------------------------
    plan = result["plan"]
    phases = plan.get("phases", [])
    phase_summary = " → ".join(
        f"Phase {p['number']} ({p['name']})" for p in phases
    )
    u = result["token_usage"]
    savings_pct = u["cache_read_input_tokens"] / max(u["input_tokens"], 1) * 100

    await _log(f"Plan complete — {len(phases)} phases: {phase_summary}")
    await _log(
        f"Tokens: {u['input_tokens']:,} in / {u['output_tokens']:,} out "
        f"(cache saved {savings_pct:.0f}%)"
    )

    # Broadcast a structured event so the UI can render the plan summary
    await _broadcast_build_event(user_id, build_id, "plan_complete", {
        "plan_path": result["plan_path"],
        "phases": phases,
        "token_usage": u,
        "iterations": result["iterations"],
    })

    return result


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _contracts_to_request(contracts: list[dict]) -> str:
    """Build a project-request string from ForgeGuard contracts.

    All contracts are fetched from the Neon DB and embedded directly into the
    planner's initial user turn — the planner has no filesystem access.

    Priority order controls the section sequence in the output.  intake briefs
    come last so the model reads core contracts first, then any supplementary
    requirements from the intake.
    """
    priority = [
        "blueprint", "stack", "schema", "manifesto",
        "physics", "boundaries", "ui", "phases", "intake",
    ]
    sections: list[str] = []
    seen: set[str] = set()

    for ctype in priority:
        for c in contracts:
            if c.get("contract_type") == ctype and ctype not in seen:
                seen.add(ctype)
                label = ctype.upper()
                sections.append(f"## {label} CONTRACT\n\n{c['content'].strip()}")
                break

    if not sections:
        return "No project contracts provided — produce a minimal plan."

    return "\n\n---\n\n".join(sections)


def _log_future_exc(fut, label: str) -> None:
    """Done-callback for fire-and-forget coroutine futures.

    Surfaces exceptions from background log/broadcast calls to the Python
    logger so they are visible in server logs rather than silently discarded.
    Called from a thread by concurrent.futures machinery — must not raise.
    """
    try:
        exc = fut.exception()
        if exc is not None:
            logger.warning("Planner callback failed (%s): %s", label, exc)
    except Exception:
        pass  # CancelledError or similar — ignore


def _make_turn_callback(loop, build_id, user_id, build_repo, broadcast_fn):
    """Return a sync callable that streams planner turn progress to build logs.

    The planner runs in a thread-pool executor (synchronous), but we need to
    post async log/WS calls back to the main event loop.
    asyncio.run_coroutine_threadsafe() is the only safe bridge: it schedules a
    coroutine on `loop` from any thread and returns a concurrent.futures.Future.
    We do NOT await it — fire-and-forget is intentional (logging must not block
    the planner thread).  done_callbacks surface any exceptions to the logger.
    """
    def callback(turn_data: dict) -> None:
        turn = turn_data.get("turn", "?")
        tools = turn_data.get("tool_calls", [])
        out_tokens = turn_data.get("output_tokens", 0)
        cache_hit = turn_data.get("cache_hit", False)
        stop_reason = turn_data.get("stop_reason", "")

        if tools:
            tool_summary = ", ".join(
                f"{t['name']}({', '.join(t['input_keys'])})" for t in tools
            )
        else:
            tool_summary = f"end_turn ({stop_reason})" if stop_reason else "thinking…"

        cache_note = " [cache hit]" if cache_hit else ""
        msg = f"[Turn {turn}] {tool_summary} | {out_tokens} tokens out{cache_note}"

        log_fut = asyncio.run_coroutine_threadsafe(
            build_repo.append_build_log(build_id, msg, source="planner", level="info"),
            loop,
        )
        log_fut.add_done_callback(lambda f: _log_future_exc(f, "append_build_log"))

        ws_fut = asyncio.run_coroutine_threadsafe(
            broadcast_fn(user_id, build_id, "build_log", {
                "message": msg, "source": "planner", "level": "info",
            }),
            loop,
        )
        ws_fut.add_done_callback(lambda f: _log_future_exc(f, "broadcast"))

        # Surface each tool call as a Sonnet pane entry (shows what the planner is doing)
        for tool in tools:
            tool_name = tool.get("name", "?")
            preview = tool.get("input_preview", {})
            # Format: 🔧 read_file("app/auth.py") — use first value as display arg
            first_val = next(iter(preview.values()), None) if preview else None
            if first_val:
                tool_display = f"🔧 {tool_name}({first_val})"
            else:
                tool_display = f"🔧 {tool_name}()"
            tool_fut = asyncio.run_coroutine_threadsafe(
                broadcast_fn(user_id, build_id, "build_log", {
                    "message": tool_display, "source": "planner_tool", "level": "info",
                    "worker": "sonnet",
                }),
                loop,
            )
            tool_fut.add_done_callback(lambda f: _log_future_exc(f, "tool_broadcast"))

        # Surface extended thinking blocks to the UI when present (Sonnet/Opus only)
        thinking_text = turn_data.get("thinking_text")
        if thinking_text:
            think_fut = asyncio.run_coroutine_threadsafe(
                broadcast_fn(user_id, build_id, "thinking_block", {
                    "turn": turn,
                    "source": "planner",
                    "reasoning_text": thinking_text[:4000],
                    "reasoning_length": len(thinking_text),
                }),
                loop,
            )
            think_fut.add_done_callback(lambda f: _log_future_exc(f, "thinking_block"))

        # Surface model text narration when no extended thinking is present (any model)
        # This is the model's visible reasoning — what it writes between tool calls.
        text_content = turn_data.get("text_content")
        if text_content and not thinking_text:
            text_fut = asyncio.run_coroutine_threadsafe(
                broadcast_fn(user_id, build_id, "thinking_block", {
                    "turn": turn,
                    "source": "planner",
                    "reasoning_text": text_content[:4000],
                    "reasoning_length": len(text_content),
                }),
                loop,
            )
            text_fut.add_done_callback(lambda f: _log_future_exc(f, "text_content"))

    return callback


def _call_planner_sync(
    *,
    run_planner_fn,
    PlannerError,
    project_request: str,
    api_key: str,
    stop_event=None,
    model: str | None = None,
    turn_callback=None,
    thinking_budget: int = 0,
    thinking_model: str | None = None,
    max_phases: int | None = None,
) -> dict | None:
    """Synchronous wrapper — injects the BYOK key then calls run_planner().

    Runs in a thread-pool executor; must NOT touch the async event loop.
    Temporarily sets ANTHROPIC_API_KEY so the standalone planner picks it up.
    """
    original = os.environ.get("ANTHROPIC_API_KEY")
    os.environ["ANTHROPIC_API_KEY"] = api_key
    try:
        return run_planner_fn(
            project_request=project_request,
            verbose=False,
            stop_event=stop_event,
            model=model,
            turn_callback=turn_callback,
            thinking_budget=thinking_budget,
            thinking_model=thinking_model,
            max_phases=max_phases,
        )
    except PlannerError as exc:
        logger.error("PlannerError: %s", exc)
        return None
    finally:
        if original is None:
            os.environ.pop("ANTHROPIC_API_KEY", None)
        else:
            os.environ["ANTHROPIC_API_KEY"] = original
