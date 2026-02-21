"""Shared helpers for contract generation — used by both greenfield and Scout flows.

De-duplicates utilities that were previously copy-pasted between
``contract_generator.py`` and ``scout_contract_generator.py``.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from app.clients.llm_client import chat as llm_chat
from app.services.project.contract_generator import (
    _CONTRACT_INSTRUCTIONS,
    _load_generic_template,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Mini-build defaults — applied at contract generation time when the
# questionnaire skipped sections.  These are NOT baked into the
# questionnaire system prompt so the user can override them by simply
# mentioning preferences during the intent / UI discussion.
# ---------------------------------------------------------------------------

MINI_DEFAULTS: dict[str, dict] = {
    "tech_stack": {
        "backend": "Python 3.12+ / FastAPI",
        "frontend": "React + TypeScript",
        "database": "PostgreSQL",
        "package_manager": "pip / npm",
    },
    "database_schema": {
        "note": "Minimal tables derived from product intent.",
    },
    "api_endpoints": {
        "note": "Standard REST endpoints derived from product intent.",
    },
    "architectural_boundaries": {
        "style": "Layered separation (routes / services / repos)",
        "forbidden_imports": "Routes must not import repos directly",
    },
    "deployment_target": {
        "runtime": "Docker-ready",
        "local_dev": "docker-compose single-stack",
    },
}


def _adaptive_mini_default(key: str, answers_data: dict) -> dict:
    """Return a mini default enriched with product-intent context.

    For mini builds, questionnaire sections beyond product_intent and
    ui_requirements are auto-filled.  Rather than returning static
    defaults, we inject the user's product description so the contract
    LLM can *infer* a reasonable value from what the user actually said.
    """
    base = dict(MINI_DEFAULTS.get(key, {}))
    intent = answers_data.get("product_intent")
    if intent and isinstance(intent, dict) and not _is_placeholder(intent):
        base["_inferred_from_product_intent"] = (
            "The user only answered product-intent and UI questions. "
            "Infer sensible values for this section based on what they "
            "described. The defaults above are starting suggestions only — "
            "override them if the product clearly needs something different "
            f"(e.g. a mobile app might need React Native, not React).\n\n"
            f"Product intent: {json.dumps(intent, default=str)}"
        )
    return base

_AUTO_PLACEHOLDER_PREFIXES = ("completed by LLM", "inferred from conversation", "force-completed")


def _is_placeholder(val: object) -> bool:
    """Return True if *val* is a questionnaire auto-fill placeholder."""
    if not isinstance(val, dict):
        return False
    auto = val.get("auto")
    if not isinstance(auto, str):
        return False
    return any(auto.startswith(p) for p in _AUTO_PLACEHOLDER_PREFIXES)


# ---------------------------------------------------------------------------
# Text sanitisation
# ---------------------------------------------------------------------------


def strip_code_fences(text: str) -> str:
    """Remove markdown code fences if the LLM wrapped its output.

    Handles ````` and `````lang`` prefixes.
    """
    if not text.startswith("```"):
        return text
    lines = text.split("\n")
    if lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()


def extract_text_from_blocks(content_blocks: list[dict]) -> str:
    """Join all ``text`` blocks from an Anthropic response into a single string."""
    return "\n".join(
        b["text"] for b in content_blocks if b.get("type") == "text"
    ).strip()


# ---------------------------------------------------------------------------
# Tool-use system prompt builder
# ---------------------------------------------------------------------------

_TOOL_USE_PREAMBLE = """\
You are a Forge contract generator.  You produce concise, structured
project specification documents for the Forge autonomous build system.

You are generating the **{contract_type}** contract for the repository
"{repo_name}".

{mode_section}

INSTRUCTIONS:
{instructions}

RULES:
- Output ONLY the contract content.  No preamble, no 'Here is...', no
  commentary, no explanations.  The text passed to submit_contract must be
  the FINAL contract and nothing else.
- Be concise and structured.  Bullet points over prose.  No filler words.
  Every line must carry information.  Fill all required fields with
  project-specific facts.  Match the FORMAT of the reference — not its
  verbosity.  Do not exceed it.

