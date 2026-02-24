"""Shared in-memory state and small helpers used across build sub-modules.

Every module in *app.services.build* imports its shared dicts/flags from here
so that there is a single source of truth.
"""

import asyncio
import logging
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

from app.repos import build_repo
from app.ws_manager import manager

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Maximum consecutive loopback failures before pausing
from app.config import settings

MAX_LOOP_COUNT = settings.PAUSE_THRESHOLD

# Phase completion signal the builder emits
PHASE_COMPLETE_SIGNAL = "=== PHASE SIGN-OFF: PASS ==="

# Build error signal
BUILD_ERROR_SIGNAL = "RISK_EXCEEDS_SCOPE"

# Plan block delimiters
PLAN_START_PATTERN = re.compile(r"^=== PLAN ===$", re.MULTILINE)
PLAN_END_PATTERN = re.compile(r"^=== END PLAN ===$", re.MULTILINE)

# Context compaction threshold (tokens)
CONTEXT_COMPACTION_THRESHOLD = 150_000

# Universal governance contract directory
FORGE_CONTRACTS_DIR = Path(__file__).resolve().parent.parent.parent.parent / "Forge" / "Contracts"

# File block delimiters for parsing builder output
FILE_START_PATTERN = re.compile(r"^=== FILE:\s*(.+?)\s*===$", re.MULTILINE)
FILE_END_PATTERN = re.compile(r"^=== END FILE ===$", re.MULTILINE)

# Valid build target types
VALID_TARGET_TYPES = {"github_new", "github_existing"}

# Language detection by file extension
_EXT_TO_LANG: dict[str, str] = {
    ".py": "python", ".js": "javascript", ".ts": "typescript",
    ".tsx": "typescriptreact", ".jsx": "javascriptreact",
    ".json": "json", ".yaml": "yaml", ".yml": "yaml",
    ".md": "markdown", ".html": "html", ".css": "css",
    ".sql": "sql", ".sh": "shell", ".ps1": "powershell",
    ".toml": "toml", ".txt": "plaintext", ".env": "dotenv",
    ".gitignore": "ignore",
}


def _detect_language(file_path: str) -> str:
    """Detect language from file extension."""
    ext = Path(file_path).suffix.lower()
    name = Path(file_path).name.lower()
    if name in _EXT_TO_LANG:
        return _EXT_TO_LANG[name]
    return _EXT_TO_LANG.get(ext, "plaintext")


# ---------------------------------------------------------------------------
# In-memory process state (mutable module-level dicts/sets)
# ---------------------------------------------------------------------------

# Active build tasks keyed by build_id
_active_tasks: dict[str, asyncio.Task] = {}

# Pause/resume coordination keyed by build_id
_pause_events: dict[str, asyncio.Event] = {}
_resume_actions: dict[str, str] = {}           # "retry" | "skip" | "abort" | "edit"
_interjection_queues: dict[str, asyncio.Queue] = {}

# Slash-command signals for plan-execute loop
_cancel_flags: set[str] = set()   # build IDs that should cancel ASAP
_pause_flags: set[str] = set()    # build IDs that should pause after current file
_compact_flags: set[str] = set()  # build IDs that should compact context ASAP

# Stop events — asyncio.Event per build, checked by sub-agents between LLM rounds.
# Set by cancel_build() so execute_tier → run_builder → run_sub_agent can bail out
# without waiting for the current Anthropic API call to finish.
_build_stop_events: dict[str, asyncio.Event] = {}  # build_id -> event

# Tracks which file is currently being generated per build
_current_generating: dict[str, str] = {}  # build_id -> file path

# Live activity status
_build_activity_status: dict[str, str] = {}  # build_id -> status label

# Cost tracking — user who started the build (for cost-cap broadcast)
_build_cost_user: dict[str, "UUID"] = {}  # build_id -> user_id

