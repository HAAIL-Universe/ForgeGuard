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
import sys
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

# ---------------------------------------------------------------------------
# MCP-driven system prompt (Phase 56) — used when USE_MCP_CONTRACTS=True
# ---------------------------------------------------------------------------

MCP_SYSTEM_PROMPT = """\
You are an autonomous software builder operating under the Forge governance framework.
You build projects phase-by-phase using the tools below.

## Your Tools

### Project Tools
- **read_file**(path): Read a file from the project.
- **list_directory**(path): List files/folders in a directory.
- **search_code**(pattern, glob?): Search for patterns across files.
- **write_file**(path, content): Write or overwrite a file.
- **edit_file**(path, edits): Apply surgical edits to an existing file.
- **run_tests**(command, timeout?): Run the test suite.
- **check_syntax**(file_path): Check a file for syntax errors.
- **run_command**(command, timeout?): Run safe shell commands.

### Forge Contract Tools
- **forge_get_summary**(): Overview of governance framework. Call FIRST to orient yourself.
- **forge_list_contracts**(): List all available contracts.
- **forge_get_contract**(name): Read a specific governance contract (blueprint, stack, schema, physics, boundaries, manifesto, ui, builder_directive, builder_contract).
- **forge_get_phase_window**(phase_number): Get current + next phase deliverables.
- **forge_scratchpad**(operation, key?, value?): Persistent notes across phases (survives context compaction).

## AEM (Autonomous Execution Mode) — The Build Loop

For EACH phase, follow these steps exactly:

### Step 1: Fetch Phase Context
Call `forge_get_phase_window(N)` where N is the current phase number.
Read any contracts relevant to this phase's deliverables:
- Always read: `blueprint` (what to build), `stack` (tech requirements)
- If writing APIs: `physics` (endpoint spec), `boundaries` (layer rules)
- If writing UI: `ui` (design contract)
- If first phase: `manifesto` (project ethos), `schema` (data model)

### Step 2: Plan
Emit a structured plan for THIS phase only:
=== PLAN ===
1. First task for this phase
2. Second task for this phase
...
=== END PLAN ===
Do NOT plan future phases. Use `forge_scratchpad("write", ...)` to record
any decisions you'll need later.

### Step 3: Build
Write code using `write_file` and `edit_file`. After each file:
- Call `check_syntax` to catch errors immediately.
- Do NOT explore the filesystem unnecessarily — the workspace listing
  is in the first message, and you can `list_directory` if needed.

### Step 4: Mark Progress
After completing each plan task, emit: === TASK DONE: N ===

### Step 5: Verify
Run `run_tests` with the project's test command. If tests fail:
- Read the error output carefully
- Fix with `edit_file` or `write_file`
- Re-run tests
- Repeat until tests pass (max 3 attempts per issue)

### Step 6: Phase Sign-Off
When ALL tasks are done and tests pass, emit:
=== PHASE SIGN-OFF: PASS ===
Phase: {phase_name}
Deliverables: {comma-separated list}
Tests: PASS
=== END PHASE SIGN-OFF ===

### Step 7: Next Phase
After sign-off, a new message will arrive with the next phase context.
Start again from Step 1.

## Critical Rules

1. **Minimal Diff**: Only change what the phase requires. No renames,
   no cleanup, no unrelated refactors.
2. **Boundary Enforcement**: Routers → Services → Repos. No skipping layers.
   Read `boundaries` contract if unsure.
3. **Contract Exclusion**: NEVER include Forge contract content, references,
   or metadata in committed source files, READMEs, or code comments.
   The `Forge/` directory is server-side only.
4. **Evidence**: Every change must be traceable to a phase deliverable.
5. **STOP Codes**: If you encounter an unresolvable issue, emit one of:
   EVIDENCE_MISSING, AMBIGUOUS_INTENT, CONTRACT_CONFLICT,
   RISK_EXCEEDS_SCOPE, NON_DETERMINISTIC_BEHAVIOR, ENVIRONMENT_LIMITATION
6. **README**: Before the final phase, write a comprehensive README.md that includes
   project name, description, key features, tech stack, setup instructions,
   environment variables, usage examples, API reference, and license placeholder.
7. **Environment Files**: Write a `.env.example` with all required environment
   variables (placeholder values, commented descriptions). Also write a `.env`
   with working local dev defaults. `.env` is gitignored; `.env.example` is
   committed so users know what to configure.
8. **Dependencies**: When you write `requirements.txt` or `package.json`,
   dependencies are auto-installed into the project's virtual environment.
   You do NOT need to call `run_command("pip install ...")` after creating
   requirements.txt — it happens automatically.

## First Turn

On your VERY FIRST response:
1. Call `forge_get_phase_window(0)` to get Phase 0 deliverables
2. Call `forge_get_contract("blueprint")` and `forge_get_contract("stack")`
3. Emit === PLAN === for Phase 0
4. Start writing code immediately with `write_file`
"""

# ---------------------------------------------------------------------------
# MCP Mini-build system prompt (Phase 56B) — 2-phase rapid scaffold
# ---------------------------------------------------------------------------

