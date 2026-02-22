"""
Builder Agent Interface
=======================
Integration shim between ForgeGuard's build_service and the standalone
builder/ orchestrator module.

The builder orchestrator lives in builder/builder_agent.py (mirrors
planner/planner_agent.py). This shim adapts the generate_file() interface
used by build_service._run_build_plan_execute() to the builder's run_builder()
function.

Output contract
---------------
``generate_file()`` returns a ``str``:
  - Complete file content ready to write to disk.
  - No markdown code fences (stripped by CODER single-shot parser).
  - Trailing newline ensured by the CODER writer.

The downstream code in ``_run_build_plan_execute()`` expects exactly this
contract — it writes the string directly to disk and does not post-process it.
That second disk write is idempotent; the CODER sub-agent already wrote the
file as a side effect of its single-shot runner.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

# Re-export BuilderError and BuilderResult so callers only need to import
# from this module.
from builder.builder_agent import (  # noqa: F401
    BuilderError,
    BuilderResult,
    run_builder,
)


async def generate_file(
    build_id: str,
    user_id: str,
    api_key: str,
    file_entry: dict[str, Any],
    contracts: list[str],
    context: list[str],
    phase_deliverables: list[str],
    working_dir: str | Path,
    phase_plan_context: str = "",
    audit_api_key: str | None = None,
) -> str:
    """Generate a single file's content via the builder agent pipeline.

    Args:
        build_id:           Active build UUID (string).
        user_id:            Authenticated user UUID (string).
        api_key:            Anthropic API key for LLM calls.
        file_entry:         Dict with keys: path, purpose, estimated_lines, language.
        contracts:          List of contract strings (governance rules).
        context:            List of relevant existing file contents for context.
        phase_deliverables: Phase objective + acceptance criteria strings.
        working_dir:        Absolute path to the build workspace on disk.
        phase_plan_context: Serialized cross-phase context string.

    Returns:
        Complete file content as a plain string (no markdown fences,
        trailing newline guaranteed by the CODER single-shot runner).

    Raises:
        BuilderError: If the builder pipeline fails (audit failure, stop event,
                      or sub-agent hard error).
    """
    result: BuilderResult = await run_builder(
        file_entry=file_entry,
        contracts=contracts,
        context=context,
        phase_deliverables=phase_deliverables,
        working_dir=working_dir,
        build_id=build_id,
        user_id=user_id,
        api_key=api_key,
        audit_api_key=audit_api_key,
        phase_plan_context=phase_plan_context,
    )

    if result.status == "failed":
        raise BuilderError(
            f"Builder failed for {file_entry.get('path', '?')}: {result.error}"
        )

    return result.content