# Build health-check / watchdog state
_build_heartbeat_tasks: dict[str, asyncio.Task] = {}
_last_progress: dict[str, float] = {}  # build_id -> epoch timestamp
_HEARTBEAT_INTERVAL = 45     # seconds between health-check messages
_STALL_WARN_THRESHOLD = 300  # 5 min — emit warning
_STALL_FAIL_THRESHOLD = 900  # 15 min — force-fail the build

# Clarification (forge_ask_clarification tool) state
_clarification_events:  dict[str, asyncio.Event] = {}  # build_id → Event
_clarification_answers: dict[str, str] = {}            # build_id → answer
_clarification_counts:  dict[str, int] = {}            # build_id → # asked so far

# Plan-review gate state (user must approve the plan before builders run)
_plan_review_events:    dict[str, asyncio.Event] = {}  # build_id → Event
_plan_review_responses: dict[str, dict] = {}           # build_id → {"action": "approve"|"reject"|"edit", ...}

# IDE-ready gate state (user must confirm before planning starts)
_ide_ready_events:      dict[str, asyncio.Event] = {}  # build_id → Event
_ide_ready_responses:   dict[str, dict] = {}           # build_id → {"action": "commence"|"cancel"}

# Phase-review gate state (user decides continue vs fix after partial phase)
_phase_review_events:    dict[str, asyncio.Event] = {}  # build_id → Event
_phase_review_responses: dict[str, dict] = {}           # build_id → {"action": "continue"|"fix"}


# ---------------------------------------------------------------------------
# Tiny helpers used by many sub-modules
# ---------------------------------------------------------------------------


def _touch_progress(build_id: UUID | str) -> None:
    """Record a progress heartbeat so the watchdog knows the build is alive."""
    _last_progress[str(build_id)] = time.monotonic()


async def _broadcast_build_event(
    user_id: UUID, build_id: UUID, event_type: str, payload: dict
) -> None:
    """Send a build progress event via WebSocket."""
    await manager.send_to_user(str(user_id), {
        "type": event_type,
        "payload": payload,
    })


def register_clarification(build_id: str) -> asyncio.Event:
    """Create and register a clarification wait event for a build."""
    event = asyncio.Event()
    _clarification_events[str(build_id)] = event
    return event


def resolve_clarification(build_id: str, answer: str) -> bool:
    """Store answer and signal the waiting build loop.

    Returns False if no pending clarification exists for *build_id*.
    """
    bid = str(build_id)
    event = _clarification_events.get(bid)
    if not event:
        return False
    _clarification_answers[bid] = answer
    event.set()
    return True


def pop_clarification_answer(build_id: str) -> str | None:
    """Consume the stored answer and clear event state."""
    bid = str(build_id)
    _clarification_events.pop(bid, None)
    return _clarification_answers.pop(bid, None)


def increment_clarification_count(build_id: str) -> int:
    """Increment and return the number of clarifications asked for this build."""
    bid = str(build_id)
    _clarification_counts[bid] = _clarification_counts.get(bid, 0) + 1
    return _clarification_counts[bid]


def cleanup_clarification(build_id: str) -> None:
    """Remove all clarification state for a build (call on build end/fail)."""
    bid = str(build_id)
    _clarification_events.pop(bid, None)
    _clarification_answers.pop(bid, None)
    _clarification_counts.pop(bid, None)


# ---------------------------------------------------------------------------
# Plan-review gate helpers
# ---------------------------------------------------------------------------


def register_plan_review(build_id: str) -> asyncio.Event:
    """Create and register a plan-review wait event for a build."""
    event = asyncio.Event()
    _plan_review_events[str(build_id)] = event
    return event


def resolve_plan_review(build_id: str, response: dict) -> bool:
    """Store user's plan decision and unblock the waiting build.

    *response* should be ``{"action": "approve"|"reject"|"edit", ...}``.
    Returns False if no pending review exists for *build_id*.
    """
    bid = str(build_id)
    event = _plan_review_events.get(bid)
    if not event:
        return False
    _plan_review_responses[bid] = response
    event.set()
    return True


