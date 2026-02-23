"""planner_agent_loop.py — ForgeGuard Phase Planner Agent.

Replaces the legacy _generate_file_manifest + _plan_phase_chunks two-step
LLM calls with a single proper agentic loop:

  1. Model receives phase context and tools
  2. Model explores the workspace (read_file, list_directory)
  3. Model terminates by calling write_phase_plan with manifest + chunks
  4. Server validates schema and caches the result

Architecture
------------
This is the same loop pattern as z:/ForgeCollection/planner/planner_agent.py
but integrated into ForgeGuard's build infrastructure:
  - Reports progress via _broadcast_build_event
  - Records token costs via _accumulate_cost
  - Sandboxed to the project's working_dir
  - Uses FORGE_CONTRACTS_DIR for governance contracts (same path as standalone)

Called from build_service._run_build_plan_execute() once per phase.
Returns {"manifest": list[dict], "chunks": list[dict]} or None on failure.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from uuid import UUID

import anthropic
from decimal import Decimal

from . import _state
from ._state import FORGE_CONTRACTS_DIR, logger
from .cost import _accumulate_cost, _get_token_rates

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

def _get_model() -> str:
    """Resolve the planner model from the active tier / per-role override."""
    from app.config import get_model_for_role
    return get_model_for_role("planner")


def _get_thinking_budget() -> int:
    """Return extended thinking token budget for the planner role."""
    from app.config import get_model_thinking_budget
    return get_model_thinking_budget("planner")


def _get_thinking_model() -> str:
    """Return model for thinking calls — Sonnet even when FORGE_FORCE_MODEL=haiku."""
    from app.config import get_thinking_model
    return get_thinking_model("planner")


_MAX_TOKENS = 8096
_MAX_ITERATIONS = 20

# Governance contract files to load into the system prompt (cached).
# Loaded in priority order. Missing files are skipped silently.
#
# ONLY builder_contract.md belongs here — it defines phase plan structure.
# Templates (blueprint_template.md etc.) are for the questionnaire flow that
# generates user contracts, NOT for planning or building. Removing them saves
# ~8,100 tokens per phase planning call with no loss of planning quality.
# Project contracts (blueprint, stack, schema…) are passed in via the
# `contracts` parameter and included in the user message, not the system prompt.
_GOVERNANCE_FILES = [
    "builder_contract.md",
]

# File extension → language name (exported so callers can reuse without duplication)
LANGUAGE_MAP = {
    ".py": "python", ".ts": "typescript", ".tsx": "typescript",
    ".js": "javascript", ".jsx": "javascript", ".sql": "sql",
    ".json": "json", ".yaml": "yml", ".yml": "yml",
    ".toml": "toml", ".md": "markdown", ".html": "html",
    ".css": "css", ".sh": "bash", ".ps1": "powershell",
}

# ---------------------------------------------------------------------------
# Governance contract loader (cached block)
# ---------------------------------------------------------------------------

def _load_governance_contracts() -> str:
    """
    Concatenate governance contracts from FORGE_CONTRACTS_DIR into a string.

    These are loaded at planning time. If FORGE_CONTRACTS_DIR doesn't exist,
    we return an empty string (build continues without governance context —
    this is a misconfiguration, not a fatal error at the agent level).
    """
    if not FORGE_CONTRACTS_DIR.exists():
        logger.warning(
            "FORGE_CONTRACTS_DIR not found: %s — running without governance contracts",
            FORGE_CONTRACTS_DIR,
        )
        return ""

    parts: list[str] = []
    for filename in _GOVERNANCE_FILES:
        path = FORGE_CONTRACTS_DIR / filename
        if path.exists():
            content = path.read_text(encoding="utf-8")
            parts.append(f"=== FORGE CONTRACT: {filename} ===\n{content}\n")
    return "\n".join(parts)


def _build_system_prompt() -> list[dict]:
    """
    Build the system prompt as cacheable blocks.

    Block 0: planner role (~400 tokens, not explicitly cached)
    Block 1: builder_contract.md (~7,900 tokens, cache_control=ephemeral)

    Effect: first call pays full price to write the cache.
    Subsequent calls within 5 minutes pay ~10% (cache READ).
    On a 4-8 turn planning loop this saves ~76% of governance input token cost.
    """
    role = """\
