"""Planner — phase planning, LLM plan generation, plan parsing, file manifest."""

import asyncio
import json
import logging
import os
import re
from decimal import Decimal
from pathlib import Path
from typing import Any
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

# Contract relevance mapping — which contracts are useful for which file types.
# Checked in order; a file may match MULTIPLE prefixes (no early break).
# The map intentionally includes generic patterns (src/, backend/, server/,
# routes/) so projects that don't follow the ForgeGuard layout still get
# the right contracts.
_CONTRACT_RELEVANCE: dict[str, list[str]] = {
    # ── ForgeGuard-style layout ──
    "app/api/": ["blueprint", "schema", "stack", "physics", "boundaries"],
    "app/": ["blueprint", "schema", "stack", "boundaries"],
    "tests/": ["blueprint", "schema", "stack", "physics", "boundaries"],
    "web/src/components/": ["ui", "blueprint", "stack"],
    "web/src/pages/": ["ui", "blueprint", "stack", "schema"],
    "web/": ["ui", "blueprint", "stack"],
    "db/": ["schema"],
    # ── Generic patterns for any project ──
    "src/api/": ["blueprint", "schema", "stack", "physics", "boundaries"],
    "src/routes/": ["blueprint", "schema", "stack", "physics", "boundaries"],
    "src/pages/": ["ui", "blueprint", "stack", "schema"],
    "src/components/": ["ui", "blueprint", "stack"],
    "src/": ["blueprint", "schema", "stack", "boundaries"],
    "backend/": ["blueprint", "schema", "stack", "physics", "boundaries"],
    "server/": ["blueprint", "schema", "stack", "physics", "boundaries"],
    "routes/": ["blueprint", "schema", "stack", "physics"],
    "api/": ["blueprint", "schema", "stack", "physics", "boundaries"],
    "frontend/": ["ui", "blueprint", "stack"],
    "client/": ["ui", "blueprint", "stack"],
    "config": ["stack", "boundaries"],
    "doc": ["blueprint", "manifesto"],
}