WORKFLOW:
1. Use the available tools to fetch the context you need for THIS specific
   contract type.  Only call tools whose data is relevant — do not
   fetch everything.
2. Reason about what you have fetched.  If you need additional context
   (e.g. a prior contract for consistency), fetch it.
3. Once you have enough context, call submit_contract(content) with the
   COMPLETE contract.  Do NOT stream partial content.  One call, final text.

TOOL GUIDANCE:
- get_prior_contract(type)        → fetch a single previously generated
                                    contract for consistency.  Only available
                                    for types generated before this one.
- get_stack_profile()             → detected tech stack (backend, frontend,
                                    testing, infrastructure).
- get_renovation_priorities()     → executive brief: health grade, headline,
                                    top priorities, risk summary.  This is
                                    the primary statement of WHAT the build
                                    must achieve.
- get_migration_tasks()           → ordered task list from the renovation
                                    plan (from_state → to_state, priority,
                                    effort, automatable flag).
- get_project_dossier()           → LLM-written executive summary, quality
                                    assessment, risk areas, recommendations.
- get_compliance_checks()         → pass/fail/warn compliance checks.
- get_architecture_map()          → full detected architecture dict (may be
                                    large — request only if architecture
                                    structure matters for this contract).
- submit_contract(content)        → submit the finished contract.  This
                                    terminates the session.
"""

_GREENFIELD_PREAMBLE = """\
You are a Forge contract generator.  You produce concise, structured
project specification documents for the Forge autonomous build system.

You are generating the **{contract_type}** contract for the project
"{project_name}".

INSTRUCTIONS:
{instructions}

RULES:
- Output ONLY the contract content.  No preamble, no 'Here is...', no
  commentary, no explanations.  The text passed to submit_contract must be
  the FINAL contract and nothing else.
- Be concise and structured.  Bullet points over prose.  No filler words.
  Every line must carry information.  Fill all required fields with
  project-specific facts.  Match the FORMAT of the reference — not its
  verbosity.  Do not exceed it.

WORKFLOW:
1. Use the available tools to fetch the context you need for THIS specific
   contract type.  Only call tools whose data is relevant — do not
   fetch everything.  For broad contracts (blueprint, phases, builder_directive)
   prefer get_all_answers() over multiple granular calls.
2. Reason about what you have fetched.  If you need additional context
   (e.g. a prior contract for consistency), fetch it.
3. Draft the contract mentally.  For important contracts (blueprint, phases,
   builder_directive), call review_draft(content) to self-check your draft
   against the source answers before submitting.
4. Once you are confident the contract is complete and accurate, call
   submit_contract(content) with the FINAL text.  One call, final text.

TOOL GUIDANCE:
- get_prior_contract(type)         → fetch a single previously generated
                                     contract for consistency.
- get_project_intent()             → core product intent, description, and
                                     project goals from questionnaire.
- get_technical_preferences()      → tech stack choices from questionnaire
                                     (backend, frontend, DB, deployment).
- get_user_requirements()          → feature requirements, user stories,
                                     and UI preferences from questionnaire.
- get_deployment_preferences()     → hosting, scale, and infrastructure
                                     preferences from questionnaire.
- get_all_answers()                → ALL questionnaire answers in one call.
                                     Use this for contract types that need a
                                     broad view (e.g. blueprint, phases,
                                     builder_directive) instead of calling
                                     multiple granular tools.
- review_draft(content)            → returns ALL source questionnaire answers
                                     alongside your draft.  Use this to
                                     self-check before submitting.  Costs
                                     one turn but catches hallucinations.
- submit_contract(content)         → submit the finished contract.  This
                                     terminates the session.
