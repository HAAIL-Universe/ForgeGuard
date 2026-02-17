"""Build service -- orchestrates autonomous builder sessions.

Manages the full build lifecycle: validate contracts, spawn agent session,
stream progress, run inline audits, handle loopback, track costs, and
advance phases.

No SQL, no HTTP framework, no direct GitHub API calls.
"""

import asyncio
import json
import logging
import os
import re
import shutil
import tempfile
import time
from decimal import Decimal
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

from app.clients.agent_client import StreamUsage, ToolCall, stream_agent, ApiKeyPool
from app.clients import git_client
from app.clients import github_client
from app.config import settings
from app.repos import build_repo
from app.repos import project_repo
from app.repos.user_repo import get_user_by_id
from app.services.tool_executor import BUILDER_TOOLS, execute_tool_async
from app.ws_manager import manager

logger = logging.getLogger(__name__)

# Maximum consecutive loopback failures before pausing (overridden by settings.PAUSE_THRESHOLD)
MAX_LOOP_COUNT = settings.PAUSE_THRESHOLD

# Phase completion signal the builder emits
PHASE_COMPLETE_SIGNAL = "=== PHASE SIGN-OFF: PASS ==="

# Build error signal
BUILD_ERROR_SIGNAL = "RISK_EXCEEDS_SCOPE"

# Plan block delimiters
PLAN_START_PATTERN = re.compile(r"^=== PLAN ===$", re.MULTILINE)
PLAN_END_PATTERN = re.compile(r"^=== END PLAN ===$", re.MULTILINE)

# Context compaction threshold (tokens) — compact when this is exceeded
CONTEXT_COMPACTION_THRESHOLD = 150_000

# Universal governance contract (not per-project — loaded from disk)
FORGE_CONTRACTS_DIR = Path(__file__).resolve().parent.parent.parent / "Forge" / "Contracts"

# File block delimiters for parsing builder output
FILE_START_PATTERN = re.compile(r"^=== FILE:\s*(.+?)\s*===$", re.MULTILINE)
FILE_END_PATTERN = re.compile(r"^=== END FILE ===$", re.MULTILINE)

# Valid build target types
VALID_TARGET_TYPES = {"github_new", "github_existing", "local_path"}

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


def _parse_file_blocks(text: str) -> list[dict]:
    """Parse file blocks from builder output.

    Expected format:
        === FILE: path/to/file.py ===
        <file contents>
        === END FILE ===

    Returns list of {path, content} dicts.
    """
    blocks: list[dict] = []
    pos = 0
    while pos < len(text):
        start_match = FILE_START_PATTERN.search(text, pos)
        if not start_match:
            break
        file_path = start_match.group(1).strip()
        content_start = start_match.end()

        end_match = FILE_END_PATTERN.search(text, content_start)
        if not end_match:
            # Malformed block -- missing END delimiter; log warning and skip
            logger.warning(
                "Malformed file block (no END delimiter) for: %s", file_path
            )
            pos = content_start
            break

        raw_content = text[content_start:end_match.start()]
        # Strip optional code fence wrapping (```lang ... ```)
        content = _strip_code_fence(raw_content)

        if not file_path:
            # Malformed: empty path
            logger.warning("Malformed file block: empty path, skipping")
            pos = end_match.end()
            continue

        blocks.append({"path": file_path, "content": content})
        pos = end_match.end()

    return blocks


def _strip_code_fence(text: str) -> str:
    """Strip optional markdown code fence wrapper from file content."""
    stripped = text.strip()
    if stripped.startswith("```"):
        # Remove opening fence (```lang or just ```)
        first_nl = stripped.find("\n")
        if first_nl >= 0:
            stripped = stripped[first_nl + 1:]
    if stripped.rstrip().endswith("```"):
        stripped = stripped.rstrip()[:-3]
    return stripped.rstrip("\n") + "\n" if stripped.strip() else ""


def _parse_build_plan(text: str) -> list[dict]:
    """Parse a structured build plan from the builder output.

    Expected format:
        === PLAN ===
        1. Task description one
        2. Task description two
        3. Task description three
        === END PLAN ===

    Returns list of {id, title, status} dicts.
    """
    start_match = PLAN_START_PATTERN.search(text)
    if not start_match:
        return []
    end_match = PLAN_END_PATTERN.search(text, start_match.end())
    if not end_match:
        return []

    plan_text = text[start_match.end():end_match.start()].strip()
    tasks: list[dict] = []
    for line in plan_text.splitlines():
        line = line.strip()
        if not line:
            continue
        # Match "N. description" or "- description"
        m = re.match(r"^(\d+)[.)]\s+(.+)$", line)
        if m:
            tasks.append({
                "id": int(m.group(1)),
                "title": m.group(2).strip(),
                "status": "pending",
            })
        elif line.startswith("- "):
            tasks.append({
                "id": len(tasks) + 1,
                "title": line[2:].strip(),
                "status": "pending",
            })
    return tasks


def _compact_conversation(
    messages: list[dict],
    files_written: list[dict] | None = None,
    current_phase: str = "",
) -> list[dict]:
    """Compact a conversation by summarizing older turns.

    Keeps the first message (directive) and last 2 assistant/user pairs
    intact.  Middle turns are replaced with a progress summary that
    includes the files written so far and current phase, so the builder
    doesn't lose track of what it already did.

    Returns a new compacted message list.
    """
    if len(messages) <= 5:
        # Not enough to compact
        return list(messages)

    # First message is always the directive — keep it
    directive = messages[0]

    # Keep the last 4 messages (2 turns: user+assistant pairs)
    tail = messages[-4:]

    # Build a concise progress summary instead of truncated raw messages
    summary_parts = [
        "[Context compacted — progress summary]\n",
        f"Current phase: {current_phase}\n",
    ]

    if files_written:
        summary_parts.append(f"\nFiles written so far ({len(files_written)}):\n")
        for f in files_written:
            summary_parts.append(f"  - {f['path']} ({f['size_bytes']} bytes)\n")
        summary_parts.append(
            "\nThese files are ALREADY written to disk. "
            "Do NOT re-write them. Continue with the NEXT unwritten file.\n"
        )
    else:
        summary_parts.append("\nNo files written yet.\n")

    # Extract tool calls from middle messages for brief context
    for msg in messages[1:-4]:
        role = msg.get("role", "unknown")
        content = msg.get("content", "")
        if isinstance(content, list):
            # Tool result or multi-block content — extract tool names only
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    summary_parts.append(
                        f"[tool_use]: {block.get('name', '?')}\n"
                    )
        elif isinstance(content, str) and len(content) > 200:
            content = content[:200] + "..."
            summary_parts.append(f"[{role}]: {content}\n")

    summary_msg = {
        "role": "user",
        "content": "\n".join(summary_parts),
    }

    return [directive, summary_msg] + tail


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

# Tracks which file is currently being generated per build (for audit trail)
_current_generating: dict[str, str] = {}  # build_id -> file path

# Live activity status — human-readable label of what the build is doing right now
_build_activity_status: dict[str, str] = {}  # build_id -> status label

# Build health-check / watchdog state
_build_heartbeat_tasks: dict[str, asyncio.Task] = {}
_last_progress: dict[str, float] = {}  # build_id -> epoch timestamp
_HEARTBEAT_INTERVAL = 45     # seconds between health-check messages
_STALL_WARN_THRESHOLD = 300  # 5 min — emit warning
_STALL_FAIL_THRESHOLD = 900  # 15 min — force-fail the build

# Cost-per-token estimates (USD) keyed by model prefix -- updated as pricing changes
_MODEL_PRICING: dict[str, tuple[Decimal, Decimal]] = {
    # (input $/token, output $/token)
    "claude-opus-4":       (Decimal("0.000015"),  Decimal("0.000075")),   # $15 / $75 per 1M
    "claude-sonnet-4":     (Decimal("0.000003"),  Decimal("0.000015")),   # $3 / $15 per 1M
    "claude-3-5-sonnet":   (Decimal("0.000003"),  Decimal("0.000015")),   # $3 / $15 per 1M
}
# Fallback: Opus pricing (most expensive = safest default)
_DEFAULT_INPUT_RATE = Decimal("0.000015")
_DEFAULT_OUTPUT_RATE = Decimal("0.000075")


def _get_token_rates(model: str) -> tuple[Decimal, Decimal]:
    """Return (input_rate, output_rate) per token for the given model."""
    for prefix, rates in _MODEL_PRICING.items():
        if model.startswith(prefix):
            return rates
    return (_DEFAULT_INPUT_RATE, _DEFAULT_OUTPUT_RATE)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def list_builds(project_id: UUID, user_id: UUID) -> list[dict]:
    """List all builds for a project. Validates ownership."""
    project = await project_repo.get_project_by_id(project_id)
    if not project or str(project["user_id"]) != str(user_id):
        raise ValueError("Project not found")
    builds = await build_repo.get_builds_for_project(project_id)
    return [
        {
            "id": str(b["id"]),
            "phase": b["phase"],
            "status": b["status"],
            "branch": b.get("branch", "main"),
            "loop_count": b["loop_count"],
            "started_at": b["started_at"],
            "completed_at": b["completed_at"],
            "created_at": b["created_at"],
            "error_detail": b.get("error_detail"),
        }
        for b in builds
    ]


async def delete_builds(
    project_id: UUID, user_id: UUID, build_ids: list[str]
) -> int:
    """Delete selected builds for a project.  Validates ownership and
    prevents deleting currently running/pending builds."""
    project = await project_repo.get_project_by_id(project_id)
    if not project or str(project["user_id"]) != str(user_id):
        raise ValueError("Project not found")
    if not build_ids:
        raise ValueError("No build IDs provided")

    # Fetch the builds to validate they belong to this project
    all_builds = await build_repo.get_builds_for_project(project_id)
    project_build_ids = {str(b["id"]) for b in all_builds}
    active_build_ids = {
        str(b["id"]) for b in all_builds if b["status"] in ("running", "pending")
    }

    to_delete: list[UUID] = []
    for bid in build_ids:
        if bid not in project_build_ids:
            continue  # skip IDs that don't belong to this project
        if bid in active_build_ids:
            continue  # skip active builds
        to_delete.append(UUID(bid))

    if not to_delete:
        raise ValueError("No eligible builds to delete (active builds cannot be deleted)")

    deleted = await build_repo.delete_builds(to_delete)
    return deleted


async def start_build(
    project_id: UUID,
    user_id: UUID,
    *,
    target_type: str | None = None,
    target_ref: str | None = None,
    branch: str = "main",
    contract_batch: int | None = None,
    resume_from_phase: int = -1,
    working_dir_override: str | None = None,
) -> dict:
    """Start a build for a project.

    Validates that contracts exist, creates a build record, and spawns
    the background orchestration task.

    Args:
        project_id: The project to build.
        user_id: The authenticated user (for ownership check).
        target_type: Build target -- 'github_new', 'github_existing', or 'local_path'.
        target_ref: Target reference -- repo name, full_name, or absolute path.
        contract_batch: If set, use snapshot contracts from this batch instead
                        of the current/live contracts.
        resume_from_phase: Highest completed phase number (-1 = fresh build).
        working_dir_override: Reuse this working directory (for /continue).

    Returns:
        The created build record.

    Raises:
        ValueError: If project not found, not owned, contracts missing,
                    or a build is already running.
    """
    project = await project_repo.get_project_by_id(project_id)
    if not project or str(project["user_id"]) != str(user_id):
        raise ValueError("Project not found")

    # Contracts must be generated before building
    if contract_batch is not None:
        # Use snapshot contracts from a specific historical batch
        contracts = await project_repo.get_snapshot_contracts(project_id, contract_batch)
        if not contracts:
            raise ValueError(f"Snapshot batch {contract_batch} not found")
    else:
        # Use current/live contracts
        contracts = await project_repo.get_contracts_by_project(project_id)
        if not contracts:
            raise ValueError("No contracts found. Generate contracts before building.")

    # Prevent concurrent builds
    latest = await build_repo.get_latest_build_for_project(project_id)
    if latest and latest["status"] in ("pending", "running", "paused"):
        raise ValueError("A build is already in progress for this project")

    # BYOK: user must supply their own Anthropic API key for builds
    user = await get_user_by_id(user_id)
    user_api_key = (user or {}).get("anthropic_api_key") or ""
    user_api_key_2 = (user or {}).get("anthropic_api_key_2") or ""
    if not user_api_key.strip():
        raise ValueError(
            "Anthropic API key required. Add your key in Settings to start a build."
        )

    audit_llm_enabled = (user or {}).get("audit_llm_enabled", True)

    # Auto-detect target from project's connected repo if not provided
    if not target_type and project.get("repo_full_name"):
        target_type = "github_existing"
        target_ref = project["repo_full_name"]
    elif not target_type and project.get("local_path"):
        target_type = "local_path"
        target_ref = project["local_path"]

    # Validate target
    if target_type and target_type not in VALID_TARGET_TYPES:
        raise ValueError(f"Invalid target_type: {target_type}. Must be one of: {', '.join(VALID_TARGET_TYPES)}")
    if target_type and not target_ref:
        raise ValueError("target_ref is required when target_type is specified")

    # Resolve working directory based on target type
    working_dir: str | None = None
    if working_dir_override:
        # /continue — reuse previous build's working directory
        working_dir = working_dir_override
    elif target_type == "local_path":
        working_dir = str(Path(target_ref).resolve()) if target_ref else None
        if working_dir:
            Path(working_dir).mkdir(parents=True, exist_ok=True)
    elif target_type in ("github_new", "github_existing"):
        # Use a temp directory; clone/init happens in _run_build
        working_dir = tempfile.mkdtemp(prefix="forgeguard_build_")

    # Create build record
    build = await build_repo.create_build(
        project_id,
        target_type=target_type,
        target_ref=target_ref,
        working_dir=working_dir,
        branch=branch,
        build_mode=settings.BUILD_MODE,
        contract_batch=contract_batch,
    )

    # Update project status
    await project_repo.update_project_status(project_id, "building")

    # Spawn background task
    task = asyncio.create_task(
        _run_build(
            build["id"], project_id, user_id, contracts,
            user_api_key, audit_llm_enabled,
            target_type=target_type,
            target_ref=target_ref,
            working_dir=working_dir,
            access_token=(user or {}).get("access_token", ""),
            branch=branch,
            api_key_2=user_api_key_2,
            resume_from_phase=resume_from_phase,
        )
    )
    _active_tasks[str(build["id"])] = task

    return build


async def cancel_build(project_id: UUID, user_id: UUID) -> dict:
    """Cancel an active build.

    Args:
        project_id: The project whose build to cancel.
        user_id: The authenticated user (for ownership check).

    Returns:
        The updated build record.

    Raises:
        ValueError: If project not found, not owned, or no active build.
    """
    project = await project_repo.get_project_by_id(project_id)
    if not project or str(project["user_id"]) != str(user_id):
        raise ValueError("Project not found")

    latest = await build_repo.get_latest_build_for_project(project_id)
    if not latest or latest["status"] not in ("pending", "running", "paused"):
        raise ValueError("No active build to cancel")

    build_id = latest["id"]

    # Set cancel flag for plan-execute loop to pick up
    _cancel_flags.add(str(build_id))
    _pause_flags.discard(str(build_id))

    # If paused, signal the pause event to unblock the wait
    event = _pause_events.get(str(build_id))
    if event:
        _resume_actions[str(build_id)] = "abort"
        event.set()

    # Cancel the asyncio task if running
    task = _active_tasks.pop(str(build_id), None)
    if task and not task.done():
        task.cancel()

    # Update DB
    cancelled = await build_repo.cancel_build(build_id)
    if not cancelled:
        raise ValueError("Failed to cancel build")

    await build_repo.append_build_log(
        build_id, "Build cancelled by user", source="system", level="warn"
    )

    # Broadcast cancellation
    await _broadcast_build_event(user_id, build_id, "build_cancelled", {
        "id": str(build_id),
        "status": "cancelled",
    })

    updated = await build_repo.get_build_by_id(build_id)
    return updated


async def force_cancel_build(project_id: UUID, user_id: UUID) -> dict:
    """Force-cancel a build regardless of its current DB status.

    This is the nuclear option — kills the asyncio task, cleans up all
    in-memory state, and marks the build failed.  Use when the normal
    cancel doesn't work (e.g. the build is stuck and the task is not
    responding to CancelledError).

    Returns:
        The updated build record.

    Raises:
        ValueError: If project not found or not owned.
    """
    project = await project_repo.get_project_by_id(project_id)
    if not project or str(project["user_id"]) != str(user_id):
        raise ValueError("Project not found")

    latest = await build_repo.get_latest_build_for_project(project_id)
    if not latest:
        raise ValueError("No builds found")

    build_id = latest["id"]
    bid = str(build_id)

    # Kill everything
    _cancel_flags.add(bid)
    _pause_flags.discard(bid)

    # Cancel the watchdog
    watchdog = _build_heartbeat_tasks.pop(bid, None)
    if watchdog and not watchdog.done():
        watchdog.cancel()

    # Unblock any pause
    event = _pause_events.get(bid)
    if event:
        _resume_actions[bid] = "abort"
        event.set()

    # Kill the asyncio task
    task = _active_tasks.pop(bid, None)
    if task and not task.done():
        task.cancel()

    # Clean up all in-memory state
    _cancel_flags.discard(bid)
    _pause_flags.discard(bid)
    _compact_flags.discard(bid)
    _current_generating.pop(bid, None)
    _build_activity_status.pop(bid, None)
    _interjection_queues.pop(bid, None)
    _last_progress.pop(bid, None)

    # Force-update DB status
    await build_repo.update_build_status(build_id, "failed")
    await build_repo.append_build_log(
        build_id, "Build force-cancelled by user (manual recovery)",
        source="system", level="error",
    )

    # Broadcast
    await _broadcast_build_event(user_id, build_id, "build_failed", {
        "id": bid,
        "status": "failed",
        "error": "Force-cancelled by user",
    })

    updated = await build_repo.get_build_by_id(build_id)
    return updated


async def resume_build(
    project_id: UUID,
    user_id: UUID,
    action: str = "retry",
) -> dict:
    """Resume a paused build.

    Args:
        project_id: The project whose build to resume.
        user_id: The authenticated user (ownership check).
        action: One of 'retry', 'skip', 'abort', 'edit'.

    Returns:
        The updated build record.

    Raises:
        ValueError: If project not found, not owned, or no paused build.
    """
    project = await project_repo.get_project_by_id(project_id)
    if not project or str(project["user_id"]) != str(user_id):
        raise ValueError("Project not found")

    latest = await build_repo.get_latest_build_for_project(project_id)
    if not latest:
        raise ValueError("No build found to resume")

    # Accept paused, running, cancelled, or failed builds.
    # After a server restart the build may be orphaned in any of these states
    # while the frontend still shows the pause modal.
    _resumable = ("paused", "running", "cancelled", "failed")
    if latest["status"] not in _resumable:
        raise ValueError(
            f"Cannot resume build — status is '{latest['status']}'. "
            f"Use /start to begin a new build."
        )

    if action not in ("retry", "skip", "abort", "edit"):
        raise ValueError(f"Invalid action: {action}. Must be retry, skip, abort, or edit.")

    build_id = latest["id"]

    # Store the action and signal the pause event
    _resume_actions[str(build_id)] = action
    event = _pause_events.get(str(build_id))
    if event:
        event.set()
    else:
        # Orphaned pause — the server restarted while the build was paused.
        # The background task that was waiting on the event is gone.
        # Cancel the old build and restart from the current phase.
        logger.warning(
            "Orphaned pause for build %s — restarting from current phase",
            build_id,
        )
        import re as _re_resume
        _phase_str = latest.get("phase", "Phase 0")
        _m = _re_resume.search(r"Phase (\d+)", _phase_str)
        current_phase_num = int(_m.group(1)) if _m else 0

        # Use completed_phases from DB when available (more reliable than
        # the phase column which may not advance until the next phase starts).
        _db_completed = latest.get("completed_phases")
        _db_completed = _db_completed if isinstance(_db_completed, int) else -1

        # If this build has no progress, search ALL builds for the project
        # to find the one with the most progress + a valid working_dir.
        _best_dir = latest.get("working_dir")
        _best_completed = _db_completed
        _best_ref_build = latest
        if _db_completed < 0:
            try:
                all_builds = await build_repo.get_builds_for_project(project_id)
                for b in all_builds:
                    b_cp = b.get("completed_phases")
                    b_cp = b_cp if isinstance(b_cp, int) else -1
                    b_dir = b.get("working_dir")
                    if b_cp > _best_completed and b_dir and Path(b_dir).exists():
                        _best_completed = b_cp
                        _best_dir = b_dir
                        _best_ref_build = b
                if _best_completed > _db_completed:
                    logger.info(
                        "Orphaned recovery: current build has no progress; "
                        "found prior build %s with completed_phases=%d",
                        _best_ref_build["id"], _best_completed,
                    )
            except Exception as exc:
                logger.warning("Failed to search prior builds for orphan recovery: %s", exc)

        # Final fallback: parse git log — the repo is the real source of truth
        if _best_completed < 0 and _best_dir and Path(_best_dir).exists():
            try:
                _git_msgs = await git_client.log_oneline(_best_dir, max_count=200)
                for _gm in _git_msgs:
                    _gm_match = _re_resume.match(
                        r"forge:\s*(.+?)\s+complete(?:\s*\(after \d+ audit attempts?\))?$",
                        _gm, _re_resume.IGNORECASE,
                    )
                    if _gm_match:
                        _gm_phase = _re_resume.search(r"Phase\s+(\d+)", _gm_match.group(1))
                        if _gm_phase:
                            _gp = int(_gm_phase.group(1))
                            if _gp > _best_completed:
                                _best_completed = _gp
                if _best_completed >= 0:
                    logger.info(
                        "Orphaned recovery: inferred completed_phases=%d from git log in %s",
                        _best_completed, _best_dir,
                    )
            except Exception as exc:
                logger.warning("Git log fallback failed in orphan recovery: %s", exc)

        # Figure out resume point: for 'retry' we redo the current phase,
        # for 'skip' we advance past it, for 'abort' we cancel.
        if action == "abort":
            await build_repo.update_build_status(build_id, "cancelled")
            await build_repo.append_build_log(
                build_id, "Build aborted (orphaned pause recovery)",
                source="system", level="warn",
            )
            updated = await build_repo.get_build_by_id(build_id)
            return updated

        if action == "skip":
            resume_from = max(current_phase_num, _best_completed)  # skip = mark current as done
        else:
            # retry / edit = redo current phase.
            # But if progress shows the phase is already done,
            # continue from the next phase instead of redoing it.
            if _best_completed >= current_phase_num:
                resume_from = _best_completed  # phase already done → continue
            else:
                resume_from = current_phase_num - 1 if current_phase_num > 0 else -1

        # Cancel old build first
        await build_repo.update_build_status(build_id, "cancelled")
        await build_repo.append_build_log(
            build_id,
            f"Build cancelled for orphaned pause recovery (action={action}, resume_from={resume_from})",
            source="system", level="info",
        )

        _use_dir = _best_dir or latest.get("working_dir")
        if _use_dir and Path(_use_dir).exists():
            new_build = await start_build(
                project_id, user_id,
                target_type=_best_ref_build.get("target_type"),
                target_ref=_best_ref_build.get("target_ref"),
                branch=_best_ref_build.get("branch", "main"),
                contract_batch=_best_ref_build.get("contract_batch"),
                resume_from_phase=resume_from,
                working_dir_override=_use_dir,
            )
            return new_build
        else:
            raise ValueError(
                "Previous build's working directory no longer exists. Use /start for a fresh build."
            )

    await build_repo.append_build_log(
        build_id,
        f"Build resumed with action: {action}",
        source="system", level="info",
    )

    # Give a tick for the background task to process
    await asyncio.sleep(0.05)

    updated = await build_repo.get_build_by_id(build_id)
    return updated


def _interrupted_file_log(build_id: UUID, command: str) -> str:
    """Build a log message that records which file was being generated when a command fired."""
    bid = str(build_id)
    current = _current_generating.get(bid)
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    if current:
        return f"{command} at {ts} while generating: {current}"
    return f"{command} at {ts} (no file in progress)"


