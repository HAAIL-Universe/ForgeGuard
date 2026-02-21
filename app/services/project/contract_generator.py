"""Contract generation — LLM-based contract creation, cancellation, push-to-git."""

import asyncio
import json
import logging
import time
from pathlib import Path
from uuid import UUID

from app.clients.llm_client import chat as llm_chat, chat_streaming as llm_chat_streaming
from app.config import settings, get_model_for_role
from app.repos import build_repo
from app.repos.project_repo import (
    get_contracts_by_project,
    get_project_by_id,
    save_generation_metrics,
    snapshot_contracts,
    update_project_status,
    upsert_contract,
)
from app.ws_manager import manager

from .questionnaire import QUESTIONNAIRE_SECTIONS, MINI_QUESTIONNAIRE_SECTIONS, _sections_for_mode

logger = logging.getLogger(__name__)


# Late imports to avoid circular dependency (contract_utils imports from us)
def _get_tool_use_helpers():
    from .contract_utils import (
        CONTEXT_TOOLS_GREENFIELD,
        build_tool_use_system_prompt,
        execute_greenfield_tool,
        generate_contract_with_tools,
    )
    return CONTEXT_TOOLS_GREENFIELD, build_tool_use_system_prompt, execute_greenfield_tool, generate_contract_with_tools


class ContractCancelled(Exception):
    """Raised when contract generation is cancelled by the user."""


# Active contract generation tasks — maps project-id → cancel Event
_active_generations: dict[str, asyncio.Event] = {}

CONTRACT_TYPES = [
    "manifesto",
    "blueprint",
    "stack",
    "schema",
    "physics",
    "boundaries",
    "ui",
    "phases",
    "builder_directive",
]

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates" / "contracts"
BUILDER_EXAMPLES_DIR = Path(__file__).resolve().parent.parent / "templates" / "builder_examples"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _format_answers_for_prompt(project: dict, answers: dict) -> str:
    """Format all questionnaire answers into a readable text block for prompts."""
    lines = [
        f"Project name: {project['name']}",
        f"Description: {project.get('description', 'N/A')}",
        "",
    ]
    for section in QUESTIONNAIRE_SECTIONS:
        section_data = answers.get(section)
        lines.append(f"### {section}")
        if not section_data:
            lines.append("(no data collected)")
        elif isinstance(section_data, dict):
            for k, v in section_data.items():
                if isinstance(v, list):
                    lines.append(f"- {k}:")
                    for item in v:
                        lines.append(f"  - {item}")
                elif isinstance(v, dict):
                    lines.append(f"- {k}: {json.dumps(v, indent=2)}")
                else:
                    lines.append(f"- {k}: {v}")
        else:
            lines.append(str(section_data))
        lines.append("")
    return "\n".join(lines)


def _load_generic_template(contract_type: str) -> str | None:
    """Load a generic builder example as a structural reference.

    Templates live in app/templates/builder_examples/ and are fully
    fictional (TaskFlow app) so they never leak ForgeGuard's own docs
    into user project generation.
    """
    ext_map = {
        "physics": ".yaml",
        "boundaries": ".json",
    }
    ext = ext_map.get(contract_type, ".md")
    path = BUILDER_EXAMPLES_DIR / f"{contract_type}_example{ext}"
    if path.exists():
        try:
            return path.read_text(encoding="utf-8")
        except Exception:
            return None
    return None


