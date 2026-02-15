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
from decimal import Decimal
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

from app.clients.agent_client import StreamUsage, ToolCall, stream_agent
from app.clients import git_client
from app.clients import github_client
from app.config import settings
from app.repos import build_repo
from app.repos import project_repo
from app.repos.user_repo import get_user_by_id
from app.services.tool_executor import BUILDER_TOOLS, execute_tool
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


def _compact_conversation(messages: list[dict]) -> list[dict]:
    """Compact a conversation by summarizing older turns.

    Keeps the first message (directive) and last 2 assistant/user pairs
    intact.  Middle turns are replaced with a summary message.

    Returns a new compacted message list.
    """
    if len(messages) <= 5:
        # Not enough to compact
        return list(messages)

    # First message is always the directive — keep it
    directive = messages[0]

    # Keep the last 4 messages (2 turns: user+assistant pairs)
    tail = messages[-4:]

    # Summarize the middle
    middle = messages[1:-4]
    if not middle:
        return list(messages)

    summary_parts = ["[Context compacted — summary of earlier conversation turns]\n"]
    for msg in middle:
        role = msg.get("role", "unknown")
        content = msg.get("content", "")
        # Truncate each message to 500 chars for summary
        if len(content) > 500:
            content = content[:500] + "..."
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


async def start_build(
    project_id: UUID,
    user_id: UUID,
    *,
    target_type: str | None = None,
    target_ref: str | None = None,
) -> dict:
    """Start a build for a project.

    Validates that contracts exist, creates a build record, and spawns
    the background orchestration task.

    Args:
        project_id: The project to build.
        user_id: The authenticated user (for ownership check).
        target_type: Build target -- 'github_new', 'github_existing', or 'local_path'.
        target_ref: Target reference -- repo name, full_name, or absolute path.

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
    contracts = await project_repo.get_contracts_by_project(project_id)
    if not contracts:
        raise ValueError("No contracts found. Generate contracts before building.")

    # Prevent concurrent builds
    latest = await build_repo.get_latest_build_for_project(project_id)
    if latest and latest["status"] in ("pending", "running"):
        raise ValueError("A build is already in progress for this project")

    # BYOK: user must supply their own Anthropic API key for builds
    user = await get_user_by_id(user_id)
    user_api_key = (user or {}).get("anthropic_api_key") or ""
    if not user_api_key.strip():
        raise ValueError(
            "Anthropic API key required. Add your key in Settings to start a build."
        )

    audit_llm_enabled = (user or {}).get("audit_llm_enabled", True)

    # Validate target
    if target_type and target_type not in VALID_TARGET_TYPES:
        raise ValueError(f"Invalid target_type: {target_type}. Must be one of: {', '.join(VALID_TARGET_TYPES)}")
    if target_type and not target_ref:
        raise ValueError("target_ref is required when target_type is specified")

    # Resolve working directory based on target type
    working_dir: str | None = None
    if target_type == "local_path":
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
    if not latest or latest["status"] != "paused":
        raise ValueError("No paused build to resume")

    if action not in ("retry", "skip", "abort", "edit"):
        raise ValueError(f"Invalid action: {action}. Must be retry, skip, abort, or edit.")

    build_id = latest["id"]

    # Store the action and signal the pause event
    _resume_actions[str(build_id)] = action
    event = _pause_events.get(str(build_id))
    if event:
        event.set()
    else:
        raise ValueError("Build pause state not found — build may have already resumed")

    await build_repo.append_build_log(
        build_id,
        f"Build resumed with action: {action}",
        source="system", level="info",
    )

    # Give a tick for the background task to process
    await asyncio.sleep(0.05)

    updated = await build_repo.get_build_by_id(build_id)
    return updated


async def interject_build(
    project_id: UUID,
    user_id: UUID,
    message: str,
) -> dict:
    """Inject a user message into an active build.

    The message will be prepended to the next builder turn as a
    [User interjection] block.

    Args:
        project_id: The project whose build to interject.
        user_id: The authenticated user (ownership check).
        message: The interjection text.

    Returns:
        Acknowledgement dict.

    Raises:
        ValueError: If project not found, not owned, or no active build.
    """
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
# Background orchestration
# ---------------------------------------------------------------------------


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
                await _fail_build(build_id, user_id, f"Failed to clone repo: {exc}")
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

        # Track files written during this build
        files_written: list[dict] = []

        phase_start_time = datetime.now(timezone.utc)  # Phase timeout tracking
        # Conversation history for the agent (multi-turn)
        messages: list[dict] = [
            {"role": "user", "content": directive},
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

        # System prompt for the builder agent
        system_prompt = (
            "You are an autonomous software builder operating under the Forge governance framework.\n\n"
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
            "- **list_directory**: List files/folders to understand project structure before making changes.\n"
            "- **search_code**: Search for patterns across files to find implementations or imports.\n"
            "- **write_file**: Write or overwrite a file. Preferred over === FILE: ... === blocks.\n\n"
            "Guidelines for tool use:\n"
            "1. Use list_directory at the start of each phase to understand the current state.\n"
            "2. Use read_file to examine existing code before modifying it.\n"
            "3. Prefer write_file tool over === FILE: path === blocks for creating/updating files.\n"
            "4. Use search_code to find existing patterns, imports, or implementations.\n"
            "5. After writing files, use read_file to verify the content was written correctly.\n"
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
            if total_tokens_all_turns > CONTEXT_COMPACTION_THRESHOLD and len(messages) > 5:
                messages = _compact_conversation(messages)
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

            async for item in stream_agent(
                api_key=api_key,
                model=settings.LLM_BUILDER_MODEL,
                system_prompt=system_prompt,
                messages=messages,
                usage_out=usage,
                tools=BUILDER_TOOLS if working_dir else None,
            ):
                if isinstance(item, ToolCall):
                    # --- Tool call detected ---
                    tool_result = execute_tool(item.name, item.input, working_dir or "")

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

                # Log chunks in batches (every ~500 chars)
                if len(chunk) >= 10:
                    await build_repo.append_build_log(
                        build_id, chunk, source="builder", level="info"
                    )
                    await _broadcast_build_event(
                        user_id, build_id, "build_log", {
                            "message": chunk,
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

            # Turn complete — handle tool calls if any
            if tool_calls_this_turn:
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

                # Continue to next iteration — the agent will respond to tool results
                total_tokens_all_turns += usage.input_tokens + usage.output_tokens
                continue

            # Add assistant response to conversation history
            messages.append({"role": "assistant", "content": turn_text})

            # Check for user interjections between turns
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

            # Update total token count
            total_tokens_all_turns += usage.input_tokens + usage.output_tokens

            if not compacted:
                await _broadcast_build_event(user_id, build_id, "build_turn", {
                    "turn": turn_count,
                    "total_tokens": total_tokens_all_turns,
                    "compacted": False,
                })

            # Detect phase completion
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
                phase_input_tokens = usage.input_tokens
                phase_output_tokens = usage.output_tokens

                await _broadcast_build_event(
                    user_id, build_id, "phase_complete", {
                        "phase": current_phase,
                        "status": "pass",
                        "input_tokens": phase_input_tokens,
                        "output_tokens": phase_output_tokens,
                    }
                )

                # Run inline audit
                audit_verdict, audit_report = await _run_inline_audit(
                    build_id, current_phase, accumulated_text,
                    contracts, api_key, audit_llm_enabled,
                )

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

                    # Reset for next phase
                    phase_start_time = datetime.now(timezone.utc)
                    phase_loop_count = 0
                    accumulated_text = ""
                    plan_tasks = []  # Fresh plan for next phase

                    # Record cost for this phase
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

                    # Record cost for this failed attempt
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

            # If no phase was completed and the agent stopped, the build is done
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
                            working_dir, access_token=access_token,
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
                        working_dir, access_token=access_token,
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

    Prepends the universal builder_contract.md (governance framework)
    then concatenates all per-project contracts in canonical order.
    """
    parts = ["# Forge Governance & Project Contracts\n"]

    # 1. Load universal builder_contract.md from disk
    builder_contract_path = FORGE_CONTRACTS_DIR / "builder_contract.md"
    if builder_contract_path.exists():
        parts.append("\n---\n## builder_contract (universal governance)\n")
        parts.append(builder_contract_path.read_text(encoding="utf-8"))
        parts.append("\n")
    else:
        logger.warning("builder_contract.md not found at %s", builder_contract_path)

    # 2. Per-project contracts in canonical order
    parts.append("\n---\n# Per-Project Contracts\n")
    type_order = [
        "blueprint", "manifesto", "stack", "schema", "physics",
        "boundaries", "phases", "ui", "builder_directive",
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
        parts.append(f"\n---\n## {contract['contract_type']}\n")
        parts.append(contract["content"])
        parts.append("\n")
    return "\n".join(parts)


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
            f"Respond with your audit report. Your verdict MUST be either:\n"
            f"- `CLEAN` — if everything passes\n"
            f"- `FLAGS FOUND` — if there are issues\n\n"
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


async def _fail_build(build_id: UUID, user_id: UUID, detail: str) -> None:
    """Mark a build as failed and broadcast the event."""
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
    input_t = usage.input_tokens
    output_t = usage.output_tokens
    model = usage.model or settings.LLM_BUILDER_MODEL
    input_rate, output_rate = _get_token_rates(model)
    cost = (Decimal(input_t) * input_rate
            + Decimal(output_t) * output_rate)
    await build_repo.record_build_cost(
        build_id, phase, input_t, output_t, model, cost
    )
    # Reset for next phase
    usage.input_tokens = 0
    usage.output_tokens = 0


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