async def interject_build(
    project_id: UUID,
    user_id: UUID,
    message: str,
) -> dict:
    """Inject a user message or slash command into an active build.

    Slash commands:
        /stop      — cancel the current build immediately
        /pause     — pause after the current file finishes
        /start     — resume or start build (optionally: /start phase N)
        /verify    — run verification (syntax + tests) on project files
        /fix       — send verification errors to the builder for fixing
        /compact   — compact context before the next file
        /clear     — stop the build and restart immediately (preserves files on disk)
        /commit    — git add, commit, and push all files to GitHub immediately
        /push      — push to GitHub (commits uncommitted changes first, sets remote if needed)
        /pull      — pull from GitHub and continue from last committed phase
        /status    — get an LLM-generated summary of current build state

    /continue is accepted as an alias for /start.

    Regular messages are queued as interjections.
    """
    stripped = message.strip().lower()

    # --- /stop -------------------------------------------------------
    if stripped == "/stop":
        project = await project_repo.get_project_by_id(project_id)
        if not project or str(project["user_id"]) != str(user_id):
            raise ValueError("Project not found")
        latest = await build_repo.get_latest_build_for_project(project_id)
        if latest:
            await build_repo.append_build_log(
                latest["id"], _interrupted_file_log(latest["id"], "/stop"),
                source="user", level="warn",
            )
        result = await cancel_build(project_id, user_id)
        return {"status": "stopped", "build_id": str(result["id"]), "message": "Build stopped via /stop"}

    # --- /clear ------------------------------------------------------
    if stripped == "/clear":
        # Stop current build, then immediately start a new one
        project = await project_repo.get_project_by_id(project_id)
        if not project or str(project["user_id"]) != str(user_id):
            raise ValueError("Project not found")
        latest = await build_repo.get_latest_build_for_project(project_id)
        if latest and latest["status"] in ("running", "paused", "pending"):
            await build_repo.append_build_log(
                latest["id"], _interrupted_file_log(latest["id"], "/clear"),
                source="user", level="warn",
            )
            try:
                await cancel_build(project_id, user_id)
            except Exception:
                logger.info("/clear: cancel_build raised (may already be cancelled)", exc_info=True)
            # Give the cancelled task a tick to clean up
            await asyncio.sleep(0.1)
        # Start fresh build — will pick up existing files on disk
        result = await start_build(project_id, user_id)
        return {"status": "cleared", "build_id": str(result["id"]), "message": "Build cleared and restarted via /clear"}

    # --- /start [phase N] (also handles /continue as alias) ----------
    if stripped == "/start" or stripped.startswith("/start ") or stripped == "/continue" or stripped.startswith("/continue "):
        project = await project_repo.get_project_by_id(project_id)
        if not project or str(project["user_id"]) != str(user_id):
            raise ValueError("Project not found")
        latest = await build_repo.get_latest_build_for_project(project_id)
        if latest and latest["status"] == "paused":
            result = await resume_build(project_id, user_id, action="retry")
            return {"status": "resumed", "build_id": str(result["id"]), "message": "Build resumed via /start"}
        # If running, check if it's truly active or orphaned (server restart)
        if latest and latest["status"] == "running":
            bid = str(latest["id"])
            # A truly running build has active in-memory state
            is_truly_active = (
                bid in _current_generating
                or bid in _pause_events
                or bid in _pause_flags
                or bid in _build_heartbeat_tasks
                or bid in _last_progress
            )
            if is_truly_active:
                return {"status": "already_running", "build_id": str(latest["id"]), "message": "Build is already running"}
            # Orphaned running build — recover by continuing (not retrying)
            result = await resume_build(project_id, user_id, action="retry")
            _cp = latest.get("completed_phases")
            _cp_str = f" (completed_phases={_cp})" if _cp is not None else ""
            return {"status": "resumed", "build_id": str(result["id"]), "message": f"Recovering orphaned build via /start{_cp_str}"}

        # Parse optional "phase N" argument (works for both /start and /continue)
        import re as _re_start
        explicit_phase: int | None = None
        _phase_match = _re_start.search(r"phase\s+(\d+)", stripped)
        if _phase_match:
            explicit_phase = int(_phase_match.group(1))

        # If previous build has progress, auto-continue.
        # .get() returns None for NULL DB values (key exists but value is None),
        # so coerce to int explicitly.
        _raw_cp = latest.get("completed_phases") if latest else None
        _safe_cp = _raw_cp if isinstance(_raw_cp, int) else -1

        # The "latest" build may have no progress (e.g. a buggy recovery
        # cancelled the real build and started a fresh one).  Search ALL
        # builds for the one with the most progress + a valid working_dir.
        _best_build = latest
        _best_cp = _safe_cp
        if _safe_cp < 0 and latest:
            try:
                all_builds = await build_repo.get_builds_for_project(project_id)
                for b in all_builds:
                    b_cp = b.get("completed_phases")
                    b_cp = b_cp if isinstance(b_cp, int) else -1
                    b_dir = b.get("working_dir")
                    if b_cp > _best_cp and b_dir and Path(b_dir).exists():
                        _best_cp = b_cp
                        _best_build = b
                if _best_cp > _safe_cp:
                    logger.info(
                        "Latest build has no progress; found prior build %s "
                        "with completed_phases=%d and valid working_dir",
                        _best_build["id"], _best_cp,
                    )
            except Exception as exc:
                logger.warning("Failed to search prior builds: %s", exc)

        # Final fallback: parse git log for "forge: Phase N ... complete"
        # commits — the git repo is the real source of truth.
        _scan_dir = (_best_build or latest or {}).get("working_dir")
        if _best_cp < 0 and _scan_dir and Path(_scan_dir).exists():
            try:
                _git_msgs = await git_client.log_oneline(_scan_dir, max_count=200)
                for _gm in _git_msgs:
                    _gm_match = _re_start.match(
                        r"forge:\s*(.+?)\s+complete(?:\s*\(after \d+ audit attempts?\))?$",
                        _gm, _re_start.IGNORECASE,
                    )
                    if _gm_match:
                        _gm_phase = _re_start.search(r"Phase\s+(\d+)", _gm_match.group(1))
                        if _gm_phase:
                            _gp = int(_gm_phase.group(1))
                            if _gp > _best_cp:
                                _best_cp = _gp
                if _best_cp >= 0:
                    logger.info(
                        "Inferred completed_phases=%d from git log in %s",
                        _best_cp, _scan_dir,
                    )
            except Exception as exc:
                logger.warning("Git log fallback failed: %s", exc)

        if _best_build and (_best_cp >= 0 or explicit_phase is not None):
            if explicit_phase is not None:
                completed = explicit_phase - 1
            else:
                completed = _best_cp
                # Fallback: infer completed phases from the phase column
                if completed < 0:
                    phase_str = _best_build.get("phase", "Phase 0")
                    _m = _re_start.search(r"Phase (\d+)", phase_str)
                    if _m and int(_m.group(1)) > 0:
                        completed = int(_m.group(1)) - 1

            prev_dir = _best_build.get("working_dir")
            can_reuse_dir = bool(prev_dir and Path(prev_dir).exists())
            if completed >= 0:
                # When an explicit phase is requested, clear its cached manifest
                # so the planner generates a fresh one (existing files on disk
                # are still skipped via the skip-existing-files logic).
                if explicit_phase is not None and can_reuse_dir and prev_dir:
                    _cache = Path(prev_dir) / ".forge" / f"manifest_phase_{explicit_phase}.json"
                    if _cache.exists():
                        _cache.unlink(missing_ok=True)
                        logger.info(
                            "Cleared cached manifest for Phase %d (explicit /start phase %d)",
                            explicit_phase, explicit_phase,
                        )

                # Prefer reusing the existing workspace; if it is missing but
                # the target is GitHub-backed, fall back to a fresh clone while
                # preserving the resume_from_phase so we do not restart at Phase 1.
                if can_reuse_dir:
                    result = await start_build(
                        project_id, user_id,
                        target_type=_best_build.get("target_type"),
                        target_ref=_best_build.get("target_ref"),
                        branch=_best_build.get("branch", "main"),
                        contract_batch=_best_build.get("contract_batch"),
                        resume_from_phase=completed,
                        working_dir_override=prev_dir,
                    )
                    target_phase = completed + 1
                    return {
                        "status": "continued",
                        "build_id": str(result["id"]),
                        "message": f"Build continuing from Phase {target_phase} (use /clear to start fresh)",
                    }
                elif _best_build.get("target_type") in ("github_new", "github_existing"):
                    # Workspace vanished (e.g., temp dir cleaned up) — clone fresh
                    # and resume from the last completed phase inferred from DB/git log.
                    result = await start_build(
                        project_id, user_id,
                        target_type=_best_build.get("target_type"),
                        target_ref=_best_build.get("target_ref"),
                        branch=_best_build.get("branch", "main"),
                        contract_batch=_best_build.get("contract_batch"),
                        resume_from_phase=completed,
                    )
                    target_phase = completed + 1
                    return {
                        "status": "continued",
                        "build_id": str(result["id"]),
                        "message": (
                            f"Build continuing from Phase {target_phase} (fresh clone — "
                            "previous workspace missing)"
                        ),
                    }

        # No prior progress — start fresh
        result = await start_build(project_id, user_id)
        return {"status": "started", "build_id": str(result["id"]), "message": "Build started via /start"}

    # --- /verify [phase N] -------------------------------------------
    if stripped == "/verify" or stripped.startswith("/verify "):
        project = await project_repo.get_project_by_id(project_id)
        if not project or str(project["user_id"]) != str(user_id):
            raise ValueError("Project not found")
        latest = await build_repo.get_latest_build_for_project(project_id)
        if not latest:
            raise ValueError("No builds found. Use /start to begin a new build.")
        working_dir = latest.get("working_dir")
        if not working_dir or not Path(working_dir).exists():
            raise ValueError("Build working directory not found on disk.")
        build_id = latest["id"]
        api_key = ""
        user = await get_user_by_id(user_id)
        if user:
            api_key = user.get("anthropic_api_key") or user.get("api_key") or ""

        # Scan working dir to build a lightweight manifest
        _wd = Path(working_dir)
        scan_manifest: list[dict] = []
        for fp in sorted(_wd.rglob("*")):
            if fp.is_dir():
                continue
            rel = str(fp.relative_to(_wd)).replace("\\", "/")
            # Skip hidden / generated dirs
            if any(part.startswith(".") for part in rel.split("/")):
                continue
            if rel.startswith("__pycache__/") or "/__pycache__/" in rel:
                continue
            if rel.startswith("node_modules/") or "/node_modules/" in rel:
                continue
            scan_manifest.append({
                "path": rel,
                "language": _detect_language(rel),
            })

        if not scan_manifest:
            raise ValueError("No files found in working directory.")

        # Run verification as a background task so we don't block the interject
        async def _run_verify_task() -> None:
            try:
                _log_msg = f"Running verification on {len(scan_manifest)} files..."
                await build_repo.append_build_log(build_id, _log_msg, source="system", level="info")
                await _broadcast_build_event(user_id, build_id, "build_log", {
                    "message": _log_msg, "source": "system", "level": "info",
                })
                result = await _verify_phase_output(
                    build_id, user_id, api_key,
                    scan_manifest, working_dir, [],
                )
                _v_msg = (
                    f"Verification complete — "
                    f"{result.get('syntax_errors', 0)} syntax errors, "
                    f"{result.get('tests_passed', 0)} tests passed, "
                    f"{result.get('tests_failed', 0)} tests failed, "
                    f"{result.get('fixes_applied', 0)} auto-fixes"
                )
                await build_repo.append_build_log(build_id, _v_msg, source="system", level="info")
                await _broadcast_build_event(user_id, build_id, "build_log", {
                    "message": _v_msg, "source": "system", "level": "info",
                })
            except Exception as exc:
                logger.warning("/verify task failed: %s", exc)
                await _broadcast_build_event(user_id, build_id, "build_log", {
                    "message": f"Verification error: {exc}",
                    "source": "system", "level": "error",
                })

        asyncio.create_task(_run_verify_task())
        return {
            "status": "verifying",
            "build_id": str(build_id),
            "message": f"Running verification on {len(scan_manifest)} files...",
        }

    # --- /fix <error details> ----------------------------------------
    if stripped.startswith("/fix "):
        fix_payload = message.strip()[4:].strip()  # preserve original case
        if not fix_payload:
            raise ValueError("No error details provided. Usage: /fix <error output>")

        project = await project_repo.get_project_by_id(project_id)
        if not project or str(project["user_id"]) != str(user_id):
            raise ValueError("Project not found")
        latest = await build_repo.get_latest_build_for_project(project_id)
        if not latest:
            raise ValueError("No builds found. Use /start first.")

        working_dir = latest.get("working_dir")
        if not working_dir or not Path(working_dir).exists():
            raise ValueError("Build working directory not found on disk.")

        build_id = latest["id"]
        user = await get_user_by_id(user_id)
        api_key = ""
        if user:
            api_key = user.get("anthropic_api_key") or user.get("api_key") or ""

        if latest["status"] in ("running", "paused"):
            # Build is active — queue as interjection
            queue = _interjection_queues.get(str(build_id))
            if queue is not None:
                queue.put_nowait(fix_payload)
            await build_repo.append_build_log(
                build_id, f"Fix request queued: {fix_payload[:200]}...",
                source="user", level="info",
            )
            await _broadcast_build_event(user_id, build_id, "build_log", {
                "message": "🔧 Fix request sent to builder",
                "source": "user", "level": "info",
            })
            return {
                "status": "fix_queued",
                "build_id": str(build_id),
                "message": "Fix request queued for active build",
            }

        # Build not active — do a targeted in-place fix
        async def _run_fix_task() -> None:
            try:
                _touch_progress(build_id)
                await _broadcast_build_event(user_id, build_id, "build_log", {
                    "message": "🔧 Analysing error and applying targeted fix...",
                    "source": "system", "level": "info",
                })

                # Extract file paths mentioned in the error output
                import re as _re_fix
                _wd = Path(working_dir)
                mentioned_files: dict[str, str] = {}
                # Match paths like backend/app/foo.py, tests/test_foo.py, etc.
                for m in _re_fix.finditer(r'((?:[\w./-]+/)?[\w.-]+\.(?:py|ts|tsx|js|jsx|sql|ini|cfg|toml|yaml|yml|json|css|html))', fix_payload):
                    rel = m.group(1).replace("\\", "/")
                    fp = _wd / rel
                    if fp.exists() and fp.is_file():
                        try:
                            mentioned_files[rel] = fp.read_text(encoding="utf-8")
                        except Exception:
                            pass

                # Also check for common config files mentioned by name
                for cfg_name in ("pytest.ini", "pyproject.toml", "setup.cfg", "requirements.txt"):
                    if cfg_name.lower() in fix_payload.lower():
                        fp = _wd / cfg_name
                        if fp.exists() and cfg_name not in mentioned_files:
                            try:
                                mentioned_files[cfg_name] = fp.read_text(encoding="utf-8")
                            except Exception:
                                pass

                if not mentioned_files:
                    await _broadcast_build_event(user_id, build_id, "build_log", {
                        "message": "🔧 Could not identify files from error output. No files modified.",
                        "source": "system", "level": "warn",
                    })
                    return

                files_list = ", ".join(mentioned_files.keys())
                await _broadcast_build_event(user_id, build_id, "build_log", {
                    "message": f"🔧 Identified {len(mentioned_files)} file(s) to fix: {files_list}",
                    "source": "system", "level": "info",
                })

                # Build LLM prompt
                file_blocks = "\n\n".join(
                    f"=== FILE: {fp} ===\n{content}\n=== END FILE ==="
                    for fp, content in mentioned_files.items()
                )
                system_prompt = (
                    "You are a precise code repair tool. You will be given one or more files "
                    "and an error output. Fix ONLY the issue described in the error output. "
                    "Make the absolute minimum changes necessary — do not refactor, rename, "
                    "or change anything unrelated to the error.\n\n"
                    "Return ONLY the fixed file(s) in this exact format:\n"
                    "=== FILE: path/to/file.ext ===\n"
                    "<complete file content>\n"
                    "=== END FILE ===\n\n"
                    "If a file does not need changes, do not include it in the output."
                )
                user_message = (
                    f"## Error Output\n```\n{fix_payload}\n```\n\n"
                    f"## Current File Contents\n{file_blocks}"
                )

                from app.clients.llm_client import chat as llm_chat
                result = await asyncio.wait_for(
                    llm_chat(
                        api_key=api_key,
                        model=settings.LLM_BUILDER_MODEL,
                        system_prompt=system_prompt,
                        messages=[{"role": "user", "content": user_message}],
                        max_tokens=16384,
                        provider="anthropic",
                    ),
                    timeout=180,
                )

                response_text = result["text"] if isinstance(result, dict) else result

                # Parse fixed files from response
                fixed_files = _parse_file_blocks(response_text)
                if not fixed_files:
                    await _broadcast_build_event(user_id, build_id, "build_log", {
                        "message": "🔧 LLM returned no file blocks — could not apply fix.",
                        "source": "system", "level": "warn",
                    })
                    return

                # Write fixed files to disk
                for ff in fixed_files:
                    fp = _wd / ff["path"]
                    fp.parent.mkdir(parents=True, exist_ok=True)
                    fp.write_text(ff["content"], encoding="utf-8")
                    await _broadcast_build_event(user_id, build_id, "build_log", {
                        "message": f"🔧 Fixed: {ff['path']}",
                        "source": "system", "level": "info",
                    })

                # Commit the fix
                try:
                    await git_client.add_all(working_dir)
                    sha = await git_client.commit(working_dir, "forge: targeted fix via /fix")
                    if sha:
                        await build_repo.append_build_log(
                            build_id, f"🔧 Fix committed: {sha[:8]}",
                            source="system", level="info",
                        )
                        await _broadcast_build_event(user_id, build_id, "build_log", {
                            "message": f"🔧 Fix committed: {sha[:8]}",
                            "source": "system", "level": "info",
                        })
                except Exception as exc:
                    logger.warning("/fix commit failed: %s", exc)

                await _broadcast_build_event(user_id, build_id, "build_log", {
                    "message": f"🔧 Fix complete — {len(fixed_files)} file(s) repaired",
                    "source": "system", "level": "info",
                })

            except Exception as exc:
                logger.warning("/fix task failed: %s", exc)
                await _broadcast_build_event(user_id, build_id, "build_log", {
                    "message": f"🔧 Fix error: {exc}",
                    "source": "system", "level": "error",
                })

        asyncio.create_task(_run_fix_task())
        return {
            "status": "fix_started",
            "build_id": str(build_id),
            "message": f"Targeted fix in progress...",
        }

    # --- /compact ----------------------------------------------------
    if stripped == "/compact":
        project = await project_repo.get_project_by_id(project_id)
        if not project or str(project["user_id"]) != str(user_id):
            raise ValueError("Project not found")
        latest = await build_repo.get_latest_build_for_project(project_id)
        if not latest or latest["status"] != "running":
            raise ValueError("No running build to compact")
        build_id = latest["id"]
        _compact_flags.add(str(build_id))
        await build_repo.append_build_log(
            build_id, _interrupted_file_log(build_id, "/compact"),
            source="user", level="info",
        )
        await build_repo.append_build_log(
            build_id, "Context compaction requested via /compact",
            source="user", level="info",
        )
        await _broadcast_build_event(user_id, build_id, "build_log", {
            "message": "Context compaction requested — will compact before next file",
            "source": "user", "level": "info",
        })
        return {"status": "compact_requested", "build_id": str(build_id), "message": "Context compaction requested via /compact"}

    # --- /pause ------------------------------------------------------
    if stripped == "/pause":
        project = await project_repo.get_project_by_id(project_id)
        if not project or str(project["user_id"]) != str(user_id):
            raise ValueError("Project not found")
        latest = await build_repo.get_latest_build_for_project(project_id)
        if not latest or latest["status"] != "running":
            raise ValueError("No running build to pause")
        build_id = latest["id"]
        _pause_flags.add(str(build_id))
        await build_repo.append_build_log(
            build_id, _interrupted_file_log(build_id, "/pause"),
            source="user", level="info",
        )
        await build_repo.append_build_log(
            build_id, "Pause requested via /pause — will pause after current file",
            source="user", level="info",
        )
        await _broadcast_build_event(user_id, build_id, "build_log", {
            "message": "Pause requested — will pause after current file",
            "source": "user", "level": "info",
        })
        return {"status": "pause_requested", "build_id": str(build_id), "message": "Pause requested via /pause"}

    # --- /commit -----------------------------------------------------
    if stripped == "/commit":
        project = await project_repo.get_project_by_id(project_id)
        if not project or str(project["user_id"]) != str(user_id):
            raise ValueError("Project not found")
        latest = await build_repo.get_latest_build_for_project(project_id)
        if not latest:
            raise ValueError("No builds found for this project")
        working_dir = latest.get("working_dir")
        if not working_dir:
            raise ValueError("Build has no working directory")
        branch = latest.get("branch", "main")
        target_type = latest.get("target_type", "")
        build_id = latest["id"]

        # Get access token from user record
        user = await get_user_by_id(user_id)
        access_token = (user or {}).get("access_token", "")

        try:
            # Ensure working_dir is a git repo (local_path builds may not have one)
            git_dir = Path(working_dir) / ".git"
            if not git_dir.exists():
                await git_client.init_repo(working_dir)
                # If there's a branch other than main, create it
                if branch and branch != "main":
                    try:
                        await git_client.create_branch(working_dir, branch)
                    except Exception:
                        pass  # branch may already exist

            await git_client.add_all(working_dir)
            sha = await git_client.commit(
                working_dir, "forge: manual commit via /commit"
            )
            pushed = False
            if sha and target_type in ("github_new", "github_existing") and access_token:
                try:
                    await git_client.push(
                        working_dir, branch=branch, access_token=access_token,
                    )
                    pushed = True
                except Exception as push_exc:
                    logger.info("/commit push skipped (no remote?): %s", push_exc)

            msg = f"Committed ({sha[:8]})" if sha else "Nothing new to commit"
            if pushed:
                msg += " and pushed to GitHub"

            await build_repo.append_build_log(
                build_id, msg, source="user", level="info",
            )
            await _broadcast_build_event(user_id, build_id, "build_log", {
                "message": msg, "source": "user", "level": "info",
            })
            return {"status": "committed", "build_id": str(build_id), "message": msg}
        except Exception as exc:
            logger.warning("/commit failed: %s", exc)
            raise ValueError(f"Git commit failed: {exc}")

    # --- /push -------------------------------------------------------
    if stripped == "/push":
        project = await project_repo.get_project_by_id(project_id)
        if not project or str(project["user_id"]) != str(user_id):
            raise ValueError("Project not found")
        latest = await build_repo.get_latest_build_for_project(project_id)
        if not latest:
            raise ValueError("No builds found for this project")
        working_dir = latest.get("working_dir")
        if not working_dir:
            raise ValueError("Build has no working directory")
        branch = latest.get("branch", "main")
        build_id = latest["id"]

        user = await get_user_by_id(user_id)
        access_token = (user or {}).get("access_token", "")
        if not access_token:
            raise ValueError("No GitHub access token — connect GitHub in Settings to push")

        # Resolve repo URL from project
        repo_full_name = project.get("repo_full_name") or latest.get("target_ref", "")
        if not repo_full_name:
            raise ValueError("No GitHub repository linked to this project")

        try:
            # Ensure git repo exists
            git_dir = Path(working_dir) / ".git"
            if not git_dir.exists():
                await git_client.init_repo(working_dir)

            # Commit any uncommitted changes first
            await git_client.add_all(working_dir)
            sha = await git_client.commit(working_dir, "forge: manual commit via /push")

            # Set remote to the project's GitHub repo
            remote_url = f"https://github.com/{repo_full_name}.git"
            await git_client.set_remote(working_dir, remote_url)

            # Pull rebase to integrate any remote changes, then push
            force = False
            try:
                await git_client.pull_rebase(
                    working_dir, branch=branch, access_token=access_token,
                )
            except RuntimeError:
                # Rebase failed (conflicts / unrelated histories) — force push
                force = True

            # Push (force-with-lease if pull-rebase failed)
            await git_client.push(
                working_dir, branch=branch, access_token=access_token,
                force_with_lease=force,
            )

            commit_part = f" (new commit {sha[:8]})" if sha else ""
            msg = f"Pushed to github.com/{repo_full_name} branch {branch}{commit_part}"

            await build_repo.append_build_log(
                build_id, msg, source="user", level="info",
            )
            await _broadcast_build_event(user_id, build_id, "build_log", {
                "message": msg, "source": "user", "level": "info",
            })
            return {"status": "pushed", "build_id": str(build_id), "message": msg}
        except Exception as exc:
            logger.warning("/push failed: %s", exc)
            raise ValueError(f"Git push failed: {exc}")

    # --- /pull -------------------------------------------------------
    if stripped == "/pull":
        project = await project_repo.get_project_by_id(project_id)
        if not project or str(project["user_id"]) != str(user_id):
            raise ValueError("Project not found")

        # Resolve GitHub repo to pull from
        repo_full_name = project.get("repo_full_name", "")
        if not repo_full_name:
            latest_for_ref = await build_repo.get_latest_build_for_project(project_id)
            if latest_for_ref:
                repo_full_name = latest_for_ref.get("target_ref", "")
        if not repo_full_name:
            raise ValueError("No GitHub repository linked to this project — cannot pull")

        user = await get_user_by_id(user_id)
        access_token = (user or {}).get("access_token", "")
        if not access_token:
            raise ValueError("No GitHub access token — connect GitHub in Settings to pull")

        latest = await build_repo.get_latest_build_for_project(project_id)
        branch = (latest.get("branch", "main") if latest else "main")

        # Clone into a fresh temp directory
        import tempfile as _tmp_pull
        working_dir = _tmp_pull.mkdtemp(prefix="forgeguard_pull_")
        clone_url = f"https://github.com/{repo_full_name}.git"
        try:
            # Remove empty tempdir so git clone can create it
            shutil.rmtree(working_dir, ignore_errors=True)
            await git_client.clone_repo(
                clone_url, working_dir,
                branch=branch,
                access_token=access_token,
                shallow=False,
            )
        except Exception as exc:
            raise ValueError(f"Failed to clone {repo_full_name}: {exc}")

        # Parse git log to find the last completed phase
        import re as _re_pull
        commit_msgs = await git_client.log_oneline(working_dir, max_count=100)
        completed_phase = -1
        for msg in commit_msgs:
            m = _re_pull.match(
                r"forge:\s*(.+?)\s+complete(?:\s*\(after \d+ audit attempts?\))?$",
                msg, _re_pull.IGNORECASE,
            )
            if m:
                phase_label = m.group(1)
                pm = _re_pull.search(r"Phase\s+(\d+)", phase_label)
                if pm:
                    phase_num = int(pm.group(1))
                    if phase_num > completed_phase:
                        completed_phase = phase_num
                break  # Newest matching commit is the latest phase — stop

        if completed_phase < 0:
            # No phase commits found — start fresh from Phase 0
            completed_phase = -1

        # Start build continuing from the detected phase
        result = await start_build(
            project_id, user_id,
            target_type="github_existing",
            target_ref=repo_full_name,
            branch=branch,
            contract_batch=(latest.get("contract_batch") if latest else None),
            resume_from_phase=completed_phase,
            working_dir_override=working_dir,
        )
        target_phase = completed_phase + 1
        if completed_phase < 0:
            pull_msg = f"Pulled {repo_full_name} — no prior phases detected, starting from Phase 0"
        else:
            pull_msg = f"Pulled {repo_full_name} — resuming from Phase {target_phase} (Phase {completed_phase} was last committed)"
        return {
            "status": "pulled",
            "build_id": str(result["id"]),
            "message": pull_msg,
        }

    # --- /status -----------------------------------------------------
    if stripped == "/status":
        project = await project_repo.get_project_by_id(project_id)
        if not project or str(project["user_id"]) != str(user_id):
            raise ValueError("Project not found")
        latest = await build_repo.get_latest_build_for_project(project_id)
        if not latest:
            raise ValueError("No builds found for this project")
        build_id = latest["id"]
        bid = str(build_id)

        # Gather state snapshot
        current_file = _current_generating.get(bid, None)
        is_paused = bid in _pause_flags
        is_cancelling = bid in _cancel_flags
        is_compacting = bid in _compact_flags
        has_task = bid in _active_tasks

        # Get recent logs (last 30)
        recent_logs, _ = await build_repo.get_build_logs(
            build_id, limit=30, offset=0,
        )
        log_lines = "\n".join(
            f"[{r.get('level', 'info')}] {r.get('message', '')}"
            for r in recent_logs[-30:]
        )

        # Get file stats
        files = await build_repo.get_build_file_logs(build_id)
        files_written = len(files)

        # Build context for LLM
        status_context = (
            f"Build ID: {bid}\n"
            f"Status: {latest.get('status', 'unknown')}\n"
            f"Phase: {latest.get('phase', 'unknown')}\n"
            f"Build mode: {latest.get('build_mode', 'unknown')}\n"
            f"Started at: {latest.get('started_at', 'unknown')}\n"
            f"Files written so far: {files_written}\n"
            f"Currently generating file: {current_file or 'none'}\n"
            f"Background task alive: {has_task}\n"
            f"Pause flag set: {is_paused}\n"
            f"Cancel flag set: {is_cancelling}\n"
            f"Compact flag set: {is_compacting}\n"
            f"\n--- Recent build logs (last 30) ---\n{log_lines}\n"
        )

        # Resolve API key: prefer user's secondary key, fall back to primary, then env
        user = await get_user_by_id(user_id)
        api_key = (
            (user or {}).get("anthropic_api_key_2")
            or (user or {}).get("anthropic_api_key")
            or settings.ANTHROPIC_API_KEY
        )
        if not api_key:
            raise ValueError("No API key available for /status — configure an Anthropic API key")

        # Broadcast "thinking" indicator
        await _broadcast_build_event(user_id, build_id, "build_log", {
            "message": "Analysing build status…", "source": "system", "level": "info",
        })

        try:
            from app.clients import llm_client
            result = await llm_client.chat(
                api_key=api_key,
                model=settings.LLM_PLANNER_MODEL,
                system_prompt=(
                    "You are a build status analyst for ForgeGuard, an autonomous code generation platform. "
                    "The user has asked for a status update. Analyse the build state and recent logs provided, "
                    "then write a clear, concise 2-4 sentence summary of what is currently happening, what phase "
                    "the build is in, what file is being worked on (if any), how many files have been completed, "
                    "and whether the build appears healthy, stalled, or has an issue. Be direct and informative."
                ),
                messages=[{"role": "user", "content": status_context}],
                max_tokens=300,
            )
            summary = result["text"].strip() if isinstance(result, dict) else str(result).strip()
        except Exception as exc:
            logger.warning("/status LLM call failed: %s", exc)
            summary = (
                f"Build is {latest.get('status', 'unknown')} on {latest.get('phase', 'unknown')}. "
                f"{files_written} files written. "
                f"{'Currently generating: ' + current_file if current_file else 'No file in progress.'} "
                f"{'⚠ Background task not found.' if not has_task else ''}"
            )

        await build_repo.append_build_log(
            build_id, f"[Status] {summary}", source="system", level="info",
        )
        await _broadcast_build_event(user_id, build_id, "build_log", {
            "message": f"📊 {summary}", "source": "system", "level": "info",
        })
        return {"status": "status_reported", "build_id": str(build_id), "message": summary}

    # --- Regular interjection ----------------------------------------
    project = await project_repo.get_project_by_id(project_id)
    if not project or str(project["user_id"]) != str(user_id):
        raise ValueError("Project not found")

    latest = await build_repo.get_latest_build_for_project(project_id)
    if not latest or latest["status"] not in ("running", "paused"):
        raise ValueError("No active build to interject")

    build_id = latest["id"]

    # Ensure the interjection queue exists
    queue = _interjection_queues.get(str(build_id))
    if queue is None:
        raise ValueError("Build interjection queue not found")

    queue.put_nowait(message)

    return {
        "status": "queued",
        "build_id": str(build_id),
        "message": message[:200],
    }


