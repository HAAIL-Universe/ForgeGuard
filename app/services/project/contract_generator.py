"""Contract generation — LLM-based contract creation, cancellation, push-to-git."""

import asyncio
import json
import logging
from pathlib import Path
from uuid import UUID

from app.clients.llm_client import chat as llm_chat, chat_streaming as llm_chat_streaming
from app.config import settings
from app.repos import build_repo
from app.repos.project_repo import (
    get_contracts_by_project,
    get_project_by_id,
    snapshot_contracts,
    update_project_status,
    upsert_contract,
)
from app.ws_manager import manager

from .questionnaire import QUESTIONNAIRE_SECTIONS, MINI_QUESTIONNAIRE_SECTIONS, _sections_for_mode

logger = logging.getLogger(__name__)


class ContractCancelled(Exception):
    """Raised when contract generation is cancelled by the user."""


# Active contract generation tasks — maps project-id → cancel Event
_active_generations: dict[str, asyncio.Event] = {}

CONTRACT_TYPES = [
    "blueprint",
    "manifesto",
    "stack",
    "schema",
    "physics",
    "boundaries",
    "phases",
    "ui",
    "builder_directive",
]

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates" / "contracts"
FORGE_CONTRACTS_DIR = Path(__file__).resolve().parent.parent.parent / "Forge" / "Contracts"


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


def _load_forge_example(contract_type: str) -> str | None:
    """Load the Forge example contract as a structural reference, if it exists."""
    ext_map = {
        "physics": "physics.yaml",
        "boundaries": "boundaries.json",
    }
    filename = ext_map.get(contract_type, f"{contract_type}.md")
    path = FORGE_CONTRACTS_DIR / filename
    if path.exists():
        try:
            return path.read_text(encoding="utf-8")
        except Exception:
            return None
    return None