# Per-contract generation instructions
_CONTRACT_INSTRUCTIONS: dict[str, str] = {
    "blueprint": """\
Generate a project blueprint. Include:
1) Product intent — 3-4 bullets: what it does, who it's for, why it exists
2) Core interaction invariants — 5-7 MUST-hold rules (one line each)
3) MVP scope — must-ship features (one-liner per feature) AND not-MVP list
4) Hard boundaries — one line per architectural layer (Routers, Services, Repos, Clients, etc.)
5) Deployment target — one line

Facts only. One line per item. No prose paragraphs.""",

    "manifesto": """\
Generate a project manifesto with 4-5 non-negotiable principles.
Each principle: title + 2-3 bullets (facts only, no elaboration).
Include a "Confirm-before-write" list (what needs user confirmation vs. exempt).
Project-specific — no generic platitudes.""",

    "stack": """\
Generate a technology stack document. Sections:
- Backend (language, framework, version, key libraries)
- Database (engine, version, connection method)
- Auth (method, provider, flow)
- Frontend (framework, bundler, key libraries)
- Testing (framework, coverage target)
- Deployment (platform, scale)
- Environment Variables table (name, description, required/optional)
- forge.json schema (JSON block)

Facts only — no rationale or justification prose.""",

    "schema": """\
Generate a database schema. Include:
- Conventions: 3 bullets (naming, common columns, key patterns)
- CREATE TABLE SQL for every table (types, constraints, indexes, ENUMs)

Use PostgreSQL syntax. Every column the app needs must be defined. No traceability matrix.""",

    "phases": """\
Generate a phase breakdown with 6-12 phases.
Each phase MUST include exactly these 4 fields:
- Phase number and name (e.g. "Phase 0 — Genesis")
- Objective: 1 sentence
- Deliverables: bullet list (each item is one line — no sub-bullets)
- Schema coverage: table names only
- Exit criteria: bullet list (concrete, testable — one line each)

Phase 0 is always Genesis. Final phase must include README.md deliverable.
Phases ordered by dependency.""",

    "phases_mini": """\
MINI BUILD — EXACTLY 2 phases. No more, no fewer.
IGNORE any structural reference that has more phases — output only these two.

Phase 0 — Backend Scaffold: entire backend (scaffold, DB, migrations, all API endpoints, auth, boot script).
Phase 1 — Frontend & Ship: entire frontend (all pages, components, API integration, README.md).

Each phase uses exactly these 4 fields:
- Phase number and name
- Objective: 1 sentence
- Deliverables: bullet list (one line each — exhaustive, every feature in one of these 2 phases)
- Schema coverage: table names
- Exit criteria: bullet list (concrete, testable)

CRITICAL: Exactly Phase 0 and Phase 1. Nothing else.""",

    # ── Mini-specific instructions for other contract types ────────────
    "blueprint_mini": """\
Generate a concise blueprint for a 2-phase mini build:
1) Product intent — 2-3 bullets (what it does, who it's for)
2) Core invariants — 3-5 MUST-hold rules (one line each)
3) MVP scope — features that fit in 2 phases AND not-in-scope list
4) Layer boundaries — one line per layer (routes → services → repos)
5) Deployment — Docker-ready local dev

One line per item. No prose paragraphs.""",

    "manifesto_mini": """\
Generate a manifesto with 3-5 principles for a 2-phase build.
Each principle: title + 2-3 bullets. Facts only, no ceremony.
No confirm-before-write section needed.""",

    "stack_mini": """\
Generate a tech stack for a 2-phase proof-of-concept:
- Backend (language, framework, version, key libraries)
- Database (engine, version)
- Frontend (framework, bundler, key libraries)
- Deployment (Docker Compose)
- Environment Variables table (name, description, required/optional)

Facts only — no rationale.""",

    "schema_mini": """\
Generate a database schema. Include:
- Conventions section (naming, common columns)
- CREATE TABLE SQL for all tables needed by the MVP
- Keep minimal — only tables required for core features

Use PostgreSQL syntax. Only define tables the app actually needs for its MVP scope.""",

    "ui_mini": """\
Generate a concise UI/UX blueprint. Include:
1) App Shell & Layout — structure, navigation
2) Screens/Views — spec for each page (what it shows, key interactions)
3) Component list — reusable components needed
4) Visual Style — color palette, typography basics
5) Key User Flows — 2-3 primary user journeys

This is a 2-phase mini build. Keep the UI scope focused on core functionality.""",

    "physics_mini": """\
Generate an API specification in YAML format. Include:
- info: title, version, description
- paths: every API endpoint with method, summary, auth, request/response shapes
- schemas: data model definitions

Use the custom Forge physics YAML format (NOT OpenAPI).
Keep it focused — only endpoints needed for the MVP features.""",

    "boundaries_mini": """\
Generate an architectural boundaries spec in JSON format:
{
  "description": "Layer boundary rules...",
  "layers": [
    {
      "name": "<layer_name>",
      "glob": "<file_glob_pattern>",
      "forbidden": [
        { "pattern": "<regex_or_import>", "reason": "<why forbidden>" }
      ]
    }
  ],
  "known_violations": []
}
Define 3-4 layers with basic separation of concerns. Keep it simple for a mini build.
Each entry in "forbidden" MUST be an object with "pattern" and "reason" keys — never a plain string.
Output MUST be valid JSON.""",

    "builder_directive_mini": """\
Generate a builder directive — operational instructions for the AI builder.
Include:
- Step-by-step: read contracts → Phase 0 (backend) → Phase 1 (frontend) → commit
- Phase list: Phase 0 — Backend Scaffold, Phase 1 — Frontend & Ship
- Project summary (one sentence)
- boot_script flag

CRITICAL: There are exactly 2 phases. The builder must execute both to completion.
Keep it concise — this is a mini build.""",

    "ui": """\
Generate a UI/UX blueprint. Include:
1) App Shell & Layout — device priority, shell structure, navigation
2) Screens/Views — each screen: what it shows, key interactions, empty state
3) Component Inventory — table of reusable components (name + one-line description)
4) Visual Style — palette (hex values), typography (font, size), density (one line each)
5) Key User Flows — 3-4 primary journeys (one line per step)
6) What This Is NOT — explicit out-of-scope UI list

No "Interaction Patterns" section.""",

    "physics": """\
Generate an API physics specification in YAML format. Structure:
- info: title, version, description
- paths: every API endpoint with:
  - HTTP method (get/post/put/delete)
  - summary
  - auth requirement (none, token, github-token)
  - request body shape (if applicable)
  - response shape
  - query parameters (if applicable)
- schemas: Pydantic/data model definitions

Use the custom Forge physics YAML format (NOT OpenAPI).
Group endpoints by resource with comments.
Every endpoint the app needs MUST be listed.""",

    "boundaries": """\
Generate an architectural boundaries specification in JSON format. Structure:
{
  "description": "Layer boundary rules for <project_name>...",
  "layers": [
    {
      "name": "<layer_name>",
      "glob": "<file_glob_pattern>",
      "forbidden": [
        { "pattern": "<regex_or_import>", "reason": "<why forbidden>" }
      ]
    }
  ],
  "known_violations": []
}

Define 4-6 layers (e.g. routers, services, repos, clients, audit/engine).
Each layer has forbidden imports/patterns that enforce separation of concerns.
Output MUST be valid JSON.""",

    "builder_directive": """\
Generate a builder directive — operational instructions for the AI builder.
Include:
- AEM status (enabled/disabled) and auto-authorize setting
- Step-by-step instructions (read contracts → execute phases → run audit → commit)
- Autonomy rules (when to auto-commit, when to stop and ask)
- Phase list with phase numbers and names
- Project summary (one sentence)
- boot_script flag

Keep it concise.""",
}


