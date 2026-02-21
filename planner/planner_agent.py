"""
planner_agent.py — The Forge Planner Agent.

This is the educational core of the entire agent framework. Read this file
and you understand how every Anthropic-SDK agent works.

THE CORE INSIGHT
----------------
A system prompt + tools in a chat = single-turn inference. NOT an agent.

An agent = a loop where:
  1. The model sees the full message history and available tools
  2. The model either calls tools (needs more info) or ends (done)
  3. If it calls tools, you execute them and add results to the history
  4. The loop repeats until the model calls the terminal tool or ends cleanly

The application code does nothing intelligent — it only dispatches tool
calls and feeds results back. The model does all the reasoning.

LOOP TERMINATION
----------------
There are two termination paths:
  A. write_plan(plan_json, project_name) returns {success: True}
     → clean exit, plan artifact on disk
  B. Iteration count exceeds MAX_ITERATIONS
     → PlannerError raised, no artifact written

Path A is the only valid exit for a well-behaved run. Path B means the
model got stuck (usually due to a bad system prompt or impossible task).

TOKEN TRACKING
--------------
Every API response carries a usage object. We accumulate across turns to
give you a total cost picture at the end. The cache fields tell you how
much the prompt caching actually saved.

Usage:
    from planner_agent import run_planner
    result = run_planner("Build a FastAPI task API with PostgreSQL and JWT auth")
    print(result["plan_path"])
"""

from __future__ import annotations
import json
import os
import threading
import anthropic

from context_loader import build_system_prompt
from tools import TOOL_DEFINITIONS, dispatch_tool

# ─── Configuration ───────────────────────────────────────────────────────────

# Model is read from env var so ForgeGuard can inject its tier setting.
# Defaults to Haiku for cheap flow validation; set LLM_PLANNER_MODEL in
# the environment (or pass model= to run_planner) to use a different model.
# FORGE_FORCE_MODEL beats LLM_PLANNER_MODEL — same override chain as config.py
MODEL = (
    os.environ.get("FORGE_FORCE_MODEL")
    or os.environ.get("LLM_PLANNER_MODEL")
    or "claude-haiku-4-5"
)

# Max tokens the model can output in a single response.
# 8096 is enough for a full plan.json in a single write_plan call.
MAX_TOKENS = 8096

# Safety valve: if the loop hasn't exited after this many turns, abort.
# A healthy planning run uses 4-8 turns. 20 gives plenty of room for
# validation failures and retries while preventing runaway loops.
MAX_ITERATIONS = 20

# Initial user turn — orients the model to its task.
# {max_phases_constraint} is replaced at runtime — empty string for full builds,
# a hard limit instruction for mini-builds.
INITIAL_USER_TURN = """\
Please analyze the following project contracts and produce a complete Forge build plan.

All project data is provided below — it was fetched from the database for you.
Do NOT attempt to read any files from disk; no filesystem tools are available.
{max_phases_constraint}
PROJECT CONTRACTS:
{project_request}

INSTRUCTIONS:
1. Read the contracts above carefully — they contain everything you need.
2. When you have a complete plan that covers all required phases, call write_plan.
   Do NOT call write_plan with a partial or incomplete plan.

REMINDER: The plan must be self-contained. The builder uses ONLY plan.json
to build the entire project — it must not need to ask clarifying questions.
"""

# Correction injected when the model uses end_turn without calling write_plan
END_TURN_CORRECTION = (
    "You used end_turn without calling write_plan. "
    "Your plan has not been saved yet. "
    "All project contracts are already in your context. "
    "Please call write_plan now with your complete plan."
)


# ─── Helpers (pre-loop) ──────────────────────────────────────────────────────

def _content_to_params(content: list) -> list[dict]:
    """
    Convert Anthropic SDK content block objects to plain dicts.

    The SDK returns typed Pydantic objects (TextBlock, ToolUseBlock, etc.).
    Storing them directly in message history and re-sending causes
    PydanticSerializationError on the next API call (model_dump(by_alias=True)
    fails when a field value is None). Converting to plain dicts avoids this.
    """
    params = []
    for block in content:
        if block.type == "text":
            params.append({"type": "text", "text": block.text})
        elif block.type == "tool_use":
            params.append({
                "type": "tool_use",
                "id": block.id,
                "name": block.name,
                "input": block.input,
            })
        elif block.type == "thinking":
            # Thinking blocks MUST be echoed back with their signature.
            # The API validates the signature chain — omitting causes errors.
            params.append({
                "type": "thinking",
                "thinking": block.thinking,
                "signature": block.signature,
            })
    return params