MCP_MINI_SYSTEM_PROMPT = """\
You are an autonomous software builder operating under the Forge governance framework.
You are running a **Mini Build** — a rapid 2-phase proof-of-concept scaffold.

## Your Tools

### Project Tools
- **read_file**(path): Read a file from the project.
- **list_directory**(path): List files/folders in a directory.
- **search_code**(pattern, glob?): Search for patterns across files.
- **write_file**(path, content): Write or overwrite a file.
- **edit_file**(path, edits): Apply surgical edits to an existing file.
- **run_tests**(command, timeout?): Run the test suite.
- **check_syntax**(file_path): Check a file for syntax errors.
- **run_command**(command, timeout?): Run safe shell commands.

### Forge Contract Tools
- **forge_get_summary**(): Overview of governance framework. Call FIRST to orient yourself.
- **forge_list_contracts**(): List all available contracts.
- **forge_get_contract**(name): Read a specific governance contract.
- **forge_get_phase_window**(phase_number): Get current + next phase deliverables.
- **forge_scratchpad**(operation, key?, value?): Persistent notes across phases.

## Mini Build — 2 Phases Only

This is a **Mini Build**. There are EXACTLY 2 phases:
- **Phase 0 — Backend Scaffold**: Project structure, database, API endpoints, auth, tests
- **Phase 1 — Frontend & Ship**: UI pages/components, API integration, styling, README

Do NOT create additional phases. Do NOT split work into more than 2 phases.
Every deliverable MUST fit into Phase 0 or Phase 1.

## AEM (Autonomous Execution Mode) — The Build Loop

For EACH phase, follow these steps exactly:

### Step 1: Fetch Phase Context
Call `forge_get_phase_window(N)` where N is the current phase number (0 or 1).
Read contracts relevant to this phase:
- Phase 0: `blueprint`, `stack`, `schema`, `physics`, `boundaries`, `manifesto`
- Phase 1: `blueprint`, `ui`, `stack` (re-read if needed after compaction)

### Step 2: Plan
Emit a structured plan for THIS phase only:
=== PLAN ===
1. First task for this phase
2. Second task for this phase
...
=== END PLAN ===

### Step 3: Build
Write code using `write_file` and `edit_file`. After each file:
- Call `check_syntax` to catch errors immediately.
- Do NOT explore the filesystem — the workspace listing is in the first message.

### Step 4: Mark Progress
After completing each plan task, emit: === TASK DONE: N ===

### Step 5: Verify
Run `run_tests` with the project's test command. If tests fail:
- Read the error output carefully
- Fix with `edit_file` or `write_file`
- Re-run tests (max 3 attempts per issue)

### Step 6: Phase Sign-Off
When ALL tasks are done and tests pass, emit:
=== PHASE SIGN-OFF: PASS ===
Phase: {phase_name}
Deliverables: {comma-separated list}
Tests: PASS
=== END PHASE SIGN-OFF ===

### Step 7: Next Phase (Phase 0 only)
After Phase 0 sign-off, a new message will arrive with Phase 1 context.
Start again from Step 1.
After Phase 1 sign-off, the build is COMPLETE.

## Critical Rules

1. **2 Phases Only**: Phase 0 (backend) and Phase 1 (frontend). Nothing else.
2. **Minimal Diff**: Only change what the phase requires.
3. **Boundary Enforcement**: Routers → Services → Repos. No skipping layers.
4. **Contract Exclusion**: NEVER include Forge contract content in source files.
   The `Forge/` directory is server-side only.
5. **STOP Codes**: If you encounter an unresolvable issue, emit one of:
   EVIDENCE_MISSING, AMBIGUOUS_INTENT, CONTRACT_CONFLICT,
   RISK_EXCEEDS_SCOPE, NON_DETERMINISTIC_BEHAVIOR, ENVIRONMENT_LIMITATION
6. **README**: In Phase 1, write a comprehensive README.md with project name,
   description, features, tech stack, setup instructions, environment variables,
   usage examples, API reference, and license placeholder.
7. **Environment Files**: Write `.env.example` (committed — placeholder values)
   and `.env` (gitignored — working local defaults) with all required env vars.
8. **Dependencies**: Writing `requirements.txt` or `package.json` auto-triggers
   installation. No need to manually `run_command("pip install ...")`.

## First Turn

On your VERY FIRST response:
1. Call `forge_get_phase_window(0)` to get Phase 0 deliverables
2. Call `forge_get_contract("blueprint")` and `forge_get_contract("stack")`
3. Call `forge_get_contract("schema")` — you need the data model for backend
4. Emit === PLAN === for Phase 0
5. Start writing code immediately with `write_file`
"""