You are the ForgeGuard Phase Planner Agent.

Your job is to plan ONE phase of a multi-phase build. You will receive:
  - The phase definition (number, name, objective, deliverables)
  - The current project workspace (files already built)
  - The project's filled contracts (blueprint, stack, schema, etc.)
  - The prior-phase context (what was built in phases 0 to N-1)

You must produce:
  1. A FILE MANIFEST — every file to create or modify in this phase
  2. A CHUNK PLAN — how to group those files for parallel Opus builders

TOOLS AVAILABLE:
  read_file(path)          — read a file in the project workspace
  list_directory(path)     — list a directory in the project workspace
  write_phase_plan(...)    — output manifest + chunks and end the session

WORKFLOW:
  Step 1. Use list_directory to understand the current workspace structure.
  Step 2. Read specific files if needed (imports, interfaces, prior phase output).
  Step 3. When you have enough context, call write_phase_plan.

MANIFEST RULES (builder_contract.md §9):
  - List every file the builder must CREATE or MODIFY in this phase.
  - Do NOT include files from prior phases unless this phase modifies them.
  - Every database table must be claimed by exactly one phase (§9.6).
  - If this phase imports from a prior phase, the manifest should NOT re-build those files.
  - Assign each file a layer: "router" | "service" | "repo" | "llm" | "config" | "test"
  - Set action: "create" or "modify"

CHUNK RULES:
  - Group files into 1-6 chunks of 2-5 files each.
  - foundation/config files go first (chunk 0).
  - Tests go last (final chunk).
  - Each chunk gets a work_order with objective, constraints (1-4), patterns (0-2),
    success_criteria (1-3).