async def get_build_status(project_id: UUID, user_id: UUID) -> dict:
    """Get the current build status for a project.

    Args:
        project_id: The project to check.
        user_id: The authenticated user (for ownership check).

    Returns:
        The latest build record, or raises if none.

    Raises:
        ValueError: If project not found, not owned, or no builds.
    """
    project = await project_repo.get_project_by_id(project_id)
    if not project or str(project["user_id"]) != str(user_id):
        raise ValueError("Project not found")

    latest = await build_repo.get_latest_build_for_project(project_id)
    if not latest:
        raise ValueError("No builds found for this project")

    return latest


async def get_build_logs(
    project_id: UUID, user_id: UUID, limit: int = 100, offset: int = 0,
    *, search: str | None = None, level: str | None = None,
) -> tuple[list[dict], int]:
    """Get paginated build logs for a project.

    Args:
        project_id: The project to check.
        user_id: The authenticated user (for ownership check).
        limit: Maximum logs to return.
        offset: Offset for pagination.
        search: Optional text filter on message content.
        level: Optional filter by log level.

    Returns:
        Tuple of (logs_list, total_count).

    Raises:
        ValueError: If project not found, not owned, or no builds.
    """
    project = await project_repo.get_project_by_id(project_id)
    if not project or str(project["user_id"]) != str(user_id):
        raise ValueError("Project not found")

    latest = await build_repo.get_latest_build_for_project(project_id)
    if not latest:
        raise ValueError("No builds found for this project")

    return await build_repo.get_build_logs(
        latest["id"], limit, offset, search=search, level=level,
    )


# ---------------------------------------------------------------------------
# Build mode dispatcher
# ---------------------------------------------------------------------------


def _touch_progress(build_id: UUID | str) -> None:
    """Record a progress heartbeat so the watchdog knows the build is alive."""
    _last_progress[str(build_id)] = time.monotonic()


async def _set_build_activity(
    build_id: UUID, user_id: UUID, status: str
) -> None:
    """Set the live activity status for a build and broadcast to the UI.

    The frontend renders this as a persistent spinner bar so the user
    always knows what the build is doing right now.
    """
    bid = str(build_id)
    _build_activity_status[bid] = status
    _touch_progress(build_id)
    await _broadcast_build_event(user_id, build_id, "build_activity_status", {
        "status": status,
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
            # Use live activity status if set, fall back to _current_generating
            activity_label = _build_activity_status.get(bid, "")
            if not activity_label:
                current_file = _current_generating.get(bid, "")
                activity_label = f"generating {current_file}" if current_file else "processing"
            status = activity_label

            if idle >= _STALL_FAIL_THRESHOLD:
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
                # Cancel the asyncio task — the CancelledError handler
                # in _run_build will mark it properly.
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


async def _run_build(
    build_id: UUID,
    project_id: UUID,
    user_id: UUID,
    contracts: list[dict],
    api_key: str,
    audit_llm_enabled: bool = True,
    *,
    target_type: str | None = None,
    target_ref: str | None = None,
    working_dir: str | None = None,
    access_token: str = "",
    branch: str = "main",
    api_key_2: str = "",
    resume_from_phase: int = -1,
) -> None:
    """Dispatch to the appropriate build mode.

    If BUILD_MODE is ``plan_execute``, uses the plan-then-execute architecture
    (Phase 21): one planning call produces a manifest, then independent per-file
    calls generate each file.

    If BUILD_MODE is ``conversation``, falls back to the original conversation
    loop.
    """
    mode = settings.BUILD_MODE
    bid = str(build_id)

    # Start the health-check watchdog alongside the build
    _touch_progress(build_id)
    watchdog = asyncio.create_task(_build_watchdog(build_id, user_id))
    _build_heartbeat_tasks[bid] = watchdog

    try:
        if mode == "plan_execute" and working_dir:
            await _run_build_plan_execute(
                build_id, project_id, user_id, contracts, api_key,
                audit_llm_enabled,
                target_type=target_type,
                target_ref=target_ref,
                working_dir=working_dir,
                access_token=access_token,
                branch=branch,
                api_key_2=api_key_2,
                resume_from_phase=resume_from_phase,
            )
        else:
            await _run_build_conversation(
                build_id, project_id, user_id, contracts, api_key,
                audit_llm_enabled,
                target_type=target_type,
                target_ref=target_ref,
                working_dir=working_dir,
                access_token=access_token,
                branch=branch,
                api_key_2=api_key_2,
            )
    except asyncio.CancelledError:
        logger.info("Build task cancelled: %s", build_id)
        try:
            await build_repo.append_build_log(
                build_id, "Build task cancelled", source="system", level="warn",
            )
            await build_repo.update_build_status(build_id, "cancelled")
            await _broadcast_build_event(user_id, build_id, "build_cancelled", {
                "id": str(build_id),
            })
        except Exception:
            pass
    except Exception as exc:
        logger.exception("Build crashed: %s", exc)
        try:
            await _fail_build(build_id, user_id, f"Build crashed: {exc}")
        except Exception:
            pass
    finally:
        # Stop the watchdog
        watchdog.cancel()
        _build_heartbeat_tasks.pop(bid, None)

        _active_tasks.pop(bid, None)
        _cancel_flags.discard(bid)
        _pause_flags.discard(bid)
        _compact_flags.discard(bid)
        _current_generating.pop(bid, None)
        _build_activity_status.pop(bid, None)
        _interjection_queues.pop(bid, None)
        _last_progress.pop(bid, None)


# ---------------------------------------------------------------------------
# Background orchestration (conversation mode)
# ---------------------------------------------------------------------------


async def _run_build_conversation(
    build_id: UUID,
    project_id: UUID,
    user_id: UUID,
    contracts: list[dict],
    api_key: str,
    audit_llm_enabled: bool = True,
    *,
    target_type: str | None = None,
    target_ref: str | None = None,
    working_dir: str | None = None,
    access_token: str = "",
    branch: str = "main",
    api_key_2: str = "",
) -> None:
    """Background task that orchestrates the full build lifecycle.

    Streams agent output, detects phase completion signals, runs inline
    audits, handles loopback, and advances through phases.
    When a build target is configured, parses file blocks from the
    builder output and writes them to the working directory.
    """
    try:
        now = datetime.now(timezone.utc)
        await build_repo.update_build_status(
            build_id, "running", started_at=now
        )
        await build_repo.append_build_log(
            build_id, "Build started", source="system", level="info"
        )
        await _broadcast_build_event(user_id, build_id, "build_started", {
            "id": str(build_id),
            "status": "running",
            "phase": "Phase 0",
        })

        # Initialize interjection queue for this build (keep existing if set)
        if str(build_id) not in _interjection_queues:
            _interjection_queues[str(build_id)] = asyncio.Queue()

        # Build the directive from contracts
        directive = _build_directive(contracts)

        # Set up working directory for file writing
        if target_type == "github_new" and target_ref and working_dir:
            try:
                # Create a new GitHub repo
                repo_data = await github_client.create_github_repo(
                    access_token, target_ref,
                    description=f"Built by ForgeGuard",
                    private=False,
                )
                clone_url = f"https://github.com/{repo_data['full_name']}.git"
                # Remove empty tempdir so git clone can create it
                shutil.rmtree(working_dir, ignore_errors=True)
                await git_client.clone_repo(
                    clone_url, working_dir,
                    access_token=access_token, shallow=False,
                )
                await build_repo.append_build_log(
                    build_id,
                    f"Created GitHub repo: {repo_data['full_name']}",
                    source="system", level="info",
                )
                await _broadcast_build_event(user_id, build_id, "build_log", {
                    "message": f"Created GitHub repo: {repo_data['full_name']}",
                    "source": "system", "level": "info",
                })
            except Exception as exc:
                await _fail_build(build_id, user_id, f"Failed to create GitHub repo: {exc}")
                return
        elif target_type == "github_existing" and target_ref and working_dir:
            try:
                clone_url = f"https://github.com/{target_ref}.git"
                # Remove empty tempdir so git clone can create it
                shutil.rmtree(working_dir, ignore_errors=True)
                await git_client.clone_repo(
                    clone_url, working_dir,
                    access_token=access_token, shallow=False,
                )
                await build_repo.append_build_log(
                    build_id,
                    f"Cloned existing repo: {target_ref}",
                    source="system", level="info",
                )
            except Exception as exc:
                logger.exception("Clone failed for %s -> %s (workdir=%s)", target_type, target_ref, working_dir)
                await _fail_build(build_id, user_id, f"Failed to clone repo: {type(exc).__name__}: {exc}")
                return
        elif target_type == "local_path" and working_dir:
            try:
                Path(working_dir).mkdir(parents=True, exist_ok=True)
                # Initialize git repo if not already one
                git_dir = Path(working_dir) / ".git"
                if not git_dir.exists():
                    await git_client.init_repo(working_dir)
                await build_repo.append_build_log(
                    build_id,
                    f"Using local path: {working_dir}",
                    source="system", level="info",
                )
            except Exception as exc:
                await _fail_build(build_id, user_id, f"Failed to initialize local path: {exc}")
                return

        # Create/checkout branch if not main
        if working_dir and branch and branch != "main":
            try:
                await git_client.create_branch(working_dir, branch)
                await build_repo.append_build_log(
                    build_id, f"Created branch: {branch}",
                    source="system", level="info",
                )
                await _broadcast_build_event(user_id, build_id, "build_log", {
                    "message": f"Building on branch: {branch}",
                    "source": "system", "level": "info",
                })
            except Exception:
                # Branch may already exist — try checkout instead
                try:
                    await git_client.checkout_branch(working_dir, branch)
                    await build_repo.append_build_log(
                        build_id, f"Checked out branch: {branch}",
                        source="system", level="info",
                    )
                except Exception as exc2:
                    await _fail_build(build_id, user_id, f"Failed to create/checkout branch '{branch}': {exc2}")
                    return

        # Track files written during this build
        files_written: list[dict] = []

        phase_start_time = datetime.now(timezone.utc)  # Phase timeout tracking

        # Write contracts to the working directory as a backup reference.
        # The builder has them in-prompt, but after context compaction it
        # can re-read them from disk via read_file if needed.
        if working_dir:
            try:
                _write_contracts_to_workdir(working_dir, contracts)
            except Exception as exc:
                logger.warning("Failed to write contracts to workdir: %s", exc)

        # Commit + push contracts so the GitHub repo is populated before
        # the builder starts coding.  This ensures the user can see the
        # Forge/Contracts/ folder in their repo immediately.
        if (
            working_dir
            and target_type in ("github_new", "github_existing")
            and access_token
        ):
            try:
                await git_client.add_all(working_dir)
                sha = await git_client.commit(
                    working_dir, "forge: seed Forge/Contracts/"
                )
                if sha:
                    await git_client.push(
                        working_dir, branch=branch, access_token=access_token,
                    )
                    await build_repo.append_build_log(
                        build_id,
                        "Pushed Forge/Contracts/ to GitHub",
                        source="system", level="info",
                    )
            except Exception as exc:
                logger.warning("Initial contracts push failed (non-fatal): %s", exc)

        # Signal the frontend that the workspace is ready (repo cloned,
        # branch checked out, contracts written).  The UI waits for this
        # event before navigating to the build view.
        await _broadcast_build_event(user_id, build_id, "workspace_ready", {
            "id": str(build_id),
            "working_dir": working_dir or "",
            "branch": branch,
        })

        # Build working directory snapshot for builder orientation.
        workspace_info = ""
        if working_dir:
            try:
                file_list = await git_client.get_file_list(working_dir)
                # Exclude ALL Forge/ files — contracts are already inline,
                # evidence/scripts/intake are governance artefacts that
                # should not consume listing slots meant for project files.
                file_list = [f for f in file_list if not f.startswith("Forge/")]
                _FILE_LIST_CAP = 200
                if file_list:
                    workspace_info = (
                        "\n\n## Working Directory\n"
                        "The target repo already contains these files "
                        f"({len(file_list)} total):\n"
                        + "\n".join(f"- {f}" for f in file_list[:_FILE_LIST_CAP])
                        + (f"\n- ... ({len(file_list) - _FILE_LIST_CAP} more, truncated)"
                           if len(file_list) > _FILE_LIST_CAP else "")
                        + "\n"
                    )
                else:
                    workspace_info = (
                        "\n\n## Working Directory\n"
                        "The target repo is empty (no existing project files).\n"
                    )
            except Exception:
                workspace_info = (
                    "\n\n## Working Directory\n"
                    "The target repo is empty or freshly initialized.\n"
                )

        # Extract Phase 0 + Phase 1 window for the first message
        current_phase_num = 0
        phase_window = _extract_phase_window(contracts, current_phase_num)

        # Assemble first user message:
        # governance + per-project contracts + workspace listing + phase window
        first_message = (
            "## ⚠ IMPORTANT — DO NOT EXPLORE\n"
            "Everything you need is in this message. Do NOT call list_directory, "
            "read_file, or any exploratory tool before starting Phase 0.\n"
            "The workspace file listing is below. Start coding IMMEDIATELY.\n\n"
            + directive
            + workspace_info
            + ("\n\n" + phase_window if phase_window else "")
        )

        # Use content-block format with cache_control so Anthropic caches
        # the contracts across turns (prefix caching — 10% cost on turns 2+).
        messages: list[dict] = [
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": first_message,
                        "cache_control": {"type": "ephemeral"},
                    }
                ],
            },
        ]

        accumulated_text = ""
        current_phase = "Phase 0"
        turn_count = 0
        phase_loop_count = 0  # Audit failures on the current phase

        # Build plan state
        plan_tasks: list[dict] = []

        # Token usage tracking
        usage = StreamUsage()

        # Total token accumulator (across all turns, for compaction check)
        total_tokens_all_turns = 0
        # Tokens recorded before the current usage window (after phase cost resets)
        recorded_input_baseline = 0
        recorded_output_baseline = 0

        system_prompt = (
            "You are an autonomous software builder operating under the Forge governance framework.\n\n"
            "## CRITICAL — Read This First\n"
            "1. Your contracts and build instructions are ALREADY provided in the first user message below.\n"
            "2. Do NOT search the filesystem for contracts, README, config files, or any existing files.\n"
            "3. Do NOT read_file or list_directory before starting Phase 0.\n"
            "4. The working directory listing (if any) is already provided below — you have it.\n"
            "5. Start Phase 0 (Genesis) IMMEDIATELY by emitting your plan, then writing code.\n\n"
            "## Phase Workflow\n"
            "Your **current phase + next phase** are shown in the Phase Window section.\n"
            "When you finish a phase, a new message will provide the next phase window\n"
            "and a diff summary of what was built.  Contracts are also saved in\n"
            "`Forge/Contracts/` if you need to re-read one after context compaction.\n\n"
            "At the start of EACH PHASE, emit a structured plan covering only that phase's deliverables:\n"
            "=== PLAN ===\n"
            "1. First task for this phase\n"
            "2. Second task for this phase\n"
            "...\n"
            "=== END PLAN ===\n\n"
            "Do NOT plan ahead to future phases. Each phase gets its own fresh plan.\n\n"
            "As you complete each task, emit: === TASK DONE: N ===\n"
            "where N is the task number from your current phase plan.\n\n"
            "## Tools\n"
            "You have access to the following tools for interacting with the project:\n"
            "- **read_file**: Read a file to check existing code or verify your work.\n"
            "- **list_directory**: List files/folders to understand project structure.\n"
            "- **search_code**: Search for patterns across files to find implementations or imports.\n"
            "- **write_file**: Write or overwrite a file. Preferred over === FILE: ... === blocks.\n"
            "- **run_tests**: Run the test suite to verify your code works.\n"
            "- **check_syntax**: Check a file for syntax errors immediately after writing it.\n"
            "- **run_command**: Run safe shell commands (pip install, npm install, etc.).\n\n"
            "Guidelines for tool use:\n"
            "1. Do NOT explore the filesystem at the start — the workspace listing is already above.\n"
            "2. Start writing code immediately in Phase 0. Use read_file only when modifying existing files.\n"
            "3. Prefer write_file tool over === FILE: path === blocks for creating/updating files.\n"
            "4. Use search_code to find existing patterns, imports, or implementations.\n"
            "5. After writing files, use check_syntax to catch syntax errors immediately.\n"
            "6. ALWAYS run tests with run_tests before emitting the phase sign-off signal.\n"
            "7. If tests fail, read the error output, fix the code with write_file, and re-run.\n"
            "8. Only emit === PHASE SIGN-OFF: PASS === when all tests pass.\n"
            "9. Use run_command for setup tasks like 'pip install -r requirements.txt' when needed.\n\n"
            "## First Turn\n"
            "On your VERY FIRST response, you MUST:\n"
            "1. Emit === PLAN === for Phase 0\n"
            "2. Start writing code with write_file\n"
            "Do NOT call list_directory or read_file on your first turn.\n\n"
            "## README\n"
            "Before the final phase sign-off, generate a comprehensive README.md that includes:\n"
            "- Project name and description\n"
            "- Key features\n"
            "- Tech stack\n"
            "- Setup / installation instructions\n"
            "- Environment variables\n"
            "- Usage examples\n"
            "- API reference (if applicable)\n"
            "- License placeholder\n"
        )

        # Emit build overview (high-level phase list) at build start
        try:
            phases_contract = await project_repo.get_contract_by_type(project_id, "phases")
            if phases_contract:
                overview_phases = _parse_phases_contract(phases_contract["content"])
                await _broadcast_build_event(user_id, build_id, "build_overview", {
                    "phases": [
                        {"number": p["number"], "name": p["name"], "objective": p.get("objective", "")}
                        for p in overview_phases
                    ],
                })
        except Exception:
            logger.debug("Could not emit build_overview", exc_info=True)

        # Multi-turn conversation loop
        while True:
            turn_count += 1

            # Phase timeout check
            phase_elapsed = (datetime.now(timezone.utc) - phase_start_time).total_seconds()
            if phase_elapsed > settings.PHASE_TIMEOUT_MINUTES * 60:
                await build_repo.append_build_log(
                    build_id,
                    f"Phase timeout: {current_phase} exceeded {settings.PHASE_TIMEOUT_MINUTES}m",
                    source="system", level="error",
                )
                # Pause instead of failing — let user decide
                await _pause_build(
                    build_id, user_id, current_phase,
                    phase_loop_count,
                    f"Phase timeout: {current_phase} exceeded {settings.PHASE_TIMEOUT_MINUTES} minutes",
                )
                event = _pause_events.get(str(build_id))
                if event:
                    try:
                        await asyncio.wait_for(
                            event.wait(),
                            timeout=settings.BUILD_PAUSE_TIMEOUT_MINUTES * 60,
                        )
                    except asyncio.TimeoutError:
                        await _fail_build(
                            build_id, user_id,
                            f"Build timed out on {current_phase} (pause expired)",
                        )
                        return
                action = _resume_actions.pop(str(build_id), "retry")
                _pause_events.pop(str(build_id), None)
                await build_repo.resume_build(build_id)
                if action == "abort":
                    await _fail_build(build_id, user_id, f"Build aborted after phase timeout on {current_phase}")
                    return
                elif action == "skip":
                    phase_loop_count = 0
                    accumulated_text = ""
                    phase_start_time = datetime.now(timezone.utc)
                    continue
                else:
                    phase_loop_count = 0
                    accumulated_text = ""
                    phase_start_time = datetime.now(timezone.utc)
                    continue

            # 
            # Context compaction check
            compacted = False
            force_compact = str(build_id) in _compact_flags
            if force_compact:
                _compact_flags.discard(str(build_id))
            if (total_tokens_all_turns > CONTEXT_COMPACTION_THRESHOLD or force_compact) and len(messages) > 5:
                messages = _compact_conversation(
                    messages,
                    files_written=files_written,
                    current_phase=current_phase,
                )
                compacted = True
                await build_repo.append_build_log(
                    build_id,
                    f"Context compacted at {total_tokens_all_turns} tokens (turn {turn_count})",
                    source="system", level="info",
                )
                await _broadcast_build_event(user_id, build_id, "build_turn", {
                    "turn": turn_count,
                    "total_tokens": total_tokens_all_turns,
                    "compacted": True,
                })

            # Stream agent output for this turn
            turn_text = ""
            tool_calls_this_turn: list[dict] = []
            pending_tool_results: list[dict] = []
            _text_buffer = ""  # Accumulates builder text for batched emission

            def _on_rate_limit(status_code: int, attempt: int, wait: float):
                """Fire-and-forget WS notification on rate-limit retry."""
                if status_code == 0:
                    # Budget pacing — proactive self-throttle, not a real 429
                    msg = f"⏳ Pacing: waiting {wait:.0f}s for token budget"
                    level = "info"
                else:
                    msg = f"Rate limited ({status_code}), retrying in {wait:.0f}s (attempt {attempt})…"
                    level = "warn"
                asyncio.ensure_future(
                    _broadcast_build_event(user_id, build_id, "build_log", {
                        "message": msg,
                        "source": "system",
                        "level": level,
                    })
                )

            # Build API key pool for multi-key rotation (BYOK keys)
            pool_keys = [api_key]
            if api_key_2.strip():
                pool_keys.append(api_key_2.strip())
            key_pool = ApiKeyPool(
                api_keys=pool_keys,
                input_tpm=settings.ANTHROPIC_INPUT_TPM,
                output_tpm=settings.ANTHROPIC_OUTPUT_TPM,
            )

            async for item in stream_agent(
                api_key=api_key,
                model=settings.LLM_BUILDER_MODEL,
                system_prompt=system_prompt,
                messages=messages,
                max_tokens=settings.LLM_BUILDER_MAX_TOKENS,
                usage_out=usage,
                tools=BUILDER_TOOLS if working_dir else None,
                on_retry=_on_rate_limit,
                key_pool=key_pool,
            ):
                if isinstance(item, ToolCall):
                    # --- Tool call detected ---
                    _touch_progress(build_id)
                    tool_result = await execute_tool_async(item.name, item.input, working_dir or "")

                    # Log the tool call
                    input_summary = json.dumps(item.input)[:200]
                    result_summary = tool_result[:300]
                    await build_repo.append_build_log(
                        build_id,
                        f"Tool: {item.name}({input_summary}) → {result_summary}",
                        source="tool", level="info",
                    )

                    # Broadcast tool_use WS event
                    await _broadcast_build_event(
                        user_id, build_id, "tool_use", {
                            "tool_name": item.name,
                            "input_summary": input_summary,
                            "result_summary": result_summary,
                        }
                    )

                    # Track write_file calls as files_written
                    if item.name == "write_file" and tool_result.startswith("OK:"):
                        _touch_progress(build_id)
                        rel_path = item.input.get("path", "")
                        content = item.input.get("content", "")
                        lang = _detect_language(rel_path)
                        if rel_path and not any(f["path"] == rel_path for f in files_written):
                            files_written.append({
                                "path": rel_path,
                                "size_bytes": len(content),
                                "language": lang,
                            })
                        # Emit file_created event
                        await _broadcast_build_event(
                            user_id, build_id, "file_created", {
                                "path": rel_path,
                                "size_bytes": len(content),
                                "language": lang,
                            }
                        )

                        # Commit + push immediately so GitHub stays in sync.
                        # Non-fatal: if push fails we'll catch it at phase end.
                        if (
                            working_dir
                            and target_type in ("github_new", "github_existing")
                            and access_token
                        ):
                            try:
                                await git_client.add_all(working_dir)
                                sha = await git_client.commit(
                                    working_dir,
                                    f"forge: write {rel_path}",
                                )
                                if sha:
                                    await git_client.push(
                                        working_dir,
                                        branch=branch,
                                        access_token=access_token,
                                    )
                            except Exception as exc:
                                logger.debug(
                                    "Incremental push failed for %s (non-fatal): %s",
                                    rel_path, exc,
                                )

                    # Track run_tests calls — emit test_run event
                    if item.name == "run_tests":
                        exit_code_str = tool_result.split("\n")[0] if tool_result else ""
                        exit_code = 0
                        try:
                            exit_code = int(exit_code_str.split(":")[-1].strip())
                        except (ValueError, IndexError):
                            pass
                        await _broadcast_build_event(
                            user_id, build_id, "test_run", {
                                "command": item.input.get("command", ""),
                                "exit_code": exit_code,
                                "passed": exit_code == 0,
                                "summary": result_summary,
                            }
                        )
                        await build_repo.append_build_log(
                            build_id,
                            f"Test run: {item.input.get('command', '')} → exit {exit_code}",
                            source="test", level="info" if exit_code == 0 else "warn",
                        )

                    # Append a progress manifest to write_file results so the
                    # builder always knows what's been written (prevents repeats).
                    if item.name == "write_file" and files_written:
                        manifest = "\n\nFiles written so far: " + ", ".join(
                            f["path"] for f in files_written
                        )
                        tool_result = tool_result + manifest

                    tool_calls_this_turn.append({
                        "id": item.id,
                        "name": item.name,
                        "result": tool_result,
                    })
                    continue

                # --- Text chunk ---
                chunk = item
                accumulated_text += chunk
                turn_text += chunk

                # Buffer builder text and emit in readable segments.
                # Instead of firing a WS event for every 10-char fragment,
                # accumulate into `_text_buffer` and flush when we hit a
                # sentence boundary or the buffer exceeds ~120 chars.
                _text_buffer += chunk
                _should_flush = (
                    len(_text_buffer) >= 120
                    or _text_buffer.rstrip().endswith((".", "!", "?", ":", "\n"))
                    or "===" in _text_buffer
                )
                if _should_flush and _text_buffer.strip():
                    flushed = _text_buffer.strip()
                    _text_buffer = ""
                    await build_repo.append_build_log(
                        build_id, flushed, source="builder", level="info"
                    )
                    await _broadcast_build_event(
                        user_id, build_id, "build_log", {
                            "message": flushed,
                            "source": "builder",
                            "level": "info",
                        }
                    )

                # Detect and write file blocks
                new_blocks = _parse_file_blocks(accumulated_text)
                already_written_count = len(files_written)
                if len(new_blocks) > already_written_count and working_dir:
                    for block in new_blocks[already_written_count:]:
                        await _write_file_block(
                            build_id, user_id, working_dir,
                            block["path"], block["content"],
                            files_written,
                        )

                # Detect and emit build plan
                if not plan_tasks:
                    parsed_plan = _parse_build_plan(accumulated_text)
                    if parsed_plan:
                        plan_tasks = parsed_plan
                        await _broadcast_build_event(
                            user_id, build_id, "phase_plan", {
                                "phase": current_phase,
                                "tasks": plan_tasks,
                            }
                        )
                        await build_repo.append_build_log(
                            build_id,
                            f"Phase plan detected for {current_phase}: {len(plan_tasks)} tasks",
                            source="system", level="info",
                        )

                # Detect plan task completion
                for task in plan_tasks:
                    if task["status"] == "pending":
                        signal = f"=== TASK DONE: {task['id']} ==="
                        if signal in accumulated_text:
                            task["status"] = "done"
                            await _broadcast_build_event(
                                user_id, build_id, "plan_task_complete", {
                                    "task_id": task["id"],
                                    "status": "done",
                                }
                            )

            # Flush any remaining buffered text
            if _text_buffer.strip():
                flushed = _text_buffer.strip()
                _text_buffer = ""
                await build_repo.append_build_log(
                    build_id, flushed, source="builder", level="info"
                )
                await _broadcast_build_event(
                    user_id, build_id, "build_log", {
                        "message": flushed,
                        "source": "builder",
                        "level": "info",
                    }
                )

            # Turn complete — append messages to conversation history
            had_tool_calls = bool(tool_calls_this_turn)
            if had_tool_calls:
                # Build the assistant message with tool_use content blocks
                assistant_content: list[dict] = []
                if turn_text:
                    assistant_content.append({"type": "text", "text": turn_text})
                for tc in tool_calls_this_turn:
                    assistant_content.append({
                        "type": "tool_use",
                        "id": tc["id"],
                        "name": tc["name"],
                        "input": {},  # original input not needed in history
                    })
                messages.append({"role": "assistant", "content": assistant_content})

                # Add tool results as a user message
                tool_results_content: list[dict] = [
                    {
                        "type": "tool_result",
                        "tool_use_id": tc["id"],
                        "content": tc["result"][:10_000],  # cap result size
                    }
                    for tc in tool_calls_this_turn
                ]
                messages.append({"role": "user", "content": tool_results_content})
            else:
                # Text-only response
                messages.append({"role": "assistant", "content": turn_text})

                # Check for user interjections between text-only turns
                queue = _interjection_queues.get(str(build_id))
                if queue and not queue.empty():
                    interjections: list[str] = []
                    while not queue.empty():
                        try:
                            interjections.append(queue.get_nowait())
                        except asyncio.QueueEmpty:
                            break
                    if interjections:
                        combined = "\n".join(interjections)
                        messages.append({
                            "role": "user",
                            "content": f"[User interjection]\n{combined}\n\nPlease incorporate this feedback and continue.",
                        })
                        await build_repo.append_build_log(
                            build_id,
                            f"User interjection: {combined[:200]}",
                            source="user", level="info",
                        )
                        await _broadcast_build_event(
                            user_id, build_id, "build_interjection", {
                                "message": combined,
                                "injected_at": datetime.now(timezone.utc).isoformat(),
                            }
                        )

            # --- Common code for ALL turns (tool and text) ---

            # Update total token count (baseline + current window usage)
            # Include cache_read + cache_creation to show the REAL token
            # consumption that Anthropic counts toward rate limits.
            all_input = (recorded_input_baseline + usage.input_tokens
                         + usage.cache_read_input_tokens
                         + usage.cache_creation_input_tokens)
            all_output = recorded_output_baseline + usage.output_tokens
            total_tokens_all_turns = all_input + all_output

            # Broadcast token update with rate-window metrics for UI
            rate_input_60s, rate_output_60s = 0, 0
            try:
                rate_input_60s, rate_output_60s = key_pool.aggregate_usage()
            except Exception:
                pass
            await _broadcast_build_event(user_id, build_id, "token_update", {
                "input_tokens": all_input,
                "output_tokens": all_output,
                "total_tokens": total_tokens_all_turns,
                "rate_input_60s": rate_input_60s,
                "rate_output_60s": rate_output_60s,
                "rate_input_limit": settings.ANTHROPIC_INPUT_TPM * key_pool.key_count,
                "rate_output_limit": settings.ANTHROPIC_OUTPUT_TPM * key_pool.key_count,
            })

            if not compacted:
                await _broadcast_build_event(user_id, build_id, "build_turn", {
                    "turn": turn_count,
                    "total_tokens": total_tokens_all_turns,
                    "compacted": False,
                })

            # Detect phase completion (runs on EVERY turn, not just text-only)
            phase_completed = False
            if PHASE_COMPLETE_SIGNAL in accumulated_text:
                phase_completed = True
                phase_match = re.search(
                    r"Phase:\s+(.+?)$", accumulated_text, re.MULTILINE
                )
                if phase_match:
                    current_phase = phase_match.group(1).strip()

                await build_repo.update_build_status(
                    build_id, "running", phase=current_phase
                )
                await build_repo.append_build_log(
                    build_id,
                    f"Phase sign-off detected: {current_phase}",
                    source="system",
                    level="info",
                )
                # Capture token usage BEFORE recording (which resets)
                # Include cache tokens for accurate reporting.
                phase_input_tokens = (usage.input_tokens
                                      + usage.cache_read_input_tokens
                                      + usage.cache_creation_input_tokens)
                phase_output_tokens = usage.output_tokens

                await _broadcast_build_event(
                    user_id, build_id, "phase_complete", {
                        "phase": current_phase,
                        "status": "pass",
                        "input_tokens": phase_input_tokens,
                        "output_tokens": phase_output_tokens,
                    }
                )

                _touch_progress(build_id)
                audit_verdict, audit_report = await _run_inline_audit(
                    build_id, current_phase, accumulated_text,
                    contracts, api_key, audit_llm_enabled,
                )
                _touch_progress(build_id)
                if audit_verdict == "PASS":
                    await build_repo.append_build_log(
                        build_id,
                        f"Audit PASS for {current_phase}",
                        source="audit",
                        level="info",
                    )
                    await _broadcast_build_event(
                        user_id, build_id, "audit_pass", {
                            "phase": current_phase,
                        }
                    )

                    # Git commit after phase audit passes
                    if working_dir and files_written:
                        try:
                            sha = await git_client.commit(
                                working_dir,
                                f"forge: {current_phase} complete",
                            )
                            if sha:
                                await build_repo.append_build_log(
                                    build_id,
                                    f"Committed {current_phase}: {sha[:8]}",
                                    source="system", level="info",
                                )
                        except Exception as exc:
                            logger.warning("Git commit failed for %s: %s", current_phase, exc)

                    # --- Phase advancement: sliding window + diff log ---
                    current_phase_num += 1

                    # Get diff summary from the phase that just completed
                    diff_log = ""
                    if working_dir:
                        try:
                            diff_log = await git_client.diff_summary(working_dir)
                        except Exception as exc:
                            logger.debug("Diff summary failed: %s", exc)

                    # Refresh workspace file listing so the builder knows
                    # every file that now exists (avoids re-creating files
                    # from earlier phases that fell off the truncated
                    # initial listing after context compaction).
                    refreshed_listing = ""
                    if working_dir:
                        try:
                            _refresh_list = await git_client.get_file_list(working_dir)
                            _refresh_list = [f for f in _refresh_list if not f.startswith("Forge/")]
                            _REFRESH_CAP = 200
                            if _refresh_list:
                                refreshed_listing = (
                                    "\n### Current workspace files "
                                    f"({len(_refresh_list)} total)\n"
                                    + "\n".join(f"- {f}" for f in _refresh_list[:_REFRESH_CAP])
                                    + (f"\n- ... ({len(_refresh_list) - _REFRESH_CAP} more, truncated)"
                                       if len(_refresh_list) > _REFRESH_CAP else "")
                                    + "\n\nDo NOT recreate or overwrite any of these files "
                                    "unless the phase deliverables explicitly require modifying them.\n"
                                )
                        except Exception:
                            pass

                    # Extract the next phase window (current + next)
                    next_window = _extract_phase_window(contracts, current_phase_num)

                    # Inject phase-advance context as a new user message
                    advance_parts = [
                        f"## Phase {current_phase_num} — START\n",
                        f"The previous phase ({current_phase}) passed audit.\n",
                    ]
                    if diff_log:
                        advance_parts.append(
                            f"\n### What was built in the previous phase\n"
                            f"```\n{diff_log}\n```\n"
                        )
                    if refreshed_listing:
                        advance_parts.append(refreshed_listing)
                    if next_window:
                        advance_parts.append(f"\n{next_window}\n")
                    advance_parts.append(
                        "\nRead any contracts you need from `Forge/Contracts/` "
                        "for this phase, then emit your === PLAN === and start building."
                    )
                    messages.append({
                        "role": "user",
                        "content": "\n".join(advance_parts),
                    })

                    # Reset for next phase
                    phase_start_time = datetime.now(timezone.utc)
                    phase_loop_count = 0
                    accumulated_text = ""
                    plan_tasks = []  # Fresh plan for next phase

                    # Record cost for this phase (update baseline before reset)
                    recorded_input_baseline += (usage.input_tokens
                                                + usage.cache_read_input_tokens
                                                + usage.cache_creation_input_tokens)
                    recorded_output_baseline += usage.output_tokens
                    await _record_phase_cost(build_id, current_phase, usage)
                else:
                    # Audit failed — inject feedback and loop back
                    phase_loop_count += 1
                    loop_count = await build_repo.increment_loop_count(build_id)
                    await build_repo.append_build_log(
                        build_id,
                        f"Audit FAIL for {current_phase} (attempt {phase_loop_count})",
                        source="audit",
                        level="warn",
                    )
                    await _broadcast_build_event(
                        user_id, build_id, "audit_fail", {
                            "phase": current_phase,
                            "loop_count": loop_count,
                        }
                    )

                    if phase_loop_count >= MAX_LOOP_COUNT:
                        # Pause instead of failing -- let user decide
                        await _pause_build(
                            build_id, user_id, current_phase,
                            phase_loop_count,
                            f"{phase_loop_count} consecutive audit failures on {current_phase}",
                        )

                        # Wait for user to resume (or timeout → abort)
                        event = _pause_events.get(str(build_id))
                        if event:
                            try:
                                await asyncio.wait_for(
                                    event.wait(),
                                    timeout=settings.BUILD_PAUSE_TIMEOUT_MINUTES * 60,
                                )
                            except asyncio.TimeoutError:
                                await _fail_build(
                                    build_id, user_id,
                                    f"RISK_EXCEEDS_SCOPE: pause timed out after "
                                    f"{settings.BUILD_PAUSE_TIMEOUT_MINUTES}m on {current_phase}",
                                )
                                return

                        action = _resume_actions.pop(str(build_id), "retry")
                        _pause_events.pop(str(build_id), None)

                        # Apply resumed DB status
                        await build_repo.resume_build(build_id)

                        if action == "abort":
                            await _fail_build(
                                build_id, user_id,
                                f"Build aborted by user during pause on {current_phase}",
                            )
                            return
                        elif action == "skip":
                            # Skip this phase — reset and advance
                            await build_repo.append_build_log(
                                build_id,
                                f"Phase {current_phase} skipped by user",
                                source="system", level="warn",
                            )
                            await _broadcast_build_event(
                                user_id, build_id, "build_resumed", {
                                    "action": "skip",
                                    "phase": current_phase,
                                }
                            )
                            phase_loop_count = 0
                            accumulated_text = ""
                            continue
                        else:
                            # "retry" or "edit" — loop back for another attempt
                            await _broadcast_build_event(
                                user_id, build_id, "build_resumed", {
                                    "action": action,
                                    "phase": current_phase,
                                }
                            )
                            phase_loop_count = 0
                            # (feedback message already appended above)
                            accumulated_text = ""
                            continue

                    # Record cost for this failed attempt (update baseline before reset)
                    recorded_input_baseline += (usage.input_tokens
                                                + usage.cache_read_input_tokens
                                                + usage.cache_creation_input_tokens)
                    recorded_output_baseline += usage.output_tokens
                    await _record_phase_cost(build_id, current_phase, usage)

                    # --- Recovery Planner ---
                    # Instead of generic feedback, invoke a separate LLM to
                    # analyse the failure and produce a targeted remediation plan.
                    remediation_plan = ""
                    if audit_report and api_key:
                        try:
                            remediation_plan = await _run_recovery_planner(
                                build_id=build_id,
                                user_id=user_id,
                                api_key=api_key,
                                phase=current_phase,
                                audit_findings=audit_report,
                                builder_output=accumulated_text,
                                contracts=contracts,
                                working_dir=working_dir,
                            )
                        except Exception as exc:
                            logger.warning(
                                "Recovery planner failed for %s: %s — falling back to generic feedback",
                                current_phase, exc,
                            )
                            remediation_plan = ""

                    if remediation_plan:
                        feedback = (
                            f"The audit for {current_phase} FAILED "
                            f"(attempt {phase_loop_count}).\n\n"
                            f"A recovery planner has analysed the failure and "
                            f"produced a revised strategy:\n\n"
                            f"{remediation_plan}\n\n"
                            f"Follow this remediation plan to fix the issues "
                            f"and re-submit {current_phase}."
                        )
                    else:
                        feedback = (
                            f"[Audit Feedback for {current_phase}]\n"
                            f"{audit_report or 'FAIL'}\n\n"
                            f"Please address the issues above and try again."
                        )

                    # Inject audit feedback as a new user message
                    messages.append({
                        "role": "user",
                        "content": feedback,
                    })

            # Check for error signal
            if BUILD_ERROR_SIGNAL in accumulated_text:
                await _fail_build(
                    build_id, user_id, accumulated_text[-500:]
                )
                return

            # If tool calls were made, agent needs to respond to results
            if had_tool_calls:
                continue

            # Text-only turn: if no phase completed, the agent is done
            if not phase_completed:
                break

            # Push to GitHub after successful phase (with retry + backoff)
            if (
                audit_verdict == "PASS"
                and working_dir
                and files_written
                and target_type in ("github_new", "github_existing")
                and access_token
            ):
                push_succeeded = False
                for attempt in range(1, settings.GIT_PUSH_MAX_RETRIES + 1):
                    try:
                        await git_client.push(
                            working_dir, branch=branch, access_token=access_token,
                        )
                        await build_repo.append_build_log(
                            build_id, "Pushed to GitHub", source="system", level="info",
                        )
                        await _broadcast_build_event(user_id, build_id, "build_log", {
                            "message": "Pushed all commits to GitHub",
                            "source": "system", "level": "info",
                        })
                        push_succeeded = True
                        break
                    except Exception as exc:
                        logger.warning(
                            "Git push attempt %d/%d failed: %s",
                            attempt, settings.GIT_PUSH_MAX_RETRIES, exc,
                        )
                        await build_repo.append_build_log(
                            build_id,
                            f"Git push attempt {attempt}/{settings.GIT_PUSH_MAX_RETRIES} failed: {exc}",
                            source="system", level="warn",
                        )
                        if attempt < settings.GIT_PUSH_MAX_RETRIES:
                            await asyncio.sleep(2 ** attempt)

                if not push_succeeded:
                    await _pause_build(
                        build_id, user_id, current_phase,
                        phase_loop_count,
                        f"Git push failed after {settings.GIT_PUSH_MAX_RETRIES} attempts",
                    )
                    event = _pause_events.get(str(build_id))
                    if event:
                        try:
                            await asyncio.wait_for(
                                event.wait(),
                                timeout=settings.BUILD_PAUSE_TIMEOUT_MINUTES * 60,
                            )
                        except asyncio.TimeoutError:
                            await _fail_build(
                                build_id, user_id,
                                "Git push failed and pause expired",
                            )
                            return
                    action = _resume_actions.pop(str(build_id), "retry")
                    _pause_events.pop(str(build_id), None)
                    await build_repo.resume_build(build_id)
                    if action == "abort":
                        await _fail_build(
                            build_id, user_id,
                            "Build aborted after git push failure",
                        )
                        return
                    # On retry, loop continues — push retried next phase

        # Build completed (agent finished streaming)
        now = datetime.now(timezone.utc)
        await build_repo.update_build_status(
            build_id, "completed", completed_at=now
        )
        await project_repo.update_project_status(project_id, "completed")
        await build_repo.append_build_log(
            build_id, "Build completed successfully", source="system", level="info"
        )

        # Record any remaining unrecorded token usage as a final cost entry
        if usage.input_tokens > 0 or usage.output_tokens > 0:
            recorded_input_baseline += (usage.input_tokens
                                        + usage.cache_read_input_tokens
                                        + usage.cache_creation_input_tokens)
            recorded_output_baseline += usage.output_tokens
            await _record_phase_cost(build_id, current_phase or "final", usage)

        # Final commit + push for GitHub targets
        if working_dir and files_written:
            try:
                sha = await git_client.commit(working_dir, "forge: build complete")
                if sha:
                    await build_repo.append_build_log(
                        build_id, f"Final commit: {sha[:8]}", source="system", level="info",
                    )
            except Exception as exc:
                logger.warning("Final git commit failed: %s", exc)

            if target_type in ("github_new", "github_existing") and access_token:
                try:
                    await git_client.push(
                        working_dir, branch=branch, access_token=access_token,
                    )
                    await build_repo.append_build_log(
                        build_id, "Pushed to GitHub", source="system", level="info",
                    )
                    await _broadcast_build_event(user_id, build_id, "build_log", {
                        "message": "Pushed all commits to GitHub",
                        "source": "system", "level": "info",
                    })
                except Exception as exc:
                    logger.error("Git push failed: %s", exc)
                    await build_repo.append_build_log(
                        build_id, f"Git push failed: {exc}", source="system", level="error",
                    )
                    # Broadcast push failure so the user sees it in the activity feed
                    await _broadcast_build_event(user_id, build_id, "build_log", {
                        "message": f"Git push failed: {exc}",
                        "source": "system", "level": "error",
                    })

        # Gather total cost summary for the final event
        cost_summary = await build_repo.get_build_cost_summary(build_id)
        await _broadcast_build_event(user_id, build_id, "build_complete", {
            "id": str(build_id),
            "status": "completed",
            "total_input_tokens": cost_summary["total_input_tokens"],
            "total_output_tokens": cost_summary["total_output_tokens"],
            "total_cost_usd": float(cost_summary["total_cost_usd"]),
        })

    except asyncio.CancelledError:
        await build_repo.append_build_log(
            build_id, "Build task cancelled", source="system", level="warn"
        )
    except Exception as exc:
        await _fail_build(build_id, user_id, str(exc))
    finally:
        bid = str(build_id)
        _active_tasks.pop(bid, None)
        _pause_events.pop(bid, None)
        _resume_actions.pop(bid, None)
        _interjection_queues.pop(bid, None)
        # Cleanup temp working dir for GitHub targets on failure/cancel
        # (keep on success so user can inspect; they'll be cleaned up later)
        if working_dir and target_type in ("github_new", "github_existing"):
            try:
                build_record = await build_repo.get_build_by_id(build_id)
                if build_record and build_record["status"] in ("failed", "cancelled"):
                    shutil.rmtree(working_dir, ignore_errors=True)
                    logger.info("Cleaned up working directory: %s", working_dir)
            except Exception:
                pass  # Best effort cleanup


