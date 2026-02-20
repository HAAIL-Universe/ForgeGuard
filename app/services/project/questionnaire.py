"""Questionnaire — multi-turn chat state machine for project intake."""

import json
import logging
from uuid import UUID

from app.clients.llm_client import chat as llm_chat
from app.config import settings
from app.repos.project_repo import (
    get_project_by_id,
    update_project_status,
    update_questionnaire_history,
    update_questionnaire_state,
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

MINI_QUESTIONNAIRE_SECTIONS = [
    "product_intent",
    "ui_requirements",
]

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

_MINI_SYSTEM_PROMPT = """\
You are a project intake specialist for Forge, an autonomous build system.
This is a MINI BUILD — a quick proof-of-concept scaffold.

Your job is to collect just two things from the user:
1. product_intent — What the product does, who it’s for, the core problem it
   solves, and the 2-3 key features that define the experience.
2. ui_requirements — Design style, layout preferences, colour scheme,
   responsive needs, and overall look-and-feel.

That’s it. Only these two sections exist. There are NO other sections.
Do NOT ask about tech stack, backend, frontend framework, database, APIs,
architectural boundaries, or deployment. Those are auto-resolved by Forge.

CRITICAL RULES:
- Even if a project description is provided, do NOT treat product_intent as
  already captured. The description is just a brief summary. You MUST ask the
  user to elaborate on their vision.
- Ask 1-2 focused questions per section, then set section_complete=true.
- After the user answers, ALWAYS set section_complete to true and move on.
  Do NOT ask follow-ups within the same section. One answer per section is enough.
- Infer sensible defaults for anything unclear — DO NOT ask for clarification.
- ALWAYS include extracted_data with at least a summary of what you captured.
- Your response MUST be ONLY valid JSON — no markdown fences, no extra text:
  {
    "reply": "<your message>",
    "section": "<product_intent | ui_requirements | complete>",
    "section_complete": true|false,
    "extracted_data": { <key-value pairs of captured information> }
  }
- The "section" field MUST be one of: "product_intent", "ui_requirements", or
  "complete". Never use any other value.
- When section_complete is true, extracted_data MUST contain the captured data.
- When both sections are done, set section to "complete".
"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sections_for_mode(build_mode: str) -> list[str]:
    """Return the section list appropriate for the build mode."""
    if build_mode == "mini":
        return MINI_QUESTIONNAIRE_SECTIONS
    return QUESTIONNAIRE_SECTIONS


def _system_prompt_for_mode(build_mode: str) -> str:
    """Return the system prompt appropriate for the build mode."""
    if build_mode == "mini":
        return _MINI_SYSTEM_PROMPT
    return _SYSTEM_PROMPT


def _questionnaire_progress(qs: dict, build_mode: str = "full") -> dict:
    """Build a questionnaire progress dict from state."""
    sections = _sections_for_mode(build_mode)
    completed = qs.get("completed_sections", [])
    remaining = [s for s in sections if s not in completed]
    current = remaining[0] if remaining else None
    return {
        "current_section": current,
        "completed_sections": completed,
        "remaining_sections": remaining,
        "is_complete": len(remaining) == 0,
        "build_mode": build_mode,
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


# ---------------------------------------------------------------------------
# Public API
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
    qh = project.get("questionnaire_history") or {}
    completed = qs.get("completed_sections", [])
    answers = qs.get("answers", {})
    history = qh.get("conversation_history", [])

    # Resolve section list and system prompt for this project's build_mode
    build_mode = project.get("build_mode", "full")
    sections = _sections_for_mode(build_mode)
    base_prompt = _system_prompt_for_mode(build_mode)

    # Determine the current section
    current_section = None
    for section in sections:
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
    section_msg_count = sum(
        1 for m in history if m["role"] == "user" and m.get("section") == current_section
    )
    context_msg = (
        f"Project name: {project['name']}\n"
        f"Project description: {project.get('description', 'N/A')}\n"
        f"Current section: {current_section}\n"
        f"Completed sections: {', '.join(completed) if completed else 'none'}\n"
        f"Previously collected data: {json.dumps(answers, indent=2)}\n"
    )
    # Only push the LLM to complete the section after real user exchanges
    if section_msg_count >= 1:
        context_msg += (
            f"IMPORTANT: The user has been on '{current_section}' for "
            f"{section_msg_count} messages already. Set section_complete=true "
            f"NOW and move to the next section."
        )
    else:
        context_msg += (
            f"The user is just arriving at '{current_section}'. "
            f"Greet them warmly and ask your questions for this section. "
            f"Do NOT set section_complete=true yet — wait for their answer first."
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
        f"{base_prompt}\n\n"
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
    # Clamp: if LLM references a section not in this build mode, treat it
    # as the current section so we don't accidentally jump to full-build sections.
    if llm_section not in sections and llm_section != "complete":
        logger.info("LLM returned section '%s' outside build mode sections %s — clamping to '%s'",
                     llm_section, sections, current_section)
        llm_section = current_section
    llm_says_complete = bool(parsed.get("section_complete"))
    extracted = parsed.get("extracted_data")

    # Trigger 1: LLM explicitly says section_complete=true
    if llm_says_complete:
        section_name = llm_section if llm_section in sections else current_section
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
    if llm_section in sections and llm_section != current_section:
        llm_idx = sections.index(llm_section)
        cur_idx = sections.index(current_section)
        if llm_idx > cur_idx:
            for i in range(cur_idx, llm_idx):
                s = sections[i]
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
    for s in sections:
        if s not in completed:
            next_section = s
            break
    reply_section = next_section or current_section

    history.append({"role": "assistant", "content": parsed["reply"], "section": reply_section})

    # Accumulate token usage
    prev_usage = qh.get("token_usage", {"input_tokens": 0, "output_tokens": 0})
    total_usage = {
        "input_tokens": prev_usage.get("input_tokens", 0) + usage.get("input_tokens", 0),
        "output_tokens": prev_usage.get("output_tokens", 0) + usage.get("output_tokens", 0),
    }

    new_state = {
        "completed_sections": completed,
        "answers": answers,
    }
    await update_questionnaire_state(project_id, new_state)
    await update_questionnaire_history(project_id, history, total_usage)

    # Check if all sections are now complete
    remaining = [s for s in sections if s not in completed]
    is_complete = len(remaining) == 0

    if is_complete and project["status"] != "contracts_ready":
        await update_project_status(project_id, "contracts_ready")

    # Use the authoritative next-section rather than raw LLM output.
    # This prevents the LLM from advertising sections outside the build mode.
    authoritative_section = "complete" if is_complete else (next_section or current_section)

    return {
        "reply": parsed["reply"],
        "section": authoritative_section,
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
    qh = project.get("questionnaire_history") or {}
    build_mode = project.get("build_mode", "full")
    progress = _questionnaire_progress(qs, build_mode)
    progress["conversation_history"] = qh.get("conversation_history", [])
    progress["token_usage"] = qh.get("token_usage", {"input_tokens": 0, "output_tokens": 0})
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
    await update_questionnaire_history(project_id, [], {"input_tokens": 0, "output_tokens": 0})
    await update_project_status(project_id, "draft")
    return {"status": "reset"}
