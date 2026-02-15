"""Project service -- orchestrates project CRUD, questionnaire chat, and contract generation."""

import json
import logging
from pathlib import Path
from uuid import UUID

from app.clients.llm_client import chat as llm_chat
from app.config import settings
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
    "phase_breakdown",
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
    "builder_contract",
    "builder_directive",
]

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates" / "contracts"

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
8. phase_breakdown — Implementation phases with deliverables and exit criteria

CRITICAL RULES:
- Ask AT MOST 2-3 focused questions per section, then COMPLETE IT.
- Do NOT over-ask. If the user gives a reasonable answer, mark the section
  complete and move on. You can infer sensible defaults for anything unclear.
- One section at a time. After completing one, immediately ask about the next.
- If the user's single answer covers multiple sections, mark ALL covered
  sections as complete in one response.
- Your response MUST be ONLY valid JSON — no markdown fences, no extra text:
  {
    "reply": "<your conversational message to the user>",
    "section": "<current section name>",
    "section_complete": true|false,
    "extracted_data": { <key-value pairs of captured information> }
  }
- When section_complete is true, extracted_data MUST contain the collected data.
- When section_complete is false, extracted_data should be {} or partial data.
- ALWAYS set section_complete to true after AT MOST 2 exchanges per section.
  Don't let any section drag on — keep momentum. The user sees a progress bar;
  it must visibly advance.
- Be conversational but FAST. Prefer completing a section and moving on over
  asking the perfect follow-up question.
- When all sections are done, set section to "complete" and section_complete
  to true.
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
        f"Previously collected data: {json.dumps(answers, indent=2)}"
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
        raw_reply = await llm_chat(
            api_key=llm_api_key,
            model=llm_model,
            system_prompt=dynamic_system,
            messages=llm_messages,
            provider=provider,
        )
    except Exception as exc:
        logger.exception("LLM chat failed for project %s", project_id)
        raise ValueError(f"LLM service error: {exc}") from exc

    logger.info("LLM raw response (first 500 chars): %s", raw_reply[:500])

    # Parse the structured JSON response from the LLM
    parsed = _parse_llm_response(raw_reply)
    logger.info("Parsed LLM response — section_complete=%s, section=%s",
                parsed.get("section_complete"), parsed.get("section"))

    # Update state based on LLM response
    history.append({"role": "user", "content": message})
    history.append({"role": "assistant", "content": parsed["reply"]})

    if parsed.get("section_complete") and parsed.get("extracted_data"):
        section_name = parsed.get("section", current_section)
        answers[section_name] = parsed["extracted_data"]
        if section_name not in completed:
            completed.append(section_name)

    new_state = {
        "completed_sections": completed,
        "answers": answers,
        "conversation_history": history,
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
    return progress


# ---------------------------------------------------------------------------
# Contract generation
# ---------------------------------------------------------------------------


async def generate_contracts(
    user_id: UUID,
    project_id: UUID,
) -> list[dict]:
    """Generate all contract files from questionnaire answers.

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
    template_vars = _build_template_vars(project, answers)

    generated = []
    for contract_type in CONTRACT_TYPES:
        content = _render_template(contract_type, template_vars)
        row = await upsert_contract(project_id, contract_type, content)
        generated.append({
            "id": str(row["id"]),
            "project_id": str(row["project_id"]),
            "contract_type": row["contract_type"],
            "version": row["version"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
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


def _build_template_vars(project: dict, answers: dict) -> dict:
    """Flatten questionnaire answers into template variables."""
    variables = {
        "project_name": project["name"],
        "project_description": project.get("description", ""),
    }

    # Flatten all answer sections into the variables dict
    for section_name, section_data in answers.items():
        if isinstance(section_data, dict):
            for key, value in section_data.items():
                if isinstance(value, list):
                    variables[key] = "\n".join(f"- {v}" for v in value)
                else:
                    variables[key] = str(value)
        elif isinstance(section_data, str):
            variables[section_name] = section_data

    return variables


def _render_template(contract_type: str, variables: dict) -> str:
    """Render a contract template with the given variables.

    Uses safe substitution -- missing variables become empty strings.
    """
    template_file = TEMPLATES_DIR / f"{contract_type}.md"
    if contract_type == "physics":
        template_file = TEMPLATES_DIR / "physics.yaml"
    elif contract_type == "boundaries":
        template_file = TEMPLATES_DIR / "boundaries.json"

    if not template_file.exists():
        return f"# {variables.get('project_name', 'Project')} — {contract_type}\n\nTemplate not found."

    raw = template_file.read_text(encoding="utf-8")

    # Safe substitution: replace {key} with value, leave unknown keys empty
    import re

    def _replacer(match: re.Match) -> str:
        key = match.group(1)
        return variables.get(key, "")

    return re.sub(r"\{(\w+)\}", _replacer, raw)