# ─── Main Agent Function ─────────────────────────────────────────────────────

def run_planner(
    project_request: str,
    verbose: bool = True,
    turn_callback: "callable | None" = None,
    stop_event: "threading.Event | None" = None,
    model: str | None = None,
    thinking_budget: int = 0,
    thinking_model: str | None = None,
    max_phases: int | None = None,
) -> dict:
    """
    Run the Planner Agent to completion and return the plan artifact.

    Args:
        project_request: Natural language description of what to build.
                         Include stack preferences, mode, and constraints
                         if you have them. Example:
                           "Build a FastAPI task management API with PostgreSQL,
                            JWT auth, and pytest test coverage. Greenfield project."
        verbose: If True, prints each loop iteration and tool call to stdout.
                 Set to False for programmatic use.

    Returns:
        dict with keys:
          "plan_path"   (str)  — absolute path to the written plan.json
          "plan"        (dict) — the parsed plan content
          "token_usage" (dict) — cumulative token counts across all turns
          "iterations"  (int)  — number of agentic loop turns taken

    Raises:
        PlannerError: if the loop exceeds MAX_ITERATIONS without completing,
                      or if the ANTHROPIC_API_KEY is not set.
    """
    # client() picks up ANTHROPIC_API_KEY from environment automatically.
    # Timeout prevents threads from hanging indefinitely — critical because
    # this runs in a thread pool executor and Python cannot kill threads once
    # started. Without a timeout, a cancelled build still charges until the
    # API call eventually completes (up to 600s by default).
    # 120s: planning turns with large contract contexts (20-30K tokens) can
    # legitimately take 60-90s to respond — 30s was too aggressive.
    client = anthropic.Anthropic(timeout=120.0)
    _model = model or MODEL

    # ── Build the cacheable system prompt ────────────────────────────────────
    # First call: pays full token price to write the cache.
    # Calls 2-N: pays ~10% of the token price (cache READ).
    system_blocks = build_system_prompt(project_hint=project_request)

    # ── Initialize message history ───────────────────────────────────────────
    # This list is the agent's state. It grows with every turn.
    # The model sees the entire list on every API call — that is how it
    # knows what it already did and what it still needs to do.
    if max_phases is not None:
        _phases_constraint = (
            f"\nPHASE LIMIT: Produce a plan with a MAXIMUM of {max_phases} phases. "
            + ("This is a MINI-BUILD (proof of concept) — focus on the minimal viable "
               "implementation (core functionality only, no extras).\n"
               if max_phases <= 3 else
               f"Keep the plan to at most {max_phases} phases.\n")
        )
    else:
        _phases_constraint = ""

    messages: list[dict] = [
        {
            "role": "user",
            "content": INITIAL_USER_TURN.format(
                project_request=project_request,
                max_phases_constraint=_phases_constraint,
            ),
        }
    ]

    # ── Cumulative token accounting ──────────────────────────────────────────
    total_usage = {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_creation_input_tokens": 0,  # tokens written to cache (paid once)
        "cache_read_input_tokens": 0,       # tokens served from cache (10% cost)
    }

    plan_write_result: dict | None = None
    iteration = 0

    # ════════════════════════════════════════════════════════════════════════
    # THE AGENTIC LOOP
    # ════════════════════════════════════════════════════════════════════════
    while iteration < MAX_ITERATIONS:
        # ── (0) CHECK STOP SIGNAL ─────────────────────────────────────────────
        # Allows callers (e.g. planner_service on server shutdown) to interrupt
        # the loop between turns without killing the thread forcibly.
        if stop_event is not None and stop_event.is_set():
            raise PlannerError("Planner interrupted by stop signal")

        iteration += 1

        if verbose:
            print(f"\n[PLANNER] ── Turn {iteration}/{MAX_ITERATIONS} ──")

        # ── (1) INFERENCE — send message history to the model ────────────────
        # The model sees: system prompt (cached) + all prior messages + tools.
        # It decides whether to call tools or end the turn.
        # Use thinking_model for thinking turns (Sonnet even when Haiku is forced).
        # Falls back to _model for all non-thinking turns.
        _call_model = (thinking_model or _model) if thinking_budget > 0 else _model
        _create_kwargs: dict = dict(
            model=_call_model,
            max_tokens=max(MAX_TOKENS, thinking_budget + 4096) if thinking_budget > 0 else MAX_TOKENS,
            system=system_blocks,     # cacheable blocks from context_loader
            tools=TOOL_DEFINITIONS,   # tool JSON schemas from tools.py
            messages=messages,
        )
        if thinking_budget > 0:
            _create_kwargs["thinking"] = {"type": "enabled", "budget_tokens": thinking_budget}

        response = client.messages.create(**_create_kwargs)

        # ── (2) ACCUMULATE TOKEN USAGE ───────────────────────────────────────
        u = response.usage
        total_usage["input_tokens"] += u.input_tokens
        total_usage["output_tokens"] += u.output_tokens
        total_usage["cache_creation_input_tokens"] += getattr(u, "cache_creation_input_tokens", 0)
        total_usage["cache_read_input_tokens"] += getattr(u, "cache_read_input_tokens", 0)

        if verbose:
            cache_read = getattr(u, "cache_read_input_tokens", 0)
            cache_write = getattr(u, "cache_creation_input_tokens", 0)
            status = (
                f"stop={response.stop_reason}, "
                f"out={u.output_tokens} tokens, "
                f"cache_read={cache_read}, cache_write={cache_write}"
            )
            print(f"[PLANNER] API: {status}")
            if thinking_budget > 0:
                thinking_blocks = [b for b in response.content if b.type == "thinking"]
                if thinking_blocks:
                    total_chars = sum(len(b.thinking) for b in thinking_blocks)
                    print(f"[PLANNER] Extended thinking: {total_chars:,} chars across {len(thinking_blocks)} block(s)")

        # ── (2b) FIRE TURN CALLBACK ───────────────────────────────────────────
        # Allows callers (e.g. MCP tool) to observe each turn as it completes.
        if turn_callback is not None:
            _tblocks = [b for b in response.content if b.type == "thinking"] if thinking_budget > 0 else []
            _thinking_text: str | None = "\n\n".join(b.thinking for b in _tblocks) if _tblocks else None
            _text_blocks = [b for b in response.content if b.type == "text"]
            _text_content: str | None = "\n\n".join(b.text for b in _text_blocks) if _text_blocks else None
            turn_callback({
                "turn": iteration,
                "stop_reason": response.stop_reason,
                "output_tokens": u.output_tokens,
                "cache_hit": bool(getattr(u, "cache_read_input_tokens", 0)),
                "tool_calls": [
                    {
                        "name": b.name,
                        "input_keys": list(b.input.keys()),
                        "input_preview": {
                            k: str(v)[:120]
                            for k, v in b.input.items()
                            if k != "plan_json"
                        },
                    }
                    for b in response.content if b.type == "tool_use"
                ],
                "thinking_text": _thinking_text,
                "text_content": _text_content,
            })

        # ── (3) APPEND ASSISTANT TURN TO HISTORY ─────────────────────────────
        # CRITICAL: this must happen BEFORE any tool dispatch.
        # Without appending the assistant turn, the next API call won't
        # have the model's reasoning in context — it will repeat itself.
        #
        # Convert SDK Pydantic objects to plain dicts. Storing SDK objects
        # directly and re-sending causes PydanticSerializationError on the
        # next API call when model_dump(by_alias=True) encounters a None field.
        messages.append({"role": "assistant", "content": _content_to_params(response.content)})

        # ── (4) HANDLE end_turn: model finished without calling write_plan ───
        # This is a protocol violation — the planner must always deliver its
        # artifact via write_plan. We inject a correction and continue.
        if response.stop_reason == "end_turn":
            if verbose:
                print("[PLANNER] WARNING: end_turn without write_plan — injecting correction.")
            messages.append({"role": "user", "content": END_TURN_CORRECTION})
            continue

        # ── (5) DISPATCH TOOL CALLS ───────────────────────────────────────────
        # Collect all tool_use blocks from this response turn,
        # execute each one, and gather results for the next turn.
        tool_results: list[dict] = []
        should_exit = False

        for block in response.content:
            if block.type != "tool_use":
                continue  # skip text blocks (model narrating its reasoning)

            if verbose:
                keys = list(block.input.keys())
                print(f"[PLANNER] Tool: {block.name}({keys})")

            result = dispatch_tool(block.name, block.input)

            # ── (5a) TERMINAL TOOL CHECK ──────────────────────────────────────
            # write_plan exits the loop ONLY on success.
            # On validation failure, errors go back to the model as a tool
            # result and the loop continues — the model fixes and retries.
            if block.name == "write_plan":
                if verbose:
                    ok = result.get("success", False)
                    print(f"[PLANNER] write_plan: {'SUCCESS' if ok else 'FAILED'}")
                    if not ok:
                        for err in result.get("errors", []):
                            print(f"[PLANNER]   error: {err}")

                if result.get("success"):
                    plan_write_result = result
                    should_exit = True
                    # Still add the tool result so the message history is clean,
                    # but we will break out of the outer while loop after.

            tool_results.append({
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": json.dumps(result),
            })

        # ── (6) INJECT TOOL RESULTS as a new user turn ───────────────────────
        # This is how the model sees what its tool calls returned.
        # "user" role for tool results is required by the Anthropic API.
        if tool_results:
            messages.append({"role": "user", "content": tool_results})

        # ── (7) EXIT if write_plan succeeded ─────────────────────────────────
        if should_exit:
            break

    else:
        # Loop exhausted without a successful write_plan call
        raise PlannerError(
            f"Planner exceeded {MAX_ITERATIONS} iterations without completing "
            "write_plan. The model may be stuck. Check your system prompt and "
            "contracts for conflicting instructions."
        )
    # ════════════════════════════════════════════════════════════════════════
    # END OF LOOP
    # ════════════════════════════════════════════════════════════════════════

    # Stamp the planner model into metadata (the tool can't self-report model ID)
    _stamp_model(plan_write_result["path"], _model)

    if verbose:
        _print_summary(plan_write_result["path"], total_usage, iteration)

    # Load plan from disk and return it
    from pathlib import Path
    import json as _json
    plan_data = _json.loads(Path(plan_write_result["path"]).read_text(encoding="utf-8"))

    return {
        "plan_path": plan_write_result["path"],
        "plan": plan_data,
        "token_usage": total_usage,
        "iterations": iteration,
    }


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _stamp_model(plan_path: str, model: str) -> None:
    """Add planner_model to the metadata block after writing."""
    import json as _json
    from pathlib import Path
    p = Path(plan_path)
    data = _json.loads(p.read_text(encoding="utf-8"))
    data.setdefault("metadata", {})["planner_model"] = model
    p.write_text(_json.dumps(data, indent=2), encoding="utf-8")


