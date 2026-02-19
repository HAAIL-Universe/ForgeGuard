"""Generate project contracts inferred from a Scout deep-scan run.

Unlike the questionnaire-based generator, this module INFERS all nine
contract types from analysed codebase data:
  - Detected tech stack (stack_profile)
  - Mapped architecture
  - LLM dossier (executive summary, risks, recommendations)
  - Compliance check results
  - Renovation plan (if already generated)

Contracts are persisted to the DB (via upsert_contract) AND stored in the
MCP artifact store for token-efficient retrieval by sub-agents.

Sends the same ``contract_progress`` WebSocket events as the questionnaire-
based generator so the frontend can reuse the same progress UI.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Awaitable, Callable
from uuid import UUID

from app.clients.llm_client import chat_streaming as llm_chat_streaming
from app.config import settings
from app.repos.project_repo import (
    get_contracts_by_project,
    get_project_by_id,
    snapshot_contracts,
    update_project_status,
    upsert_contract,
)
from app.repos.scout_repo import get_scout_run
from app.ws_manager import manager
from forge_ide.mcp.artifact_store import store_artifact

from .contract_generator import (
    CONTRACT_TYPES,
    ContractCancelled,
    _CONTRACT_INSTRUCTIONS,
    _active_generations,
    _load_generic_template,
)
from .contract_utils import (
    CONTEXT_TOOLS_SCOUT,
    build_tool_use_system_prompt,
    execute_scout_tool,
    generate_contract_with_tools,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Context formatter — scout results → prompt-ready text
# ---------------------------------------------------------------------------


def _format_scout_context_for_prompt(
    repo_name: str,
    scout_results: dict,
    renovation_plan: dict | None,
) -> str:
    """Convert Scout deep-scan results into a prompt-ready context block."""
    lines: list[str] = []

    metadata = scout_results.get("metadata") or {}
    stack = scout_results.get("stack_profile") or {}
    arch = scout_results.get("architecture") or {}
    dossier = scout_results.get("dossier") or {}
    metrics = scout_results.get("metrics") or {}
    checks = scout_results.get("checks") or []
    warnings_list = scout_results.get("warnings") or []

    # ── Repository header ──────────────────────────────────────
    lines.append(f"Repository: {repo_name}")
    desc = metadata.get("description") or ""
    if desc:
        lines.append(f"Description: {desc}")
    lang = metadata.get("language") or stack.get("primary_language") or ""
    if lang:
        lines.append(f"Primary Language: {lang}")
    tree_size = scout_results.get("tree_size")
    if tree_size:
        lines.append(f"Files in tree: {tree_size}")
    lines.append("")

    # ── Tech stack ─────────────────────────────────────────────
    if stack:
        lines.append("--- DETECTED TECH STACK ---")
        for section in ("backend", "frontend", "testing", "infrastructure"):
            sect_data = stack.get(section) or {}
            if sect_data:
                lines.append(f"{section.capitalize()}:")
                for k, v in sect_data.items():
                    if v:
                        lines.append(f"  {k}: {v}")
        lines.append("")

    # ── Architecture map ────────────────────────────────────────
    if arch:
        lines.append("--- ARCHITECTURE MAP ---")
        arch_json = json.dumps(arch, indent=2)
        lines.append(arch_json[:3_000])
        if len(arch_json) > 3_000:
            lines.append("[... truncated for brevity ...]")
        lines.append("")

    # ── LLM dossier ─────────────────────────────────────────────
    if dossier:
        lines.append("--- PROJECT DOSSIER (AI ANALYSIS) ---")
        exec_sum = dossier.get("executive_summary") or ""
        if exec_sum:
            lines.append(f"Executive Summary: {exec_sum}")
        intent = dossier.get("intent") or ""
        if intent:
            lines.append(f"Intent: {intent}")

        qa = dossier.get("quality_assessment") or {}
        score = qa.get("score") or metrics.get("computed_score") or 0
        lines.append(f"Quality Score: {score}/100")

        for label, key in (("Strengths", "strengths"), ("Weaknesses", "weaknesses")):
            items = qa.get(key) or []
            if items:
                lines.append(f"{label}:")
                for item in items[:5]:
                    lines.append(f"  • {item}")

        risks = dossier.get("risk_areas") or []
        if risks:
            lines.append("Risk Areas:")
            for r in risks[:5]:
                sev = str(r.get("severity", "?")).upper()
                lines.append(
                    f"  [{sev}] {r.get('area')}: {str(r.get('detail', ''))[:120]}"
                )

        recs = dossier.get("recommendations") or []
        if recs:
            lines.append("Recommendations:")
            for r in recs[:5]:
                pri = str(r.get("priority", "?")).upper()
                lines.append(f"  [{pri}] {str(r.get('suggestion', ''))[:120]}")
        lines.append("")

    # ── Compliance checks ───────────────────────────────────────
    all_checks = list(checks) + list(warnings_list)
    if all_checks:
        lines.append("--- COMPLIANCE CHECKS ---")
        for c in all_checks[:20]:
            code = c.get("code", "?")
            name = c.get("name", "?")
            result = c.get("result", "?")
            detail = str(c.get("detail") or "")[:100]
            entry = f"  {code} ({name}): {result}"
            if detail:
                entry += f" — {detail}"
            lines.append(entry)
        lines.append("")

    # ── Quality metrics ─────────────────────────────────────────
    computed_score = metrics.get("computed_score")
    if computed_score is not None:
        lines.append("--- QUALITY METRICS ---")
        lines.append(f"  Overall Score: {computed_score}/100")
        for k, v in metrics.items():
            if k != "computed_score" and isinstance(v, (int, float, str, bool)):
                lines.append(f"  {k}: {v}")
        lines.append("")

    # ── Renovation plan ─────────────────────────────────────────
    if renovation_plan:
        lines.append("--- RENOVATION PLAN ---")
        eb = renovation_plan.get("executive_brief") or {}
        if eb:
            lines.append(f"Health Grade: {eb.get('health_grade', '?')}")
            lines.append(f"Headline: {eb.get('headline', '')}")
            risk_sum = eb.get("risk_summary") or ""
            if risk_sum:
                lines.append(f"Risk: {risk_sum}")
            auto_note = eb.get("forge_automation_note") or ""
            if auto_note:
                lines.append(f"Automation Note: {auto_note}")
            prios = eb.get("top_priorities") or []
            if prios:
                lines.append("Top Priorities:")
                for p in prios:
                    lines.append(f"  • {p}")

        vr = renovation_plan.get("version_report") or {}
        outdated = vr.get("outdated") or []
        if outdated:
            lines.append("Outdated Dependencies:")
            for d in outdated[:10]:
                lines.append(
                    f"  {d.get('name')}: "
                    f"{d.get('current_version', '?')} → {d.get('latest_version', '?')}"
                )

        migrations = renovation_plan.get("migration_recommendations") or []
        if migrations:
            lines.append("Migration Tasks:")
            for m in migrations[:8]:
                auto = "automatable" if m.get("forge_automatable") else "manual"
                lines.append(
                    f"  [{str(m.get('priority', '?')).upper()}] "
                    f"{m.get('from_state', '?')} → {m.get('to_state', '?')} "
                    f"(effort: {m.get('effort', '?')}, {auto})"
                )
        lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# LLM generation — scout-mode variant
# ---------------------------------------------------------------------------


async def _generate_scout_contract_content(
    contract_type: str,
    repo_name: str,
    scout_context: str,
    api_key: str,
    model: str,
    provider: str,
    prior_contracts: dict[str, str] | None = None,
    on_token_progress: Callable[[int, int], Awaitable[None]] | None = None,
) -> tuple[str, dict]:
    """Generate a single contract from Scout deep-scan context.

    Uses the same per-contract instructions and structural examples as the
    questionnaire generator, but substitutes scout analysis for questionnaire
    answers as the source of truth.

    Returns (content, usage) where usage has input_tokens / output_tokens.
    """
    example = _load_generic_template(contract_type)
    instructions = _CONTRACT_INSTRUCTIONS.get(
        contract_type, f"Generate a {contract_type} contract document."
    )

    system_parts = [
        f"You are a Forge contract generator. You produce detailed, production-quality "
        f"project specification documents for the Forge autonomous build system.\n\n"
        f"You are generating the **{contract_type}** contract for the repository "
        f'"{repo_name}".\n\n'
        f"IMPORTANT — EXISTING CODEBASE MODE:\n"
        f"This is an EXISTING codebase analysed by Scout. Your contract must:\n"
        f"- Reflect the ACTUAL detected technology stack (do not invent technologies)\n"
        f"- Describe the project as it SHOULD be after remediation\n"
        f"- For builder_directive: instruct the AI builder to UPDATE/FIX existing code,\n"
        f"  not to build from scratch\n\n"
        f"INSTRUCTIONS:\n{instructions}\n\n"
        f"RULES:\n"
        f"- Output ONLY the contract content. No preamble, no 'Here is...', no explanations.\n"
        f"- Be thorough and detailed. Use the actual detected stack and architecture.\n"
        f"- Do NOT leave any section empty or with placeholder text.\n"
        f"- Match the structural depth and detail level of the reference example.\n"
    ]

    if example:
        system_parts.append(
            f"\n--- STRUCTURAL REFERENCE (match this level of detail and format) ---\n"
            f"{example}\n"
            f"--- END REFERENCE ---\n"
        )

    system_prompt = "\n".join(system_parts)

    # User message: scout context + snowball-chained prior contracts
    user_parts = [
        f"Generate the {contract_type} contract for this repository.\n\n"
        f"--- SCOUT DEEP-SCAN CONTEXT ---\n"
        f"{scout_context}\n"
        f"--- END SCOUT CONTEXT ---"
    ]

    # Snowball chain: each contract sees all previously generated ones
    if prior_contracts:
        total_budget = 24_000
        per_cap = 12_000
        chain_parts: list[str] = []
        used = 0
        for dep_type in CONTRACT_TYPES:
            if dep_type == contract_type:
                break
            dep_content = prior_contracts.get(dep_type)
            if not dep_content:
                continue
            snippet = dep_content[:per_cap]
            if len(dep_content) > per_cap:
                snippet += "\n[... truncated ...]"
            part = (
                f"--- PREVIOUSLY GENERATED: {dep_type} ---\n"
                f"{snippet}\n"
                f"--- END {dep_type} ---"
            )
            if used + len(part) > total_budget:
                break
            chain_parts.append(part)
            used += len(part)
        if chain_parts:
            user_parts.append(
                "\n\nUse these previously-generated contracts for consistency:\n"
                + "\n\n".join(chain_parts)
            )

    user_msg = "\n".join(user_parts)

    try:
        result = await llm_chat_streaming(
            api_key=api_key,
            model=model,
            system_prompt=system_prompt,
            messages=[{"role": "user", "content": user_msg}],
            provider=provider,
            max_tokens=16384,
            on_progress=on_token_progress,
        )
        usage = result.get("usage", {"input_tokens": 0, "output_tokens": 0})
        content = result["text"].strip()

        # Strip markdown code fences if the LLM wrapped the output
        if content.startswith("```"):
            lines = content.split("\n")
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            content = "\n".join(lines).strip()

        return content, usage

    except Exception as exc:
        logger.exception(
            "LLM scout-contract generation failed for %s: %s", contract_type, exc
        )
        return (
            f"# {repo_name} — {contract_type}\n\n"
            f"**Generation failed:** {exc}\n\n"
            f"Please regenerate this contract or edit it manually."
        ), {"input_tokens": 0, "output_tokens": 0}


# ---------------------------------------------------------------------------
# Tool-use generation — replaces snowball text-injection
# ---------------------------------------------------------------------------


def _extract_context_data(raw_results: dict) -> dict:
    """Extract structured context data from scout results for tool dispatch.

    Returns a flat dict whose keys correspond to tool names in
    ``CONTEXT_TOOLS_SCOUT``.  Each value is the raw data structure that will
    be JSON-serialised when the LLM invokes the corresponding tool.
    """
    renovation_plan = raw_results.get("renovation_plan") or {}
    return {
        "stack_profile": raw_results.get("stack_profile") or {},
        "executive_brief": renovation_plan.get("executive_brief") or {},
        "migration_tasks": renovation_plan.get("migration_recommendations") or [],
        "dossier": raw_results.get("dossier") or {},
        "checks": (raw_results.get("checks") or [])
        + (raw_results.get("warnings") or []),
        "architecture": raw_results.get("architecture") or {},
    }


async def _generate_scout_contract_with_tools(
    contract_type: str,
    repo_name: str,
    context_data: dict,
    api_key: str,
    model: str,
    provider: str,
    prior_contracts: dict[str, str],
) -> tuple[str, dict]:
    """Generate a single scout contract using the multi-turn tool-use loop.

    The LLM decides which context tools to call (stack, dossier, renovation
    plan, etc.), fetches exactly what it needs, then calls
    ``submit_contract(content)`` to produce the final contract.

    Returns (content, usage) matching the signature of the old
    ``_generate_scout_contract_content``.
    """
    system_prompt = build_tool_use_system_prompt(
        contract_type,
        repo_name=repo_name,
        mode="scout",
    )

    def _executor(name: str, tool_input: dict) -> str:
        return execute_scout_tool(name, tool_input, context_data, prior_contracts)

    return await generate_contract_with_tools(
        contract_type=contract_type,
        system_prompt=system_prompt,
        tools=CONTEXT_TOOLS_SCOUT,
        tool_executor=_executor,
        api_key=api_key,
        model=model,
        provider=provider,
        repo_name=repo_name,
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def generate_contracts_from_scout(
    user_id: UUID,
    project_id: UUID,
    scout_run_id: UUID,
) -> list[dict]:
    """Generate all 9 project contracts inferred from a Scout deep-scan run.

    Contracts are persisted to the DB and stored in the MCP artifact store.
    Sends ``contract_progress`` WebSocket events (same format as the
    questionnaire-based generator) with ``source: "scout"`` added to the
    payload so the frontend can distinguish the source.

    Parameters
    ----------
    user_id      : Authenticated user — ownership validated against both project
                   and scout run.
    project_id   : Target project to attach contracts to.
    scout_run_id : Completed deep-scan run to infer contracts from.

    Returns
    -------
    list[dict] — generated contract records.

    Raises
    ------
    ValueError  — project/run not found, ownership mismatch, wrong scan type,
                  run not completed, or duplicate generation already in progress.
    """
    # ── Validate project ────────────────────────────────────────
    project = await get_project_by_id(project_id)
    if not project:
        raise ValueError("Project not found")
    if str(project["user_id"]) != str(user_id):
        raise ValueError("Project not found")

    # ── Validate scout run ──────────────────────────────────────
    run = await get_scout_run(scout_run_id)
    if run is None:
        raise ValueError("Scout run not found")
    if str(run["user_id"]) != str(user_id):
        raise ValueError("Scout run not found")
    if run.get("scan_type") != "deep":
        raise ValueError(
            "Scout run must be a deep-scan — quick-scans do not have the "
            "architecture or stack data needed for contract inference."
        )
    if run.get("status") != "completed":
        raise ValueError(
            f"Scout run is not completed (status: {run.get('status', 'unknown')}). "
            "Wait for the scan to finish before generating contracts."
        )

    # ── Extract results ──────────────────────────────────────────
    raw_results = run.get("results") or {}
    if isinstance(raw_results, str):
        try:
            raw_results = json.loads(raw_results)
        except json.JSONDecodeError:
            raw_results = {}

    scout_results: dict = raw_results
    renovation_plan: dict | None = raw_results.get("renovation_plan")
    repo_name: str = run.get("repo_name") or str(scout_run_id)

    # ── I1: Renovation plan is mandatory for contract generation ─
    if not renovation_plan:
        raise ValueError(
            "Scout run does not contain a renovation plan. "
            "The renovation plan is required for contract generation — "
            "it provides the health grade, priorities, and migration tasks "
            "that drive every contract. Re-run the deep-scan to generate one."
        )

    # ── Extract structured context for tool-use dispatch ─────────
    context_data = _extract_context_data(raw_results)

    # ── LLM config ───────────────────────────────────────────────
    provider = (settings.LLM_PROVIDER or "").strip().lower()
    if not provider:
        provider = "anthropic" if settings.ANTHROPIC_API_KEY else "openai"
    if provider == "openai":
        llm_api_key = settings.OPENAI_API_KEY
        llm_model = settings.OPENAI_MODEL
    else:
        llm_api_key = settings.ANTHROPIC_API_KEY
        llm_model = settings.LLM_QUESTIONNAIRE_MODEL

    pid = str(project_id)

    # ── Duplicate-run guard (shared with questionnaire generator) ─
    if pid in _active_generations:
        raise ValueError(
            "Contract generation is already in progress for this project. "
            "Wait for it to finish or cancel it first."
        )

    cancel_event = asyncio.Event()
    _active_generations[pid] = cancel_event

    # ── Resume: check for partially-generated contracts ──────────
    existing = await get_contracts_by_project(project_id)
    existing_map: dict[str, dict] = {c["contract_type"]: c for c in existing}

    if all(ct in existing_map for ct in CONTRACT_TYPES):
        batch = await snapshot_contracts(project_id)
        if batch:
            logger.info(
                "Archived contracts as snapshot batch %d for project %s", batch, pid
            )
        existing_map = {}  # Regenerate from scratch

    # ── (Context is now fetched on-demand via tool-use loop) ────

    generated: list[dict] = []
    prior_contracts: dict[str, str] = {
        ct: existing_map[ct]["content"]
        for ct in CONTRACT_TYPES
        if ct in existing_map
    }
    total = len(CONTRACT_TYPES)

    try:
        for idx, contract_type in enumerate(CONTRACT_TYPES):
            # Cancellation check
            if cancel_event.is_set():
                logger.info(
                    "Scout contract generation cancelled for project %s", pid
                )
                await manager.send_to_user(str(user_id), {
                    "type": "contract_progress",
                    "payload": {
                        "project_id": pid,
                        "contract_type": contract_type,
                        "status": "cancelled",
                        "index": idx,
                        "total": total,
                        "source": "scout",
                    },
                })
                raise ContractCancelled("Contract generation cancelled")

            # Resume: skip contracts that already exist
            if contract_type in existing_map:
                row = existing_map[contract_type]
                generated.append({
                    "id": str(row["id"]),
                    "project_id": str(row["project_id"]),
                    "contract_type": row["contract_type"],
                    "version": row["version"],
                    "created_at": row["created_at"],
                    "updated_at": row["updated_at"],
                })
                await manager.send_to_user(str(user_id), {
                    "type": "contract_progress",
                    "payload": {
                        "project_id": pid,
                        "contract_type": contract_type,
                        "status": "done",
                        "index": idx,
                        "total": total,
                        "input_tokens": 0,
                        "output_tokens": 0,
                        "resumed": True,
                        "source": "scout",
                    },
                })
                continue

            # Notify frontend: generation starting
            await manager.send_to_user(str(user_id), {
                "type": "contract_progress",
                "payload": {
                    "project_id": pid,
                    "contract_type": contract_type,
                    "status": "generating",
                    "index": idx,
                    "total": total,
                    "source": "scout",
                },
            })

            # ── Tool-use generation (replaces snowball text-injection) ─
            llm_task = asyncio.ensure_future(
                _generate_scout_contract_with_tools(
                    contract_type=contract_type,
                    repo_name=repo_name,
                    context_data=context_data,
                    api_key=llm_api_key,
                    model=llm_model,
                    provider=provider,
                    prior_contracts=prior_contracts,
                )
            )
            cancel_task = asyncio.ensure_future(cancel_event.wait())

            done, _ = await asyncio.wait(
                [llm_task, cancel_task],
                return_when=asyncio.FIRST_COMPLETED,
            )

            if cancel_task in done:
                llm_task.cancel()
                logger.info(
                    "Scout contract generation cancelled mid-LLM for project %s", pid
                )
                await manager.send_to_user(str(user_id), {
                    "type": "contract_progress",
                    "payload": {
                        "project_id": pid,
                        "contract_type": contract_type,
                        "status": "cancelled",
                        "index": idx,
                        "total": total,
                        "source": "scout",
                    },
                })
                raise ContractCancelled("Contract generation cancelled")

            cancel_task.cancel()
            content, usage = llm_task.result()

            # Accumulate for tool-use get_prior_contract lookups
            prior_contracts[contract_type] = content

            # Persist to DB
            row = await upsert_contract(project_id, contract_type, content)

            # Persist to MCP artifact store for sub-agent lazy-loading
            store_artifact(
                project_id=pid,
                artifact_type="contract",
                key=contract_type,
                content=content,
                persist=True,
            )

            generated.append({
                "id": str(row["id"]),
                "project_id": str(row["project_id"]),
                "contract_type": row["contract_type"],
                "version": row["version"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            })

            await manager.send_to_user(str(user_id), {
                "type": "contract_progress",
                "payload": {
                    "project_id": pid,
                    "contract_type": contract_type,
                    "status": "done",
                    "index": idx,
                    "total": total,
                    "input_tokens": usage.get("input_tokens", 0),
                    "output_tokens": usage.get("output_tokens", 0),
                    "source": "scout",
                },
            })

    finally:
        _active_generations.pop(pid, None)

    await update_project_status(project_id, "contracts_ready")
    return generated
