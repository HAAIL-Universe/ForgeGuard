"""Project service -- orchestrates project CRUD, questionnaire chat, and contract generation."""

import json
import logging
from pathlib import Path
from uuid import UUID

from app.clients.llm_client import chat as llm_chat
from app.config import settings
from app.ws_manager import manager
from app.repos.project_repo import (
    create_project as repo_create_project,
    delete_project as repo_delete_project,
    get_contract_by_type,
    get_contracts_by_project,
    get_project_by_id,
    get_projects_by_user,
    update_contract_content as repo_update_contract_content,
    update_project_status,
    update_questionnaire_state,
    upsert_contract,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Questionnaire definitions
# ---------------------------------------------------------------------------

QUESTIONNAIRE_SECTIONS = [
    "product_intent",
    "tech_stack",
    "database_schema",
    "api_endpoints",
    "ui_requirements",
    "architectural_boundaries",
    "deployment_target",
]

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

_SYSTEM_PROMPT = """\
You are a project intake specialist for Forge, an autonomous build system.
Your job is to guide the user through a structured questionnaire to collect
all the information needed to generate Forge contract files for their project.

The questionnaire has these sections (in order):
1. product_intent — What the product does, who it's for, key features
2. tech_stack — Backend/frontend languages, frameworks, database, deployment
3. database_schema — Tables, columns, relationships, constraints
4. api_endpoints — REST/GraphQL endpoints, auth, request/response shapes
5. ui_requirements — Pages, components, design system, responsive needs
6. architectural_boundaries — Layer rules, forbidden imports, separation concerns
7. deployment_target — Where it runs, CI/CD, infrastructure

NOTE: Do NOT ask the user about implementation phases. Phases are auto-derived
architecturally during contract generation based on all gathered information.

CRITICAL RULES:
- Ask AT MOST 1-2 focused questions per section, then set section_complete=true.
- After the user answers, ALWAYS set section_complete to true and move on.
  Do NOT ask follow-ups within the same section. One answer per section is enough.
- Infer sensible defaults for anything unclear — DO NOT ask for clarification.
- ALWAYS include extracted_data with at least a summary of what you captured.
- Your response MUST be ONLY valid JSON — no markdown fences, no extra text:
  {
    "reply": "<your message — summarise what you captured, then ask about the NEXT section>",
    "section": "<the section you just completed or are asking about>",
    "section_complete": true|false,
    "extracted_data": { <key-value pairs of captured information> }
  }
- When section_complete is true, extracted_data MUST contain the captured data.
  extracted_data must NEVER be empty or null when section_complete is true.
- The user sees a progress bar. Every response MUST advance it. If the user
  answered your question, section_complete MUST be true.
- When all 7 sections are done, set section to "complete".
"""


# ---------------------------------------------------------------------------
# Project CRUD
# ---------------------------------------------------------------------------


async def create_new_project(
    user_id: UUID,
    name: str,
    description: str | None = None,
    repo_id: UUID | None = None,
    local_path: str | None = None,
) -> dict:
    """Create a new project, optionally linked to a repo or local path."""
    project = await repo_create_project(
        user_id, name, description, repo_id=repo_id, local_path=local_path
    )
    return project


async def list_user_projects(user_id: UUID) -> list[dict]:
    """List all projects for a user."""
    return await get_projects_by_user(user_id)


async def get_project_detail(user_id: UUID, project_id: UUID) -> dict:
    """Get full project detail. Raises ValueError if not found or not owned."""
    project = await get_project_by_id(project_id)
    if not project:
        raise ValueError("Project not found")
    if str(project["user_id"]) != str(user_id):
        raise ValueError("Project not found")

    contracts = await get_contracts_by_project(project_id)
    qs = project.get("questionnaire_state", {})

    project["questionnaire_progress"] = _questionnaire_progress(qs)
    project["contracts"] = [
        {
            "id": str(c["id"]),
            "project_id": str(c["project_id"]),
            "contract_type": c["contract_type"],
            "version": c["version"],
            "created_at": c["created_at"],
            "updated_at": c["updated_at"],
        }
        for c in contracts
    ]
    return project


async def delete_user_project(user_id: UUID, project_id: UUID) -> bool:
    """Delete a project if owned by user. Returns True if deleted."""
    project = await get_project_by_id(project_id)
    if not project or str(project["user_id"]) != str(user_id):
        raise ValueError("Project not found")
    return await repo_delete_project(project_id)


# ---------------------------------------------------------------------------
# Questionnaire
# ---------------------------------------------------------------------------


async def process_questionnaire_message(
    user_id: UUID,
    project_id: UUID,
    message: str,
) -> dict:
    """Process a user message in the questionnaire chat.

    Returns the LLM reply with section progress information.
    """
    project = await get_project_by_id(project_id)
    if not project:
        raise ValueError("Project not found")
    if str(project["user_id"]) != str(user_id):
        raise ValueError("Project not found")

    qs = project.get("questionnaire_state") or {}
    completed = qs.get("completed_sections", [])
    answers = qs.get("answers", {})
    history = qs.get("conversation_history", [])

    # Determine the current section
    current_section = None
    for section in QUESTIONNAIRE_SECTIONS:
        if section not in completed:
            current_section = section
            break

    if current_section is None:
        return {
            "reply": "The questionnaire is already complete. You can now generate contracts.",
            "section": "complete",
            "completed_sections": completed,
            "remaining_sections": [],
            "is_complete": True,
        }

    # If this is the first message, update status to questionnaire
    if project["status"] == "draft":
        await update_project_status(project_id, "questionnaire")

    # Build conversation for LLM
    context_msg = (
        f"Project name: {project['name']}\n"
        f"Project description: {project.get('description', 'N/A')}\n"
        f"Current section: {current_section}\n"
        f"Completed sections: {', '.join(completed) if completed else 'none'}\n"
        f"Previously collected data: {json.dumps(answers, indent=2)}\n"
        f"IMPORTANT: The user has been on '{current_section}' for "
        f"{sum(1 for m in history if m['role'] == 'user' and m.get('section') == current_section)} "
        f"messages already. Set section_complete=true NOW and move to the next section."
    )

    # Anthropic requires strictly alternating user/assistant roles.
    # Merge context into the first user message and ensure no consecutive
    # same-role messages exist.
    llm_messages: list[dict] = []
    for m in history:
        if llm_messages and llm_messages[-1]["role"] == m["role"]:
            # Merge consecutive same-role messages
            llm_messages[-1]["content"] += "\n\n" + m["content"]
        else:
            llm_messages.append({"role": m["role"], "content": m["content"]})

    # Append the new user message
    new_user_content = message

    if llm_messages and llm_messages[-1]["role"] == "user":
        llm_messages[-1]["content"] += "\n\n" + new_user_content
    else:
        llm_messages.append({"role": "user", "content": new_user_content})

    # Pick LLM provider: explicit env var > auto-detect by available key
    provider = settings.LLM_PROVIDER.strip().lower() if settings.LLM_PROVIDER else ""
    if not provider:
        provider = "anthropic" if settings.ANTHROPIC_API_KEY else "openai"

    if provider == "openai":
        llm_api_key = settings.OPENAI_API_KEY
        llm_model = settings.OPENAI_MODEL
    else:
        llm_api_key = settings.ANTHROPIC_API_KEY
        llm_model = settings.LLM_QUESTIONNAIRE_MODEL

    # Build dynamic system prompt with project context
    dynamic_system = (
        f"{_SYSTEM_PROMPT}\n\n"
        f"--- PROJECT CONTEXT ---\n"
        f"{context_msg}"
    )

    try:
        llm_result = await llm_chat(
            api_key=llm_api_key,
            model=llm_model,
            system_prompt=dynamic_system,
            messages=llm_messages,
            provider=provider,
        )
    except Exception as exc:
        logger.exception("LLM chat failed for project %s", project_id)
        raise ValueError(f"LLM service error: {exc}") from exc

    raw_reply = llm_result["text"]
    usage = llm_result.get("usage", {})
    logger.info("LLM raw response (first 500 chars): %s", raw_reply[:500])

    # Parse the structured JSON response from the LLM
    parsed = _parse_llm_response(raw_reply)
    logger.info("Parsed LLM response — section_complete=%s, section=%s",
                parsed.get("section_complete"), parsed.get("section"))

    # Update state based on LLM response
    history.append({"role": "user", "content": message, "section": current_section})

    # --- Section-completion logic (3 independent triggers) ---
    llm_section = parsed.get("section") or current_section
    llm_says_complete = bool(parsed.get("section_complete"))
    extracted = parsed.get("extracted_data")

    # Trigger 1: LLM explicitly says section_complete=true
    if llm_says_complete:
        section_name = llm_section if llm_section in QUESTIONNAIRE_SECTIONS else current_section
        if extracted and isinstance(extracted, dict):
            answers[section_name] = extracted
        elif section_name not in answers:
            answers[section_name] = {"auto": "completed by LLM"}
        if section_name not in completed:
            completed.append(section_name)
        logger.info("Section completed (LLM explicit): %s", section_name)

    # Trigger 2: LLM jumped ahead — mentions a later section, implying
    #            the current one is done.  Complete all sections up to
    #            (but not including) the one the LLM is now asking about.
    if llm_section in QUESTIONNAIRE_SECTIONS and llm_section != current_section:
        llm_idx = QUESTIONNAIRE_SECTIONS.index(llm_section)
        cur_idx = QUESTIONNAIRE_SECTIONS.index(current_section)
        if llm_idx > cur_idx:
            for i in range(cur_idx, llm_idx):
                s = QUESTIONNAIRE_SECTIONS[i]
                if s not in completed:
                    completed.append(s)
                    if s not in answers:
                        answers[s] = {"auto": "inferred from conversation"}
                    logger.info("Section auto-completed (LLM jumped ahead): %s", s)

    # Trigger 3: Exchange-count safety net — if we've had >= 3 user
    #            messages in the same section, force-complete it so
    #            the user always sees progress.
    section_user_msgs = sum(
        1 for m in history if m["role"] == "user" and m.get("section") == current_section
    )
    if section_user_msgs >= 3 and current_section not in completed:
        completed.append(current_section)
        if current_section not in answers:
            answers[current_section] = {"auto": "force-completed after 3 exchanges"}
        logger.info("Section force-completed (3 exchanges): %s", current_section)

    # Determine the next section for tagging the assistant reply
    next_section = None
    for s in QUESTIONNAIRE_SECTIONS:
        if s not in completed:
            next_section = s
            break
    reply_section = next_section or current_section

    history.append({"role": "assistant", "content": parsed["reply"], "section": reply_section})

    # Accumulate token usage
    prev_usage = qs.get("token_usage", {"input_tokens": 0, "output_tokens": 0})
    total_usage = {
        "input_tokens": prev_usage.get("input_tokens", 0) + usage.get("input_tokens", 0),
        "output_tokens": prev_usage.get("output_tokens", 0) + usage.get("output_tokens", 0),
    }

    new_state = {
        "completed_sections": completed,
        "answers": answers,
        "conversation_history": history,
        "token_usage": total_usage,
    }
    await update_questionnaire_state(project_id, new_state)

    # Check if all sections are now complete
    remaining = [s for s in QUESTIONNAIRE_SECTIONS if s not in completed]
    is_complete = len(remaining) == 0

    if is_complete and project["status"] != "contracts_ready":
        await update_project_status(project_id, "contracts_ready")

    return {
        "reply": parsed["reply"],
        "section": parsed.get("section", current_section),
        "completed_sections": completed,
        "remaining_sections": remaining,
        "is_complete": is_complete,
        "token_usage": total_usage,
    }


async def get_questionnaire_state(
    user_id: UUID,
    project_id: UUID,
) -> dict:
    """Return current questionnaire progress and conversation history."""
    project = await get_project_by_id(project_id)
    if not project:
        raise ValueError("Project not found")
    if str(project["user_id"]) != str(user_id):
        raise ValueError("Project not found")

    qs = project.get("questionnaire_state") or {}
    progress = _questionnaire_progress(qs)
    progress["conversation_history"] = qs.get("conversation_history", [])
    progress["token_usage"] = qs.get("token_usage", {"input_tokens": 0, "output_tokens": 0})
    return progress


async def reset_questionnaire(
    user_id: UUID,
    project_id: UUID,
) -> dict:
    """Clear all questionnaire state and reset the project to draft."""
    project = await get_project_by_id(project_id)
    if not project:
        raise ValueError("Project not found")
    if str(project["user_id"]) != str(user_id):
        raise ValueError("Project not found")

    await update_questionnaire_state(project_id, {})
    await update_project_status(project_id, "draft")
    return {"status": "reset"}


# ---------------------------------------------------------------------------
# Contract generation
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
    remaining = [s for s in QUESTIONNAIRE_SECTIONS if s not in completed]

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

    generated = []
    total = len(CONTRACT_TYPES)
    for idx, contract_type in enumerate(CONTRACT_TYPES):
        # Notify client that generation of this contract has started
        await manager.send_to_user(str(user_id), {
            "type": "contract_progress",
            "payload": {
                "project_id": str(project_id),
                "contract_type": contract_type,
                "status": "generating",
                "index": idx,
                "total": total,
            },
        })

        content, usage = await _generate_contract_content(
            contract_type, project, answers_text, llm_api_key, llm_model, provider
        )
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
                "project_id": str(project_id),
                "contract_type": contract_type,
                "status": "done",
                "index": idx,
                "total": total,
                "input_tokens": usage.get("input_tokens", 0),
                "output_tokens": usage.get("output_tokens", 0),
            },
        })

    await update_project_status(project_id, "contracts_ready")
    return generated