# ---------------------------------------------------------------------------
# Sub-module imports (R7 decomposition)
# ---------------------------------------------------------------------------
from app.services.build._state import (  # noqa: E402
    MAX_LOOP_COUNT,
    PHASE_COMPLETE_SIGNAL,
    BUILD_ERROR_SIGNAL,
    PLAN_START_PATTERN,
    PLAN_END_PATTERN,
    CONTEXT_COMPACTION_THRESHOLD,
    FORGE_CONTRACTS_DIR,
    FILE_START_PATTERN,
    FILE_END_PATTERN,
    VALID_TARGET_TYPES,
    _EXT_TO_LANG,
    _active_tasks,
    _pause_events,
    _resume_actions,
    _interjection_queues,
    _cancel_flags,
    _pause_flags,
    _compact_flags,
    _current_generating,
    _build_activity_status,
    _build_heartbeat_tasks,
    _last_progress,
    _HEARTBEAT_INTERVAL,
    _STALL_WARN_THRESHOLD,
    _STALL_FAIL_THRESHOLD,
    _build_cost_user,
    _detect_language,
    _touch_progress,
    _set_build_activity,
    _build_watchdog,
    _broadcast_build_event,
    _fail_build,
    _pause_build,
)
from app.services.build.cost import (  # noqa: E402
    CostCapExceeded,
    _MODEL_PRICING,
    _DEFAULT_INPUT_RATE,
    _DEFAULT_OUTPUT_RATE,
    _get_token_rates,
    _accumulate_cost,
    _broadcast_cost_ticker,
    _check_cost_gate,
    _init_cost_tracking,
    _cleanup_cost_tracking,
    get_build_cost_live,
    _record_phase_cost,
    _build_running_cost,
    _build_api_calls,
    _build_total_input_tokens,
    _build_total_output_tokens,
    _build_spend_caps,
    _build_cost_warned,
    _last_cost_ticker,
)
from app.services.build.context import (  # noqa: E402
    _parse_file_blocks,
    _strip_code_fence,
    _compact_conversation,
    _build_directive,
    _write_contracts_to_workdir,
    _extract_phase_window,
    inject_forge_gitignore,
)
from app.services.build.planner import (  # noqa: E402
    _parse_build_plan,
    _gather_project_state,
    _run_recovery_planner,
    _parse_phases_contract,
    _generate_deploy_instructions,
    _select_contracts_for_file,
    _calculate_context_budget,
    _topological_sort,
    _generate_file_manifest,
    _generate_single_file,
    _generate_fix_manifest,
    _CONTRACT_RELEVANCE,
)
from app.services.build.verification import (  # noqa: E402
    _FILE_AUDIT_SEMAPHORE,
    _AUDITOR_FIX_ROUNDS,
    _run_inline_audit,
    _fix_single_file,
    _builder_drain_fix_queue,
    _update_manifest_cache,
    _audit_and_cache,
    _audit_single_file,
    _audit_single_file_llm,
    _verify_phase_output,
    _run_governance_checks,
)


# ---------------------------------------------------------------------------
# Project environment setup
# ---------------------------------------------------------------------------


async def _setup_project_environment(
    working_dir: str,
    build_id: UUID | None = None,
    user_id: UUID | None = None,
    contracts: list[dict] | None = None,
) -> str | None:
    """Create a project-local virtual environment and install base dependencies.

    This runs at build start, **before the LLM agent begins**, and streams
    every line of output to the build log so the user sees it in the IDE
    console — just like a normal terminal.

    Steps:
        1. ``python -m venv .venv`` (creates the venv)
        2. ``pip install --upgrade pip`` (ensure latest pip)
        3. If the stack contract mentions Node: ``npm init -y`` (if no package.json)

    For dependency installation triggered by write_file (requirements.txt,
    package.json etc.), see ``_auto_install_deps`` in tool_executor.py.

    Returns the absolute path to the venv directory, or ``None`` on failure.
    """
    import subprocess as _sp

    venv_dir = Path(working_dir) / ".venv"

    async def _log(msg: str, level: str = "info") -> None:
        """Persist + broadcast a build log line."""
        if build_id:
            await build_repo.append_build_log(
                build_id, msg, source="setup", level=level,
            )
        if build_id and user_id:
            await _broadcast_build_event(user_id, build_id, "build_log", {
                "message": msg, "source": "setup", "level": level,
            })

    async def _stream_command(
        cmd: list[str], label: str, timeout: int = 120,
    ) -> tuple[int, str]:
        """Run a command and stream stdout/stderr lines to the build log.

        Returns (exit_code, combined_output).
        """
        await _log(f"$ {' '.join(cmd)}")

        def _sync() -> tuple[int, list[str]]:
            lines: list[str] = []
            try:
                proc = _sp.Popen(
                    cmd, stdout=_sp.PIPE, stderr=_sp.STDOUT,
                    text=True, cwd=working_dir,
                    env=_venv_env(working_dir),
                )
                assert proc.stdout is not None
                for line in proc.stdout:
                    lines.append(line.rstrip())
                proc.wait(timeout=timeout)
                return proc.returncode, lines
            except _sp.TimeoutExpired:
                proc.kill()  # type: ignore[union-attr]
                lines.append(f"[timeout after {timeout}s]")
                return -1, lines
            except Exception as exc:
                lines.append(f"[error: {exc}]")
                return -1, lines

        loop = asyncio.get_running_loop()
        exit_code, output_lines = await loop.run_in_executor(None, _sync)

        # Stream each line to the build log
        for line in output_lines:
            if line.strip():
                await _log(f"  {line}")

        if exit_code == 0:
            await _log(f"\u2714 {label} succeeded")
        else:
            await _log(f"\u2718 {label} failed (exit code {exit_code})", level="warn")

        return exit_code, "\n".join(output_lines)

    # ----- Step 1: Create venv -----
    if venv_dir.exists():
        await _log("Virtual environment already exists (.venv) \u2014 reusing")
        return str(venv_dir)

    await _log("\U0001f4e6 Creating virtual environment (.venv)...")
    exit_code, _ = await _stream_command(
        [sys.executable, "-m", "venv", str(venv_dir)],
        label="venv creation",
    )
    if exit_code != 0 or not venv_dir.exists():
        await _log("Failed to create virtual environment", level="error")
        return None

    # ----- Step 2: Upgrade pip -----
    pip_exe = str(
        venv_dir / ("Scripts" if os.name == "nt" else "bin") / "pip"
    )
    await _log("\u2b06 Upgrading pip...")
    await _stream_command(
        [pip_exe, "install", "--upgrade", "pip"],
        label="pip upgrade",
        timeout=60,
    )

    # ----- Step 3: Detect stack and set up Node if needed -----
    stack_needs_node = False
    if contracts:
        for c in contracts:
            if c.get("contract_type") == "stack":
                content_lower = c.get("content", "").lower()
                if any(kw in content_lower for kw in (
                    "node", "react", "next", "vue", "angular", "typescript", "npm",
                )):
                    stack_needs_node = True
                break

    if stack_needs_node:
        pkg_json = Path(working_dir) / "package.json"
        if not pkg_json.exists():
            await _log("\U0001f4e6 Initialising Node.js project (npm init)...")
            await _stream_command(
                ["npm", "init", "-y"],
                label="npm init",
                timeout=30,
            )

    await _log("\u2705 Project environment ready")
    return str(venv_dir)


