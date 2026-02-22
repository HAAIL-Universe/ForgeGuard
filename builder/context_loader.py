"""
context_loader.py — System prompt builder for the Builder Orchestrator.

Mirrors planner/context_loader.py in structure. The builder orchestrator
does NOT call the LLM directly — it delegates every LLM call to sub-agents
(Scout, Coder, Auditor, Fixer). This module exists for structural parity
with the planner and for future CLI introspection tooling.

Prompt caching strategy
-----------------------
Sub-agents handle their own prompt caching. The orchestrator's system prompt
describes what role it plays and is cached across calls for the same file type.
"""

from __future__ import annotations


BUILDER_ROLE = """\
You are the Forge Builder Orchestrator.

You do NOT write code yourself. You coordinate a pipeline of specialist
sub-agents that write, audit, and fix each file in a build plan:

  1. SCOUT   — reads the project to map context before coding begins
  2. CODER   — generates the file content (single-shot, no tool loop)
  3. AUDITOR — reviews the file for structural issues against contracts
  4. FIXER   — applies surgical edits if the auditor finds problems

Your output contract
--------------------
Return a BuilderResult with:
  - file_path: the relative path to the generated file
  - content:   the final file content (post-audit, post-fix)
  - status:    "completed" | "failed"
  - error:     error message if failed (empty string on success)
  - token_usage: {"input_tokens": int, "output_tokens": int}
  - sub_agent_results: list of SubAgentResult for the audit trail
  - iterations: number of sub-agent invocations

Pipeline termination
--------------------
  A. AUDITOR returns PASS  → return BuilderResult(status="completed")
  B. AUDITOR returns FAIL  → dispatch FIXER, re-run AUDITOR (max 2 retries)
  C. Max retries exceeded  → return BuilderResult(status="failed")
  D. stop_event.is_set()   → raise BuilderError("interrupted by stop signal")
"""


def build_system_prompt(
    file_entry: dict,
    contracts: list[str],
) -> list[dict]:
    """
    Build the orchestrator system prompt as cacheable blocks.

    Returns two blocks:
      [0] orchestrator role description (~300 tokens, not explicitly cached)
      [1] file metadata + contract summary (~variable, marked with cache_control)

    The cache boundary on [1] caches everything up to and including it.

    Args:
        file_entry: Dict with keys: path, purpose, estimated_lines, language.
        contracts:  List of contract strings loaded for this build session.

    Returns:
        list of Anthropic-API-compatible system content blocks.
    """
    file_meta = (
        f"## Target File\n"
        f"- path:            {file_entry.get('path', '?')}\n"
        f"- purpose:         {file_entry.get('purpose', '?')}\n"
        f"- language:        {file_entry.get('language', 'unknown')}\n"
        f"- estimated_lines: {file_entry.get('estimated_lines', '?')}\n"
    )
    contracts_summary = (
        f"## Loaded Contracts ({len(contracts)})\n"
        + "\n".join(f"- contract block {i+1}" for i in range(len(contracts)))
    )

    return [
        {
            "type": "text",
            "text": BUILDER_ROLE,
        },
        {
            "type": "text",
            "text": f"{file_meta}\n{contracts_summary}",
            "cache_control": {"type": "ephemeral"},
        },
    ]