Do NOT call write_phase_plan with an incomplete or partial plan.
The manifest must cover all deliverables for this phase.
"""

    governance = _load_governance_contracts()

    if governance:
        return [
            {"type": "text", "text": role},
            {
                "type": "text",
                "text": f"=== FORGE GOVERNANCE CONTRACTS ===\n\n{governance}",
                # Cache boundary: everything above is frozen for 5 minutes.
                # 90% discount on all subsequent calls in the same planning session.
                "cache_control": {"type": "ephemeral"},
            },
        ]
    else:
        # No governance contracts — return single uncached block
        return [{"type": "text", "text": role}]


# ---------------------------------------------------------------------------
# Tool definitions (what Claude sees)
# ---------------------------------------------------------------------------

_TOOL_DEFINITIONS: list[dict] = [
    {
        "name": "read_file",
        "description": (
            "Read the contents of a file in the project workspace. "
            "Large files (>8,000 chars) are truncated. "
            "Returns file content and size info."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Path relative to the project working directory.",
                }
            },
            "required": ["path"],
        },
    },
    {
        "name": "list_directory",
        "description": (
            "List the contents of a directory in the project workspace. "
            "Returns files and subdirectories."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Path relative to the project working directory. "
                                   "Use '.' for the root.",
                }
            },
            "required": ["path"],
        },
    },
    {
        "name": "write_phase_plan",
        "description": (
            "Output the completed phase plan (manifest + chunks). "
            "ONLY call this when your plan is fully complete. "
            "This terminates the planning session. "
            "Validation errors are returned so you can fix and retry if needed."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "manifest": {
                    "type": "array",
                    "description": "List of files for this phase.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "path":           {"type": "string", "description": "File path relative to project root"},
                            "action":         {"type": "string", "enum": ["create", "modify"], "description": "create or modify"},
                            "purpose":        {"type": "string", "description": "One sentence: what this file does"},
                            "depends_on":     {"type": "array",  "items": {"type": "string"}, "description": "Other files in THIS phase that must exist first"},
                            "estimated_lines":{"type": "integer","description": "Rough line count estimate"},
                        },
                        "required": ["path", "action", "purpose"],
                    },
                },
                "chunks": {
                    "type": "array",
                    "description": "Build chunk plan — groups of files for parallel Opus builders.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name":  {"type": "string", "description": "Short descriptive chunk name"},
                            "files": {"type": "array", "items": {"type": "string"}, "description": "File paths in this chunk (must be in manifest)"},
                            "work_order": {
                                "type": "object",
                                "properties": {
                                    "objective":         {"type": "string"},
                                    "constraints":       {"type": "array", "items": {"type": "string"}},
                                    "patterns":          {"type": "array", "items": {"type": "string"}},
                                    "success_criteria":  {"type": "array", "items": {"type": "string"}},
                                },
                                "required": ["objective"],
                            },
                        },
                        "required": ["name", "files", "work_order"],
                    },
                },
            },
            "required": ["manifest", "chunks"],
        },
    },
]

# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------

_MAX_FILE_CHARS = 8_000


def _tool_read_file(path: str, working_dir: str) -> dict:
    """Sandbox-safe file reader. Restricted to working_dir."""
    resolved = (Path(working_dir) / path).resolve()
    root = Path(working_dir).resolve()
    try:
        resolved.relative_to(root)
    except ValueError:
        return {"error": "Access denied: path is outside the project directory."}

    if not resolved.exists():
        return {"error": f"File not found: {path}"}
    if resolved.is_dir():
        return {"error": f"{path} is a directory — use list_directory."}
    try:
        content = resolved.read_text(encoding="utf-8")
        total = len(content)
        truncated = total > _MAX_FILE_CHARS
        return {
            "content": content[:_MAX_FILE_CHARS] if truncated else content,
            "size_chars": total,
            "truncated": truncated,
        }
    except Exception as exc:
        return {"error": f"Read failed: {exc}"}


def _tool_list_directory(path: str, working_dir: str) -> dict:
    """Sandbox-safe directory lister. Restricted to working_dir."""
    resolved = (Path(working_dir) / path).resolve()
    root = Path(working_dir).resolve()
    try:
        resolved.relative_to(root)
    except ValueError:
        return {"error": "Access denied: path is outside the project directory."}

    if not resolved.exists():
        return {"error": f"Directory not found: {path}"}
    if not resolved.is_dir():
        return {"error": f"{path} is not a directory — use read_file."}
    try:
        entries = sorted(resolved.iterdir(), key=lambda e: (e.is_file(), e.name))
        _skip = {"__pycache__", ".git", "node_modules", ".venv", "Forge"}
        return {
            "path": path,
            "directories": [e.name for e in entries if e.is_dir() and e.name not in _skip],
            "files": [e.name for e in entries if e.is_file()],
        }
    except Exception as exc:
        return {"error": f"List failed: {exc}"}


def _validate_and_enrich_plan(
    manifest_raw: list[dict],
    chunks_raw: list[dict],
) -> tuple[list[dict], list[dict], list[str]]:
    """
    Validate and enrich the model's raw plan output.

    Returns: (manifest, chunks, errors)
      - errors is non-empty if validation failed
      - manifest has status, language, context_files auto-added
      - chunks have entries auto-populated from manifest
    """
    errors: list[str] = []

    # --- Validate + enrich manifest ---
    if not isinstance(manifest_raw, list) or not manifest_raw:
        errors.append("manifest must be a non-empty list")
        return [], [], errors

    manifest_paths: set[str] = set()
    manifest: list[dict] = []
    for i, entry in enumerate(manifest_raw):
        if not isinstance(entry, dict):
            errors.append(f"manifest[{i}] is not an object")
            continue
        path = entry.get("path", "")
        action = entry.get("action", "create")
        if not path:
            errors.append(f"manifest[{i}] missing 'path'")
            continue
        if action not in ("create", "modify", "delete"):
            errors.append(f"manifest[{i}].action must be 'create', 'modify', or 'delete'")
        ext = Path(path).suffix.lower()
        manifest.append({
            "path": path,
            "action": action,
            "purpose": entry.get("purpose", ""),
            "depends_on": entry.get("depends_on", []),
            "context_files": entry.get("depends_on", []),  # builder uses depends_on as context
            "estimated_lines": int(entry.get("estimated_lines", 100)),
            "language": LANGUAGE_MAP.get(ext, "python"),
            "status": "pending",
        })
        manifest_paths.add(path)

    if errors:
        return [], [], errors

    # --- Validate + enrich chunks ---
    if not isinstance(chunks_raw, list) or not chunks_raw:
        errors.append("chunks must be a non-empty list")
        return manifest, [], errors

    chunks: list[dict] = []
    for i, chunk in enumerate(chunks_raw):
        if not isinstance(chunk, dict):
            errors.append(f"chunks[{i}] is not an object")
            continue
        name = chunk.get("name", f"Chunk {i}")
        files = chunk.get("files", [])
        work_order = chunk.get("work_order", {})

        # Verify all chunk files are in manifest
        missing = [f for f in files if f not in manifest_paths]
        if missing:
            errors.append(
                f"chunks[{i}] references files not in manifest: {missing}"
            )
            continue

        if not isinstance(work_order, dict):
            work_order = {"objective": f"Build {name}"}

        chunks.append({
            "name": name,
            "files": files,
            "entries": [e for e in manifest if e["path"] in files],
            "builder_prompt": "",  # deprecated field, kept for compatibility
            "work_order": {
                "objective": work_order.get("objective", f"Build {name}"),
                "constraints": work_order.get("constraints", []),
                "patterns": work_order.get("patterns", []),
                "success_criteria": work_order.get("success_criteria", []),
            },
        })

    # Verify all manifest files are in at least one chunk
    chunked_paths = {f for c in chunks for f in c["files"]}
    unchunked = manifest_paths - chunked_paths
    if unchunked:
        errors.append(
            f"These manifest files are not assigned to any chunk: {sorted(unchunked)}. "
            "Add them to an existing chunk or create a new chunk."
        )

    return manifest, chunks, errors


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _content_to_params(content: list) -> list[dict]:
    """
    Convert Anthropic SDK content block objects to plain dicts.

    The SDK returns typed Pydantic objects (TextBlock, ToolUseBlock, etc.).
    Storing them directly in message history and re-sending causes
    PydanticSerializationError on the next API call (model_dump(by_alias=True)
    fails when a field value is None). Converting to plain dicts avoids this.
    """
    params = []
    for block in content:
        if block.type == "text":
            params.append({"type": "text", "text": block.text})
        elif block.type == "tool_use":
            params.append({
                "type": "tool_use",
                "id": block.id,
                "name": block.name,
                "input": block.input,
            })
        elif block.type == "thinking":
            # Thinking blocks MUST be echoed back with their signature.
            params.append({
                "type": "thinking",
                "thinking": block.thinking,
                "signature": block.signature,
            })
    return params


# ---------------------------------------------------------------------------
# The agentic loop
# ---------------------------------------------------------------------------

async def run_phase_planner_agent(
    *,
    build_id: UUID,
    user_id: UUID,
    api_key: str,
    contracts: list[dict],
    phase: dict,
    workspace_info: str,
    working_dir: str,
    prior_phase_context: str = "",
) -> dict | None:
    """
    Run the Phase Planner Agent for a single build phase.

    Replaces _generate_file_manifest() + _plan_phase_chunks() with one
    agentic loop that plans both manifest and chunks together.

    Args:
        build_id, user_id: ForgeGuard telemetry + WebSocket broadcast
        api_key:           Anthropic API key
        contracts:         Project-specific contracts (blueprint, stack, etc.)
        phase:             Phase dict with number, name, objective, deliverables
        workspace_info:    Current file tree (markdown string)
        working_dir:       Absolute path to the project root (for tool sandbox)
        prior_phase_context: Markdown summary of files built in prior phases

    Returns:
        {"manifest": list[dict], "chunks": list[dict]} on success
        None on failure (caller handles retry/pause)
    """
    phase_name = f"Phase {phase.get('number', '?')}"
    phase_label = f"{phase_name} ({phase.get('name', '')})"

    await _state._broadcast_build_event(user_id, build_id, "build_log", {
        "message": f"[Sonnet] Planner agent starting for {phase_label}",
        "source": "planner", "level": "info",
    })

    # 30s timeout per API call — prevents threads from hanging indefinitely
    # after the async task is cancelled (Python cannot kill threads, so an
    # uncancellable API call would keep the thread alive and charging).
    client = anthropic.Anthropic(api_key=api_key, timeout=30.0)
    system_blocks = _build_system_prompt()

    # --- Append project contracts to system prompt (cached across turns) ---
    # Project contracts are stable for the entire phase planning session.
    # Putting them in the system prompt means turns 2+ pay ~10% via cache READ
    # instead of full price on every turn in the user message.
    contract_parts: list[str] = []
    for c in contracts:
        ctype = c.get("contract_type", "")
        content = c.get("content", "")
        if ctype and content:
            contract_parts.append(f"## Project Contract: {ctype}\n{content}\n")
    if contract_parts:
        system_blocks.append({
            "type": "text",
            "text": "=== PROJECT CONTRACTS ===\n\n" + "\n".join(contract_parts),
            "cache_control": {"type": "ephemeral"},
        })

    # --- Deliverables text ---
    phase_deliverables = (
        f"Phase {phase.get('number', '?')} — {phase.get('name', '')}\n"
        f"Objective: {phase.get('objective', '')}\n"
        "Deliverables:\n"
        + "\n".join(f"  - {d}" for d in phase.get("deliverables", []))
    )

    # --- Prior phase context ---
    prior_section = ""
    if prior_phase_context:
        prior_section = f"\n## Prior Phase Context\n{prior_phase_context}\n"

    # --- Initial user message (lightweight — contracts are in system prompt) ---
    initial_message = f"""\