def pop_plan_review_response(build_id: str) -> dict | None:
    """Consume the stored plan-review response and clear event state."""
    bid = str(build_id)
    _plan_review_events.pop(bid, None)
    return _plan_review_responses.pop(bid, None)


def cleanup_plan_review(build_id: str) -> None:
    """Remove all plan-review state for a build."""
    bid = str(build_id)
    _plan_review_events.pop(bid, None)
    _plan_review_responses.pop(bid, None)


# ---------------------------------------------------------------------------
# Phase-review gate helpers (pause after partial phase completion)
# ---------------------------------------------------------------------------


def register_phase_review(build_id: str) -> asyncio.Event:
    """Create and register a phase-review wait event for a build."""
    event = asyncio.Event()
    _phase_review_events[str(build_id)] = event
    return event


def resolve_phase_review(build_id: str, response: dict) -> bool:
    """Store user's phase-review decision and unblock the waiting build.

    *response* should be ``{"action": "continue"|"fix"}``.
    Returns False if no pending review exists for *build_id*.
    """
    bid = str(build_id)
    event = _phase_review_events.get(bid)
    if not event:
        return False
    _phase_review_responses[bid] = response
    event.set()
    return True


def pop_phase_review_response(build_id: str) -> dict | None:
    """Consume the stored phase-review response and clear event state."""
    bid = str(build_id)
    _phase_review_events.pop(bid, None)
    return _phase_review_responses.pop(bid, None)


def cleanup_phase_review(build_id: str) -> None:
    """Remove all phase-review state for a build."""
    bid = str(build_id)
    _phase_review_events.pop(bid, None)
    _phase_review_responses.pop(bid, None)


# ---------------------------------------------------------------------------
# IDE-ready gate helpers
# ---------------------------------------------------------------------------


def register_ide_ready(build_id: str) -> asyncio.Event:
    """Create and register an IDE-ready wait event for a build."""
    event = asyncio.Event()
    _ide_ready_events[str(build_id)] = event
    return event


def resolve_ide_ready(build_id: str, response: dict) -> bool:
    """Store user's commence decision and unblock the waiting build.

    *response* should be ``{"action": "commence"|"cancel"}``.
    Returns False if no pending ready gate exists for *build_id*.
    """
    bid = str(build_id)
    event = _ide_ready_events.get(bid)
    if not event:
        return False
    _ide_ready_responses[bid] = response
    event.set()
    return True


def pop_ide_ready_response(build_id: str) -> dict | None:
    """Consume the stored IDE-ready response and clear event state."""
    bid = str(build_id)
    _ide_ready_events.pop(bid, None)
    return _ide_ready_responses.pop(bid, None)


def cleanup_ide_ready(build_id: str) -> None:
    """Remove all IDE-ready state for a build."""
    bid = str(build_id)
    _ide_ready_events.pop(bid, None)
    _ide_ready_responses.pop(bid, None)


async def _set_build_activity(
    build_id: UUID, user_id: UUID, status: str,
    model: str = "",
) -> None:
    """Set the live activity status for a build and broadcast to the UI.

    Parameters
    ----------
    model : str
        Which model bucket this activity belongs to ("opus", "sonnet", or
        empty for system).  The frontend uses this to route the message to
        the correct worker panel.
    """
    bid = str(build_id)
    _build_activity_status[bid] = status
    _touch_progress(build_id)
    payload: dict = {"status": status}
    if model:
        payload["model"] = model
    await _broadcast_build_event(user_id, build_id, "build_activity_status", payload)


