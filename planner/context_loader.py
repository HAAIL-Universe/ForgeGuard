"""
context_loader.py — Contract loading and prompt cache preparation.

Prompt caching strategy
-----------------------
The system prompt + planner-relevant contracts form a single static block.
This block changes rarely (only when you edit contract files).
We mark it with cache_control so Anthropic caches the KV state:

  First call:  full token price (cache WRITE)
  Next calls:  ~10% of token price (cache READ) — within 5-minute window

On a typical 4-8 turn planning loop this saves ~80% of the input token cost
for the contracts portion of each call.

Cache boundary placement
------------------------
We put cache_control on the LAST block we want cached (the contracts block).
Everything before that block is automatically included in the cache.
Tool results and the user's project request are NOT cached — they change
every turn and must not be frozen.

What the planner needs (and what it does NOT)
---------------------------------------------
The planner produces plan.json.  It does NOT:
  - Run audit checks (auditor_prompt.md, audit_reference.md are irrelevant)
  - Run product-design conversations (system_prompt.md / Director is irrelevant)

Loading those files was a ~9,400 token waste per planning run.

Token budget for the cached block
----------------------------------
  builder_contract.md    ~7,900 tokens  ← defines plan.json structure
  8 template files       ~8,100 tokens  ← contract format references
  ─────────────────────────────────────
  Total cached block     ~16,000 tokens

  Without caching (8 turns): 8 × 16,000 × $3.00/MTok = ~$0.38
  With caching (8 turns):    1 × $3.75/MTok + 7 × $0.30/MTok = ~$0.09
  Savings: ~76%

Fetching other governance contracts on demand
---------------------------------------------
If the planner ever needs system_prompt.md, auditor_prompt.md, or any other
governance file, it can call read_file() — those are available via the
z:/ForgeCollection/ForgeGuard/Forge/Contracts/ path.
"""

from __future__ import annotations
import sys
from pathlib import Path

# Ensure ForgeGuard root is importable so forge_constitution can be found
_forgeguard_root = str(Path(__file__).resolve().parent.parent)
if _forgeguard_root not in sys.path:
    sys.path.insert(0, _forgeguard_root)

from forge_constitution import CONSTITUTION

CONTRACTS_DIR = Path(__file__).parent.parent.parent / "Forge" / "Contracts"

# Core contracts — always loaded into the planner's system prompt.
# ONLY include contracts the planner actually needs to produce plan.json.
# Audit contracts (auditor_prompt.md, audit_reference.md) and the Director
# chat prompt (system_prompt.md) are irrelevant to planning — fetch them
# on demand via read_file() if ever needed.
CORE_CONTRACTS = [
    "builder_contract.md",
]

# Template contracts — loaded by default, filterable
TEMPLATE_CONTRACTS = [
    "templates/blueprint_template.md",
    "templates/phases_template.md",
    "templates/stack_template.md",
    "templates/schema_template.md",
    "templates/physics_template.yaml",
    "templates/boundaries_template.json",
    "templates/manifesto_template.md",
    "templates/ui_template.md",
]

# For projects that explicitly need no frontend
NO_UI_KEYWORDS = {"api only", "api-only", "no frontend", "no ui", "backend only"}


def load_contracts(project_hint: str = "", include_templates: bool = True) -> str:
    """
    Load and concatenate contracts into a single string.

    Args:
        project_hint: lowercased project request — used to skip irrelevant templates.
        include_templates: if False, loads core contracts only (faster for simple cases).

    Returns:
        A single string with all contracts separated by headers.
        Missing files are skipped with a warning comment — never raise on missing files
        because the planner must be able to run even in a partially set-up repo.
    """
    hint_lower = project_hint.lower()
    skip_ui = any(kw in hint_lower for kw in NO_UI_KEYWORDS)

    files_to_load: list[str] = list(CORE_CONTRACTS)
    if include_templates:
        for tmpl in TEMPLATE_CONTRACTS:
            if "ui_template" in tmpl and skip_ui:
                continue  # saves ~800 tokens for API-only projects
            files_to_load.append(tmpl)

    parts: list[str] = []
    for filename in files_to_load:
        path = CONTRACTS_DIR / filename
        if not path.exists():
            parts.append(f"=== CONTRACT: {filename} [MISSING — skipped] ===\n")
            continue
        content = path.read_text(encoding="utf-8")
        parts.append(f"=== CONTRACT: {filename} ===\n{content}\n")

    return "\n".join(parts)


