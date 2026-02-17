"""Planner — phase planning, LLM plan generation, plan parsing, file manifest."""

import asyncio
import json
import logging
import os
import re
from decimal import Decimal
from pathlib import Path
from uuid import UUID

from . import _state
from ._state import (
    FORGE_CONTRACTS_DIR,
    PLAN_START_PATTERN,
    PLAN_END_PATTERN,
    logger,
)
from .cost import _accumulate_cost, _get_token_rates

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_MAX_PROJECT_STATE_BYTES = 200_000
_MAX_SINGLE_FILE_BYTES = 10_000
_CODE_EXTENSIONS = frozenset({
    ".py", ".ts", ".tsx", ".js", ".jsx", ".sql", ".json", ".yaml", ".yml",
    ".toml", ".cfg", ".md", ".html", ".css",
})

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


# ---------------------------------------------------------------------------
# Plan parsing
# ---------------------------------------------------------------------------


def _parse_build_plan(text: str) -> list[dict]:
    """Parse a structured build plan from the builder output.

    Expected format:
        === PLAN ===
        1. Task description one
        2. Task description two
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


def _parse_phases_contract(content: str) -> list[dict]:
    """Parse a phases contract markdown into structured phase definitions."""
    phases: list[dict] = []
    phase_blocks = re.split(r"(?=^## Phase )", content, flags=re.MULTILINE)

    for block in phase_blocks:
        header = re.match(
            r"^## Phase\s+(\d+)\s*[-—–]+\s*(.+?)\s*$", block, re.MULTILINE
        )
        if not header:
            continue

        phase_num = int(header.group(1))
        phase_name = header.group(2).strip()

        obj_match = re.search(
            r"\*\*Objective:\*\*\s*(.+?)(?=\n\n|\n\*\*|$)", block, re.DOTALL
        )
        objective = obj_match.group(1).strip() if obj_match else ""

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
# Project state gathering
# ---------------------------------------------------------------------------


def _gather_project_state(working_dir: str | None) -> str:
    """Walk the working directory and produce a file tree + key file contents."""
    if not working_dir or not Path(working_dir).is_dir():
        return "(working directory not available)"

    root = Path(working_dir)
    tree_lines: list[str] = []
    file_contents: list[str] = []
    total_bytes = 0

    skip_dirs = {".git", "__pycache__", "node_modules", ".venv", "venv", ".tox", "dist", "build"}

    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in sorted(dirnames) if d not in skip_dirs]
        rel_dir = Path(dirpath).relative_to(root)

        for fname in sorted(filenames):
            rel_path = rel_dir / fname
            tree_lines.append(str(rel_path))

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

            if len(raw) > _MAX_SINGLE_FILE_BYTES:
                half = _MAX_SINGLE_FILE_BYTES // 5
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


# ---------------------------------------------------------------------------
# Recovery planner
# ---------------------------------------------------------------------------


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
    """Invoke the recovery planner to analyse an audit failure."""
    from app.clients.llm_client import chat as llm_chat

    prompt_path = FORGE_CONTRACTS_DIR / "recovery_planner_prompt.md"
    if not prompt_path.exists():
        logger.warning("recovery_planner_prompt.md not found — skipping recovery planner")
        return ""
    system_prompt = prompt_path.read_text(encoding="utf-8")

    reference_types = {
        "blueprint", "manifesto", "stack", "schema",
        "physics", "boundaries", "phases", "ui",
    }
    reference_parts = ["# Reference Contracts\n"]
    for c in contracts:
        if c["contract_type"] in reference_types:
            reference_parts.append(f"\n---\n## {c['contract_type']}\n{c['content']}\n")
    reference_text = "\n".join(reference_parts)

    project_state = _gather_project_state(working_dir)

    max_builder_chars = 30_000
    trimmed_builder = builder_output
    if len(builder_output) > max_builder_chars:
        trimmed_builder = (
            f"[... truncated {len(builder_output) - max_builder_chars} chars ...]\n"
            + builder_output[-max_builder_chars:]
        )

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

    await _state.build_repo.append_build_log(
        build_id,
        f"Invoking recovery planner for {phase}",
        source="planner",
        level="info",
    )

    result = await llm_chat(
        api_key=api_key,
        model=_state.settings.LLM_PLANNER_MODEL,
        system_prompt=system_prompt,
        messages=[{"role": "user", "content": user_message}],
        max_tokens=4096,
        provider="anthropic",
    )

    planner_text = result["text"] if isinstance(result, dict) else result
    planner_usage = result.get("usage", {}) if isinstance(result, dict) else {}

    await _state.build_repo.append_build_log(
        build_id,
        f"Recovery planner response ({planner_usage.get('input_tokens', 0)} in / "
        f"{planner_usage.get('output_tokens', 0)} out):\n{planner_text}",
        source="planner",
        level="info",
    )

    input_t = planner_usage.get("input_tokens", 0)
    output_t = planner_usage.get("output_tokens", 0)
    model = _state.settings.LLM_PLANNER_MODEL
    input_rate, output_rate = _get_token_rates(model)
    cost = Decimal(input_t) * input_rate + Decimal(output_t) * output_rate
    await _state.build_repo.record_build_cost(
        build_id, f"{phase} (planner)", input_t, output_t, model, cost,
    )
    await _accumulate_cost(build_id, input_t, output_t, model, cost)

    await _state._broadcast_build_event(
        user_id, build_id, "recovery_plan", {
            "phase": phase,
            "plan_text": planner_text,
        },
    )

    return planner_text


# ---------------------------------------------------------------------------
# Contract selection & context budget
# ---------------------------------------------------------------------------


def _select_contracts_for_file(
    file_path: str,
    contracts: list[dict],
) -> list[dict]:
    """Select the subset of contracts relevant to a given file type."""
    relevant_types: set[str] = set()

    for prefix, types in _CONTRACT_RELEVANCE.items():
        if file_path.startswith(prefix):
            relevant_types.update(types)
            break

    if not relevant_types:
        relevant_types = {"blueprint", "stack"}

    if file_path.startswith("tests/"):
        relevant_types.update(["blueprint", "schema", "stack", "boundaries"])

    if file_path.endswith(".sql"):
        relevant_types = {"schema"}

    return [c for c in contracts if c["contract_type"] in relevant_types]


def _calculate_context_budget(
    file_entry: dict,
    system_prompt_tokens: int,
    contract_tokens: int,
    available_context_files: dict[str, str],
) -> dict:
    """Calculate how much context to send per file generation call."""
    MODEL_CONTEXT_WINDOW = 200_000
    SAFETY_MARGIN = 5_000

    estimated_lines = file_entry.get("estimated_lines", 100)
    max_tokens = max(4096, min(estimated_lines * 15, 16384))

    available_input = (
        MODEL_CONTEXT_WINDOW - max_tokens - SAFETY_MARGIN
        - system_prompt_tokens - contract_tokens
    )

    files_to_include: list[str] = []
    truncated: list[str] = []
    total_context_tokens = 0

    context_paths = file_entry.get("context_files", [])
    depends_on = file_entry.get("depends_on", [])
    ordered = list(depends_on) + [p for p in context_paths if p not in depends_on]

    for path in ordered:
        if path not in available_context_files:
            continue
        content = available_context_files[path]
        estimated_tokens = len(content) // 4
        if total_context_tokens + estimated_tokens > available_input:
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
    """Sort manifest files by dependency order (topological sort)."""
    path_to_entry = {f["path"]: f for f in files}
    visited: set[str] = set()
    temp_mark: set[str] = set()
    result: list[dict] = []

    def visit(path: str) -> bool:
        if path in temp_mark:
            return False
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
                logger.warning("Circular dependency in manifest — using linear order")
                return list(files)

    return result


# ---------------------------------------------------------------------------
# File manifest generation
# ---------------------------------------------------------------------------


async def _generate_file_manifest(
    build_id: UUID,
    user_id: UUID,
    api_key: str,
    contracts: list[dict],
    current_phase: dict,
    workspace_info: str,
) -> list[dict] | None:
    """Generate a structured file manifest for a phase via Sonnet planner."""
    from app.clients.llm_client import chat as llm_chat

    if not _PLANNER_BUILD_PROMPT_PATH.exists():
        logger.warning("planner_build_prompt.md not found — cannot generate manifest")
        return None
    system_prompt = _PLANNER_BUILD_PROMPT_PATH.read_text(encoding="utf-8")

    contract_parts = []
    for c in contracts:
        if c["contract_type"] == "phases":
            continue
        contract_parts.append(f"## {c['contract_type']}\n{c['content']}\n")
    contracts_text = "\n---\n".join(contract_parts)

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

    await _state.build_repo.append_build_log(
        build_id,
        f"Generating file manifest for Phase {current_phase['number']}",
        source="planner", level="info",
    )

    text = ""
    try:
        try:
            result = await llm_chat(
                api_key=api_key,
                model=_state.settings.LLM_PLANNER_MODEL,
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
                    model=_state.settings.LLM_PLANNER_MODEL,
                    system_prompt=system_prompt,
                    messages=[{"role": "user", "content": truncated_msg}],
                    max_tokens=4096,
                    provider="anthropic",
                )
            else:
                raise

        text = result["text"] if isinstance(result, dict) else result
        usage = result.get("usage", {}) if isinstance(result, dict) else {}

        input_t = usage.get("input_tokens", 0)
        output_t = usage.get("output_tokens", 0)
        model = _state.settings.LLM_PLANNER_MODEL
        input_rate, output_rate = _get_token_rates(model)
        cost = Decimal(input_t) * input_rate + Decimal(output_t) * output_rate
        await _state.build_repo.record_build_cost(
            build_id, f"Phase {current_phase['number']} (manifest)", input_t, output_t, model, cost,
        )
        await _accumulate_cost(build_id, input_t, output_t, model, cost)

        cleaned = text.strip()
        if cleaned.startswith("```"):
            first_nl = cleaned.find("\n")
            if first_nl >= 0:
                cleaned = cleaned[first_nl + 1:]
        if cleaned.rstrip().endswith("```"):
            cleaned = cleaned.rstrip()[:-3]
        cleaned = cleaned.strip()

        manifest = json.loads(cleaned)
        files = manifest.get("files", [])

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

        sorted_files = _topological_sort(valid_files)

        await _state.build_repo.append_build_log(
            build_id,
            f"File manifest generated: {len(sorted_files)} files",
            source="planner", level="info",
        )

        await _state._broadcast_build_event(user_id, build_id, "file_manifest", {
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
        try:
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


# ---------------------------------------------------------------------------
# Single file generation
# ---------------------------------------------------------------------------


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
    """Generate a single file via an independent API call."""
    from app.clients.llm_client import chat as llm_chat

    file_path = file_entry["path"]
    purpose = file_entry.get("purpose", "")
    language = file_entry.get("language", "python")
    estimated_lines = file_entry.get("estimated_lines", 100)

    relevant_contracts = _select_contracts_for_file(file_path, contracts)
    contracts_text = ""
    for c in relevant_contracts:
        contracts_text += f"\n## {c['contract_type']}\n{c['content']}\n"

    MAX_CONTRACTS_CHARS = 120_000
    if len(contracts_text) > MAX_CONTRACTS_CHARS:
        contracts_text = (
            contracts_text[:MAX_CONTRACTS_CHARS]
            + "\n\n[... contracts truncated for context budget ...]\n"
        )
        logger.info("Contracts capped at %d chars for %s", MAX_CONTRACTS_CHARS, file_path)

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

    system_prompt_tokens = len(system_prompt) // 4
    contract_tokens = len(contracts_text) // 4
    phase_tokens = len(phase_deliverables) // 4

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

    await _state._broadcast_build_event(user_id, build_id, "file_generating", {
        "path": file_path,
    })

    _FILE_GEN_TIMEOUT = 600

    try:
        result = await asyncio.wait_for(
            llm_chat(
                api_key=api_key,
                model=_state.settings.LLM_BUILDER_MODEL,
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
            MAX_FALLBACK_CHARS = 600_000
            if len(minimal_msg) > MAX_FALLBACK_CHARS:
                minimal_msg = (
                    minimal_msg[:MAX_FALLBACK_CHARS]
                    + "\n\n[... truncated for context limit ...]\n"
                )
            result = await asyncio.wait_for(
                llm_chat(
                    api_key=api_key,
                    model=_state.settings.LLM_BUILDER_MODEL,
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

    content = re.sub(r"^```\w*\n", "", content)
    content = re.sub(r"\n```\s*$", "", content)

    if content and not content.endswith("\n"):
        content += "\n"

    if not content.strip():
        logger.warning("Empty response for %s — retrying once", file_path)
        result2 = await asyncio.wait_for(
            llm_chat(
                api_key=api_key,
                model=_state.settings.LLM_BUILDER_MODEL,
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
        for k in ("input_tokens", "output_tokens"):
            usage[k] = usage.get(k, 0) + usage2.get(k, 0)

    input_t = usage.get("input_tokens", 0)
    output_t = usage.get("output_tokens", 0)
    model = _state.settings.LLM_BUILDER_MODEL
    input_rate, output_rate = _get_token_rates(model)
    cost = Decimal(input_t) * input_rate + Decimal(output_t) * output_rate
    await _state.build_repo.record_build_cost(
        build_id, f"file:{file_path}", input_t, output_t, model, cost,
    )
    await _accumulate_cost(build_id, input_t, output_t, model, cost)

    from app.services.tool_executor import _exec_write_file
    write_result = _exec_write_file({"path": file_path, "content": content}, working_dir)

    size_bytes = len(content.encode("utf-8"))
    lang = _state._detect_language(file_path)

    await _state.build_repo.append_build_log(
        build_id,
        json.dumps({"path": file_path, "size_bytes": size_bytes, "language": lang}),
        source="file", level="info",
    )
    await _state._broadcast_build_event(user_id, build_id, "file_generated", {
        "path": file_path,
        "size_bytes": size_bytes,
        "language": lang,
        "tokens_in": input_t,
        "tokens_out": output_t,
        "duration_ms": 0,
    })

    return content


# ---------------------------------------------------------------------------
# Fix manifest generation
# ---------------------------------------------------------------------------


async def _generate_fix_manifest(
    build_id: UUID,
    user_id: UUID,
    api_key: str,
    recovery_plan: str,
    existing_files: dict[str, str],
    audit_findings: str,
    contracts: list[dict],
) -> list[dict] | None:
    """Generate a fix manifest from a recovery plan."""
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
            model=_state.settings.LLM_PLANNER_MODEL,
            system_prompt=system_prompt,
            messages=[{"role": "user", "content": user_message}],
            max_tokens=4096,
            provider="anthropic",
        )

        text = result["text"] if isinstance(result, dict) else result

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
                "language": _state._detect_language(f.get("path", "")),
                "status": "pending",
                "fix_instructions": f.get("fix_instructions", ""),
            }
            for f in fixes
            if f.get("path")
        ]

        non_delete = [m for m in manifest if m["action"] != "delete"]
        if (
            len(existing_files) > 0
            and len(non_delete) > max(5, len(existing_files) // 2)
        ):
            logger.warning(
                "Fix manifest too large (%d of %d files) — trimming to 5",
                len(non_delete), len(existing_files),
            )
            deletes = [m for m in manifest if m["action"] == "delete"]
            manifest = deletes + non_delete[:5]

        return manifest
    except Exception as exc:
        logger.warning("Fix manifest generation failed: %s", exc)
        return None