async def list_contracts(
    user_id: UUID,
    project_id: UUID,
) -> list[dict]:
    """List all contracts for a project."""
    project = await get_project_by_id(project_id)
    if not project:
        raise ValueError("Project not found")
    if str(project["user_id"]) != str(user_id):
        raise ValueError("Project not found")

    return await get_contracts_by_project(project_id)


async def get_contract(
    user_id: UUID,
    project_id: UUID,
    contract_type: str,
) -> dict:
    """Get a single contract. Raises ValueError if not found."""
    if contract_type not in CONTRACT_TYPES:
        raise ValueError(f"Invalid contract type: {contract_type}")

    project = await get_project_by_id(project_id)
    if not project:
        raise ValueError("Project not found")
    if str(project["user_id"]) != str(user_id):
        raise ValueError("Project not found")

    contract = await get_contract_by_type(project_id, contract_type)
    if not contract:
        raise ValueError(f"Contract '{contract_type}' not found")
    return contract


async def update_contract(
    user_id: UUID,
    project_id: UUID,
    contract_type: str,
    content: str,
) -> dict:
    """Update a contract's content. Raises ValueError if not found."""
    if contract_type not in CONTRACT_TYPES:
        raise ValueError(f"Invalid contract type: {contract_type}")

    project = await get_project_by_id(project_id)
    if not project:
        raise ValueError("Project not found")
    if str(project["user_id"]) != str(user_id):
        raise ValueError("Project not found")

    result = await repo_update_contract_content(project_id, contract_type, content)
    if not result:
        raise ValueError(f"Contract '{contract_type}' not found")
    return result


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _questionnaire_progress(qs: dict) -> dict:
    """Build a questionnaire progress dict from state."""
    completed = qs.get("completed_sections", [])
    remaining = [s for s in QUESTIONNAIRE_SECTIONS if s not in completed]
    current = remaining[0] if remaining else None
    return {
        "current_section": current,
        "completed_sections": completed,
        "remaining_sections": remaining,
        "is_complete": len(remaining) == 0,
    }