def _venv_env(working_dir: str) -> dict[str, str]:
    """Build a subprocess environment dict that activates the project .venv.

    Also loads variables from the project's ``.env`` file if it exists,
    so that run_tests / run_command have access to the project's environment.
    """
    env: dict[str, str] = {"PATH": os.environ.get("PATH", "")}
    # Propagate minimal OS vars
    for key in ("SYSTEMROOT", "TEMP", "TMP", "HOME", "USERPROFILE",
                "APPDATA", "LOCALAPPDATA", "COMSPEC"):
        val = os.environ.get(key)
        if val:
            env[key] = val

    venv_dir = Path(working_dir) / ".venv"
    if venv_dir.is_dir():
        env["VIRTUAL_ENV"] = str(venv_dir)
        bin_dir = venv_dir / ("Scripts" if os.name == "nt" else "bin")
        env["PATH"] = f"{bin_dir}{os.pathsep}{env.get('PATH', '')}"

    # Load project .env if it exists
    dotenv_path = Path(working_dir) / ".env"
    if dotenv_path.is_file():
        try:
            for line in dotenv_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    k, _, v = line.partition("=")
                    k = k.strip()
                    v = v.strip().strip('"').strip("'")
                    if k:
                        env[k] = v
        except Exception:
            pass  # non-fatal

    return env


