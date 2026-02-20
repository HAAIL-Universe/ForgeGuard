"""Sub-agent handoff protocol — typed roles, per-role tool sets, context packs.

This module formalises the *principle of least privilege* for LLM sub-agents.
Instead of giving every agent the full ``BUILDER_TOOLS`` list, each role
receives **only** the tools it needs:

* **Scout** — read-only reconnaissance (read_file, list_directory, search_code,
  forge contracts, scratchpad).
* **Coder** — file creation plus syntax checking.  Cannot run tests (lets the
  test step catch problems without consuming LLM tokens).
* **Auditor** — read-only structural/quality review.  Identical tool surface
  to Scout (plus scratchpad write) so it cannot *fix* the code itself.
* **Fixer** — surgical edits only.  Uses ``edit_file`` but **not**
  ``write_file`` so it cannot accidentally overwrite an entire file.

The ``SubAgentHandoff`` dataclass carries everything a sub-agent invocation
needs, and the ``SubAgentResult`` captures its output.

Usage::

    from app.services.build.subagent import (
        SubAgentRole, SubAgentHandoff, SubAgentResult,
        tools_for_role, system_prompt_for_role,
    )
"""

from __future__ import annotations

import enum
import json
import logging
import time
from dataclasses import dataclass, field, asdict
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID

from app.clients.agent_client import ApiKeyPool, StreamUsage, ToolCall, stream_agent
from app.services.tool_executor import BUILDER_TOOLS, execute_tool_async
from app.services.build.cost import _get_token_rates
from . import _state

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class SubAgentRole(str, enum.Enum):
    """Named roles with distinct tool allowlists."""

    SCOUT = "scout"
    CODER = "coder"
    AUDITOR = "auditor"
    FIXER = "fixer"