# ---------------------------------------------------------------------------
# Template contract generators — zero LLM calls for pure-format contracts
# ---------------------------------------------------------------------------

# Standard Python/FastAPI layer boundaries (used for ~90% of ForgeGuard projects)
_PYTHON_FASTAPI_BOUNDARIES: dict = {
    "description": "",
    "layers": [
        {
            "name": "routers",
            "glob": "app/routers/**/*.py",
            "forbidden": [
                {"pattern": "from app.repos", "reason": "Routers must not import repos directly — use services"},
                {"pattern": "asyncpg|psycopg|sqlalchemy", "reason": "No DB drivers in routers"},
            ],
        },
        {
            "name": "services",
            "glob": "app/services/**/*.py",
            "forbidden": [
                {"pattern": "from fastapi import Request|Response", "reason": "Services must not be HTTP-aware"},
                {"pattern": "from app.routers", "reason": "Services must not import routers"},
            ],
        },
        {
            "name": "repos",
            "glob": "app/repos/**/*.py",
            "forbidden": [
                {"pattern": "from app.services", "reason": "Repos must not import services — data access only"},
                {"pattern": "from fastapi", "reason": "Repos are HTTP-unaware"},
            ],
        },
        {
            "name": "clients",
            "glob": "app/clients/**/*.py",
            "forbidden": [
                {"pattern": "from app.repos", "reason": "Clients must not access DB directly"},
                {"pattern": "from app.services", "reason": "Clients are thin external API wrappers only"},
            ],
        },
    ],
    "known_violations": [],
}

# Node/Express variant
_NODE_BOUNDARIES: dict = {
    "description": "",
    "layers": [
        {
            "name": "routes",
            "glob": "src/routes/**/*.{js,ts}",
            "forbidden": [
                {"pattern": "require.*db|import.*db", "reason": "Routes must not access DB directly — use services"},
            ],
        },
        {
            "name": "services",
            "glob": "src/services/**/*.{js,ts}",
            "forbidden": [
                {"pattern": "req\\.body|res\\.json|express", "reason": "Services must not be HTTP-aware"},
            ],
        },
        {
            "name": "repositories",
            "glob": "src/repos/**/*.{js,ts}",
            "forbidden": [
                {"pattern": "from.*services|require.*services", "reason": "Repos are data access only"},
            ],
        },
    ],
    "known_violations": [],
}

# Contracts generated from templates instead of LLM calls
_TEMPLATE_CONTRACTS: frozenset[str] = frozenset({"stack", "boundaries", "builder_directive"})


