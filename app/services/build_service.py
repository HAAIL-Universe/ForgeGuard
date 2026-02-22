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
from app.config import settings, get_model_for_role
from app.repos import build_repo
from app.repos import project_repo
from app.repos.user_repo import get_user_by_id
from app.services.tool_executor import BUILDER_TOOLS, execute_tool_async
from app.ws_manager import manager

logger = logging.getLogger(__name__)


def _get_workspace_dir(project_id: UUID | str) -> Path:
    """Return the persistent workspace directory for a project.

    Uses WORKSPACE_DIR env var when set; otherwise defaults to
    ~/.forgeguard/workspaces so workspaces survive server restarts.
    In Docker, set WORKSPACE_DIR=/data/workspaces and mount a volume there.
    """
    base = settings.WORKSPACE_DIR.strip() if settings.WORKSPACE_DIR else ""
    if not base:
        base = str(Path.home() / ".forgeguard" / "workspaces")
    ws_base = Path(base)
    ws_base.mkdir(parents=True, exist_ok=True)
    return ws_base / str(project_id)


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
2. **Boundary Enforcement**: Routers â†' Services â†' Repos. No skipping layers.
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
3. **Boundary Enforcement**: Routers â†' Services â†' Repos. No skipping layers.
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
    _plan_review_events,
    register_plan_review,
    pop_plan_review_response,
    cleanup_plan_review,
    _ide_ready_events,
    register_ide_ready,
    pop_ide_ready_response,
    cleanup_ide_ready,
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
    estimate_phase_cost,
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
    write_forge_config_to_workdir,
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
    _generate_single_file,
    _generate_fix_manifest,
    _CONTRACT_RELEVANCE,
)
from app.services.build.planner_agent_loop import (  # noqa: E402
    run_phase_planner_agent,
)
from app.services.build.plan_artifacts import (  # noqa: E402
    store_phase_plan,
    store_phase_outcome,
    get_prior_phase_context,
    get_current_phase_plan_context,
    clear_build_artifacts,
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

        # On Windows, .cmd/.bat files (e.g. npm.cmd) are batch scripts that
        # need cmd.exe to interpret them.  Without shell=True, Popen tries
        # CreateProcess directly, which fails and triggers a Windows dialog
        # ("Windows cannot run this program").
        _use_shell = os.name == "nt"

        def _sync() -> tuple[int, list[str]]:
            lines: list[str] = []
            try:
                proc = _sp.Popen(
                    cmd, stdout=_sp.PIPE, stderr=_sp.STDOUT,
                    text=True, cwd=working_dir,
                    env=_venv_env(working_dir),
                    shell=_use_shell,
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
    # Use 'python -m pip' instead of bare pip.exe to avoid Windows file-locking
    # (pip.exe can't overwrite itself while running as a console wrapper).
    _py_exe = str(
        venv_dir / ("Scripts" if os.name == "nt" else "bin")
        / ("python.exe" if os.name == "nt" else "python")
    )
    _setup_warnings: list[str] = []
    await _log("\u2b06 Upgrading pip...")
    _pip_rc, _ = await _stream_command(
        [_py_exe, "-m", "pip", "install", "--upgrade", "pip"],
        label="pip upgrade",
        timeout=60,
    )
    if _pip_rc != 0:
        _setup_warnings.append("pip upgrade")

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
        import shutil as _shutil_setup
        pkg_json = Path(working_dir) / "package.json"
        # On Windows, npm ships as npm.cmd — Popen can't resolve .cmd
        # without shell=True, so use the explicit extension.
        _npm_cmd = "npm.cmd" if os.name == "nt" else "npm"
        _npm_path = _shutil_setup.which(_npm_cmd)
        if not _npm_path:
            await _log(
                "\u26a0 npm not found on PATH \u2014 Node.js setup skipped. "
                "Install Node.js if this project requires a frontend build.",
                level="warn",
            )
            _setup_warnings.append("npm not found")
        elif not pkg_json.exists():
            await _log("\U0001f4e6 Initialising Node.js project (npm init)...")
            _npm_rc, _ = await _stream_command(
                [_npm_cmd, "init", "-y"],
                label="npm init",
                timeout=30,
            )
            if _npm_rc != 0:
                _setup_warnings.append("npm init")

    if _setup_warnings:
        await _log(
            f"\u26a0 Project environment ready (warnings: {', '.join(_setup_warnings)})",
            level="warn",
        )
    else:
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
    prevents deleting currently running/pending builds.

    Also cleans up remote GitHub branches that were created by each build
    (skips main/master/develop to protect default branches).
    """
    project = await project_repo.get_project_by_id(project_id)
    if not project or str(project["user_id"]) != str(user_id):
        raise ValueError("Project not found")
    if not build_ids:
        raise ValueError("No build IDs provided")

    # Fetch the builds to validate they belong to this project
    all_builds = await build_repo.get_builds_for_project(project_id)
    builds_by_id = {str(b["id"]): b for b in all_builds}
    active_build_ids = {
        str(b["id"]) for b in all_builds if b["status"] in ("running", "pending")
    }

    to_delete: list[UUID] = []
    builds_to_cleanup: list[dict] = []
    for bid in build_ids:
        if bid not in builds_by_id:
            continue  # skip IDs that don't belong to this project
        if bid in active_build_ids:
            continue  # skip active builds
        to_delete.append(UUID(bid))
        builds_to_cleanup.append(builds_by_id[bid])

    if not to_delete:
        raise ValueError("No eligible builds to delete (active builds cannot be deleted)")

    # Clean up remote GitHub branches created by these builds
    user = await get_user_by_id(user_id)
    access_token = (user or {}).get("access_token", "")
    if access_token:
        # Collect unique (repo, branch) pairs — avoid deleting the same branch twice
        _PROTECTED_BRANCHES = frozenset({"main", "master", "develop", "dev", "trunk"})
        seen_branches: set[tuple[str, str]] = set()
        for build in builds_to_cleanup:
            repo = build.get("target_ref") or ""
            branch = build.get("branch") or ""
            if not repo or not branch:
                continue
            if branch in _PROTECTED_BRANCHES:
                logger.info("Skipping deletion of protected branch %s on %s", branch, repo)
                continue
            if (repo, branch) not in seen_branches:
                seen_branches.add((repo, branch))
                try:
                    await github_client.delete_branch(access_token, repo, branch)
                    logger.info("Deleted remote branch %s on %s", branch, repo)
                except Exception as exc:
                    logger.warning(
                        "Failed to delete branch %s on %s: %s",
                        branch, repo, exc,
                    )

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
    fresh_start: bool = False,
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
            raise ValueError(
                "No contracts found — generate contracts from the project page before starting a build."
            )

    # Prevent concurrent builds
    latest = await build_repo.get_latest_build_for_project(project_id)
    if latest and latest["status"] in ("pending", "running", "paused"):
        raise ValueError("A build is already in progress for this project")

    # Auto-clean zombie builds (terminal, completed no phases — abandoned/failed early)
    zombies = await build_repo.delete_zombie_builds_for_project(project_id)
    if zombies:
        logger.info(
            "Auto-deleted %d zombie build(s) for project %s (no phase progress)",
            zombies, project_id,
        )

    # Block new build if a meaningful historical build exists (completed >= 1 phase).
    # The user must continue it or explicitly delete it via Build History first.
    latest = await build_repo.get_latest_build_for_project(project_id)
    if latest and latest["completed_phases"] >= 0:
        raise ValueError(
            "A previous build with progress exists. "
            "Open Build History to delete it, or continue it from where it left off."
        )

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
        # Use a persistent, project-scoped directory so the venv, cloned repo,
        # and installed deps survive server restarts and Docker container cycles.
        working_dir = str(_get_workspace_dir(project_id))

    # Create build record
    build = await build_repo.create_build(
        project_id,
        target_type=target_type,
        target_ref=target_ref,
        working_dir=working_dir,
        branch=branch,
        build_mode="plan_execute",
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
            fresh_start=fresh_start,
        )
    )
    _active_tasks[str(build["id"])] = task

    return build


async def _handle_clarification(
    build_id: UUID,
    user_id: UUID,
    tool_input: dict,
) -> str:
    """Handle a forge_ask_clarification tool call from the builder.

    Emits a build_clarification_request WS event, waits for the user's
    answer (up to CLARIFICATION_TIMEOUT_MINUTES), then returns the answer
    as a string for the builder to use as the tool result.
    """
    import uuid as _uuid

    from app.services.build._state import (
        register_clarification,
        pop_clarification_answer,
        increment_clarification_count,
    )

    bid = str(build_id)
    count = increment_clarification_count(bid)
    if count > settings.MAX_CLARIFICATIONS_PER_BUILD:
        return (
            "Maximum clarification limit reached. Make your best decision "
            "based on the available contracts and continue."
        )

    question_id = str(_uuid.uuid4())
    question = str(tool_input.get("question", ""))[:200]
    context  = str(tool_input.get("context", ""))[:300]
    options  = tool_input.get("options", [])

    await _broadcast_build_event(user_id, build_id, "build_clarification_request", {
        "build_id": bid,
        "question_id": question_id,
        "question": question,
        "context": context,
        "options": options,
    })

    await build_repo.append_build_log(
        build_id,
        f"Awaiting clarification: {question}",
        source="builder", level="info",
    )

    event = register_clarification(bid)

    try:
        await asyncio.wait_for(
            event.wait(),
            timeout=settings.CLARIFICATION_TIMEOUT_MINUTES * 60,
        )
    except asyncio.TimeoutError:
        pop_clarification_answer(bid)
        await _broadcast_build_event(user_id, build_id, "build_clarification_resolved", {
            "build_id": bid,
            "question_id": question_id,
            "answer": "(timed out — AI will decide)",
        })
        return (
            "No answer received within the timeout. "
            "Make your best decision based on the available contracts and continue."
        )

    answer = pop_clarification_answer(bid) or "(no answer)"

    await build_repo.append_build_log(
        build_id,
        f"User answered: {answer}",
        source="user", level="info",
    )
    await _broadcast_build_event(user_id, build_id, "build_clarification_resolved", {
        "build_id": bid,
        "question_id": question_id,
        "answer": answer,
    })

    return answer


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


async def nuke_build(project_id: UUID, user_id: UUID) -> dict:
    """Completely destroy a build — reverting all git changes and deleting the DB record.

    1. Force-cancel if the build is still active.
    2. Delete the remote branch (if non-default) **or** force-push the branch
       back to its pre-build base commit (if default branch).
    3. Delete the build record from the database.

    Returns:
        ``{"nuked": True, "build_id": "<id>", "branch_action": "..."}``

    Raises:
        ValueError: If project not found, not owned, or no build exists.
    """
    project = await project_repo.get_project_by_id(project_id)
    if not project or str(project["user_id"]) != str(user_id):
        raise ValueError("Project not found")

    latest = await build_repo.get_latest_build_for_project(project_id)
    if not latest:
        raise ValueError("No build found to nuke")

    build_id = latest["id"]
    bid = str(build_id)
    branch = latest.get("branch") or "main"
    target_ref = latest.get("target_ref") or ""
    base_sha = latest.get("base_commit_sha") or ""
    working_dir = latest.get("working_dir") or ""

    # ── 1. Force-cancel if still active ──────────────────────────────
    if latest["status"] in ("pending", "running", "paused"):
        try:
            await force_cancel_build(project_id, user_id)
        except Exception as exc:
            logger.warning("Nuke: force-cancel failed (continuing): %s", exc)

    # ── 2. Git branch cleanup ────────────────────────────────────────
    user = await get_user_by_id(user_id)
    access_token = (user or {}).get("access_token", "")
    branch_action = "none"

    protected = {"main", "master", "develop"}

    if access_token and target_ref:
        if branch not in protected:
            # Non-default branch — delete it entirely
            try:
                await github_client.delete_branch(access_token, target_ref, branch)
                branch_action = f"deleted branch '{branch}'"
                logger.info("Nuke: deleted branch %s on %s", branch, target_ref)
            except Exception as exc:
                logger.warning("Nuke: branch delete failed: %s", exc)
                branch_action = f"branch delete failed: {exc}"
        elif base_sha and working_dir:
            # Default branch — force-push back to pre-build commit
            try:
                await git_client.force_push_ref(
                    working_dir, base_sha, branch, access_token,
                )
                branch_action = f"reverted '{branch}' to {base_sha[:8]}"
                logger.info("Nuke: reverted %s to %s on %s", branch, base_sha[:8], target_ref)
            except Exception as exc:
                logger.warning("Nuke: force-push revert failed: %s", exc)
                branch_action = f"revert failed: {exc}"
        else:
            branch_action = "skipped — no base SHA or working dir"

    # ── 3. Delete DB record ──────────────────────────────────────────
    await build_repo.delete_builds([build_id])
    logger.info("Nuke: deleted build record %s", bid)

    # ── 4. Broadcast ─────────────────────────────────────────────────
    await _broadcast_build_event(user_id, build_id, "build_nuked", {
        "id": bid,
        "branch_action": branch_action,
    })

    return {"nuked": True, "build_id": bid, "branch_action": branch_action}


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

        # --- Disk fallback: detect mid-phase manifests on disk ----------
        #     When completed_phases == -1 but the working dir has cached
        #     manifests, infer the in-progress phase from the highest one.
        if _best_completed < 0 and _best_dir and Path(_best_dir).exists():
            _orph_manifests = sorted(Path(_best_dir).glob(".forge/manifest_phase_*.json"))
            if _orph_manifests:
                for _omf in _orph_manifests:
                    _omf_m = _re_resume.search(r"manifest_phase_(\d+)", _omf.name)
                    if _omf_m:
                        _omf_phase = int(_omf_m.group(1))
                        current_phase_num = max(current_phase_num, _omf_phase)
                logger.info(
                    "Orphan recovery: inferred current_phase=%d from "
                    "manifest cache in %s",
                    current_phase_num, _best_dir,
                )

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
                resume_from = _best_completed  # phase already done â†' continue
            else:
                # Use max(..., 0) so Phase 0 retry gets resume_from=0
                # (not -1, which would trigger a fresh clone over the
                # existing working directory).
                resume_from = max(current_phase_num - 1, 0)

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


async def resume_clarification(
    project_id: UUID,
    user_id: UUID,
    question_id: str,
    answer: str,
) -> dict:
    """Submit a user's answer to a pending builder clarification.

    Raises ValueError if no active build is found or no clarification is pending.
    """
    from app.services.build._state import resolve_clarification

    build = await build_repo.get_latest_build_for_project(project_id)
    if not build or str(build.get("user_id", "")) != str(user_id):
        # Also try looking up via project ownership
        proj = await project_repo.get_project_by_id(project_id)
        if not proj or str(proj.get("user_id")) != str(user_id):
            raise ValueError("No active build found for this project")
        if not build:
            raise ValueError("No active build found for this project")

    build_id = str(build["id"])
    resolved = resolve_clarification(build_id, answer)
    if not resolved:
        raise ValueError("No pending clarification for this build")

    return {"ok": True, "build_id": build_id}


async def approve_plan(
    project_id: UUID,
    user_id: UUID,
    action: str = "approve",
) -> dict:
    """Submit user's decision on a pending plan review.

    Args:
        action: ``"approve"`` to proceed, ``"reject"`` to cancel the build.

    Raises:
        ValueError: If no active build or no pending plan review.
    """
    from app.services.build._state import resolve_plan_review

    project = await project_repo.get_project_by_id(project_id)
    if not project or str(project.get("user_id")) != str(user_id):
        raise ValueError("Project not found")

    build = await build_repo.get_latest_build_for_project(project_id)
    if not build:
        raise ValueError("No active build found")

    build_id = str(build["id"])

    if action not in ("approve", "reject"):
        raise ValueError(f"Invalid action: {action}. Must be approve or reject.")

    resolved = resolve_plan_review(build_id, {"action": action})
    if not resolved:
        raise ValueError("No pending plan review for this build")

    await build_repo.append_build_log(
        build["id"],
        f"Plan {'approved' if action == 'approve' else 'rejected'} by user",
        source="user", level="info",
    )

    # On approval, commit the plan to git so it survives server restarts.
    # Non-fatal: if the workspace isn't ready yet (edge case), skip silently.
    sha: str = ""
    if action == "approve":
        try:
            commit_result = await commit_plan_to_git(project_id, user_id)
            sha = commit_result.get("sha", "")
        except Exception as exc:
            logger.warning("approve_plan: plan git commit skipped: %s", exc)

    return {"ok": True, "build_id": build_id, "action": action, "sha": sha}


async def commit_plan_to_git(project_id: UUID, user_id: UUID) -> dict:
    """Write forge_plan.json to the workspace, commit it, and push to GitHub.

    Called automatically when the user approves the plan in the IDE (via
    approve_plan).  Ensures the plan is persisted to git before any builder
    work starts — so closing/restarting does not silently lose it.
    """
    from fastapi import HTTPException

    build = await build_repo.get_latest_build_for_project(project_id)
    if not build:
        raise HTTPException(status_code=404, detail="No active build found")

    plan = await project_repo.get_cached_plan(project_id)
    if not plan:
        raise HTTPException(status_code=404, detail="No cached plan found")

    working_dir = str(_get_workspace_dir(project_id))
    if not Path(working_dir).exists():
        raise HTTPException(status_code=400, detail="Workspace not yet initialised")

    plan_file = Path(working_dir) / "forge_plan.json"
    plan_file.write_text(json.dumps(plan, indent=2))

    await git_client.add_all(working_dir)
    sha = await git_client.commit(working_dir, "forge: save build plan")

    build_id = build["id"]

    # Push to GitHub so the plan survives server restarts
    pushed = False
    if sha:
        try:
            project = await project_repo.get_project_by_id(project_id)
            user = await get_user_by_id(user_id)
            access_token = (user or {}).get("access_token", "")
            repo_full_name = (project or {}).get("repo_full_name") or build.get("target_ref", "")
            branch = build.get("branch", "main")
            if access_token and repo_full_name:
                remote_url = f"https://github.com/{repo_full_name}.git"
                await git_client.set_remote(working_dir, remote_url)
                await git_client.push(working_dir, branch=branch, access_token=access_token)
                pushed = True
        except Exception as exc:
            logger.warning("commit_plan_to_git: push failed (non-fatal): %s", exc)

    log_msg = f"📋 Plan committed ({sha[:8]})" if sha else "📋 Plan unchanged (nothing to commit)"
    if pushed:
        log_msg += " and pushed to GitHub"
    await build_repo.append_build_log(build_id, log_msg, source="system", level="info")
    return {"ok": True, "sha": sha or "", "pushed": pushed}


async def commence_build(
    project_id: UUID,
    user_id: UUID,
    action: str = "commence",
) -> dict:
    """Signal that the user is ready to start the build after IDE warm-up.

    Called after the ``forge_ide_ready`` WS event.  The build background
    task is waiting on this before it starts planning.

    Args:
        action: ``"commence"`` to start, ``"cancel"`` to abort.

    Raises:
        ValueError: If no active build or no pending ready gate.
    """
    from app.services.build._state import resolve_ide_ready

    project = await project_repo.get_project_by_id(project_id)
    if not project or str(project.get("user_id")) != str(user_id):
        raise ValueError("Project not found")

    build = await build_repo.get_latest_build_for_project(project_id)
    if not build:
        raise ValueError("No active build found")

    build_id = str(build["id"])

    if action not in ("commence", "cancel"):
        raise ValueError(f"Invalid action: {action}. Must be commence or cancel.")

    resolved = resolve_ide_ready(build_id, {"action": action})
    if not resolved:
        raise ValueError("No pending IDE ready gate for this build")

    await build_repo.append_build_log(
        build["id"],
        f"Build {'commenced' if action == 'commence' else 'cancelled'} by user",
        source="user", level="info",
    )

    return {"ok": True, "build_id": build_id, "action": action}


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

    # --- /reset — force-nuke stuck/orphaned build ---------------------
    if stripped == "/reset":
        project = await project_repo.get_project_by_id(project_id)
        if not project or str(project["user_id"]) != str(user_id):
            raise ValueError("Project not found")
        latest = await build_repo.get_latest_build_for_project(project_id)
        if not latest or latest["status"] not in ("pending", "running", "paused"):
            return {"status": "no_build", "message": "No active build to reset — type /plan or /start to begin a new build"}
        bid = str(latest["id"])
        try:
            await nuke_build(project_id, user_id)
            return {"status": "reset", "build_id": bid, "message": "Build reset — type /plan or /start to begin a new build"}
        except Exception as exc:
            raise ValueError(f"Reset failed: {exc}") from exc

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

    # --- /plan — run planner preview, populate phases panel ----
    if stripped == "/plan":
        project = await project_repo.get_project_by_id(project_id)
        if not project or str(project["user_id"]) != str(user_id):
            raise ValueError("Project not found")
        latest = await build_repo.get_latest_build_for_project(project_id)
        # Handle both "running" (builders active) and "pending" (warm-up/gate)
        if latest and latest["status"] in ("running", "pending"):
            bid = str(latest["id"])
            # Case 1: At the IDE ready gate — trigger planner in preview mode.
            # The gate is registered for "pending" status builds (warm-up done,
            # waiting for user action).  The /plan command resolves the gate with
            # action="prep" so the planner runs without commencing builders.
            if bid in _ide_ready_events:
                from app.services.build._state import resolve_ide_ready
                resolve_ide_ready(bid, {"action": "prep"})
                return {"status": "prepping", "build_id": bid, "message": "Planner running \u2014 phases will appear shortly"}
            # Case 2: Plan already generated, waiting at the review gate
            if bid in _plan_review_events:
                return {"status": "plan_ready", "build_id": bid, "message": "Plan already generated \u2014 check the phases panel. Type /start to approve and begin building."}
            # Case 3: Builders are actively running (status=running, no open gate)
            if latest["status"] == "running":
                return {"status": "building", "build_id": bid, "message": "Build in progress \u2014 plan was already generated. Check the phases panel."}
            # Case 4: pending + no open gate — warm-up still in progress or orphaned.
            # If the task is still running (e.g. mid-warm-up), tell the user to wait.
            # If orphaned (server restart), fall through to nuke + fresh start.
            _task = _active_tasks.get(bid)
            _is_active = (
                (bool(_task) and not _task.done())
                or bid in _current_generating
                or bid in _pause_events
                or bid in _pause_flags
                or bid in _build_heartbeat_tasks
                or bid in _last_progress
            )
            if _is_active:
                return {"status": "pending", "build_id": bid, "message": "Workspace is still warming up \u2014 try /plan again in a moment"}
            # Orphaned pending build — nuke it so start_build() is not blocked.
            try:
                await nuke_build(project_id, user_id)
                await asyncio.sleep(0.05)
                logger.info("/plan: auto-nuked orphaned pending build %s", bid)
            except Exception:
                logger.warning("/plan: auto-nuke of orphaned build %s failed", bid, exc_info=True)
        # If there is an orphaned DB record in "paused" state (plan already generated,
        # server restarted before user approved).  DO NOT nuke — replay the cached plan
        # so the user can review and approve without re-spending planner tokens.
        elif latest and latest["status"] == "paused":
            bid = str(latest["id"])
            _task = _active_tasks.get(bid)
            _orphaned = not (
                (bool(_task) and not _task.done())
                or bid in _current_generating
                or bid in _pause_events
                or bid in _pause_flags
                or bid in _build_heartbeat_tasks
                or bid in _last_progress
            )
            if _orphaned:
                # Check for a cached plan before nuking.
                from app.repos.project_repo import get_cached_plan
                _cached = await get_cached_plan(project_id)
                if _cached:
                    # Replay plan_complete so the IDE re-shows the plan panel.
                    _phases_raw = _cached.get("phases", [])
                    await _broadcast_build_event(user_id, UUID(bid), "plan_complete", {
                        "plan_path": "",
                        "phases": _phases_raw,
                        "token_usage": {
                            "input_tokens": 0, "output_tokens": 0,
                            "cache_read_input_tokens": 0,
                        },
                        "iterations": 0,
                    })
                    logger.info("/plan: replayed cached plan for orphaned paused build %s", bid)
                    return {
                        "status": "plan_ready",
                        "build_id": bid,
                        "message": "Plan restored \u2014 REVIEW to inspect or PUSH to approve & begin building.",
                    }
                # No cached plan — re-run the planner for this existing paused build
                # WITHOUT nuking it.  Nuking first is dangerous: if start_build then
                # fails for any reason the user is left with 0 builds and all logs lost.
                try:
                    _contracts = await project_repo.get_contracts_by_project(project_id)
                    _u = await get_user_by_id(user_id)
                    _ak = (_u or {}).get("anthropic_api_key") or ""
                    _pr = await project_repo.get_project_by_id(project_id)
                    _bm = (_pr or {}).get("build_mode", "full")
                    _mp = 3 if _bm == "mini" else None
                    from app.services.planner_service import run_project_planner
                    from app.repos.project_repo import set_cached_plan
                    _rp = await run_project_planner(
                        contracts=_contracts,
                        build_id=UUID(bid),
                        user_id=user_id,
                        api_key=_ak,
                        max_phases=_mp,
                    )
                    if _rp:
                        await set_cached_plan(project_id, _rp["plan"])
                        logger.info("/plan: re-planned orphaned paused build %s (no prior cache)", bid)
                        return {
                            "status": "plan_ready",
                            "build_id": bid,
                            "message": "Plan regenerated \u2014 REVIEW or PUSH to approve & begin building.",
                        }
                except Exception as _rp_exc:
                    logger.warning("/plan: re-plan for orphaned paused build %s failed: %s", bid, _rp_exc)
                # Re-plan failed — now nuke and start fresh as last resort.
                try:
                    await nuke_build(project_id, user_id)
                    await asyncio.sleep(0.05)
                    logger.info("/plan: auto-nuked orphaned paused build %s (no cached plan, re-plan failed)", bid)
                except Exception:
                    logger.warning("/plan: auto-nuke of orphaned build %s failed", bid, exc_info=True)
        # Start a fresh build: workspace setup runs, then the planner generates the phases
        # plan automatically, which appears in the phases panel. The build then pauses at
        # the plan-review gate so the user can inspect and approve with /start.
        result = await start_build(project_id, user_id)
        return {"status": "started", "build_id": str(result["id"]), "message": "Build starting \u2014 planner will generate your phases plan shortly. Type /start when ready to begin building."}

    # --- /start [phase N] (also handles /continue as alias) ----------
    if stripped == "/start" or stripped.startswith("/start ") or stripped == "/continue" or stripped.startswith("/continue "):
        project = await project_repo.get_project_by_id(project_id)
        if not project or str(project["user_id"]) != str(user_id):
            raise ValueError("Project not found")
        latest = await build_repo.get_latest_build_for_project(project_id)

        # ── Check if the build is waiting at the IDE ready gate ──
        if latest and latest["status"] == "running":
            bid = str(latest["id"])
            if bid in _ide_ready_events:
                from app.services.build._state import resolve_ide_ready
                resolve_ide_ready(bid, {"action": "commence"})
                return {"status": "commenced", "build_id": bid, "message": "Build commenced \u2014 planning phase starting"}

            # ── Check if the build is waiting at the plan review gate ──
            if bid in _plan_review_events:
                from app.services.build._state import resolve_plan_review
                resolve_plan_review(bid, {"action": "approve"})
                return {"status": "approved", "build_id": bid, "message": "Plan approved \u2014 builders starting"}

        # If pending — check gates first, then orphan logic.
        # A pending build is at the IDE ready gate (warm-up done, waiting for user).
        # /start should resolve the gate and begin the build, not return "starting up".
        if latest and latest["status"] == "pending":
            bid = str(latest["id"])
            # Check gates before the general "truly active" guard — if waiting, resolve
            if bid in _ide_ready_events:
                from app.services.build._state import resolve_ide_ready
                resolve_ide_ready(bid, {"action": "commence"})
                return {"status": "commenced", "build_id": bid, "message": "Build commenced \u2014 planning phase starting"}
            if bid in _plan_review_events:
                from app.services.build._state import resolve_plan_review
                resolve_plan_review(bid, {"action": "approve"})
                return {"status": "approved", "build_id": bid, "message": "Plan approved \u2014 builders starting"}
            _task = _active_tasks.get(bid)
            _is_truly_active = (
                (bool(_task) and not _task.done())
                or bid in _current_generating
                or bid in _pause_events
                or bid in _pause_flags
                or bid in _build_heartbeat_tasks
                or bid in _last_progress
            )
            if _is_truly_active:
                return {"status": "already_running", "build_id": bid, "message": "Build is starting up"}
            # Orphaned pending build — nuke it and re-fetch so downstream logic starts clean
            try:
                await nuke_build(project_id, user_id)
                await asyncio.sleep(0.05)
                latest = await build_repo.get_latest_build_for_project(project_id)
                logger.info("/start: auto-nuked orphaned pending build %s", bid)
            except Exception:
                logger.warning("/start: auto-nuke of orphaned pending build %s failed", bid, exc_info=True)

        if latest and latest["status"] == "paused":
            result = await resume_build(project_id, user_id, action="retry")
            return {"status": "resumed", "build_id": str(result["id"]), "message": "Build resumed via /start"}
        # If running, check if it's truly active or orphaned (server restart)
        if latest and latest["status"] == "running":
            bid = str(latest["id"])
            # A truly running build has either an active asyncio Task or
            # in-memory markers set by the running task.  Checking the Task
            # object directly covers the narrow race between task creation
            # (line 793, synchronous) and the first _touch_progress call
            # (line 2389, first thing _run_build() does) where the other
            # markers have not been populated yet.
            _task = _active_tasks.get(bid)
            is_truly_active = (
                (bool(_task) and not _task.done())
                or bid in _current_generating
                or bid in _ide_ready_events    # defensive: gate-waiting builds are active
                or bid in _plan_review_events  # defensive: review-gate builds are active
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
                        model=get_model_for_role("builder"),
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

            # Fetch first so remote-tracking refs exist (required for
            # --force-with-lease to work correctly).  Non-fatal for new
            # repos where the remote branch doesn't exist yet.
            try:
                await git_client.fetch(working_dir, access_token=access_token)
            except RuntimeError:
                pass  # empty remote or unreachable — continue

            # Rebase local commits onto remote; fall back to force push
            force = False
            try:
                await git_client.pull_rebase(
                    working_dir, branch=branch, access_token=access_token,
                )
            except RuntimeError:
                # Rebase failed (conflicts or no common ancestor) — force push.
                # Tracking refs were established by the fetch above, so
                # --force-with-lease is now safe to use.
                force = True

            # Push — force-with-lease if pull-rebase was skipped/failed
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
            "message": "Analysing build statusâ€¦", "source": "system", "level": "info",
        })

        try:
            from app.clients import llm_client
            result = await llm_client.chat(
                api_key=api_key,
                model=get_model_for_role("planner"),
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
                f"{'âš  Background task not found.' if not has_task else ''}"
            )

        await build_repo.append_build_log(
            build_id, f"[Status] {summary}", source="system", level="info",
        )
        await _broadcast_build_event(user_id, build_id, "build_log", {
            "message": f"🔊 {summary}", "source": "system", "level": "info",
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

    result = dict(latest)
    # If the build is waiting at the IDE ready gate, signal the frontend so it
    # can show "✔ IDE ready — type /start" even if it missed the WS event
    # (e.g. user navigated to the build page after forge_ide_ready was broadcast).
    if latest["status"] == "pending" and str(latest["id"]) in _ide_ready_events:
        result["ide_gate_pending"] = True
    # If the build is paused at plan-review (completed_phases == -1 or unset),
    # include the cached plan phases so the IDE can repopulate the phases panel
    # on reconnect without needing to re-broadcast plan_complete.
    completed = latest.get("completed_phases", -1)
    if latest["status"] == "paused" and (completed is None or completed < 0):
        from app.repos.project_repo import get_cached_plan as _gcp
        _cached = await _gcp(project_id)
        if _cached:
            result["cached_plan_phases"] = _cached.get("phases", [])
    return result


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
    fresh_start: bool = False,
) -> None:
    """Dispatch to the plan-execute build mode.

    Uses the plan-then-execute architecture (Phase 21): the planner agent
    produces a per-phase manifest, then the builder agent generates each file.

    The legacy conversation mode has been removed.
    """
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

    # Scope MCP project-scoped tool calls to this build (Phase G)
    try:
        from forge_ide.mcp.session import set_session as _mcp_set_session
        _mcp_set_session(
            project_id=str(project_id),
            build_id=str(build_id),
            user_id=str(user_id),
        )
        logger.debug("[mcp:session] scoped to project=%s build=%s", project_id, build_id)
    except Exception as _exc:
        logger.warning("[mcp:session] set_session failed (non-fatal): %s", _exc)

    try:
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
            fresh_start=fresh_start,
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
    """Legacy conversation-mode build loop — removed.

    The plan-execute architecture (``_run_build_plan_execute``) is the only
    supported build mode.  This function is kept as a stub so existing
    references compile; it is never called from ``_run_build()``.
    """
    raise NotImplementedError(
        "_run_build_conversation has been removed. "
        "Use _run_build_plan_execute (the only supported build mode)."
    )  # fmt: skip
    # noqa — unreachable legacy code below kept for reference
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
        # Skip clone if the directory is already a git repo (orphan recovery
        # may reuse an existing workspace with resume_from_phase unset).
        _conv_already_cloned = working_dir and (Path(working_dir) / ".git").is_dir()
        if target_type == "github_new" and target_ref and working_dir and not _conv_already_cloned:
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
        elif target_type == "github_existing" and target_ref and working_dir and not _conv_already_cloned:
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

        # Capture base commit SHA for nuke/revert capability
        if working_dir and (Path(working_dir) / ".git").is_dir():
            try:
                _base_sha = await git_client.rev_parse_head(working_dir)
                await build_repo.update_base_commit_sha(build_id, _base_sha)
            except Exception:
                pass  # Non-fatal — nuke will just skip revert

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

        if working_dir:
            try:
                _proj = await project_repo.get_project_by_id(project_id)
                if _proj:
                    write_forge_config_to_workdir(working_dir, _proj)
            except Exception as exc:
                logger.warning("Failed to write forge.json: %s", exc)

        # Ensure .gitignore excludes Forge/ so contracts never leak to git
        if working_dir:
            try:
                inject_forge_gitignore(working_dir)
            except Exception as exc:
                logger.warning("Failed to inject Forge .gitignore rules: %s", exc)

        # Commit + push initial workspace setup (e.g. .gitignore) to GitHub.
        if (
            working_dir
            and target_type in ("github_new", "github_existing")
            and access_token
        ):
            try:
                await git_client.add_all(working_dir, include_contracts=True)
                sha = await git_client.commit(
                    working_dir, "forge: init workspace",
                    include_contracts=True,
                )
                if sha:
                    await git_client.push(
                        working_dir, branch=branch, access_token=access_token,
                    )
                    await build_repo.append_build_log(
                        build_id,
                        "Pushed workspace init commit to GitHub",
                        source="system", level="info",
                    )
            except Exception as exc:
                logger.warning("Initial workspace push failed (non-fatal): %s", exc)

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
                + ("**Mini Build** — 2 phases: backend scaffold â†' frontend & ship.\n\n"
                   if _build_mode == "mini" else "")
                + workspace_info
                + "\n\nBegin Phase 0. Use your forge tools to fetch the contracts "
                "you need, then emit your === PLAN === and start building.\n"
            )
        else:
            # Legacy mode: full contract dump (~27K tokens)
            first_message = (
                "## âš  IMPORTANT — DO NOT EXPLORE\n"
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
                "and a diff summary of what was built.  Use forge_get_contract(name) if you\n"
                "need to re-read a contract (blueprint, stack, schema, etc.) at any point.\n\n"
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
                    msg = f"â³ Pacing: waiting {wait:.0f}s for token budget"
                    level = "info"
                else:
                    msg = f"Rate limited ({status_code}), retrying in {wait:.0f}s (attempt {attempt})â€¦"
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
                model=get_model_for_role("builder"),
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
                    if item.name == "forge_ask_clarification":
                        tool_result = await _handle_clarification(build_id, user_id, item.input)
                    else:
                        tool_result = await execute_tool_async(item.name, item.input, working_dir or "", project_id=str(project_id))

                    # Log the tool call
                    input_summary = json.dumps(item.input)[:200]
                    result_summary = tool_result[:300]
                    await build_repo.append_build_log(
                        build_id,
                        f"Tool: {item.name}({input_summary}) â†' {result_summary}",
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

                    # Broadcast scratchpad writes to UI
                    if item.name == "forge_scratchpad":
                        _sp_op = (item.input.get("operation") or "").lower()
                        if _sp_op in ("write", "append"):
                            _sp_key = item.input.get("key", "")
                            _sp_val = item.input.get("value", "")
                            await _broadcast_build_event(
                                user_id, build_id, "scratchpad_write", {
                                    "key": _sp_key,
                                    "source": "opus",
                                    "role": "builder",
                                    "summary": f"Builder wrote to scratchpad: {_sp_key}",
                                    "content_preview": str(_sp_val)[:2000],
                                    "full_length": len(str(_sp_val)),
                                },
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
                            f"Test run: {item.input.get('command', '')} â†' exit {exit_code}",
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

                # Auto-resolve errors for this phase
                try:
                    resolved = await build_repo.resolve_errors_for_phase(build_id, current_phase)
                    for err in resolved:
                        await _broadcast_build_event(user_id, build_id, "build_error_resolved", {
                            "error_id": str(err["id"]),
                            "method": "phase-complete",
                            "summary": err.get("resolution_summary", ""),
                        })
                except Exception:
                    pass

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

                        # Phase push — non-fatal; final push at build end is the safety net.
                        if target_type in ("github_new", "github_existing") and access_token:
                            try:
                                await git_client.push(
                                    working_dir, branch=branch, access_token=access_token,
                                )
                                await build_repo.append_build_log(
                                    build_id, f"↑ Pushed {current_phase} to GitHub",
                                    source="system", level="info",
                                )
                            except Exception as _push_exc:
                                logger.warning("Phase push failed (non-fatal): %s", _push_exc)
                                await build_repo.append_build_log(
                                    build_id,
                                    f"Phase push failed — will retry at build end: {_push_exc}",
                                    source="system", level="warning",
                                )

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
                            "\nUse forge_get_contract(name) to re-read any contracts "
                            "you need for this phase, then emit your === PLAN === and start building."
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

                        # Wait for user to resume (or timeout â†' abort)
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

        # ── Persist forge seal ──────────────────────────────────────────
        try:
            from app.services.certificate_aggregator import aggregate_certificate_data
            from app.services.certificate_scorer import compute_certificate_scores
            from app.services.certificate_renderer import render_certificate
            from app.repos import certificate_repo
            cert_data = await aggregate_certificate_data(project_id, user_id)
            scores    = compute_certificate_scores(cert_data)
            html      = render_certificate(scores, "html")
            await certificate_repo.create_certificate(
                project_id=project_id, build_id=build_id,
                user_id=user_id, scores=scores, certificate_html=html,
            )
            logger.info("Forge seal persisted for build %s", build_id)
        except Exception as _exc:
            logger.warning("Forge seal persistence failed (non-fatal): %s", _exc)

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

        # Broadcast file_created event with content preview for Changes tab
        _preview_lines = content.split("\n")[:150]
        _preview = "\n".join(_preview_lines)[:5000]
        await _broadcast_build_event(user_id, build_id, "file_created", {
            **file_info,
            "after_snippet": _preview,
        })

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


async def get_phase_files(
    project_id: UUID, user_id: UUID
) -> dict:
    """Return files grouped by phase number from stored phase outcome artifacts.

    Used by the frontend on load to populate the expandable phase file list
    for builds that are already partially or fully complete.

    Returns ``{"phases": {0: [{path, size_bytes, committed}], 1: [...], ...}}``.
    """
    from app.services.build.plan_artifacts import get_artifact

    project = await project_repo.get_project_by_id(project_id)
    if not project or str(project["user_id"]) != str(user_id):
        raise ValueError("Project not found")

    latest = await build_repo.get_latest_build_for_project(project_id)
    if not latest:
        raise ValueError("No builds found for this project")

    bid = str(latest["id"])
    result: dict[int, list[dict]] = {}
    # Scan up to 20 phases (practical upper bound)
    for ph_num in range(20):
        try:
            outcome = get_artifact(bid, "phase", f"outcome_phase_{ph_num}")
            if not outcome or not outcome.get("content"):
                continue
            files = outcome["content"].get("files_written", [])
            result[ph_num] = [
                {**f, "committed": True}
                for f in files
            ]
        except Exception:
            continue

    return {"phases": result}


async def build_chat(
    project_id: UUID,
    user_id: UUID,
    message: str,
    history: list[dict] | None = None,
) -> dict:
    """Answer a user's free-text question about the current build.

    Uses Haiku with read-only tools so it can inspect git log, files,
    build logs, and errors on demand rather than relying on a static
    context snapshot.

    Returns ``{"reply": str, "usage": {input_tokens, output_tokens}}``.
    """
    from app.clients.llm_client import chat as llm_chat
    from app.services.build.plan_artifacts import get_artifact

    project = await project_repo.get_project_by_id(project_id)
    if not project or str(project["user_id"]) != str(user_id):
        raise ValueError("Project not found")

    # --- Resolve API key (prefer key2, then key1, then env) ---
    user = await get_user_by_id(user_id)
    api_key = (
        (user or {}).get("anthropic_api_key_2")
        or (user or {}).get("anthropic_api_key")
        or settings.ANTHROPIC_API_KEY
    )
    if not api_key:
        raise ValueError("No API key configured -- add an Anthropic key in Settings")

    model = settings.LLM_NARRATOR_MODEL  # Haiku -- cheap + fast

    # --- Gather initial build context (lightweight summary) ---
    latest = await build_repo.get_latest_build_for_project(project_id)
    ctx_parts: list[str] = []
    bid: UUID | None = None
    working_dir: str | None = None

    if latest:
        bid = latest["id"]
        working_dir = latest.get("working_dir")
        ctx_parts.append(
            f"BUILD STATUS: {latest.get('status', 'unknown')}\n"
            f"  Phase: {latest.get('phase', '?')}\n"
            f"  Loop count: {latest.get('loop_count', 0)}\n"
            f"  Started: {latest.get('created_at', '?')}\n"
            f"  Working dir: {working_dir or '?'}"
        )

        # Phase summary from artifacts (names only, no file lists)
        phase_lines: list[str] = []
        for ph_num in range(20):
            try:
                outcome = get_artifact(str(bid), "phase", f"outcome_phase_{ph_num}")
                if not outcome or not outcome.get("content"):
                    continue
                c = outcome["content"]
                status = c.get("status", "?")
                name = c.get("phase_name", f"Phase {ph_num}")
                file_count = c.get("file_count", 0)
                phase_lines.append(
                    f"  Phase {ph_num} ({name}): {status} -- {file_count} files"
                )
            except Exception:
                continue
        if phase_lines:
            ctx_parts.append("COMPLETED PHASES:\n" + "\n".join(phase_lines))

        # Live cost
        try:
            cost = get_build_cost_live(str(bid))
            if cost and cost.get("total_cost"):
                ctx_parts.append(f"COST SO FAR: ${cost['total_cost']:.4f}")
        except Exception:
            pass
    else:
        ctx_parts.append("BUILD STATUS: No builds yet for this project")

    ctx_parts.insert(0,
        f"PROJECT: {project.get('name', '?')}\n"
        f"  Repo: {project.get('repo_full_name', '?')}\n"
        f"  Description: {(project.get('description') or 'N/A')[:200]}"
    )

    context_block = "\n\n".join(ctx_parts)

    # --- Tool definitions (read-only) ---
    tools = [
        {
            "name": "git_log",
            "description": "Show recent git commits with hashes and messages. Use this to answer questions about commits, pushes, or git history.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "max_count": {
                        "type": "integer",
                        "description": "Max commits to return (default 20)",
                        "default": 20,
                    },
                },
                "required": [],
            },
        },
        {
            "name": "git_diff",
            "description": "Show a diff summary between two git refs (e.g. HEAD~1 vs HEAD).",
            "input_schema": {
                "type": "object",
                "properties": {
                    "from_ref": {
                        "type": "string",
                        "description": "Start ref (default HEAD~1)",
                        "default": "HEAD~1",
                    },
                    "to_ref": {
                        "type": "string",
                        "description": "End ref (default HEAD)",
                        "default": "HEAD",
                    },
                },
                "required": [],
            },
        },
        {
            "name": "list_project_files",
            "description": "List all files in the build working directory (tracked + untracked).",
            "input_schema": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
        {
            "name": "read_project_file",
            "description": "Read the content of a specific file from the build working directory.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Relative file path within the project",
                    },
                },
                "required": ["path"],
            },
        },
        {
            "name": "search_build_logs",
            "description": "Search build logs for a keyword. Returns matching log entries.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "keyword": {
                        "type": "string",
                        "description": "Search term to find in logs",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Max results (default 20)",
                        "default": 20,
                    },
                },
                "required": ["keyword"],
            },
        },
        {
            "name": "get_build_errors",
            "description": "Get build errors. Can filter by resolved/unresolved status.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "resolved": {
                        "type": "boolean",
                        "description": "True=resolved only, False=unresolved only, omit=all",
                    },
                },
                "required": [],
            },
        },
        {
            "name": "get_phase_files",
            "description": "Get the list of files written in a specific build phase.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "phase_number": {
                        "type": "integer",
                        "description": "Phase number (0, 1, 2, ...)",
                    },
                },
                "required": ["phase_number"],
            },
        },
    ]

    # --- Tool execution ---
    async def _exec_tool(name: str, inp: dict) -> str:
        """Execute a read-only tool and return the result as a string."""
        try:
            if name == "git_log":
                if not working_dir:
                    return "No working directory available"
                max_count = inp.get("max_count", 20)
                raw = await git_client._run_git(
                    ["log", f"--max-count={max_count}",
                     "--format=%h %s (%cr)"],
                    cwd=working_dir,
                )
                lines = [l.strip() for l in raw.splitlines() if l.strip()]
                if not lines:
                    return "No commits found"
                return "\n".join(lines)

            elif name == "git_diff":
                if not working_dir:
                    return "No working directory available"
                from_ref = inp.get("from_ref", "HEAD~1")
                to_ref = inp.get("to_ref", "HEAD")
                return await git_client.diff_summary(
                    working_dir, from_ref=from_ref, to_ref=to_ref,
                    max_bytes=4000,
                )

            elif name == "list_project_files":
                if not working_dir:
                    return "No working directory available"
                files = await git_client.get_file_list(working_dir)
                if not files:
                    return "No files found"
                return f"{len(files)} files:\n" + "\n".join(files)

            elif name == "read_project_file":
                if not working_dir:
                    return "No working directory available"
                fpath = inp.get("path", "")
                if not fpath:
                    return "No path provided"
                # Security: prevent path traversal
                from pathlib import PurePosixPath
                clean = PurePosixPath(fpath)
                if ".." in clean.parts:
                    return "Invalid path"
                full = Path(working_dir) / str(clean)
                if not full.exists():
                    return f"File not found: {fpath}"
                try:
                    content = full.read_text(encoding="utf-8", errors="replace")
                    if len(content) > 8000:
                        return content[:8000] + f"\n... (truncated, {len(content)} chars total)"
                    return content
                except Exception as e:
                    return f"Error reading file: {e}"

            elif name == "search_build_logs":
                if not bid:
                    return "No active build"
                keyword = inp.get("keyword", "")
                limit = inp.get("limit", 20)
                logs, total = await build_repo.get_build_logs(
                    bid, search=keyword, limit=limit,
                )
                if not logs:
                    return f"No logs matching '{keyword}'"
                lines = []
                for l in logs:
                    lines.append(
                        f"[{l.get('level', 'info')}] {l.get('source', '?')}: "
                        f"{l.get('message', '')[:200]}"
                    )
                return f"{total} total matches, showing {len(logs)}:\n" + "\n".join(lines)

            elif name == "get_build_errors":
                if not bid:
                    return "No active build"
                resolved = inp.get("resolved")
                errors = await build_repo.get_build_errors(
                    bid, resolved_filter=resolved,
                )
                if not errors:
                    qualifier = "resolved" if resolved else ("unresolved" if resolved is False else "")
                    return f"No {qualifier} errors found"
                lines = []
                for e in errors[:20]:
                    lines.append(
                        f"[{e.get('severity', '?')}] {e.get('phase', '?')} / "
                        f"{e.get('file_path', '?')}: {e.get('message', '')[:150]} "
                        f"(seen {e.get('occurrence_count', 1)}x, "
                        f"{'resolved' if e.get('resolved') else 'unresolved'})"
                    )
                return f"{len(errors)} errors:\n" + "\n".join(lines)

            elif name == "get_phase_files":
                if not bid:
                    return "No active build"
                ph = inp.get("phase_number", 0)
                outcome = get_artifact(str(bid), "phase", f"outcome_phase_{ph}")
                if not outcome or not outcome.get("content"):
                    # Fallback: check DB file logs (can't distinguish phase)
                    return f"No artifact data for Phase {ph}"
                c = outcome["content"]
                files = c.get("files_written", [])
                name_str = c.get("phase_name", f"Phase {ph}")
                status = c.get("status", "?")
                if not files:
                    return f"Phase {ph} ({name_str}): {status}, no files recorded"
                lines = [f"Phase {ph} ({name_str}): {status}, {len(files)} files:"]
                for f in files:
                    lines.append(
                        f"  - {f.get('path', '?')} "
                        f"({f.get('size_bytes', 0)} bytes"
                        f"{', ' + f['language'] if f.get('language') else ''})"
                    )
                return "\n".join(lines)

            else:
                return f"Unknown tool: {name}"
        except Exception as e:
            return f"Tool error ({name}): {e}"

    # --- System prompt ---
    system_prompt = (
        "You are a build assistant for ForgeGuard, an AI-powered code build system. "
        "Answer the user's question using the tools available to you. "
        "Be direct and factual. Use short paragraphs.\n\n"
        "Available tools let you: check git log/diff, list/read project files, "
        "search build logs, get build errors, and get per-phase file lists.\n\n"
        "ALWAYS use tools to look up information rather than guessing. "
        "For example, use git_log to check commits, list_project_files to see "
        "what was built, search_build_logs to find specific events.\n\n"
        f"--- BUILD CONTEXT ---\n{context_block}\n--- END CONTEXT ---"
    )

    # --- Tool-use conversation loop (max 5 rounds) ---
    # Prepend conversation history so Haiku has context of prior turns.
    # History entries are {role, content} dicts from the frontend.
    prior: list[dict] = []
    if history:
        for h in history[-20:]:  # cap at 20 prior turns to keep context bounded
            role = h.get("role", "")
            content = h.get("content", "")
            if role in ("user", "assistant") and content:
                prior.append({"role": role, "content": str(content)})
    messages: list[dict] = prior + [{"role": "user", "content": message}]
    total_usage = {"input_tokens": 0, "output_tokens": 0}
    MAX_TOOL_ROUNDS = 5

    for _round in range(MAX_TOOL_ROUNDS):
        resp = await llm_chat(
            api_key=api_key,
            model=model,
            system_prompt=system_prompt,
            messages=messages,
            max_tokens=1024,
            tools=tools,
        )

        # Accumulate usage
        usage = resp.get("usage", {})
        total_usage["input_tokens"] += usage.get("input_tokens", 0)
        total_usage["output_tokens"] += usage.get("output_tokens", 0)

        stop_reason = resp.get("stop_reason", "end_turn")
        content_blocks = resp.get("content", [])

        if stop_reason != "tool_use":
            # Final response -- extract text
            text_parts = [
                b["text"] for b in content_blocks if b.get("type") == "text"
            ]
            return {
                "reply": "\n".join(text_parts) if text_parts else "(no response)",
                "usage": total_usage,
            }

        # Process tool calls
        messages.append({"role": "assistant", "content": content_blocks})

        tool_results = []
        for block in content_blocks:
            if block.get("type") != "tool_use":
                continue
            tool_name = block["name"]
            tool_input = block.get("input", {})
            tool_id = block["id"]

            result_text = await _exec_tool(tool_name, tool_input)
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": tool_id,
                "content": result_text[:6000],  # cap tool output
            })

        messages.append({"role": "user", "content": tool_results})

    # Exhausted rounds -- ask for final answer
    messages.append({
        "role": "user",
        "content": "Please provide your final answer based on the tool results above.",
    })
    resp = await llm_chat(
        api_key=api_key,
        model=model,
        system_prompt=system_prompt,
        messages=messages,
        max_tokens=1024,
    )
    usage = resp.get("usage", {})
    total_usage["input_tokens"] += usage.get("input_tokens", 0)
    total_usage["output_tokens"] += usage.get("output_tokens", 0)

    return {
        "reply": resp.get("text", "(no response)"),
        "usage": total_usage,
    }


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
    fresh_start: bool = False,
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

    # Planning (cache check + planner agent) runs AFTER the IDE ready gate
    # so the user has a chance to load the IDE before any LLM spend begins.
    # For resume builds, phases are passed in as a parameter and this is skipped.

    project = await project_repo.get_project_by_id(project_id)

    # Derive max_phases from project build_mode so the planner knows it is a
    # mini-build and omits auth/JWT/deployment phases automatically.
    _build_mode = (project or {}).get("build_mode", "full")
    _planner_max_phases = 3 if _build_mode == "mini" else None

    # --- Workspace setup (git clone, branch, contracts) ---
    # --- Workspace setup (skip if continuing from a prior build) ---
    # fresh_start=True means the user explicitly requested a clean build.
    # Wipe any leftover workspace so the stale .git doesn't trick the
    # _already_cloned check into treating this as a resume.
    if fresh_start and Path(working_dir).exists():
        _wipe_ok = True
        try:
            # Git pack files are read-only on Windows — chmod before rmtree.
            for _wf in Path(working_dir).rglob("*"):
                try:
                    _wf.chmod(0o777)
                except OSError:
                    pass
            shutil.rmtree(working_dir, ignore_errors=False)
        except Exception as _wipe_exc:
            logger.warning("Could not wipe stale workspace (non-fatal): %s", _wipe_exc)
            _wipe_ok = False
        if _wipe_ok:
            await build_repo.append_build_log(
                build_id, "Fresh start — wiped stale workspace",
                source="system", level="info",
            )

    # Also skip clone if the working_dir is already a git repo (orphan
    # recovery may set resume_from_phase=-1 with an existing workspace).
    _already_cloned = (Path(working_dir) / ".git").is_dir()
    if resume_from_phase < 0 and not _already_cloned:
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

        # Capture base commit SHA for nuke/revert capability
        if working_dir and (Path(working_dir) / ".git").is_dir():
            try:
                _base_sha = await git_client.rev_parse_head(working_dir)
                await build_repo.update_base_commit_sha(build_id, _base_sha)
            except Exception:
                pass  # Non-fatal — nuke will just skip revert

        # Create project-local virtual environment & stream setup to IDE log
        try:
            await _setup_project_environment(
                working_dir, build_id, user_id, contracts,
            )
        except Exception as exc:
            logger.warning("Failed to set up project environment (non-fatal): %s", exc)

        try:
            _project = await project_repo.get_project_by_id(project_id)
            if _project:
                write_forge_config_to_workdir(working_dir, _project)
        except Exception as exc:
            logger.warning("Failed to write forge.json: %s", exc)

        # Ensure .gitignore excludes Forge/ so contracts never leak to git
        try:
            inject_forge_gitignore(working_dir)
        except Exception as exc:
            logger.warning("Failed to inject Forge .gitignore rules: %s", exc)

        # Commit + push initial workspace setup (e.g. .gitignore) to GitHub
        if target_type in ("github_new", "github_existing") and access_token:
            try:
                await git_client.add_all(working_dir, include_contracts=True)
                sha = await git_client.commit(
                    working_dir, "forge: init workspace",
                    include_contracts=True,
                )
                if sha:
                    await git_client.push(working_dir, branch=branch, access_token=access_token)
                    await build_repo.append_build_log(
                        build_id, "Pushed workspace init commit to GitHub",
                        source="system", level="info",
                    )
            except Exception as exc:
                logger.warning("Initial workspace push failed (non-fatal): %s", exc)
    else:
        # Continuing from a prior build — verify workspace exists
        if not Path(working_dir).exists():
            await _fail_build(
                build_id, user_id,
                "Working directory no longer exists — cannot continue. Use /start for a fresh build.",
            )
            return
        # If .git exists but source files are missing (e.g. a prior failed
        # resume wiped them via shutil.rmtree), restore from git HEAD.
        _git_dir = Path(working_dir) / ".git"
        if _git_dir.is_dir():
            _src_count = sum(
                1 for f in Path(working_dir).rglob("*")
                if f.is_file() and ".git" not in f.parts
                and ".venv" not in f.parts and "__pycache__" not in f.parts
                and "node_modules" not in f.parts
            )
            if _src_count < 3:
                try:
                    from app.clients.git_client import _run_git as _git_run
                    await _git_run(["checkout", "HEAD", "--", "."], cwd=working_dir)
                    _restored = sum(
                        1 for f in Path(working_dir).rglob("*")
                        if f.is_file() and ".git" not in f.parts
                        and ".venv" not in f.parts
                    )
                    _log_msg = f"Restored {_restored} files from git HEAD (workspace was damaged)"
                    await build_repo.append_build_log(
                        build_id, _log_msg, source="system", level="warn",
                    )
                    await _broadcast_build_event(user_id, build_id, "build_log", {
                        "message": _log_msg, "source": "system", "level": "warn",
                    })
                except Exception as _git_exc:
                    logger.warning("Failed to restore files from git HEAD: %s", _git_exc)
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
    await build_repo.append_build_log(
        build_id, "Build started (plan-execute mode)", source="system", level="info",
    )
    await _broadcast_build_event(user_id, build_id, "build_started", {
        "id": str(build_id),
        "status": "pending",
        "phase": "Phase 0",
    })

    # Initialize interjection queue
    if str(build_id) not in _interjection_queues:
        _interjection_queues[str(build_id)] = asyncio.Queue()

    # Emit build overview (resume case — phases already known from parameter).
    # Fresh-start build_overview fires later, after the planner runs below the gate.
    if phases:
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
    if fresh_start:
        # Fresh start: skip scanning existing files
        _recon_log = "Fresh start -- skipping workspace scan, building from contracts only"
        await build_repo.append_build_log(
            build_id, _recon_log, source="system", level="info",
        )
        await _broadcast_build_event(user_id, build_id, "recon_complete", {
            "total_files": 0, "total_lines": 0, "test_count": 0,
            "tables": [], "symbols_count": 0, "fresh_start": True,
        })
        await _broadcast_build_event(user_id, build_id, "build_log", {
            "message": _recon_log, "source": "system", "level": "info",
        })
        logger.info("Fresh start build %s -- workspace recon skipped", build_id)
    else:
        try:
            from forge_ide.workspace import Workspace as _IdeWorkspace, capture_snapshot, snapshot_to_workspace_info, update_snapshot as _update_snapshot
            _ide_ws = _IdeWorkspace(working_dir)
            _ws_snapshot = capture_snapshot(_ide_ws)
            _recon_log = (
                f"Recon complete -- {_ws_snapshot.total_files} files, "
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

    # --- Phase plan-artifact housekeeping ---
    # Fresh build: clear stale phase artifacts so the planner starts clean.
    # Resume: keep existing artifacts (same build_id = same artifact scope).
    if resume_from_phase < 0:
        clear_build_artifacts(build_id)

    # ── IDE ready gate ─────────────────────────────────────────
    # Workspace is set up, recon is done, journal + invariants
    # initialised.  Pause here and let the user decide when to start
    # spending money on planners and builders.
    if _ws_snapshot is not None:
        _n_files = _ws_snapshot.total_files
        _n_lines = _ws_snapshot.total_lines
        _n_tests = _ws_snapshot.test_inventory.test_count
        _n_tables = len(_ws_snapshot.schema_inventory.tables)
        _n_symbols = len(_ws_snapshot.symbol_table)
    else:
        _n_files = _n_lines = _n_tests = _n_tables = _n_symbols = 0

    # Each line is broadcast as a separate build_log so it renders as
    # its own row in the Activity feed (no collapsed "Show more" block).
    _welcome_lines: list[tuple[str, str]] = [
        ("",                                                                     "system"),
        ("==============================================",                        "system"),
        ("   FORGE",                                                              "system"),
        ("==============================================",                        "system"),
        ("",                                                                     "system"),
        (f"Workspace ready  --  {_n_files} files, {_n_lines:,} lines",           "info"),
        (f"  {_n_tests} tests  |  {_n_tables} tables  |  {_n_symbols} symbols",  "info"),
        ("",                                                                     "system"),
        ("/plan             Preview build phases without starting",                 "info"),
        ("/start            Begin build (planning + execution)",                  "info"),
        ("/start phase N    Resume from a specific phase",                        "info"),
        ("/stop             Cancel the build",                                    "info"),
        ("...or just type a message to ask a question",                           "info"),
        ("",                                                                     "system"),
    ]

    for _wl_msg, _wl_level in _welcome_lines:
        await build_repo.append_build_log(
            build_id, _wl_msg, source="system", level=_wl_level,
        )
        await _broadcast_build_event(user_id, build_id, "build_log", {
            "message": _wl_msg, "source": "system", "level": _wl_level,
        })

    _welcome_summary = (
        f"Forge IDE ready -- {_n_files} files, {_n_lines:,} lines, "
        f"{_n_tests} tests, {_n_tables} tables, {_n_symbols} symbols. "
        "Use /start to begin."
    )
    # Register the gate BEFORE broadcasting — prevents a race where the client
    # responds to forge_ide_ready before register_ide_ready has run.
    _ready_event = register_ide_ready(str(build_id))

    await _broadcast_build_event(user_id, build_id, "forge_ide_ready", {
        "message": _welcome_summary,
        "total_files": _n_files,
        "total_lines": _n_lines,
        "test_count": _n_tests,
        "tables": list(_ws_snapshot.schema_inventory.tables) if _ws_snapshot is not None else [],
        "symbols_count": _n_symbols,
        "options": ["commence", "cancel", "prep"],
    })

    # Gate loop — supports /plan (planner preview) before /start.
    while True:
        try:
            await asyncio.wait_for(
                _ready_event.wait(),
                timeout=settings.BUILD_PAUSE_TIMEOUT_MINUTES * 60,
            )
        except asyncio.TimeoutError:
            await _fail_build(build_id, user_id, "IDE ready gate timed out \u2014 no response from user")
            return

        _ready_response = pop_ide_ready_response(str(build_id))
        _ready_action = (_ready_response or {}).get("action", "commence")

        if _ready_action == "cancel":
            await _fail_build(build_id, user_id, "User cancelled before build commenced")
            return

        if _ready_action == "prep":
            # Run the planner to populate the phases panel — does NOT start execution.
            _prep_log = "Running planner preview \u2014 phases will appear in the panel shortly"
            await build_repo.append_build_log(build_id, _prep_log, source="system", level="info")
            await _broadcast_build_event(user_id, build_id, "build_log", {
                "message": _prep_log, "source": "system", "level": "info",
            })
            if not phases:
                _prep_result = None
                try:
                    from app.services.planner_service import run_project_planner
                    _prep_result = await run_project_planner(
                        contracts=contracts,
                        build_id=build_id,
                        user_id=user_id,
                        api_key=api_key,
                        max_phases=_planner_max_phases,
                    )
                except Exception as _prep_exc:
                    logger.warning("Prep planner failed (%s) \u2014 trying legacy fallback", _prep_exc)
                if _prep_result:
                    phases = [
                        {
                            "number": p["number"],
                            "name": p["name"],
                            "objective": p.get("purpose", ""),
                            "deliverables": p.get("acceptance_criteria", []),
                        }
                        for p in _prep_result["plan"].get("phases", [])
                    ]
                    # Save plan to DB — source of truth for resume after server restart.
                    # Separated from planner try/except so a DB write failure does
                    # NOT silently appear as "Prep planner failed".
                    try:
                        from app.repos.project_repo import set_cached_plan
                        await set_cached_plan(project_id, _prep_result["plan"])
                    except Exception as _cache_exc:
                        logger.error("Failed to cache plan for build %s: %s", build_id, _cache_exc)
                    await build_repo.update_build_status(build_id, "paused")
                # Legacy fallback: parse phases contract directly
                if not phases:
                    for c in contracts:
                        if c["contract_type"] == "phases":
                            phases = _parse_phases_contract(c["content"])
                            break
            if phases:
                await _broadcast_build_event(user_id, build_id, "build_overview", {
                    "phases": [
                        {"number": p["number"], "name": p["name"], "objective": p.get("objective", "")}
                        for p in phases
                    ],
                })
                _ready_msg = "Phase plan ready \u2014 type /start to begin building"
            else:
                _ready_msg = "Planner could not produce phases \u2014 type /start to try again"
            await build_repo.append_build_log(build_id, _ready_msg, source="system", level="info")
            await _broadcast_build_event(user_id, build_id, "build_log", {
                "message": _ready_msg, "source": "system", "level": "info",
            })
            # Re-register the gate so the next /start or /plan resolves it.
            # Also touch progress so the watchdog doesn't count plan-review
            # time as a stall (the watchdog will see _ide_ready_events and
            # reset its own counter, but touching here is belt-and-suspenders).
            _touch_progress(build_id)
            _ready_event = register_ide_ready(str(build_id))
            continue

        # "commence" — break out and start building
        break

    await build_repo.update_build_status(build_id, "running", started_at=datetime.now(timezone.utc))
    await build_repo.append_build_log(
        build_id, "Build commenced by user", source="system", level="info",
    )
    await _broadcast_build_event(user_id, build_id, "build_commenced", {
        "message": "Build commenced \u2014 planning phase starting",
    })
    # ── End IDE ready gate ──────────────────────────────────

    # ── Planning: runs after user confirms /start ─────────────────────────
    if not phases:
        # ── Check DB cache first — avoid re-planning after server restart ──
        # If the user ran /plan before /start and the server restarted, the
        # plan is already in cached_plan_json.  Use it directly instead of
        # spending tokens on a duplicate planning run.
        from app.repos.project_repo import get_cached_plan as _get_cached_plan
        _db_plan = await _get_cached_plan(project_id)
        if _db_plan and _db_plan.get("phases"):
            logger.info(
                "build %s: loaded plan from DB cache (%d phases) — skipping re-plan",
                build_id, len(_db_plan["phases"]),
            )
            phases = [
                {
                    "number": p["number"],
                    "name": p["name"],
                    "objective": p.get("purpose", ""),
                    "deliverables": p.get("acceptance_criteria", []),
                }
                for p in _db_plan["phases"]
            ]

        # ── New project planner agent (primary path) ──────────────────────
        # Only runs if no cached plan exists.  Falls back to the legacy
        # phases contract if the planner also fails.
        if not phases:
            try:
                from app.services.planner_service import run_project_planner
                _plan_result = await run_project_planner(
                    contracts=contracts,
                    build_id=build_id,
                    user_id=user_id,
                    api_key=api_key,
                    max_phases=_planner_max_phases,
                )
                if _plan_result:
                    phases = [
                        {
                            "number": p["number"],
                            "name": p["name"],
                            "objective": p.get("purpose", ""),
                            "deliverables": p.get("acceptance_criteria", []),
                        }
                        for p in _plan_result["plan"].get("phases", [])
                    ]
                    # Save plan to DB — allows orphaned builds to recover the plan.
                    from app.repos.project_repo import set_cached_plan
                    await set_cached_plan(project_id, _plan_result["plan"])
            except Exception as _planner_exc:
                logger.warning(
                    "Project planner failed (%s) — falling back to phases contract",
                    _planner_exc,
                )

        # ── Legacy fallback: phases contract ──────────────────────────────
        if not phases:
            for c in contracts:
                if c["contract_type"] == "phases":
                    phases = _parse_phases_contract(c["content"])
                    break

    if not phases:
        await _fail_build(
            build_id, user_id,
            "No phases found: project planner failed and no phases contract exists",
        )
        return

    # Build overview (fresh-start — resume already broadcast above).
    if resume_from_phase < 0:
        await _broadcast_build_event(user_id, build_id, "build_overview", {
            "phases": [
                {"number": p["number"], "name": p["name"], "objective": p.get("objective", "")}
                for p in phases
            ],
        })

    for phase in phases:
        phase_num = phase["number"]
        phase_name = f"Phase {phase_num}"
        current_phase = phase_name
        _touch_progress(build_id)

        # Skip already-completed phases when continuing a prior build
        if phase_num <= resume_from_phase:
            _log_msg = f"â­ Skipping {phase_name}: {phase['name']} — already completed"
            await build_repo.append_build_log(
                build_id, _log_msg, source="system", level="info",
            )
            await _broadcast_build_event(user_id, build_id, "build_log", {
                "message": _log_msg, "source": "system", "level": "info",
            })
            # Retrieve files from stored phase outcome for the sidebar
            _skip_files: list[dict] = []
            try:
                from app.services.build.plan_artifacts import get_artifact
                _outcome = get_artifact(str(build_id), "phase", f"outcome_phase_{phase_num}")
                if _outcome and _outcome.get("content"):
                    _skip_files = [
                        {**f, "committed": True}
                        for f in _outcome["content"].get("files_written", [])
                    ]
            except Exception:
                pass
            await _broadcast_build_event(user_id, build_id, "phase_complete", {
                "phase": phase_name,
                "status": "pass",
                "files": _skip_files,
                "committed": True,
            })
            # Auto-resolve errors for this phase
            try:
                resolved = await build_repo.resolve_errors_for_phase(build_id, phase_name)
                for err in resolved:
                    await _broadcast_build_event(user_id, build_id, "build_error_resolved", {
                        "error_id": str(err["id"]),
                        "method": "phase-complete",
                        "summary": err.get("resolution_summary", ""),
                    })
            except Exception:
                pass
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
        if fresh_start and not all_files_written:
            # Fresh start + first phase: planner sees empty workspace
            workspace_info = "(empty workspace -- fresh start)"
        elif _ws_snapshot is not None:
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
        _phase_chunks: list[dict] = []  # produced by run_phase_planner_agent alongside manifest

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
            # Retrieve prior-phase context for cross-phase awareness
            _prior_ctx = get_prior_phase_context(build_id, phase_num)
            _phase_plan = await run_phase_planner_agent(
                build_id=build_id, user_id=user_id, api_key=api_key,
                contracts=contracts, phase=phase, workspace_info=workspace_info,
                working_dir=working_dir, prior_phase_context=_prior_ctx,
            )
            if _phase_plan:
                manifest = _phase_plan["manifest"]
                _phase_chunks = _phase_plan.get("chunks", [])

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

            # Store phase plan as artifact for cross-phase context
            store_phase_plan(build_id, phase, manifest)

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
            # Retrieve prior-phase context for cross-phase awareness
            _prior_ctx = get_prior_phase_context(build_id, phase_num)
            _phase_plan_retry = await run_phase_planner_agent(
                build_id=build_id, user_id=user_id, api_key=api_key,
                contracts=contracts, phase=phase, workspace_info=workspace_info,
                working_dir=working_dir, prior_phase_context=_prior_ctx,
            )
            if _phase_plan_retry:
                manifest = _phase_plan_retry["manifest"]
                _phase_chunks = _phase_plan_retry.get("chunks", [])
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
        # Fix queue: files auditor couldn't fix â†' builder picks up after generation
        _fix_queue: asyncio.Queue[tuple[str, str]] = asyncio.Queue()
        # Track only files created/modified in this phase to scope verification
        touched_files: set[str] = set()
        # Use key 2 for auditor if available (separate rate limits)
        _audit_key = api_key_2.strip() if api_key_2.strip() else api_key
        # Build API key pool for multi-key rotation (BYOK keys)
        pool_keys = [api_key]
        if api_key_2.strip():
            pool_keys.append(api_key_2.strip())
        key_pool = ApiKeyPool(
            api_keys=pool_keys,
            input_tpm=settings.ANTHROPIC_INPUT_TPM,
            output_tpm=settings.ANTHROPIC_OUTPUT_TPM,
        )
        # Incremental commit counter (how many commits so far this phase)
        _incr_commit_count = 0

        # --- Incremental commit+push helper (saves work after each chunk) ---
        async def _incremental_commit_push(label: str, file_count: int) -> str | None:
            """Git add + commit + push for a chunk of audited files.

            Returns the commit SHA if successful, None otherwise.
            """
            nonlocal _incr_commit_count
            if not working_dir or not phase_files_written:
                return None
            try:
                await git_client.add_all(working_dir)
                _incr_commit_count += 1
                _cm = f"forge: {phase_name} {label} ({file_count} files)"
                sha = await git_client.commit(working_dir, _cm)
                if not sha:
                    return None  # nothing to commit (no changes)
                _cmsg = f"Committed {label}: {sha[:8]} ({file_count} files)"
                await build_repo.append_build_log(
                    build_id, _cmsg, source="system", level="info",
                )
                await _broadcast_build_event(user_id, build_id, "build_log", {
                    "message": _cmsg, "source": "system", "level": "info",
                })
            except Exception as _ce:
                logger.warning("Incremental commit failed (%s): %s", label, _ce)
                return None

            # Push immediately to save remotely
            if target_type in ("github_new", "github_existing") and access_token:
                for _pa in range(1, settings.GIT_PUSH_MAX_RETRIES + 1):
                    try:
                        await git_client.push(
                            working_dir, branch=branch, access_token=access_token,
                        )
                        await build_repo.append_build_log(
                            build_id,
                            f"Pushed {label} to GitHub",
                            source="system", level="info",
                        )
                        await _broadcast_build_event(user_id, build_id, "build_log", {
                            "message": f"Pushed {label} to GitHub",
                            "source": "system", "level": "info",
                        })
                        break
                    except Exception as _pe:
                        logger.warning(
                            "Incremental push attempt %d/%d failed (%s): %s",
                            _pa, settings.GIT_PUSH_MAX_RETRIES, label, _pe,
                        )
                        if _pa < settings.GIT_PUSH_MAX_RETRIES:
                            await asyncio.sleep(2 ** _pa)
            return sha

        # ──────────────────────────────────────────────────────────────
        # 2a. Tier-based parallel generation
        #     Sonnet plans interfaces per tier → Opus sub-agents build
        #     in parallel → extract actual interfaces for next tier.
        #     Any files not covered fall through to the sequential loop.
        # ──────────────────────────────────────────────────────────────
        _tier_handled: set[str] = set()  # paths fully generated by tier system
        try:
            # Pre-load cached / already-on-disk files so tier system can skip them
            _cached_content: dict[str, str] = {}
            for _pre in manifest:
                _pp = _pre["path"]
                _ps = _pre.get("status", "pending")
                _ep = Path(working_dir) / _pp
                if _ps in ("audited", "fixed") and _ep.exists() and _ep.stat().st_size > 0:
                    _cached_content[_pp] = _ep.read_text(encoding="utf-8", errors="replace")
                    phase_files_written[_pp] = _cached_content[_pp]
                    all_files_written[_pp] = _cached_content[_pp]
                    _tier_handled.add(_pp)
                    _pv = _pre.get("audit_verdict", "PASS")
                    await _broadcast_build_event(user_id, build_id, "file_generated", {
                        "path": _pp, "size_bytes": _ep.stat().st_size,
                        "language": _detect_language(_pp),
                        "tokens_in": 0, "tokens_out": 0, "duration_ms": 0,
                        "skipped": True,
                    })
                    await _broadcast_build_event(user_id, build_id, "file_audited", {
                        "path": _pp, "verdict": _pv, "findings": "", "duration_ms": 0,
                    })
                    if _pv == "FAIL":
                        blocking_files.append((_pp, _pre.get("audit_findings", "")))
                    else:
                        passed_files.append(_pp)
                elif _pre.get("action", "create") == "create" and _ep.exists() and _ep.stat().st_size > 0:
                    _cached_content[_pp] = _ep.read_text(encoding="utf-8", errors="replace")
                    phase_files_written[_pp] = _cached_content[_pp]
                    all_files_written[_pp] = _cached_content[_pp]
                    _tier_handled.add(_pp)
                    await _broadcast_build_event(user_id, build_id, "file_generated", {
                        "path": _pp, "size_bytes": _ep.stat().st_size,
                        "language": _detect_language(_pp),
                        "tokens_in": 0, "tokens_out": 0, "duration_ms": 0,
                        "skipped": True,
                    })
                    _update_manifest_cache(_manifest_cache_path, _pp, "audited", "PASS")
                    await _broadcast_build_event(user_id, build_id, "file_audited", {
                        "path": _pp, "verdict": "PASS", "findings": "", "duration_ms": 0,
                    })
                    passed_files.append(_pp)

            _pending_manifest = [f for f in manifest if f["path"] not in _tier_handled]

            if _pending_manifest:
                from app.services.build.planner import (
                    _review_chunk_completion,
                    _plan_tier_interfaces, _extract_tier_interfaces,
                    _review_written_files, execute_tier,
                )

                # ── Use chunks produced by the phase planner agent ────
                # run_phase_planner_agent already planned manifest + chunks
                # together.  Filter to files still pending (not tier-handled).
                _pending_paths = {f["path"] for f in _pending_manifest}
                chunks = [
                    {**c, "files": [f for f in c.get("files", []) if f in _pending_paths]}
                    for c in _phase_chunks
                    if any(f in _pending_paths for f in c.get("files", []))
                ]
                # Fallback: one chunk for all pending files if agent gave none
                if not chunks:
                    chunks = [{
                        "name": f"{phase_name} files",
                        "files": [f["path"] for f in _pending_manifest],
                    }]
                _chunk_log = f"Planner divided phase into {len(chunks)} chunks"
                await build_repo.append_build_log(build_id, _chunk_log, source="planner", level="info")
                await _broadcast_build_event(user_id, build_id, "build_log", {
                    "message": _chunk_log, "source": "planner", "level": "info",
                })
                await _broadcast_build_event(user_id, build_id, "tiers_computed", {
                    "phase": phase_name,
                    "tier_count": len(chunks),
                    "tiers": [
                        {"tier": ci, "name": c.get("name", ""), "files": c["files"]}
                        for ci, c in enumerate(chunks)
                    ],
                })

                # ── Plan confirmation gate ────────────────────────────
                # Pause and show the plan to the user before spending
                # money on Opus builders.  The user can approve, edit,
                # or reject.  Mirrors the clarification wait pattern.
                _cost_so_far = float(_build_running_cost.get(str(build_id), 0))
                _cost_estimate = estimate_phase_cost(
                    _pending_manifest, chunks,
                    spent_so_far=_cost_so_far,
                    spend_cap=_build_spend_caps.get(str(build_id)),
                )

                # Build human-readable plan summary
                _plan_lines: list[str] = []
                for _ci, _ch in enumerate(chunks):
                    _ch_name = _ch.get("name", f"Chunk {_ci}")
                    _ch_files = _ch.get("files", [])
                    _plan_lines.append(f"**Chunk {_ci + 1}: {_ch_name}**")
                    for _fp in _ch_files:
                        _entry = next((f for f in _pending_manifest if f["path"] == _fp), {})
                        _purpose = _entry.get("purpose", "")
                        _est = _entry.get("estimated_lines", "?")
                        _plan_lines.append(f"  - `{_fp}` — {_purpose} (~{_est} lines)")
                    _plan_lines.append("")
                _plan_text = "\n".join(_plan_lines)

                await _broadcast_build_event(user_id, build_id, "plan_review", {
                    "phase": phase_name,
                    "phase_num": phase_num,
                    "plan_text": _plan_text,
                    "chunks": [
                        {
                            "index": ci,
                            "name": c.get("name", f"Chunk {ci}"),
                            "files": [
                                {
                                    "path": fp,
                                    "purpose": next(
                                        (f.get("purpose", "") for f in _pending_manifest if f["path"] == fp), ""
                                    ),
                                    "estimated_lines": next(
                                        (f.get("estimated_lines", 0) for f in _pending_manifest if f["path"] == fp), 0
                                    ),
                                    "language": next(
                                        (f.get("language", "") for f in _pending_manifest if f["path"] == fp), ""
                                    ),
                                }
                                for fp in c.get("files", [])
                            ],
                        }
                        for ci, c in enumerate(chunks)
                    ],
                    "cost_estimate": _cost_estimate,
                    "options": ["approve", "reject"],
                })

                await build_repo.append_build_log(
                    build_id,
                    f"Plan ready for review: {len(chunks)} chunks, "
                    f"{len(_pending_manifest)} files, "
                    f"estimated ${_cost_estimate['estimated_cost_low_usd']:.2f}"
                    f"–${_cost_estimate['estimated_cost_high_usd']:.2f}",
                    source="planner", level="info",
                )

                # Wait for user to approve/reject.
                # Touch progress so the watchdog doesn't force-fail while
                # the user is reading the plan (user-think-time, not a stall).
                _touch_progress(build_id)
                _review_event = register_plan_review(str(build_id))
                try:
                    await asyncio.wait_for(
                        _review_event.wait(),
                        timeout=settings.BUILD_PAUSE_TIMEOUT_MINUTES * 60,
                    )
                except asyncio.TimeoutError:
                    await _fail_build(build_id, user_id, "Plan review timed out — no response from user")
                    return

                _review_response = pop_plan_review_response(str(build_id))
                _review_action = (_review_response or {}).get("action", "approve")

                if _review_action == "reject":
                    await _fail_build(build_id, user_id, "User rejected the build plan")
                    return

                await build_repo.append_build_log(
                    build_id,
                    f"Plan approved by user — executing {len(chunks)} chunks",
                    source="system", level="info",
                )
                await _broadcast_build_event(user_id, build_id, "plan_approved", {
                    "phase": phase_name,
                    "chunks": len(chunks),
                    "files": len(_pending_manifest),
                })
                # ── End plan confirmation gate ────────────────────────

                _accumulated_interfaces = ""

                # Helper: merge chunk results into tracking dicts
                async def _merge_tier_results(
                    tier_written: dict[str, str],
                    tier_files_ref: list[dict],
                    tier_idx_ref: int,
                ) -> None:
                    for fp, content in tier_written.items():
                        phase_files_written[fp] = content
                        all_files_written[fp] = content
                        touched_files.add(fp)
                        _tier_handled.add(fp)
                        file_info = {
                            "path": fp,
                            "size_bytes": len(content.encode("utf-8")),
                            "language": _detect_language(fp),
                        }
                        if not any(f["path"] == fp for f in files_written_list):
                            files_written_list.append(file_info)

                        # Broadcast file_created so the Changes tab updates
                        _preview = "\n".join(content.split("\n")[:150])[:5000]
                        await _broadcast_build_event(
                            user_id, build_id, "file_created", {
                                **file_info,
                                "after_snippet": _preview,
                            },
                        )

                        _journal.record(
                            "file_written",
                            f"Wrote {fp} ({len(content.encode('utf-8'))} bytes)",
                            metadata={"file_path": fp, "size_bytes": len(content.encode('utf-8'))},
                        )

                        _purpose = next(
                            (f.get("purpose", "") for f in tier_files_ref if f["path"] == fp), "",
                        )
                        pending_file_audits.append(asyncio.create_task(
                            _audit_and_cache(
                                _manifest_cache_path,
                                build_id, user_id,
                                _audit_key,
                                fp, content, _purpose,
                                audit_llm_enabled,
                                audit_index=len(pending_file_audits) + 1,
                                audit_total=len(manifest),
                                working_dir=working_dir,
                                fix_queue=_fix_queue,
                            )
                        ))

                for chunk_idx, chunk in enumerate(chunks):
                    chunk_name = chunk.get("name", f"Chunk {chunk_idx}")
                    chunk_files = chunk.get("entries", [])
                    builder_prompt = chunk.get("builder_prompt", "")
                    work_order = chunk.get("work_order", {})

                    # Cancel / pause check between chunks
                    bid = str(build_id)
                    if bid in _cancel_flags:
                        _cancel_flags.discard(bid)
                        await _fail_build(build_id, user_id, "Build stopped via /stop")
                        return
                    if bid in _pause_flags:
                        _pause_flags.discard(bid)
                        await _pause_build(build_id, user_id, phase_name, 0, "Build paused via /pause")
                        event = _pause_events.get(bid)
                        if event:
                            try:
                                await asyncio.wait_for(event.wait(), timeout=settings.BUILD_PAUSE_TIMEOUT_MINUTES * 60)
                            except asyncio.TimeoutError:
                                await _fail_build(build_id, user_id, "RISK_EXCEEDS_SCOPE: pause timed out")
                                return
                        action = _resume_actions.pop(bid, "retry")
                        _pause_events.pop(bid, None)
                        await build_repo.resume_build(build_id)
                        if action == "abort":
                            await _fail_build(build_id, user_id, "Build aborted after /pause")
                            return

                    # ── Step 1: Planner prepares interface map for this chunk ──
                    if chunk_idx == 0:
                        # First chunk — no prior interfaces to reference
                        interface_map = ""
                        await build_repo.append_build_log(
                            build_id,
                            f"Chunk 0 ({chunk_name}): foundation chunk — skipping interface planning",
                            source="planner", level="info",
                        )
                    else:
                        await _set_build_activity(
                            build_id, user_id,
                            f"Chunk {chunk_idx}/{len(chunks)-1} ({chunk_name}): Planning interfaces...",
                            model="sonnet",
                        )
                        interface_map = await _plan_tier_interfaces(
                            build_id, user_id, api_key,
                            chunk_idx, chunk_files,
                            _accumulated_interfaces,
                            contracts, phase_deliverables,
                            working_dir,
                        )

                    # Inject the planner's work_order + builder_prompt into the interface map
                    _work_order_parts: list[str] = []
                    if work_order.get("objective"):
                        _work_order_parts.append(
                            f"## Chunk Objective\n{work_order['objective']}"
                        )
                    if work_order.get("constraints"):
                        _work_order_parts.append(
                            "## Contract Constraints\n"
                            + "\n".join(f"- {c}" for c in work_order["constraints"])
                        )
                    if work_order.get("patterns"):
                        _work_order_parts.append(
                            "## Patterns to Follow\n"
                            + "\n".join(f"- {p}" for p in work_order["patterns"])
                        )
                    if work_order.get("success_criteria"):
                        _work_order_parts.append(
                            "## Success Criteria\n"
                            + "\n".join(f"- {c}" for c in work_order["success_criteria"])
                        )
                    # Fall back to legacy builder_prompt if no structured work_order
                    if not _work_order_parts and builder_prompt:
                        _work_order_parts.append(
                            f"## Planner Instructions\n{builder_prompt}"
                        )
                    if _work_order_parts:
                        interface_map = (
                            "\n\n".join(_work_order_parts) + "\n\n" + interface_map
                        )

                    # ── Step 2: Send chunk to Builder ──
                    await _set_build_activity(
                        build_id, user_id,
                        f"Chunk {chunk_idx}/{len(chunks)-1} ({chunk_name}): "
                        f"Building {len(chunk_files)} files...",
                        model="opus",
                    )
                    await _broadcast_build_event(user_id, build_id, "build_log", {
                        "message": f"Sending chunk {chunk_idx} ({chunk_name}) to Builder — {len(chunk_files)} files",
                        "source": "planner", "level": "info",
                    })

                    chunk_written = await execute_tier(
                        build_id, user_id, api_key,
                        chunk_idx, chunk_files, contracts,
                        phase_deliverables, working_dir,
                        interface_map, all_files_written,
                        key_pool=key_pool,
                        audit_api_key=_audit_key,
                    )

                    # ── Step 3: Merge results + audit ──
                    _chunk_audit_start = len(pending_file_audits)
                    await _merge_tier_results(chunk_written, chunk_files, chunk_idx)
                    _chunk_audit_tasks = pending_file_audits[_chunk_audit_start:]

                    # Broadcast how many audits are in-flight so UI doesn't go quiet
                    if _chunk_audit_tasks:
                        _audit_file_names = ", ".join(
                            fp for fp in chunk_written.keys()
                        )[:300]
                        await _broadcast_build_event(user_id, build_id, "build_log", {
                            "message": (
                                f"Chunk {chunk_idx}: {len(_chunk_audit_tasks)} file audit(s) queued "
                                f"({_audit_file_names})"
                            ),
                            "source": "audit", "level": "info",
                        })

                    # ── Step 4: Planner reviews what was built ──
                    remaining = chunks[chunk_idx + 1:]
                    chunk_interfaces = await _review_chunk_completion(
                        build_id, user_id, api_key,
                        chunk_idx, chunk_name,
                        chunk_written, remaining,
                        _accumulated_interfaces, working_dir,
                    )
                    _accumulated_interfaces += f"\n\n{chunk_interfaces}"

                    # Quick Sonnet review (advisory, non-blocking)
                    try:
                        _reviews = await _review_written_files(
                            build_id, user_id, api_key,
                            chunk_written, interface_map, chunk_idx,
                        )
                        _warn_count = sum(1 for r in _reviews if r["verdict"] == "warn")
                        if _warn_count > 0:
                            await _set_build_activity(
                                build_id, user_id,
                                f"Sonnet found {_warn_count} warning(s) in chunk {chunk_idx}",
                                model="sonnet",
                            )
                    except Exception:
                        pass

                    # ── Step 5: Await audits ──
                    if _chunk_audit_tasks:
                        await _set_build_activity(
                            build_id, user_id,
                            f"Chunk {chunk_idx}: Awaiting {len(_chunk_audit_tasks)} audits...",
                        )
                        _chunk_raw = await asyncio.gather(
                            *_chunk_audit_tasks, return_exceptions=True,
                        )
                        _pass_count = 0
                        _fail_count = 0
                        for _tr in _chunk_raw:
                            if isinstance(_tr, BaseException):
                                logger.warning("Chunk %d audit error: %s", chunk_idx, _tr)
                                continue
                            fpath, fverdict, ffindings = _tr
                            if fverdict == "FAIL":
                                blocking_files.append((fpath, ffindings))
                                _fail_count += 1
                            else:
                                passed_files.append(fpath)
                                _pass_count += 1

                        # Broadcast audit summary so user sees progress
                        await _broadcast_build_event(user_id, build_id, "build_log", {
                            "message": (
                                f"Chunk {chunk_idx} audits done: "
                                f"{_pass_count} passed, {_fail_count} failed"
                            ),
                            "source": "audit", "level": "info" if _fail_count == 0 else "warn",
                        })

                    # ── Step 6: Fix any audit failures ──
                    if not _fix_queue.empty():
                        _touch_progress(build_id)
                        await _set_build_activity(
                            build_id, user_id,
                            f"Chunk {chunk_idx}: Builder fixing audit failures...",
                            model="opus",
                        )
                        _chunk_fixes = await _builder_drain_fix_queue(
                            build_id, user_id, api_key, _audit_key,
                            _fix_queue, working_dir, _manifest_cache_path,
                            audit_llm_enabled,
                        )
                        for fpath, fverdict, ffindings in _chunk_fixes:
                            if fverdict == "PASS":
                                blocking_files = [
                                    (bp, bf) for bp, bf in blocking_files if bp != fpath
                                ]
                                if fpath not in passed_files:
                                    passed_files.append(fpath)
                                try:
                                    _fp_full = Path(working_dir) / fpath
                                    if _fp_full.exists():
                                        _fc = _fp_full.read_text(encoding="utf-8")
                                        phase_files_written[fpath] = _fc
                                        all_files_written[fpath] = _fc
                                        touched_files.add(fpath)
                                except Exception:
                                    pass
                            elif not any(bp == fpath for bp, _ in blocking_files):
                                blocking_files.append((fpath, ffindings))

                    # ── Step 7: Commit + push ──
                    if chunk_written:
                        await _incremental_commit_push(
                            f"chunk {chunk_idx}: {chunk_name}",
                            len(chunk_written),
                        )

                    await build_repo.append_build_log(
                        build_id,
                        f"Chunk {chunk_idx} ({chunk_name}) done: "
                        f"{len(chunk_written)}/{len(chunk_files)} files — "
                        f"Planner ready for next chunk",
                        source="planner", level="info",
                    )

        except Exception as _tier_exc:
            logger.error("Chunk execution failed: %s — falling back to sequential", _tier_exc, exc_info=True)
            _tier_err_msg = f"Chunk system error: {_tier_exc} — remaining files will be generated sequentially"
            await build_repo.append_build_log(
                build_id, _tier_err_msg,
                source="system", level="warn",
            )
            await _broadcast_build_event(user_id, build_id, "build_error", {
                "error_detail": _tier_err_msg,
                "severity": "error",
                "source": "tier_system",
            })

        # ──────────────────────────────────────────────────────────────
        # 2b. Sequential fallback — generate any files NOT handled
        #     by the tier system (errors, missing, etc.)
        # ──────────────────────────────────────────────────────────────
        _remaining_count = sum(1 for f in manifest if f["path"] not in _tier_handled)
        if _remaining_count:
            _fb_msg = f"Generating {_remaining_count} remaining files sequentially..."
            await build_repo.append_build_log(build_id, _fb_msg, source="system", level="info")
            await _broadcast_build_event(user_id, build_id, "build_log", {
                "message": _fb_msg, "source": "system", "level": "info",
            })

        _seq_since_commit = 0  # files generated since last incremental commit
        _seq_audit_start = len(pending_file_audits)  # track sequential audits

        for i, file_entry in enumerate(manifest):
            file_path = file_entry["path"]

            # Skip anything already handled by tier execution or pre-load
            if file_path in _tier_handled:
                continue

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
            await _set_build_activity(build_id, user_id, f"Generating {file_path}", model="opus")

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
                # Get current phase plan context for cross-file awareness
                _plan_ctx = get_current_phase_plan_context(build_id, phase_num)
                content = await _generate_single_file(
                    build_id, user_id, api_key,
                    file_entry, contracts, context,
                    phase_deliverables, working_dir,
                    phase_plan_context=_plan_ctx,
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

                # Broadcast file_created so the Changes tab updates
                _preview = "\n".join(content.split("\n")[:150])[:5000]
                await _broadcast_build_event(
                    user_id, build_id, "file_created", {
                        **file_info,
                        "after_snippet": _preview,
                    },
                )

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
                _seq_since_commit += 1

                # Incremental commit every 3 sequential files
                if _seq_since_commit >= 3:
                    _seq_audits = pending_file_audits[_seq_audit_start:]
                    if _seq_audits:
                        _seq_raw = await asyncio.gather(
                            *_seq_audits, return_exceptions=True,
                        )
                        for _sr in _seq_raw:
                            if isinstance(_sr, BaseException):
                                continue
                            fpath, fverdict, ffindings = _sr
                            if fverdict == "FAIL":
                                blocking_files.append((fpath, ffindings))
                            else:
                                passed_files.append(fpath)
                    # Drain fix queue
                    if not _fix_queue.empty():
                        _sq_fixes = await _builder_drain_fix_queue(
                            build_id, user_id, api_key, _audit_key,
                            _fix_queue, working_dir, _manifest_cache_path,
                            audit_llm_enabled,
                        )
                        for fpath, fverdict, ffindings in _sq_fixes:
                            if fverdict == "PASS":
                                blocking_files = [
                                    (bp, bf) for bp, bf in blocking_files if bp != fpath
                                ]
                                if fpath not in passed_files:
                                    passed_files.append(fpath)
                                try:
                                    _fp_full = Path(working_dir) / fpath
                                    if _fp_full.exists():
                                        _fc = _fp_full.read_text(encoding="utf-8")
                                        phase_files_written[fpath] = _fc
                                        all_files_written[fpath] = _fc
                                        touched_files.add(fpath)
                                except Exception:
                                    pass
                            elif not any(bp == fpath for bp, _ in blocking_files):
                                blocking_files.append((fpath, ffindings))
                    await _incremental_commit_push(
                        f"batch {_incr_commit_count + 1}",
                        _seq_since_commit,
                    )
                    _seq_since_commit = 0
                    _seq_audit_start = len(pending_file_audits)

            except Exception as exc:
                _current_generating.pop(bid, None)
                logger.error("Failed to generate %s: %s", file_path, exc)
                _file_err_msg = f"Failed to generate {file_path}: {exc}"
                await build_repo.append_build_log(
                    build_id, _file_err_msg,
                    source="system", level="error",
                )
                await _broadcast_build_event(user_id, build_id, "build_error", {
                    "error_detail": _file_err_msg,
                    "severity": "error",
                    "source": "file_generation",
                })

                # DAG: mark task failed + cascade blocks + emit progress
                if _phase_dag is not None and _dag_tid:
                    _phase_dag.mark_failed(_dag_tid, str(exc))
                    _blocked = _phase_dag.get_blocked_by(_dag_tid)
                    if _blocked:
                        _blocked_paths = [b.file_path for b in _blocked if b.file_path]
                        logger.warning(
                            "Task %s failed â†' %d downstream tasks blocked: %s",
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

        # Commit any remaining sequential files not yet committed
        if _seq_since_commit > 0:
            _remaining_audits = pending_file_audits[_seq_audit_start:]
            if _remaining_audits:
                _rem_raw = await asyncio.gather(
                    *_remaining_audits, return_exceptions=True,
                )
                for _rr in _rem_raw:
                    if isinstance(_rr, BaseException):
                        continue
                    fpath, fverdict, ffindings = _rr
                    if fverdict == "FAIL":
                        blocking_files.append((fpath, ffindings))
                    else:
                        passed_files.append(fpath)
            if not _fix_queue.empty():
                _rem_fixes = await _builder_drain_fix_queue(
                    build_id, user_id, api_key, _audit_key,
                    _fix_queue, working_dir, _manifest_cache_path,
                    audit_llm_enabled,
                )
                for fpath, fverdict, ffindings in _rem_fixes:
                    if fverdict == "PASS":
                        blocking_files = [
                            (bp, bf) for bp, bf in blocking_files if bp != fpath
                        ]
                        if fpath not in passed_files:
                            passed_files.append(fpath)
                        try:
                            _fp_full = Path(working_dir) / fpath
                            if _fp_full.exists():
                                _fc = _fp_full.read_text(encoding="utf-8")
                                phase_files_written[fpath] = _fc
                                all_files_written[fpath] = _fc
                                touched_files.add(fpath)
                        except Exception:
                            pass
                    elif not any(bp == fpath for bp, _ in blocking_files):
                        blocking_files.append((fpath, ffindings))
            await _incremental_commit_push(
                f"batch {_incr_commit_count + 1}",
                _seq_since_commit,
            )
            _seq_audit_start = len(pending_file_audits)

        # 3. Collect any remaining per-file audit results not yet gathered
        _ungathered = pending_file_audits[_seq_audit_start:]
        if _ungathered:
            _log_msg = f"Collecting {len(_ungathered)} remaining audit results..."
            await build_repo.append_build_log(build_id, _log_msg, source="system", level="info")
            await _broadcast_build_event(user_id, build_id, "build_log", {
                "message": _log_msg, "source": "system", "level": "info",
            })
            await _set_build_activity(build_id, user_id, "Collecting audit results...")

            raw_results = await asyncio.gather(
                *_ungathered, return_exceptions=True,
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
        else:
            _log_msg = f"All {len(phase_files_written)} files audited for {phase_name} (incremental)"
            await build_repo.append_build_log(build_id, _log_msg, source="system", level="info")
            await _broadcast_build_event(user_id, build_id, "build_log", {
                "message": _log_msg, "source": "system", "level": "info",
            })

        # 3b. Builder drains fix queue (files auditor couldn't fix)
        if not _fix_queue.empty():
            _touch_progress(build_id)
            await _set_build_activity(build_id, user_id, "Builder fixing audit failures...", model="opus")
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
                        # Capture before content for diff
                        _before = existing.get(fix_path, "")
                        phase_files_written[fix_path] = fix_content
                        all_files_written[fix_path] = fix_content
                        touched_files.add(fix_path)

                        # Broadcast file change so Changes tab updates
                        _action = fix_entry.get("action", "modify")
                        _before_preview = "\n".join(_before.split("\n")[:150])[:5000] if _before else ""
                        _after_preview = "\n".join(fix_content.split("\n")[:150])[:5000]
                        await _broadcast_build_event(user_id, build_id, "file_created", {
                            "path": fix_path,
                            "size_bytes": len(fix_content.encode("utf-8")),
                            "language": _detect_language(fix_path),
                            "action": _action,
                            "before_snippet": _before_preview if _action == "modify" else "",
                            "after_snippet": _after_preview,
                        })
                    except Exception as exc:
                        logger.warning("Fix generation failed for %s: %s", fix_path, exc)

                # Re-commit fixes and push incrementally
                try:
                    await git_client.add_all(working_dir)
                    _fix_sha = await git_client.commit(
                        working_dir, f"forge: {phase_name} fixes (attempt {audit_attempts})",
                    )
                    if _fix_sha:
                        _incr_commit_count += 1
                        # Push fix commit immediately
                        if target_type in ("github_new", "github_existing") and access_token:
                            try:
                                await git_client.push(
                                    working_dir, branch=branch, access_token=access_token,
                                )
                            except Exception:
                                pass  # non-critical; final push will catch up
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
                        "message": f"✔ Verification clean after {verify_round} round(s)",
                        "source": "verify", "level": "info",
                    })
                break  # All good

            if verify_round >= MAX_VERIFY_ROUNDS:
                # Exhausted verification rounds — log warning but proceed
                _warn_msg = (
                    f"âš  Verification still has issues after {MAX_VERIFY_ROUNDS} rounds: "
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
                            _ok_msg = f"✔ Governance gate passed after fix round {gov_fix}"
                            await build_repo.append_build_log(
                                build_id, _ok_msg, source="governance", level="info",
                            )
                            await _broadcast_build_event(user_id, build_id, "build_log", {
                                "message": _ok_msg, "source": "governance", "level": "info",
                            })
                            break

                    if not governance_result["passed"]:
                        _fail_msg = (
                            f"âš  Governance gate still has {governance_result['blocking_failures']} "
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

        # 6. Final commit -- captures recovery fixes, verification fixes,
        #    and governance fixes since the last incremental commit.
        #    If incremental commits saved everything, this may be a no-op.
        _phase_committed = _incr_commit_count > 0
        _phase_file_list: list[dict] = []
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
                    else f"forge: {phase_name} complete (audit{audit_attempts}x, {_fix_count} auto-fixes{_gov_tag})"
                )
                sha = await git_client.commit(working_dir, commit_msg)
                if sha:
                    _phase_committed = True
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
            # Build file list for sidebar regardless of commit success
            _lang_map = {
                ".py": "python", ".ts": "typescript", ".tsx": "typescript",
                ".js": "javascript", ".jsx": "javascript", ".json": "json",
                ".css": "css", ".scss": "scss", ".html": "html", ".md": "markdown",
            }
            for _fp, _fc in phase_files_written.items():
                _ext = Path(_fp).suffix.lower()
                _phase_file_list.append({
                    "path": _fp,
                    "size_bytes": len(_fc.encode("utf-8")) if isinstance(_fc, str) else None,
                    "language": _lang_map.get(_ext),
                    "committed": _phase_committed,
                })

        # Determine final phase verdict: audit + verification + governance
        verification_clean = (
            verification.get("syntax_errors", 0) == 0
            and verification.get("tests_failed", 0) == 0
        )
        governance_clean = governance_result.get("passed", True)

        if audit_verdict == "PASS" and verification_clean and governance_clean:
            _gov_summary = f", governance {len(governance_result.get('checks', []))} checks"
            _log_msg = f"âœ… Phase {phase_name} PASS (audit + verification + governance)"
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
                "files": _phase_file_list,
                "committed": _phase_committed,
            })
            # Auto-resolve errors for this phase
            try:
                resolved = await build_repo.resolve_errors_for_phase(build_id, phase_name)
                for err in resolved:
                    await _broadcast_build_event(user_id, build_id, "build_error_resolved", {
                        "error_id": str(err["id"]),
                        "method": "phase-complete",
                        "summary": err.get("resolution_summary", ""),
                    })
            except Exception:
                pass

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
            _log_msg = f"âš  Phase {phase_name} — audit PASS, {'; '.join(_issues)}"
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
                "files": _phase_file_list,
                "committed": _phase_committed,
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
            _push_ok = False
            for _push_attempt in range(1, settings.GIT_PUSH_MAX_RETRIES + 1):
                try:
                    await _set_build_activity(build_id, user_id, f"Pushing {phase_name} to GitHub...")
                    await git_client.push(
                        working_dir, branch=branch, access_token=access_token,
                    )
                    _push_msg = f"✅ Pushed {phase_name} to GitHub"
                    await build_repo.append_build_log(
                        build_id, _push_msg,
                        source="system", level="info",
                    )
                    await _broadcast_build_event(user_id, build_id, "build_log", {
                        "message": _push_msg, "source": "system", "level": "info",
                    })
                    _push_ok = True
                    break
                except Exception as exc:
                    logger.warning(
                        "Git push attempt %d/%d failed for %s: %s",
                        _push_attempt, settings.GIT_PUSH_MAX_RETRIES, phase_name, exc,
                    )
                    _fail_msg = f"Git push attempt {_push_attempt}/{settings.GIT_PUSH_MAX_RETRIES} failed: {exc}"
                    await build_repo.append_build_log(
                        build_id, _fail_msg, source="system", level="warn",
                    )
                    await _broadcast_build_event(user_id, build_id, "build_log", {
                        "message": _fail_msg, "source": "system", "level": "warn",
                    })
                    if _push_attempt < settings.GIT_PUSH_MAX_RETRIES:
                        await asyncio.sleep(2 ** _push_attempt)

            if not _push_ok:
                _final_msg = f"⚠ Git push failed after {settings.GIT_PUSH_MAX_RETRIES} attempts — commit is local only"
                await build_repo.append_build_log(
                    build_id, _final_msg, source="system", level="error",
                )
                await _broadcast_build_event(user_id, build_id, "build_log", {
                    "message": _final_msg, "source": "system", "level": "error",
                })

        # --- Store phase outcome as artifact for next-phase context ---
        _outcome_status = "pass"
        if not (audit_verdict == "PASS" and verification_clean and governance_clean):
            _outcome_status = "partial" if audit_verdict == "PASS" else "fail"
        store_phase_outcome(
            build_id, phase,
            status=_outcome_status,
            files_written=phase_files_written,
            audit_verdict=audit_verdict,
            audit_attempts=audit_attempts,
            fixes_applied=verification.get("fixes_applied", 0),
            verification=verification,
            governance=governance_result,
        )

        # --- External phase audit (background, non-blocking) ---
        asyncio.create_task(
            _run_phase_audit(
                build_id=build_id,
                project_id=project_id,
                phase_number=phase_num,
                phase_files=list(phase_files_written.keys()),
                contracts=contracts,
            )
        )

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
                f"🔧„ {phase_name} complete — transitioning to "
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
            _trans_msg = f"ðŸ {phase_name} complete — all phases finished"
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

    # ── Persist forge seal ──────────────────────────────────────────
    try:
        from app.services.certificate_aggregator import aggregate_certificate_data
        from app.services.certificate_scorer import compute_certificate_scores
        from app.services.certificate_renderer import render_certificate
        from app.repos import certificate_repo
        cert_data = await aggregate_certificate_data(project_id, user_id)
        scores    = compute_certificate_scores(cert_data)
        html      = render_certificate(scores, "html")
        await certificate_repo.create_certificate(
            project_id=project_id, build_id=build_id,
            user_id=user_id, scores=scores, certificate_html=html,
        )
        logger.info("Forge seal persisted for build %s", build_id)
    except Exception as _exc:
        logger.warning("Forge seal persistence failed (non-fatal): %s", _exc)


async def _run_phase_audit(
    build_id: UUID,
    project_id: UUID,
    phase_number: int,
    phase_files: list[str],
    contracts: list[dict],
) -> None:
    """Fire-and-forget background phase audit using the standalone auditor module.

    Runs after each phase completes. Failures are logged but never propagate.
    """
    _auditor_dir = Path(__file__).resolve().parent.parent.parent / "auditor"
    if not _auditor_dir.exists():
        logger.debug("[PHASE_AUDIT] Auditor directory not found, skipping: %s", _auditor_dir)
        return

    import sys as _sys
    if str(_auditor_dir) not in _sys.path:
        _sys.path.insert(0, str(_auditor_dir))

    try:
        from auditor_agent import run_auditor  # type: ignore[import]

        # Build a simple contract fetcher from the contracts list
        _index = {c["contract_type"]: c.get("content", "") for c in (contracts or [])}

        def _fetcher(contract_type: str):
            return _index.get(contract_type)

        result = await run_auditor(
            mode="phase",
            build_id=build_id,
            project_id=project_id,
            phase_number=phase_number,
            phase_files=phase_files,
            contract_fetcher=_fetcher,
            verbose=False,
        )
        status_str = result.status if hasattr(result, "status") else "unknown"
        issue_count = len(result.issues) if hasattr(result, "issues") else 0
        logger.info("[PHASE_AUDIT] Phase %d: %s (%d issues)", phase_number, status_str, issue_count)
    except Exception as exc:
        logger.warning("[PHASE_AUDIT] Non-blocking failure (phase %d): %s", phase_number, exc)