class HandoffStatus(str, enum.Enum):
    """Lifecycle state of a sub-agent handoff."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


# ---------------------------------------------------------------------------
# Per-role tool allow-lists
# ---------------------------------------------------------------------------

# Maps each role to the set of tool *names* it is allowed to use.
# The conductor picks the right set via ``tools_for_role(role)``.

_ROLE_TOOL_NAMES: dict[SubAgentRole, frozenset[str]] = {
    # Scout — pure read for reconnaissance / context gathering
    SubAgentRole.SCOUT: frozenset({
        "read_file",
        "list_directory",
        "search_code",
        "forge_get_contract",
        "forge_get_phase_window",
        "forge_list_contracts",
        "forge_get_summary",
        "forge_scratchpad",
        "forge_ask_clarification",
        # Project-scoped tools (Phase F) — pull contracts from DB on demand
        "forge_get_project_context",
        "forge_list_project_contracts",
        "forge_get_project_contract",
    }),
    # Coder — write new files + syntax check.  No tests, no run_command
    # (auto-install is handled by the tool_executor post-write hook).
    SubAgentRole.CODER: frozenset({
        "read_file",
        "list_directory",
        "search_code",
        "write_file",
        "edit_file",
        "check_syntax",
        "run_command",
        "forge_get_contract",
        "forge_get_phase_window",
        "forge_list_contracts",
        "forge_get_summary",
        "forge_scratchpad",
        "forge_ask_clarification",
        # Project-scoped tools (Phase F) — pull stack, physics, boundaries on demand
        "forge_get_project_contract",
        "forge_list_project_contracts",
    }),
    # Auditor — read-only structural review, same surface as scout
    SubAgentRole.AUDITOR: frozenset({
        "read_file",
        "list_directory",
        "search_code",
        "forge_get_contract",
        "forge_list_contracts",
        "forge_get_summary",
        "forge_scratchpad",
        # Project-scoped tools (Phase F) — fetch boundaries + physics for compliance checks
        "forge_get_project_contract",
        "forge_list_project_contracts",
    }),
    # Fixer — edit_file only (no write_file).  Can read + check syntax.
    SubAgentRole.FIXER: frozenset({
        "read_file",
        "list_directory",
        "search_code",
        "edit_file",
        "check_syntax",
        "forge_scratchpad",
        # Project-scoped tools (Phase F) — pinned snapshot only (immutable reference)
        "forge_get_build_contracts",
    }),
}


def tools_for_role(
    role: SubAgentRole,
    all_tools: list[dict] | None = None,
) -> list[dict]:
    """Return the filtered tool definitions for *role*.

    Parameters
    ----------
    role:
        The sub-agent role whose allow-list will be applied.
    all_tools:
        Full tool list (defaults to ``BUILDER_TOOLS`` from tool_executor).

    Returns
    -------
    list[dict]
        Only the tool dicts whose ``name`` is in the role's allow-list,
        preserving the original order.
    """
    if all_tools is None:
        all_tools = BUILDER_TOOLS

    allowed = _ROLE_TOOL_NAMES.get(role, frozenset())
    return [t for t in all_tools if t["name"] in allowed]


def tool_names_for_role(role: SubAgentRole) -> frozenset[str]:
    """Return the raw set of allowed tool names for *role*."""
    return _ROLE_TOOL_NAMES.get(role, frozenset())


# ---------------------------------------------------------------------------
# System prompt templates per role
# ---------------------------------------------------------------------------

_ROLE_SYSTEM_PROMPTS: dict[SubAgentRole, str] = {
    SubAgentRole.SCOUT: (
        "You are a **Scout** sub-agent in the Forge build system.\n\n"
        "Your job is to gather context about the project before coding begins. "
        "You have READ-ONLY access to the project files and governance contracts.\n\n"
        "## Contract Pull Model (Phase F)\n"
        "Project-specific contracts are stored in the ForgeGuard database. "
        "Pull only what you need — do NOT assume the generic templates apply:\n"
        "1. Call `forge_get_project_context()` first to see which contracts are available.\n"
        "2. Call `forge_get_project_contract('manifesto')` to understand the project's goals and ethos.\n"
        "3. Call `forge_get_project_contract('stack')` to understand the required tech stack.\n"
        "4. Call `forge_list_project_contracts()` if you need to check other available types.\n\n"
        "Tasks:\n"
        "- Map the existing directory structure\n"
        "- Identify key interfaces, imports, and patterns\n"
        "- Read relevant contracts (blueprint, stack, schema, boundaries)\n"
        "- Summarise what the coder needs to know\n\n"
        "Output a structured JSON object with your findings:\n"
        "```json\n"
        '{\n  "directory_tree": "...",\n  "key_interfaces": [...],\n'
        '  "patterns": {...},\n  "imports_map": {...},\n'
        '  "recommendations": "..."\n}\n```\n\n'
        "Rules:\n"
        "- Do NOT create, modify, or delete any files\n"
        "- Do NOT run tests or commands\n"
        "- Focus on accuracy over speed\n"
        "- Keep your summary under 4000 tokens\n"
    ),
    SubAgentRole.CODER: (
        "You are a **Coder** sub-agent in the Forge build system.\n\n"
        "You write production-quality code for specific files assigned to you. "
        "You have access to file creation tools and syntax checking.\n\n"
        "## Contract Pull Model — IMPORTANT\n"
        "Your assignment includes a contract INDEX (types + previews), NOT full text. "
        "You MUST pull the contracts you need via tools before writing any code:\n"
        "1. Call `forge_get_project_contract('stack')` — required languages, "
        "frameworks, and versions. Your code MUST match these exactly.\n"
        "2. Call `forge_get_project_contract('physics')` — the canonical API "
        "spec (endpoints, auth, request/response schemas).\n"
        "3. Call `forge_get_project_contract('boundaries')` — layer boundary "
        "rules (which imports are forbidden in each layer).\n"
        "4. Call `forge_get_project_contract('schema')` if your files touch data models.\n"
        "Pull ONLY the contracts relevant to your assigned files. "
        "Do NOT pull all contracts — be selective.\n\n"
        "Rules:\n"
        "- Write ONLY the files specified in your assignment\n"
        "- Follow the project contracts exactly\n"
        "- Respect layer boundaries (routers -> services -> repos -> clients)\n"
        "- Include type hints and proper error handling\n"
        "- Check syntax after writing each file\n"
        "- Use the context provided — do not re-read the whole project\n"
        "- Do NOT run tests (the test step handles that separately)\n\n"
        "## Code Style — CRITICAL\n"
        "- Output PURE CODE only. No tutorial prose, no narrative paragraphs between functions.\n"
        "- Docstrings: one-line only (e.g. `\"\"\"Fetch user by ID.\"\"\"`) — NEVER multi-line explanatory docstrings.\n"
        "- Comments: only where logic is non-obvious. No 'this function does X' comments.\n"
        "- No module-level essays or section headers with long explanations.\n"
        "- Every token of output costs money — be maximally concise.\n\n"
        "After writing all assigned files, output a brief summary:\n"
        "```json\n"
        '{\n  "files_written": [...],\n  "decisions": "...",\n'
        '  "known_issues": "..."\n}\n```\n'
    ),
    SubAgentRole.AUDITOR: (
        "You are an **Auditor** sub-agent in the Forge build system.\n\n"
        "You perform structural quality review of generated code. "
        "You have READ-ONLY access — you cannot modify any files.\n\n"
        "## Contract Pull Model (Phase F)\n"
        "Project-specific contracts define the compliance bar. Pull them before reviewing:\n"
        "1. Call `forge_get_project_contract('boundaries')` — layer boundary rules "
        "that all code must respect (no skipping layers, forbidden imports per layer).\n"
        "2. Call `forge_get_project_contract('physics')` — the canonical API spec; "
        "verify every endpoint shape, auth method, and response schema against this.\n"
        "3. Call `forge_list_project_contracts()` if schema or ui contracts may be relevant.\n\n"
        "Check for:\n"
        "- Missing or broken imports/exports\n"
        "- Functions/classes referenced but never defined\n"
        "- Contract violations (layer boundaries, naming, API shape)\n"
        "- Obvious logic errors or unreachable code\n"
        "- File doesn't match its stated purpose\n\n"
        "Do NOT flag: style preferences, naming conventions, missing docs, "
        "optional improvements.\n\n"
        "For each file, output:\n"
        "```json\n"
        '{\n  "path": "...",\n  "verdict": "PASS|FAIL",\n'
        '  "findings": [\n    {"line": 42, "severity": "error", "message": "..."}\n'
        "  ]\n}\n```\n\n"
        "If the file is structurally sound, set verdict to PASS with empty findings.\n"
    ),
    SubAgentRole.FIXER: (
        "You are a **Fixer** sub-agent in the Forge build system.\n\n"
        "You apply targeted, surgical fixes to files that failed audit. "
        "You can use ``edit_file`` to patch specific lines — you CANNOT use "
        "``write_file`` (no full rewrites).\n\n"
        "## Contract Pull Model (Phase F)\n"
        "Before applying any fix, retrieve the pinned, immutable contract snapshot "
        "that was in effect when this build started:\n"
        "1. Call `forge_get_build_contracts()` — returns the exact contracts frozen "
        "at build start. Use these as your authoritative reference. "
        "Mid-build edits to contracts do NOT affect these snapshots.\n\n"
        "Rules:\n"
        "- Fix ONLY the issues listed in the audit findings\n"
        "- Do NOT refactor, restyle, or change working code\n"
        "- Preserve all existing functionality\n"
        "- Keep the same imports, structure, and style\n"
        "- If an import is missing, add it via edit\n"
        "- Check syntax after each fix\n\n"
        "After fixing, output:\n"
        "```json\n"
        '{\n  "files_fixed": [...],\n  "edits_applied": 3,\n'
        '  "remaining_issues": "none|..."\n}\n```\n'
    ),
}


def system_prompt_for_role(
    role: SubAgentRole,
    *,
    extra: str = "",
) -> str:
    """Return the system prompt for *role*, optionally appending *extra*."""
    base = _ROLE_SYSTEM_PROMPTS.get(role, "")
    if extra:
        return f"{base}\n\n{extra}"
    return base


# ---------------------------------------------------------------------------
# Handoff dataclass — what gets passed to a sub-agent invocation
# ---------------------------------------------------------------------------


@dataclass
class SubAgentHandoff:
    """Everything a sub-agent invocation needs.

    The conductor creates one of these per sub-agent call, the runner
    consumes it and returns a ``SubAgentResult``.
    """

    # Identity
    role: SubAgentRole
    build_id: UUID
    user_id: UUID

    # What to do
    assignment: str  # human-readable task description
    files: list[str] = field(default_factory=list)  # target file paths

    # Context (kept slim on purpose)
    context_files: dict[str, str] = field(default_factory=dict)
    contracts_text: str = ""
    phase_deliverables: str = ""
    error_context: str = ""  # audit findings for fixer

    # Config overrides
    model: str = ""  # empty → use default for role
    max_tokens: int = 16_384
    timeout_seconds: float = 600.0

    # Metadata
    handoff_id: str = ""  # auto-assigned if empty
    parent_handoff_id: str = ""  # chain reference

    def to_dict(self) -> dict:
        """Serialise for logging / .forge persistence."""
        d = asdict(self)
        d["role"] = self.role.value
        d["build_id"] = str(self.build_id)
        d["user_id"] = str(self.user_id)
        return d


# ---------------------------------------------------------------------------
# Result dataclass — what comes back from a sub-agent invocation
# ---------------------------------------------------------------------------


@dataclass
class SubAgentResult:
    """Captures the output of a single sub-agent run."""

    handoff_id: str
    role: SubAgentRole
    status: HandoffStatus = HandoffStatus.COMPLETED

    # Output
    text_output: str = ""
    structured_output: dict = field(default_factory=dict)
    files_written: list[str] = field(default_factory=list)
    files_read: list[str] = field(default_factory=list)

    # Token accounting
    input_tokens: int = 0
    output_tokens: int = 0
    model: str = ""
    cost_usd: float = 0.0

    # Timing
    started_at: float = 0.0
    finished_at: float = 0.0
    duration_seconds: float = 0.0

    # Errors
    error: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        d["role"] = self.role.value
        d["status"] = self.status.value
        return d


# ---------------------------------------------------------------------------
# Context pack builder — assembles minimal context for a sub-agent
# ---------------------------------------------------------------------------


def build_context_pack(
    working_dir: str,
    target_files: list[str],
    *,
    max_context_files: int = 12,
    max_context_chars: int = 60_000,
    include_siblings: bool = True,
    include_imports: bool = True,
) -> dict[str, str]:
    """Assemble a minimal context dict for sub-agent consumption.

    Reads the target files + their imports + siblings, trimmed to budget.

    Parameters
    ----------
    working_dir:
        Absolute path to the project root.
    target_files:
        Relative paths of files the sub-agent will work on.
    max_context_files:
        Maximum number of context files to include.
    max_context_chars:
        Total character budget across all context files.
    include_siblings:
        Whether to include sibling files in the same directories.
    include_imports:
        Whether to parse and include imported modules.

    Returns
    -------
    dict[str, str]
        ``{relative_path: file_content}`` trimmed to budget.
    """
    import re as _re

    wd = Path(working_dir)
    ctx: dict[str, str] = {}
    seen: set[str] = set()

    def _add_file(rel_path: str) -> bool:
        """Try to add a file to context.  Returns True if added."""
        if rel_path in seen or len(ctx) >= max_context_files:
            return False
        seen.add(rel_path)
        fp = wd / rel_path
        if not fp.exists() or not fp.is_file():
            return False
        try:
            content = fp.read_text(encoding="utf-8", errors="replace")
        except Exception:
            return False
        # Skip very large files
        if len(content) > 30_000:
            content = content[:30_000] + "\n[... truncated ...]\n"
        ctx[rel_path] = content
        return True

    # 1. Target files first (always included)
    for tf in target_files:
        _add_file(tf.replace("\\", "/"))

    # 2. Parse imports from target files
    if include_imports:
        for tf in target_files:
            content = ctx.get(tf.replace("\\", "/"), "")
            for imp_match in _re.finditer(r'(?:from|import)\s+([\w.]+)', content):
                mod = imp_match.group(1)
                # Convert dotted module to relative path
                mod_path = mod.replace(".", "/") + ".py"
                _add_file(mod_path)
                # Also try as package __init__
                pkg_path = mod.replace(".", "/") + "/__init__.py"
                _add_file(pkg_path)

    # 3. Sibling files in the same directories
    if include_siblings:
        dirs_seen: set[str] = set()
        for tf in target_files:
            rel = tf.replace("\\", "/")
            parent = str(Path(rel).parent)
            if parent in dirs_seen:
                continue
            dirs_seen.add(parent)
            parent_abs = wd / parent
            if not parent_abs.exists():
                continue
            for sibling in sorted(parent_abs.iterdir()):
                if len(ctx) >= max_context_files:
                    break
                if sibling.is_file() and sibling.suffix in (
                    ".py", ".ts", ".tsx", ".js", ".jsx",
                ):
                    rel_sib = str(sibling.relative_to(wd)).replace("\\", "/")
                    _add_file(rel_sib)

    # 4. Trim to character budget
    total = sum(len(v) for v in ctx.values())
    if total > max_context_chars:
        trimmed: dict[str, str] = {}
        running = 0
        # Prioritise target files
        for tf in target_files:
            key = tf.replace("\\", "/")
            if key in ctx:
                trimmed[key] = ctx[key]
                running += len(ctx[key])
        # Add remaining until budget hit
        for k, v in ctx.items():
            if k in trimmed:
                continue
            if running + len(v) > max_context_chars:
                break
            trimmed[k] = v
            running += len(v)
        return trimmed

    return ctx


# ---------------------------------------------------------------------------
# .forge directory management
# ---------------------------------------------------------------------------

_FORGE_DIR = ".forge"
_HANDOFFS_DIR = f"{_FORGE_DIR}/handoffs"
_PROGRESS_FILE = f"{_FORGE_DIR}/progress.json"


def ensure_forge_dir(working_dir: str) -> Path:
    """Create the .forge/ directory structure if it doesn't exist."""
    forge = Path(working_dir) / _FORGE_DIR
    (forge / "handoffs").mkdir(parents=True, exist_ok=True)
    return forge


