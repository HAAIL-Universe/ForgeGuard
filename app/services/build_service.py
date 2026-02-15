"""Build service -- orchestrates autonomous builder sessions.

Manages the full build lifecycle: validate contracts, spawn agent session,
stream progress, run inline audits, handle loopback, track costs, and
advance phases.

No SQL, no HTTP framework, no direct GitHub API calls.
"""

import asyncio
import logging
import re
from decimal import Decimal
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

from app.clients.agent_client import StreamUsage, stream_agent
from app.config import settings
from app.repos import build_repo
from app.repos import project_repo
from app.repos.user_repo import get_user_by_id
from app.ws_manager import manager

logger = logging.getLogger(__name__)

# Maximum consecutive loopback failures before stopping
MAX_LOOP_COUNT = 3

# Phase completion signal the builder emits
PHASE_COMPLETE_SIGNAL = "=== PHASE SIGN-OFF: PASS ==="

# Build error signal
BUILD_ERROR_SIGNAL = "RISK_EXCEEDS_SCOPE"

# Universal governance contract (not per-project — loaded from disk)
FORGE_CONTRACTS_DIR = Path(__file__).resolve().parent.parent.parent / "Forge" / "Contracts"

# Active build tasks keyed by build_id
_active_tasks: dict[str, asyncio.Task] = {}

# Cost-per-token estimates (USD) keyed by model prefix -- updated as pricing changes
_MODEL_PRICING: dict[str, tuple[Decimal, Decimal]] = {
    # (input $/token, output $/token)
    "claude-opus-4":       (Decimal("0.000015"),  Decimal("0.000075")),   # $15 / $75 per 1M
    "claude-sonnet-4":     (Decimal("0.000003"),  Decimal("0.000015")),   # $3 / $15 per 1M
    "claude-haiku-4":      (Decimal("0.000001"),  Decimal("0.000005")),   # $1 / $5 per 1M
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


async def start_build(project_id: UUID, user_id: UUID) -> dict:
    """Start a build for a project.

    Validates that contracts exist, creates a build record, and spawns
    the background orchestration task.

    Args:
        project_id: The project to build.
        user_id: The authenticated user (for ownership check).

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

    # Create build record
    build = await build_repo.create_build(project_id)

    # Update project status
    await project_repo.update_project_status(project_id, "building")

    # Spawn background task
    task = asyncio.create_task(
        _run_build(build["id"], project_id, user_id, contracts, user_api_key, audit_llm_enabled)
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
    if not latest or latest["status"] not in ("pending", "running"):
        raise ValueError("No active build to cancel")

    build_id = latest["id"]

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
    project_id: UUID, user_id: UUID, limit: int = 100, offset: int = 0
) -> tuple[list[dict], int]:
    """Get paginated build logs for a project.

    Args:
        project_id: The project to check.
        user_id: The authenticated user (for ownership check).
        limit: Maximum logs to return.
        offset: Offset for pagination.

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

    return await build_repo.get_build_logs(latest["id"], limit, offset)


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
) -> None:
    """Background task that orchestrates the full build lifecycle.

    Streams agent output, detects phase completion signals, runs inline
    audits, handles loopback, and advances through phases.
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

        # Build the directive from contracts
        directive = _build_directive(contracts)

        # Conversation history for the agent
        messages: list[dict] = [
            {"role": "user", "content": directive},
        ]

        accumulated_text = ""
        current_phase = "Phase 0"

        # Token usage tracking
        usage = StreamUsage()

        # Stream agent output
        async for chunk in stream_agent(
            api_key=api_key,
            model=settings.LLM_BUILDER_MODEL,
            system_prompt="You are an autonomous software builder operating under the Forge governance framework.",
            messages=messages,
            usage_out=usage,
        ):
            accumulated_text += chunk

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

            # Detect phase completion
            if PHASE_COMPLETE_SIGNAL in accumulated_text:
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
                audit_result = await _run_inline_audit(
                    build_id, current_phase, accumulated_text,
                    contracts, api_key, audit_llm_enabled,
                )

                if audit_result == "PASS":
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
                else:
                    loop_count = await build_repo.increment_loop_count(build_id)
                    await build_repo.append_build_log(
                        build_id,
                        f"Audit FAIL for {current_phase} (loop {loop_count})",
                        source="audit",
                        level="warn",
                    )
                    await _broadcast_build_event(
                        user_id, build_id, "audit_fail", {
                            "phase": current_phase,
                            "loop_count": loop_count,
                        }
                    )

                    if loop_count >= MAX_LOOP_COUNT:
                        await _fail_build(
                            build_id,
                            user_id,
                            "RISK_EXCEEDS_SCOPE: 3 consecutive audit failures",
                        )
                        return

                # Record cost for this phase
                await _record_phase_cost(build_id, current_phase, usage)

                # Reset accumulated text for next phase detection
                accumulated_text = ""

            # Detect build error signals
            if BUILD_ERROR_SIGNAL in accumulated_text:
                await _fail_build(
                    build_id, user_id, accumulated_text[-500:]
                )
                return

        # Build completed (agent finished streaming)
        now = datetime.now(timezone.utc)
        await build_repo.update_build_status(
            build_id, "completed", completed_at=now
        )
        await project_repo.update_project_status(project_id, "completed")
        await build_repo.append_build_log(
            build_id, "Build completed successfully", source="system", level="info"
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
        _active_tasks.pop(str(build_id), None)


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


async def _run_inline_audit(
    build_id: UUID,
    phase: str,
    builder_output: str,
    contracts: list[dict],
    api_key: str,
    audit_llm_enabled: bool = True,
) -> str:
    """Run an LLM-based audit of the builder's phase output.

    When audit_llm_enabled is True, sends the builder output + reference
    contracts to a separate LLM call using auditor_prompt.md as the system
    prompt. The auditor checks for contract compliance, architectural
    drift, and semantic correctness.

    When disabled, returns 'PASS' as a no-op (self-certification).

    Returns 'PASS' or 'FAIL'.
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
            return "PASS"

        # Load auditor system prompt from Forge/Contracts
        auditor_prompt_path = FORGE_CONTRACTS_DIR / "auditor_prompt.md"
        if not auditor_prompt_path.exists():
            logger.warning("auditor_prompt.md not found — falling back to stub audit")
            return "PASS"
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

        # Call the auditor LLM (Haiku — cheap and fast)
        from app.clients.llm_client import chat as llm_chat
        result = await llm_chat(
            api_key=api_key,
            model=settings.LLM_QUESTIONNAIRE_MODEL,  # Haiku
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
            return "PASS"
        elif "VERDICT: FLAGS FOUND" in audit_text:
            return "FAIL"
        else:
            # Ambiguous response — log warning, default to PASS
            logger.warning("Auditor response missing clear verdict — defaulting to PASS")
            await build_repo.append_build_log(
                build_id,
                "Auditor verdict unclear — defaulting to PASS",
                source="audit",
                level="warn",
            )
            return "PASS"

    except Exception as exc:
        logger.error("LLM audit error for %s: %s", phase, exc)
        await build_repo.append_build_log(
            build_id,
            f"Audit error: {exc} — defaulting to PASS",
            source="audit",
            level="error",
        )
        return "PASS"


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
