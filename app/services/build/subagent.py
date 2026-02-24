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
import re as _re
import time
from dataclasses import dataclass, field, asdict
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID

from app.clients.agent_client import ApiKeyPool, StreamUsage, ToolCall, stream_agent
from app.services.tool_executor import BUILDER_TOOLS, execute_tool_async
from app.services.build.cost import _get_token_rates
from forge_constitution import CONSTITUTION
from . import _state

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level builder contract loading — loaded once, stripped per role
# ---------------------------------------------------------------------------

_bc_path = _state.FORGE_CONTRACTS_DIR / "builder_contract.md"
_BUILDER_CONTRACT_FULL = _bc_path.read_text(encoding="utf-8") if _bc_path.exists() else ""

# Core: strip §10 AEM (planner-only logic, ~4K tokens / 37% of contract)
_BUILDER_CONTRACT_CORE = _re.sub(
    r'## 10\) Autonomous Execution Mode \(AEM\).*?(?=## 11\))',
    '', _BUILDER_CONTRACT_FULL, flags=_re.DOTALL,
)

# Scout: also strip §1 (contract read gate) + §9 (verification hierarchy)
_BUILDER_CONTRACT_SCOUT = _re.sub(
    r'## 9\) Verification hierarchy.*?(?=## 1[0-9]\))',
    '', _BUILDER_CONTRACT_CORE, flags=_re.DOTALL,
)
_BUILDER_CONTRACT_SCOUT = _re.sub(
    r'## 1\) Contract read gate.*?(?=## 2\))',
    '', _BUILDER_CONTRACT_SCOUT, flags=_re.DOTALL,
)

# Fixer: also strip §9 (verification hierarchy)
_BUILDER_CONTRACT_FIXER = _re.sub(
    r'## 9\) Verification hierarchy.*?(?=## 1[0-9]\))',
    '', _BUILDER_CONTRACT_CORE, flags=_re.DOTALL,
)