# Planner build prompt template path
_PLANNER_BUILD_PROMPT_PATH = (
    Path(__file__).resolve().parent.parent.parent / "templates" / "contracts" / "planner_build_prompt.md"
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
    lines.append("7. Start the application (pick the script for your OS):\n"
                 "   - **macOS/Linux:** `bash boot.sh`\n"
                 "   - **Windows CMD:** `boot.bat`\n"
                 "   - **Windows PowerShell:** `pwsh -File boot.ps1`\n")

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
        enable_caching=True,
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
    """Select the subset of contracts relevant to a given file type.

    Checks ALL prefixes in ``_CONTRACT_RELEVANCE`` (no early break) so a
    file like ``app/api/routers/users.py`` matches both ``app/api/`` and
    ``app/``, accumulating all relevant contract types.
    """
    relevant_types: set[str] = set()

    for prefix, types in _CONTRACT_RELEVANCE.items():
        if file_path.startswith(prefix):
            relevant_types.update(types)
            # No break — keep checking for more specific matches

    if not relevant_types:
        # Fallback for paths that match nothing
        relevant_types = {"blueprint", "stack"}

    # Test files always get boundaries for layer-checking context
    if file_path.startswith("tests/") or "/tests/" in file_path:
        relevant_types.update(["blueprint", "schema", "stack", "boundaries"])

    # SQL files only need schema
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
# Chunk planning — Sonnet breaks a phase into ordered build chunks
# ---------------------------------------------------------------------------

# Maximum files per chunk.  Keeps each builder invocation focused.
_MAX_CHUNK_SIZE = 6

# Config/boilerplate files that don't need full contracts context.
# When a batch contains ONLY these files, contracts_text is omitted
# to save 4-10K input tokens.
_NO_CONTRACT_FILES = frozenset({
    "package.json", "tsconfig.json", "pyproject.toml", "setup.py",
    "setup.cfg", ".eslintrc.js", ".eslintrc.json", "jest.config.js",
    ".prettierrc", ".prettierrc.json", "Dockerfile", "docker-compose.yml",
    ".dockerignore", ".gitignore", ".env.example", "README.md",
    "tailwind.config.js", "postcss.config.js", "vite.config.ts",
    "vite.config.js", "next.config.js", "next.config.ts",
})


def _dependency_sort(files: list[dict]) -> list[dict]:
    """Sort files so dependencies come before dependents.

    Returns the same files in a safe build order.  Does NOT group into tiers
    — the LLM decides the grouping via ``_plan_phase_chunks``.
    """
    path_to_entry = {f["path"]: f for f in files}
    path_to_depth: dict[str, int] = {}
    temp_mark: set[str] = set()

    def depth(path: str) -> int:
        if path in path_to_depth:
            return path_to_depth[path]
        if path in temp_mark:
            return 0
        if path not in path_to_entry:
            return -1
        temp_mark.add(path)
        entry = path_to_entry[path]
        max_dep = -1
        for dep in entry.get("depends_on", []):
            if dep in path_to_entry:
                max_dep = max(max_dep, depth(dep))
        temp_mark.discard(path)
        d = max_dep + 1
        path_to_depth[path] = d
        return d

    for f in files:
        depth(f["path"])

    return sorted(files, key=lambda f: path_to_depth.get(f["path"], 0))

# ---------------------------------------------------------------------------
# REMOVED: _plan_phase_chunks and _algorithmic_chunks
# Phase manifest + chunk planning now in planner_agent_loop.run_phase_planner_agent().
# Do NOT re-add chunk planning logic here.
# ---------------------------------------------------------------------------


async def _review_chunk_completion(
    build_id: UUID,
    user_id: UUID,
    api_key: str,
    chunk_idx: int,
    chunk_name: str,
    chunk_written: dict[str, str],
    remaining_chunks: list[dict],
    accumulated_interfaces: str,
    working_dir: str,
) -> str:
    """After a chunk completes, Sonnet reviews what was built and preps the next.

    Returns an interface summary of what was just built (for the next chunk's
    context).  Also writes observations to the scratchpad.
    """
    from app.services.tool_executor import _exec_forge_scratchpad

    # Extract actual exports from written files (cheap — no LLM)
    parts = [f"# Chunk {chunk_idx}: {chunk_name}\n"]
    for path, content in chunk_written.items():
        parts.append(f"\n## {path}")
        lines = content.split("\n")
        exports = []
        for line in lines:
            stripped = line.strip()
            if (stripped.startswith("class ") or stripped.startswith("def ") or
                    stripped.startswith("async def ")) and not line.startswith(" " * 4):
                exports.append(stripped.split("(")[0].split(":")[0])
            elif "=" in stripped and stripped.split("=")[0].strip().isupper() and not line.startswith(" "):
                exports.append(stripped.split("=")[0].strip())
            elif stripped.startswith("export "):
                exports.append(stripped[:100])
        if exports:
            parts.append("\n".join(f"- `{e}`" for e in exports[:20]))
        else:
            parts.append("(no top-level exports detected)")

    interfaces_text = "\n".join(parts)

    # Write to scratchpad
    scratchpad_key = f"chunk_{chunk_idx}_done"
    summary = (
        f"Chunk {chunk_idx} ({chunk_name}) complete: "
        f"{len(chunk_written)}/{len(chunk_written)} files written.\n"
        f"Next: {len(remaining_chunks)} chunks remaining."
    )
    _exec_forge_scratchpad(
        {"operation": "write", "key": scratchpad_key, "value": summary + "\n\n" + interfaces_text},
        working_dir,
    )

    await _state._broadcast_build_event(user_id, build_id, "scratchpad_write", {
        "key": scratchpad_key,
        "source": "sonnet",
        "role": "planner",
        "summary": summary,
        "content": interfaces_text[:500],
        "full_length": len(interfaces_text),
    })

    await _state.build_repo.append_build_log(
        build_id,
        f"Chunk {chunk_idx} ({chunk_name}) done — "
        f"{len(chunk_written)} files, {len(remaining_chunks)} chunks remaining",
        source="planner", level="info",
    )

    return interfaces_text


# ---------------------------------------------------------------------------
# Legacy tier analysis — kept as algorithmic fallback
# ---------------------------------------------------------------------------

# Maximum files per tier.  Large tiers get split into sub-tiers so each
# group is small enough for the agents to reason carefully about.
_MAX_TIER_SIZE = 6


def _compute_tiers(files: list[dict]) -> list[list[dict]]:
    """Group manifest files into dependency tiers for parallel execution.

    Each tier contains files that depend ONLY on files in previous tiers,
    meaning all files within a tier can be built concurrently.

    Large tiers (> _MAX_TIER_SIZE) are split into sub-tiers grouped by
    directory affinity so agents get smaller, focused batches.

    Returns a list-of-lists: ``tiers[0]`` is the foundation tier (no deps),
    ``tiers[1]`` depends only on tier 0, etc.

    Falls back to single-file tiers (sequential) on circular deps.
    """
    path_to_entry = {f["path"]: f for f in files}
    path_to_depth: dict[str, int] = {}
    visited: set[str] = set()
    temp_mark: set[str] = set()

    def depth(path: str) -> int:
        if path in path_to_depth:
            return path_to_depth[path]
        if path in temp_mark:
            return 0  # cycle — treat as depth 0
        if path not in path_to_entry:
            return -1  # external dep
        temp_mark.add(path)
        entry = path_to_entry[path]
        max_dep = -1
        for dep in entry.get("depends_on", []):
            if dep in path_to_entry:
                max_dep = max(max_dep, depth(dep))
        temp_mark.discard(path)
        d = max_dep + 1
        path_to_depth[path] = d
        return d

    for f in files:
        depth(f["path"])

    # Group by depth
    max_depth = max(path_to_depth.values()) if path_to_depth else 0
    raw_tiers: list[list[dict]] = [[] for _ in range(max_depth + 1)]
    for f in files:
        d = path_to_depth.get(f["path"], 0)
        raw_tiers[d].append(f)

    # Split oversized tiers into sub-tiers of _MAX_TIER_SIZE
    tiers: list[list[dict]] = []
    for tier in raw_tiers:
        if not tier:
            continue
        if len(tier) <= _MAX_TIER_SIZE:
            tiers.append(tier)
        else:
            # Group by directory for cohesion, then chunk
            dir_groups: dict[str, list[dict]] = {}
            for f in tier:
                d = str(Path(f["path"]).parent)
                dir_groups.setdefault(d, []).append(f)
            # Build sub-tiers by packing directory groups
            sub_tier: list[dict] = []
            for _d, group in sorted(dir_groups.items()):
                if len(sub_tier) + len(group) > _MAX_TIER_SIZE:
                    if sub_tier:
                        tiers.append(sub_tier)
                    # If a single dir group exceeds cap, chunk it
                    if len(group) > _MAX_TIER_SIZE:
                        for j in range(0, len(group), _MAX_TIER_SIZE):
                            tiers.append(group[j:j + _MAX_TIER_SIZE])
                        sub_tier = []
                    else:
                        sub_tier = list(group)
                else:
                    sub_tier.extend(group)
            if sub_tier:
                tiers.append(sub_tier)

    return tiers


def _tier_summary(tiers: list[list[dict]]) -> str:
    """Return a human-readable summary of tiers for logging/display."""
    lines = []
    for i, tier in enumerate(tiers):
        paths = [f["path"] for f in tier]
        lines.append(f"Tier {i} ({len(tier)} files): {', '.join(paths)}")
    return "\n".join(lines)


async def _plan_tier_interfaces(
    build_id: UUID,
    user_id: UUID,
    api_key: str,
    tier_index: int,
    tier_files: list[dict],
    prior_tier_interfaces: str,
    contracts: list[dict],
    phase_deliverables: str,
    working_dir: str,
) -> str:
    """Have Sonnet produce an interface map for a tier before builders start.

    Returns the interface map text (also written to scratchpad).
    """
    from app.clients.llm_client import chat as llm_chat
    from app.services.tool_executor import _exec_forge_scratchpad

    file_specs = []
    for f in tier_files:
        deps = ", ".join(f.get("depends_on", [])) or "none"
        file_specs.append(
            f"- **{f['path']}** ({f.get('language', 'python')}): "
            f"{f.get('purpose', 'no description')}  "
            f"[depends_on: {deps}]"
        )

    # Select relevant contracts for this tier
    relevant_contract_types = set()
    for f in tier_files:
        for prefix, types in _CONTRACT_RELEVANCE.items():
            if f["path"].startswith(prefix) or f["path"].replace("backend/", "").startswith(prefix):
                relevant_contract_types.update(types)
    # Always include core contracts
    relevant_contract_types.update(["manifesto", "blueprint", "schema", "stack"])

    contracts_text = ""
    for c in contracts:
        ctype = c.get("contract_type", "")
        if ctype in relevant_contract_types:
            contracts_text += f"\n### {ctype}\n{c.get('content', '')[:8000]}\n"

    # Cap contracts to keep prompt small — interface planning is about
    # names and signatures, not full contract text.
    MAX_CONTRACT_CHARS = 4000
    if len(contracts_text) > MAX_CONTRACT_CHARS:
        contracts_text = contracts_text[:MAX_CONTRACT_CHARS] + "\n[...truncated]"

    prompt = f"""Produce a SHORT interface cheat-sheet for Tier {tier_index}.

These {len(tier_files)} files will be built in parallel. Each builder only sees this cheat-sheet — keep it concise.

For each file give ONE block:
  path: <file>
  exports: ClassName, function_name(args) -> ret, CONSTANT
  imports: from <prior_file> import X, Y

Prior tiers already built:
{prior_tier_interfaces or '(none — this is the first tier)'}

Files in this tier:
{chr(10).join(file_specs)}

Contracts (summary):
{contracts_text}

Rules:
- Use exact identifiers the builders should use.
- One line per export. No prose, no explanations, no examples.
- Keep the whole output under 120 lines."""

    # Broadcast thinking event — what Sonnet is about to receive
    _imap_sys = "You are a build coordinator. Output ONLY the interface cheat-sheet. No preamble."
    await _state._broadcast_build_event(user_id, build_id, "llm_thinking", {
        "model": "sonnet",
        "purpose": f"Planning Tier {tier_index} interfaces ({len(tier_files)} files)",
        "system_prompt": _imap_sys,
        "user_message_preview": prompt[:800],
        "user_message_length": len(prompt),
    })

    try:
        result = await llm_chat(
            api_key=api_key,
            model=_state.settings.LLM_PLANNER_MODEL,
            system_prompt=_imap_sys,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=2048,
            provider="anthropic",
            enable_caching=True,
        )

        text = result["text"] if isinstance(result, dict) else result
        usage = result.get("usage", {}) if isinstance(result, dict) else {}

        # Record cost
        input_t = usage.get("input_tokens", 0)
        output_t = usage.get("output_tokens", 0)
        model = _state.settings.LLM_PLANNER_MODEL
        input_rate, output_rate = _get_token_rates(model)
        cost = Decimal(input_t) * input_rate + Decimal(output_t) * output_rate
        await _state.build_repo.record_build_cost(
            build_id, f"tier_plan:{tier_index}", input_t, output_t, model, cost,
        )
        from .cost import _accumulate_cost
        await _accumulate_cost(build_id, input_t, output_t, model, cost)

        # Write to scratchpad
        scratchpad_key = f"tier_{tier_index}_interfaces"
        _exec_forge_scratchpad(
            {"operation": "write", "key": scratchpad_key, "value": text},
            working_dir,
        )

        # Broadcast scratchpad event — short preview only
        await _state._broadcast_build_event(user_id, build_id, "scratchpad_write", {
            "key": scratchpad_key,
            "source": "sonnet",
            "role": "planner",
            "summary": f"Tier {tier_index} interfaces ({len(tier_files)} files)",
            "content": text[:500],
            "full_length": len(text),
        })

        await _state.build_repo.append_build_log(
            build_id,
            f"Tier {tier_index} interfaces planned ({len(tier_files)} files, {input_t + output_t} tok)",
            source="planner", level="info",
        )

        return text

    except Exception as exc:
        logger.error("Failed to plan tier %d interfaces: %s", tier_index, exc, exc_info=True)
        await _state.build_repo.append_build_log(
            build_id,
            f"Tier {tier_index} interface planning failed: {exc} — builders will use manifest only",
            source="planner", level="warn",
        )
        return ""


async def _extract_tier_interfaces(
    build_id: UUID,
    user_id: UUID,
    api_key: str,
    tier_index: int,
    written_files: dict[str, str],
    working_dir: str,
) -> str:
    """After a tier is built, extract the actual interfaces from written code.

    This provides accurate interface info for the next tier's builders,
    rather than relying on the planned interfaces which may have diverged.
    """
    from app.services.tool_executor import _exec_forge_scratchpad

    # Build a compact summary of exports from each file
    parts = [f"# Tier {tier_index} — Actual Interfaces\n"]
    for path, content in written_files.items():
        parts.append(f"\n## {path}")
        # Extract key definitions from code
        lines = content.split("\n")
        exports = []
        for line in lines:
            stripped = line.strip()
            # Python: class/def/async def at top level
            if (stripped.startswith("class ") or stripped.startswith("def ") or
                    stripped.startswith("async def ")) and not line.startswith(" " * 4):
                exports.append(stripped.split("(")[0].split(":")[0])
            # Python: module-level constants (UPPER_CASE = ...)
            elif "=" in stripped and stripped.split("=")[0].strip().isupper() and not line.startswith(" "):
                exports.append(stripped.split("=")[0].strip())
            # TypeScript/JS: export
            elif stripped.startswith("export "):
                exports.append(stripped[:100])
        if exports:
            parts.append("\n".join(f"- `{e}`" for e in exports[:30]))
        else:
            parts.append("(no top-level exports detected)")

    text = "\n".join(parts)

    # Write to scratchpad
    scratchpad_key = f"tier_{tier_index}_actual"
    _exec_forge_scratchpad(
        {"operation": "write", "key": scratchpad_key, "value": text},
        working_dir,
    )

    await _state._broadcast_build_event(user_id, build_id, "scratchpad_write", {
        "key": scratchpad_key,
        "source": "sonnet",
        "role": "planner",
        "summary": f"Tier {tier_index} actual exports ({len(written_files)} files)",
        "content": text[:500],
        "full_length": len(text),
    })

    return text


# ---------------------------------------------------------------------------
# Live Sonnet quality review — reviews files as they're written
# ---------------------------------------------------------------------------

_REVIEW_SYSTEM_PROMPT = """You are a rapid code reviewer. Check each file for:
1. Missing imports or wrong import paths
2. Empty/stub functions that should have implementation
3. Type errors or signature mismatches with the interface map
4. Missing error handling in critical paths

Output ONE LINE per file:
OK: <path> — no issues
WARN: <path> — <brief issue description>

Keep it SHORT. No code blocks, no suggestions, just verdicts."""


async def _review_written_files(
    build_id: UUID,
    user_id: UUID,
    api_key: str,
    files: dict[str, str],
    interface_map: str,
    tier_index: int,
) -> list[dict]:
    """Quick Sonnet review of written files — catches issues before formal audit.

    Returns list of {path, verdict, note} dicts.
    """
    from app.clients.llm_client import chat as llm_chat

    if not files:
        return []

    # Build file snippets — just first/last lines for speed
    snippets = []
    for path, content in files.items():
        lines = content.split("\n")
        if len(lines) > 60:
            preview = "\n".join(lines[:30]) + "\n...\n" + "\n".join(lines[-20:])
        else:
            preview = content
        snippets.append(f"### {path}\n```\n{preview}\n```")

    prompt = (
        f"Review these {len(files)} files from Tier {tier_index}.\n\n"
        f"Interface map:\n{interface_map[:2000]}\n\n"
        + "\n\n".join(snippets)
    )

    # Broadcast thinking event — Sonnet review prompt
    await _state._broadcast_build_event(user_id, build_id, "llm_thinking", {
        "model": "sonnet",
        "purpose": f"Reviewing {len(files)} files from Tier {tier_index}",
        "system_prompt": _REVIEW_SYSTEM_PROMPT,
        "user_message_preview": prompt[:800],
        "user_message_length": len(prompt),
    })

    try:
        result = await llm_chat(
            api_key=api_key,
            model=_state.settings.LLM_PLANNER_MODEL,
            system_prompt=_REVIEW_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=1024,
            provider="anthropic",
            enable_caching=True,
        )

        text = result["text"] if isinstance(result, dict) else result
        usage = result.get("usage", {}) if isinstance(result, dict) else {}

        # Record cost
        input_t = usage.get("input_tokens", 0)
        output_t = usage.get("output_tokens", 0)
        model = _state.settings.LLM_PLANNER_MODEL
        input_rate, output_rate = _get_token_rates(model)
        cost = Decimal(input_t) * input_rate + Decimal(output_t) * output_rate
        await _state.build_repo.record_build_cost(
            build_id, f"review:tier_{tier_index}", input_t, output_t, model, cost,
        )
        await _accumulate_cost(build_id, input_t, output_t, model, cost)

        # Parse results
        reviews: list[dict] = []
        for line in text.strip().split("\n"):
            line = line.strip()
            if not line:
                continue
            if line.startswith("OK:"):
                parts = line[3:].split("—", 1)
                path = parts[0].strip()
                reviews.append({"path": path, "verdict": "ok", "note": ""})
            elif line.startswith("WARN:"):
                parts = line[5:].split("—", 1)
                path = parts[0].strip()
                note = parts[1].strip() if len(parts) > 1 else "potential issue"
                reviews.append({"path": path, "verdict": "warn", "note": note})

        # Broadcast results
        for r in reviews:
            icon = "✅" if r["verdict"] == "ok" else "⚠️"
            msg = f"{icon} Reviewed {r['path']}"
            if r["note"]:
                msg += f" — {r['note']}"
            await _state._broadcast_build_event(user_id, build_id, "sonnet_review", {
                "path": r["path"],
                "verdict": r["verdict"],
                "note": r["note"],
                "tier": tier_index,
            })

        await _state.build_repo.append_build_log(
            build_id,
            f"Sonnet reviewed {len(reviews)} files from tier {tier_index} "
            f"({sum(1 for r in reviews if r['verdict'] == 'warn')} warnings)",
            source="planner", level="info",
        )

        return reviews

    except Exception as exc:
        logger.warning("Sonnet review failed for tier %d: %s", tier_index, exc)
        return []


# ---------------------------------------------------------------------------
# REMOVED: Context assembly helpers and legacy manifest generators
#   _analyse_phase_scope, _filter_contracts_for_phase,
#   _extract_relevant_sections, _filter_workspace_for_phase,
#   _deduplicate_prior_phase_files, _generate_skeleton_manifest,
#   _generate_file_manifest, _generate_full_manifest
# All replaced by planner_agent_loop.py. Do NOT re-add here.
# ---------------------------------------------------------------------------


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
    phase_plan_context: str = "",
) -> str:
    """Generate a single file via an independent API call.

    Parameters
    ----------
    phase_plan_context : str
        Markdown listing all files being created in the same phase.
        Helps the generator understand sibling imports and interfaces.
    """
    from app.clients.llm_client import chat as llm_chat

    file_path = file_entry["path"]
    purpose = file_entry.get("purpose", "")
    language = file_entry.get("language", "python")
    estimated_lines = file_entry.get("estimated_lines", 100)

    relevant_contracts = _select_contracts_for_file(file_path, contracts)
    contracts_text = ""
    _PER_CONTRACT_CAP = 4_000  # keep each contract type compact
    for c in relevant_contracts:
        _ctext = c["content"]
        if len(_ctext) > _PER_CONTRACT_CAP:
            _ctext = (
                _ctext[:_PER_CONTRACT_CAP]
                + f"\n[... {c['contract_type']} truncated at {_PER_CONTRACT_CAP} chars ...]\n"
            )
        contracts_text += f"\n## {c['contract_type']}\n{_ctext}\n"

    MAX_CONTRACTS_CHARS = 20_000
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
        "- Include type hints\n\n"
        "Code Style — CRITICAL:\n"
        "- Output PURE CODE only. Zero narrative prose or tutorial-style text.\n"
        "- Docstrings: single-line only (e.g. `\"\"\"Fetch user by ID.\"\"\"`). "
        "NEVER multi-line docstrings with Args/Returns/Yields sections.\n"
        "- Comments: only where logic is non-obvious. No 'this function does X' comments.\n"
        "- No module-level essays, no section separator comments with explanations.\n"
        "- Every output token costs money — be maximally concise.\n"
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

    if phase_plan_context:
        parts.append(f"\n{phase_plan_context}\n")

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

    # Broadcast thinking event — what Opus is about to receive
    await _state._broadcast_build_event(user_id, build_id, "llm_thinking", {
        "model": "opus",
        "purpose": f"Building {file_path}",
        "system_prompt": system_prompt,
        "user_message_preview": user_message[:800],
        "user_message_length": len(user_message),
        "file": file_path,
        "contracts_included": [c['contract_type'] for c in relevant_contracts],
        "context_files": list(budgeted_context.keys()),
    })

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
                enable_caching=True,
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
            MAX_FALLBACK_CHARS = 80_000
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
                    enable_caching=True,
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
                enable_caching=True,
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
            enable_caching=True,
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