# Backward-compat alias
async def _create_project_venv(
    working_dir: str,
    build_id: UUID | None = None,
) -> str | None:
    """Thin wrapper for backward compatibility."""
    return await _setup_project_environment(working_dir, build_id)

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
    _cleanup_cost_tracking(build_id)

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

    # Initialise cost tracking — read user's spend cap
    user = await get_user_by_id(user_id)
    raw_cap = (user or {}).get("build_spend_cap")
    spend_cap = float(raw_cap) if raw_cap is not None else None
    _init_cost_tracking(build_id, user_id, spend_cap)

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
        _cleanup_cost_tracking(build_id)


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

        # Build the directive from contracts (only needed in legacy mode)
        if settings.USE_MCP_CONTRACTS:
            directive = ""  # MCP mode — builder fetches contracts via tools
        else:
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

        # Create project-local virtual environment & stream setup to IDE log
        if working_dir:
            try:
                await _setup_project_environment(
                    working_dir, build_id, user_id, contracts,
                )
            except Exception as exc:
                logger.warning("Failed to set up project environment (non-fatal): %s", exc)

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

        # Ensure .gitignore excludes Forge/ so contracts never leak to git
        if working_dir:
            try:
                inject_forge_gitignore(working_dir)
            except Exception as exc:
                logger.warning("Failed to inject Forge .gitignore rules: %s", exc)

        # Commit + push contracts so the GitHub repo is populated before
        # the builder starts coding.  This ensures the user can see the
        # Forge/Contracts/ folder in their repo immediately.
        if (
            working_dir
            and target_type in ("github_new", "github_existing")
            and access_token
        ):
            try:
                await git_client.add_all(working_dir, include_contracts=True)
                sha = await git_client.commit(
                    working_dir, "forge: seed Forge/Contracts/",
                    include_contracts=True,
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
        if not settings.USE_MCP_CONTRACTS:
            phase_window = _extract_phase_window(contracts, current_phase_num)
        else:
            phase_window = ""  # MCP mode — builder fetches via forge_get_phase_window

        # Assemble first user message — mode-dependent
        if settings.USE_MCP_CONTRACTS:
            # MCP mode: slim first message (~1.5K tokens)
            # Extract project name from blueprint contract
            _project_name = ""
            _project_desc = ""
            for c in contracts:
                if c["contract_type"] == "blueprint":
                    _lines = c["content"].splitlines()
                    for _line in _lines:
                        _stripped = _line.strip().lstrip("#").strip()
                        if _stripped:
                            _project_name = _stripped
                            break
                    # Second non-empty line as description
                    _found_name = False
                    for _line in _lines:
                        _stripped = _line.strip().lstrip("#").strip()
                        if _stripped and not _found_name:
                            _found_name = True
                            continue
                        if _stripped and _found_name:
                            _project_desc = _stripped
                            break
                    break
            first_message = (
                f"# Project: {_project_name}\n\n"
                + (f"{_project_desc}\n\n" if _project_desc else "")
                + ("**Mini Build** — 2 phases: backend scaffold → frontend & ship.\n\n"
                   if _build_mode == "mini" else "")
                + workspace_info
                + "\n\nBegin Phase 0. Use your forge tools to fetch the contracts "
                "you need, then emit your === PLAN === and start building.\n"
            )
        else:
            # Legacy mode: full contract dump (~27K tokens)
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

        # Resolve project build_mode (mini vs full) for prompt selection
        _project_rec = await project_repo.get_project_by_id(project_id)
        _build_mode = (_project_rec or {}).get("build_mode", "full")

        if settings.USE_MCP_CONTRACTS:
            if _build_mode == "mini":
                system_prompt = MCP_MINI_SYSTEM_PROMPT
            else:
                system_prompt = MCP_SYSTEM_PROMPT
        else:
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
                "9. Dependencies are auto-installed when you write requirements.txt or package.json.\n"
                "   You do NOT need to run 'pip install' manually after writing requirements.txt.\n"
                "10. Write .env.example (committed) and .env (gitignored) for environment config.\n\n"
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
                "\n"
                "## Contract Exclusion\n"
                "NEVER include Forge contract file contents, contract references, or\n"
                "contract metadata in any committed source files, READMEs, or code comments.\n"
                "The `Forge/` directory is on the server only and excluded from git pushes.\n"
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
                    use_mcp_contracts=settings.USE_MCP_CONTRACTS,
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

            # --- Must-read gate (MCP mode, turn 1 only) ---
            # If the builder's first turn contained zero forge_get_* calls
            # it ignored the "fetch before coding" instruction.  Inject a
            # corrective user message so the next turn fetches contracts.
            if (
                settings.USE_MCP_CONTRACTS
                and turn_count == 1
                and not any(
                    tc["name"].startswith("forge_get_")
                    for tc in tool_calls_this_turn
                )
            ):
                messages.append({
                    "role": "user",
                    "content": (
                        "You MUST fetch governance context before writing any code.\n"
                        "Call forge_get_phase_window(0) and forge_get_contract('blueprint') now.\n"
                        "Do NOT proceed until you have read the project contracts."
                    ),
                })
                await build_repo.append_build_log(
                    build_id,
                    "Must-read gate triggered: builder skipped forge_get_* on turn 1",
                    source="system", level="warn",
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
                    if settings.USE_MCP_CONTRACTS:
                        next_window = ""  # MCP mode — builder fetches via tool
                    else:
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
                    if settings.USE_MCP_CONTRACTS:
                        advance_parts.append(
                            f"\nCall `forge_get_phase_window({current_phase_num})` to get "
                            "this phase's deliverables, then emit your === PLAN === and start building."
                        )
                    else:
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

    # Mini build: hard-cap at 2 phases regardless of what the contract says
    project = await project_repo.get_project_by_id(project_id)
    if project and project.get("build_mode") == "mini" and len(phases) > 2:
        logger.info("Mini build — capping phases from %d to 2", len(phases))
        phases = phases[:2]

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

        # Create project-local virtual environment & stream setup to IDE log
        try:
            await _setup_project_environment(
                working_dir, build_id, user_id, contracts,
            )
        except Exception as exc:
            logger.warning("Failed to set up project environment (non-fatal): %s", exc)

        # Write contracts to working directory
        try:
            _write_contracts_to_workdir(working_dir, contracts)
        except Exception as exc:
            logger.warning("Failed to write contracts to workdir: %s", exc)

        # Ensure .gitignore excludes Forge/ so contracts never leak to git
        try:
            inject_forge_gitignore(working_dir)
        except Exception as exc:
            logger.warning("Failed to inject Forge .gitignore rules: %s", exc)

        # Commit + push contracts seed
        if target_type in ("github_new", "github_existing") and access_token:
            try:
                await git_client.add_all(working_dir, include_contracts=True)
                sha = await git_client.commit(
                    working_dir, "forge: seed Forge/Contracts/",
                    include_contracts=True,
                )
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

    # --- Reconnaissance: capture initial workspace snapshot ---
    _ws_snapshot = None
    try:
        from forge_ide.workspace import Workspace as _IdeWorkspace, capture_snapshot, snapshot_to_workspace_info, update_snapshot as _update_snapshot
        _ide_ws = _IdeWorkspace(working_dir)
        _ws_snapshot = capture_snapshot(_ide_ws)
        _recon_log = (
            f"Recon complete — {_ws_snapshot.total_files} files, "
            f"{_ws_snapshot.total_lines:,} lines, "
            f"{len(_ws_snapshot.symbol_table)} symbols, "
            f"{_ws_snapshot.test_inventory.test_count} tests, "
            f"{len(_ws_snapshot.schema_inventory.tables)} tables"
        )
        await build_repo.append_build_log(
            build_id, _recon_log, source="system", level="info",
        )
        await _broadcast_build_event(user_id, build_id, "recon_complete", {
            "total_files": _ws_snapshot.total_files,
            "total_lines": _ws_snapshot.total_lines,
            "test_count": _ws_snapshot.test_inventory.test_count,
            "tables": list(_ws_snapshot.schema_inventory.tables),
            "symbols_count": len(_ws_snapshot.symbol_table),
        })
        await _broadcast_build_event(user_id, build_id, "build_log", {
            "message": _recon_log, "source": "system", "level": "info",
        })
    except Exception as exc:
        logger.warning("Workspace snapshot failed (non-fatal): %s", exc)
        _ws_snapshot = None

    # --- Session Journal (Phase 43) ---
    from forge_ide.journal import SessionJournal as _SessionJournal, compute_snapshot_hash as _compute_snapshot_hash
    _journal = _SessionJournal(str(build_id))
    if _ws_snapshot is not None:
        _journal.record(
            "recon",
            f"Reconnaissance: {_ws_snapshot.total_files} files, "
            f"{_ws_snapshot.total_lines:,} lines, "
            f"{_ws_snapshot.test_inventory.test_count} tests",
            metadata={
                "total_files": _ws_snapshot.total_files,
                "total_lines": _ws_snapshot.total_lines,
                "test_count": _ws_snapshot.test_inventory.test_count,
            },
        )
        _journal.set_invariant("initial_test_count", _ws_snapshot.test_inventory.test_count)

    # --- Invariant Registry (Phase 44) ---
    from forge_ide.invariants import InvariantRegistry as _InvariantRegistry
    _inv_registry = _InvariantRegistry()
    _inv_initial: dict[str, int] = {}
    if _ws_snapshot is not None:
        _inv_initial["backend_test_count"] = _ws_snapshot.test_inventory.test_count
        _inv_initial["total_files"] = _ws_snapshot.total_files
    _inv_registry.register_builtins(_inv_initial)

    # Try restoring journal from a prior checkpoint (for resume)
    if resume_from_phase >= 0:
        try:
            _ckpt_logs, _ckpt_count = await build_repo.get_build_logs(
                build_id, search="journal_checkpoint", limit=1,
            )
            if _ckpt_logs:
                from forge_ide.journal import JournalCheckpoint as _JournalCheckpoint
                _ckpt = _JournalCheckpoint.from_json(_ckpt_logs[0]["message"])
                _journal = _SessionJournal.restore_from_checkpoint(_ckpt)
                logger.info("Restored session journal from checkpoint (phase %s)", _ckpt.phase)
                # Restore invariant registry from checkpoint
                _inv_data = _journal.invariants.pop("_inv_registry", None)
                if _inv_data and isinstance(_inv_data, dict):
                    _inv_registry = _InvariantRegistry.from_dict(_inv_data)
                    logger.info("Restored invariant registry from checkpoint (%d invariants)", len(_inv_registry))
        except Exception as _je:
            logger.warning("Failed to restore journal from checkpoint: %s", _je)

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

        # Journal: record phase start
        _journal.set_phase(phase_name)
        _journal.record(
            "phase_start",
            f"Beginning {phase_name}: {phase['name']}",
            metadata={"phase": phase_name, "phase_name": phase.get("name", ""), "task_count": len(phase.get("deliverables", []))},
        )
        await _broadcast_build_event(user_id, build_id, "build_log", {
            "message": f"Starting {phase_name}: {phase['name']}",
            "source": "system", "level": "info",
        })

        # Build workspace info — prefer snapshot, fallback to raw walk
        workspace_info = ""
        if _ws_snapshot is not None:
            try:
                # Update snapshot incrementally if files were written
                if all_files_written and _ide_ws is not None:
                    _ws_snapshot = _update_snapshot(
                        _ws_snapshot, list(all_files_written.keys()), _ide_ws,
                    )
                workspace_info = snapshot_to_workspace_info(_ws_snapshot)
            except Exception as exc:
                logger.warning("Snapshot workspace_info failed: %s", exc)
                workspace_info = ""
        if not workspace_info:
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

        # --- Build Task DAG for dependency-aware execution ---
        from forge_ide.contracts import TaskDAG as _TaskDAG, CyclicDependencyError as _CyclicDepError
        _phase_dag: _TaskDAG | None = None
        try:
            _phase_dag = _TaskDAG.from_manifest(
                manifest,
                phase_label=str(phase.get("number", "")),
            )
            # Pre-mark cached/audited files as skipped in the DAG
            for _dag_node in _phase_dag.nodes.values():
                for _mf in manifest:
                    if _mf["path"] == _dag_node.file_path and _mf.get("status") in ("audited", "fixed"):
                        _phase_dag.mark_skipped(_dag_node.id)
                        break
            await _broadcast_build_event(user_id, build_id, "dag_initialized", {
                "phase": phase_name,
                "dag": _phase_dag.to_dict(),
            })
        except _CyclicDepError:
            logger.warning("Cyclic dependency in manifest for %s — DAG disabled", phase_name)
            _phase_dag = None
        except Exception:
            logger.warning("Failed to build task DAG for %s", phase_name, exc_info=True)
            _phase_dag = None

        # Map file paths to DAG node IDs for quick lookup
        _path_to_dag_id: dict[str, str] = {}
        if _phase_dag is not None:
            for _n in _phase_dag.nodes.values():
                if _n.file_path:
                    _path_to_dag_id[_n.file_path] = _n.id

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
                    # DAG: mark skipped (already handled in DAG init, but ensure)
                    _skip_tid = _path_to_dag_id.get(file_path)
                    if _phase_dag is not None and _skip_tid and _phase_dag.nodes[_skip_tid].status.value == "pending":
                        _phase_dag.mark_skipped(_skip_tid)
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
                    # DAG: mark skipped (file already on disk)
                    _skip_tid = _path_to_dag_id.get(file_path)
                    if _phase_dag is not None and _skip_tid and _phase_dag.nodes[_skip_tid].status.value == "pending":
                        _phase_dag.mark_skipped(_skip_tid)
                    continue

            # Track which file is being generated (for audit trail on /stop etc.)
            _current_generating[bid] = file_path
            _touch_progress(build_id)
            await _set_build_activity(build_id, user_id, f"Generating {file_path}")

            # DAG: mark task in-progress
            _dag_tid = _path_to_dag_id.get(file_path)
            if _phase_dag is not None and _dag_tid:
                _phase_dag.mark_in_progress(_dag_tid)
                await _broadcast_build_event(user_id, build_id, "task_started", {
                    "task_id": _dag_tid,
                    "file_path": file_path,
                })

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

                # DAG: mark task completed + emit progress
                if _phase_dag is not None and _dag_tid:
                    _phase_dag.mark_completed(_dag_tid, actual_tokens=0)
                    await _broadcast_build_event(user_id, build_id, "task_completed", {
                        "task_id": _dag_tid,
                        "file_path": file_path,
                    })
                    await _broadcast_build_event(user_id, build_id, "dag_progress",
                        _phase_dag.get_progress().model_dump(),
                    )

                # Journal: record task + file
                _journal.record(
                    "task_completed",
                    f"Generated {file_path}",
                    task_id=_dag_tid or str(i),
                    metadata={"file_path": file_path, "size_bytes": len(content.encode('utf-8'))},
                )
                _journal.record(
                    "file_written",
                    f"Wrote {file_path} ({len(content.encode('utf-8'))} bytes)",
                    metadata={"file_path": file_path, "size_bytes": len(content.encode('utf-8'))},
                )

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

                # DAG: mark task failed + cascade blocks + emit progress
                if _phase_dag is not None and _dag_tid:
                    _phase_dag.mark_failed(_dag_tid, str(exc))
                    _blocked = _phase_dag.get_blocked_by(_dag_tid)
                    if _blocked:
                        _blocked_paths = [b.file_path for b in _blocked if b.file_path]
                        logger.warning(
                            "Task %s failed → %d downstream tasks blocked: %s",
                            _dag_tid, len(_blocked), _blocked_paths,
                        )
                    await _broadcast_build_event(user_id, build_id, "task_failed", {
                        "task_id": _dag_tid,
                        "file_path": file_path,
                        "error": str(exc),
                        "blocked_count": len(_blocked) if _blocked else 0,
                    })
                    await _broadcast_build_event(user_id, build_id, "dag_progress",
                        _phase_dag.get_progress().model_dump(),
                    )

                # Journal: record error
                _journal.record(
                    "error",
                    f"Generation failed for {file_path}: {exc}",
                    task_id=_dag_tid or str(i),
                    metadata={"file_path": file_path, "error": str(exc)},
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

                # Journal: record test/verification run
                _journal.record(
                    "test_run",
                    f"Tests: {verification.get('tests_passed', 0)} passed, "
                    f"{verification.get('tests_failed', 0)} failed",
                    metadata={
                        "passed": verification.get("tests_passed", 0),
                        "failed": verification.get("tests_failed", 0),
                        "syntax_errors": verification.get("syntax_errors", 0),
                        "fixes_applied": verification.get("fixes_applied", 0),
                    },
                )

                # --- Invariant gate (Phase 44) ---
                _gate_values = {
                    "backend_test_count": verification.get("tests_passed", 0),
                    "backend_test_failures": verification.get("tests_failed", 0),
                    "syntax_errors": verification.get("syntax_errors", 0),
                }
                _gate_results = _inv_registry.check_all(_gate_values)
                for _gr in _gate_results:
                    await _broadcast_build_event(user_id, build_id, "invariant_check", {
                        "name": _gr.name,
                        "passed": _gr.passed,
                        "expected": _gr.expected,
                        "actual": _gr.actual,
                        "constraint": _gr.constraint.value,
                    })
                    if not _gr.passed:
                        _journal.record(
                            "invariant_violated",
                            _gr.message,
                            metadata={
                                "name": _gr.name,
                                "expected": _gr.expected,
                                "actual": _gr.actual,
                                "constraint": _gr.constraint.value,
                            },
                        )
                        await build_repo.append_build_log(
                            build_id, _gr.message,
                            source="invariant", level="error",
                        )
                        await _broadcast_build_event(user_id, build_id, "build_log", {
                            "message": _gr.message,
                            "source": "invariant", "level": "error",
                        })

                # Update baselines for passing invariants
                _gate_violations = [r for r in _gate_results if not r.passed]
                if not _gate_violations:
                    for _gr in _gate_results:
                        _inv_registry.update(_gr.name, _gr.actual)
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

        # 5b. Governance gate — deterministic checks before commit
        governance_result: dict = {"passed": True, "checks": [], "blocking_failures": 0, "warnings": 0}
        if phase_files_written and touched_files:
            try:
                governance_result = await _run_governance_checks(
                    build_id, user_id, api_key,
                    manifest, working_dir, contracts,
                    touched_files, phase_name,
                )

                if not governance_result["passed"]:
                    # Attempt auto-fix for blocking governance failures (up to 2 rounds)
                    gov_fix_rounds = 2
                    for gov_fix in range(1, gov_fix_rounds + 1):
                        blocking_details = [
                            f"{c['code']}: {c['detail']}"
                            for c in governance_result.get("checks", [])
                            if c["result"] == "FAIL"
                        ]
                        _gov_msg = (
                            f"Governance gate FAIL — {governance_result['blocking_failures']} blocking "
                            f"issue(s). Attempting fix {gov_fix}/{gov_fix_rounds}..."
                        )
                        await build_repo.append_build_log(
                            build_id, _gov_msg, source="governance", level="warn",
                        )
                        await _broadcast_build_event(user_id, build_id, "build_log", {
                            "message": _gov_msg, "source": "governance", "level": "warn",
                        })

                        # Try to fix via recovery plan
                        fix_plan = (
                            "Governance gate failures detected. Fix these BLOCKING issues:\n"
                            + "\n".join(f"- {d}" for d in blocking_details)
                        )
                        existing_files: dict[str, str] = {}
                        for tf in touched_files:
                            fp = Path(working_dir) / tf
                            if fp.exists():
                                try:
                                    existing_files[tf] = fp.read_text(encoding="utf-8")
                                except Exception:
                                    pass

                        fix_manifest = await _generate_fix_manifest(
                            build_id, user_id, api_key,
                            fix_plan, existing_files,
                            "\n".join(blocking_details), contracts,
                        )

                        if fix_manifest:
                            for fm_entry in fix_manifest:
                                if fm_entry.get("action") == "delete":
                                    del_path = Path(working_dir) / fm_entry["path"]
                                    if del_path.exists():
                                        del_path.unlink()
                                    continue
                                await _fix_single_file(
                                    build_id, user_id, api_key,
                                    fm_entry, working_dir, contracts,
                                    existing_files, "\n".join(blocking_details),
                                )

                        # Re-run governance checks
                        governance_result = await _run_governance_checks(
                            build_id, user_id, api_key,
                            manifest, working_dir, contracts,
                            touched_files, phase_name,
                        )
                        if governance_result["passed"]:
                            _ok_msg = f"✓ Governance gate passed after fix round {gov_fix}"
                            await build_repo.append_build_log(
                                build_id, _ok_msg, source="governance", level="info",
                            )
                            await _broadcast_build_event(user_id, build_id, "build_log", {
                                "message": _ok_msg, "source": "governance", "level": "info",
                            })
                            break

                    if not governance_result["passed"]:
                        _fail_msg = (
                            f"⚠ Governance gate still has {governance_result['blocking_failures']} "
                            f"blocking failure(s) after {gov_fix_rounds} fix rounds — proceeding with caution"
                        )
                        await build_repo.append_build_log(
                            build_id, _fail_msg, source="governance", level="warn",
                        )
                        await _broadcast_build_event(user_id, build_id, "build_log", {
                            "message": _fail_msg, "source": "governance", "level": "warn",
                        })
            except Exception as exc:
                logger.warning("Governance gate failed: %s", exc)
                await build_repo.append_build_log(
                    build_id,
                    f"Governance gate error (non-blocking): {exc}",
                    source="governance", level="warn",
                )

        # 6. Commit after audit + verification + governance — never commit unverified code
        if phase_files_written:
            try:
                await _set_build_activity(build_id, user_id, f"Committing {phase_name}...")
                await git_client.add_all(working_dir)
                _fix_count = verification.get("fixes_applied", 0)
                _gov_tag = ""
                if governance_result.get("blocking_failures", 0) > 0:
                    _gov_tag = f", gov:{governance_result['blocking_failures']}F"
                elif governance_result.get("warnings", 0) > 0:
                    _gov_tag = f", gov:{governance_result['warnings']}W"
                commit_msg = (
                    f"forge: {phase_name} complete"
                    if audit_attempts <= 1 and _fix_count == 0 and not _gov_tag
                    else f"forge: {phase_name} complete (audit×{audit_attempts}, {_fix_count} auto-fixes{_gov_tag})"
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

        # Determine final phase verdict: audit + verification + governance
        verification_clean = (
            verification.get("syntax_errors", 0) == 0
            and verification.get("tests_failed", 0) == 0
        )
        governance_clean = governance_result.get("passed", True)

        if audit_verdict == "PASS" and verification_clean and governance_clean:
            _gov_summary = f", governance {len(governance_result.get('checks', []))} checks"
            _log_msg = f"✅ Phase {phase_name} PASS (audit + verification + governance)"
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

            # Journal: record phase complete + save checkpoint
            _journal.record(
                "phase_complete",
                f"Phase {phase_name} completed — all checks passed",
                metadata={
                    "tasks_completed": len(phase_files_written),
                    "files_written": list(phase_files_written.keys()),
                },
            )
            try:
                _dag_state = _phase_dag.to_dict() if _phase_dag else {}
                _snap_hash = _compute_snapshot_hash(list(all_files_written.keys())) if all_files_written else ""
                # Include invariant registry state in journal invariants
                _journal.invariants["_inv_registry"] = _inv_registry.to_dict()
                _ckpt = _journal.get_checkpoint(dag_state=_dag_state, snapshot_hash=_snap_hash)
                await build_repo.append_build_log(
                    build_id,
                    _ckpt.to_json(),
                    source="journal_checkpoint", level="info",
                )
                _journal.record("checkpoint_saved", f"Checkpoint saved for {phase_name}")
            except Exception as _ckpt_exc:
                logger.warning("Failed to save journal checkpoint: %s", _ckpt_exc)
        elif audit_verdict == "PASS":
            # Audit passed but verification/governance has remaining issues
            _issues: list[str] = []
            if not verification_clean:
                _issues.append(
                    f"verification: {verification.get('syntax_errors', 0)} syntax errors, "
                    f"{verification.get('tests_failed', 0)} test failures"
                )
            if not governance_clean:
                _issues.append(
                    f"governance: {governance_result.get('blocking_failures', 0)} blocking, "
                    f"{governance_result.get('warnings', 0)} warnings"
                )
            _log_msg = f"⚠ Phase {phase_name} — audit PASS, {'; '.join(_issues)}"
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
                "governance": governance_result,
            })
            # Still lock in phase so /continue works — verification issues
            # can accumulate and be fixed across phases
            await build_repo.update_completed_phases(build_id, phase_num)
            # Clean up cached manifest — phase is done
            _mc = Path(working_dir) / ".forge" / f"manifest_phase_{phase_num}.json"
            if _mc.exists():
                _mc.unlink(missing_ok=True)

            # Journal: record partial phase complete + checkpoint
            _journal.record(
                "phase_complete",
                f"Phase {phase_name} completed (partial — verification/governance issues)",
                metadata={
                    "tasks_completed": len(phase_files_written),
                    "status": "partial",
                },
            )
            try:
                _dag_state = _phase_dag.to_dict() if _phase_dag else {}
                _snap_hash = _compute_snapshot_hash(list(all_files_written.keys())) if all_files_written else ""
                _ckpt = _journal.get_checkpoint(dag_state=_dag_state, snapshot_hash=_snap_hash)
                await build_repo.append_build_log(
                    build_id,
                    _ckpt.to_json(),
                    source="journal_checkpoint", level="info",
                )
            except Exception as _ckpt_exc:
                logger.warning("Failed to save journal checkpoint (partial): %s", _ckpt_exc)
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
