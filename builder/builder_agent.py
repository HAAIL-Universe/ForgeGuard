"""
builder_agent.py — Forge Builder Orchestrator.

Mirrors planner/planner_agent.py in structure and standalone usability.
The orchestrator does NOT call the LLM directly — it coordinates a pipeline
of specialist sub-agents for each file in the build plan:

  SCOUT → CODER → AUDITOR → INTEGRATION → FIXER (if needed) → BuilderResult

All agents use pull-first contract delivery: contracts are fetched on demand
via forge tools. Only the universal builder_contract.md is injected into
system prompts (for caching). Each sub-agent is dispatched via
run_sub_agent() from subagent.py, which handles all LLM calls, tool
execution, WS broadcasts, and DB logging.

Usage (programmatic):
    from builder.builder_agent import run_builder, BuilderResult, BuilderError

    result = await run_builder(
        file_entry={"path": "app/main.py", "purpose": "Entry point", ...},
        contracts=[],
        context=[],
        phase_deliverables=["FastAPI app with health endpoint"],
        working_dir="/workspace/my-project",
        build_id="<uuid>",
        user_id="<uuid>",
        api_key="sk-ant-...",
    )
    if result.status == "completed":
        print(result.content)

Pipeline termination:
  A. AUDITOR returns PASS  → BuilderResult(status="completed")
  B. AUDITOR returns FAIL  → dispatch FIXER, re-run AUDITOR (max 2 retries)
  C. Max retries exceeded  → BuilderResult(status="failed")
  D. stop_event.is_set()   → BuilderError("interrupted by stop signal")
  E. Sub-agent hard failure → BuilderResult(status="failed", error=...)
"""

from __future__ import annotations

import json
import logging
import sys
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import UUID

# Ensure the ForgeGuard package root is on sys.path so `app.*` is importable
# when this module is imported directly (e.g. via CLI or tests).
_THIS_DIR = Path(__file__).parent
_REPO_ROOT = _THIS_DIR.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from app.services.build.subagent import (  # noqa: E402
    HandoffStatus,
    SubAgentHandoff,
    SubAgentResult,
    SubAgentRole,
    run_sub_agent,
)

logger = logging.getLogger(__name__)

# Maximum number of FIXER + re-AUDIT cycles per file.
MAX_FIX_RETRIES = 2


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


@dataclass
class BuilderResult:
    """Output of a single run_builder() invocation."""

    file_path: str
    content: str
    status: str                                 # "completed" | "failed"
    error: str = ""
    token_usage: dict = field(default_factory=dict)
    sub_agent_results: list = field(default_factory=list)
    iterations: int = 0


class BuilderError(Exception):
    """Raised when the builder pipeline encounters an unrecoverable error."""
    pass


# ---------------------------------------------------------------------------
# Token accounting helpers
# ---------------------------------------------------------------------------


def _accumulate_tokens(
    total: dict[str, int],
    result: SubAgentResult,
) -> None:
    """Add a sub-agent result's token counts into *total* in-place."""
    total["input_tokens"] = total.get("input_tokens", 0) + result.input_tokens
    total["output_tokens"] = total.get("output_tokens", 0) + result.output_tokens


# ---------------------------------------------------------------------------
# Context assembly helpers
# ---------------------------------------------------------------------------



def _phase_deliverables_text(phase_deliverables: list[str]) -> str:
    """Join deliverable strings into a bulleted block for handoffs."""
    return "\n".join(f"- {d}" for d in phase_deliverables) if phase_deliverables else ""


def _format_audit_findings(structured_output: dict) -> str:
    """Format AUDITOR structured_output into a readable error_context string."""
    findings = structured_output.get("findings", [])
    if not findings:
        return structured_output.get("verdict", "FAIL")

    lines: list[str] = []
    for f in findings:
        line = f.get("line", "?")
        sev = f.get("severity", "error")
        msg = f.get("message", "unknown issue")
        lines.append(f"  Line {line} [{sev}]: {msg}")
    return "Audit findings:\n" + "\n".join(lines)