async def _fail_build(build_id: UUID, user_id: UUID, detail: str) -> None:
    """Mark a build as failed and broadcast the event."""
    bid = str(build_id)
    _cancel_flags.discard(bid)
    _pause_flags.discard(bid)
    _compact_flags.discard(bid)
    _build_stop_events.pop(bid, None)
    _current_generating.pop(bid, None)
    _build_activity_status.pop(bid, None)
    cleanup_clarification(bid)
    cleanup_plan_review(bid)
    cleanup_phase_review(bid)
    cleanup_ide_ready(bid)
    now = datetime.now(timezone.utc)
    await build_repo.update_build_status(
        build_id, "failed", completed_at=now, error_detail=detail
    )
    await build_repo.append_build_log(
        build_id, f"Build failed: {detail}", source="system", level="error"
    )
    await _broadcast_build_event(user_id, build_id, "build_error", {
        "id": str(build_id),
        "status": "failed",
        "error_detail": detail,
    })


async def _pause_build(
    build_id: UUID,
    user_id: UUID,
    phase: str,
    loop_count: int,
    reason: str,
) -> None:
    """Pause a build, persist state, and broadcast event."""
    await build_repo.pause_build(build_id, reason, phase)

    # Set up the pause event for the background task to wait on
    event = asyncio.Event()
    _pause_events[str(build_id)] = event

    await build_repo.append_build_log(
        build_id,
        f"Build paused: {reason}",
        source="system", level="warn",
    )
    await _broadcast_build_event(user_id, build_id, "build_paused", {
        "phase": phase,
        "loop_count": loop_count,
        "audit_findings": reason,
        "options": ["retry", "skip", "abort", "edit"],
    })


async def _build_watchdog(build_id: UUID, user_id: UUID) -> None:
    """Periodic health-check that runs alongside a build.

    Every ``_HEARTBEAT_INTERVAL`` seconds it:
    - Emits a health status line to the activity log
    - Warns if the build looks stalled (>5 min silence)
    - Force-fails the build if stalled >15 min
    """
    bid = str(build_id)
    try:
        while True:
            await asyncio.sleep(_HEARTBEAT_INTERVAL)

            # Build may have finished while we were sleeping
            if bid not in _active_tasks:
                break

            last = _last_progress.get(bid, time.monotonic())
            idle = time.monotonic() - last
            activity_label = _build_activity_status.get(bid, "")
            if not activity_label:
                current_file = _current_generating.get(bid, "")
                activity_label = f"generating {current_file}" if current_file else "processing"
            status = activity_label

            if idle >= _STALL_FAIL_THRESHOLD:
                # Never force-fail a build that is intentionally waiting for
                # user action.  Both gates are user-think-time, not real stalls:
                #   _ide_ready_events    — user hasn't clicked PUSH yet (pre-plan)
                #   _plan_review_events  — user hasn't approved the plan yet (post-plan)
                if bid in _ide_ready_events or bid in _plan_review_events:
                    _touch_progress(build_id)   # reset stall clock
                    idle = 0                    # fall through to normal heartbeat
                else:
                    msg = (
                        f"\u26A0 Health: build stalled — no progress for "
                        f"{int(idle)}s while {status}. Force-failing."
                    )
                    await build_repo.append_build_log(
                        build_id, msg, source="health", level="error",
                    )
                    await _broadcast_build_event(user_id, build_id, "build_log", {
                        "message": msg, "source": "health", "level": "error",
                    })
                    task = _active_tasks.get(bid)
                    if task and not task.done():
                        task.cancel()
                    break

            if idle >= _STALL_WARN_THRESHOLD:
                msg = (
                    f"\u26A0 Health: {status} — no progress for "
                    f"{int(idle)}s (force-fail at {_STALL_FAIL_THRESHOLD}s)"
                )
                level = "warning"
            else:
                mins = int(idle) // 60
                secs = int(idle) % 60
                elapsed_str = f"{mins}m{secs}s" if mins else f"{secs}s"
                msg = f"\u2764 Health: {status} — {elapsed_str} since last progress"
                level = "info"

            await build_repo.append_build_log(
                build_id, msg, source="health", level=level,
            )
            await _broadcast_build_event(user_id, build_id, "build_log", {
                "message": msg, "source": "health", "level": level,
            })
    except asyncio.CancelledError:
        pass
    finally:
        _build_heartbeat_tasks.pop(bid, None)
        _last_progress.pop(bid, None)