def _template_stack(project: dict, answers_data: dict) -> tuple[str, dict]:
    """Generate stack contract from questionnaire answers. Returns (content, forge_config).

    forge_config is the machine-readable dict that gets written as forge.json
    at build start — it holds test commands, venv path, entry module, etc.
    """
    name = project.get("name", "Unnamed Project")
    tech = answers_data.get("tech_stack") or {}
    deployment = answers_data.get("deployment_target") or {}

    backend = str(tech.get("backend") or "Python 3.12+ / FastAPI")
    frontend = str(tech.get("frontend") or "React + TypeScript")
    database = str(tech.get("database") or "PostgreSQL")
    auth = str(tech.get("auth") or "JWT bearer tokens")
    testing = str(tech.get("testing") or "pytest (backend), Vitest (frontend)")
    deploy_platform = str(tech.get("deployment") or deployment.get("platform") or "Docker Compose")
    scale = str(deployment.get("scale") or "<100 concurrent users")

    # Determine backend runtime for forge.json
    b = backend.lower()
    if any(x in b for x in ("python", "fastapi", "django", "flask")):
        lang, test_fw, dep_file, venv, test_cmd, entry = (
            "python", "pytest", "requirements.txt", ".venv", "pytest -x", "app.main"
        )
    elif any(x in b for x in ("node", "express", "fastify", "nest")):
        lang, test_fw, dep_file, venv, test_cmd, entry = (
            "node", "jest", "package.json", "node_modules", "npm test", "src/index.js"
        )
    else:
        lang, test_fw, dep_file, venv, test_cmd, entry = (
            "python", "pytest", "requirements.txt", ".venv", "pytest -x", "app.main"
        )

    f = frontend.lower()
    fe_enabled = bool(frontend) and "none" not in f and "no frontend" not in f
    fe_dir = "web" if fe_enabled else ""
    fe_build = "npm run build" if fe_enabled else ""
    fe_test = "npm test" if fe_enabled else ""

    forge_config = {
        "project_name": name,
        "backend": {
            "language": lang,
            "entry_module": entry,
            "test_framework": test_fw,
            "test_command": test_cmd,
            "dependency_file": dep_file,
            "venv_path": venv,
        },
        "frontend": {
            "enabled": fe_enabled,
            "dir": fe_dir,
            "build_cmd": fe_build,
            "test_cmd": fe_test,
        },
    }

    fe_section = (
        f"\n## Frontend\n- **Framework:** {frontend}\n"
        if fe_enabled
        else "\n## Frontend\n- Not applicable\n"
    )

    forge_json_str = json.dumps(forge_config, indent=2)

    content = f"""# {name} — Technology Stack

## Backend
- **Stack:** {backend}

## Database
- **Engine:** {database}

## Auth
- **Method:** {auth}
{fe_section}
## Testing
- **Framework:** {testing}

## Deployment
- **Platform:** {deploy_platform}
- **Scale:** {scale}

## Environment Variables

| Name | Description | Required |
|------|-------------|----------|
| DATABASE_URL | Database connection string | Yes |
| JWT_SECRET | Secret key for token signing | Yes |
| CORS_ORIGINS | Allowed CORS origins | Yes |

## forge.json

```json
{forge_json_str}
```
"""
    return content, forge_config


def _template_boundaries(project: dict, answers_data: dict) -> str:
    """Generate boundaries contract JSON from detected stack type."""
    import copy
    name = project.get("name", "project")
    tech = answers_data.get("tech_stack") or {}
    b = str(tech.get("backend") or "").lower()

    if any(x in b for x in ("node", "express", "fastify", "nest")):
        tmpl = copy.deepcopy(_NODE_BOUNDARIES)
    else:
        tmpl = copy.deepcopy(_PYTHON_FASTAPI_BOUNDARIES)

    tmpl["description"] = f"Layer boundary rules for {name}"
    return json.dumps(tmpl, indent=2)


def _template_builder_directive(
    project: dict,
    answers_data: dict,
    prior_contracts: dict,
) -> str:
    """Generate builder_directive from phases contract + fixed operational template."""
    import re as _re
    name = project.get("name", "Unnamed Project")

    # Extract phase list from the already-generated phases contract
    phases_text = prior_contracts.get("phases", "")
    phase_lines = _re.findall(r"^## (Phase \d+[^\n]+)", phases_text, _re.MULTILINE)
    if not phase_lines:
        phase_lines = ["Phase 0 — Genesis", "Phase 1 — Ship"]
    phase_list = "\n".join(f"- {p}" for p in phase_lines)

    return f"""# {name} — Builder Directive

## AEM
- status: enabled
- auto_authorize: true

## Instructions
1. Read all Forge/Contracts/ files before writing any code
2. Execute phases in order — do not skip or merge phases
3. After each phase: run tests, verify exit criteria, commit
4. Final phase: run audit, write README.md, final commit

## Autonomy Rules
- Auto-commit on successful phase completion (all exit criteria met)
- Stop and request clarification if requirements are ambiguous
- If tests fail: attempt up to 3 self-correction cycles before stopping
- Do not modify files outside the current phase scope

## Phase List
{phase_list}

## Project Summary
{name}

## Config
- boot_script: true
- forge_config: forge.json
"""