# ---------------------------------------------------------------------------
# Summary printer (matches planner's _print_summary format)
# ---------------------------------------------------------------------------


def _print_summary(result: BuilderResult) -> None:
    u = result.token_usage
    inp = u.get("input_tokens", 0)
    out = u.get("output_tokens", 0)

    print(f"\n[BUILDER] ════════════════════════════════════")
    print(f"[BUILDER] FILE {'COMPLETE' if result.status == 'completed' else 'FAILED'}")
    print(f"[BUILDER]   File:       {result.file_path}")
    print(f"[BUILDER]   Status:     {result.status}")
    if result.error:
        print(f"[BUILDER]   Error:      {result.error}")
    print(f"[BUILDER]   Iterations: {result.iterations}  (sub-agent calls)")
    print(f"[BUILDER] TOKEN USAGE:")
    print(f"[BUILDER]   Input:      {inp:>10,}")
    print(f"[BUILDER]   Output:     {out:>10,}")
    print(f"[BUILDER] ════════════════════════════════════")


# ---------------------------------------------------------------------------
# Main orchestrator function
# ---------------------------------------------------------------------------


async def run_builder(
    file_entry: dict,
    contracts: list[str],
    context: list[str],
    phase_deliverables: list[str],
    working_dir: str | Path,
    build_id: str,
    user_id: str,
    api_key: str,
    audit_api_key: "str | None" = None,
    phase_plan_context: str = "",
    build_mode: str = "full",
    verbose: bool = True,
    turn_callback: "callable | None" = None,
    stop_event: "threading.Event | None" = None,
    integration_check: "callable | None" = None,
) -> BuilderResult:
    """
    Run the Builder Agent pipeline for a single file.

    Dispatches SCOUT → CODER → AUDITOR → INTEGRATION → FIXER (if needed)
    and returns a BuilderResult containing the final file content and audit trail.

    Args:
        file_entry:         Dict with keys: path, purpose, estimated_lines, language.
        contracts:          Contract strings (governance rules) for this build.
        context:            Relevant existing file contents for the coder.
        phase_deliverables: Phase objective + acceptance criteria strings.
        working_dir:        Absolute path to the build workspace on disk.
        build_id:           Active build UUID (string).
        user_id:            Authenticated user UUID (string).
        api_key:            Anthropic API key for LLM calls.
        phase_plan_context: Serialized cross-phase context string.
        verbose:            If True, prints progress to stdout.
        turn_callback:      Optional callable(dict) fired after each sub-agent.
        stop_event:         If set, the pipeline is interrupted cleanly.

    Returns:
        BuilderResult with status "completed" or "failed".

    Raises:
        BuilderError: If stop_event is set before the first sub-agent dispatches.
    """
    working_dir = Path(working_dir)
    file_path = file_entry.get("path", "unknown")

    try:
        _build_uuid = UUID(build_id)
        _user_uuid = UUID(user_id)
    except ValueError as e:
        raise BuilderError(f"Invalid build_id or user_id UUID: {e}") from e

    # Shared state across the pipeline
    total_usage: dict[str, int] = {"input_tokens": 0, "output_tokens": 0}
    sub_agent_results: list[SubAgentResult] = []
    deliverables_text = _phase_deliverables_text(phase_deliverables)

    if verbose:
        print(f"\n[BUILDER] ── Building: {file_path} ──")

    # ────────────────────────────────────────────────────────────────────────
    # (0) PRE-CHECK STOP SIGNAL
    # ────────────────────────────────────────────────────────────────────────
    if stop_event is not None and stop_event.is_set():
        raise BuilderError(f"Builder interrupted by stop signal before starting {file_path}")

    # ────────────────────────────────────────────────────────────────────────
    # (1) SCOUT — gather context before coding begins
    # ────────────────────────────────────────────────────────────────────────
    if verbose:
        print(f"[BUILDER]   [1/3+] SCOUT: mapping context for {file_path}")

    scout_context: dict[str, str] = {}
    if phase_plan_context:
        scout_context["phase_plan_context.md"] = phase_plan_context

    scout_handoff = SubAgentHandoff(
        role=SubAgentRole.SCOUT,
        build_id=_build_uuid,
        user_id=_user_uuid,
        assignment=(
            f"Map context for {file_path}. "
            f"Purpose: {file_entry.get('purpose', 'unknown')}. "
            "Read the project structure and identify patterns relevant to writing this file."
        ),
        files=[file_path],
        context_files=scout_context,
        contracts_text="",          # Scout fetches contracts via its own tools
        phase_deliverables=deliverables_text,
        build_mode=build_mode,
    )

    scout_result = await run_sub_agent(
        scout_handoff,
        str(working_dir),
        api_key,
    )
    sub_agent_results.append(scout_result)
    _accumulate_tokens(total_usage, scout_result)

    if verbose:
        status_str = scout_result.status.value if hasattr(scout_result.status, "value") else str(scout_result.status)
        print(f"[BUILDER]   SCOUT done: {status_str} ({scout_result.input_tokens + scout_result.output_tokens} tokens)")

    if turn_callback is not None:
        turn_callback({
            "role": "scout",
            "status": scout_result.status.value if hasattr(scout_result.status, "value") else str(scout_result.status),
            "tokens": scout_result.input_tokens + scout_result.output_tokens,
        })

    # ────────────────────────────────────────────────────────────────────────
    # (2) CHECK STOP SIGNAL
    # ────────────────────────────────────────────────────────────────────────
    if stop_event is not None and stop_event.is_set():
        return BuilderResult(
            file_path=file_path,
            content="",
            status="failed",
            error="Builder interrupted by stop signal after SCOUT",
            token_usage=total_usage,
            sub_agent_results=sub_agent_results,
            iterations=len(sub_agent_results),
        )

    # ────────────────────────────────────────────────────────────────────────
    # (3) CODER — generate the file (single-shot mode)
    # ────────────────────────────────────────────────────────────────────────
    if verbose:
        print(f"[BUILDER]   [2/3+] CODER: generating {file_path}")

    # Merge scout's structured output + provided context into coder's context_files
    coder_context: dict[str, str] = {}
    if scout_result.structured_output:
        coder_context["scout_analysis.json"] = json.dumps(
            scout_result.structured_output, indent=2
        )
    for i, ctx_content in enumerate(context):
        coder_context[f"context_{i + 1}.txt"] = ctx_content

    coder_handoff = SubAgentHandoff(
        role=SubAgentRole.CODER,
        build_id=_build_uuid,
        user_id=_user_uuid,
        assignment=(
            f"Write {file_path}: {file_entry.get('purpose', 'see deliverables')}. "
            f"Estimated lines: {file_entry.get('estimated_lines', 'unknown')}. "
            f"Language: {file_entry.get('language', 'unknown')}."
        ),
        files=[file_path],
        context_files=coder_context,
        contracts_text="",               # pull-first: Coder fetches via tools
        phase_deliverables=deliverables_text,
        build_mode=build_mode,
    )

    coder_result = await run_sub_agent(
        coder_handoff,
        str(working_dir),
        api_key,
    )
    sub_agent_results.append(coder_result)
    _accumulate_tokens(total_usage, coder_result)

    if verbose:
        status_str = coder_result.status.value if hasattr(coder_result.status, "value") else str(coder_result.status)
        files_written = coder_result.files_written
        print(f"[BUILDER]   CODER done: {status_str} (wrote {files_written}, {coder_result.input_tokens + coder_result.output_tokens} tokens)")

    if turn_callback is not None:
        turn_callback({
            "role": "coder",
            "status": coder_result.status.value if hasattr(coder_result.status, "value") else str(coder_result.status),
            "files_written": coder_result.files_written,
            "tokens": coder_result.input_tokens + coder_result.output_tokens,
        })

    # If CODER failed entirely (e.g., parse error), bail early
    if coder_result.status == HandoffStatus.FAILED:
        return BuilderResult(
            file_path=file_path,
            content="",
            status="failed",
            error=f"CODER failed: {coder_result.error}",
            token_usage=total_usage,
            sub_agent_results=sub_agent_results,
            iterations=len(sub_agent_results),
        )

    # ────────────────────────────────────────────────────────────────────────
    # (4) AUDITOR → FIXER loop (max MAX_FIX_RETRIES fix attempts)
    # ────────────────────────────────────────────────────────────────────────
    audit_verdict = "PASS"
    last_audit_findings = ""

    for fix_attempt in range(MAX_FIX_RETRIES + 1):
        # Check stop signal before each audit/fix cycle
        if stop_event is not None and stop_event.is_set():
            return BuilderResult(
                file_path=file_path,
                content="",
                status="failed",
                error=f"Builder interrupted by stop signal (fix_attempt={fix_attempt})",
                token_usage=total_usage,
                sub_agent_results=sub_agent_results,
                iterations=len(sub_agent_results),
            )

        # Read the file from disk for auditor context
        file_abs = working_dir / file_path
        auditor_context: dict[str, str] = {}
        if file_abs.exists():
            try:
                auditor_context[file_path] = file_abs.read_text(encoding="utf-8")
            except Exception as e:
                logger.warning("Could not read %s for auditor: %s", file_abs, e)

        # --- AUDITOR ---
        if verbose:
            label = f"[{fix_attempt + 3}/3+]" if fix_attempt == 0 else f"[re-audit {fix_attempt}]"
            print(f"[BUILDER]   {label} AUDITOR: reviewing {file_path}")

        auditor_handoff = SubAgentHandoff(
            role=SubAgentRole.AUDITOR,
            build_id=_build_uuid,
            user_id=_user_uuid,
            assignment=f"Audit {file_path} for structural issues and contract compliance.",
            files=[file_path],
            context_files=auditor_context,
            contracts_text="",               # pull-first: Auditor fetches via tools
            phase_deliverables=deliverables_text,
            build_mode=build_mode,
        )

        auditor_result = await run_sub_agent(
            auditor_handoff,
            str(working_dir),
            audit_api_key or api_key,
        )
        sub_agent_results.append(auditor_result)
        _accumulate_tokens(total_usage, auditor_result)

        audit_verdict = auditor_result.structured_output.get("verdict", "PASS")
        last_audit_findings = _format_audit_findings(auditor_result.structured_output)

        if verbose:
            status_str = auditor_result.status.value if hasattr(auditor_result.status, "value") else str(auditor_result.status)
            print(f"[BUILDER]   AUDITOR done: verdict={audit_verdict} ({auditor_result.input_tokens + auditor_result.output_tokens} tokens)")

        if turn_callback is not None:
            turn_callback({
                "role": "auditor",
                "status": auditor_result.status.value if hasattr(auditor_result.status, "value") else str(auditor_result.status),
                "verdict": audit_verdict,
                "tokens": auditor_result.input_tokens + auditor_result.output_tokens,
            })

        # --- INTEGRATION CHECK (cross-file validation) ---
        integration_findings = ""
        if integration_check is not None:
            if verbose:
                print(f"[BUILDER]   INTEGRATION: cross-file check for {file_path}")
            try:
                _integ_issues = await integration_check(file_path)
                _integ_errors = [i for i in _integ_issues if i.severity == "error"]
                if _integ_errors:
                    integration_findings = "Integration audit findings:\n" + "\n".join(
                        f"  [{i.severity}] {i.message}"
                        + (f" (related: {i.related_file})" if i.related_file else "")
                        for i in _integ_errors
                    )
                    if verbose:
                        print(f"[BUILDER]   INTEGRATION: {len(_integ_errors)} error(s) found")
                elif verbose:
                    print(f"[BUILDER]   INTEGRATION: passed")
            except Exception as _integ_exc:
                logger.warning("Integration check failed for %s: %s", file_path, _integ_exc)

        if turn_callback is not None and integration_findings:
            turn_callback({
                "role": "integration",
                "status": "failed",
                "tokens": 0,
            })

        # Combine auditor + integration findings for FIXER
        needs_fix = audit_verdict == "FAIL" or bool(integration_findings)
        combined_findings = last_audit_findings
        if integration_findings:
            combined_findings = (combined_findings + "\n\n" + integration_findings).strip()

        # PASS on both → done
        if not needs_fix:
            break

        # FAIL + retries exhausted → give up
        if fix_attempt >= MAX_FIX_RETRIES:
            if verbose:
                print(f"[BUILDER]   Max fix retries ({MAX_FIX_RETRIES}) reached for {file_path}")
            break

        # Check stop signal before dispatching fixer
        if stop_event is not None and stop_event.is_set():
            return BuilderResult(
                file_path=file_path,
                content="",
                status="failed",
                error="Builder interrupted by stop signal before FIXER",
                token_usage=total_usage,
                sub_agent_results=sub_agent_results,
                iterations=len(sub_agent_results),
            )

        # --- FIXER (receives combined audit + integration findings) ---
        if verbose:
            _fix_sources = []
            if audit_verdict == "FAIL":
                _fix_sources.append("audit")
            if integration_findings:
                _fix_sources.append("integration")
            print(f"[BUILDER]   FIXER: applying fixes from {'+'.join(_fix_sources)} (attempt {fix_attempt + 1}/{MAX_FIX_RETRIES})")

        fixer_handoff = SubAgentHandoff(
            role=SubAgentRole.FIXER,
            build_id=_build_uuid,
            user_id=_user_uuid,
            assignment=(
                f"Fix {file_path}: apply surgical edits to resolve the findings below. "
                "Use edit_file only — do NOT rewrite the entire file with write_file."
            ),
            files=[file_path],
            context_files=auditor_context,
            contracts_text="",               # pull-first: Fixer uses forge_get_build_contracts
            phase_deliverables=deliverables_text,
            error_context=combined_findings,
            build_mode=build_mode,
        )

        fixer_result = await run_sub_agent(
            fixer_handoff,
            str(working_dir),
            audit_api_key or api_key,
        )
        sub_agent_results.append(fixer_result)
        _accumulate_tokens(total_usage, fixer_result)

        if verbose:
            status_str = fixer_result.status.value if hasattr(fixer_result.status, "value") else str(fixer_result.status)
            print(f"[BUILDER]   FIXER done: {status_str} ({fixer_result.input_tokens + fixer_result.output_tokens} tokens)")

        if turn_callback is not None:
            turn_callback({
                "role": "fixer",
                "status": fixer_result.status.value if hasattr(fixer_result.status, "value") else str(fixer_result.status),
                "tokens": fixer_result.input_tokens + fixer_result.output_tokens,
            })

    # ────────────────────────────────────────────────────────────────────────
    # (5) READ FINAL FILE CONTENT FROM DISK
    # ────────────────────────────────────────────────────────────────────────
    file_abs = working_dir / file_path
    final_content = ""
    if file_abs.exists():
        try:
            final_content = file_abs.read_text(encoding="utf-8")
        except Exception as e:
            logger.error("Could not read final output %s: %s", file_abs, e)

    # ────────────────────────────────────────────────────────────────────────
    # (6) BUILD RESULT
    # ────────────────────────────────────────────────────────────────────────
    iterations = len(sub_agent_results)

    if audit_verdict == "FAIL":
        result = BuilderResult(
            file_path=file_path,
            content=final_content,
            status="failed",
            error=f"Audit failed after {MAX_FIX_RETRIES} fix attempt(s): {last_audit_findings[:200]}",
            token_usage=total_usage,
            sub_agent_results=sub_agent_results,
            iterations=iterations,
        )
    else:
        result = BuilderResult(
            file_path=file_path,
            content=final_content,
            status="completed",
            token_usage=total_usage,
            sub_agent_results=sub_agent_results,
            iterations=iterations,
        )

    if verbose:
        _print_summary(result)

    return result