def save_handoff(working_dir: str, handoff: SubAgentHandoff) -> Path:
    """Persist a handoff to .forge/handoffs/ for debugging / replay."""
    ensure_forge_dir(working_dir)
    fp = Path(working_dir) / _HANDOFFS_DIR / f"{handoff.handoff_id}.json"
    fp.write_text(json.dumps(handoff.to_dict(), indent=2, default=str), encoding="utf-8")
    return fp


def save_result(working_dir: str, result: SubAgentResult) -> Path:
    """Persist a result alongside its handoff."""
    ensure_forge_dir(working_dir)
    fp = Path(working_dir) / _HANDOFFS_DIR / f"{result.handoff_id}_result.json"
    fp.write_text(json.dumps(result.to_dict(), indent=2, default=str), encoding="utf-8")
    return fp


def load_progress(working_dir: str) -> dict:
    """Load .forge/progress.json (or empty dict)."""
    fp = Path(working_dir) / _PROGRESS_FILE
    if fp.exists():
        try:
            return json.loads(fp.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def save_progress(working_dir: str, data: dict) -> None:
    """Save .forge/progress.json."""
    ensure_forge_dir(working_dir)
    fp = Path(working_dir) / _PROGRESS_FILE
    fp.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")


# ---------------------------------------------------------------------------
# Sub-agent runner — the core invocation function
# ---------------------------------------------------------------------------


async def run_sub_agent(
    handoff: SubAgentHandoff,
    working_dir: str,
    api_key: str,
    *,
    key_pool: Any | None = None,
) -> SubAgentResult:
    """Execute a sub-agent with its role-specific tools and context.

    This is the single entry point the conductor uses to dispatch work.
    It:
    1. Selects tools for the role
    2. Builds system prompt
    3. Assembles context into the user message
    4. Streams the agent (with tool execution loop)
    5. Returns a structured result

    Parameters
    ----------
    handoff:
        The ``SubAgentHandoff`` describing the assignment.
    working_dir:
        Absolute path to the project working directory.
    api_key:
        Anthropic API key (fallback when key_pool is None).
    key_pool:
        Optional ``ApiKeyPool`` for multi-key rotation.

    Returns
    -------
    SubAgentResult
        Captures output, token usage, timing, and any errors.
    """
    import asyncio as _asyncio

    # Assign handoff ID if empty
    if not handoff.handoff_id:
        handoff.handoff_id = f"{handoff.role.value}_{handoff.build_id.hex[:8]}_{int(time.time())}"

    result = SubAgentResult(
        handoff_id=handoff.handoff_id,
        role=handoff.role,
        status=HandoffStatus.RUNNING,
        started_at=time.time(),
    )

    # 1. Resolve model
    model = handoff.model or _default_model_for_role(handoff.role)
    result.model = model

    # 2. Select tools
    role_tools = tools_for_role(handoff.role, BUILDER_TOOLS)
    allowed_names = tool_names_for_role(handoff.role)

    # 3. Build system prompt
    sys_prompt = system_prompt_for_role(handoff.role)

    # 4. Build user message
    parts: list[str] = []

    if handoff.contracts_text:
        parts.append(f"## Project Contracts\n{handoff.contracts_text}\n")
    if handoff.phase_deliverables:
        parts.append(f"## Phase Deliverables\n{handoff.phase_deliverables}\n")
    if handoff.context_files:
        parts.append("## Context Files\n")
        for path, content in handoff.context_files.items():
            parts.append(f"### {path}\n```\n{content}\n```\n")
    if handoff.error_context:
        parts.append(f"## Error Context / Audit Findings\n{handoff.error_context}\n")

    parts.append(f"## Assignment\n{handoff.assignment}\n")

    if handoff.files:
        parts.append("## Target Files\n" + "\n".join(f"- `{f}`" for f in handoff.files) + "\n")

    user_message = "\n".join(parts)
    messages: list[dict] = [{"role": "user", "content": user_message}]

    # 5. Broadcast start
    await _state._broadcast_build_event(
        handoff.user_id, handoff.build_id, "subagent_start", {
            "role": handoff.role.value,
            "handoff_id": handoff.handoff_id,
            "files": handoff.files,
            "assignment": handoff.assignment[:200],
        },
    )

    # Save handoff to disk for debugging
    try:
        save_handoff(working_dir, handoff)
    except Exception as exc:
        logger.debug("Could not save handoff: %s", exc)

    # 6. Stream with tool loop
    usage = StreamUsage()
    text_chunks: list[str] = []
    max_tool_rounds = 25  # safety limit
    tool_rounds = 0

    try:
        while tool_rounds < max_tool_rounds:
            tool_calls_this_round: list[dict] = []

            async for event in stream_agent(
                api_key=api_key,
                model=model,
                system_prompt=sys_prompt,
                messages=messages,
                max_tokens=handoff.max_tokens,
                usage_out=usage,
                tools=role_tools if role_tools else None,
                key_pool=key_pool,
            ):
                if isinstance(event, str):
                    text_chunks.append(event)
                elif isinstance(event, ToolCall):
                    # Enforce tool allow-list
                    if event.name not in allowed_names:
                        logger.warning(
                            "Sub-agent %s tried disallowed tool %s — blocked",
                            handoff.role.value, event.name,
                        )
                        tool_calls_this_round.append({
                            "id": event.id,
                            "name": event.name,
                            "result": f"Error: tool '{event.name}' is not available to the {handoff.role.value} role.",
                        })
                        continue

                    # Execute the allowed tool (clarification is intercepted first)
                    try:
                        if event.name == "forge_ask_clarification":
                            from app.services.build_service import _handle_clarification
                            tool_result = await _handle_clarification(
                                build_id=handoff.build_id,
                                user_id=handoff.user_id,
                                tool_input=event.input,
                            )
                        else:
                            tool_result = await execute_tool_async(
                                event.name, event.input, working_dir,
                                build_id=str(handoff.build_id),
                            )
                    except Exception as te:
                        tool_result = f"Tool error: {te}"

                    tool_calls_this_round.append({
                        "id": event.id,
                        "name": event.name,
                        "result": tool_result,
                    })

                    # Track files read/written
                    if event.name in ("read_file", "list_directory", "search_code"):
                        path = event.input.get("path", "")
                        if path and path not in result.files_read:
                            result.files_read.append(path)
                    elif event.name in ("write_file", "edit_file"):
                        path = event.input.get("path", "")
                        if path and path not in result.files_written:
                            result.files_written.append(path)

                    # Broadcast scratchpad writes to UI
                    elif event.name == "forge_scratchpad":
                        _sp_op = (event.input.get("operation") or "").lower()
                        if _sp_op in ("write", "append"):
                            _sp_key = event.input.get("key", "")
                            _sp_val = event.input.get("value", "")
                            await _state._broadcast_build_event(
                                handoff.user_id, handoff.build_id, "scratchpad_write", {
                                    "key": _sp_key,
                                    "source": "opus" if handoff.role in (SubAgentRole.CODER, SubAgentRole.FIXER) else "sonnet",
                                    "role": handoff.role.value,
                                    "summary": f"{handoff.role.value.title()} wrote to scratchpad: {_sp_key}",
                                    "content": str(_sp_val)[:2000],
                                    "full_length": len(str(_sp_val)),
                                },
                            )

            # If no tool calls, the agent is done
            if not tool_calls_this_round:
                break

            # Append assistant + tool results to messages for next round
            assistant_text = "".join(text_chunks[-50:]) if text_chunks else ""
            # Build tool_use content blocks
            tool_use_blocks = []
            for tc in tool_calls_this_round:
                tool_use_blocks.append({
                    "type": "tool_use",
                    "id": tc["id"],
                    "name": tc["name"],
                    "input": {},  # original input not stored — OK for continuation
                })

            messages.append({
                "role": "assistant",
                "content": (
                    ([{"type": "text", "text": assistant_text}] if assistant_text else [])
                    + tool_use_blocks
                ),
            })

            # Tool results
            tool_result_blocks = []
            for tc in tool_calls_this_round:
                tool_result_blocks.append({
                    "type": "tool_result",
                    "tool_use_id": tc["id"],
                    "content": str(tc["result"])[:10_000],
                })
            messages.append({"role": "user", "content": tool_result_blocks})

            tool_rounds += 1
            text_chunks.clear()

        # Success
        result.text_output = "".join(text_chunks)
        result.status = HandoffStatus.COMPLETED

    except _asyncio.TimeoutError:
        result.error = f"Sub-agent timed out after {handoff.timeout_seconds}s"
        result.status = HandoffStatus.FAILED
        logger.warning("Sub-agent %s timed out: %s", handoff.role.value, result.error)
    except Exception as exc:
        result.error = str(exc)
        result.status = HandoffStatus.FAILED
        logger.error("Sub-agent %s failed: %s", handoff.role.value, exc, exc_info=True)

    # 7. Finalise result
    result.finished_at = time.time()
    result.duration_seconds = result.finished_at - result.started_at
    result.input_tokens = usage.input_tokens
    result.output_tokens = usage.output_tokens

    # Cost calculation
    input_rate, output_rate = _get_token_rates(model)
    result.cost_usd = float(
        Decimal(usage.input_tokens) * input_rate
        + Decimal(usage.output_tokens) * output_rate
    )

    # 8. Broadcast completion
    await _state._broadcast_build_event(
        handoff.user_id, handoff.build_id, "subagent_done", {
            "role": handoff.role.value,
            "handoff_id": handoff.handoff_id,
            "status": result.status.value,
            "files_written": result.files_written,
            "duration_s": round(result.duration_seconds, 1),
            "tokens": result.input_tokens + result.output_tokens,
            "error": result.error[:200] if result.error else "",
        },
    )

    # Record cost
    try:
        await _state.build_repo.record_build_cost(
            handoff.build_id,
            f"subagent:{handoff.role.value}:{handoff.handoff_id}",
            result.input_tokens,
            result.output_tokens,
            model,
            Decimal(str(result.cost_usd)),
        )
    except Exception as exc:
        logger.debug("Could not record sub-agent cost: %s", exc)

    # Persist result
    try:
        save_result(working_dir, result)
    except Exception as exc:
        logger.debug("Could not save result: %s", exc)

    # Log to build log
    try:
        await _state.build_repo.append_build_log(
            handoff.build_id,
            f"Sub-agent [{handoff.role.value}] {result.status.value} "
            f"({result.duration_seconds:.1f}s, "
            f"{result.input_tokens + result.output_tokens} tokens"
            f"{', error: ' + result.error[:100] if result.error else ''})",
            source="subagent",
            level="info" if result.status == HandoffStatus.COMPLETED else "warn",
        )
    except Exception:
        pass

    # Try to parse structured JSON from the tail of the output
    result.structured_output = _extract_json_block(result.text_output)

    return result


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _default_model_for_role(role: SubAgentRole) -> str:
    """Return the default model for a given role.

    * Coder / Fixer → use the main builder model (Opus-class)
    * Scout / Auditor → use the lighter model (Sonnet-class)
    """
    if role in (SubAgentRole.CODER, SubAgentRole.FIXER):
        return _state.settings.LLM_BUILDER_MODEL
    # Scout and auditor use the cheaper/faster model
    return _state.settings.LLM_QUESTIONNAIRE_MODEL


def _extract_json_block(text: str) -> dict:
    """Try to extract a JSON object from the tail of LLM output.

    Looks for the last ``{...}`` block, optionally wrapped in markdown fences.
    Returns empty dict on failure (never raises).
    """
    import re as _re

    if not text:
        return {}

    # Try markdown-fenced JSON first
    fenced = _re.findall(r'```(?:json)?\s*(\{.*?\})\s*```', text, _re.DOTALL)
    if fenced:
        try:
            return json.loads(fenced[-1])
        except (json.JSONDecodeError, ValueError):
            pass

    # Try bare JSON (last { ... })
    brace_depth = 0
    last_start = -1
    for i in range(len(text) - 1, -1, -1):
        if text[i] == '}':
            if brace_depth == 0:
                end = i
            brace_depth += 1
        elif text[i] == '{':
            brace_depth -= 1
            if brace_depth == 0:
                last_start = i
                break

    if last_start >= 0:
        try:
            return json.loads(text[last_start:end + 1])
        except (json.JSONDecodeError, ValueError, UnboundLocalError):
            pass

    return {}