# Per-contract generation instructions
_CONTRACT_INSTRUCTIONS: dict[str, str] = {
    "blueprint": """\
Generate a comprehensive project blueprint. Include:
1) Product intent — what it does, who it's for, why it exists (2-3 paragraphs)
2) Core interaction invariants — 5-7 MUST-hold rules (bullet list)
3) MVP scope — Must-ship features (numbered, with sub-details) AND explicitly-not-MVP list
4) Hard boundaries — anti-godfile rules per architectural layer (Routers, Services, Repos, Clients, etc.)
5) Deployment target — where it runs, scale expectations

Use specific, concrete language. No vagueness. Every feature must be described precisely enough
that a developer could implement it without asking questions.""",

    "manifesto": """\
Generate a project manifesto defining 5-7 non-negotiable principles.
Each principle should have:
- A descriptive title (e.g. "Contract-first, schema-first")
- 3-5 bullet points elaborating the rule
- Project-specific details (not generic platitudes)

Include a "Confirm-before-write" section listing what requires user confirmation
and what is exempt. Include any privacy or security principles relevant to this project.""",

    "stack": """\
Generate a technology stack document. Include sections for:
- Backend (language, framework, version, key libraries)
- Database (engine, version, connection method)
- Auth (method, provider, flow)
- Frontend (framework, bundler, key libraries)
- Testing (framework, coverage requirements)
- Deployment (platform, expected scale)
- Environment Variables table (name, description, required/optional)
- forge.json schema (JSON block showing project metadata structure)

For each technology choice, briefly explain WHY it was chosen.""",

    "schema": """\
Generate a complete database schema document. Include:
- Schema version header
- Conventions section (naming patterns, common columns like id/created_at/updated_at)
- Full CREATE TABLE SQL for EVERY table, including:
  - Column definitions with types and constraints
  - PRIMARY KEY, FOREIGN KEY, UNIQUE constraints
  - Indexes
  - ENUM descriptions where applicable
- Schema-to-Phase traceability matrix (which table is built in which phase)

Use PostgreSQL syntax. Be thorough — every column the app needs must be defined.""",

    "phases": """\
Generate a detailed phase breakdown document with 6-12 phases.
Each phase MUST include:
- Phase number and name (e.g. "Phase 0 — Genesis")
- Objective (1-2 sentences)
- Deliverables (detailed bullet list of what gets built)
- Schema coverage (which tables/columns are created or modified)
- Exit criteria (bullet list of concrete, testable conditions)

Phase 0 is always Genesis (project scaffold, config, tooling).
The final phase should be Ship & Deploy, and MUST include a deliverable to generate a comprehensive
README.md covering: project description, features, tech stack, setup/install instructions,
environment variables, usage examples, and API reference (if applicable).
Phases should be ordered by dependency — earlier phases provide foundations for later ones.""",

    "phases_mini": """\
This is a MINI BUILD — generate EXACTLY 2 phases. No more, no fewer.
IGNORE any structural reference that has more phases — output only these two.

Phase 0 — Backend Scaffold:
  Build the ENTIRE backend from scratch in a single phase: project scaffold,
  configuration, database schema & migrations, models, ALL API endpoints,
  authentication, middleware, and a boot/setup script.
  Include everything needed for the server to start and respond to every request
  defined in the physics contract.

Phase 1 — Frontend & Ship:
  Build the ENTIRE frontend from scratch and ship: project scaffold, ALL
  pages/routes, components, API integration with the backend, styling, and
  a comprehensive README.md covering: project description, features, tech stack,
  setup/install instructions, environment variables, and usage examples.
  Include everything needed for the user to see and interact with the full app.

Each phase MUST include:
- Phase number and name ("Phase 0 — Backend Scaffold", "Phase 1 — Frontend & Ship")
- Objective (1-2 sentences)
- Deliverables (detailed bullet list — be exhaustive, every feature goes in one of these 2 phases)
- Schema coverage (which tables/columns)
- Exit criteria (concrete, testable conditions)

CRITICAL: Do NOT add phases beyond 0 and 1. Do NOT split into more granular phases.
Every deliverable must fit into one of these two phases. The output must contain
exactly Phase 0 and Phase 1, nothing else.""",

    # ── Mini-specific instructions for other contract types ────────────
    "blueprint_mini": """\
Generate a concise project blueprint for a mini/proof-of-concept build.
Keep scope tight — this will be built in exactly 2 phases (backend + frontend).
Include:
1) Product intent — what it does and for whom (1 paragraph)
2) Core invariants — 3-5 MUST-hold rules
3) MVP scope — features that fit in 2 build phases AND explicit not-in-scope list
4) Layer boundaries — basic separation (routes → services → repos)
5) Deployment — Docker-ready local dev

Be specific but concise. No sprawling feature lists — just what's buildable in 2 phases.""",

    "manifesto_mini": """\
Generate a brief manifesto with 3-5 non-negotiable principles.
This is a mini build (2 phases) — keep principles focused on what matters
for a working proof-of-concept. Each principle: title + 2-3 bullets.
Skip ceremony — no confirm-before-write section needed for a mini build.""",

    "stack_mini": """\
Generate a technology stack document. Include:
- Backend (language, framework, version, key libraries)
- Database (engine, version)
- Frontend (framework, bundler, key libraries)
- Deployment (Docker, single-compose)
- Environment Variables table (name, description, required/optional)

Keep it focused — this is a 2-phase proof-of-concept, not an enterprise deployment.""",

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
  "layers": [ { "name": "...", "glob": "...", "forbidden": [...] } ],
  "known_violations": []
}
Define 3-4 layers with basic separation of concerns. Keep it simple for a mini build.
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
Generate a UI/UX blueprint document. Include:
1) App Shell & Layout — device priority, shell structure, navigation model
2) Screens/Views — detailed spec for each page (what it shows, key interactions, empty states)
3) Component Inventory — table of reusable components
4) Visual Style — color palette, typography, visual density, tone
5) Interaction Patterns — data loading, empty states, error states, confirmation dialogs, responsive behavior
6) User Flows — 3-4 key user journeys described step by step
7) What This Is NOT — explicit list of out-of-scope UI features