async def _store_forge_config(project_id: UUID, forge_config: dict) -> None:
    """Persist forge_config inside questionnaire_state JSONB (no schema migration needed)."""
    from app.repos.db import get_pool
    pool = await get_pool()
    await pool.execute(
        """UPDATE projects
           SET questionnaire_state = jsonb_set(
               COALESCE(questionnaire_state, '{}'),
               '{forge_config}',
               $2::jsonb
           ),
           updated_at = now()
           WHERE id = $1""",
        project_id,
        json.dumps(forge_config),
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def generate_contracts(
    user_id: UUID,
    project_id: UUID,
) -> list[dict]:
    """Generate all contract files from questionnaire answers using the LLM.

    Each contract is generated individually with a contract-specific system
    prompt that references the Forge example contract as a structural blueprint.
    Raises ValueError if questionnaire is not complete.
    """
    project = await get_project_by_id(project_id)
    if not project:
        raise ValueError("Project not found")
    if str(project["user_id"]) != str(user_id):
        raise ValueError("Project not found")

    qs = project.get("questionnaire_state") or {}
    completed = qs.get("completed_sections", [])
    build_mode = project.get("build_mode", "full")
    sections = _sections_for_mode(build_mode)
    remaining = [s for s in sections if s not in completed]

    if remaining:
        raise ValueError(
            f"Questionnaire is not complete. Remaining sections: {', '.join(remaining)}"
        )

    answers = qs.get("answers", {})
    answers_text = _format_answers_for_prompt(project, answers)

    # Pick LLM provider
    provider = settings.LLM_PROVIDER.strip().lower() if settings.LLM_PROVIDER else ""
    if not provider:
        provider = "anthropic" if settings.ANTHROPIC_API_KEY else "openai"
    if provider == "openai":
        llm_api_key = settings.OPENAI_API_KEY
        llm_model = settings.OPENAI_MODEL
    else:
        llm_api_key = settings.ANTHROPIC_API_KEY
        llm_model = get_model_for_role("questionnaire")

    pid = str(project_id)

    # ── Duplicate-run guard ──────────────────────────────────────────
    if pid in _active_generations:
        raise ValueError(
            "Contract generation is already in progress for this project. "
            "Wait for it to finish or cancel it first."
        )

    cancel_event = asyncio.Event()
    _active_generations[pid] = cancel_event

    # ── Resume: detect already-generated contracts from a partial run ─
    existing = await get_contracts_by_project(project_id)
    existing_map: dict[str, dict] = {c["contract_type"]: c for c in existing}

    # If we have a complete set, snapshot and regenerate from scratch
    all_exist = all(ct in existing_map for ct in CONTRACT_TYPES)
    if all_exist:
        batch = await snapshot_contracts(project_id)
        if batch:
            logger.info("Archived contracts as snapshot batch %d for project %s", batch, pid)
        existing_map = {}  # regenerate everything

    generated = []
    generation_timing: dict[str, float] = {}  # contract_type -> elapsed seconds
    gen_wall_start = time.monotonic()  # wall-clock start for total elapsed
    # Seed prior_contracts from existing contracts for chaining continuity
    prior_contracts: dict[str, str] = {
        ct: existing_map[ct]["content"]
        for ct in CONTRACT_TYPES
        if ct in existing_map
    }
    total = len(CONTRACT_TYPES)
    try:
        for idx, contract_type in enumerate(CONTRACT_TYPES):
            # Check cancellation between contracts
            if cancel_event.is_set():
                logger.info("Contract generation cancelled for project %s", pid)
                await manager.send_to_user(str(user_id), {
                    "type": "contract_progress",
                    "payload": {
                        "project_id": pid,
                        "contract_type": contract_type,
                        "status": "cancelled",
                        "index": idx,
                        "total": total,
                    },
                })
                raise ContractCancelled("Contract generation cancelled")

            # ── Resume: skip contracts that already exist from partial run ─
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
                logger.info("Skipping %s — already exists from partial run", contract_type)
                # Notify frontend so it shows as done immediately
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
                    },
                })
                continue

            # Notify client that generation of this contract has started
            await manager.send_to_user(str(user_id), {
                "type": "contract_progress",
                "payload": {
                    "project_id": pid,
                    "contract_type": contract_type,
                    "status": "generating",
                    "index": idx,
                    "total": total,
                },
            })

            # Race the LLM call against the cancel event so cancellation
            # takes effect immediately, even mid-generation.
            answers_data = _extract_answers_data(project, answers)

            # ── Template contracts: skip LLM entirely ────────────────────
            if contract_type in _TEMPLATE_CONTRACTS:
                t0 = time.monotonic()
                if contract_type == "stack":
                    content, forge_config = _template_stack(project, answers_data)
                    try:
                        await _store_forge_config(project_id, forge_config)
                    except Exception as _fc_exc:
                        logger.warning("Failed to store forge_config: %s", _fc_exc)
                elif contract_type == "boundaries":
                    content = _template_boundaries(project, answers_data)
                else:  # builder_directive
                    content = _template_builder_directive(project, answers_data, prior_contracts)

                elapsed_s = round(time.monotonic() - t0, 4)
                generation_timing[contract_type] = elapsed_s
                prior_contracts[contract_type] = content
                row = await upsert_contract(project_id, contract_type, content)
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
                        "elapsed_s": elapsed_s,
                        "templated": True,
                    },
                })
                continue  # skip LLM path
            # ─────────────────────────────────────────────────────────────

            # Callback for per-turn live token progress on the UI
            async def _turn_progress(in_tok: int, out_tok: int, _ct=contract_type, _idx=idx) -> None:
                await manager.send_to_user(str(user_id), {
                    "type": "contract_progress",
                    "payload": {
                        "project_id": pid,
                        "contract_type": _ct,
                        "status": "streaming",
                        "index": _idx,
                        "total": total,
                        "input_tokens": in_tok,
                        "output_tokens": out_tok,
                    },
                })

            t0 = time.monotonic()
            llm_task = asyncio.ensure_future(
                _generate_greenfield_contract_with_tools(
                    contract_type=contract_type,
                    project=project,
                    answers_data=answers_data,
                    api_key=llm_api_key,
                    model=llm_model,
                    provider=provider,
                    prior_contracts=prior_contracts,
                    build_mode=build_mode,
                    on_turn_progress=_turn_progress,
                )
            )
            cancel_task = asyncio.ensure_future(cancel_event.wait())

            done, pending = await asyncio.wait(
                [llm_task, cancel_task],
                return_when=asyncio.FIRST_COMPLETED,
            )

            if cancel_task in done:
                # Cancel fired while LLM was running — abort immediately
                llm_task.cancel()
                logger.info("Contract generation cancelled mid-LLM for project %s", pid)
                await manager.send_to_user(str(user_id), {
                    "type": "contract_progress",
                    "payload": {
                        "project_id": pid,
                        "contract_type": contract_type,
                        "status": "cancelled",
                        "index": idx,
                        "total": total,
                    },
                })
                raise ContractCancelled("Contract generation cancelled")

            # LLM finished first — clean up the cancel waiter
            cancel_task.cancel()
            elapsed_s = round(time.monotonic() - t0, 2)
            generation_timing[contract_type] = elapsed_s
            content, usage = llm_task.result()

            # Store for chaining into subsequent contracts
            prior_contracts[contract_type] = content

            row = await upsert_contract(project_id, contract_type, content)
            generated.append({
                "id": str(row["id"]),
                "project_id": str(row["project_id"]),
                "contract_type": row["contract_type"],
                "version": row["version"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            })

            # Notify client that this contract is done
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
                    "elapsed_s": elapsed_s,
                },
            })
    finally:
        _active_generations.pop(pid, None)

    # --- Fix 4: Post-generation cross-contract consistency check ----------
    await _run_consistency_check(project_id, user_id, prior_contracts)

    # --- Store generation timing metrics ----------------------------------
    total_elapsed_s = round(time.monotonic() - gen_wall_start, 2)
    await save_generation_metrics(project_id, {
        "timing": generation_timing,
        "total_elapsed_s": total_elapsed_s,
        "model": llm_model,
        "build_mode": build_mode,
        "generated_at": asyncio.get_event_loop().time(),
    })

    await update_project_status(project_id, "contracts_ready")
    return generated