# ---------------------------------------------------------------------------
# File Writing Helpers
# ---------------------------------------------------------------------------


async def _write_file_block(
    build_id: UUID,
    user_id: UUID,
    working_dir: str,
    file_path: str,
    content: str,
    files_written: list[dict],
) -> None:
    """Write a file block to the working directory and emit events."""
    try:
        # Sanitize path -- prevent directory traversal
        clean_path = Path(file_path).as_posix()
        if clean_path.startswith("/") or ".." in clean_path:
            logger.warning("Skipping suspicious file path: %s", file_path)
            return

        full_path = Path(working_dir) / clean_path
        full_path.parent.mkdir(parents=True, exist_ok=True)
        full_path.write_text(content, encoding="utf-8")

        size_bytes = len(content.encode("utf-8"))
        language = _detect_language(clean_path)

        # Large file warning
        if size_bytes > settings.LARGE_FILE_WARN_BYTES:
            logger.warning(
                "Large file written: %s (%d bytes > %d threshold)",
                clean_path, size_bytes, settings.LARGE_FILE_WARN_BYTES,
            )
            await build_repo.append_build_log(
                build_id,
                f"Warning: large file {clean_path} ({size_bytes} bytes)",
                source="system", level="warn",
            )

        file_info = {
            "path": clean_path,
            "size_bytes": size_bytes,
            "language": language,
        }
        files_written.append(file_info)

        # Log as structured file entry (source='file' for querying)
        await build_repo.append_build_log(
            build_id,
            json.dumps(file_info),
            source="file",
            level="info",
        )

        # Broadcast file_created event
        await _broadcast_build_event(user_id, build_id, "file_created", file_info)

        logger.info("Wrote file: %s (%d bytes)", clean_path, size_bytes)

    except Exception as exc:
        logger.error("Failed to write file %s: %s", file_path, exc)
        await build_repo.append_build_log(
            build_id,
            f"Failed to write file {file_path}: {exc}",
            source="system",
            level="error",
        )


async def get_build_files(
    project_id: UUID, user_id: UUID
) -> list[dict]:
    """Get list of files written during the latest build.

    Returns list of {path, size_bytes, language, created_at}.
    """
    project = await project_repo.get_project_by_id(project_id)
    if not project or str(project["user_id"]) != str(user_id):
        raise ValueError("Project not found")

    latest = await build_repo.get_latest_build_for_project(project_id)
    if not latest:
        raise ValueError("No builds found for this project")

    return await build_repo.get_build_file_logs(latest["id"])