def _contract_for_role(role: "SubAgentRole") -> str:
    """Return the role-appropriate builder contract text."""
    if role.value == "scout":
        return _BUILDER_CONTRACT_SCOUT
    if role.value == "fixer":
        return _BUILDER_CONTRACT_FIXER
    return _BUILDER_CONTRACT_CORE  # Coder + Auditor


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
    # Scout — workspace scanning only. Contracts are the Coder's domain.
    # Scout reads existing code to identify patterns, interfaces, imports.
    SubAgentRole.SCOUT: frozenset({
        "read_file",
        "list_directory",
        "search_code",
        "forge_scratchpad",
        "forge_ask_clarification",
    }),
    # Coder — pull-first model: fetches contracts via tools, writes
    # files via write_file, and checks syntax interactively.
    SubAgentRole.CODER: frozenset({
        "read_file",
        "write_file",
        "edit_file",
        "check_syntax",
        # Pull-first: Coder fetches contracts it needs
        "forge_get_contract",
        "forge_get_project_contract",
        "forge_list_contracts",
        "forge_scratchpad",
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
# Dynamic round caps — proportional to file complexity
# ---------------------------------------------------------------------------

# Files that are structurally simple and never need many tool rounds.
_SIMPLE_FILE_NAMES = frozenset({
    "requirements.txt", ".env", ".env.example", "dockerfile",
    "docker-compose.yml", "docker-compose.yaml",
    "pyproject.toml", "setup.py", "setup.cfg",
    ".gitignore", "alembic.ini", "conftest.py",
    "manifest.json", "robots.txt", "favicon.ico",
    ".dockerignore", "Procfile", "runtime.txt",
})

# Default round caps per role (used when no per-file override).
_DEFAULT_TOOL_ROUNDS: dict[SubAgentRole, int] = {
    SubAgentRole.SCOUT: 4,
    SubAgentRole.CODER: 8,
    SubAgentRole.AUDITOR: 8,
    SubAgentRole.FIXER: 10,
}


def max_rounds_for_file(role: SubAgentRole, file_entry: dict) -> int:
    """Compute a round cap proportional to file complexity.

    The planner provides ``estimated_lines``, ``depends_on``, and the file
    path.  Simple files get fewer rounds; complex files keep the full budget.

    Scout and Fixer caps are fixed (scouts are already deterministic for most
    tiers; fixers need room to iterate on findings).

    Returns the round cap integer.
    """
    if role == SubAgentRole.SCOUT:
        return 4
    if role == SubAgentRole.FIXER:
        return 10

    est_lines = file_entry.get("estimated_lines", 100)
    deps = len(file_entry.get("depends_on", []))
    name = Path(file_entry.get("path", "")).name.lower()

    # Trivial/simple files: 3 rounds (write + syntax + scratchpad)
    if name in _SIMPLE_FILE_NAMES or est_lines <= 30:
        return 3
    # Standard files: 5 rounds
    if est_lines <= 150 and deps < 3:
        return 5
    # Complex files: full budget
    return _DEFAULT_TOOL_ROUNDS.get(role, 8)


def _build_batch_audit_sysprompt(build_mode: str = "full") -> str:
    """Build system prompt for batch auditor: Constitution + batch prompt."""
    parts = [CONSTITUTION, BATCH_AUDITOR_PROMPT]
    if build_mode == "mini":
        parts.append(_MINI_BUILD_CONTEXT)
    return "\n\n".join(parts)


def build_batch_auditor_handoff(
    build_id: UUID,
    user_id: UUID,
    tier_files: dict[str, str],
    *,
    project_id: UUID | None = None,
    contracts: dict[str, str] | None = None,
    build_mode: str = "full",
) -> SubAgentHandoff:
    """Create a SubAgentHandoff for batch auditing all files in a tier.

    ``tier_files`` is ``{relative_path: file_content}``.  Each file is injected
    into ``context_files`` so the auditor sees everything in one call.

    Uses ``SubAgentRole.AUDITOR`` (same tools + model) but swaps the system
    prompt via ``system_prompt_override`` to the multi-file batch prompt.
    """
    context_files: dict[str, str] = {}
    # Inject the actual code files with clear labelling
    for path, content in tier_files.items():
        context_files[path] = content
    # Inject pre-fetched contracts
    if contracts:
        for k, v in contracts.items():
            if k not in context_files:
                context_files[k] = v

    file_list = ", ".join(tier_files.keys())
    return SubAgentHandoff(
        role=SubAgentRole.AUDITOR,
        build_id=build_id,
        user_id=user_id,
        assignment=(
            f"Batch audit {len(tier_files)} files: {file_list}. "
            "Review all files for structural issues and contract compliance. "
            "Output a single JSON with per-file verdicts."
        ),
        project_id=project_id,
        files=list(tier_files.keys()),
        context_files=context_files,
        build_mode=build_mode,
        system_prompt_override=_build_batch_audit_sysprompt(build_mode),
        max_tool_rounds=5,  # batch audit: read contracts + review, then output
    )


# ---------------------------------------------------------------------------
# System prompt templates per role
# ---------------------------------------------------------------------------

_ROLE_SYSTEM_PROMPTS: dict[SubAgentRole, str] = {
    SubAgentRole.SCOUT: (
        "You are a **Scout** sub-agent in the Forge build system.\n\n"
        "# ROLE\n"
        "Scan the workspace to gather context before coding begins. You have READ-ONLY\n"
        "access to project files on disk. Your output directly feeds the Coder —\n"
        "everything you miss, the Coder will hallucinate.\n\n"
        "You do NOT have access to contracts — the Coder fetches those directly.\n"
        "Your job is workspace analysis: directory structure, existing patterns,\n"
        "interfaces, and import conventions.\n\n"
        "# INPUTS\n"
        "1. Working directory — the project workspace on disk\n"
        "2. Phase deliverables — what files will be built in this phase\n\n"
        "# PROCESS (follow this order)\n"
        "Step 1. Scan the workspace:\n"
        "  - `list_directory('.')` for top-level structure\n"
        "  - `list_directory` on key subdirectories (app/, src/, etc.)\n"
        "  - `read_file` on files that the current phase's deliverables depend on\n"
        "  - `search_code` for import patterns, class definitions, route handlers\n"
        "  Make multiple calls in PARALLEL in a single turn for speed.\n\n"
        "Step 2. Produce DIRECTIVE output — not just observations, but explicit\n"
        "  instructions the Coder must follow.\n\n"
        "# OUTPUT FORMAT — MANDATORY\n"
        "Output exactly this JSON structure. Truncate field values to their limits.\n"
        "Do NOT add extra fields. Do NOT exceed the caps below.\n\n"
        "```json\n"
        '{\n'
        '  "directory_tree": "<compact tree — MAX 500 chars>",\n'
        '  "key_interfaces": [\n'
        '    {"file": "path/to/file.py", "exports": "ClassName(args), funcName(args) — MAX 150 chars each"}\n'
        '  ],\n'
        '  "patterns": {\n'
        '    "auth": "<how auth is wired — MAX 150 chars>",\n'
        '    "db": "<how DB is accessed — MAX 150 chars>",\n'
        '    "api": "<request/response shape patterns — MAX 150 chars>"\n'
        '  },\n'
        '  "imports_map": {\n'
        '    "module.path": ["ExportA", "ExportB"]\n'
        '  },\n'
        '  "directives": [\n'
        '    "MUST import UserRepo from app/repos/user_repo.py",\n'
        '    "MUST use async def for all route handlers",\n'
        '    "MUST NOT import directly from app/db/session.py in routers"\n'
        '  ],\n'
        '  "recommendations": "<what the coder must know — MAX 400 chars>"\n'
        '}\n```\n\n'
        "Hard limits (truncate, never expand):\n"
        "- directory_tree: max 500 chars\n"
        "- key_interfaces: max 10 entries, exports max 150 chars each\n"
        "- patterns: max 4 keys, values max 150 chars each\n"
        "- imports_map: max 10 entries\n"
        "- directives: max 10 entries, each a MUST/MUST NOT statement\n"
        "- recommendations: max 400 chars\n\n"
        "# CONSTRAINTS\n"
        "- Do NOT create, modify, or delete any files\n"
        "- Do NOT run tests or commands\n"
        "- Focus on accuracy over speed — verify before reporting\n"
        "- Truncate any value that would exceed its limit\n"
        "- If the workspace is empty (new project): set directory_tree to \"empty\",\n"
        "  set key_interfaces to [], and focus directives on contracts only\n\n"
        "# EXAMPLE — CORRECT OUTPUT\n"
        "<example>\n"
        '{\n'
        '  "directory_tree": "app/ (api/ models/ repos/ services/) tests/ alembic/ pyproject.toml",\n'
        '  "key_interfaces": [\n'
        '    {"file": "app/repos/user_repo.py", "exports": "UserRepo.get_by_id(id: UUID) -> User, .create(data: CreateUser) -> User"},\n'
        '    {"file": "app/services/auth.py", "exports": "get_current_user(token: str) -> User, create_token(user: User) -> str"}\n'
        '  ],\n'
        '  "patterns": {\n'
        '    "db": "async SQLAlchemy sessions via get_db() dependency, repos take session param",\n'
        '    "api": "Pydantic request/response models in app/models/, 422 on validation failure",\n'
        '    "auth": "JWT Bearer token, get_current_user dependency injected in routers"\n'
        '  },\n'
        '  "imports_map": {\n'
        '    "app.repos.user_repo": ["UserRepo"],\n'
        '    "app.services.auth": ["get_current_user", "create_token"],\n'
        '    "app.db.session": ["get_db"]\n'
        '  },\n'
        '  "directives": [\n'
        '    "MUST use get_db() dependency for database sessions — never create sessions manually",\n'
        '    "MUST place Pydantic models in app/models/ — never define inline in routers",\n'
        '    "MUST NOT import repos directly in routers — go through services layer"\n'
        '  ],\n'
        '  "recommendations": "Auth middleware already exists — reuse get_current_user dep. All repos follow same pattern: class with static async methods taking db session. Tests use conftest.py fixtures for db and auth."\n'
        '}\n'
        "</example>\n\n"
        "# FAILURE MODES\n"
        "- If the workspace is empty (new project): set directory_tree to \"empty\",\n"
        "  set key_interfaces to [], patterns to {}, imports_map to {},\n"
        "  and put a note in recommendations that this is a fresh project\n"
        "- If you cannot determine a pattern: omit that key rather than guessing\n\n"
        "# SCRATCHPAD PROTOCOL\n"
        "After completing your analysis, write key findings to scratchpad so future\n"
        "phase agents can skip re-scanning:\n"
        "  forge_scratchpad(\"write\", \"scout_patterns\", \"<auth: ..., db: ..., api: ...>\")\n"
        "  forge_scratchpad(\"write\", \"scout_interfaces\", \"<file: export1, export2>\")\n"
        "Keep each entry under 500 chars. Write to scratchpad BEFORE your JSON output.\n"
    ),
    SubAgentRole.CODER: (
        "You are a **Coder** sub-agent in the Forge build system.\n\n"
        "# ROLE\n"
        "Write production-quality code for specific files assigned to you.\n"
        "You have access to file tools, syntax checking, and contract tools.\n"
        "You receive Scout findings — use them, do not re-scan.\n\n"
        "# INPUTS\n"
        "1. builder_contract — universal governance rules (in your system prompt).\n"
        "2. Scout findings — directives, patterns, and imports_map from the Scout.\n"
        "   Treat Scout directives as MUST/MUST NOT rules.\n"
        "3. Phase deliverables — what other files are being built in this phase.\n"
        "4. Context files — source of existing files the Scout identified as relevant.\n\n"
        "# PROCESS\n"
        "Step 1. Review the contracts in your Context Files section (contract_stack.md,\n"
        "  contract_boundaries.md are pre-loaded — do NOT fetch them via tools).\n"
        "  If you need schema/physics/ui contracts not already in context, fetch ONLY\n"
        "  those using forge_get_contract (NOT forge_get_project_contract).\n"
        "Step 2. Read your assignment + Scout directives. Follow every MUST/MUST NOT.\n"
        "Step 3. Verify your design: imports match stack, endpoints match physics,\n"
        "  tables match schema, layers match boundaries.\n"
        "Step 4. Write the file using `write_file`. Output PURE CODE — no markdown.\n"
        "Step 5. Run `check_syntax` on the written file. Fix any errors immediately.\n"
        "Step 6. Output your summary JSON.\n\n"
        "# CONSTRAINTS\n"
        "- Write ONLY the files specified in your assignment\n"
        "- Respect layer boundaries: routers → services → repos → clients\n"
        "  Routers NEVER import repos. Services NEVER import routers.\n"
        "- Include type hints on all function signatures\n"
        "- Do NOT run tests (the test step handles that separately)\n"
        "- Do NOT re-read the whole project — use the context provided\n"
        "- Do NOT add packages or dependencies not listed in the stack contract\n"
        "- Do NOT create helper utilities, base classes, or abstractions unless\n"
        "  explicitly listed in your file assignment\n\n"
        "# CODE STYLE — VIOLATION IS A BUILD FAILURE\n"
        "- Output PURE CODE only. No tutorial prose between functions.\n"
        "- Docstrings: one-line only (`\"\"\"Fetch user by ID.\"\"\"`) — NEVER multi-line.\n"
        "- Comments: only where logic is non-obvious.\n"
        "- No module-level essays, section headers, or ASCII dividers.\n"
        "- Every token costs money — be maximally concise.\n\n"
        "# GROUNDING RULES\n"
        "- EVERY import must correspond to: a file in the workspace, a file in this\n"
        "  phase's manifest, OR a package in the stack contract. No other imports.\n"
        "- EVERY route handler must match an endpoint in the physics contract.\n"
        "- EVERY database model/table must match the schema contract.\n"
        "- If you need something that doesn't exist yet: import from the planned\n"
        "  path (per the phase manifest) and note it in known_issues.\n\n"
        "# EXAMPLE — CORRECT (FastAPI service file)\n"
        "<example>\n"
        "# File: app/services/project_service.py\n"
        "from uuid import UUID\n"
        "from app.repos.project_repo import ProjectRepo\n"
        "from app.models.project import Project, CreateProject\n\n"
        "async def create_project(db, data: CreateProject, user_id: UUID) -> Project:\n"
        "    \"\"\"Create a new project.\"\"\"\n"
        "    return await ProjectRepo.create(db, data, owner_id=user_id)\n\n"
        "async def get_project(db, project_id: UUID, user_id: UUID) -> Project | None:\n"
        "    \"\"\"Fetch project by ID with ownership check.\"\"\"\n"
        "    project = await ProjectRepo.get_by_id(db, project_id)\n"
        "    if project and project.owner_id != user_id:\n"
        "        return None\n"
        "    return project\n"
        "</example>\n\n"
        "# ANTI-EXAMPLE — WRONG (never do this)\n"
        "<bad_example>\n"
        '\"\"\"Project Service Module\\n\\nThis module provides...\\n\\n'
        "Attributes:\\n    ...\\n\"\"\"\n"
        "# === Imports === #\n"
        "# === Constants === #\n"
        "# === Helper Functions === #\n"
        "def _validate_project_name(name: str) -> bool:  # unnecessary helper\n"
        "</bad_example>\n\n"
        "# FAILURE MODES\n"
        "- If a dependency file doesn't exist yet: import from its planned path,\n"
        "  note in known_issues. The build order handles this.\n"
        "- If contracts are ambiguous on a detail: choose the simpler implementation,\n"
        "  note the ambiguity in decisions.\n"
        "- If you cannot complete the file: write a minimal working skeleton with\n"
        "  TODO markers and set known_issues accordingly. Partial > empty.\n\n"
        "# OUTPUT FORMAT — MANDATORY\n"
        "After writing all assigned files, output exactly:\n"
        "```json\n"
        '{\n  "files_written": ["path/to/file.py"],\n'
        '  "decisions": "brief non-obvious choices made",\n'
        '  "known_issues": "none | list of issues"\n}\n```\n\n'
        "# PRIOR FILES CONTEXT\n"
        "If your Context Files include `prior_files.md`, it contains summaries of\n"
        "files already built in this tier. Use this to:\n"
        "- Match established import patterns (import from the paths shown)\n"
        "- Reuse naming conventions (if User model uses UUID pk, so should Project)\n"
        "- Avoid conflicts (don't redefine exports that already exist)\n\n"
        "# ACTIVITY LOG — MANDATORY\n"
        "Before your JSON output, log what you did to the build scratchpad:\n"
        "  forge_scratchpad(\"write\", \"coder_<filename_stem>\", \"<what was built — max 300 chars>\")\n"
        "This is displayed to the user in the build activity panel.\n"
    ),
    SubAgentRole.AUDITOR: (
        "You are an **Auditor** sub-agent in the Forge build system.\n\n"
        "# ROLE\n"
        "Perform structural quality review of generated code. You have READ-ONLY\n"
        "access — you CANNOT modify any files. Your verdict determines whether\n"
        "the file ships or gets sent to the Fixer.\n\n"
        "# PROCESS\n"
        "Step 1. The file under review is provided in your **Context Files** section.\n"
        "  Do NOT re-read it with read_file — it is already in your context.\n"
        "Step 2. Review the contracts in your Context Files section (contract_stack.md,\n"
        "  contract_boundaries.md are pre-loaded). Fetch ONLY missing contracts:\n"
        "  - `forge_get_contract('physics')` — if checking API endpoints\n"
        "  - `forge_get_contract('schema')` — if checking DB models\n"
        "  Do NOT use forge_get_project_contract — use forge_get_contract instead.\n"
        "  Do NOT re-fetch stack or boundaries — they are already in your context.\n"
        "Step 3. Check against the severity table below.\n"
        "Step 4. Output your verdict JSON.\n\n"
        "# SEVERITY TABLE — what triggers FAIL vs PASS\n\n"
        "## FAIL (severity: \"error\") — these MUST be fixed:\n"
        "- Import references a module that does not exist in workspace or stack\n"
        "- Function/class referenced but never defined or imported\n"
        "- Layer boundary violation (router imports repo, service imports router)\n"
        "- API endpoint shape doesn't match physics contract\n"
        "- Database table/column doesn't match schema contract\n"
        "- Syntax error (missing colon, unmatched brackets, invalid Python)\n"
        "- File doesn't match its stated purpose at all\n"
        "- Missing return type on public function signatures\n"
        "- Hardcoded secrets or credentials (not env vars)\n\n"
        "## PASS with WARNING (severity: \"warn\") — note but do NOT fail:\n"
        "- Minor naming inconsistency (camelCase vs snake_case in one spot)\n"
        "- Missing error handling on a non-critical path\n"
        "- Import exists but is unused\n"
        "- TODO marker left by Coder (expected for partial completions)\n\n"
        "## IGNORE — do NOT flag these:\n"
        "- Style preferences (single vs double quotes, trailing commas)\n"
        "- Missing docstrings or comments\n"
        "- Code that works but could be \"more elegant\"\n"
        "- Optional improvements or refactoring suggestions\n"
        "- Test file structure or test naming conventions\n\n"
        "A file with only warnings gets verdict PASS. Only errors trigger FAIL.\n\n"
        "# OUTPUT FORMAT — MANDATORY\n"
        "For each file, output exactly:\n"
        "```json\n"
        '{\n'
        '  "path": "relative/path/to/file.py",\n'
        '  "verdict": "PASS|FAIL",\n'
        '  "findings": [\n'
        '    {"line": 42, "severity": "error|warn", "message": "concise description"}\n'
        '  ]\n'
        '}\n```\n\n'
        "If the file is structurally sound: verdict=PASS, findings=[].\n\n"
        "# EXAMPLE — CORRECT AUDIT\n"
        "<example>\n"
        "File: app/routers/projects.py\n"
        '{\n'
        '  "path": "app/routers/projects.py",\n'
        '  "verdict": "FAIL",\n'
        '  "findings": [\n'
        '    {"line": 5, "severity": "error", "message": "imports ProjectRepo directly — boundary violation, must go through service layer"},\n'
        '    {"line": 23, "severity": "error", "message": "references create_project_table() which is not defined or imported"}\n'
        '  ]\n'
        '}\n'
        "</example>\n\n"
        "# ANTI-EXAMPLE — WRONG (over-auditing)\n"
        "<bad_example>\n"
        '{"line": 1, "severity": "error", "message": "missing module docstring"}\n'
        '{"line": 10, "severity": "error", "message": "could use list comprehension instead of for loop"}\n'
        '{"line": 15, "severity": "error", "message": "variable name x is not descriptive"}\n'
        "</bad_example>\n"
        "These are style preferences, NOT structural errors. Flagging them wastes\n"
        "Fixer tokens and produces unnecessary churn.\n\n"
        "# FAILURE MODES\n"
        "- If a contract is unavailable: audit only for structural issues (imports,\n"
        "  syntax, layer boundaries). Note missing contract in findings as a warn.\n"
        "- If the file is a test file: apply relaxed rules (test helpers, fixtures,\n"
        "  and mock imports are acceptable even if not in stack contract).\n"
        "- If you cannot determine whether an import is valid: set severity to\n"
        "  \"warn\" not \"error\" — let the Fixer investigate rather than false-failing.\n\n"
        "# PRIOR FILES CONTEXT\n"
        "If your Context Files include `prior_files.md`, use it to verify:\n"
        "- Imports reference files that actually exist (listed in prior_files.md)\n"
        "- Exports don't conflict with already-built files\n"
        "- Naming conventions are consistent across the tier\n\n"
        "# ACTIVITY LOG — MANDATORY\n"
        "Before your JSON output, log your verdict to scratchpad:\n"
        "  forge_scratchpad(\"write\", \"audit_<filename_stem>\", \"PASS|FAIL: <key findings or 'clean' — max 200 chars>\")\n"
        "This is displayed to the user in the build activity panel.\n"
    ),
    SubAgentRole.FIXER: (
        "You are a **Fixer** sub-agent in the Forge build system.\n\n"
        "# ROLE\n"
        "Apply targeted, surgical fixes to files that failed audit. You use\n"
        "`edit_file` to patch specific lines. You CANNOT use `write_file` — no\n"
        "full rewrites allowed. You are a scalpel, not a sledgehammer.\n\n"
        "# INPUTS\n"
        "1. Audit findings — the specific errors that caused FAIL verdict\n"
        "2. The file content — as written by the Coder\n"
        "3. Build contracts — immutable snapshot frozen at build start\n\n"
        "# PROCESS\n"
        "Step 1. Review contracts in your Context Files section. If boundaries or\n"
        "  stack are not pre-loaded, call `forge_get_contract('boundaries')` to fetch.\n"
        "  Do NOT use forge_get_build_contracts or forge_get_project_contract.\n"
        "Step 2. Read the audit findings carefully. Each finding has a line number,\n"
        "  severity, and message.\n"
        "Step 3. For EACH finding with severity \"error\":\n"
        "  a. Read the relevant lines in the file\n"
        "  b. Determine the minimal edit to fix the issue\n"
        "  c. Apply the edit using `edit_file`\n"
        "  d. Run `check_syntax` to verify the fix didn't break anything\n"
        "Step 4. Output your summary JSON.\n\n"
        "# CONSTRAINTS — CRITICAL\n"
        "- Fix ONLY the issues listed in the audit findings\n"
        "- Do NOT refactor, restyle, or \"improve\" working code\n"
        "- Do NOT add docstrings, comments, or type hints that weren't there\n"
        "- Do NOT change variable names, function signatures, or code structure\n"
        "- Do NOT add error handling beyond what the finding requires\n"
        "- Preserve ALL existing functionality — if it worked before, it works after\n"
        "- Keep the same imports, structure, and style as the original\n"
        "- If an import is missing: add ONLY that import, change nothing else\n"
        "- Check syntax after EVERY edit — never batch edits without checking\n\n"
        "# EXAMPLE — CORRECT FIX\n"
        "<example>\n"
        "Audit finding: {\"line\": 5, \"severity\": \"error\", \"message\": \"imports ProjectRepo directly — boundary violation\"}\n\n"
        "BEFORE (line 5): from app.repos.project_repo import ProjectRepo\n"
        "FIX: edit_file to replace line 5 with: from app.services.project_service import create_project, get_project\n"
        "THEN: update the function calls that used ProjectRepo to use the service functions.\n"
        "THEN: check_syntax to verify.\n"
        "</example>\n\n"
        "# ANTI-EXAMPLE — WRONG (scope creep)\n"
        "<bad_example>\n"
        "Audit finding says: \"missing import for UUID\"\n"
        "Fixer adds the UUID import BUT ALSO:\n"
        "  - Adds docstrings to all functions\n"
        "  - Renames variables for clarity\n"
        "  - Adds error handling to unrelated functions\n"
        "  - Rewrites a working loop as a list comprehension\n"
        "This is WRONG. Fix the import. Touch nothing else.\n"
        "</bad_example>\n\n"
        "# FAILURE MODES\n"
        "- If a finding is ambiguous: apply the most conservative fix possible.\n"
        "  When in doubt, do less.\n"
        "- If fixing one error would require restructuring the entire file:\n"
        "  set remaining_issues to describe what's needed and let the next\n"
        "  build iteration handle it. Do NOT attempt a full rewrite via edits.\n"
        "- If check_syntax fails after your edit: revert the edit and try a\n"
        "  different approach. Do NOT leave the file in a broken state.\n\n"
        "# OUTPUT FORMAT — MANDATORY\n"
        "```json\n"
        '{\n'
        '  "files_fixed": ["path/to/file.py"],\n'
        '  "edits_applied": 3,\n'
        '  "remaining_issues": "none | description of unfixable issues"\n'
        '}\n```\n\n'
        "# ACTIVITY LOG\n"
        "After fixing, log what was changed to scratchpad:\n"
        "  forge_scratchpad(\"write\", \"fixes_applied\", \"<path:line — what was fixed>\")\n"
        "This is displayed to the user in the build activity panel.\n"
        "Keep under 300 chars total.\n"
    ),
}


_MINI_BUILD_CONTEXT = """\
## MINI BUILD MODE — PROOF OF CONCEPT
This is a rapid scaffold, NOT a production build. Adjust your output accordingly:
- Working code over robust error handling — skip edge cases
- Simple patterns — no complex abstractions, no overengineering
- No auth, rate limiting, caching, or advanced middleware
- Basic happy-path tests only (1-2 per endpoint)
- Lean output — aim for ~50-70% of estimated_lines
- No Docker files — this is a dev-ready local build (`pip install && uvicorn`)
- No CI/CD configuration or deployment scripts
- Minimal dependencies — only what the core feature needs
"""


# ---------------------------------------------------------------------------
# Batch auditor prompt — reviews all tier files in a single LLM call
# ---------------------------------------------------------------------------

BATCH_AUDITOR_PROMPT = (
    "You are a **Batch Auditor** sub-agent in the Forge build system.\n\n"
    "# ROLE\n"
    "Review ALL files for a build tier in a single pass. You have READ-ONLY\n"
    "access — you CANNOT modify any files. Your per-file verdicts determine\n"
    "which files ship and which get sent to the Fixer.\n\n"
    "# INPUTS\n"
    "All files for this tier are provided in your **Context Files** section.\n"
    "Do NOT re-read them with read_file — they are already in your context.\n\n"
    "# PROCESS\n"
    "Step 1. Review the contracts in your Context Files section (contract_stack.md,\n"
    "  contract_boundaries.md are pre-loaded). Fetch ONLY missing contracts:\n"
    "  - `forge_get_contract('physics')` — if checking API endpoints\n"
    "  - `forge_get_contract('schema')` — if checking DB models\n"
    "  Do NOT use forge_get_project_contract — use forge_get_contract instead.\n"
    "Step 2. For EACH file, check against the severity table below.\n"
    "Step 3. Output your batch verdict JSON.\n\n"
    "# SEVERITY TABLE — what triggers FAIL vs PASS\n\n"
    "## FAIL (severity: \"error\") — these MUST be fixed:\n"
    "- Import references a module that does not exist in workspace or stack\n"
    "- Function/class referenced but never defined or imported\n"
    "- Layer boundary violation (router imports repo, service imports router)\n"
    "- API endpoint shape doesn't match physics contract\n"
    "- Database table/column doesn't match schema contract\n"
    "- Syntax error (missing colon, unmatched brackets, invalid Python)\n"
    "- File doesn't match its stated purpose at all\n"
    "- Missing return type on public function signatures\n"
    "- Hardcoded secrets or credentials (not env vars)\n\n"
    "## PASS with WARNING (severity: \"warn\") — note but do NOT fail:\n"
    "- Minor naming inconsistency\n"
    "- Missing error handling on a non-critical path\n"
    "- Unused import\n"
    "- TODO marker left by Coder\n\n"
    "## IGNORE — do NOT flag these:\n"
    "- Style preferences (quotes, commas)\n"
    "- Missing docstrings or comments\n"
    "- Code that works but could be \"more elegant\"\n"
    "- Test file structure or naming conventions\n\n"
    "A file with only warnings gets verdict PASS. Only errors trigger FAIL.\n\n"
    "# CROSS-FILE CHECKS (batch advantage)\n"
    "Because you see ALL tier files at once, also check:\n"
    "- Imports between files in this tier resolve correctly\n"
    "- Shared types/interfaces are consistent across files\n"
    "- No circular imports within the tier\n\n"
    "# OUTPUT FORMAT — MANDATORY\n"
    "Output exactly this JSON structure with one entry per file:\n"
    "```json\n"
    '{\n'
    '  "files": [\n'
    '    {\n'
    '      "path": "relative/path/to/file.py",\n'
    '      "verdict": "PASS",\n'
    '      "findings": []\n'
    '    },\n'
    '    {\n'
    '      "path": "relative/path/to/other.py",\n'
    '      "verdict": "FAIL",\n'
    '      "findings": [\n'
    '        {"line": 42, "severity": "error", "message": "concise description"}\n'
    '      ]\n'
    '    }\n'
    '  ]\n'
    '}\n```\n\n'
    "IMPORTANT: Include ALL files from Context Files in your output, even if PASS.\n"
    "If a file is structurally sound: verdict=PASS, findings=[].\n\n"
    "# CONSTRAINTS\n"
    "- Do NOT re-read files — use context provided\n"
    "- Review ALL files, do not skip any\n"
    "- Be efficient — one pass through all files, then output\n"
    "- Do NOT flag style issues as errors\n"
)


def system_prompt_for_role(
    role: SubAgentRole,
    *,
    extra: str = "",
    build_mode: str = "full",
) -> str:
    """Return the system prompt for *role*, prepended with the Forge Constitution.

    The constitution (shared law for all agents) comes first, then the
    role-specific prompt, then any extra context. For mini builds, a
    scope-reduction block is appended.
    """
    base = _ROLE_SYSTEM_PROMPTS.get(role, "")
    parts = [CONSTITUTION, base]
    if build_mode == "mini":
        parts.append(_MINI_BUILD_CONTEXT)
    if extra:
        parts.append(extra)
    return "\n\n".join(parts)


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
    project_id: UUID | None = None  # needed for forge_get_contract DB-direct tool
    context_files: dict[str, str] = field(default_factory=dict)
    contracts_text: str = ""
    phase_deliverables: str = ""
    error_context: str = ""  # audit findings for fixer

    # Config overrides
    model: str = ""  # empty → use default for role
    max_tokens: int = 16_384
    max_tool_rounds: int = 0  # 0 → use default for role; >0 → override
    timeout_seconds: float = 600.0
    build_mode: str = "full"  # "mini" or "full" — affects prompt scoping
    system_prompt_override: str = ""  # non-empty → replaces role-based system prompt

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
    input_tokens: int = 0          # total input (fresh + cache_read + cache_creation)
    output_tokens: int = 0
    cache_read_tokens: int = 0     # subset of input_tokens served from cache (90% cheaper)
    cache_creation_tokens: int = 0 # subset of input_tokens that created cache (25% surcharge)
    model: str = ""
    cost_usd: float = 0.0

    # Timing
    started_at: float = 0.0
    finished_at: float = 0.0
    duration_seconds: float = 0.0

    # Rounds
    tool_rounds: int = 0

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
# Deterministic tier-level scout (replaces per-file LLM scouts)
# ---------------------------------------------------------------------------

_SCOUT_SKIP_DIRS = frozenset({
    ".git", "node_modules", ".venv", "__pycache__", ".forge",
    ".mypy_cache", ".pytest_cache", "dist", "build", ".tox",
    ".eggs", ".ruff_cache", "htmlcov", ".coverage",
})

_SCOUT_SOURCE_SUFFIXES = frozenset({".py", ".ts", ".tsx", ".js", ".jsx"})


def build_tier_scout_context(
    working_dir: str,
    tier_files: list[dict],
    all_files_written: dict[str, str],
    *,
    max_tree_chars: int = 500,
    max_interfaces: int = 10,
    max_imports: int = 10,
    max_directives: int = 10,
) -> dict:
    """Build scout-equivalent structured context for an entire tier deterministically.

    Produces the same JSON schema the LLM scout outputs (matching _trim_scout_output
    caps). Zero LLM tokens. Runs once per tier. O(n) in workspace file count.

    Uses only stdlib: os, ast, re, pathlib. No network, no subprocess.

    Parameters
    ----------
    working_dir:
        Absolute path to the build workspace on disk.
    tier_files:
        List of file dicts (with 'path', 'purpose', etc.) for the current tier.
    all_files_written:
        Dict of {relative_path: content} for all files written in prior tiers.
    max_tree_chars:
        Character cap for directory_tree field (default: 500).
    max_interfaces:
        Maximum entries in key_interfaces (default: 10).
    max_imports:
        Maximum entries in imports_map (default: 10).
    max_directives:
        Maximum entries in directives (default: 10).

    Returns
    -------
    dict
        Scout-schema JSON: directory_tree, key_interfaces, patterns,
        imports_map, directives, recommendations.
    """
    import ast as _ast
    import os as _os
    import re as _re

    wd = Path(working_dir)
    result: dict = {}

    def _should_skip_dir(dirname: str) -> bool:
        return dirname in _SCOUT_SKIP_DIRS or dirname.endswith(".egg-info")

    # -- 1. directory_tree --------------------------------------------------
    tree_parts: list[str] = []
    tree_len = 0
    for dirpath, dirnames, _filenames in _os.walk(str(wd)):
        dirnames[:] = [d for d in dirnames if not _should_skip_dir(d)]
        rel = _os.path.relpath(dirpath, str(wd)).replace("\\", "/")
        if rel == ".":
            rel = ""
        if dirnames:
            entry = (f"{rel}/" if rel else "") + " ".join(sorted(dirnames)) + "/"
        elif rel:
            entry = f"{rel}/"
        else:
            continue
        if entry and tree_len + len(entry) + 2 < max_tree_chars:
            tree_parts.append(entry)
            tree_len += len(entry) + 2
        if tree_len >= max_tree_chars:
            break
    result["directory_tree"] = (
        "  ".join(tree_parts)[:max_tree_chars] if tree_parts else "empty"
    )

    # -- 2. key_interfaces (Python: ast.parse; TS/JS: regex) ----------------
    interfaces: list[dict] = []
    _scanned_for_iface = 0
    _MAX_IFACE_SCAN = 200

    def _extract_py_exports(file_path: Path) -> str | None:
        """Extract public class/function signatures from a Python file via AST."""
        try:
            source = file_path.read_text(encoding="utf-8", errors="replace")
            tree = _ast.parse(source, filename=str(file_path))
        except Exception:
            return None
        exports: list[str] = []
        for node in _ast.iter_child_nodes(tree):
            if isinstance(node, _ast.ClassDef):
                init = next(
                    (n for n in _ast.iter_child_nodes(node)
                     if isinstance(n, _ast.FunctionDef) and n.name == "__init__"),
                    None,
                )
                if init:
                    args = [a.arg for a in init.args.args if a.arg != "self"]
                    exports.append(f"{node.name}({', '.join(args)})")
                else:
                    exports.append(node.name)
            elif isinstance(node, (_ast.FunctionDef, _ast.AsyncFunctionDef)):
                if node.name.startswith("_"):
                    continue
                args = [a.arg for a in node.args.args if a.arg != "self"]
                exports.append(f"{node.name}({', '.join(args)})")
        return ", ".join(exports[:8]) if exports else None

    def _extract_ts_exports(file_path: Path) -> str | None:
        """Extract named exports from a TypeScript/JavaScript file via regex."""
        try:
            source = file_path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            return None
        names: list[str] = []
        for m in _re.finditer(
            r'export\s+(?:default\s+)?(?:function|class|const|interface|type|enum)\s+(\w+)',
            source,
        ):
            names.append(m.group(1))
        return ", ".join(names[:8]) if names else None

    for dirpath, dirnames, filenames in _os.walk(str(wd)):
        dirnames[:] = [d for d in dirnames if not _should_skip_dir(d)]
        for fname in sorted(filenames):
            if _scanned_for_iface >= _MAX_IFACE_SCAN or len(interfaces) >= max_interfaces:
                break
            fpath = Path(dirpath) / fname
            suffix = fpath.suffix.lower()
            if suffix not in _SCOUT_SOURCE_SUFFIXES:
                continue
            _scanned_for_iface += 1
            rel = str(fpath.relative_to(wd)).replace("\\", "/")
            if suffix == ".py":
                exports_str = _extract_py_exports(fpath)
            elif suffix in (".ts", ".tsx", ".js", ".jsx"):
                exports_str = _extract_ts_exports(fpath)
            else:
                continue
            if exports_str:
                interfaces.append({"file": rel, "exports": exports_str[:150]})
        if len(interfaces) >= max_interfaces:
            break
    result["key_interfaces"] = interfaces[:max_interfaces]

    # -- 3. patterns (heuristic detection via regex) ------------------------
    _PATTERN_SEARCHES: dict[str, list[tuple[str, str]]] = {
        "auth": [
            (r"get_current_user|verify_token|jwt|bearer|oauth", "auth middleware"),
            (r"Depends\(get_current_user\)", "FastAPI dependency injection auth"),
        ],
        "db": [
            (r"get_db|async_session|asyncpg|create_pool", "async DB sessions"),
            (r"Column\(|relationship\(|mapped_column", "SQLAlchemy ORM"),
            (r"await\s+\w+\.fetch|\.execute\(", "raw async DB queries"),
        ],
        "api": [
            (r"@(?:app|router)\.\w+\(", "decorated route handlers"),
            (r"APIRouter\(\)", "FastAPI router pattern"),
            (r"BaseModel|Field\(", "Pydantic models"),
        ],
    }
    _pattern_evidence: dict[str, list[str]] = {"auth": [], "db": [], "api": []}
    _pattern_files_checked = 0
    _MAX_PATTERN_FILES = 50

    for dirpath, dirnames, filenames in _os.walk(str(wd)):
        dirnames[:] = [d for d in dirnames if not _should_skip_dir(d)]
        for fname in sorted(filenames):
            if _pattern_files_checked >= _MAX_PATTERN_FILES:
                break
            fpath = Path(dirpath) / fname
            if fpath.suffix.lower() not in _SCOUT_SOURCE_SUFFIXES:
                continue
            try:
                sample = fpath.read_text(encoding="utf-8", errors="replace")[:5000]
            except Exception:
                continue
            _pattern_files_checked += 1
            for category, searches in _PATTERN_SEARCHES.items():
                for pattern, label in searches:
                    if _re.search(pattern, sample):
                        bucket = _pattern_evidence[category]
                        if label not in bucket:
                            bucket.append(label)
        if _pattern_files_checked >= _MAX_PATTERN_FILES:
            break

    patterns: dict[str, str] = {}
    for cat, evidence in _pattern_evidence.items():
        if evidence:
            patterns[cat] = ", ".join(evidence)[:150]
    result["patterns"] = patterns

    # -- 4. imports_map (Python from-import parsing) ------------------------
    imports_map: dict[str, list[str]] = {}
    for dirpath, dirnames, filenames in _os.walk(str(wd)):
        dirnames[:] = [d for d in dirnames if not _should_skip_dir(d)]
        for fname in sorted(filenames):
            fpath = Path(dirpath) / fname
            if fpath.suffix.lower() != ".py":
                continue
            try:
                source = fpath.read_text(encoding="utf-8", errors="replace")[:8000]
            except Exception:
                continue
            for m in _re.finditer(r'from\s+([\w.]+)\s+import\s+(.+?)(?:\n|$)', source):
                mod = m.group(1)
                # Only track project-internal imports (not stdlib/third-party)
                if not any(mod.startswith(p) for p in ("app.", "src.", "lib.", "api.")):
                    continue
                names = [n.strip().split(" as ")[0].strip()
                         for n in m.group(2).split(",")]
                if mod not in imports_map:
                    imports_map[mod] = []
                for name in names:
                    if name and name not in imports_map[mod]:
                        imports_map[mod].append(name)
            if len(imports_map) >= max_imports:
                break
        if len(imports_map) >= max_imports:
            break
    result["imports_map"] = dict(list(imports_map.items())[:max_imports])

    # -- 5. directives (workspace-structure-derived rules) ------------------
    directives: list[str] = []
    has_repos = (wd / "app" / "repos").is_dir()
    has_services = (wd / "app" / "services").is_dir()
    has_routers = (
        (wd / "app" / "api" / "routers").is_dir()
        or (wd / "app" / "routers").is_dir()
    )
    has_conftest = (wd / "tests" / "conftest.py").is_file()
    has_alembic = (wd / "alembic").is_dir()

    if has_repos and has_services and has_routers:
        directives.append(
            "MUST NOT import repos directly in routers — go through services layer"
        )
        directives.append(
            "MUST NOT import routers in services — layer violation"
        )
    if has_repos:
        directives.append(
            "MUST place database access code in app/repos/ — never in routers or services"
        )
    if has_services:
        directives.append(
            "MUST use service layer for business logic between routers and repos"
        )
    if has_conftest:
        directives.append(
            "MUST use conftest.py fixtures for test setup — do not duplicate"
        )
    if has_alembic:
        directives.append(
            "MUST NOT modify database schema directly — use Alembic migrations"
        )

    # Detect lifespan pattern from main.py
    _main_candidates = [wd / "app" / "main.py", wd / "main.py", wd / "src" / "main.py"]
    for mc in _main_candidates:
        if mc.is_file():
            try:
                main_src = mc.read_text(encoding="utf-8", errors="replace")[:3000]
                if "lifespan" in main_src:
                    directives.append(
                        "MUST use lifespan context manager for app startup/shutdown"
                    )
                break
            except Exception:
                pass

    result["directives"] = [d[:200] for d in directives[:max_directives]]

    # -- 6. recommendations -------------------------------------------------
    tier_paths = [f.get("path", "") for f in tier_files]
    existing_count = sum(1 for p in tier_paths if (wd / p).is_file())
    written_count = len(all_files_written)
    rec_parts: list[str] = []
    if written_count > 0:
        rec_parts.append(f"{written_count} files already built in prior tiers")
    rec_parts.append(f"This tier builds {len(tier_files)} files")
    if existing_count > 0:
        rec_parts.append(
            f"{existing_count}/{len(tier_files)} already exist on disk (overwrite)"
        )
    if interfaces:
        rec_parts.append(
            f"Workspace has {len(interfaces)} interface files to reference"
        )
    result["recommendations"] = ". ".join(rec_parts)[:400]

    return result


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
    stop_event: "Any | None" = None,
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

    # 3. Build system prompt — inject role-specific builder contract slice.
    #    §10 AEM stripped for all sub-agents; Scout/Fixer also lose §9/§1.
    #    Loaded once at module level for Anthropic prompt caching benefit.
    #    system_prompt_override allows batch auditor to use a custom prompt.
    sys_prompt = (
        handoff.system_prompt_override
        if handoff.system_prompt_override
        else system_prompt_for_role(handoff.role, build_mode=handoff.build_mode)
    )
    _contract_text = _contract_for_role(handoff.role)
    if _contract_text:
        sys_prompt += f"\n\n## builder_contract (governance)\n{_contract_text}"

    # 4. Build user message
    parts: list[str] = []

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

    # --- Context load metric ---
    _ctx_keys = list(handoff.context_files.keys()) if handoff.context_files else []
    _contract_keys = [k for k in _ctx_keys if k.startswith("contract_")]
    _ctx_total_chars = sum(len(v) for v in handoff.context_files.values()) if handoff.context_files else 0
    logger.debug(
        "METRIC | type=context_load | role=%s | file=%s | "
        "context_files=%d | contract_files=%d | contracts=%s | total_chars=%d",
        handoff.role.value, (handoff.files[0] if handoff.files else "?"),
        len(_ctx_keys), len(_contract_keys),
        "+".join(_contract_keys) if _contract_keys else "none",
        _ctx_total_chars,
    )

    # 5. Broadcast start
    await _state._broadcast_build_event(
        handoff.user_id, handoff.build_id, "subagent_start", {
            "role": handoff.role.value,
            "handoff_id": handoff.handoff_id,
            "files": handoff.files,
            "assignment": handoff.assignment[:200],
        },
    )

    # 5b. Broadcast LLM thinking event — shows prompt details in the correct panel
    _model_label = "opus" if handoff.role in (SubAgentRole.CODER, SubAgentRole.FIXER) else "sonnet"
    _PURPOSE_BY_ROLE = {
        SubAgentRole.SCOUT: "Scouting",
        SubAgentRole.CODER: "Building",
        SubAgentRole.AUDITOR: "Auditing",
        SubAgentRole.FIXER: "Fixing",
    }
    _purpose_verb = _PURPOSE_BY_ROLE.get(handoff.role, "Processing")
    _purpose_file = handoff.files[0] if handoff.files else "file"

    # Build a useful preview: role-specific instructions first (what differs
    # between agents), then a constitution summary.  The actual LLM receives
    # the full untruncated sys_prompt — this is display-only.
    _role_section = _ROLE_SYSTEM_PROMPTS.get(handoff.role, "")
    _sys_preview = (
        f"# ROLE: {handoff.role.value.upper()}\n{_role_section}\n\n"
        f"# CONSTITUTION (shared — {len(CONSTITUTION)} chars)\n"
        f"{CONSTITUTION[:600]}...\n"
    )

    await _state._broadcast_build_event(
        handoff.user_id, handoff.build_id, "llm_thinking", {
            "model": _model_label,
            "role": handoff.role.value,
            "purpose": f"{_purpose_verb} {_purpose_file}",
            "system_prompt": _sys_preview[:6000],
            "user_message_preview": user_message[:4000],
            "user_message_length": len(user_message),
            "file": _purpose_file,
            "context_files": list(handoff.context_files.keys()) if handoff.context_files else [],
        },
    )

    # Save handoff to disk for debugging
    try:
        save_handoff(working_dir, handoff)
    except Exception as exc:
        logger.debug("Could not save handoff: %s", exc)

    # 6. Stream with tool loop (all roles including CODER)
    usage = StreamUsage()
    text_chunks: list[str] = []
    # Per-role tool round caps — prevents O(n^2) token growth from multi-turn
    # conversations.  Each round re-sends full message history, so cumulative
    # token cost across rounds grows quadratically.  Capping rounds is the
    # primary defense against runaway costs.
    #
    # The handoff can carry a per-file override (from max_rounds_for_file()).
    # If not set (0), fall back to the role default.
    max_tool_rounds = (
        handoff.max_tool_rounds
        if handoff.max_tool_rounds > 0
        else _DEFAULT_TOOL_ROUNDS.get(handoff.role, 25)
    )
    tool_rounds = 0

    # Early-termination tracking for CODER role.
    # Once the file is written and syntax checks clean, allow 1 grace round
    # (for scratchpad + summary JSON) then stop — prevents wasted rounds.
    _et_wrote_file = False
    _et_syntax_clean = False
    _et_grace_rounds = 1  # how many rounds to allow after task completion

    try:
        while tool_rounds < max_tool_rounds:
            # ── Check stop event before each LLM round ──
            # This is the fastest bail-out point: if the user clicked Stop,
            # we skip the next API call entirely instead of burning tokens.
            if stop_event is not None and stop_event.is_set():
                logger.info(
                    "Sub-agent %s interrupted by stop_event before round %d",
                    handoff.role.value, tool_rounds,
                )
                return SubAgentResult(
                    status=HandoffStatus.FAILED,
                    structured_output={"error": "Build stopped by user"},
                    text_output="",
                    input_tokens=usage.input_tokens,
                    output_tokens=usage.output_tokens,
                    cache_read_tokens=usage.cache_read_tokens,
                )

            tool_calls_this_round: list[dict] = []

            # Snapshot token counts before this round for per-round delta
            _round_t0 = time.monotonic()
            _pre_in = usage.input_tokens
            _pre_out = usage.output_tokens
            _pre_cache_read = usage.cache_read_input_tokens
            _pre_cache_create = usage.cache_creation_input_tokens

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
                                project_id=str(handoff.project_id) if handoff.project_id else "",
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

                    # ── Broadcast sub-agent activity so UI can show live trace ──
                    _activity_detail: dict = {
                        "role": handoff.role.value,
                        "handoff_id": handoff.handoff_id,
                        "tool": event.name,
                        "round": tool_rounds + 1,
                    }
                    if event.name == "read_file":
                        _activity_detail["action"] = "read"
                        _activity_detail["path"] = event.input.get("path", "")
                    elif event.name == "list_directory":
                        _activity_detail["action"] = "list"
                        _activity_detail["path"] = event.input.get("path", "")
                    elif event.name == "search_code":
                        _activity_detail["action"] = "search"
                        _activity_detail["query"] = event.input.get("pattern", event.input.get("query", ""))[:200]
                        _activity_detail["path"] = event.input.get("path", "")
                    elif event.name in ("write_file", "edit_file"):
                        _activity_detail["action"] = "write" if event.name == "write_file" else "edit"
                        _activity_detail["path"] = event.input.get("path", "")
                    elif event.name == "check_syntax":
                        _activity_detail["action"] = "syntax_check"
                        _activity_detail["path"] = event.input.get("path", "")
                    else:
                        _activity_detail["action"] = event.name

                    # Truncated result preview (for searches / reads)
                    _result_str = str(tool_result) if tool_result else ""
                    _activity_detail["result_preview"] = _result_str[:500]
                    _activity_detail["result_length"] = len(_result_str)
                    _activity_detail["success"] = not _result_str.startswith("Tool error:")

                    await _state._broadcast_build_event(
                        handoff.user_id, handoff.build_id, "subagent_activity", _activity_detail,
                    )

                    # Broadcast scratchpad writes to UI
                    if event.name == "forge_scratchpad":
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
                                    "content": str(_sp_val)[:6000],
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

            # Tool results — cap at 4K to limit context bloat across rounds
            tool_result_blocks = []
            _MAX_TOOL_RESULT = 4_000
            for tc in tool_calls_this_round:
                _raw = str(tc["result"])
                if len(_raw) > _MAX_TOOL_RESULT:
                    _raw = _raw[:_MAX_TOOL_RESULT] + f"\n... [truncated, {len(_raw)} total chars]"
                tool_result_blocks.append({
                    "type": "tool_result",
                    "tool_use_id": tc["id"],
                    "content": _raw,
                })
            messages.append({"role": "user", "content": tool_result_blocks})

            tool_rounds += 1

            # --- Per-round token metric ---
            _round_elapsed = time.monotonic() - _round_t0
            _delta_in = usage.input_tokens - _pre_in
            _delta_out = usage.output_tokens - _pre_out
            _delta_cache_read = usage.cache_read_input_tokens - _pre_cache_read
            _delta_cache_create = usage.cache_creation_input_tokens - _pre_cache_create
            _delta_fresh = _delta_in - _delta_cache_read - _delta_cache_create
            _tools_this_round = [tc["name"] for tc in tool_calls_this_round]
            logger.debug(
                "METRIC | type=round_tokens | role=%s | round=%d/%d | "
                "fresh=%d | cached=%d | cache_create=%d | out=%d | "
                "tools=%s | tool_count=%d | wall_ms=%.0f | msgs=%d",
                handoff.role.value, tool_rounds, max_tool_rounds,
                _delta_fresh, _delta_cache_read, _delta_cache_create, _delta_out,
                "+".join(_tools_this_round) if _tools_this_round else "none",
                len(_tools_this_round),
                _round_elapsed * 1000, len(messages),
            )

            text_chunks.clear()

            # ── Early termination for CODER ──
            # Once write_file has been called and check_syntax passed, the task
            # is functionally done.  Allow a grace round for scratchpad/summary
            # then break — saves 2-5 wasted rounds on simple files.
            if handoff.role == SubAgentRole.CODER:
                for tc in tool_calls_this_round:
                    if tc["name"] == "write_file":
                        _et_wrote_file = True
                    if tc["name"] == "check_syntax":
                        _res = str(tc.get("result", "")).lower()
                        if "error" not in _res and "syntax" not in _res:
                            _et_syntax_clean = True
                        else:
                            _et_syntax_clean = False  # reset on failure

                if _et_wrote_file and _et_syntax_clean:
                    _et_grace_rounds -= 1
                    if _et_grace_rounds < 0:
                        logger.debug(
                            "Early termination: CODER for %s — file written + syntax clean, "
                            "stopping after round %d/%d",
                            handoff.files[0] if handoff.files else "?",
                            tool_rounds, max_tool_rounds,
                        )
                        break

            # Compact old tool results to bound O(n²) context growth
            if tool_rounds > 2:
                _compacted, _saved = _compact_tool_history(messages, keep_recent=2)
                if _compacted > 0:
                    logger.debug(
                        "METRIC | type=compaction | role=%s | round=%d | "
                        "rounds_compacted=%d | chars_saved=%d | msgs_after=%d",
                        handoff.role.value, tool_rounds,
                        _compacted, _saved, len(messages),
                    )

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
    result.tool_rounds = tool_rounds
    # Include ALL input tokens (fresh + cache_read + cache_creation) so the
    # UI token counter reflects actual API usage, not just fresh tokens.
    result.input_tokens = (
        usage.input_tokens
        + usage.cache_read_input_tokens
        + usage.cache_creation_input_tokens
    )
    result.output_tokens = usage.output_tokens
    result.cache_read_tokens = usage.cache_read_input_tokens
    result.cache_creation_tokens = usage.cache_creation_input_tokens

    # Cost calculation — cache tokens have different rates:
    #   cache_read = 10% of base input rate
    #   cache_creation = 125% of base input rate (25% surcharge)
    input_rate, output_rate = _get_token_rates(model)
    _cache_read_rate = input_rate * Decimal("0.1")
    _cache_create_rate = input_rate * Decimal("1.25")
    result.cost_usd = float(
        Decimal(usage.input_tokens) * input_rate
        + Decimal(usage.cache_read_input_tokens) * _cache_read_rate
        + Decimal(usage.cache_creation_input_tokens) * _cache_create_rate
        + Decimal(usage.output_tokens) * output_rate
    )

    # --- Sub-agent cost summary metric ---
    _total_in_for_metric = (
        usage.input_tokens + usage.cache_read_input_tokens
        + usage.cache_creation_input_tokens
    )
    _cache_pct = (
        (usage.cache_read_input_tokens / _total_in_for_metric * 100)
        if _total_in_for_metric > 0 else 0
    )
    logger.info(
        "METRIC | type=subagent_cost | role=%s | file=%s | model=%s | "
        "fresh=%d | cached=%d | cache_create=%d | out=%d | "
        "cache_hit_pct=%.0f | cost_usd=%.4f | rounds=%d/%d | wall_s=%.1f",
        handoff.role.value, (handoff.files[0] if handoff.files else "?"),
        model,
        usage.input_tokens, usage.cache_read_input_tokens,
        usage.cache_creation_input_tokens, usage.output_tokens,
        _cache_pct, result.cost_usd,
        tool_rounds, max_tool_rounds, result.duration_seconds,
    )

    # 8. Parse structured JSON BEFORE broadcast so verdict is available
    result.structured_output = _extract_json_block(result.text_output)

    # Hard-enforce Scout output limits so its summary never bloats downstream handoffs
    if handoff.role == SubAgentRole.SCOUT and result.structured_output:
        result.structured_output = _trim_scout_output(result.structured_output)

    # 9. Broadcast completion (role-specific summary for clearer UI)
    _done_summary = f"{len(result.files_written)} files written"
    _verdict: str | None = None
    if handoff.role == SubAgentRole.SCOUT:
        _done_summary = "context gathered"
    elif handoff.role == SubAgentRole.AUDITOR:
        _verdict = (result.structured_output or {}).get("verdict", "unknown")
        _done_summary = f"verdict: {_verdict}"
    elif handoff.role == SubAgentRole.FIXER:
        _done_summary = f"{len(result.files_written)} files patched"
    await _state._broadcast_build_event(
        handoff.user_id, handoff.build_id, "subagent_done", {
            "role": handoff.role.value,
            "handoff_id": handoff.handoff_id,
            "status": result.status.value,
            "files": handoff.files,              # target files from handoff
            "files_written": result.files_written,
            "summary": _done_summary,
            "verdict": _verdict,                 # PASS/FAIL/unknown for auditor, None for others
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

    return result


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _compact_tool_history(
    messages: list[dict],
    *,
    keep_recent: int = 2,
    summary_chars: int = 200,
) -> tuple[int, int]:
    """Replace old tool result content with brief summaries to bound context growth.

    Multi-turn agentic loops re-send full message history every round, causing
    O(n^2) token growth.  This function compacts tool results from older rounds
    to short summaries, transforming the growth to O(n).

    Industry precedent: LangChain ``trim_messages``, AutoGen ``clear_history``.

    Mutates *messages* in place.  Never touches the first user message (the
    original assignment at index 0) or assistant text blocks.

    Returns ``(rounds_compacted, chars_saved)`` for observability.
    """
    # Identify tool-round boundaries: a round is an assistant message with
    # tool_use blocks followed by a user message with tool_result blocks.
    round_indices: list[tuple[int, int]] = []  # (assistant_idx, user_idx)
    for i in range(1, len(messages) - 1):
        msg = messages[i]
        next_msg = messages[i + 1] if i + 1 < len(messages) else None
        if (
            msg.get("role") == "assistant"
            and isinstance(msg.get("content"), list)
            and any(b.get("type") == "tool_use" for b in msg["content"] if isinstance(b, dict))
            and next_msg
            and next_msg.get("role") == "user"
        ):
            round_indices.append((i, i + 1))

    if len(round_indices) <= keep_recent:
        return (0, 0)  # nothing to compact

    # Compact all rounds except the last `keep_recent`
    rounds_to_compact = round_indices[: len(round_indices) - keep_recent]
    _chars_saved = 0
    for _asst_idx, user_idx in rounds_to_compact:
        user_msg = messages[user_idx]
        content = user_msg.get("content")
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    raw = block.get("content", "")
                    if isinstance(raw, str) and len(raw) > summary_chars + 20:
                        _new = f"[Compacted] {raw[:summary_chars]}..."
                        _chars_saved += len(raw) - len(_new)
                        block["content"] = _new
        elif isinstance(content, str) and len(content) > summary_chars + 20:
            _new = f"[Compacted] {content[:summary_chars]}..."
            _chars_saved += len(content) - len(_new)
            user_msg["content"] = _new

    return (len(rounds_to_compact), _chars_saved)


def _trim_scout_output(data: dict) -> dict:
    """Enforce hard character limits on Scout structured output.

    Prevents Scout from returning bloated summaries that inflate the
    context of every downstream CODER and AUDITOR handoff.

    Limits match the caps defined in the Scout system prompt.
    """
    _MAX_TREE = 500
    _MAX_INTERFACES = 10
    _MAX_INTERFACE_EXPORTS = 150
    _MAX_PATTERN_KEYS = 4
    _MAX_PATTERN_VALUE = 150
    _MAX_IMPORTS = 10
    _MAX_DIRECTIVES = 10
    _MAX_DIRECTIVE_LEN = 200
    _MAX_RECOMMENDATIONS = 400

    out: dict = {}
    if "directory_tree" in data:
        out["directory_tree"] = str(data["directory_tree"])[:_MAX_TREE]
    if "key_interfaces" in data:
        ifaces = (data["key_interfaces"] or [])[:_MAX_INTERFACES]
        out["key_interfaces"] = [
            {
                "file": str(i.get("file", "")),
                "exports": str(i.get("exports", ""))[:_MAX_INTERFACE_EXPORTS],
            }
            for i in ifaces
            if isinstance(i, dict)
        ]
    if "patterns" in data:
        pats = data.get("patterns") or {}
        trimmed: dict = {}
        for k, v in list(pats.items())[:_MAX_PATTERN_KEYS]:
            trimmed[str(k)] = str(v)[:_MAX_PATTERN_VALUE]
        out["patterns"] = trimmed
    if "imports_map" in data:
        imap = data.get("imports_map") or {}
        out["imports_map"] = dict(list(imap.items())[:_MAX_IMPORTS])
    if "directives" in data:
        dirs = (data["directives"] or [])[:_MAX_DIRECTIVES]
        out["directives"] = [str(d)[:_MAX_DIRECTIVE_LEN] for d in dirs]
    if "recommendations" in data:
        out["recommendations"] = str(data["recommendations"])[:_MAX_RECOMMENDATIONS]
    return out


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _default_model_for_role(role: SubAgentRole) -> str:
    """Return the default model for a given role.

    * Coder / Fixer → use the main builder model (Opus-class)
    * Auditor       → use the dedicated auditor model (defaults to questionnaire tier)
    * Scout / other → use the lighter questionnaire model
    """
    if role in (SubAgentRole.CODER, SubAgentRole.FIXER):
        return _state.settings.LLM_BUILDER_MODEL
    if role == SubAgentRole.AUDITOR:
        return _state.settings.LLM_AUDITOR_MODEL
    # Scout and others use the cheaper/faster questionnaire model
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