def _print_summary(plan_path: str, usage: dict, iterations: int) -> None:
    inp = usage["input_tokens"]
    out = usage["output_tokens"]
    c_write = usage["cache_creation_input_tokens"]
    c_read = usage["cache_read_input_tokens"]
    savings_pct = (c_read / max(inp, 1)) * 100

    print(f"\n[PLANNER] ════════════════════════════════════")
    print(f"[PLANNER] PLAN COMPLETE")
    print(f"[PLANNER]   Output:     {plan_path}")
    print(f"[PLANNER]   Iterations: {iterations}")
    print(f"[PLANNER] TOKEN USAGE:")
    print(f"[PLANNER]   Input:             {inp:>10,}")
    print(f"[PLANNER]   Output:            {out:>10,}")
    print(f"[PLANNER]   Cache writes:      {c_write:>10,}")
    print(f"[PLANNER]   Cache reads:       {c_read:>10,}  ← served at ~10% cost")
    print(f"[PLANNER]   Cache savings:     {savings_pct:>9.1f}%  of input tokens")
    print(f"[PLANNER] ════════════════════════════════════")
    print(f"[PLANNER] NEXT STEP:")
    print(f"[PLANNER]   python run_builder.py \"{plan_path}\"")


class PlannerError(Exception):
    """Raised when the planner loop fails to produce a valid plan."""
    pass