PLANNER_ROLE = """\
You are the Forge Planner Agent.

Your job is to analyze the user's project request and produce a structured
Forge build plan (plan.json) that the Builder Agent can execute without
needing to re-read all governance contracts.

You are operating under the Forge governance framework. The contracts below
define the exact structure the builder expects from your plan. Your plan MUST:

  1. Map every Forge contract template to concrete project-specific content
  2. Break work into phases that satisfy builder_contract.md §9-§10
  3. Include a summary.key_constraints list so the builder internalizes the
     most important rules without re-reading the contracts in full
  4. Identify every file the builder will create, phase by phase, with layer
     and action fields matching the builder's file-manifest expectations
  5. Claim every database table in exactly one phase (builder_contract.md §9.6)
  6. Include wires_from_phase on any phase that imports from a prior phase (§9.5)

CONSTRAINTS — read carefully:
  - Do NOT include USER_INSTRUCTIONS.md in any phase file_manifest. It is
    generated automatically after the build and must NOT be a planned deliverable.
  - Do NOT include boot.ps1 in any phase file_manifest. Setup scripts are not
    part of the automated build pipeline.
  - If the project manifest includes a max_phases constraint <= 3, this is a
    MINI-BUILD. Mini-build rules OVERRIDE contract content:
    • NO auth of any kind — exclude JWT, OAuth, login, sessions, auth.py,
      security.py, any auth middleware, even if contracts mention them.
    • NO user management — no user table, no user endpoints.
    • Public unauthenticated endpoints only. Auth is deferred by design.

WHAT IS PRE-LOADED IN YOUR CONTEXT:
  - builder_contract.md (plan.json structure and builder expectations)
  - All contract template files (blueprint, stack, schema, phases, ui, etc.)

WHAT IS IN THE FIRST USER MESSAGE:
  - A manifest of available project contracts (type names and char counts only).
    The actual contract CONTENT is NOT pre-loaded — you must fetch it via tool.

TOOLS AVAILABLE:
  forge_get_project_contract(contract_type) — fetch a project contract by type
  write_plan(plan_json, project_name)       — output your plan and end the session

WORKFLOW:
  Step 1. In your FIRST turn, call forge_get_project_contract for EVERY contract
          listed in the manifest — make all calls in PARALLEL in a single turn.
          Do NOT skip any contract, including ui. Fetch every type listed.
  Step 2. When you have read all contracts and have a complete plan, call write_plan.

DISCIPLINE:
  - Do NOT call write_plan with a partial or incomplete plan.
  - Do NOT attempt to read files or list directories — no filesystem tools
    are available beyond forge_get_project_contract and write_plan.
  - Do NOT include fields in plan_json that are not in the schema — extra fields
    will cause a validation error and you will need to fix and retry.
  - The plan must be self-contained: the builder uses ONLY plan.json to build.
    It must not need to ask clarifying questions.

DEPENDENCY MAPPING (REQUIRED):
  For EVERY file in file_manifest, you MUST specify:

  1. depends_on: list of file paths (from the same plan) that this file imports from.
     - Router depends on service → ["app/services/timer_service.py"]
     - Service depends on repo + models → ["app/repos/timer_repo.py", "app/models/timer.py"]
     - Test depends on the module it tests → ["app/services/timer_service.py"]
     - Frontend component depends on hooks → ["src/hooks/useTimer.ts"]
     - Files with no dependencies → [] (but still include the field)

  2. exports: list of public interface signatures this file MUST export.
     One string per class, function, enum, type, or route group.
     Include parameter names + types for functions/methods.
     Include field names + types for models/interfaces.
     Include route prefixes + HTTP methods for routers.
     These form the CONTRACT — downstream files code against these exact signatures.

     Examples:
       "class Timer(BaseModel): id: str, name: str, duration_seconds: int, state: TimerState"
       "class TimerService.__init__(self, repo: TimerRepo)"
       "async def TimerService.create_timer(self, name: str, duration: int) -> Timer"
       "enum TimerState(str, Enum): IDLE, RUNNING, PAUSED"
       "router prefix: /timers — POST / GET /{id} DELETE /{id} PUT /{id}/start"
       "interface TimerState { id: string; name: string; durationSeconds: number }"
       "function useTimer(timerId: string): { timer, start, pause, reset }"

  Dependency mapping rules:
     - If file A depends_on file B, file B's exports MUST contain everything A needs.
     - Field names must be consistent: if backend uses snake_case, list snake_case names
       and note the frontend camelCase equivalent in the frontend file's exports.
     - Every __init__.py needed for Python package imports MUST appear in the manifest.
     - Router prefix chains must be explicit: if main.py mounts at /api and router adds
       /timers, the router's exports should say "router prefix: /api/timers".
     - async vs sync must be explicit: use "async def" for async methods, "def" for sync.

EXAMPLE — CORRECT PHASE STRUCTURE:
  {
    "phase_number": 0,
    "phase_name": "Backend Foundation",
    "objectives": ["Set up FastAPI app structure", "Create database models and migrations", "Implement core CRUD repositories"],
    "acceptance_criteria": ["Server starts without errors", "All models have corresponding repos", "Database migrations run cleanly"],
    "file_manifest": [
      {
        "path": "app/__init__.py", "purpose": "Package marker", "language": "python",
        "layer": "config", "action": "create", "estimated_lines": 1,
        "depends_on": [], "exports": []
      },
      {
        "path": "app/models/user.py", "purpose": "User data model", "language": "python",
        "layer": "model", "action": "create", "estimated_lines": 40,
        "depends_on": [],
        "exports": [
          "class User(BaseModel): id: UUID, email: str, name: str, created_at: datetime",
          "class CreateUserRequest(BaseModel): email: str, name: str"
        ]
      },
      {
        "path": "app/repos/user_repo.py", "purpose": "User CRUD repository", "language": "python",
        "layer": "repo", "action": "create", "estimated_lines": 50,
        "depends_on": ["app/models/user.py"],
        "exports": [
          "class UserRepo.__init__(self)",
          "async def UserRepo.create(self, req: CreateUserRequest) -> User",
          "async def UserRepo.get_by_id(self, user_id: UUID) -> User | None"
        ]
      },
      {
        "path": "app/main.py", "purpose": "FastAPI app entry point", "language": "python",
        "layer": "entrypoint", "action": "create", "estimated_lines": 30,
        "depends_on": ["app/routers/users.py"],
        "exports": ["router prefix: /api/users (from users_router)"]
      }
    ],
    "wires_from_phase": []
  }

  Note: each file has layer and action fields. layer matches the boundaries
  contract (entrypoint, router, service, repo, model, infrastructure, test).
  action is "create" for new files or "modify" for existing files.

ANTI-EXAMPLE — WRONG PHASE:
  {
    "phase_number": 0,
    "phase_name": "Setup Everything",
    "objectives": ["Build the whole app"],
    "file_manifest": [
      {"path": "app.py", "purpose": "everything"}
    ]
  }

  This is wrong because: vague objectives, no acceptance_criteria, single
  monolithic file, missing layer/action/estimated_lines fields.

FAILURE MODES:
  - If a contract is missing or empty: plan based on the contracts you DO have.
    Note the missing contract in summary.key_constraints.
  - If contracts conflict (e.g. schema mentions a table that blueprint doesn't):
    follow schema > physics > boundaries > stack > blueprint > ui priority.
  - If write_plan returns a validation error: read the error message carefully,
    fix ONLY the invalid fields, and re-call write_plan. Do NOT regenerate
    the entire plan from scratch.
  - If the project is too large for the phase limit: prioritise MVP features
    from the blueprint. Defer nice-to-haves to later phases.
"""


def build_system_prompt(project_hint: str = "") -> list[dict]:
    """
    Build the system prompt as a list of blocks with prompt caching enabled.

    Returns a list of two dicts:
      [0] planner role instructions (~400 tokens, not explicitly cached)
      [1] builder_contract.md + 8 templates (~16,000 tokens, marked with cache_control)

    Both blocks are included in the cache — the cache boundary is on [1],
    which tells Anthropic to cache everything up to and including that block.

    Usage:
        system = build_system_prompt(project_request)
        response = client.messages.create(system=system, ...)
    """
    contracts_text = load_contracts(project_hint=project_hint, include_templates=True)

    return [
        {
            "type": "text",
            "text": f"{CONSTITUTION}\n\n{PLANNER_ROLE}",
            # No cache_control here — it is included in the cache because
            # the next block sets the cache boundary.
        },
        {
            "type": "text",
            "text": f"=== FORGE GOVERNANCE CONTRACTS ===\n\n{contracts_text}",
            # THIS is the cache boundary.
            # "ephemeral" = cached for 5 minutes, then evicted automatically.
            # Cost: full input price on first call, ~10% on subsequent calls
            # within the same 5-minute window.
            "cache_control": {"type": "ephemeral"},
        },
    ]