_CONSISTENCY_PROMPT = """\
You are a senior technical reviewer.  You have been given a set of software \
design contracts for the same project.  Your job is to identify concrete \
inconsistencies between them — e.g.

• A database column referenced in the schema contract but missing from the \
  API contract.
• A feature described in the blueprint that the phases contract never schedules.
• Tech-stack choices in the stack contract that contradict the boundaries contract.
• Naming mismatches (different names for the same entity across contracts).

Respond ONLY with a JSON array of issue objects:
[{"severity":"warn"|"error","contracts":["type1","type2"],"description":"..."}]

If everything is consistent, return an empty array: []
Do NOT include commentary outside the JSON.
"""


async def _run_consistency_check(
    project_id: UUID,
    user_id: UUID,
    prior_contracts: dict[str, str],
) -> None:
    """Run a lightweight LLM pass to flag cross-contract inconsistencies.

    This is best-effort: failures are logged but never block the pipeline.
    Results are pushed to the client via WS so the user (and any future
    auto-fix step) can see them.
    """
    if len(prior_contracts) < 2:
        return  # Nothing to cross-check

    pid = str(project_id)
    try:
        # Build a condensed view — truncate very long contracts to stay in budget
        MAX_CHARS_PER_CONTRACT = 12_000
        condensed = []
        for ctype, content in prior_contracts.items():
            text = content[:MAX_CHARS_PER_CONTRACT]
            if len(content) > MAX_CHARS_PER_CONTRACT:
                text += "\n\n[... truncated ...]"
            condensed.append(f"=== {ctype} ===\n{text}")

        user_msg = "\n\n".join(condensed)

        resp = await llm_chat(
            api_key=settings.ANTHROPIC_API_KEY,
            model=get_model_for_role("planner"),
            system_prompt=_CONSISTENCY_PROMPT,
            messages=[{"role": "user", "content": user_msg}],
            max_tokens=2048,
        )

        raw = resp.get("text", "").strip()
        issues = json.loads(raw) if raw else []

        if issues:
            logger.warning(
                "Consistency check found %d issue(s) for project %s: %s",
                len(issues), pid, json.dumps(issues, indent=2),
            )
        else:
            logger.info("Consistency check passed for project %s", pid)

        # Push results to client regardless (empty = all good)
        await manager.send_to_user(str(user_id), {
            "type": "contract_consistency",
            "payload": {
                "project_id": pid,
                "issues": issues,
            },
        })

    except Exception:
        logger.exception(
            "Consistency check failed for project %s — non-blocking", pid,
        )