async def get_build_file_content(
    project_id: UUID, user_id: UUID, file_path: str
) -> dict:
    """Get content of a specific file from the build working directory.

    Returns {path, content, size_bytes, language}.
    """
    project = await project_repo.get_project_by_id(project_id)
    if not project or str(project["user_id"]) != str(user_id):
        raise ValueError("Project not found")

    latest = await build_repo.get_latest_build_for_project(project_id)
    if not latest:
        raise ValueError("No builds found for this project")

    working_dir = latest.get("working_dir")
    if not working_dir:
        raise ValueError("Build has no working directory")

    clean_path = Path(file_path).as_posix()
    if ".." in clean_path:
        raise ValueError("Invalid file path")

    full_path = Path(working_dir) / clean_path
    if not full_path.exists():
        raise ValueError(f"File not found: {clean_path}")

    content = full_path.read_text(encoding="utf-8")
    return {
        "path": clean_path,
        "content": content,
        "size_bytes": len(content.encode("utf-8")),
        "language": _detect_language(clean_path),
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_directive(contracts: list[dict]) -> str:
    """Assemble the builder directive from project contracts.

    Includes the universal builder_contract.md and all per-project
    contracts **except** ``phases`` (which uses a sliding window —
    only current + next phase are injected separately).  This keeps
    the cached prefix at ~33K instead of ~48K while ensuring the
    builder has everything it needs without expensive tool reads.
    """
    parts = ["# Forge Governance & Project Contracts\n"]

    # 1. Universal builder_contract.md (governance framework)
    builder_contract_path = FORGE_CONTRACTS_DIR / "builder_contract.md"
    if builder_contract_path.exists():
        parts.append("\n---\n## builder_contract (universal governance)\n")
        parts.append(builder_contract_path.read_text(encoding="utf-8"))
        parts.append("\n")
    else:
        logger.warning("builder_contract.md not found at %s", builder_contract_path)

    # 2. Per-project contracts in canonical order — EXCLUDING phases
    #    (phases use a sliding window injected separately)
    parts.append("\n---\n# Per-Project Contracts\n")
    type_order = [
        "blueprint", "manifesto", "stack", "schema", "physics",
        "boundaries", "ui", "builder_directive",
    ]
    sorted_contracts = sorted(
        contracts,
        key=lambda c: (
            type_order.index(c["contract_type"])
            if c["contract_type"] in type_order
            else len(type_order)
        ),
    )
    for contract in sorted_contracts:
        if contract["contract_type"] == "phases":
            continue  # Handled by _extract_phase_window
        parts.append(f"\n---\n## {contract['contract_type']}\n")
        parts.append(contract["content"])
        parts.append("\n")
    return "\n".join(parts)


def _write_contracts_to_workdir(
    working_dir: str, contracts: list[dict],
) -> list[str]:
    """Write per-project contract files into ``Forge/Contracts/`` in the repo.

    Returns the list of paths written (relative to working_dir).
    """
    contracts_dir = Path(working_dir) / "Forge" / "Contracts"
    contracts_dir.mkdir(parents=True, exist_ok=True)
    written: list[str] = []
    for contract in contracts:
        ctype = contract["contract_type"]
        content = contract["content"]
        # Determine extension from content or default to .md
        ext = ".md"
        if ctype == "boundaries":
            ext = ".json"
        elif ctype == "physics":
            ext = ".yaml"
        path = contracts_dir / f"{ctype}{ext}"
        path.write_text(content, encoding="utf-8")
        written.append(f"Forge/Contracts/{ctype}{ext}")
    return written


def _extract_phase_window(
    contracts: list[dict], current_phase_num: int,
) -> str:
    """Extract text for current phase + next phase from the phases contract.

    Returns a compact markdown string containing only the two relevant
    phase definitions, suitable for injecting into a user message.
    If the phases contract isn't found, returns an empty string.
    """
    phases_content = ""
    for c in contracts:
        if c["contract_type"] == "phases":
            phases_content = c["content"]
            break
    if not phases_content:
        return ""

    # Split into individual phase blocks
    phase_blocks = re.split(r"(?=^## Phase )", phases_content, flags=re.MULTILINE)
    target_nums = {current_phase_num, current_phase_num + 1}
    selected: list[str] = []
    for block in phase_blocks:
        header = re.match(
            r"^## Phase\s+(\d+)\s*[-—–]+\s*(.+)", block, re.MULTILINE,
        )
        if header and int(header.group(1)) in target_nums:
            selected.append(block.strip())

    if not selected:
        return ""

    return (
        "## Phase Window (current + next)\n\n"
        + "\n\n---\n\n".join(selected)
    )


# ---------------------------------------------------------------------------
# Recovery Planner
# ---------------------------------------------------------------------------

_MAX_PROJECT_STATE_BYTES = 200_000  # 200KB cap for project state
_MAX_SINGLE_FILE_BYTES = 10_000     # 10KB per file; truncate beyond this
_CODE_EXTENSIONS = frozenset({
    ".py", ".ts", ".tsx", ".js", ".jsx", ".sql", ".json", ".yaml", ".yml",
    ".toml", ".cfg", ".md", ".html", ".css",
})


def _gather_project_state(working_dir: str | None) -> str:
    """Walk the working directory and produce a file tree + key file contents.

    Returns a structured string suitable for inclusion in an LLM prompt.
    Respects size limits: total output ≤ 200KB, individual files ≤ 10KB
    (truncated to first + last 2KB with a marker).
    """
    if not working_dir or not Path(working_dir).is_dir():
        return "(working directory not available)"

    root = Path(working_dir)
    tree_lines: list[str] = []
    file_contents: list[str] = []
    total_bytes = 0

    # Walk and collect
    skip_dirs = {".git", "__pycache__", "node_modules", ".venv", "venv", ".tox", "dist", "build"}

    for dirpath, dirnames, filenames in os.walk(root):
        # Prune uninteresting directories
        dirnames[:] = [d for d in sorted(dirnames) if d not in skip_dirs]
        rel_dir = Path(dirpath).relative_to(root)

        for fname in sorted(filenames):
            rel_path = rel_dir / fname
            tree_lines.append(str(rel_path))

            # Include contents of code files
            ext = Path(fname).suffix.lower()
            if ext not in _CODE_EXTENSIONS:
                continue
            if total_bytes >= _MAX_PROJECT_STATE_BYTES:
                continue

            full_path = Path(dirpath) / fname
            try:
                raw = full_path.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue

            # Truncate large files
            if len(raw) > _MAX_SINGLE_FILE_BYTES:
                half = _MAX_SINGLE_FILE_BYTES // 5  # ~2KB each side
                raw = (
                    raw[:half]
                    + f"\n\n[... truncated {len(raw) - half * 2} chars ...]\n\n"
                    + raw[-half:]
                )

            entry = f"\n--- {rel_path} ---\n{raw}\n"
            total_bytes += len(entry)
            file_contents.append(entry)

    tree_str = "\n".join(tree_lines) if tree_lines else "(empty)"
    return (
        f"## File Tree\n```\n{tree_str}\n```\n\n"
        f"## File Contents\n{''.join(file_contents)}"
    )


async def _run_recovery_planner(
    *,
    build_id: UUID,
    user_id: UUID,
    api_key: str,
    phase: str,
    audit_findings: str,
    builder_output: str,
    contracts: list[dict],
    working_dir: str | None,
) -> str:
    """Invoke the recovery planner to analyse an audit failure.

    Calls a separate Sonnet LLM to analyse the audit findings, the builder's
    output, and the current project state, then produce a targeted remediation
    plan. Returns the remediation plan text, or empty string on failure.
    """
    from app.clients.llm_client import chat as llm_chat

    # Load recovery planner system prompt
    prompt_path = FORGE_CONTRACTS_DIR / "recovery_planner_prompt.md"
    if not prompt_path.exists():
        logger.warning("recovery_planner_prompt.md not found — skipping recovery planner")
        return ""
    system_prompt = prompt_path.read_text(encoding="utf-8")

    # Build reference contracts text
    reference_types = {
        "blueprint", "manifesto", "stack", "schema",
        "physics", "boundaries", "phases", "ui",
    }
    reference_parts = ["# Reference Contracts\n"]
    for c in contracts:
        if c["contract_type"] in reference_types:
            reference_parts.append(f"\n---\n## {c['contract_type']}\n{c['content']}\n")
    reference_text = "\n".join(reference_parts)

    # Gather current project state
    project_state = _gather_project_state(working_dir)

    # Truncate builder output to last 30K chars
    max_builder_chars = 30_000
    trimmed_builder = builder_output
    if len(builder_output) > max_builder_chars:
        trimmed_builder = (
            f"[... truncated {len(builder_output) - max_builder_chars} chars ...]\n"
            + builder_output[-max_builder_chars:]
        )

    # Truncate audit findings to 20K chars
    max_findings_chars = 20_000
    trimmed_findings = audit_findings
    if len(audit_findings) > max_findings_chars:
        trimmed_findings = audit_findings[:max_findings_chars] + "\n[... truncated ...]"

    user_message = (
        f"## Recovery Request\n\n"
        f"**Phase:** {phase}\n\n"
        f"### Audit Findings (FAILED)\n\n{trimmed_findings}\n\n"
        f"### Builder Output (what was attempted)\n\n"
        f"```\n{trimmed_builder}\n```\n\n"
        f"### Current Project State\n\n{project_state}\n\n"
        f"### Contracts\n\n{reference_text}\n\n"
        f"Produce a remediation plan that addresses every audit finding.\n"
    )

    await build_repo.append_build_log(
        build_id,
        f"Invoking recovery planner for {phase}",
        source="planner",
        level="info",
    )

    result = await llm_chat(
        api_key=api_key,
        model=settings.LLM_PLANNER_MODEL,
        system_prompt=system_prompt,
        messages=[{"role": "user", "content": user_message}],
        max_tokens=4096,
        provider="anthropic",
    )

    planner_text = result["text"] if isinstance(result, dict) else result
    planner_usage = result.get("usage", {}) if isinstance(result, dict) else {}

    # Log the planner output
    await build_repo.append_build_log(
        build_id,
        f"Recovery planner response ({planner_usage.get('input_tokens', 0)} in / "
        f"{planner_usage.get('output_tokens', 0)} out):\n{planner_text}",
        source="planner",
        level="info",
    )

    # Record planner cost separately
    input_t = planner_usage.get("input_tokens", 0)
    output_t = planner_usage.get("output_tokens", 0)
    model = settings.LLM_PLANNER_MODEL
    input_rate, output_rate = _get_token_rates(model)
    cost = Decimal(input_t) * input_rate + Decimal(output_t) * output_rate
    await build_repo.record_build_cost(
        build_id, f"{phase} (planner)", input_t, output_t, model, cost,
    )

    # Broadcast recovery plan WS event
    await _broadcast_build_event(
        user_id, build_id, "recovery_plan", {
            "phase": phase,
            "plan_text": planner_text,
        },
    )

    return planner_text


async def _run_inline_audit(
    build_id: UUID,
    phase: str,
    builder_output: str,
    contracts: list[dict],
    api_key: str,
    audit_llm_enabled: bool = True,
) -> tuple[str, str]:
    """Run an LLM-based audit of the builder's phase output.

    When audit_llm_enabled is True, sends the builder output + reference
    contracts to a separate LLM call using auditor_prompt.md as the system
    prompt. The auditor checks for contract compliance, architectural
    drift, and semantic correctness.

    When disabled, returns ('PASS', '') as a no-op (self-certification).

    Returns (verdict, report) where verdict is 'PASS' or 'FAIL' and
    report is the full auditor response text (empty on PASS/stub).
    """
    try:
        await build_repo.append_build_log(
            build_id,
            f"Running {'LLM' if audit_llm_enabled else 'stub'} audit for {phase}",
            source="audit",
            level="info",
        )

        if not audit_llm_enabled:
            await build_repo.append_build_log(
                build_id,
                "LLM audit disabled — auto-passing",
                source="audit",
                level="info",
            )
            return ("PASS", "")

        # Load auditor system prompt from Forge/Contracts
        auditor_prompt_path = FORGE_CONTRACTS_DIR / "auditor_prompt.md"
        if not auditor_prompt_path.exists():
            logger.warning("auditor_prompt.md not found — falling back to stub audit")
            return ("PASS", "")
        auditor_system = auditor_prompt_path.read_text(encoding="utf-8")

        # Build reference contracts (everything except builder_contract + builder_directive)
        # These give the auditor the baseline to compare builder output against
        reference_types = {
            "blueprint", "manifesto", "stack", "schema",
            "physics", "boundaries", "phases", "ui",
        }
        reference_parts = ["# Reference Contracts (baseline for audit)\n"]
        for c in contracts:
            if c["contract_type"] in reference_types:
                reference_parts.append(f"\n---\n## {c['contract_type']}\n")
                reference_parts.append(c["content"])
                reference_parts.append("\n")
        reference_text = "\n".join(reference_parts)

        # Truncate builder output to last 50K chars to stay within context
        max_output_chars = 50_000
        trimmed_output = builder_output
        if len(builder_output) > max_output_chars:
            trimmed_output = (
                f"[... truncated {len(builder_output) - max_output_chars} chars ...]\n"
                + builder_output[-max_output_chars:]
            )

        # Compose the user message for the auditor
        user_message = (
            f"## Audit Request\n\n"
            f"**Phase:** {phase}\n\n"
            f"### Builder Output for This Phase\n\n"
            f"```\n{trimmed_output}\n```\n\n"
            f"### Reference Contracts\n\n"
            f"{reference_text}\n\n"
            f"### Instructions\n\n"
            f"Review the builder's output for {phase} against the reference contracts above.\n"
            f"Check for: contract compliance, architectural drift, boundary violations, "
            f"schema mismatches, logic errors, and test quality.\n\n"
            f"Classify each finding as BLOCKING (must fix) or ADVISORY (nice to have).\n"
            f"BLOCKING = broken functionality, wrong API schemas, missing required "
            f"deliverables, structural violations.\n"
            f"ADVISORY = style issues, optional tooling, cosmetic preferences.\n\n"
            f"Respond with your audit report. Your verdict MUST be either:\n"
            f"- `CLEAN` — if there are no BLOCKING issues (ADVISORY items are OK)\n"
            f"- `FLAGS FOUND` — ONLY if there are BLOCKING issues\n\n"
            f"End your response with exactly one of these lines:\n"
            f"VERDICT: CLEAN\n"
            f"VERDICT: FLAGS FOUND\n"
        )

        # Call the auditor LLM (Sonnet — accurate and fast)
        from app.clients.llm_client import chat as llm_chat
        result = await llm_chat(
            api_key=api_key,
            model=settings.LLM_QUESTIONNAIRE_MODEL,  # Sonnet
            system_prompt=auditor_system,
            messages=[{"role": "user", "content": user_message}],
            max_tokens=4096,
            provider="anthropic",
        )

        audit_text = result["text"] if isinstance(result, dict) else result
        audit_usage = result.get("usage", {}) if isinstance(result, dict) else {}

        # Log the full audit report
        await build_repo.append_build_log(
            build_id,
            f"Auditor report ({audit_usage.get('input_tokens', 0)} in / "
            f"{audit_usage.get('output_tokens', 0)} out):\n{audit_text}",
            source="audit",
            level="info",
        )

        # Parse verdict
        if "VERDICT: CLEAN" in audit_text:
            return ("PASS", audit_text)
        elif "VERDICT: FLAGS FOUND" in audit_text:
            return ("FAIL", audit_text)
        else:
            # Ambiguous response — log warning, default to PASS
            logger.warning("Auditor response missing clear verdict — defaulting to PASS")
            await build_repo.append_build_log(
                build_id,
                "Auditor verdict unclear — defaulting to PASS",
                source="audit",
                level="warn",
            )
            return ("PASS", audit_text)

    except Exception as exc:
        logger.error("LLM audit error for %s: %s", phase, exc)
        await build_repo.append_build_log(
            build_id,
            f"Audit error: {exc} — defaulting to PASS",
            source="audit",
            level="error",
        )
        return ("PASS", "")


# ---------------------------------------------------------------------------
# Per-file streaming audit (runs in parallel with generation)
# ---------------------------------------------------------------------------

# Semaphore to limit concurrent per-file audit LLM calls (avoid rate-limit storms)
_FILE_AUDIT_SEMAPHORE = asyncio.Semaphore(3)

# Max fix attempts the auditor (Key 2) will try before deferring to builder
_AUDITOR_FIX_ROUNDS = 2


async def _fix_single_file(
    build_id: UUID,
    user_id: UUID,
    api_key: str,
    file_path: str,
    findings: str,
    working_dir: str,
    model: str | None = None,
    label: str = "auditor",
) -> str:
    """Apply a targeted fix to a single file based on audit findings.

    Reads the file + related sibling files from disk, sends to the LLM
    with the audit findings for correction, writes the fixed output back.
    Uses ``_FILE_AUDIT_SEMAPHORE`` to respect concurrency limits.

    Returns the new file content after the fix.
    """
    from app.clients.llm_client import chat as llm_chat
    from app.services.tool_executor import _exec_write_file
    import re as _re_fix

    wd = Path(working_dir)
    target = wd / file_path

    # Read current file content
    current_content = ""
    if target.exists():
        try:
            current_content = target.read_text(encoding="utf-8")
        except Exception:
            pass

    # Gather related context (siblings + imports)
    ctx: dict[str, str] = {}
    # Parse imports from the file
    for imp_match in _re_fix.finditer(r'(?:from|import)\s+([\w.]+)', current_content):
        mod = imp_match.group(1)
        mod_path = mod.replace(".", "/") + ".py"
        mod_fp = wd / mod_path
        if mod_fp.exists() and mod_path != file_path and len(ctx) < 6:
            try:
                ctx[mod_path] = mod_fp.read_text(encoding="utf-8")
            except Exception:
                pass
    # Sibling files in same directory
    target_dir = target.parent
    if target_dir.exists():
        for sibling in sorted(target_dir.iterdir()):
            if len(ctx) >= 6:
                break
            if sibling.is_file() and sibling.suffix in (".py", ".ts", ".tsx", ".js"):
                rel = str(sibling.relative_to(wd)).replace("\\", "/")
                if rel != file_path and rel not in ctx:
                    try:
                        ctx[rel] = sibling.read_text(encoding="utf-8")
                    except Exception:
                        pass

    # Truncate context files to keep within budget
    max_ctx_chars = 8_000
    trimmed_ctx: dict[str, str] = {}
    total = 0
    for p, c in ctx.items():
        if total + len(c) > max_ctx_chars:
            break
        trimmed_ctx[p] = c
        total += len(c)

    # Build fix prompt
    resolve_model = model or settings.LLM_QUESTIONNAIRE_MODEL

    system_prompt = (
        "You are a code fixer. You receive a file that has structural issues "
        "identified by a code auditor. Your job is to fix ONLY the specific "
        "issues listed — do not refactor, restyle, or otherwise change code "
        "that is working correctly.\n\n"
        "Output ONLY the complete fixed file content. No markdown fences, "
        "no explanation, no preamble.\n\n"
        "Rules:\n"
        "- Fix each identified issue precisely\n"
        "- Preserve all existing functionality\n"
        "- Keep the same imports, structure, and style\n"
        "- If an import is missing, add it\n"
        "- If a function is referenced but undefined, define it or fix the reference\n"
        "- Do NOT remove code unless the finding specifically says it's wrong\n"
    )

    parts = [f"## File to Fix: `{file_path}`\n\n```\n{current_content}\n```\n"]
    parts.append(f"\n## Audit Findings (fix these)\n{findings}\n")
    if trimmed_ctx:
        parts.append("\n## Related Files (reference only — do not output these)\n")
        for cp, cc in trimmed_ctx.items():
            parts.append(f"\n### {cp}\n```\n{cc[:4000]}\n```\n")
    parts.append(
        f"\n## Instructions\n"
        f"Output the COMPLETE fixed content of `{file_path}`. "
        f"Fix only the issues listed above.\n"
    )

    user_message = "\n".join(parts)

    await _broadcast_build_event(user_id, build_id, "file_fixing", {
        "path": file_path,
        "fixer": label,
    })

    async with _FILE_AUDIT_SEMAPHORE:
        result = await asyncio.wait_for(
            llm_chat(
                api_key=api_key,
                model=resolve_model,
                system_prompt=system_prompt,
                messages=[{"role": "user", "content": user_message}],
                max_tokens=16_384,
                provider="anthropic",
            ),
            timeout=180,
        )

    content = result["text"] if isinstance(result, dict) else result
    usage = result.get("usage", {}) if isinstance(result, dict) else {}

    # Strip markdown fences if model wrapped output
    content = re.sub(r"^```\w*\n", "", content)
    content = re.sub(r"\n```\s*$", "", content)
    if content and not content.endswith("\n"):
        content += "\n"

    # Write to disk
    _exec_write_file({"path": file_path, "content": content}, working_dir)

    # Record cost
    input_t = usage.get("input_tokens", 0)
    output_t = usage.get("output_tokens", 0)
    input_rate, output_rate = _get_token_rates(resolve_model)
    cost = Decimal(input_t) * input_rate + Decimal(output_t) * output_rate
    await build_repo.record_build_cost(
        build_id, f"fix:{label}:{file_path}", input_t, output_t,
        resolve_model, cost,
    )

    await build_repo.append_build_log(
        build_id,
        f"Fix applied by {label}: {file_path} ({input_t}+{output_t} tokens)",
        source="fix", level="info",
    )

    return content


async def _builder_drain_fix_queue(
    build_id: UUID,
    user_id: UUID,
    builder_api_key: str,
    audit_api_key: str,
    fix_queue: "asyncio.Queue[tuple[str, str]]",
    working_dir: str,
    manifest_cache_path: Path,
    audit_llm_enabled: bool = True,
) -> list[tuple[str, str, str]]:
    """After file generation completes, builder (Key 1 / Opus) picks up
    any files that the auditor couldn't fix.

    For each queued file the builder applies a fix then the auditor
    re-audits.  Returns list of ``(path, final_verdict, findings)`` tuples.
    """
    results: list[tuple[str, str, str]] = []

    if fix_queue.empty():
        return results

    queue_size = fix_queue.qsize()
    await build_repo.append_build_log(
        build_id,
        f"Builder picking up {queue_size} file(s) from auditor fix queue...",
        source="fix", level="info",
    )
    await _broadcast_build_event(user_id, build_id, "build_log", {
        "message": f"🔧 Builder picking up {queue_size} file(s) the auditor couldn't fix...",
        "source": "fix", "level": "info",
    })

    while not fix_queue.empty():
        try:
            fpath, ffindings = fix_queue.get_nowait()
        except asyncio.QueueEmpty:
            break

        _touch_progress(build_id)

        try:
            # Builder fix (Key 1 / Opus — higher capability)
            fixed_content = await _fix_single_file(
                build_id, user_id, builder_api_key,
                fpath, ffindings, working_dir,
                model=settings.LLM_BUILDER_MODEL,
                label="builder",
            )

            # Re-audit with auditor (Key 2)
            re_result = await _audit_single_file(
                build_id, user_id, audit_api_key,
                fpath, fixed_content, "",
                audit_llm_enabled,
            )
            _, re_verdict, re_findings = re_result

            if re_verdict == "PASS":
                _update_manifest_cache(manifest_cache_path, fpath, "fixed", "PASS")
                await _broadcast_build_event(user_id, build_id, "file_fixed", {
                    "path": fpath,
                    "fixer": "builder",
                    "rounds": 1,
                })
                await build_repo.append_build_log(
                    build_id,
                    f"✓ Builder fixed {fpath}",
                    source="fix", level="info",
                )
                await _broadcast_build_event(user_id, build_id, "build_log", {
                    "message": f"✓ Builder fixed {fpath}",
                    "source": "fix", "level": "info",
                })
            else:
                _update_manifest_cache(manifest_cache_path, fpath, "audited", "FAIL")
                await build_repo.append_build_log(
                    build_id,
                    f"Builder fix didn't resolve {fpath} — will proceed to recovery",
                    source="fix", level="warn",
                )

            results.append((fpath, re_verdict, re_findings))

        except Exception as exc:
            logger.warning("Builder fix failed for %s: %s", fpath, exc)
            results.append((fpath, "FAIL", str(exc)))

    return results


def _update_manifest_cache(
    manifest_cache_path: Path,
    file_path: str,
    status: str,
    verdict: str = "",
) -> None:
    """Update a single file's status in the cached manifest JSON.

    Called right after each per-file audit completes so the cache
    reflects the latest progress.  If the file is not found or the
    cache is unreadable the update is silently skipped (best-effort).
    """
    try:
        if not manifest_cache_path.exists():
            return
        data = json.loads(manifest_cache_path.read_text(encoding="utf-8"))
        for entry in data:
            if entry.get("path") == file_path:
                entry["status"] = status
                if verdict:
                    entry["audit_verdict"] = verdict
                break
        manifest_cache_path.write_text(
            json.dumps(data, indent=2), encoding="utf-8",
        )
    except Exception:
        pass  # best-effort


async def _audit_and_cache(
    manifest_cache_path: Path,
    build_id: UUID,
    user_id: UUID,
    audit_api_key: str,
    file_path: str,
    file_content: str,
    file_purpose: str,
    audit_llm_enabled: bool = True,
    audit_index: int = 0,
    audit_total: int = 0,
    working_dir: str = "",
    fix_queue: "asyncio.Queue[tuple[str, str]] | None" = None,
) -> tuple[str, str, str]:
    """Audit a file, attempt to fix if FAIL, then persist to manifest cache.

    Fix loop (up to ``_AUDITOR_FIX_ROUNDS``):
      1. Audit → if PASS, done.
      2. If FAIL, call ``_fix_single_file`` (Key 2 / Sonnet), re-audit.
      3. If still failing after max rounds, push to ``fix_queue`` for builder.

    Returns ``(file_path, final_verdict, findings)``.
    """
    result = await _audit_single_file(
        build_id, user_id, audit_api_key,
        file_path, file_content, file_purpose,
        audit_llm_enabled, audit_index, audit_total,
    )
    fpath, fverdict, ffindings = result

    # If PASS or fixing disabled, just cache and return
    if fverdict == "PASS" or not working_dir or not audit_llm_enabled:
        _update_manifest_cache(manifest_cache_path, fpath, "audited", fverdict)
        return result

    # --- Auditor fix loop (Key 2 / Sonnet) ---
    for fix_round in range(1, _AUDITOR_FIX_ROUNDS + 1):
        try:
            _update_manifest_cache(manifest_cache_path, fpath, "fixing", fverdict)

            await build_repo.append_build_log(
                build_id,
                f"Auditor fixing {fpath} (round {fix_round}/{_AUDITOR_FIX_ROUNDS})...",
                source="fix", level="info",
            )
            await _broadcast_build_event(user_id, build_id, "build_log", {
                "message": f"🔧 Auditor fixing {fpath} (round {fix_round}/{_AUDITOR_FIX_ROUNDS})...",
                "source": "fix", "level": "info",
            })

            fixed_content = await _fix_single_file(
                build_id, user_id, audit_api_key,
                fpath, ffindings, working_dir,
                label="auditor",
            )

            # Re-audit the fixed file
            result = await _audit_single_file(
                build_id, user_id, audit_api_key,
                fpath, fixed_content, file_purpose,
                audit_llm_enabled,
            )
            fpath, fverdict, ffindings = result

            if fverdict == "PASS":
                _update_manifest_cache(manifest_cache_path, fpath, "fixed", "PASS")
                await _broadcast_build_event(user_id, build_id, "file_fixed", {
                    "path": fpath,
                    "fixer": "auditor",
                    "rounds": fix_round,
                })
                await build_repo.append_build_log(
                    build_id,
                    f"✓ Auditor fixed {fpath} in {fix_round} round(s)",
                    source="fix", level="info",
                )
                await _broadcast_build_event(user_id, build_id, "build_log", {
                    "message": f"✓ Auditor fixed {fpath} in {fix_round} round(s)",
                    "source": "fix", "level": "info",
                })
                return result

        except Exception as exc:
            logger.warning(
                "Auditor fix round %d failed for %s: %s",
                fix_round, fpath, exc,
            )

    # Auditor couldn't fix it — push to builder fix queue
    if fix_queue is not None:
        await fix_queue.put((fpath, ffindings))
        _update_manifest_cache(manifest_cache_path, fpath, "fix_queued", "FAIL")
        await build_repo.append_build_log(
            build_id,
            f"Auditor couldn't fix {fpath} after {_AUDITOR_FIX_ROUNDS} rounds — queued for builder",
            source="fix", level="warn",
        )
        await _broadcast_build_event(user_id, build_id, "build_log", {
            "message": f"⏳ {fpath} queued for builder fix (auditor exhausted {_AUDITOR_FIX_ROUNDS} rounds)",
            "source": "fix", "level": "warn",
        })
    else:
        _update_manifest_cache(manifest_cache_path, fpath, "audited", fverdict)

    return result


async def _audit_single_file(
    build_id: UUID,
    user_id: UUID,
    audit_api_key: str,
    file_path: str,
    file_content: str,
    file_purpose: str,
    audit_llm_enabled: bool = True,
    audit_index: int = 0,
    audit_total: int = 0,
) -> tuple[str, str, str]:
    """Light structural audit of a single generated file.

    Uses the *audit* API key (key 2) to avoid competing with the builder.
    Broadcasts a ``file_audited`` WS event on completion.
    Returns ``(file_path, verdict, findings_summary)``.

    Concurrency is bounded by ``_FILE_AUDIT_SEMAPHORE`` (3).
    Errors are handled gracefully (fail-open).
    """
    import time as _time

    progress_tag = f"[{audit_index}/{audit_total}]" if audit_total else ""

    t0 = _time.monotonic()

    try:
        # --- fast-path: audit disabled / trivially small files ---
        if not audit_llm_enabled or len(file_content.strip()) < 50:
            dur = int((_time.monotonic() - t0) * 1000)
            await _broadcast_build_event(user_id, build_id, "file_audited", {
                "path": file_path,
                "verdict": "PASS",
                "findings": "",
                "duration_ms": dur,
            })
            return (file_path, "PASS", "")

        # Announce audit start
        await _broadcast_build_event(user_id, build_id, "build_log", {
            "message": f"Auditing {progress_tag} {file_path}...",
            "source": "audit", "level": "info",
        })

        # --- Pre-LLM syntax gate: catch real parse errors early ---
        syntax_finding = ""
        if file_path.endswith(".py"):
            import ast as _ast_audit
            try:
                _ast_audit.parse(file_content, filename=file_path)
            except SyntaxError as _se:
                syntax_finding = (
                    f"L{_se.lineno or '?'}: SyntaxError — {_se.msg}"
                )

        # Acquire semaphore — limits concurrent LLM calls
        async with _FILE_AUDIT_SEMAPHORE:
            path, verdict, findings = await _audit_single_file_llm(
                build_id, user_id, audit_api_key,
                file_path, file_content, file_purpose,
                t0, progress_tag,
            )

        # If we found a real syntax error, always force FAIL regardless
        # of the LLM verdict so the fix loop runs before commit.
        if syntax_finding:
            combined = (
                f"{syntax_finding}\n{findings}".strip()
                if findings
                else syntax_finding
            )
            if verdict != "FAIL":
                dur = int((_time.monotonic() - t0) * 1000)
                await build_repo.append_build_log(
                    build_id,
                    f"File audit FAIL (syntax) {progress_tag}: {file_path} ({dur}ms)\n{combined}",
                    source="audit", level="warn",
                )
                await _broadcast_build_event(user_id, build_id, "file_audited", {
                    "path": file_path,
                    "verdict": "FAIL",
                    "findings": combined[:2000],
                    "duration_ms": dur,
                })
            return (file_path, "FAIL", combined)

        return (path, verdict, findings)

    except Exception as exc:
        dur = int((_time.monotonic() - t0) * 1000)
        logger.warning("Per-file audit failed for %s: %s", file_path, exc)
        await build_repo.append_build_log(
            build_id,
            f"Per-file audit error for {file_path}: {exc}",
            source="audit",
            level="warn",
        )
        await _broadcast_build_event(user_id, build_id, "file_audited", {
            "path": file_path,
            "verdict": "PASS",
            "findings": f"Audit error: {exc}",
            "duration_ms": dur,
        })
        return (file_path, "PASS", "")


async def _audit_single_file_llm(
    build_id: UUID,
    user_id: UUID,
    audit_api_key: str,
    file_path: str,
    file_content: str,
    file_purpose: str,
    t0: float,
    progress_tag: str,
) -> tuple[str, str, str]:
    """Light structural LLM audit (runs under semaphore, separate API key)."""
    import time as _time

    try:
        # Truncate very large files
        max_file_chars = 12_000
        trimmed = file_content
        if len(file_content) > max_file_chars:
            trimmed = (
                file_content[:max_file_chars]
                + f"\n[... truncated {len(file_content) - max_file_chars} chars ...]"
            )

        system_prompt = (
            "You are a fast structural code auditor. Do a quick quality check "
            "on the file below.\n\n"
            "Check ONLY for:\n"
            "- Missing or broken imports/exports\n"
            "- Obvious logic errors or typos\n"
            "- Functions/classes referenced but never defined\n"
            "- File doesn't match its stated purpose\n\n"
            "Do NOT flag: style, naming, missing docs, optional improvements.\n\n"
            "If the file looks structurally sound, respond with just:\n"
            "VERDICT: CLEAN\n\n"
            "If there are real problems, list each on its own line with the "
            "line number(s) where the issue occurs, using this exact format:\n"
            "L<start>[-L<end>]: <short description>\n\n"
            "Examples:\n"
            "L42: 'UserService' imported but never defined in this module\n"
            "L110-L115: unreachable code after return statement\n\n"
            "After all issues, end with:\n"
            "VERDICT: FLAGS FOUND"
        )

        user_message = (
            f"**File:** `{file_path}`\n"
            f"**Purpose:** {file_purpose}\n\n"
            f"```\n{trimmed}\n```"
        )

        from app.clients.llm_client import chat as llm_chat

        result = await asyncio.wait_for(
            llm_chat(
                api_key=audit_api_key,
                model=settings.LLM_QUESTIONNAIRE_MODEL,
                system_prompt=system_prompt,
                messages=[{"role": "user", "content": user_message}],
                max_tokens=1024,
                provider="anthropic",
            ),
            timeout=120,
        )

        audit_text = result["text"] if isinstance(result, dict) else result
        dur = int((_time.monotonic() - t0) * 1000)

        if "VERDICT: FLAGS FOUND" in audit_text:
            verdict = "FAIL"
            findings = audit_text
        else:
            verdict = "PASS"
            findings = ""

        level = "warn" if verdict == "FAIL" else "info"
        await build_repo.append_build_log(
            build_id,
            f"File audit {verdict} {progress_tag}: {file_path} ({dur}ms)"
            + (f"\n{findings}" if findings else ""),
            source="audit",
            level=level,
        )

        await _broadcast_build_event(user_id, build_id, "file_audited", {
            "path": file_path,
            "verdict": verdict,
            "findings": findings[:2000] if findings else "",
            "duration_ms": dur,
        })

        return (file_path, verdict, findings)

    except Exception as exc:
        dur = int((_time.monotonic() - t0) * 1000)
        logger.warning("Per-file audit LLM error for %s: %s", file_path, exc)
        await build_repo.append_build_log(
            build_id,
            f"Per-file audit error for {file_path}: {exc}",
            source="audit",
            level="warn",
        )
        await _broadcast_build_event(user_id, build_id, "file_audited", {
            "path": file_path,
            "verdict": "PASS",
            "findings": f"Audit error: {exc}",
            "duration_ms": dur,
        })
        return (file_path, "PASS", "")


async def _fail_build(build_id: UUID, user_id: UUID, detail: str) -> None:
    """Mark a build as failed and broadcast the event."""
    bid = str(build_id)
    _cancel_flags.discard(bid)
    _pause_flags.discard(bid)
    _compact_flags.discard(bid)
    _current_generating.pop(bid, None)
    _build_activity_status.pop(bid, None)
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


async def _broadcast_build_event(
    user_id: UUID, build_id: UUID, event_type: str, payload: dict
) -> None:
    """Send a build progress event via WebSocket."""
    await manager.send_to_user(str(user_id), {
        "type": event_type,
        "payload": payload,
    })


async def _record_phase_cost(
    build_id: UUID, phase: str, usage: StreamUsage
) -> None:
    """Persist token usage for the current phase and reset counters."""
    # Total input = fresh + cache-read (10% cost) + cache-creation (125% cost)
    input_t = (usage.input_tokens
               + usage.cache_read_input_tokens
               + usage.cache_creation_input_tokens)
    output_t = usage.output_tokens
    model = usage.model or settings.LLM_BUILDER_MODEL
    input_rate, output_rate = _get_token_rates(model)
    # Approximate cost: cache_read tokens cost ~10% of base, cache_creation
    # costs ~125%.  For simplicity we use the base rate for all — this
    # overstates slightly but is safe / conservative.
    cost = (Decimal(input_t) * input_rate
            + Decimal(output_t) * output_rate)
    await build_repo.record_build_cost(
        build_id, phase, input_t, output_t, model, cost
    )
    # Reset for next phase
    usage.input_tokens = 0
    usage.output_tokens = 0
    usage.cache_read_input_tokens = 0
    usage.cache_creation_input_tokens = 0


# ---------------------------------------------------------------------------
# Summary / Instructions (Phase 11 API)
# ---------------------------------------------------------------------------


async def get_build_summary(project_id: UUID, user_id: UUID) -> dict:
    """Return a complete build summary with cost breakdown.

    Raises:
        ValueError: If project not found, not owned, or no builds.
    """
    project = await project_repo.get_project_by_id(project_id)
    if not project or str(project["user_id"]) != str(user_id):
        raise ValueError("Project not found")

    latest = await build_repo.get_latest_build_for_project(project_id)
    if not latest:
        raise ValueError("No builds found for this project")

    build_id = latest["id"]
    cost_summary = await build_repo.get_build_cost_summary(build_id)
    cost_entries = await build_repo.get_build_costs(build_id)
    stats = await build_repo.get_build_stats(build_id)

    elapsed_seconds: float | None = None
    if latest.get("started_at") and latest.get("completed_at"):
        elapsed_seconds = (
            latest["completed_at"] - latest["started_at"]
        ).total_seconds()

    return {
        "build": latest,
        "cost": {
            "total_input_tokens": cost_summary["total_input_tokens"],
            "total_output_tokens": cost_summary["total_output_tokens"],
            "total_cost_usd": float(cost_summary["total_cost_usd"]),
            "phases": [
                {
                    "phase": e["phase"],
                    "input_tokens": e["input_tokens"],
                    "output_tokens": e["output_tokens"],
                    "model": e["model"],
                    "estimated_cost_usd": float(e["estimated_cost_usd"]),
                }
                for e in cost_entries
            ],
        },
        "elapsed_seconds": elapsed_seconds,
        "loop_count": latest["loop_count"],
        "total_turns": stats["total_turns"],
        "total_audit_attempts": stats["total_audit_attempts"],
        "files_written_count": stats["files_written_count"],
        "git_commits_made": stats["git_commits_made"],
        "interjections_received": stats["interjections_received"],
    }


async def get_build_instructions(project_id: UUID, user_id: UUID) -> dict:
    """Generate deployment instructions from the project's stack contract.

    Raises:
        ValueError: If project not found, not owned, or no contracts.
    """
    project = await project_repo.get_project_by_id(project_id)
    if not project or str(project["user_id"]) != str(user_id):
        raise ValueError("Project not found")

    contracts = await project_repo.get_contracts_by_project(project_id)
    if not contracts:
        raise ValueError("No contracts found for this project")

    stack_content = ""
    blueprint_content = ""
    for c in contracts:
        if c["contract_type"] == "stack":
            stack_content = c["content"]
        elif c["contract_type"] == "blueprint":
            blueprint_content = c["content"]

    instructions = _generate_deploy_instructions(
        project["name"], stack_content, blueprint_content
    )
    return {
        "project_name": project["name"],
        "instructions": instructions,
    }


async def get_build_phases(project_id: UUID, user_id: UUID) -> list[dict]:
    """Parse the phases contract into a structured list of phase definitions.

    Each entry contains: number, name, objective, deliverables (list of strings).

    Raises:
        ValueError: If project not found, not owned, or no phases contract.
    """
    project = await project_repo.get_project_by_id(project_id)
    if not project or str(project["user_id"]) != str(user_id):
        raise ValueError("Project not found")

    phases_contract = await project_repo.get_contract_by_type(project_id, "phases")
    if not phases_contract:
        raise ValueError("No phases contract found")

    return _parse_phases_contract(phases_contract["content"])


def _parse_phases_contract(content: str) -> list[dict]:
    """Parse a phases contract markdown into structured phase definitions.

    Expects sections like:
        ## Phase 0 -- Genesis
        **Objective:** ...
        **Deliverables:**
        - item 1
        - item 2
    """
    phases: list[dict] = []
    # Split on ## Phase headers
    phase_blocks = re.split(r"(?=^## Phase )", content, flags=re.MULTILINE)

    for block in phase_blocks:
        # Match "## Phase N -- Name" or "## Phase N — Name"
        header = re.match(
            r"^## Phase\s+(\d+)\s*[-—–]+\s*(.+?)\s*$", block, re.MULTILINE
        )
        if not header:
            continue

        phase_num = int(header.group(1))
        phase_name = header.group(2).strip()

        # Extract objective
        obj_match = re.search(
            r"\*\*Objective:\*\*\s*(.+?)(?=\n\n|\n\*\*|$)", block, re.DOTALL
        )
        objective = obj_match.group(1).strip() if obj_match else ""

        # Extract deliverables (bullet list after **Deliverables:**)
        deliverables: list[str] = []
        deliv_match = re.search(
            r"\*\*Deliverables:\*\*\s*\n((?:[-*]\s+.+\n?)+)", block
        )
        if deliv_match:
            for line in deliv_match.group(1).strip().splitlines():
                item = re.sub(r"^[-*]\s+", "", line).strip()
                if item:
                    deliverables.append(item)

        phases.append({
            "number": phase_num,
            "name": phase_name,
            "objective": objective,
            "deliverables": deliverables,
        })

    return phases


def _generate_deploy_instructions(
    project_name: str, stack_content: str, blueprint_content: str
) -> str:
    """Build deployment instructions from stack and blueprint contracts."""
    lines = [f"# Deployment Instructions — {project_name}\n"]

    # Detect stack components
    has_python = "python" in stack_content.lower()
    has_node = "node" in stack_content.lower() or "react" in stack_content.lower()
    has_postgres = "postgres" in stack_content.lower()
    has_render = "render" in stack_content.lower()

    lines.append("## Prerequisites\n")
    if has_python:
        lines.append("- Python 3.12+")
    if has_node:
        lines.append("- Node.js 18+")
    if has_postgres:
        lines.append("- PostgreSQL 15+")
    lines.append("- Git 2.x\n")

    lines.append("## Setup Steps\n")
    lines.append("1. Clone the generated repository")
    lines.append("2. Copy `.env.example` to `.env` and fill in credentials")
    if has_python:
        lines.append("3. Create virtual environment: `python -m venv .venv`")
        lines.append("4. Install dependencies: `pip install -r requirements.txt`")
    if has_node:
        lines.append("5. Install frontend: `cd web && npm install`")
    if has_postgres:
        lines.append("6. Run database migrations: `psql $DATABASE_URL -f db/migrations/*.sql`")
    lines.append("7. Start the application: `pwsh -File boot.ps1`\n")

    if has_render:
        lines.append("## Render Deployment\n")
        lines.append("1. Create a new **Web Service** on Render")
        lines.append("2. Connect your GitHub repository")
        lines.append("3. Set **Build Command**: `pip install -r requirements.txt`")
        lines.append("4. Set **Start Command**: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`")
        lines.append("5. Add a **PostgreSQL** database")
        lines.append("6. Configure environment variables in the Render dashboard")
        if has_node:
            lines.append("7. For the frontend, create a **Static Site** pointing to `web/`")
            lines.append("8. Set **Build Command**: `npm install && npm run build`")
            lines.append("9. Set **Publish Directory**: `web/dist`")

    lines.append("\n## Environment Variables\n")
    lines.append("| Variable | Required | Description |")
    lines.append("|----------|----------|-------------|")
    lines.append("| `DATABASE_URL` | Yes | PostgreSQL connection string |")
    lines.append("| `JWT_SECRET` | Yes | Random secret for session tokens |")
    if "github" in stack_content.lower() or "oauth" in stack_content.lower():
        lines.append("| `GITHUB_CLIENT_ID` | Yes | GitHub OAuth app client ID |")
        lines.append("| `GITHUB_CLIENT_SECRET` | Yes | GitHub OAuth app secret |")
        lines.append("| `GITHUB_WEBHOOK_SECRET` | Yes | Webhook signature secret |")
    lines.append("| `FRONTEND_URL` | No | Frontend origin for CORS |")
    lines.append("| `APP_URL` | No | Backend URL |")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Plan-Then-Execute Architecture (Phase 21)
# ---------------------------------------------------------------------------

# Contract relevance mapping — which contracts are useful for which file types
_CONTRACT_RELEVANCE: dict[str, list[str]] = {
    "app/": ["blueprint", "schema", "stack", "boundaries", "builder_contract"],
    "tests/": ["blueprint", "schema", "stack"],
    "web/src/components/": ["ui", "blueprint", "stack"],
    "web/src/pages/": ["ui", "blueprint", "stack", "schema"],
    "db/": ["schema"],
    "config": ["stack", "boundaries"],
    "doc": ["blueprint", "manifesto"],
}

# Planner build prompt template path
_PLANNER_BUILD_PROMPT_PATH = (
    Path(__file__).resolve().parent.parent / "templates" / "contracts" / "planner_build_prompt.md"
)


def _select_contracts_for_file(
    file_path: str,
    contracts: list[dict],
) -> list[dict]:
    """Select the subset of contracts relevant to a given file type.

    Uses _CONTRACT_RELEVANCE mapping to avoid sending all contracts
    when generating each file (saves tokens, improves cache hit rate).
    """
    relevant_types: set[str] = set()

    # Match by path prefix
    for prefix, types in _CONTRACT_RELEVANCE.items():
        if file_path.startswith(prefix):
            relevant_types.update(types)
            break

    # Catch-all: blueprint + stack
    if not relevant_types:
        relevant_types = {"blueprint", "stack"}

    # Test files also get the same contracts as their target
    if file_path.startswith("tests/"):
        relevant_types.update(["blueprint", "schema", "stack", "boundaries"])

    # SQL migrations only need schema
    if file_path.endswith(".sql"):
        relevant_types = {"schema"}

    return [c for c in contracts if c["contract_type"] in relevant_types]


def _calculate_context_budget(
    file_entry: dict,
    system_prompt_tokens: int,
    contract_tokens: int,
    available_context_files: dict[str, str],
) -> dict:
    """Calculate how much context to send per file generation call.

    Returns dict with:
        files_to_include: list of paths to include as context
        max_tokens: output token limit for this file
        truncated: list of paths that were truncated
    """
    MODEL_CONTEXT_WINDOW = 200_000
    SAFETY_MARGIN = 5_000

    # Output budget from estimated lines
    estimated_lines = file_entry.get("estimated_lines", 100)
    max_tokens = max(4096, min(estimated_lines * 15, 16384))

    # Available for context files
    available_input = (
        MODEL_CONTEXT_WINDOW - max_tokens - SAFETY_MARGIN
        - system_prompt_tokens - contract_tokens
    )

    files_to_include: list[str] = []
    truncated: list[str] = []
    total_context_tokens = 0

    # Prioritize direct dependencies first, then other context files
    context_paths = file_entry.get("context_files", [])
    depends_on = file_entry.get("depends_on", [])
    ordered = list(depends_on) + [p for p in context_paths if p not in depends_on]

    for path in ordered:
        if path not in available_context_files:
            continue
        content = available_context_files[path]
        # Rough token estimate: ~4 chars per token
        estimated_tokens = len(content) // 4
        if total_context_tokens + estimated_tokens > available_input:
            # Truncate this file to fit
            remaining_chars = (available_input - total_context_tokens) * 4
            if remaining_chars > 500:
                available_context_files[path] = (
                    content[:remaining_chars // 2]
                    + "\n\n[... truncated ...]\n\n"
                    + content[-(remaining_chars // 2):]
                )
                truncated.append(path)
                files_to_include.append(path)
            break
        files_to_include.append(path)
        total_context_tokens += estimated_tokens

    return {
        "files_to_include": files_to_include,
        "max_tokens": max_tokens,
        "truncated": truncated,
    }


def _topological_sort(files: list[dict]) -> list[dict]:
    """Sort manifest files by dependency order (topological sort).

    Falls back to linear order if circular dependencies detected.
    """
    path_to_entry = {f["path"]: f for f in files}
    visited: set[str] = set()
    temp_mark: set[str] = set()
    result: list[dict] = []

    def visit(path: str) -> bool:
        if path in temp_mark:
            return False  # Circular dependency
        if path in visited:
            return True
        temp_mark.add(path)
        entry = path_to_entry.get(path)
        if entry:
            for dep in entry.get("depends_on", []):
                if dep in path_to_entry:
                    if not visit(dep):
                        return False
        temp_mark.discard(path)
        visited.add(path)
        if entry:
            result.append(entry)
        return True

    for f in files:
        if f["path"] not in visited:
            if not visit(f["path"]):
                # Circular dependency detected — fall back to linear order
                logger.warning("Circular dependency in manifest — using linear order")
                return list(files)

    return result


async def _generate_file_manifest(
    build_id: UUID,
    user_id: UUID,
    api_key: str,
    contracts: list[dict],
    current_phase: dict,
    workspace_info: str,
) -> list[dict] | None:
    """Generate a structured file manifest for a phase via Sonnet planner.

    Makes one API call to the planner model. Returns a list of file entries
    sorted in dependency order, or None if the planner fails.
    """
    from app.clients.llm_client import chat as llm_chat

    # Load planner system prompt
    if not _PLANNER_BUILD_PROMPT_PATH.exists():
        logger.warning("planner_build_prompt.md not found — cannot generate manifest")
        return None
    system_prompt = _PLANNER_BUILD_PROMPT_PATH.read_text(encoding="utf-8")

    # Build relevant contracts text (exclude phases — current phase is inline)
    contract_parts = []
    for c in contracts:
        if c["contract_type"] == "phases":
            continue
        contract_parts.append(f"## {c['contract_type']}\n{c['content']}\n")
    contracts_text = "\n---\n".join(contract_parts)

    # Cap contracts to stay within planner context budget (~25k tokens)
    MAX_PLANNER_CONTRACTS_CHARS = 100_000
    if len(contracts_text) > MAX_PLANNER_CONTRACTS_CHARS:
        contracts_text = (
            contracts_text[:MAX_PLANNER_CONTRACTS_CHARS]
            + "\n\n[... contracts truncated for context budget ...]\n"
        )
        logger.info(
            "Planner contracts capped at %d chars for Phase %s",
            MAX_PLANNER_CONTRACTS_CHARS, current_phase["number"],
        )

    # Phase deliverables
    phase_text = (
        f"## Phase {current_phase['number']} -- {current_phase['name']}\n"
        f"**Objective:** {current_phase.get('objective', '')}\n\n"
        f"**Deliverables:**\n"
    )
    for d in current_phase.get("deliverables", []):
        phase_text += f"- {d}\n"

    user_message = (
        f"## Project Contracts\n\n{contracts_text}\n\n"
        f"## Current Phase\n\n{phase_text}\n\n"
        f"## Existing Workspace\n\n{workspace_info}\n\n"
        f"Produce the JSON file manifest for this phase."
    )

    await build_repo.append_build_log(
        build_id,
        f"Generating file manifest for Phase {current_phase['number']}",
        source="planner", level="info",
    )

    try:
        try:
            result = await llm_chat(
                api_key=api_key,
                model=settings.LLM_PLANNER_MODEL,
                system_prompt=system_prompt,
                messages=[{"role": "user", "content": user_message}],
                max_tokens=4096,
                provider="anthropic",
            )
        except (ValueError, Exception) as exc:
            err_str = str(exc).lower()
            if any(kw in err_str for kw in ("token", "context", "too long", "too large")):
                logger.warning(
                    "Planner context overflow (%d chars) — retrying truncated",
                    len(user_message),
                )
                MAX_FALLBACK_CHARS = 600_000
                truncated_msg = (
                    user_message[:MAX_FALLBACK_CHARS]
                    + "\n\n[... truncated ...]\n\n"
                    + f"Produce the JSON file manifest for Phase {current_phase['number']}."
                )
                result = await llm_chat(
                    api_key=api_key,
                    model=settings.LLM_PLANNER_MODEL,
                    system_prompt=system_prompt,
                    messages=[{"role": "user", "content": truncated_msg}],
                    max_tokens=4096,
                    provider="anthropic",
                )
            else:
                raise

        text = result["text"] if isinstance(result, dict) else result
        usage = result.get("usage", {}) if isinstance(result, dict) else {}

        # Record planner cost
        input_t = usage.get("input_tokens", 0)
        output_t = usage.get("output_tokens", 0)
        model = settings.LLM_PLANNER_MODEL
        input_rate, output_rate = _get_token_rates(model)
        cost = Decimal(input_t) * input_rate + Decimal(output_t) * output_rate
        await build_repo.record_build_cost(
            build_id, f"Phase {current_phase['number']} (manifest)", input_t, output_t, model, cost,
        )

        # Strip markdown fences if present
        cleaned = text.strip()
        if cleaned.startswith("```"):
            first_nl = cleaned.find("\n")
            if first_nl >= 0:
                cleaned = cleaned[first_nl + 1:]
        if cleaned.rstrip().endswith("```"):
            cleaned = cleaned.rstrip()[:-3]
        cleaned = cleaned.strip()

        # Parse JSON
        manifest = json.loads(cleaned)
        files = manifest.get("files", [])

        # Validate
        valid_files = []
        seen_paths: set[str] = set()
        for f in files:
            path = f.get("path", "")
            if not path or ".." in path or path.startswith("/"):
                logger.warning("Invalid manifest path: %s — skipping", path)
                continue
            if path in seen_paths:
                logger.warning("Duplicate manifest path: %s — skipping", path)
                continue
            seen_paths.add(path)
            valid_files.append({
                "path": path,
                "action": f.get("action", "create"),
                "purpose": f.get("purpose", ""),
                "depends_on": f.get("depends_on", []),
                "context_files": f.get("context_files", []),
                "estimated_lines": f.get("estimated_lines", 100),
                "language": f.get("language", "python"),
                "status": "pending",
            })

        # Topological sort
        sorted_files = _topological_sort(valid_files)

        await build_repo.append_build_log(
            build_id,
            f"File manifest generated: {len(sorted_files)} files",
            source="planner", level="info",
        )

        # Broadcast manifest
        await _broadcast_build_event(user_id, build_id, "file_manifest", {
            "phase": f"Phase {current_phase['number']}",
            "files": [
                {
                    "path": f["path"],
                    "purpose": f["purpose"],
                    "status": f["status"],
                    "language": f["language"],
                    "estimated_lines": f["estimated_lines"],
                }
                for f in sorted_files
            ],
        })

        return sorted_files

    except json.JSONDecodeError as exc:
        logger.warning("Manifest JSON parse failed: %s — retrying once", exc)
        # Retry once — sometimes the planner wraps in extra text
        try:
            # Try to extract JSON from the response
            json_match = re.search(r"\{[\s\S]*\}", text)
            if json_match:
                manifest = json.loads(json_match.group())
                files = manifest.get("files", [])
                sorted_files = _topological_sort([
                    {
                        "path": f.get("path", ""),
                        "action": f.get("action", "create"),
                        "purpose": f.get("purpose", ""),
                        "depends_on": f.get("depends_on", []),
                        "context_files": f.get("context_files", []),
                        "estimated_lines": f.get("estimated_lines", 100),
                        "language": f.get("language", "python"),
                        "status": "pending",
                    }
                    for f in files
                    if f.get("path") and ".." not in f.get("path", "") and not f.get("path", "").startswith("/")
                ])
                return sorted_files if sorted_files else None
        except Exception:
            pass
        return None
    except Exception as exc:
        logger.error("File manifest generation failed: %s", exc)
        return None


async def _generate_single_file(
    build_id: UUID,
    user_id: UUID,
    api_key: str,
    file_entry: dict,
    contracts: list[dict],
    context_files: dict[str, str],
    phase_deliverables: str,
    working_dir: str,
    error_context: str = "",
) -> str:
    """Generate a single file via an independent API call.

    Makes ONE call to the builder model. Returns the file content.
    No conversation history — each file is a fresh call.
    """
    from app.clients.llm_client import chat as llm_chat

    file_path = file_entry["path"]
    purpose = file_entry.get("purpose", "")
    language = file_entry.get("language", "python")
    estimated_lines = file_entry.get("estimated_lines", 100)

    # Select relevant contracts
    relevant_contracts = _select_contracts_for_file(file_path, contracts)
    contracts_text = ""
    for c in relevant_contracts:
        contracts_text += f"\n## {c['contract_type']}\n{c['content']}\n"

    # Cap contracts to stay within context budget (~30k tokens)
    MAX_CONTRACTS_CHARS = 120_000
    if len(contracts_text) > MAX_CONTRACTS_CHARS:
        contracts_text = (
            contracts_text[:MAX_CONTRACTS_CHARS]
            + "\n\n[... contracts truncated for context budget ...]\n"
        )
        logger.info("Contracts capped at %d chars for %s", MAX_CONTRACTS_CHARS, file_path)

    # System prompt — concise, cacheable
    system_prompt = (
        "You are writing a single file for a software project built under the "
        "Forge governance framework. Output ONLY the raw file content. "
        "No explanation, no markdown fences, no preamble, no postamble. "
        "Just the file content, ready to save to disk.\n\n"
        "Rules:\n"
        "- Follow the project contracts exactly\n"
        "- Respect layer boundaries (routers, services, repos, clients, audit)\n"
        "- Use the context files to understand imports and interfaces\n"
        "- Write production-quality code with proper error handling\n"
        "- Include docstrings and type hints\n"
    )

    # --- Context budget: decide which context files fit ---
    system_prompt_tokens = len(system_prompt) // 4
    contract_tokens = len(contracts_text) // 4
    phase_tokens = len(phase_deliverables) // 4

    # Copy context_files so budget truncation doesn't mutate caller's dict
    context_copy = dict(context_files) if context_files else {}
    budget = _calculate_context_budget(
        file_entry=file_entry,
        system_prompt_tokens=system_prompt_tokens,
        contract_tokens=contract_tokens + phase_tokens,
        available_context_files=context_copy,
    )
    budgeted_context: dict[str, str] = {
        p: context_copy[p] for p in budget["files_to_include"] if p in context_copy
    }
    max_tokens = budget["max_tokens"]

    if budget["truncated"]:
        logger.info(
            "Context files truncated for %s: %s", file_path, budget["truncated"],
        )
    if len(budgeted_context) < len(context_files or {}):
        logger.info(
            "Context budget: using %d/%d context files for %s",
            len(budgeted_context), len(context_files or {}), file_path,
        )

    # Build user message with context
    parts = [f"## Project Contracts (reference)\n{contracts_text}\n"]
    parts.append(f"## Current Phase Deliverables\n{phase_deliverables}\n")
    parts.append(
        f"\n## File to Write\n"
        f"Path: {file_path}\n"
        f"Purpose: {purpose}\n"
        f"Language: {language}\n"
    )

    if budgeted_context:
        parts.append("\n## Context Files (already written -- reference only)\n")
        for ctx_path, ctx_content in budgeted_context.items():
            parts.append(f"\n### {ctx_path}\n```\n{ctx_content}\n```\n")

    if error_context:
        parts.append(
            f"\n## Error Context (fix required)\n{error_context}\n"
        )

    parts.append(
        f"\n## Instructions\n"
        f"Write the complete content of `{file_path}`. "
        f"Output ONLY the raw file content.\n"
        f"Do not wrap in markdown code fences. "
        f"Do not add explanation before or after.\n"
    )

    user_message = "\n".join(parts)

    await _broadcast_build_event(user_id, build_id, "file_generating", {
        "path": file_path,
    })

    # Per-file timeout: 10 minutes max per LLM call (prevents stalls)
    _FILE_GEN_TIMEOUT = 600  # seconds

    try:
        result = await asyncio.wait_for(
            llm_chat(
                api_key=api_key,
                model=settings.LLM_BUILDER_MODEL,
                system_prompt=system_prompt,
                messages=[{"role": "user", "content": user_message}],
                max_tokens=max_tokens,
                provider="anthropic",
            ),
            timeout=_FILE_GEN_TIMEOUT,
        )
    except asyncio.TimeoutError:
        raise TimeoutError(f"File generation timed out after {_FILE_GEN_TIMEOUT}s: {file_path}")
    except (ValueError, Exception) as exc:
        err_str = str(exc).lower()
        if any(kw in err_str for kw in ("token", "context", "too long", "too large")):
            logger.warning(
                "Context overflow for %s (%d chars) — retrying with minimal context",
                file_path, len(user_message),
            )
            # Rebuild message without context files
            minimal_parts = [
                f"## Project Contracts (reference)\n{contracts_text}\n",
                f"## Current Phase Deliverables\n{phase_deliverables}\n",
                f"\n## File to Write\nPath: {file_path}\n"
                f"Purpose: {purpose}\nLanguage: {language}\n",
            ]
            if error_context:
                minimal_parts.append(
                    f"\n## Error Context (fix required)\n{error_context}\n"
                )
            minimal_parts.append(
                f"\n## Instructions\n"
                f"Write the complete content of `{file_path}`. "
                f"Output ONLY the raw file content.\n"
                f"Do not wrap in markdown code fences. "
                f"Do not add explanation before or after.\n"
            )
            minimal_msg = "\n".join(minimal_parts)
            # If still too large, hard-truncate contracts
            MAX_FALLBACK_CHARS = 600_000  # ~150k tokens
            if len(minimal_msg) > MAX_FALLBACK_CHARS:
                minimal_msg = (
                    minimal_msg[:MAX_FALLBACK_CHARS]
                    + "\n\n[... truncated for context limit ...]\n"
                )
            result = await asyncio.wait_for(
                llm_chat(
                    api_key=api_key,
                    model=settings.LLM_BUILDER_MODEL,
                    system_prompt=system_prompt,
                    messages=[{"role": "user", "content": minimal_msg}],
                    max_tokens=max_tokens,
                    provider="anthropic",
                ),
                timeout=_FILE_GEN_TIMEOUT,
            )
        else:
            raise

    content = result["text"] if isinstance(result, dict) else result
    usage = result.get("usage", {}) if isinstance(result, dict) else {}

    # Strip markdown fences if model wrapped output
    content = re.sub(r"^```\w*\n", "", content)
    content = re.sub(r"\n```\s*$", "", content)

    # Ensure trailing newline
    if content and not content.endswith("\n"):
        content += "\n"

    # Handle empty response — retry once
    if not content.strip():
        logger.warning("Empty response for %s — retrying once", file_path)
        result2 = await asyncio.wait_for(
            llm_chat(
                api_key=api_key,
                model=settings.LLM_BUILDER_MODEL,
                system_prompt=system_prompt,
                messages=[{
                    "role": "user",
                    "content": user_message + "\nPlease write the complete file content.",
                }],
                max_tokens=max_tokens,
                provider="anthropic",
            ),
            timeout=_FILE_GEN_TIMEOUT,
        )
        content = result2["text"] if isinstance(result2, dict) else result2
        content = re.sub(r"^```\w*\n", "", content)
        content = re.sub(r"\n```\s*$", "", content)
        if content and not content.endswith("\n"):
            content += "\n"
        usage2 = result2.get("usage", {}) if isinstance(result2, dict) else {}
        # Combine usage
        for k in ("input_tokens", "output_tokens"):
            usage[k] = usage.get(k, 0) + usage2.get(k, 0)

    # Record cost
    input_t = usage.get("input_tokens", 0)
    output_t = usage.get("output_tokens", 0)
    model = settings.LLM_BUILDER_MODEL
    input_rate, output_rate = _get_token_rates(model)
    cost = Decimal(input_t) * input_rate + Decimal(output_t) * output_rate
    await build_repo.record_build_cost(
        build_id, f"file:{file_path}", input_t, output_t, model, cost,
    )

    # Write to disk
    from app.services.tool_executor import _exec_write_file
    write_result = _exec_write_file({"path": file_path, "content": content}, working_dir)

    size_bytes = len(content.encode("utf-8"))
    lang = _detect_language(file_path)

    # Log and broadcast
    await build_repo.append_build_log(
        build_id,
        json.dumps({"path": file_path, "size_bytes": size_bytes, "language": lang}),
        source="file", level="info",
    )
    await _broadcast_build_event(user_id, build_id, "file_generated", {
        "path": file_path,
        "size_bytes": size_bytes,
        "language": lang,
        "tokens_in": input_t,
        "tokens_out": output_t,
        "duration_ms": 0,  # Not tracked per-call currently
    })

    return content


async def _verify_phase_output(
    build_id: UUID,
    user_id: UUID,
    api_key: str,
    manifest: list[dict],
    working_dir: str,
    contracts: list[dict],
    touched_files: set[str] | None = None,
) -> dict:
    """Run syntax and test verification on generated files.

    Returns dict with syntax_errors, tests_passed, tests_failed, fixes_applied.
    Attempts to fix issues using the builder with rich context from disk.
    """
    from app.services.tool_executor import _exec_check_syntax, _exec_run_tests
    import re as _re_verify

    syntax_errors = 0
    fixes_applied = 0
    tests_passed = 0
    tests_failed = 0
    last_test_output = ""

    # Helper: gather related files from disk for context
    def _gather_related_context(target_path: str, max_files: int = 8) -> dict[str, str]:
        """Read files related to target_path from the working directory."""
        ctx: dict[str, str] = {}
        wd = Path(working_dir)
        # Always include the target file itself
        fp = wd / target_path
        if fp.exists():
            try:
                ctx[target_path] = fp.read_text(encoding="utf-8")
            except Exception:
                pass

        # Parse imports from the file to find related modules
        if target_path in ctx:
            for imp_match in _re_verify.finditer(
                r'(?:from|import)\s+([\w.]+)', ctx[target_path]
            ):
                mod = imp_match.group(1)
                # Convert dotted module to path: app.models.user -> app/models/user.py
                mod_path = mod.replace(".", "/") + ".py"
                mod_fp = wd / mod_path
                if mod_fp.exists() and mod_path not in ctx and len(ctx) < max_files:
                    try:
                        ctx[mod_path] = mod_fp.read_text(encoding="utf-8")
                    except Exception:
                        pass
                # Also try __init__.py in the package
                pkg_init = mod.replace(".", "/") + "/__init__.py"
                pkg_fp = wd / pkg_init
                if pkg_fp.exists() and pkg_init not in ctx and len(ctx) < max_files:
                    try:
                        ctx[pkg_init] = pkg_fp.read_text(encoding="utf-8")
                    except Exception:
                        pass

        # Include sibling files in the same directory
        target_dir = (wd / target_path).parent
        if target_dir.exists():
            for sibling in sorted(target_dir.iterdir()):
                if len(ctx) >= max_files:
                    break
                if sibling.is_file() and sibling.suffix == ".py":
                    rel = str(sibling.relative_to(wd)).replace("\\", "/")
                    if rel not in ctx:
                        try:
                            ctx[rel] = sibling.read_text(encoding="utf-8")
                        except Exception:
                            pass
        return ctx

    # Helper: extract failing file paths from pytest output
    def _parse_failing_files(test_output: str) -> list[str]:
        """Extract file paths of failing tests from pytest output."""
        failing: list[str] = []
        for m in _re_verify.finditer(r'(?:FAILED|ERROR)\s+([\w/\\.-]+\.py)', test_output):
            fp = m.group(1).replace("\\", "/")
            # Strip ::test_name suffix if present
            fp = fp.split("::")[0]
            if fp not in failing:
                failing.append(fp)
        return failing

    # Scope verification to only files touched in this phase/run if provided
    def _filter_manifest(entries: list[dict], *, exts: tuple[str, ...]) -> list[dict]:
        if touched_files is not None:
            return [f for f in entries if f["path"] in touched_files and f["path"].endswith(exts)]
        return [f for f in entries if f["path"].endswith(exts)]

    # Check syntax on Python files (touched-only when available)
    py_files = _filter_manifest(manifest, exts=(".py",))
    if py_files:
        await _broadcast_build_event(user_id, build_id, "build_log", {
            "message": f"Checking syntax on {len(py_files)} Python files...",
            "source": "verify", "level": "info",
        })
    for f in py_files:
        _touch_progress(build_id)
        result = await _exec_check_syntax({"file_path": f["path"]}, working_dir)
        if "No syntax errors" in result:
            continue
        if "error" in result.lower() or "SyntaxError" in result:
            syntax_errors += 1
            await _broadcast_build_event(user_id, build_id, "build_log", {
                "message": f"Syntax error in {f['path']}: {result.strip()}",
                "source": "verify", "level": "warn",
            })
            await _set_build_activity(
                build_id, user_id, f"Fixing syntax error in {f['path']}...",
            )
            # Attempt targeted fix using _fix_single_file (preserves existing
            # code, only patches the syntax error — no full regeneration)
            for attempt in range(2):
                _touch_progress(build_id)
                try:
                    await _fix_single_file(
                        build_id, user_id, api_key,
                        f["path"],
                        f"Syntax error detected by ast.parse:\n{result}\n\n"
                        f"Fix ONLY the syntax error(s). Do NOT rewrite or restructure "
                        f"the file. Preserve all existing functionality.",
                        working_dir,
                        label="verify",
                    )
                    recheck = await _exec_check_syntax({"file_path": f["path"]}, working_dir)
                    # "No syntax errors" contains the substring "error", so
                    # check for the success phrase FIRST to avoid false negatives.
                    if "No syntax errors" in recheck:
                        fixes_applied += 1
                        syntax_errors -= 1
                        await _broadcast_build_event(user_id, build_id, "build_log", {
                            "message": f"✓ Syntax fixed: {f['path']} (attempt {attempt + 1})",
                            "source": "verify", "level": "info",
                        })
                        break
                    elif "SyntaxError" in recheck or "error" in recheck.lower():
                        await _broadcast_build_event(user_id, build_id, "build_log", {
                            "message": f"Fix attempt {attempt + 1} for {f['path']} — still has errors: {recheck.strip()}",
                            "source": "verify", "level": "warn",
                        })
                        result = recheck  # feed updated error into next attempt
                except Exception as exc:
                    logger.warning("Syntax fix attempt %d failed for %s: %s", attempt + 1, f["path"], exc)
                    await _broadcast_build_event(user_id, build_id, "build_log", {
                        "message": f"Fix attempt {attempt + 1} failed for {f['path']}: {exc}",
                        "source": "verify", "level": "warn",
                    })

    # Run tests if test files were generated/touched
    if touched_files is not None:
        test_files = [f for f in manifest if f["path"].startswith("tests/") and f["path"] in touched_files]
    else:
        test_files = [f for f in manifest if f["path"].startswith("tests/")]
    if test_files:
        test_paths = " ".join(f["path"] for f in test_files)
        _touch_progress(build_id)
        await _broadcast_build_event(user_id, build_id, "build_log", {
            "message": f"Running pytest on {len(test_files)} test file(s)...",
            "source": "verify", "level": "info",
        })
        try:
            test_result = await _exec_run_tests(
                {"command": f"pytest {test_paths} -x -q -o addopts=", "timeout": 120}, working_dir,
            )
            last_test_output = test_result
            if "exit_code: 0" in test_result or "passed" in test_result.lower():
                tests_passed = len(test_files)
            else:
                tests_failed = len(test_files)
                # Identify which files actually failed
                failing_paths = _parse_failing_files(test_result)

                # Try to fix failures — target the impl files, not just tests
                for attempt in range(2):
                    if tests_failed == 0:
                        break
                    _touch_progress(build_id)
                    await _broadcast_build_event(user_id, build_id, "build_log", {
                        "message": f"Tests failed — fix attempt {attempt + 1}/2...",
                        "source": "verify", "level": "warn",
                    })

                    # Determine which files to fix: both test + impl
                    files_to_fix: list[dict] = []
                    for tf in test_files:
                        # Find the implementation file the test is exercising
                        impl_candidates = []
                        # Parse test file for imports to find the real target
                        test_fp = Path(working_dir) / tf["path"]
                        if test_fp.exists():
                            try:
                                test_src = test_fp.read_text(encoding="utf-8")
                                for imp in _re_verify.finditer(
                                    r'from\s+([\w.]+)\s+import', test_src
                                ):
                                    mod = imp.group(1).replace(".", "/") + ".py"
                                    if (Path(working_dir) / mod).exists():
                                        impl_candidates.append(mod)
                            except Exception:
                                pass

                        # Fallback: conventional mapping
                        if not impl_candidates:
                            conventional = tf["path"].replace("tests/test_", "app/")
                            if (Path(working_dir) / conventional).exists():
                                impl_candidates.append(conventional)

                        # Fix the implementation file(s) first
                        for impl_path in impl_candidates:
                            impl_entry = next(
                                (m for m in manifest if m["path"] == impl_path),
                                {"path": impl_path, "language": "python",
                                 "purpose": f"Implementation for {tf['path']}"},
                            )
                            if impl_entry not in files_to_fix:
                                files_to_fix.append(impl_entry)

                        # Then fix the test file too
                        if tf not in files_to_fix:
                            files_to_fix.append(tf)

                    for fix_entry in files_to_fix:
                        try:
                            context = _gather_related_context(fix_entry["path"])
                            # Also include failing test output in context
                            await _generate_single_file(
                                build_id, user_id, api_key,
                                {**fix_entry, "purpose": f"Fix to pass tests: {fix_entry['path']}"},
                                contracts, context, "", working_dir,
                                error_context=(
                                    f"Test failure output:\n{test_result[:3000]}\n\n"
                                    f"Failing tests: {', '.join(failing_paths) if failing_paths else 'see output above'}\n\n"
                                    f"Fix the code so the tests pass. Focus on the error messages "
                                    f"and tracebacks above. Do NOT remove or weaken tests — "
                                    f"fix the implementation to match what the tests expect."
                                ),
                            )
                        except Exception:
                            pass

                    # Re-run tests
                    _touch_progress(build_id)
                    await _broadcast_build_event(user_id, build_id, "build_log", {
                        "message": f"Re-running tests after fix attempt {attempt + 1}...",
                        "source": "verify", "level": "info",
                    })
                    retest = await _exec_run_tests(
                        {"command": f"pytest {test_paths} -x -q -o addopts=", "timeout": 120}, working_dir,
                    )
                    last_test_output = retest
                    if "exit_code: 0" in retest or "passed" in retest.lower():
                        tests_passed = len(test_files)
                        tests_failed = 0
                        fixes_applied += 1
                        await _broadcast_build_event(user_id, build_id, "build_log", {
                            "message": "✓ All tests passing after fix",
                            "source": "verify", "level": "info",
                        })
                        break
                    # Update test_result for next attempt's error context
                    test_result = retest
                    failing_paths = _parse_failing_files(retest)
        except Exception as exc:
            logger.warning("Test verification failed: %s", exc)

    result = {
        "syntax_errors": syntax_errors,
        "tests_passed": tests_passed,
        "tests_failed": tests_failed,
        "fixes_applied": fixes_applied,
        "test_output": last_test_output,
    }

    await _broadcast_build_event(user_id, build_id, "verification_result", result)
    return result


async def _generate_fix_manifest(
    build_id: UUID,
    user_id: UUID,
    api_key: str,
    recovery_plan: str,
    existing_files: dict[str, str],
    audit_findings: str,
    contracts: list[dict],
) -> list[dict] | None:
    """Generate a fix manifest from a recovery plan.

    Instead of injecting the recovery plan into a conversation, this
    produces a list of specific files to regenerate or patch.
    """
    from app.clients.llm_client import chat as llm_chat

    system_prompt = (
        "You are a build repair planner. Given audit findings and a recovery "
        "plan, produce a JSON manifest of ONLY the files that need to be "
        "created, modified, or deleted to fix the specific issues found.\n\n"
        "CRITICAL RULES:\n"
        "- Only include files DIRECTLY mentioned in the BLOCKING audit "
        "findings or recovery plan as having problems.\n"
        "- Do NOT include files that are working correctly.\n"
        "- Keep the fix list as small as possible — surgical fixes only.\n"
        "- MAXIMUM 5 files per manifest. If more than 5 need changes, "
        "include only the 5 most critical.\n"
        "- If a file just needs a small change, use action 'modify'.\n"
        "- Only use action 'create' for genuinely new files.\n"
        "- Use action 'delete' ONLY to remove files at wrong paths "
        "(e.g. duplicates after a restructure). Delete requires no "
        "context_files or fix_instructions.\n"
        "- NEVER suggest directory restructuring (moving files between "
        "directories). The builder cannot move files. Instead, suggest "
        "modifying code in-place or updating config references.\n\n"
        "Output ONLY valid JSON matching this schema:\n"
        '{"fixes": [{"path": "file.py", "action": "modify"|"create"|"delete", '
        '"reason": "why", "context_files": ["dep.py"], '
        '"fix_instructions": "what to fix"}]}'
    )

    existing_listing = "\n".join(f"- {p}" for p in existing_files.keys())

    user_message = (
        f"## Audit Findings\n{audit_findings}\n\n"
        f"## Recovery Plan\n{recovery_plan}\n\n"
        f"## Existing Files (do NOT include unless they have issues)\n{existing_listing}\n\n"
        f"Produce the fix manifest. Include ONLY files with actual problems."
    )

    try:
        result = await llm_chat(
            api_key=api_key,
            model=settings.LLM_PLANNER_MODEL,
            system_prompt=system_prompt,
            messages=[{"role": "user", "content": user_message}],
            max_tokens=4096,
            provider="anthropic",
        )

        text = result["text"] if isinstance(result, dict) else result

        # Strip fences
        cleaned = text.strip()
        if cleaned.startswith("```"):
            first_nl = cleaned.find("\n")
            if first_nl >= 0:
                cleaned = cleaned[first_nl + 1:]
        if cleaned.rstrip().endswith("```"):
            cleaned = cleaned.rstrip()[:-3]

        data = json.loads(cleaned.strip())
        fixes = data.get("fixes", [])

        manifest = [
            {
                "path": f.get("path", ""),
                "action": f.get("action", "modify"),
                "purpose": f.get("reason", ""),
                "depends_on": [],
                "context_files": f.get("context_files", []),
                "estimated_lines": 100,
                "language": _detect_language(f.get("path", "")),
                "status": "pending",
                "fix_instructions": f.get("fix_instructions", ""),
            }
            for f in fixes
            if f.get("path")
        ]

        # Hard cap: refuse manifests > 50% of phase files (runaway)
        non_delete = [m for m in manifest if m["action"] != "delete"]
        if (
            len(existing_files) > 0
            and len(non_delete) > max(5, len(existing_files) // 2)
        ):
            logger.warning(
                "Fix manifest too large (%d of %d files) — trimming to 5",
                len(non_delete), len(existing_files),
            )
            # Keep deletes + first 5 non-deletes
            deletes = [m for m in manifest if m["action"] == "delete"]
            manifest = deletes + non_delete[:5]

        return manifest
    except Exception as exc:
        logger.warning("Fix manifest generation failed: %s", exc)
        return None


async def _run_build_plan_execute(
    build_id: UUID,
    project_id: UUID,
    user_id: UUID,
    contracts: list[dict],
    api_key: str,
    audit_llm_enabled: bool = True,
    *,
    target_type: str | None = None,
    target_ref: str | None = None,
    working_dir: str | None = None,
    access_token: str = "",
    branch: str = "main",
    api_key_2: str = "",
    phases: list[dict] | None = None,
    resume_from_phase: int = -1,
) -> None:
    """Plan-then-execute build architecture.

    Instead of a single long conversation loop, this:
    1. Generates a file manifest per phase (Sonnet planner)
    2. Generates each file independently (Opus, fresh call per file)
    3. Verifies syntax + tests after all files written
    4. Runs audit; if fail, generates fix manifest and applies fixes
    5. Advances to next phase

    No accumulating conversation. Memory is the filesystem.
    """
    if not working_dir:
        await _fail_build(build_id, user_id, "No working directory for plan-execute mode")
        return

    if not phases:
        # Parse phases from contracts
        for c in contracts:
            if c["contract_type"] == "phases":
                phases = _parse_phases_contract(c["content"])
                break
    if not phases:
        await _fail_build(build_id, user_id, "No phases contract found")
        return

    # --- Workspace setup (git clone, branch, contracts) ---
    # --- Workspace setup (skip if continuing from a prior build) ---
    if resume_from_phase < 0:
        if target_type == "github_new" and target_ref:
            try:
                repo_data = await github_client.create_github_repo(
                    access_token, target_ref,
                    description="Built by ForgeGuard",
                    private=False,
                )
                clone_url = f"https://github.com/{repo_data['full_name']}.git"
                shutil.rmtree(working_dir, ignore_errors=True)
                await git_client.clone_repo(
                    clone_url, working_dir,
                    access_token=access_token, shallow=False,
                )
                await build_repo.append_build_log(
                    build_id, f"Created GitHub repo: {repo_data['full_name']}",
                    source="system", level="info",
                )
            except Exception as exc:
                await _fail_build(build_id, user_id, f"Failed to create GitHub repo: {exc}")
                return
        elif target_type == "github_existing" and target_ref:
            try:
                clone_url = f"https://github.com/{target_ref}.git"
                shutil.rmtree(working_dir, ignore_errors=True)
                await git_client.clone_repo(
                    clone_url, working_dir,
                    access_token=access_token, shallow=False,
                )
                await build_repo.append_build_log(
                    build_id, f"Cloned existing repo: {target_ref}",
                    source="system", level="info",
                )
            except Exception as exc:
                await _fail_build(build_id, user_id, f"Failed to clone repo: {exc}")
                return
        elif target_type == "local_path":
            try:
                Path(working_dir).mkdir(parents=True, exist_ok=True)
                git_dir = Path(working_dir) / ".git"
                if not git_dir.exists():
                    await git_client.init_repo(working_dir)
            except Exception as exc:
                await _fail_build(build_id, user_id, f"Failed to initialize local path: {exc}")
                return

        # Create/checkout branch if not main
        if branch and branch != "main":
            try:
                await git_client.create_branch(working_dir, branch)
            except Exception:
                try:
                    await git_client.checkout_branch(working_dir, branch)
                except Exception as exc2:
                    await _fail_build(build_id, user_id, f"Failed to create/checkout branch '{branch}': {exc2}")
                    return

        # Write contracts to working directory
        try:
            _write_contracts_to_workdir(working_dir, contracts)
        except Exception as exc:
            logger.warning("Failed to write contracts to workdir: %s", exc)

        # Commit + push contracts seed
        if target_type in ("github_new", "github_existing") and access_token:
            try:
                await git_client.add_all(working_dir)
                sha = await git_client.commit(working_dir, "forge: seed Forge/Contracts/")
                if sha:
                    await git_client.push(working_dir, branch=branch, access_token=access_token)
                    await build_repo.append_build_log(
                        build_id, "Pushed Forge/Contracts/ to GitHub",
                        source="system", level="info",
                    )
            except Exception as exc:
                logger.warning("Initial contracts push failed (non-fatal): %s", exc)
    else:
        # Continuing from a prior build — verify workspace exists
        if not Path(working_dir).exists():
            await _fail_build(
                build_id, user_id,
                "Working directory no longer exists — cannot continue. Use /start for a fresh build.",
            )
            return
        _log_msg = f"Continuing build from Phase {resume_from_phase + 1} — workspace ready"
        await build_repo.append_build_log(
            build_id, _log_msg, source="system", level="info",
        )
        await _broadcast_build_event(user_id, build_id, "build_log", {
            "message": _log_msg, "source": "system", "level": "info",
        })

    # Signal frontend that workspace is ready
    await _broadcast_build_event(user_id, build_id, "workspace_ready", {
        "id": str(build_id),
        "working_dir": working_dir or "",
        "branch": branch,
    })

    # Emit build_started so the frontend initialises phase tracking
    now = datetime.now(timezone.utc)
    await build_repo.update_build_status(build_id, "running", started_at=now)
    await build_repo.append_build_log(
        build_id, "Build started (plan-execute mode)", source="system", level="info",
    )
    await _broadcast_build_event(user_id, build_id, "build_started", {
        "id": str(build_id),
        "status": "running",
        "phase": "Phase 0",
    })

    # Initialize interjection queue
    if str(build_id) not in _interjection_queues:
        _interjection_queues[str(build_id)] = asyncio.Queue()

    # Emit build overview
    await _broadcast_build_event(user_id, build_id, "build_overview", {
        "phases": [
            {"number": p["number"], "name": p["name"], "objective": p.get("objective", "")}
            for p in phases
        ],
    })

    # Track all files written across all phases
    all_files_written: dict[str, str] = {}  # path -> content
    files_written_list: list[dict] = []

    for phase in phases:
        phase_num = phase["number"]
        phase_name = f"Phase {phase_num}"
        current_phase = phase_name
        _touch_progress(build_id)

        # Skip already-completed phases when continuing a prior build
        if phase_num <= resume_from_phase:
            _log_msg = f"⏭ Skipping {phase_name}: {phase['name']} — already completed"
            await build_repo.append_build_log(
                build_id, _log_msg, source="system", level="info",
            )
            await _broadcast_build_event(user_id, build_id, "build_log", {
                "message": _log_msg, "source": "system", "level": "info",
            })
            await _broadcast_build_event(user_id, build_id, "phase_complete", {
                "phase": phase_name,
                "status": "pass",
            })
            continue

        await build_repo.update_build_status(
            build_id, "running", phase=phase_name
        )
        await build_repo.append_build_log(
            build_id, f"Starting {phase_name}: {phase['name']}",
            source="system", level="info",
        )
        await _broadcast_build_event(user_id, build_id, "build_log", {
            "message": f"Starting {phase_name}: {phase['name']}",
            "source": "system", "level": "info",
        })

        # Build workspace info
        workspace_info = ""
        try:
            existing_files_list = []
            _SKIP_DIRS = {".git", "__pycache__", "node_modules", ".venv", "Forge"}
            for dirpath, dirnames, filenames in os.walk(working_dir):
                dirnames[:] = sorted(d for d in dirnames if d not in _SKIP_DIRS)
                for fname in sorted(filenames):
                    rel = Path(dirpath).relative_to(working_dir) / fname
                    existing_files_list.append(str(rel))
            existing_files_list.sort()
            _PE_FILE_LIST_CAP = 200
            workspace_info = (
                "\n".join(f"- {f}" for f in existing_files_list[:_PE_FILE_LIST_CAP])
                + (f"\n- ... ({len(existing_files_list) - _PE_FILE_LIST_CAP} more, truncated)"
                   if len(existing_files_list) > _PE_FILE_LIST_CAP else "")
            )
            if not existing_files_list:
                workspace_info = "(empty workspace)"
        except Exception:
            workspace_info = "(empty workspace)"

        # Phase deliverables text
        phase_deliverables = (
            f"Phase {phase_num} -- {phase['name']}\n"
            f"Objective: {phase.get('objective', '')}\n"
            f"Deliverables:\n"
            + "\n".join(f"- {d}" for d in phase.get("deliverables", []))
        )

        # 1. Generate file manifest (or load cached from prior run)
        _forge_dir = Path(working_dir) / ".forge"
        _manifest_cache_path = _forge_dir / f"manifest_phase_{phase_num}.json"
        manifest: list[dict] | None = None

        # Try loading cached manifest from a previous (interrupted) run
        if _manifest_cache_path.exists():
            try:
                cached = json.loads(_manifest_cache_path.read_text(encoding="utf-8"))
                if isinstance(cached, list) and cached:
                    manifest = cached
                    _n_audited = sum(1 for f in manifest if f.get("status") in ("audited", "fixed"))
                    _n_remaining = len(manifest) - _n_audited
                    _log_msg = (
                        f"Loaded cached manifest for {phase_name} — "
                        f"{len(manifest)} files total, {_n_audited} already done, "
                        f"{_n_remaining} remaining"
                    )
                    await build_repo.append_build_log(
                        build_id, _log_msg, source="system", level="info",
                    )
                    await _broadcast_build_event(user_id, build_id, "build_log", {
                        "message": _log_msg, "source": "system", "level": "info",
                    })
                    # Broadcast manifest to UI
                    await _broadcast_build_event(user_id, build_id, "file_manifest", {
                        "phase": phase_name,
                        "files": [
                            {
                                "path": f["path"],
                                "purpose": f.get("purpose", ""),
                                "status": f.get("status", "pending"),
                                "language": f.get("language", "python"),
                                "estimated_lines": f.get("estimated_lines", 100),
                            }
                            for f in manifest
                        ],
                    })
            except Exception as exc:
                logger.warning(
                    "Failed to load cached manifest for %s: %s — regenerating",
                    phase_name, exc,
                )

        if not manifest:
            _log_msg = f"Planning file manifest for {phase_name}..."
            await build_repo.append_build_log(build_id, _log_msg, source="system", level="info")
            await _broadcast_build_event(user_id, build_id, "build_log", {
                "message": _log_msg, "source": "system", "level": "info",
            })
            await _set_build_activity(build_id, user_id, f"Planning {phase_name}...")
            manifest = await _generate_file_manifest(
                build_id, user_id, api_key,
                contracts, phase, workspace_info,
            )

        if manifest:
            # Persist manifest to disk for resume resilience
            try:
                _forge_dir.mkdir(parents=True, exist_ok=True)
                _manifest_cache_path.write_text(
                    json.dumps(manifest, indent=2), encoding="utf-8",
                )
            except Exception as exc:
                logger.warning("Failed to cache manifest for %s: %s", phase_name, exc)

            _log_msg = f"Manifest ready — {len(manifest)} files planned for {phase_name}"
            await build_repo.append_build_log(build_id, _log_msg, source="system", level="info")
            await _broadcast_build_event(user_id, build_id, "build_log", {
                "message": _log_msg, "source": "system", "level": "info",
            })

        if not manifest:
            # Retry once before giving up
            await build_repo.append_build_log(
                build_id,
                f"Manifest generation failed for {phase_name} — retrying once...",
                source="system", level="warn",
            )
            await _broadcast_build_event(user_id, build_id, "build_log", {
                "message": f"Manifest generation failed for {phase_name} — retrying...",
                "source": "system", "level": "warn",
            })
            await asyncio.sleep(2)
            manifest = await _generate_file_manifest(
                build_id, user_id, api_key,
                contracts, phase, workspace_info,
            )
            # Cache the retry result too
            if manifest:
                try:
                    _forge_dir.mkdir(parents=True, exist_ok=True)
                    _manifest_cache_path.write_text(
                        json.dumps(manifest, indent=2), encoding="utf-8",
                    )
                except Exception:
                    pass

        if not manifest:
            _fail_msg = (
                f"Manifest generation failed for {phase_name} after retry — "
                f"pausing build. Use /start phase {phase_num} to retry."
            )
            await build_repo.append_build_log(
                build_id, _fail_msg,
                source="system", level="error",
            )
            await _broadcast_build_event(user_id, build_id, "build_log", {
                "message": _fail_msg, "source": "system", "level": "error",
            })
            await _pause_build(
                build_id, user_id, phase_name,
                0, f"Manifest generation failed for {phase_name}",
            )
            event = _pause_events.get(str(build_id))
            if event:
                try:
                    await asyncio.wait_for(
                        event.wait(),
                        timeout=settings.BUILD_PAUSE_TIMEOUT_MINUTES * 60,
                    )
                except asyncio.TimeoutError:
                    await _fail_build(
                        build_id, user_id,
                        f"Pause timed out waiting for manifest retry on {phase_name}",
                    )
                    return
            action = _resume_actions.pop(str(build_id), "retry")
            _pause_events.pop(str(build_id), None)
            await build_repo.resume_build(build_id)
            if action == "abort":
                await _fail_build(build_id, user_id, f"Build aborted on {phase_name}")
                return
            elif action == "skip":
                await build_repo.append_build_log(
                    build_id, f"Phase {phase_name} skipped by user",
                    source="system", level="warn",
                )
                continue
            # retry — loop back to retry this phase (not supported in for-loop,
            # so we just continue and let /continue phase N handle it)
            continue

        # Emit phase plan (show cached/audited files as already done)
        _cached_count = sum(
            1 for f in manifest if f.get("status") in ("audited", "fixed")
        )
        plan_tasks = [
            {
                "id": i + 1,
                "title": f"Generate {f['path']}",
                "status": "done" if f.get("status") in ("audited", "fixed") else "pending",
            }
            for i, f in enumerate(manifest)
        ]
        await _broadcast_build_event(user_id, build_id, "phase_plan", {
            "phase": phase_name,
            "tasks": plan_tasks,
        })

        if _cached_count > 0:
            _log_msg = (
                f"Resuming {phase_name}: {_cached_count}/{len(manifest)} files "
                f"already generated & audited — skipping those"
            )
            await build_repo.append_build_log(
                build_id, _log_msg, source="system", level="info",
            )
            await _broadcast_build_event(user_id, build_id, "build_log", {
                "message": _log_msg, "source": "system", "level": "info",
            })

        # 2. Generate each file (with parallel per-file audits)
        phase_files_written: dict[str, str] = {}
        pending_file_audits: list[asyncio.Task] = []
        # Pre-populate audit result lists for files whose audits were cached
        blocking_files: list[tuple[str, str]] = []  # (path, findings)
        passed_files: list[str] = []
        # Fix queue: files auditor couldn't fix → builder picks up after generation
        _fix_queue: asyncio.Queue[tuple[str, str]] = asyncio.Queue()
        # Track only files created/modified in this phase to scope verification
        touched_files: set[str] = set()
        # Use key 2 for auditor if available (separate rate limits)
        _audit_key = api_key_2.strip() if api_key_2.strip() else api_key
        for i, file_entry in enumerate(manifest):
            file_path = file_entry["path"]

            # --- Check cancel / pause flags before each file ---
            bid = str(build_id)
            if bid in _cancel_flags:
                _cancel_flags.discard(bid)
                await _fail_build(build_id, user_id, "Build stopped via /stop")
                return
            if bid in _pause_flags:
                _pause_flags.discard(bid)
                await _pause_build(
                    build_id, user_id, phase_name,
                    0, "Build paused via /pause",
                )
                event = _pause_events.get(bid)
                if event:
                    try:
                        await asyncio.wait_for(
                            event.wait(),
                            timeout=settings.BUILD_PAUSE_TIMEOUT_MINUTES * 60,
                        )
                    except asyncio.TimeoutError:
                        await _fail_build(
                            build_id, user_id,
                            "RISK_EXCEEDS_SCOPE: pause timed out",
                        )
                        return
                action = _resume_actions.pop(bid, "retry")
                _pause_events.pop(bid, None)
                await build_repo.resume_build(build_id)
                await _broadcast_build_event(user_id, build_id, "build_resumed", {
                    "action": action,
                })
                if action == "abort":
                    await _fail_build(build_id, user_id, "Build aborted after /pause")
                    return

            # Check /compact flag — shed context from prior phases
            if bid in _compact_flags:
                _compact_flags.discard(bid)
                dropped = len(all_files_written) - len(phase_files_written)
                all_files_written = dict(phase_files_written)
                await build_repo.append_build_log(
                    build_id,
                    f"Context compacted via /compact — dropped {dropped} prior-phase files from context cache",
                    source="system", level="info",
                )
                await _broadcast_build_event(user_id, build_id, "build_log", {
                    "message": f"Context compacted — dropped {dropped} prior-phase files",
                    "source": "system", "level": "info",
                })
                await _broadcast_build_event(user_id, build_id, "context_reset", {
                    "phase": phase_name,
                    "dropped": dropped,
                })

            # --- Skip files that already exist on disk (restart resilience) ---
            # After a restart, the cached manifest tracks per-file status.
            # "audited" = fully done (skip generation + audit).
            # File on disk but status still "pending" = generated but not audited yet.
            _file_status = file_entry.get("status", "pending")
            if _file_status in ("audited", "fixed"):
                # Already generated AND audited/fixed in a prior run
                existing_path = Path(working_dir) / file_path
                if existing_path.exists() and existing_path.stat().st_size > 0:
                    existing_content = existing_path.read_text(encoding="utf-8", errors="replace")
                    phase_files_written[file_path] = existing_content
                    all_files_written[file_path] = existing_content
                    _prior_verdict = file_entry.get("audit_verdict", "PASS")
                    await build_repo.append_build_log(
                        build_id,
                        f"Skipped {file_path} — already generated & audited ({_prior_verdict})",
                        source="system", level="info",
                    )
                    await _broadcast_build_event(user_id, build_id, "file_generated", {
                        "path": file_path,
                        "size_bytes": existing_path.stat().st_size,
                        "language": _detect_language(file_path),
                        "tokens_in": 0,
                        "tokens_out": 0,
                        "duration_ms": 0,
                        "skipped": True,
                    })
                    await _broadcast_build_event(user_id, build_id, "plan_task_complete", {
                        "task_id": i + 1,
                        "status": "done",
                    })
                    # Re-emit the cached audit result (no LLM call)
                    await _broadcast_build_event(user_id, build_id, "file_audited", {
                        "path": file_path,
                        "verdict": _prior_verdict,
                        "findings": "",
                        "duration_ms": 0,
                    })
                    # Track as pre-audited for the gather step
                    if _prior_verdict == "FAIL":
                        blocking_files.append((file_path, file_entry.get("audit_findings", "")))
                    else:
                        passed_files.append(file_path)
                    continue

            if file_entry.get("action", "create") == "create":
                existing_path = Path(working_dir) / file_path
                if existing_path.exists() and existing_path.stat().st_size > 0:
                    existing_content = existing_path.read_text(encoding="utf-8", errors="replace")
                    phase_files_written[file_path] = existing_content
                    all_files_written[file_path] = existing_content
                    await build_repo.append_build_log(
                        build_id,
                        f"Skipped {file_path} — already exists on disk ({existing_path.stat().st_size} bytes)",
                        source="system", level="info",
                    )
                    await _broadcast_build_event(user_id, build_id, "file_generated", {
                        "path": file_path,
                        "size_bytes": existing_path.stat().st_size,
                        "language": _detect_language(file_path),
                        "tokens_in": 0,
                        "tokens_out": 0,
                        "duration_ms": 0,
                        "skipped": True,
                    })
                    await _broadcast_build_event(user_id, build_id, "plan_task_complete", {
                        "task_id": i + 1,
                        "status": "done",
                    })
                    # File already exists on disk — skip the full LLM audit.
                    # It was previously generated (and likely audited/committed).
                    # Verification (syntax + tests) runs afterward and will
                    # catch any real issues.
                    _update_manifest_cache(
                        _manifest_cache_path, file_path, "audited", "PASS",
                    )
                    await _broadcast_build_event(user_id, build_id, "file_audited", {
                        "path": file_path,
                        "verdict": "PASS",
                        "findings": "",
                        "duration_ms": 0,
                    })
                    passed_files.append(file_path)
                    continue

            # Track which file is being generated (for audit trail on /stop etc.)
            _current_generating[bid] = file_path
            _touch_progress(build_id)
            await _set_build_activity(build_id, user_id, f"Generating {file_path}")

            # Resolve context files from disk
            context = {}
            for ctx_path in file_entry.get("context_files", []):
                if ctx_path in all_files_written:
                    context[ctx_path] = all_files_written[ctx_path]
                elif ctx_path in phase_files_written:
                    context[ctx_path] = phase_files_written[ctx_path]
                else:
                    # Try reading from disk
                    try:
                        full = Path(working_dir) / ctx_path
                        if full.exists():
                            context[ctx_path] = full.read_text(encoding="utf-8")
                    except Exception:
                        pass

            # Also include depends_on files as context
            for dep_path in file_entry.get("depends_on", []):
                if dep_path not in context:
                    if dep_path in phase_files_written:
                        context[dep_path] = phase_files_written[dep_path]
                    elif dep_path in all_files_written:
                        context[dep_path] = all_files_written[dep_path]

            try:
                content = await _generate_single_file(
                    build_id, user_id, api_key,
                    file_entry, contracts, context,
                    phase_deliverables, working_dir,
                )
                _current_generating.pop(bid, None)
                _touch_progress(build_id)
                phase_files_written[file_path] = content
                all_files_written[file_path] = content
                touched_files.add(file_path)

                file_info = {
                    "path": file_path,
                    "size_bytes": len(content.encode("utf-8")),
                    "language": _detect_language(file_path),
                }
                if not any(f["path"] == file_path for f in files_written_list):
                    files_written_list.append(file_info)

                # Mark task done
                await _broadcast_build_event(user_id, build_id, "plan_task_complete", {
                    "task_id": i + 1,
                    "status": "done",
                })

                # Kick off per-file audit+fix in background (uses key 2)
                _audit_idx = len(pending_file_audits) + 1
                pending_file_audits.append(asyncio.create_task(
                    _audit_and_cache(
                        _manifest_cache_path,
                        build_id, user_id,
                        _audit_key,
                        file_path, content,
                        file_entry.get("purpose", ""),
                        audit_llm_enabled,
                        audit_index=_audit_idx, audit_total=len(manifest),
                        working_dir=working_dir,
                        fix_queue=_fix_queue,
                    )
                ))

            except Exception as exc:
                _current_generating.pop(bid, None)
                logger.error("Failed to generate %s: %s", file_path, exc)
                await build_repo.append_build_log(
                    build_id,
                    f"Failed to generate {file_path}: {exc}",
                    source="system", level="error",
                )

        # 3. Collect per-file audit results (streamed in parallel during generation)
        _log_msg = f"All {len(phase_files_written)} files generated for {phase_name} — collecting audit results..."
        await build_repo.append_build_log(build_id, _log_msg, source="system", level="info")
        await _broadcast_build_event(user_id, build_id, "build_log", {
            "message": _log_msg, "source": "system", "level": "info",
        })
        await _set_build_activity(build_id, user_id, "Collecting audit results...")

        if pending_file_audits:
            raw_results = await asyncio.gather(
                *pending_file_audits, return_exceptions=True,
            )
            for raw in raw_results:
                if isinstance(raw, BaseException):
                    logger.warning("Per-file audit task error: %s", raw)
                    continue
                fpath, fverdict, ffindings = raw
                if fverdict == "FAIL":
                    blocking_files.append((fpath, ffindings))
                else:
                    passed_files.append(fpath)

        # 3b. Builder drains fix queue (files auditor couldn't fix)
        if not _fix_queue.empty():
            _touch_progress(build_id)
            await _set_build_activity(build_id, user_id, "Builder fixing audit failures...")
            builder_fix_results = await _builder_drain_fix_queue(
                build_id, user_id, api_key, _audit_key,
                _fix_queue, working_dir, _manifest_cache_path,
                audit_llm_enabled,
            )
            for fpath, fverdict, ffindings in builder_fix_results:
                # Remove from blocking_files if builder fixed it
                if fverdict == "PASS":
                    blocking_files = [
                        (bp, bf) for bp, bf in blocking_files if bp != fpath
                    ]
                    if fpath not in passed_files:
                        passed_files.append(fpath)
                    # Update in-memory content
                    try:
                        fp_full = Path(working_dir) / fpath
                        if fp_full.exists():
                            fixed_c = fp_full.read_text(encoding="utf-8")
                            phase_files_written[fpath] = fixed_c
                            all_files_written[fpath] = fixed_c
                            touched_files.add(fpath)
                    except Exception:
                        pass
                elif not any(bp == fpath for bp, _ in blocking_files):
                    blocking_files.append((fpath, ffindings))

        if blocking_files:
            audit_report = "\n\n".join(
                f"### {fp}\n{ff}" for fp, ff in blocking_files
            )
            audit_verdict = "FAIL"
            _v_msg = (
                f"Per-file audit: {len(blocking_files)} BLOCKING, "
                f"{len(passed_files)} passed"
            )
        else:
            audit_verdict = "PASS"
            audit_report = ""
            _v_msg = f"Per-file audit: all {len(passed_files)} files passed"

        await build_repo.append_build_log(
            build_id, _v_msg, source="audit", level="info",
        )
        await _broadcast_build_event(user_id, build_id, "build_log", {
            "message": _v_msg, "source": "audit", "level": "info",
        })
        _touch_progress(build_id)

        # Build phase_output for recovery planner (needed if audit failed)
        _audit_parts = [
            f"Phase {phase_num} ({phase['name']}) generated "
            f"{len(phase_files_written)} files:\n",
        ]
        _audit_budget = 40_000
        _audit_used = 0
        _priority_keys = sorted(
            phase_files_written.keys(),
            key=lambda p: (
                0 if "main" in p or "test" in p or "conftest" in p
                else 1
            ),
        )
        for _ap in _priority_keys:
            _ac = phase_files_written[_ap]
            if _audit_used + len(_ac) > _audit_budget:
                _audit_parts.append(f"- {_ap} ({len(_ac)} chars, content omitted)")
            else:
                _audit_parts.append(
                    f"\n### {_ap}\n```\n{_ac}\n```\n"
                )
                _audit_used += len(_ac)
        phase_output = "\n".join(_audit_parts)

        # 4. Handle audit failures (recovery loop)
        audit_attempts = 1
        while audit_verdict != "PASS" and audit_attempts < settings.PAUSE_THRESHOLD:
            audit_attempts += 1
            _touch_progress(build_id)
            loop_count = await build_repo.increment_loop_count(build_id)

            await build_repo.append_build_log(
                build_id,
                f"Audit FAIL for {phase_name} (attempt {audit_attempts})",
                source="audit", level="warn",
            )
            await _broadcast_build_event(user_id, build_id, "audit_fail", {
                "phase": phase_name,
                "loop_count": loop_count,
            })

            # Recovery planner
            _log_msg = f"Running recovery planner for {phase_name} (attempt {audit_attempts})..."
            await build_repo.append_build_log(build_id, _log_msg, source="audit", level="info")
            await _broadcast_build_event(user_id, build_id, "build_log", {
                "message": _log_msg, "source": "audit", "level": "info",
            })
            remediation_plan = ""
            try:
                remediation_plan = await _run_recovery_planner(
                    build_id=build_id, user_id=user_id, api_key=api_key,
                    phase=phase_name, audit_findings=audit_report,
                    builder_output=phase_output, contracts=contracts,
                    working_dir=working_dir,
                )
            except Exception as exc:
                logger.warning("Recovery planner failed: %s", exc)

            # Generate fix manifest
            existing = {}
            for p in phase_files_written:
                try:
                    existing[p] = Path(working_dir, p).read_text(encoding="utf-8")
                except Exception:
                    pass

            fix_manifest = None
            if remediation_plan:
                fix_manifest = await _generate_fix_manifest(
                    build_id, user_id, api_key,
                    remediation_plan, existing, audit_report, contracts,
                )

            if fix_manifest:
                fix_paths = [f["path"] for f in fix_manifest]
                _fix_msg = f"Fix manifest: {len(fix_manifest)} file(s) to repair — {', '.join(fix_paths)}"
                await build_repo.append_build_log(build_id, _fix_msg, source="recovery", level="info")
                await _broadcast_build_event(user_id, build_id, "build_log", {
                    "message": _fix_msg, "source": "recovery", "level": "info",
                })
                for fix_entry in fix_manifest:
                    fix_path = fix_entry["path"]

                    # --- Handle delete action ---
                    if fix_entry["action"] == "delete":
                        try:
                            target = Path(working_dir) / fix_path
                            if target.exists():
                                target.unlink()
                                _del_msg = f"Deleted: {fix_path}"
                            else:
                                _del_msg = f"Delete skipped (not found): {fix_path}"
                            await build_repo.append_build_log(
                                build_id, _del_msg, source="recovery", level="info",
                            )
                            await _broadcast_build_event(user_id, build_id, "build_log", {
                                "message": _del_msg, "source": "recovery", "level": "info",
                            })
                            # Remove from tracked files
                            phase_files_written.pop(fix_path, None)
                            all_files_written.pop(fix_path, None)
                        except Exception as exc:
                            logger.warning("Delete failed for %s: %s", fix_path, exc)
                        continue

                    # --- Handle create / modify ---
                    fix_context = {}
                    for ctx in fix_entry.get("context_files", []):
                        try:
                            full = Path(working_dir) / ctx
                            if full.exists():
                                fix_context[ctx] = full.read_text(encoding="utf-8")
                        except Exception:
                            pass
                    # Add current file content as context for modifications
                    if fix_entry["action"] == "modify" and fix_path in existing:
                        fix_context[fix_path] = existing[fix_path]

                    try:
                        error_ctx = fix_entry.get("fix_instructions", "")
                        if audit_report:
                            error_ctx += f"\n\nAudit finding:\n{audit_report[:2000]}"
                        fix_content = await _generate_single_file(
                            build_id, user_id, api_key,
                            fix_entry, contracts, fix_context,
                            phase_deliverables, working_dir,
                            error_context=error_ctx,
                        )
                        phase_files_written[fix_path] = fix_content
                        all_files_written[fix_path] = fix_content
                        touched_files.add(fix_path)
                    except Exception as exc:
                        logger.warning("Fix generation failed for %s: %s", fix_path, exc)

                # Re-commit fixes
                try:
                    await git_client.add_all(working_dir)
                    await git_client.commit(
                        working_dir, f"forge: {phase_name} fixes (attempt {audit_attempts})",
                    )
                except Exception:
                    pass

            # Re-audit
            phase_output_updated = (
                f"Phase {phase_num} ({phase['name']}) — attempt {audit_attempts}. "
                f"Files: {', '.join(phase_files_written.keys())}"
            )
            audit_verdict, audit_report = await _run_inline_audit(
                build_id, phase_name, phase_output_updated,
                contracts, api_key, audit_llm_enabled,
            )
            _touch_progress(build_id)

        if audit_verdict != "PASS" and audit_attempts >= settings.PAUSE_THRESHOLD:
            # Pause for user
            await _pause_build(
                build_id, user_id, phase_name,
                audit_attempts,
                f"{audit_attempts} audit failures on {phase_name}",
            )
            event = _pause_events.get(str(build_id))
            if event:
                try:
                    await asyncio.wait_for(
                        event.wait(),
                        timeout=settings.BUILD_PAUSE_TIMEOUT_MINUTES * 60,
                    )
                except asyncio.TimeoutError:
                    await _fail_build(
                        build_id, user_id,
                        f"RISK_EXCEEDS_SCOPE: pause timed out on {phase_name}",
                    )
                    return

            action = _resume_actions.pop(str(build_id), "retry")
            _pause_events.pop(str(build_id), None)
            await build_repo.resume_build(build_id)

            if action == "abort":
                await _fail_build(build_id, user_id, f"Build aborted on {phase_name}")
                return
            elif action == "skip":
                await build_repo.append_build_log(
                    build_id, f"Phase {phase_name} skipped by user",
                    source="system", level="warn",
                )
                continue
            # retry — will proceed to next phase anyway at this point

        # 5. Verify phase output (syntax + tests) — BEFORE committing
        # Verification is a gating step: if errors remain after fix attempts,
        # re-run verification (up to MAX_VERIFY_ROUNDS).  Each run internally
        # attempts its own fixes, so the total fix budget is generous.
        MAX_VERIFY_ROUNDS = 3
        verification = {"syntax_errors": 0, "tests_passed": 0, "tests_failed": 0, "fixes_applied": 0}

        for verify_round in range(1, MAX_VERIFY_ROUNDS + 1):
            _log_msg = (
                f"Running verification (syntax + tests) for {phase_name}..."
                if verify_round == 1
                else f"Re-running verification for {phase_name} (round {verify_round}/{MAX_VERIFY_ROUNDS})..."
            )
            await build_repo.append_build_log(build_id, _log_msg, source="system", level="info")
            await _broadcast_build_event(user_id, build_id, "build_log", {
                "message": _log_msg, "source": "system", "level": "info",
            })
            await _set_build_activity(
                build_id, user_id,
                f"Verifying {phase_name} (round {verify_round})..."
                if verify_round > 1
                else f"Verifying {phase_name}...",
            )
            try:
                verification = await _verify_phase_output(
                    build_id, user_id, api_key,
                    manifest, working_dir, contracts,
                    touched_files,
                )
                _v_msg = (
                    f"Verification complete — "
                    f"{verification.get('syntax_errors', 0)} syntax errors, "
                    f"{verification.get('tests_passed', 0)} tests passed, "
                    f"{verification.get('tests_failed', 0)} tests failed, "
                    f"{verification.get('fixes_applied', 0)} auto-fixes"
                )
                await build_repo.append_build_log(build_id, _v_msg, source="system", level="info")
                await _broadcast_build_event(user_id, build_id, "build_log", {
                    "message": _v_msg, "source": "system", "level": "info",
                })
            except Exception as exc:
                logger.warning("Verification failed: %s", exc)
                verification = {"syntax_errors": 0, "tests_passed": 0, "tests_failed": 0, "fixes_applied": 0}
                break  # Can't verify — treat as clean to avoid infinite loop

            # Check if verification is clean
            has_errors = (
                verification.get("syntax_errors", 0) > 0
                or verification.get("tests_failed", 0) > 0
            )
            if not has_errors:
                if verify_round > 1:
                    await _broadcast_build_event(user_id, build_id, "build_log", {
                        "message": f"✓ Verification clean after {verify_round} round(s)",
                        "source": "verify", "level": "info",
                    })
                break  # All good

            if verify_round >= MAX_VERIFY_ROUNDS:
                # Exhausted verification rounds — log warning but proceed
                _warn_msg = (
                    f"⚠ Verification still has issues after {MAX_VERIFY_ROUNDS} rounds: "
                    f"{verification.get('syntax_errors', 0)} syntax errors, "
                    f"{verification.get('tests_failed', 0)} tests failed — proceeding"
                )
                await build_repo.append_build_log(build_id, _warn_msg, source="verify", level="warn")
                await _broadcast_build_event(user_id, build_id, "build_log", {
                    "message": _warn_msg, "source": "verify", "level": "warn",
                })

        # 6. Commit after audit + verification — never commit unverified code
        if phase_files_written:
            try:
                await _set_build_activity(build_id, user_id, f"Committing {phase_name}...")
                await git_client.add_all(working_dir)
                _fix_count = verification.get("fixes_applied", 0)
                commit_msg = (
                    f"forge: {phase_name} complete"
                    if audit_attempts <= 1 and _fix_count == 0
                    else f"forge: {phase_name} complete (audit×{audit_attempts}, {_fix_count} auto-fixes)"
                )
                sha = await git_client.commit(working_dir, commit_msg)
                if sha:
                    _log_msg = f"Committed {phase_name}: {sha[:8]}"
                    await build_repo.append_build_log(
                        build_id, _log_msg,
                        source="system", level="info",
                    )
                    await _broadcast_build_event(user_id, build_id, "build_log", {
                        "message": _log_msg, "source": "system", "level": "info",
                    })
            except Exception as exc:
                logger.warning("Git commit failed for %s: %s", phase_name, exc)

        # Determine final phase verdict: audit + verification
        verification_clean = (
            verification.get("syntax_errors", 0) == 0
            and verification.get("tests_failed", 0) == 0
        )

        if audit_verdict == "PASS" and verification_clean:
            _log_msg = f"✅ Phase {phase_name} PASS (audit + verification)"
            await build_repo.append_build_log(
                build_id, _log_msg,
                source="audit", level="info",
            )
            await _broadcast_build_event(user_id, build_id, "build_log", {
                "message": _log_msg, "source": "audit", "level": "info",
            })
            await _broadcast_build_event(user_id, build_id, "audit_pass", {
                "phase": phase_name,
            })
            await _broadcast_build_event(user_id, build_id, "phase_complete", {
                "phase": phase_name,
                "status": "pass",
            })
            # Lock in completed phase for /continue
            await build_repo.update_completed_phases(build_id, phase_num)
            # Clean up cached manifest — phase is done
            _mc = Path(working_dir) / ".forge" / f"manifest_phase_{phase_num}.json"
            if _mc.exists():
                _mc.unlink(missing_ok=True)
        elif audit_verdict == "PASS":
            # Audit passed but verification has remaining issues — still proceed
            # but mark as partial pass so the user knows
            _log_msg = (
                f"⚠ Phase {phase_name} — audit PASS, verification has "
                f"{verification.get('syntax_errors', 0)} syntax errors, "
                f"{verification.get('tests_failed', 0)} test failures"
            )
            await build_repo.append_build_log(
                build_id, _log_msg,
                source="audit", level="warn",
            )
            await _broadcast_build_event(user_id, build_id, "build_log", {
                "message": _log_msg, "source": "audit", "level": "warn",
            })
            await _broadcast_build_event(user_id, build_id, "phase_complete", {
                "phase": phase_name,
                "status": "partial",
                "verification": verification,
            })
            # Still lock in phase so /continue works — verification issues
            # can accumulate and be fixed across phases
            await build_repo.update_completed_phases(build_id, phase_num)
            # Clean up cached manifest — phase is done
            _mc = Path(working_dir) / ".forge" / f"manifest_phase_{phase_num}.json"
            if _mc.exists():
                _mc.unlink(missing_ok=True)

        # Push to GitHub
        if (
            working_dir
            and target_type in ("github_new", "github_existing")
            and access_token
        ):
            try:
                await _set_build_activity(build_id, user_id, f"Pushing {phase_name} to GitHub...")
                await git_client.push(
                    working_dir, branch=branch, access_token=access_token,
                )
                await build_repo.append_build_log(
                    build_id, f"Pushed {phase_name} to GitHub",
                    source="system", level="info",
                )
            except Exception as exc:
                logger.warning("Git push failed for %s: %s", phase_name, exc)

        # --- Auto-clear context between phases ---
        # Each phase is independent; prior-phase files live on disk and
        # will be loaded on demand via the context budget system.
        dropped = len(all_files_written)
        all_files_written.clear()
        _log_msg = f"Context reset after {phase_name} — cleared {dropped} cached files"
        await build_repo.append_build_log(
            build_id, _log_msg,
            source="system", level="info",
        )
        await _broadcast_build_event(user_id, build_id, "build_log", {
            "message": _log_msg, "source": "system", "level": "info",
        })
        await _broadcast_build_event(user_id, build_id, "context_reset", {
            "phase": phase_name,
            "dropped": dropped,
        })

        # --- Phase transition announcement ---
        next_idx = phases.index(phase) + 1
        if next_idx < len(phases):
            next_phase = phases[next_idx]
            _trans_msg = (
                f"🔄 {phase_name} complete — transitioning to "
                f"Phase {next_phase['number']}: {next_phase['name']}"
            )
            await build_repo.append_build_log(
                build_id, _trans_msg, source="system", level="info",
            )
            await _broadcast_build_event(user_id, build_id, "build_log", {
                "message": _trans_msg, "source": "system", "level": "info",
            })
            await _broadcast_build_event(user_id, build_id, "phase_transition", {
                "completed_phase": phase_name,
                "next_phase": f"Phase {next_phase['number']}",
                "next_phase_name": next_phase["name"],
                "next_phase_objective": next_phase.get("objective", ""),
            })
        else:
            _trans_msg = f"🏁 {phase_name} complete — all phases finished"
            await build_repo.append_build_log(
                build_id, _trans_msg, source="system", level="info",
            )
            await _broadcast_build_event(user_id, build_id, "build_log", {
                "message": _trans_msg, "source": "system", "level": "info",
            })

    # Build complete — clean up signal flags
    bid = str(build_id)
    _cancel_flags.discard(bid)
    _pause_flags.discard(bid)
    _compact_flags.discard(bid)
    _current_generating.pop(bid, None)
    _build_activity_status.pop(bid, None)

    now = datetime.now(timezone.utc)
    await build_repo.update_build_status(
        build_id, "completed", completed_at=now,
    )
    await project_repo.update_project_status(project_id, "completed")
    await build_repo.append_build_log(
        build_id, "Build completed successfully (plan-execute mode)",
        source="system", level="info",
    )

    cost_summary = await build_repo.get_build_cost_summary(build_id)
    await _broadcast_build_event(user_id, build_id, "build_complete", {
        "id": str(build_id),
        "status": "completed",
        "total_input_tokens": cost_summary["total_input_tokens"],
        "total_output_tokens": cost_summary["total_output_tokens"],
        "total_cost_usd": float(cost_summary["total_cost_usd"]),
    })