def _parse_llm_response(raw: str) -> dict:
    """Parse structured JSON from LLM response.

    Tries multiple strategies to extract valid JSON, then falls back to
    treating the whole reply as plain text.
    """
    import re as _re

    text = raw.strip()

    # Strategy 1: strip markdown code fences (```json ... ```)
    if text.startswith("```"):
        lines = text.split("\n")
        lines = [l for l in lines if not l.strip().startswith("```")]
        text = "\n".join(lines).strip()

    # Strategy 2: direct parse
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict) and "reply" in parsed:
            logger.debug("LLM JSON parsed directly")
            return parsed
    except (json.JSONDecodeError, TypeError):
        pass

    # Strategy 3: find first { ... } block in the text
    brace_match = _re.search(r'\{[\s\S]*\}', text)
    if brace_match:
        try:
            parsed = json.loads(brace_match.group())
            if isinstance(parsed, dict) and "reply" in parsed:
                logger.debug("LLM JSON extracted via brace search")
                return parsed
        except (json.JSONDecodeError, TypeError):
            pass

    # Fallback: treat as plain text reply
    logger.warning("LLM returned non-JSON response, using fallback: %s", text[:200])
    return {
        "reply": raw,
        "section": None,
        "section_complete": False,
        "extracted_data": None,
    }


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
The final phase should be Ship & Deploy.
Phases should be ordered by dependency — earlier phases provide foundations for later ones.""",

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


async def _generate_contract_content(
    contract_type: str,
    project: dict,
    answers_text: str,
    api_key: str,
    model: str,
    provider: str,
) -> tuple[str, dict]:
    """Generate a single contract using the LLM.

    Returns (content, usage) where usage has input_tokens / output_tokens.
    """
    # Load the Forge example as a structural reference (if available)
    example = _load_forge_example(contract_type)

    instructions = _CONTRACT_INSTRUCTIONS.get(contract_type, f"Generate a {contract_type} contract document.")

    system_parts = [
        f"You are a Forge contract generator. You produce detailed, production-quality "
        f"project specification documents for the Forge autonomous build system.\n\n"
        f"You are generating the **{contract_type}** contract for the project "
        f'"{project["name"]}".\n\n'
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
        # Truncate very long examples to avoid hitting context limits
        if len(example) > 6000:
            example = example[:6000] + "\n\n... (truncated for brevity) ..."
        system_parts.append(
            f"\n--- STRUCTURAL REFERENCE (match this level of detail and format) ---\n"
            f"{example}\n"
            f"--- END REFERENCE ---\n"
        )

    system_prompt = "\n".join(system_parts)

    user_msg = (
        f"Generate the {contract_type} contract for this project.\n\n"
        f"--- PROJECT INFORMATION (from questionnaire) ---\n"
        f"{answers_text}\n"
        f"--- END PROJECT INFORMATION ---"
    )

    try:
        result = await llm_chat(
            api_key=api_key,
            model=model,
            system_prompt=system_prompt,
            messages=[{"role": "user", "content": user_msg}],
            provider=provider,
            max_tokens=16384,
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
