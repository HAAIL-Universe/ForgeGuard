"""
tools.py — Tool definitions for the Builder Orchestrator.

The builder orchestrator has NO direct tools — it delegates every LLM call
to sub-agents (Scout, Coder, Auditor, Fixer) via run_sub_agent().

This module exists for structural parity with planner/tools.py so the
builder module follows the same layout as the planner module.
"""

from __future__ import annotations
from typing import Any


# The orchestrator dispatches no tools directly.
# Each sub-agent uses its own role-filtered tool set from subagent.py.
TOOL_DEFINITIONS: list[dict] = []


def dispatch_tool(name: str, inputs: dict) -> Any:
    """Pass-through stub — the orchestrator has no direct tools.

    Sub-agent tool dispatch is handled inside run_sub_agent() in
    app/services/build/subagent.py.
    """
    return {
        "error": (
            f"Unknown tool: '{name}'. "
            "The builder orchestrator has no direct tools — "
            "tool calls are dispatched inside sub-agents."
        )
    }
