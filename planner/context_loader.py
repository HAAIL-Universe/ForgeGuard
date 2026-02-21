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
z:/ForgeCollection/Forge/Contracts/ path.
"""

from __future__ import annotations
from pathlib import Path

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
  7. Include boot_script: true in stack if the project needs a setup script (§9.8)

TOOLS AVAILABLE:
  write_plan(plan_json, project_name) — OUTPUT your plan and end the session

WHAT IS PRE-LOADED IN YOUR CONTEXT:
  - builder_contract.md (plan.json structure and builder expectations)
  - All 8 contract template files (blueprint, stack, schema, phases, etc.)
  - The user's project contracts (blueprint, stack, schema, etc.) are in the
    first user message — they were fetched from the database for you already.

WORKFLOW:
  Step 1. Read the project contracts in the user message carefully.
          They contain everything you need — do NOT attempt to read files from
          disk, they are not the user's files and are not relevant.
  Step 2. When you have a complete plan, call write_plan.

DISCIPLINE:
  - Do NOT call write_plan with a partial or incomplete plan.
  - Do NOT attempt to read files or list directories — no filesystem tools
    are available and the user's data is already in the user message.
  - Do NOT include fields in plan_json that are not in the schema — extra fields
    will cause a validation error and you will need to fix and retry.
  - The plan must be self-contained: the builder uses ONLY plan.json to build.
    It must not need to ask clarifying questions.
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
            "text": PLANNER_ROLE,
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