# ---------------------------------------------------------------------------
# Parallel tier execution — dispatch sub-agents per tier
# ---------------------------------------------------------------------------

_MAX_PARALLEL_AGENTS = 2  # max concurrent Opus sub-agents per tier (lower = cheaper, more thoughtful)


def _batch_tier_files(
    tier_files: list[dict],
    max_agents: int = _MAX_PARALLEL_AGENTS,
) -> list[list[dict]]:
    """Split a tier's files into sub-agent batches (max 3 files each).

    Groups closely related files (same directory) together.
    Returns a list of batches, each being a list of file entries.
    """
    _MAX_PER_BATCH = 3  # keep batches small so agents reason carefully

    if len(tier_files) <= _MAX_PER_BATCH:
        return [tier_files]

    # Group by directory
    dir_groups: dict[str, list[dict]] = {}
    for f in tier_files:
        d = str(Path(f["path"]).parent)
        dir_groups.setdefault(d, []).append(f)

    # Merge small groups into batches
    batches: list[list[dict]] = []
    current_batch: list[dict] = []
    for _dir, group in sorted(dir_groups.items()):
        if len(current_batch) + len(group) > _MAX_PER_BATCH:
            if current_batch:
                batches.append(current_batch)
            # If group itself is big, split it
            if len(group) > _MAX_PER_BATCH:
                for j in range(0, len(group), _MAX_PER_BATCH):
                    batches.append(group[j:j + _MAX_PER_BATCH])
                current_batch = []
            else:
                current_batch = list(group)
        else:
            current_batch.extend(group)
    if current_batch:
        batches.append(current_batch)

    # Cap to max_agents by merging smallest batches
    while len(batches) > max_agents:
        batches.sort(key=len)
        smallest = batches.pop(0)
        batches[0] = smallest + batches[0]

    return batches