Be specific about layout, data displayed, and interaction triggers.""",

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
Generate a builder directive — the operational instructions for an AI builder.
Include:
- AEM status (enabled/disabled) and auto-authorize setting
- Step-by-step instructions (read contracts → execute phases → run audit → commit)
- Autonomy rules (when to auto-commit, when to stop and ask)
- Phase list with phase numbers and names
- Project summary (one paragraph)
- boot_script flag

Keep it concise but complete — this is the builder's startup instructions.""",
}


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
        llm_model = settings.LLM_QUESTIONNAIRE_MODEL

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
            async def _on_token_progress(in_tok: int, out_tok: int) -> None:
                """Send live token streaming updates via WS."""
                await manager.send_to_user(str(user_id), {
                    "type": "contract_progress",
                    "payload": {
                        "project_id": pid,
                        "contract_type": contract_type,
                        "status": "streaming",
                        "index": idx,
                        "total": total,
                        "input_tokens": in_tok,
                        "output_tokens": out_tok,
                    },
                })

            llm_task = asyncio.ensure_future(
                _generate_contract_content(
                    contract_type, project, answers_text, llm_api_key, llm_model, provider,
                    build_mode=build_mode,
                    prior_contracts=prior_contracts,
                    on_token_progress=_on_token_progress,
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
                },
            })
    finally:
        _active_generations.pop(pid, None)

    await update_project_status(project_id, "contracts_ready")
    return generated


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


# Which previously-generated contracts to feed as context for each type.
# Key = contract being generated, value = list of prior contract types to include.
_CHAIN_CONTEXT: dict[str, list[str]] = {
    "blueprint":          [],
    "manifesto":          ["blueprint"],
    "stack":              ["blueprint"],
    "schema":             ["blueprint", "stack"],
    "physics":            ["blueprint", "schema"],
    "boundaries":         ["blueprint", "stack"],
    "phases":             ["blueprint", "stack", "schema", "physics"],
    "ui":                 ["blueprint", "physics", "schema"],
    "builder_directive":  ["blueprint", "phases", "stack"],
}


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

    # Load the Forge example as a structural reference (if available)
    # For mini builds: skip the example for phases (230KB, 13+ phases
    # overwhelms the 2-phase instruction) and cap other examples to
    # keep token budget reasonable.
    example = None
    if is_mini and contract_type == "phases":
        example = None  # never show the full phases example for mini
    else:
        example = _load_forge_example(contract_type)
        if is_mini and example and len(example) > 8000:
            # Truncate large examples for mini builds to save tokens
            example = example[:8000] + "\n\n[... truncated for mini build — match the FORMAT above, not the length ...]\n"

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
            "Be thorough within that scope but do not expand beyond it.\n"
        )

    system_parts = [
        f"You are a Forge contract generator. You produce detailed, production-quality "
        f"project specification documents for the Forge autonomous build system.\n\n"
        f"You are generating the **{contract_type}** contract for the project "
        f'"{project["name"]}".{mini_note}\n\n'
        f"INSTRUCTIONS:\n{instructions}\n\n"
        f"RULES:\n"
        f"- Output ONLY the contract content. No preamble, no 'Here is...', no explanations.\n"
        f"- Be thorough and detailed. Each contract should be comprehensive enough that a "
        f"developer can build from it without asking questions.\n"
        f"- Use the project information provided to fill in ALL sections with real, "
        f"project-specific content.\n"
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

    # Build user message with questionnaire data + chained prior contracts
    user_parts = [
        f"Generate the {contract_type} contract for this project.\n\n"
        f"--- PROJECT INFORMATION (from questionnaire) ---\n"
        f"{answers_text}\n"
        f"--- END PROJECT INFORMATION ---"
    ]

    # Inject prior contracts for cross-contract consistency (chaining)
    if prior_contracts:
        deps = _CHAIN_CONTEXT.get(contract_type, [])
        chain_parts = []
        for dep_type in deps:
            dep_content = prior_contracts.get(dep_type)
            if dep_content:
                # Cap each prior contract to avoid blowing up the context
                cap = 6000 if is_mini else 12000
                snippet = dep_content[:cap]
                if len(dep_content) > cap:
                    snippet += "\n[... truncated ...]"
                chain_parts.append(
                    f"--- PREVIOUSLY GENERATED: {dep_type} ---\n"
                    f"{snippet}\n"
                    f"--- END {dep_type} ---"
                )
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