"""


def build_tool_use_system_prompt(
    contract_type: str,
    *,
    repo_name: str | None = None,
    project_name: str | None = None,
    mode: str = "scout",
    mini: bool = False,
) -> str:
    """Build the system prompt for a tool-use contract generation session.

    Parameters
    ----------
    contract_type : str
        One of CONTRACT_TYPES (e.g. ``"stack"``, ``"builder_directive"``).
    repo_name : str | None
        Repository name (Scout flow).
    project_name : str | None
        Project name (greenfield flow).
    mode : str
        ``"scout"`` or ``"greenfield"``.
    mini : bool
        If True, use ``_mini`` instruction variants for applicable types.
    """
    instr_key = f"{contract_type}_mini" if mini else contract_type
    instructions = _CONTRACT_INSTRUCTIONS.get(
        instr_key,
        _CONTRACT_INSTRUCTIONS.get(
            contract_type, f"Generate a {contract_type} contract document."
        ),
    )

    if mode == "scout":
        mode_section = (
            "IMPORTANT — EXISTING CODEBASE MODE:\n"
            "This is an EXISTING codebase analysed by Scout.  Your contract must:\n"
            "- Reflect the ACTUAL detected technology stack (do not invent technologies)\n"
            "- Describe the project as it SHOULD be after remediation\n"
            "- For builder_directive: instruct the AI builder to UPDATE/FIX existing\n"
            "  code, not to build from scratch"
        )
        prompt = _TOOL_USE_PREAMBLE.format(
            contract_type=contract_type,
            repo_name=repo_name or "unknown",
            mode_section=mode_section,
            instructions=instructions,
        )
    else:
        prompt = _GREENFIELD_PREAMBLE.format(
            contract_type=contract_type,
            project_name=project_name or "unknown",
            instructions=instructions,
        )

    example = _load_generic_template(contract_type)
    if example:
        prompt += (
            "\n--- STRUCTURAL REFERENCE (match this level of detail and format) ---\n"
            f"{example}\n"
            "--- END REFERENCE ---\n"
        )

    return prompt


# ---------------------------------------------------------------------------
# Tool definitions — Scout context tools
# ---------------------------------------------------------------------------

CONTEXT_TOOLS_SCOUT: list[dict[str, Any]] = [
    {
        "name": "get_stack_profile",
        "description": (
            "Get the detected technology stack for this repository. "
            "Returns backend, frontend, testing, and infrastructure sections."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "get_renovation_priorities",
        "description": (
            "Get the executive brief from the renovation plan: health grade, "
            "headline, top priorities, risk summary, and forge automation note. "
            "This is the primary statement of WHAT the build should achieve."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "get_migration_tasks",
        "description": (
            "Get the ordered migration task list from the renovation plan. "
            "Each task has from_state, to_state, priority, effort, and "
            "forge_automatable flag. Use this to understand what the builder "
            "must accomplish."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "get_project_dossier",
        "description": (
            "Get the LLM dossier from the Scout deep-scan: executive summary, "
            "intent, quality assessment (strengths/weaknesses), risk areas, "
            "and recommendations."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "get_compliance_checks",
        "description": (
            "Get the compliance check results from the Scout scan. "
            "Returns check codes, names, results (pass/fail/warn), and details."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "get_architecture_map",
        "description": (
            "Get the full architecture map detected by Scout. Returns the "
            "complete architecture dict (NOT truncated). Use this when the "
            "architecture structure matters for the contract you're generating."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "get_prior_contract",
        "description": (
            "Fetch a previously generated contract by type. Use this to "
            "ensure consistency with contracts already generated in this run. "
            "Available types: manifesto, blueprint, stack, schema, physics, "
            "boundaries, ui, phases (only those generated before this one)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "contract_type": {
                    "type": "string",
                    "description": (
                        "Contract type to fetch: manifesto | blueprint | stack | "
                        "schema | physics | boundaries | ui | phases"
                    ),
                },
            },
            "required": ["contract_type"],
        },
    },
    {
        "name": "submit_contract",
        "description": (
            "Submit the completed contract content.  Call this ONLY when you "
            "have the complete, final contract ready.  This terminates the "
            "generation session.  The content must be the full contract — "
            "no preamble, no explanation, no code fences."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "content": {
                    "type": "string",
                    "description": "The complete contract content to submit.",
                },
            },
            "required": ["content"],
        },
    },
]


# ---------------------------------------------------------------------------
# Tool definitions — Greenfield context tools
# ---------------------------------------------------------------------------

CONTEXT_TOOLS_GREENFIELD: list[dict[str, Any]] = [
    {
        "name": "get_project_intent",
        "description": (
            "Get the core product intent from the questionnaire: what the "
            "project does, who it's for, and the high-level goals."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "get_technical_preferences",
        "description": (
            "Get the technology preferences from the questionnaire: "
            "chosen backend, frontend, database, and deployment targets."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "get_user_requirements",
        "description": (
            "Get the feature requirements and user stories from the "
            "questionnaire: what the app should do, key screens, and "
            "UI preferences."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "get_deployment_preferences",
        "description": (
            "Get the deployment and infrastructure preferences: hosting "
            "platform, expected scale, and environment setup."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "get_all_answers",
        "description": (
            "Get ALL questionnaire answers in a single call. Returns every "
            "section (product_intent, tech_stack, ui_requirements, etc.) "
            "plus project name and description. Use this instead of calling "
            "multiple granular tools when you need a broad view of the project."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "review_draft",
        "description": (
            "Self-check a draft contract before submitting. Pass your draft "
            "content and receive it back alongside ALL source questionnaire "
            "answers so you can verify accuracy, completeness, and consistency "
            "with what the user actually said. Use this for important contracts "
            "like blueprint, phases, and builder_directive."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "content": {
                    "type": "string",
                    "description": "The draft contract content to review.",
                },
            },
            "required": ["content"],
        },
    },
    {
        "name": "get_prior_contract",
        "description": (
            "Fetch a previously generated contract by type.  Use this to "
            "ensure consistency with contracts already generated in this run."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "contract_type": {
                    "type": "string",
                    "description": "Contract type to fetch.",
                },
            },
            "required": ["contract_type"],
        },
    },
    {
        "name": "submit_contract",
        "description": (
            "Submit the completed contract content.  Call this ONLY when you "
            "have the complete, final contract ready.  This terminates the "
            "generation session."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "content": {
                    "type": "string",
                    "description": "The complete contract content to submit.",
                },
            },
            "required": ["content"],
        },
    },
]


# ---------------------------------------------------------------------------
# Tool executor — Scout flow
# ---------------------------------------------------------------------------


def execute_scout_tool(
    name: str,
    tool_input: dict,
    context_data: dict,
    prior_contracts: dict[str, str],
) -> str:
    """Execute a Scout context tool and return a JSON-string result.

    Pure function (no I/O) — context_data is pre-extracted from scout results.
    """
    match name:
        case "get_stack_profile":
            return json.dumps(context_data.get("stack_profile") or {})
        case "get_renovation_priorities":
            return json.dumps(context_data.get("executive_brief") or {})
        case "get_migration_tasks":
            return json.dumps(context_data.get("migration_tasks") or [])
        case "get_project_dossier":
            return json.dumps(context_data.get("dossier") or {})
        case "get_compliance_checks":
            return json.dumps(context_data.get("checks") or [])
        case "get_architecture_map":
            return json.dumps(context_data.get("architecture") or {})
        case "get_prior_contract":
            ctype = tool_input.get("contract_type", "")
            content = prior_contracts.get(ctype)
            if not content:
                return json.dumps({"error": f"Contract '{ctype}' not yet generated."})
            return content
        case "submit_contract":
            # submit_contract is handled by the loop, not here
            return json.dumps({"error": "submit_contract should not be executed directly"})
        case _:
            return json.dumps({"error": f"Unknown tool: {name}"})


# ---------------------------------------------------------------------------
# Tool executor — Greenfield flow
# ---------------------------------------------------------------------------


def execute_greenfield_tool(
    name: str,
    tool_input: dict,
    answers_data: dict,
    prior_contracts: dict[str, str],
) -> str:
    """Execute a Greenfield context tool and return a JSON-string result.

    Pure function — answers_data is pre-extracted from questionnaire answers
    and project metadata.

    Expected keys in answers_data:
      product_intent, tech_stack, ui_requirements, api_endpoints,
      database_schema, architectural_boundaries, deployment_target,
      project_name, project_description
    """
    match name:
        case "get_project_intent":
            intent = {}
            for key in ("product_intent", "project_name", "project_description"):
                val = answers_data.get(key)
                if val and not _is_placeholder(val):
                    intent[key] = val
            return json.dumps(intent)
        case "get_technical_preferences":
            prefs = {}
            for key in ("tech_stack", "architectural_boundaries"):
                val = answers_data.get(key)
                if val and not _is_placeholder(val):
                    prefs[key] = val
                elif _is_placeholder(val) and key in MINI_DEFAULTS:
                    prefs[key] = _adaptive_mini_default(key, answers_data)
            return json.dumps(prefs)
        case "get_user_requirements":
            req = {}
            for key in ("ui_requirements", "api_endpoints", "database_schema"):
                val = answers_data.get(key)
                if val and not _is_placeholder(val):
                    req[key] = val
                elif _is_placeholder(val) and key in MINI_DEFAULTS:
                    req[key] = _adaptive_mini_default(key, answers_data)
            return json.dumps(req)
        case "get_deployment_preferences":
            raw = answers_data.get("deployment_target")
            if _is_placeholder(raw):
                raw = _adaptive_mini_default("deployment_target", answers_data)
            return json.dumps(raw or {})
        case "get_all_answers":
            # Return everything in one shot — useful for broad contracts
            all_data = {}
            for key, val in answers_data.items():
                if _is_placeholder(val) and key in MINI_DEFAULTS:
                    all_data[key] = _adaptive_mini_default(key, answers_data)
                elif val:
                    all_data[key] = val
            return json.dumps(all_data)
        case "review_draft":
            # Return source answers alongside the draft for self-checking
            draft = tool_input.get("content", "")
            all_answers = {}
            for key, val in answers_data.items():
                if _is_placeholder(val) and key in MINI_DEFAULTS:
                    all_answers[key] = _adaptive_mini_default(key, answers_data)
                elif val:
                    all_answers[key] = val
            review = {
                "instruction": (
                    "Compare your draft against the source answers below. "
                    "Check: (1) no hallucinated features not mentioned by the user, "
                    "(2) no missing features the user DID mention, "
                    "(3) terminology matches the user's language, "
                    "(4) scope is appropriate for the build mode. "
                    "If satisfied, call submit_contract. If not, revise and submit."
                ),
                "source_answers": all_answers,
                "your_draft_length_chars": len(draft),
            }
            return json.dumps(review)
        case "get_prior_contract":
            ctype = tool_input.get("contract_type", "")
            content = prior_contracts.get(ctype)
            if not content:
                return json.dumps({"error": f"Contract '{ctype}' not yet generated."})
            return content
        case "submit_contract":
            return json.dumps({"error": "submit_contract should not be executed directly"})
        case _:
            return json.dumps({"error": f"Unknown tool: {name}"})


# ---------------------------------------------------------------------------
# Multi-turn tool-use generation loop
# ---------------------------------------------------------------------------


async def generate_contract_with_tools(
    contract_type: str,
    system_prompt: str,
    tools: list[dict],
    tool_executor: Any,  # Callable[[str, dict], str]
    *,
    api_key: str,
    model: str,
    provider: str = "anthropic",
    repo_name: str = "",
    max_turns: int = 12,
    enable_caching: bool = True,
    on_turn_progress: "Any | None" = None,  # async (input_tokens, output_tokens) -> None
) -> tuple[str, dict]:
    """Generate a single contract using a multi-turn tool-use loop.

    The LLM decides which context tools to call, fetches what it needs,
    then calls ``submit_contract(content)`` to produce the final contract.

    Parameters
    ----------
    contract_type : str
        Contract being generated (for error messages).
    system_prompt : str
        Built via ``build_tool_use_system_prompt()``.
    tools : list[dict]
        Tool definitions (CONTEXT_TOOLS_SCOUT or CONTEXT_TOOLS_GREENFIELD).
    tool_executor : callable
        ``(name: str, tool_input: dict) -> str`` — executes a tool, returns
        the result as a string.
    api_key / model / provider : str
        LLM config.
    repo_name : str
        For the initial user message.
    max_turns : int
        Safety limit on tool-use turns.
    enable_caching : bool
        Pass through to ``chat(enable_caching=…)``.

    Returns
    -------
    (content, total_usage) where content is the raw contract text.
    """
    messages: list[dict] = [
        {
            "role": "user",
            "content": (
                f"Generate the {contract_type} contract"
                + (f" for repository '{repo_name}'" if repo_name else "")
                + ".  Use the available tools to fetch exactly the context "
                "you need, then call submit_contract with the complete "
                "contract content."
            ),
        }
    ]

    total_usage: dict[str, int] = {"input_tokens": 0, "output_tokens": 0}

    logger.info(
        "[contract:loop] START  type=%s  model=%s  provider=%s  max_turns=%d",
        contract_type, model, provider, max_turns,
    )

    for _turn in range(max_turns):
        response = await llm_chat(
            api_key=api_key,
            model=model,
            system_prompt=system_prompt,
            messages=messages,
            max_tokens=16384,
            provider=provider,
            tools=tools,
            enable_caching=enable_caching,
        )

        # Accumulate token usage
        usage = response.get("usage", {})
        total_usage["input_tokens"] += usage.get("input_tokens", 0)
        total_usage["output_tokens"] += usage.get("output_tokens", 0)

        stop_reason = response.get("stop_reason")
        content_blocks = response.get("content", [])

        logger.info(
            "[contract:loop] turn=%d/%d  type=%s  model=%s  stop=%s  in=%d out=%d",
            _turn + 1, max_turns, contract_type, model, stop_reason,
            usage.get("input_tokens", 0), usage.get("output_tokens", 0),
        )

        # Emit per-turn progress so the UI can show live token counts
        if on_turn_progress is not None:
            try:
                await on_turn_progress(
                    total_usage["input_tokens"],
                    total_usage["output_tokens"],
                )
            except Exception:
                pass  # best-effort — don't break generation

        # Append assistant turn to message history
        messages.append({"role": "assistant", "content": content_blocks})

        # ── Check for submit_contract in tool_use blocks ────────
        for block in content_blocks:
            if (
                block.get("type") == "tool_use"
                and block.get("name") == "submit_contract"
            ):
                submitted = block.get("input", {}).get("content", "")
                if submitted:
                    logger.info(
                        "[contract:loop] SUBMIT  type=%s  turn=%d  total_in=%d total_out=%d",
                        contract_type, _turn + 1,
                        total_usage["input_tokens"], total_usage["output_tokens"],
                    )
                    return strip_code_fences(submitted), total_usage
        if stop_reason == "end_turn":
            fallback = extract_text_from_blocks(content_blocks)
            if fallback:
                logger.info(
                    "[contract:loop] TEXT FALLBACK  type=%s  turn=%d  total_in=%d total_out=%d",
                    contract_type, _turn + 1,
                    total_usage["input_tokens"], total_usage["output_tokens"],
                )
                return strip_code_fences(fallback), total_usage
            raise ValueError(
                f"LLM ended turn for {contract_type} without submitting "
                "a contract or returning text."
            )

        # ── Execute tool calls and build tool_results turn ──────
        if stop_reason == "tool_use":
            tool_results: list[dict] = []
            for block in content_blocks:
                if block.get("type") != "tool_use":
                    continue
                tool_name = block.get("name", "")
                tool_input_data = block.get("input", {})
                tool_id = block.get("id", "")

                # submit_contract detected inside a multi-tool response
                if tool_name == "submit_contract":
                    submitted = tool_input_data.get("content", "")
                    if submitted:
                        return strip_code_fences(submitted), total_usage

                result_content = tool_executor(tool_name, tool_input_data)
                logger.info(
                    "[contract:tool] type=%s  turn=%d  tool=%s  input=%s",
                    contract_type, _turn + 1, tool_name,
                    json.dumps(tool_input_data, default=str)[:150],
                )
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": tool_id,
                    "content": result_content,
                })

            if tool_results:
                messages.append({"role": "user", "content": tool_results})
            continue

        # Unexpected stop_reason — break
        logger.warning(
            "[contract:loop] UNEXPECTED stop_reason=%s  type=%s  turn=%d",
            stop_reason, contract_type, _turn + 1,
        )
        break

    logger.error(
        "[contract:loop] EXHAUSTED  type=%s  max_turns=%d  total_in=%d total_out=%d",
        contract_type, max_turns,
        total_usage["input_tokens"], total_usage["output_tokens"],
    )
    raise ValueError(
        f"Tool-use loop for {contract_type} exceeded {max_turns} turns "
        "without producing a contract."
    )