async def execute_tier(
    build_id: UUID,
    user_id: UUID,
    api_key: str,
    tier_index: int,
    tier_files: list[dict],
    contracts: list[dict],
    phase_deliverables: str,
    working_dir: str,
    interface_map: str,
    all_files_written: dict[str, str],
    *,
    key_pool: Any | None = None,
    audit_api_key: str | None = None,
    build_mode: str = "full",
) -> dict[str, str]:
    """Execute a tier using per-file Builder Agent pipelines (SCOUT→CODER→AUDITOR→FIXER).

    Each file is built independently by run_builder(), with up to 3 files running
    concurrently (controlled by a semaphore).  SCOUT/CODER use api_key; AUDITOR/FIXER
    use audit_api_key (falls back to api_key if not set).

    Returns dict of ``{path: content}`` for all files written.
    """
    from .subagent import build_context_pack, SubAgentResult
    from .verification import _is_test_file
    from builder.builder_agent import run_builder, BuilderError  # noqa: E402

    # Compute common path prefix for display stripping
    all_paths = [f["path"] for f in tier_files]
    if len(all_paths) > 1:
        _common = os.path.commonpath(all_paths)
        # Only strip if it's a directory prefix (contains /)
        common_prefix = (_common.rsplit("/", 1)[0] + "/") if "/" in _common else ""
    elif all_paths and "/" in all_paths[0]:
        common_prefix = all_paths[0].rsplit("/", 1)[0] + "/"
    else:
        common_prefix = ""

    await _state.build_repo.append_build_log(
        build_id,
        f"Tier {tier_index}: {len(tier_files)} files (per-file Builder pipeline)",
        source="planner", level="info",
    )
    await _state._broadcast_build_event(user_id, build_id, "tier_start", {
        "tier": tier_index,
        "file_count": len(tier_files),
        "batch_count": min(len(tier_files), 3),  # capped by _semaphore concurrency
        "files": all_paths,
        "common_prefix": common_prefix,
        "agents": [
            {
                "agent_id": f"builder-{idx}",
                "files": [f["path"]],
            }
            for idx, f in enumerate(tier_files)
        ],
    })

    # Pre-load relevant contracts INLINE so Coder agents don't need
    # tool calls to fetch them.  Each contract pull via tool would add
    # a full round-trip (output tokens for tool_use + input replay of
    # the growing message history).  Pre-loading is far cheaper.
    _CONTRACT_PER_CAP = 3_000   # chars per contract
    _CONTRACT_TOTAL_CAP = 12_000  # total chars for all contracts
    relevant_types = set()
    for f in tier_files:
        for prefix, types in _CONTRACT_RELEVANCE.items():
            fp = f["path"]
            if fp.startswith(prefix) or fp.replace("backend/", "").startswith(prefix):
                relevant_types.update(types)
    # Always include the core contracts coders need
    relevant_types.update(["stack", "physics", "boundaries", "schema"])

    _contract_parts: list[str] = []
    _contract_total = 0
    # Prioritise key types first
    _priority_order = ["stack", "physics", "boundaries", "schema"]
    _sorted_contracts = sorted(
        contracts,
        key=lambda c: (
            _priority_order.index(c.get("contract_type", ""))
            if c.get("contract_type", "") in _priority_order
            else 100
        ),
    )
    for c in _sorted_contracts:
        ctype = c.get("contract_type", "")
        if ctype not in relevant_types:
            continue
        content = (c.get("content", "") or "").strip()
        if not content:
            continue
        if len(content) > _CONTRACT_PER_CAP:
            content = content[:_CONTRACT_PER_CAP] + "\n[...truncated]"
        if _contract_total + len(content) > _CONTRACT_TOTAL_CAP:
            break
        _contract_parts.append(f"### {ctype}\n{content}")
        _contract_total += len(content)

    contracts_summary = (
        "## Project Contracts (pre-loaded)\n"
        + "\n\n".join(_contract_parts)
    ) if _contract_parts else ""

    # Slimmed contract summary for test-heavy batches (just stack info)
    _test_contract_parts = [p for p in _contract_parts[:1]]  # stack only
    contracts_summary_slim = (
        "## Project Contracts (slim — test batch)\n"
        + "\n\n".join(_test_contract_parts)
    ) if _test_contract_parts else ""

    # Per-file builder pipeline (max 3 concurrent)
    _semaphore = asyncio.Semaphore(3)

    async def run_one_file(file_entry: dict, file_idx: int) -> None:
        fp = file_entry["path"]
        _is_test = _is_test_file(fp)
        _is_config_only = Path(fp).name in _NO_CONTRACT_FILES

        # Build context from already-written files (prior tiers/files in this tier)
        _CTX_PER_FILE_CAP = 2_000 if _is_test else 3_000
        _CTX_TOTAL_CAP = 8_000 if _is_test else 15_000
        context_files: dict[str, str] = {}
        _ctx_total = 0
        for dep in file_entry.get("depends_on", []):
            if dep in all_files_written and dep not in context_files:
                _snippet = all_files_written[dep]
                if len(_snippet) > _CTX_PER_FILE_CAP:
                    _snippet = _snippet[:_CTX_PER_FILE_CAP] + "\n# [truncated]\n"
                if _ctx_total + len(_snippet) > _CTX_TOTAL_CAP:
                    break
                context_files[dep] = _snippet
                _ctx_total += len(_snippet)
        for ctx in file_entry.get("context_files", []):
            if ctx in all_files_written and ctx not in context_files:
                _snippet = all_files_written[ctx]
                if len(_snippet) > _CTX_PER_FILE_CAP:
                    _snippet = _snippet[:_CTX_PER_FILE_CAP] + "\n# [truncated]\n"
                if _ctx_total + len(_snippet) > _CTX_TOTAL_CAP:
                    break
                context_files[ctx] = _snippet
                _ctx_total += len(_snippet)

        # Auto-detected disk context (slim for test files)
        disk_context = build_context_pack(
            working_dir, [fp],
            max_context_files=3 if _is_test else 6,
            max_context_chars=6_000 if _is_test else 15_000,
        )
        for k, v in disk_context.items():
            if k not in context_files:
                context_files[k] = v

        # Include interface map as a named context file so SCOUT and CODER see it
        if interface_map:
            context_files["interface_map.md"] = interface_map

        # Select contracts: skip for config-only, slim for test files
        if _is_config_only:
            _contracts_list: list[str] = []
        elif _is_test:
            _contracts_list = [contracts_summary_slim] if contracts_summary_slim else []
        else:
            _contracts_list = [contracts_summary] if contracts_summary else []

        # Broadcast file_generating
        _agent_id = f"builder-{file_idx}"
        await _state._broadcast_build_event(user_id, build_id, "file_generating", {
            "path": fp,
            "agent_id": _agent_id,
            "tier": tier_index,
            "common_prefix": common_prefix,
        })

        async with _semaphore:
            result = await run_builder(
                file_entry=file_entry,
                contracts=_contracts_list,
                context=list(context_files.values()),
                phase_deliverables=[phase_deliverables] if phase_deliverables else [],
                working_dir=working_dir,
                build_id=str(build_id),
                user_id=str(user_id),
                api_key=api_key,
                audit_api_key=audit_api_key,
                phase_plan_context="",
                build_mode=build_mode,
            )

        if result.status == "completed" and result.content:
            tier_written[fp] = result.content

        # Accumulate cost from all sub-agents in the pipeline
        for sar in result.sub_agent_results:
            if isinstance(sar, SubAgentResult):
                try:
                    await _accumulate_cost(
                        build_id,
                        sar.input_tokens, sar.output_tokens,
                        sar.model,
                        Decimal(str(sar.cost_usd)),
                    )
                except Exception:
                    pass

        # Broadcast completion
        await _state._broadcast_build_event(user_id, build_id, "agent_done", {
            "agent_id": _agent_id,
            "tier": tier_index,
            "files_written": [fp] if result.status == "completed" else [],
            "file_count": 1 if result.status == "completed" else 0,
            "status": result.status,
        })

    tier_written: dict[str, str] = {}

    # Run all files (semaphore limits to 3 concurrent)
    await asyncio.gather(*[
        run_one_file(fe, idx) for idx, fe in enumerate(tier_files)
    ], return_exceptions=False)

    # Emit file_generated events for each successfully written file
    for fp, content in tier_written.items():
        await _state._broadcast_build_event(user_id, build_id, "file_generated", {
            "path": fp,
            "size_bytes": len(content.encode("utf-8")),
            "language": _state._detect_language(fp),
            "tokens_in": 0,
            "tokens_out": 0,
            "duration_ms": 0,
        })

    await _state._broadcast_build_event(user_id, build_id, "tier_complete", {
        "tier": tier_index,
        "files_written": list(tier_written.keys()),
        "file_count": len(tier_written),
    })

    return tier_written