Plan the file manifest and chunk breakdown for the following phase.

{phase_deliverables}

## Current Workspace
{workspace_info or "(empty workspace)"}
{prior_section}
INSTRUCTIONS:
1. Call list_directory(".") to survey the workspace root.
2. Read relevant existing files if you need to understand current interfaces.
3. When ready, call write_phase_plan with your complete manifest and chunks.

The manifest must cover ALL deliverables above. The builder will produce
no files beyond what you list.
"""

    messages: list[dict] = [{"role": "user", "content": initial_message}]

    _model = _get_model()
    _thinking_budget = _get_thinking_budget()
    _thinking_model = _get_thinking_model()   # Sonnet fallback when Haiku is forced

    # --- Token accounting ---
    total_input = 0
    total_output = 0
    total_cache_read = 0
    total_cache_write = 0

    plan_result: dict | None = None
    iteration = 0

    # ════════════════════════════════════════════════════════════════════════
    # THE AGENTIC LOOP
    # ════════════════════════════════════════════════════════════════════════
    while iteration < _MAX_ITERATIONS:
        iteration += 1

        # (1) Inference
        _api_model = _thinking_model if _thinking_budget > 0 else _model
        await _state._broadcast_build_event(user_id, build_id, "llm_thinking", {
            "phase": phase_name,
            "model": _api_model,
            "turn": iteration,
            "source": "planner",
            # These fields drive the frontend thinking panel display.
            # Without them the UI falls back to "🧠 Processing... 0 tokens".
            "purpose": f"Planning {phase_label}: manifest + chunks (turn {iteration})",
            "user_message_preview": initial_message[:800],
            "user_message_length": len(initial_message),
            "extended_thinking": _thinking_budget > 0,
            "thinking_budget": _thinking_budget,
            "thinking_model": _thinking_model if _thinking_budget > 0 else None,
        })

        try:
            _create_kwargs: dict = dict(
                model=_api_model,
                max_tokens=max(_MAX_TOKENS, _thinking_budget + 4096) if _thinking_budget > 0 else _MAX_TOKENS,
                system=system_blocks,
                tools=_TOOL_DEFINITIONS,
                messages=messages,
            )
            if _thinking_budget > 0:
                _create_kwargs["thinking"] = {"type": "enabled", "budget_tokens": _thinking_budget}

            response = client.messages.create(**_create_kwargs)
        except Exception as exc:
            logger.error("Planner agent API error (turn %d): %s", iteration, exc)
            await _state._broadcast_build_event(user_id, build_id, "build_log", {
                "message": f"[Sonnet] Planner API error on turn {iteration}: {exc}",
                "source": "planner", "level": "error",
            })
            return None

        # (2) Accumulate token usage
        u = response.usage
        total_input += u.input_tokens
        total_output += u.output_tokens
        total_cache_read += getattr(u, "cache_read_input_tokens", 0)
        total_cache_write += getattr(u, "cache_creation_input_tokens", 0)

        # (2b) Surface extended thinking blocks to the UI when present
        _tblocks = [b for b in response.content if b.type == "thinking"]
        if _tblocks:
            _combined = "\n\n".join(b.thinking for b in _tblocks)
            await _state._broadcast_build_event(user_id, build_id, "thinking_block", {
                "turn": iteration,
                "source": "planner",
                "phase": phase_name,
                "reasoning_text": _combined[:4000],
                "reasoning_length": len(_combined),
            })

        # (3) Append assistant turn BEFORE dispatching tools.
        # Convert SDK Pydantic objects to plain dicts — storing SDK objects
        # directly causes a PydanticSerializationError on the next API call
        # when the SDK calls model_dump(by_alias=True) and a field is None.
        messages.append({"role": "assistant", "content": _content_to_params(response.content)})

        # (4) Handle end_turn without write_phase_plan — protocol violation
        if response.stop_reason == "end_turn":
            logger.warning("Planner used end_turn without write_phase_plan (turn %d)", iteration)
            messages.append({
                "role": "user",
                "content": (
                    "You did not call write_phase_plan. "
                    "Please call it now with your complete manifest and chunks, "
                    "or call read_file / list_directory if you need more information."
                ),
            })
            continue

        # (5) Dispatch tool calls
        tool_results: list[dict] = []
        should_exit = False

        for block in response.content:
            if block.type != "tool_use":
                continue

            tool_name = block.name
            tool_input = block.input

            await _state._broadcast_build_event(user_id, build_id, "build_log", {
                "message": f"[Sonnet] Planner tool: {tool_name}({list(tool_input.keys())})",
                "source": "planner", "level": "info",
            })

            if tool_name == "read_file":
                result = _tool_read_file(tool_input.get("path", ""), working_dir)

            elif tool_name == "list_directory":
                result = _tool_list_directory(tool_input.get("path", "."), working_dir)

            elif tool_name == "write_phase_plan":
                manifest_raw = tool_input.get("manifest", [])
                chunks_raw = tool_input.get("chunks", [])
                manifest, chunks, val_errors = _validate_and_enrich_plan(manifest_raw, chunks_raw)

                if val_errors:
                    result = {
                        "success": False,
                        "errors": val_errors,
                        "message": (
                            f"Plan validation failed ({len(val_errors)} error(s)). "
                            "Fix all errors and call write_phase_plan again."
                        ),
                    }
                    logger.warning("Planner plan validation failed: %s", val_errors)
                else:
                    plan_result = {"manifest": manifest, "chunks": chunks}
                    should_exit = True
                    result = {
                        "success": True,
                        "manifest_count": len(manifest),
                        "chunk_count": len(chunks),
                        "message": "Plan accepted.",
                    }

            else:
                result = {"error": f"Unknown tool: {tool_name!r}"}

            tool_results.append({
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": json.dumps(result),
            })

        # (6) Inject tool results as user turn
        if tool_results:
            messages.append({"role": "user", "content": tool_results})

        # (7) Exit on successful write_phase_plan
        if should_exit:
            break

    else:
        logger.error(
            "Planner agent exceeded %d iterations for %s — no plan produced",
            _MAX_ITERATIONS, phase_label,
        )
        await _state._broadcast_build_event(user_id, build_id, "build_log", {
            "message": (
                f"[Sonnet] Planner agent exceeded max iterations for {phase_label}. "
                "No plan produced."
            ),
            "source": "planner", "level": "error",
        })
        return None
    # ════════════════════════════════════════════════════════════════════════
    # END OF LOOP
    # ════════════════════════════════════════════════════════════════════════

    # Record token cost
    try:
        input_rate, output_rate = _get_token_rates(_model)
        effective_input = total_input + total_cache_write + int(total_cache_read * 0.1)
        cost = (
            Decimal(effective_input) * input_rate
            + Decimal(total_output) * output_rate
        )
        await _state.build_repo.record_build_cost(
            build_id, f"planner_agent_{phase_name}",
            total_input, total_output, _model, cost,
        )
        await _accumulate_cost(build_id, total_input, total_output, _model, cost)
        await _state._broadcast_build_event(user_id, build_id, "build_log", {
            "message": (
                f"[Sonnet] Planner agent done: {len(plan_result['manifest'])} files, "
                f"{len(plan_result['chunks'])} chunks | "
                f"tokens: {total_input:,} in / {total_output:,} out | "
                f"cache saved: {total_cache_read:,} | "
                f"cost: ${float(cost):.4f}"
            ),
            "source": "planner", "level": "info",
        })
    except Exception as exc:
        logger.warning("Failed to record planner cost: %s", exc)

    return plan_result
