"""
auditor_agent.py — Main auditor agent loop.

This is the external audit agent for Planner and Builder.
It is completely independent and uses governance contracts for standards.

Modes:
  - "plan": audit a plan.json from Planner
  - "phase": audit a builder phase output
"""

from __future__ import annotations

import asyncio
import functools
import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any, Callable, Optional
from uuid import UUID

# Ensure the auditor package itself is on sys.path so bare imports work
# whether this module is imported directly or as part of a package.
_AUDITOR_DIR = Path(__file__).resolve().parent
if str(_AUDITOR_DIR) not in sys.path:
    sys.path.insert(0, str(_AUDITOR_DIR))

import anthropic

from audit_schema import AuditResult, TokenUsage, validate_audit_result
from context_loader import build_audit_system_prompt
from tools import TOOL_DEFINITIONS, dispatch_tool

logger = logging.getLogger(__name__)

# Configuration
MODEL = os.environ.get("LLM_AUDITOR_MODEL", "claude-opus-4-1")
MAX_TOKENS = 16_000  # Audits are typically shorter than planning
MAX_ITERATIONS = 5  # Safety valve — prevent runaway loops

# Initial user turns for each mode
INITIAL_USER_TURN_PLAN = """\
You are auditing the plan below. Use the audit_complete tool when done.

PLAN TO AUDIT:
{plan_json}

Begin your audit now.
"""

INITIAL_USER_TURN_PHASE = """\
You are auditing the builder phase output below. Use the audit_complete tool when done.

PHASE {phase_number} OUTPUT:
- Files: {phase_files}
- Test coverage: {test_coverage}%
- Pre-detected violations: {violations_list}

Begin your audit now.
"""


class AuditorError(Exception):
    """Raised when auditor encounters an error."""

    pass