async def cancel_contract_generation(
    user_id: UUID,
    project_id: UUID,
) -> dict:
    """Cancel an in-progress contract generation.

    Sets the cancel event so the generation loop stops immediately,
    even if an LLM call is currently in flight.
    """
    pid = str(project_id)
    cancel_event = _active_generations.get(pid)
    if cancel_event is None:
        raise ValueError("No active contract generation for this project")

    # Verify ownership
    project = await get_project_by_id(project_id)
    if not project or str(project["user_id"]) != str(user_id):
        raise ValueError("Project not found")

    cancel_event.set()
    logger.info("Contract generation cancel requested for project %s", pid)
    return {"status": "cancelling"}


async def push_contracts_to_git(
    user_id: UUID,
    project_id: UUID,
) -> dict:
    """Write all contracts to the linked GitHub repo and push.

    Clones the repo into a temp directory, writes each contract file
    under a ``Forge/Contracts/`` folder, commits, and pushes.

    Returns:
        Dict with status, message, and commit sha.

    Raises:
        ValueError: If project not found, no repo linked, no access token,
                    no contracts, or git operations fail.
    """
    import shutil
    import tempfile
    from app.clients import git_client
    from app.repos.user_repo import get_user_by_id

    project = await get_project_by_id(project_id)
    if not project:
        raise ValueError("Project not found")
    if str(project["user_id"]) != str(user_id):
        raise ValueError("Project not found")

    repo_full_name = project.get("repo_full_name", "")
    if not repo_full_name:
        raise ValueError("No GitHub repository linked to this project")

    user = await get_user_by_id(user_id)
    access_token = (user or {}).get("access_token", "")
    if not access_token:
        raise ValueError("No GitHub access token — connect GitHub in Settings")

    contracts = await get_contracts_by_project(project_id)
    if not contracts:
        raise ValueError("No contracts to push — generate contracts first")

    # File extension mapping
    ext_map = {
        "physics": "physics.yaml",
        "boundaries": "boundaries.json",
    }

    # Clone repo into temp dir
    working_dir = tempfile.mkdtemp(prefix="forgeguard_contracts_push_")
    clone_url = f"https://github.com/{repo_full_name}.git"
    branch = "main"

    # Detect branch from latest build if available
    latest = await build_repo.get_latest_build_for_project(project_id)
    if latest and latest.get("branch"):
        branch = latest["branch"]

    try:
        shutil.rmtree(working_dir, ignore_errors=True)
        await git_client.clone_repo(
            clone_url, working_dir,
            branch=branch,
            access_token=access_token,
            shallow=False,
        )
    except Exception as exc:
        raise ValueError(f"Failed to clone {repo_full_name}: {exc}")

    try:
        # Write contracts under Forge/Contracts/
        contracts_dir = Path(working_dir) / "Forge" / "Contracts"
        contracts_dir.mkdir(parents=True, exist_ok=True)

        for c in contracts:
            ct = c["contract_type"]
            filename = ext_map.get(ct, f"{ct}.md")
            fp = contracts_dir / filename
            fp.write_text(c["content"], encoding="utf-8")

        # Commit and push (include_contracts=True bypasses the build-safety exclusion)
        await git_client.add_all(working_dir, include_contracts=True)
        sha = await git_client.commit(
            working_dir, "forge: push contracts", include_contracts=True,
        )
        if not sha:
            # Nothing changed — contracts already up to date
            return {
                "status": "unchanged",
                "message": f"Contracts already up to date on {repo_full_name}",
                "sha": None,
            }

        await git_client.push(
            working_dir, branch=branch, access_token=access_token,
        )

        return {
            "status": "pushed",
            "message": f"Pushed {len(contracts)} contracts to {repo_full_name} ({branch})",
            "sha": sha[:8],
        }
    except Exception as exc:
        raise ValueError(f"Failed to push contracts: {exc}")
    finally:
        shutil.rmtree(working_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Tool-use generation — greenfield variant
# ---------------------------------------------------------------------------


def _extract_answers_data(project: dict, answers: dict) -> dict:
    """Extract structured data from questionnaire answers for tool dispatch.

    Returns a dict whose keys map to greenfield tool names.
    """
    data: dict = {}
    for key in (
        "product_intent",
        "tech_stack",
        "database_schema",
        "api_endpoints",
        "ui_requirements",
        "architectural_boundaries",
        "deployment_target",
    ):
        val = answers.get(key)
        if val:
            data[key] = val
    data["project_name"] = project.get("name", "")
    data["project_description"] = project.get("description", "")
    return data


async def _generate_greenfield_contract_with_tools(
    contract_type: str,
    project: dict,
    answers_data: dict,
    api_key: str,
    model: str,
    provider: str,
    prior_contracts: dict[str, str],
    build_mode: str = "full",
    on_turn_progress: "Any | None" = None,
) -> tuple[str, dict]:
    """Generate a single greenfield contract using the multi-turn tool-use loop.

    Returns (content, usage) matching the signature of
    ``_generate_contract_content``.
    """
    (
        CONTEXT_TOOLS_GREENFIELD,
        build_tool_use_system_prompt,
        execute_greenfield_tool,
        generate_contract_with_tools,
    ) = _get_tool_use_helpers()

    is_mini = build_mode == "mini"
    system_prompt = build_tool_use_system_prompt(
        contract_type,
        project_name=project.get("name", ""),
        mode="greenfield",
        mini=is_mini,
    )

    def _executor(name: str, tool_input: dict) -> str:
        return execute_greenfield_tool(name, tool_input, answers_data, prior_contracts)

    return await generate_contract_with_tools(
        contract_type=contract_type,
        system_prompt=system_prompt,
        tools=CONTEXT_TOOLS_GREENFIELD,
        tool_executor=_executor,
        api_key=api_key,
        model=model,
        provider=provider,
        repo_name=project.get("name", ""),
        on_turn_progress=on_turn_progress,
    )


# Snowball chain: each contract receives ALL previously generated contracts
# as context.  Generation order (manifesto → blueprint → stack → schema →
# physics → boundaries → ui → phases → builder_directive) means later
# contracts see more context.  Caps keep total injection under budget.


async def _generate_contract_content(
    contract_type: str,
    project: dict,
    answers_text: str,
    api_key: str,
    model: str,
    provider: str,
    build_mode: str = "full",
    prior_contracts: dict[str, str] | None = None,
    on_token_progress: "Callable[[int, int], Awaitable[None]] | None" = None,
) -> tuple[str, dict]:
    """Generate a single contract using the LLM.

    Args:
        prior_contracts: Map of contract_type → content for previously
            generated contracts in this batch.  Used for cross-contract
            consistency (chaining).

    Returns (content, usage) where usage has input_tokens / output_tokens.
    """
    is_mini = build_mode == "mini"

    # Load a generic builder example as structural reference.
    # Templates are small (~1-2 KB each) so no truncation needed.
    example = _load_generic_template(contract_type)

    # Pick instructions: prefer mini-specific variant when available
    if is_mini:
        instructions = _CONTRACT_INSTRUCTIONS.get(
            f"{contract_type}_mini",
            _CONTRACT_INSTRUCTIONS.get(contract_type, f"Generate a {contract_type} contract document."),
        )
    else:
        instructions = _CONTRACT_INSTRUCTIONS.get(contract_type, f"Generate a {contract_type} contract document.")

    mini_note = ""
    if is_mini:
        mini_note = (
            "\n\nIMPORTANT: This is a MINI BUILD (proof-of-concept). "
            "The entire project will be built in exactly 2 phases "
            "(Phase 0: backend scaffold, Phase 1: frontend & ship). "
            "Scope ALL deliverables to fit within those 2 phases. "
            "Be concise within that scope but do not expand beyond it.\n"
        )

    system_parts = [
        f"You are a Forge contract generator. You produce concise, structured "
        f"project specification documents for the Forge autonomous build system.\n\n"
        f"You are generating the **{contract_type}** contract for the project "
        f'"{project["name"]}".{mini_note}\n\n'
        f"INSTRUCTIONS:\n{instructions}\n\n"
        f"RULES:\n"
        f"- Output ONLY the contract content. No preamble, no 'Here is...', no explanations.\n"
        f"- Be concise and structured. Bullet points over prose. No filler words or rationale.\n"
        f"- Every line must carry information. No explanatory paragraphs.\n"
        f"- Fill all required fields with project-specific facts.\n"
        f"- Match the FORMAT of the reference — not its verbosity. Do not exceed it.\n"
    ]

    if example:
        system_parts.append(
            f"\n--- STRUCTURAL REFERENCE (follow this format — do NOT write more than this) ---\n"
            f"{example}\n"
            f"--- END REFERENCE ---\n"
        )

    system_prompt = "\n".join(system_parts)

    # Build user message with questionnaire data + chained prior contracts
    user_parts = [
        f"Generate the {contract_type} contract for this project.\n\n"
        f"--- PROJECT INFORMATION (from questionnaire) ---\n"
        f"{answers_text}\n"
        f"--- END PROJECT INFORMATION ---"
    ]

    # Snowball chain: inject ALL previously generated contracts for
    # cross-contract consistency.  Per-contract cap keeps each snippet
    # reasonable; total budget caps the entire injection block.
    if prior_contracts:
        total_budget = 12_000 if is_mini else 24_000
        per_cap = 6_000 if is_mini else 12_000
        chain_parts: list[str] = []
        used = 0
        for dep_type in CONTRACT_TYPES:
            if dep_type == contract_type:
                break  # only include contracts generated *before* this one
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
                break  # stop adding once we hit the total budget
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
            # Remove first and last ``` lines
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            content = "\n".join(lines).strip()
        return content, usage
    except Exception as exc:
        logger.exception("LLM contract generation failed for %s: %s", contract_type, exc)
        # Fall back to a minimal template so the user at least gets something
        return (
            f"# {project['name']} — {contract_type}\n\n"
            f"**Generation failed:** {exc}\n\n"
            f"Please regenerate this contract or edit it manually."
        ), {"input_tokens": 0, "output_tokens": 0}