async def run_auditor(
    mode: str,
    build_id: UUID,
    project_id: UUID,
    plan_json: Optional[dict] = None,
    phase_number: Optional[int] = None,
    phase_files: Optional[list[str]] = None,
    test_coverage: Optional[float] = None,
    violations: Optional[list[dict]] = None,
    contract_fetcher: Optional[Callable] = None,
    verbose: bool = True,
) -> AuditResult:
    """
    Run auditor in specified mode.

    Args:
        mode: "plan" for planner audits, "phase" for builder audits
        build_id: unique build identifier
        project_id: project id
        plan_json: plan to audit (mode="plan")
        phase_number: phase number (mode="phase")
        phase_files: files generated in phase (mode="phase")
        test_coverage: test coverage percentage (mode="phase")
        violations: pre-detected violations (mode="phase")
        contract_fetcher: function to fetch contracts by name
        verbose: print debug output

    Returns:
        AuditResult with pass/fail status and issues

    Raises:
        AuditorError on critical failure
    """
    agent_name = "planner_auditor" if mode == "plan" else "builder_auditor"

    if verbose:
        logger.info(f"[AUDITOR] Starting {agent_name} (build={build_id}, mode={mode})")

    # Initialize client
    client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

    # Build system prompt with governance contracts
    system_blocks = build_audit_system_prompt(mode, contract_fetcher=contract_fetcher)

    # Build initial user turn
    if mode == "plan":
        user_turn = INITIAL_USER_TURN_PLAN.format(
            plan_json=json.dumps(plan_json, indent=2)[:8000]  # Truncate for token limits
        )
    elif mode == "phase":
        violations_str = "\n".join([f"  - {v}" for v in (violations or [])])
        user_turn = INITIAL_USER_TURN_PHASE.format(
            phase_number=phase_number,
            phase_files=", ".join(phase_files or [])[:500],
            test_coverage=test_coverage or 0,
            violations_list=violations_str if violations else "(none)",
        )
    else:
        raise AuditorError(f"Unknown mode: {mode}")

    messages = [{"role": "user", "content": user_turn}]
    total_usage = {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_tokens": 0,
        "cache_write_tokens": 0,
    }

    start_time = time.time()
    iteration = 0
    audit_result: Optional[AuditResult] = None

    # ─── Agentic Loop ────────────────────────────────────────────────────────
    while iteration < MAX_ITERATIONS:
        iteration += 1

        if verbose:
            logger.info(f"[AUDITOR] Turn {iteration}/{MAX_ITERATIONS}")

        try:
            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(
                None,
                functools.partial(
                    client.messages.create,
                    model=MODEL,
                    max_tokens=MAX_TOKENS,
                    system=system_blocks,
                    tools=TOOL_DEFINITIONS,
                    messages=messages,
                ),
            )
        except Exception as e:
            raise AuditorError(f"API call failed: {e}")

        # Accumulate token usage
        u = response.usage
        total_usage["input_tokens"] += u.input_tokens
        total_usage["output_tokens"] += u.output_tokens
        total_usage["cache_read_tokens"] += getattr(u, "cache_read_input_tokens", 0)
        total_usage["cache_write_tokens"] += getattr(u, "cache_creation_input_tokens", 0)

        if verbose:
            logger.info(
                f"[AUDITOR] API: {u.output_tokens} output tokens, "
                f"cache_read={total_usage['cache_read_tokens']}"
            )

        # Append assistant response to messages
        assistant_content = []
        for block in response.content:
            if block.type == "text":
                assistant_content.append({"type": "text", "text": block.text})
            elif block.type == "tool_use":
                assistant_content.append(
                    {
                        "type": "tool_use",
                        "id": block.id,
                        "name": block.name,
                        "input": block.input,
                    }
                )
        messages.append({"role": "assistant", "content": assistant_content})

        # Handle end_turn (protocol violation)
        if response.stop_reason == "end_turn":
            raise AuditorError("Auditor ended turn without calling audit_complete")

        # Dispatch tool calls
        tool_results = []
        for block in response.content:
            if block.type != "tool_use":
                continue

            if verbose:
                logger.info(f"[AUDITOR] Tool: {block.name}")

            try:
                result = dispatch_tool(block.name, block.input)
            except Exception as e:
                result = {"error": str(e), "success": False}

            if block.name == "audit_complete":
                # Audit complete — extract result
                if result.get("success"):
                    audit_data = result.get("audit_result", {})
                    try:
                        # Convert dicts to Pydantic models
                        from audit_schema import AuditCheck, AuditRecommendation

                        issues = [
                            AuditCheck(**issue) for issue in audit_data.get("issues", [])
                        ]
                        recommendations = [
                            AuditRecommendation(**rec)
                            for rec in audit_data.get("recommendations", [])
                        ]

                        audit_result = AuditResult(
                            passed=audit_data["passed"],
                            status=audit_data["status"],
                            issues=issues,
                            recommendations=recommendations,
                            token_usage=TokenUsage(**total_usage),
                            duration_seconds=time.time() - start_time,
                        )

                        # Validate result
                        is_valid, err_msg = validate_audit_result(audit_result)
                        if not is_valid:
                            raise AuditorError(f"Audit result invalid: {err_msg}")

                        if verbose:
                            logger.info(
                                f"[AUDITOR] Complete: {audit_result.status} "
                                f"({len(audit_result.issues)} issues)"
                            )

                        return audit_result

                    except Exception as e:
                        raise AuditorError(f"Failed to parse audit result: {e}")
                else:
                    tool_results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": json.dumps(result),
                        }
                    )
            else:
                # Other tools — add result back
                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": json.dumps(result),
                    }
                )

        if tool_results:
            messages.append({"role": "user", "content": tool_results})
        else:
            # No tool calls but didn't end_turn?
            if response.stop_reason != "end_turn":
                continue
            else:
                raise AuditorError("No tool calls and end_turn triggered")

    raise AuditorError(f"Auditor hit MAX_ITERATIONS ({MAX_ITERATIONS})")
