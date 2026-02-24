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
import re
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
# ANSI colour tags for console readability
# ---------------------------------------------------------------------------

_C_RESET = "\033[0m"
_C_BOLD = "\033[1m"
_C_DIM = "\033[2m"

# Role colours — chosen for contrast on dark terminals (PowerShell, cmd, etc.)
_C_BUILDER = "\033[97m"       # bright white  — orchestrator-level messages
_C_SCOUT = "\033[36m"         # cyan          — context mapping
_C_CODER = "\033[32m"         # green         — code generation
_C_AUDITOR = "\033[33m"       # yellow        — review/audit
_C_FIXER = "\033[35m"         # magenta       — fix application
_C_INTEG = "\033[34m"         # blue          — integration checks
_C_TOKENS = "\033[90m"        # grey          — token/cost info

_TAG_BUILDER = f"{_C_BOLD}{_C_BUILDER}[BUILDER]{_C_RESET}"
_TAG_SCOUT = f"{_C_BOLD}{_C_SCOUT}[SCOUT]{_C_RESET}"
_TAG_CODER = f"{_C_BOLD}{_C_CODER}[CODER]{_C_RESET}"
_TAG_AUDITOR = f"{_C_BOLD}{_C_AUDITOR}[AUDITOR]{_C_RESET}"
_TAG_FIXER = f"{_C_BOLD}{_C_FIXER}[FIXER]{_C_RESET}"
_TAG_INTEG = f"{_C_BOLD}{_C_INTEG}[INTEG]{_C_RESET}"

# Regex to strip ANSI escape codes for file logging
_ANSI_RE = re.compile(r"\033\[[0-9;]*m")


def _blog(msg: str) -> None:
    """Print to console AND log to file (ANSI-stripped).

    All builder pipeline output goes through this so it appears in both
    the terminal (colored) and the persistent log file (plain text).
    """
    print(msg)
    # Also emit through the logging system so the file handler picks it up
    logger.info(_ANSI_RE.sub("", msg))

# ---------------------------------------------------------------------------
# Trivial file detection — files that can be generated deterministically
# ---------------------------------------------------------------------------

_TRIVIAL_NAMES = frozenset({
    "__init__.py", "py.typed", ".gitkeep",
})

# Keywords in purpose that indicate the file needs real content (re-exports).
# NOTE: "import" and "export" alone are too broad — they match generic descriptions
# like "Package initialization — handles imports".  Only match specific barrel/re-export
# patterns that genuinely require LLM-generated content.
_NONTRIVIAL_KEYWORDS = ("re-export", "barrel", "public api", "public interface")


def _is_trivial_file(file_path: str, purpose: str) -> bool:
    """True if file can be generated deterministically without any LLM call."""
    name = Path(file_path).name
    if name not in _TRIVIAL_NAMES:
        return False
    purpose_lower = purpose.lower()
    return not any(kw in purpose_lower for kw in _NONTRIVIAL_KEYWORDS)


def _is_init_with_exports(file_path: str, purpose: str) -> bool:
    """True if file is __init__.py that needs re-exports (minimal CODER, no Scout/Auditor)."""
    name = Path(file_path).name
    if name != "__init__.py":
        return False
    purpose_lower = purpose.lower()
    return any(kw in purpose_lower for kw in _NONTRIVIAL_KEYWORDS)


# Config/static files that don't need SCOUT scanning or LLM auditing
_CONFIG_NAMES = frozenset({
    "tsconfig.json", "tsconfig.node.json", "tsconfig.app.json",
    "package.json", "package-lock.json",
    ".eslintrc.json", ".eslintrc.js", ".eslintrc.cjs",
    ".prettierrc", ".prettierrc.json", ".prettierignore",
    "vite.config.ts", "vite.config.js", "vite.config.mts",
    "tailwind.config.js", "tailwind.config.ts",
    "postcss.config.js", "postcss.config.cjs",
    "jest.config.ts", "jest.config.js",
    "vitest.config.ts", "vitest.config.js",
    ".gitignore", ".dockerignore", ".env.example",
    "Dockerfile", "docker-compose.yml", "docker-compose.yaml",
    "pyproject.toml", "setup.cfg", "setup.py",
    "requirements.txt", "requirements-dev.txt",
    "Makefile", "Procfile",
    "index.html",
})


def _is_config_file(file_path: str) -> bool:
    """True if file is a config/static file that doesn't need SCOUT or LLM audit."""
    return Path(file_path).name in _CONFIG_NAMES


# ---------------------------------------------------------------------------
# Tier-level scout synthesis (deterministic, 0 tokens)
# ---------------------------------------------------------------------------


def _synthesize_file_scout(
    tier_context: dict,
    file_entry: dict,
) -> SubAgentResult:
    """Create a synthetic SubAgentResult from tier-level scout context.

    Narrows the shared tier_scout_context to be file-aware by prepending
    a file-specific recommendation. All other fields pass through unchanged —
    they are workspace-level facts useful to every file.

    Parameters
    ----------
    tier_context:
        Output of build_tier_scout_context() — shared across all files in a tier.
    file_entry:
        Dict with 'path', 'purpose', etc. for the specific file being built.

    Returns
    -------
    SubAgentResult
        A completed result with structured_output matching the LLM scout schema.
        input_tokens=0, output_tokens=0, cost_usd=0.0.
    """
    file_path = file_entry.get("path", "unknown")
    file_purpose = file_entry.get("purpose", "")

    # Personalize recommendations with file-specific context
    _file_rec = f"Building {file_path}: {file_purpose}."
    _tier_rec = tier_context.get("recommendations", "")
    combined_rec = f"{_file_rec} {_tier_rec}"[:400]

    filtered = {
        "directory_tree": tier_context.get("directory_tree", "empty"),
        "key_interfaces": tier_context.get("key_interfaces", []),
        "patterns": tier_context.get("patterns", {}),
        "imports_map": tier_context.get("imports_map", {}),
        "directives": tier_context.get("directives", []),
        "recommendations": combined_rec,
    }

    return SubAgentResult(
        handoff_id=f"tier_scout_{Path(file_path).stem}_{id(tier_context) & 0xFFFF:04x}",
        role=SubAgentRole.SCOUT,
        status=HandoffStatus.COMPLETED,
        structured_output=filtered,
        text_output="",
        input_tokens=0,
        output_tokens=0,
        cache_read_tokens=0,
        cache_creation_tokens=0,
        cost_usd=0.0,
    )


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
    fixed_findings: str = ""                    # Summary of issues FIXER resolved (for lessons learned)


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
    total["cache_read_tokens"] = total.get("cache_read_tokens", 0) + result.cache_read_tokens
    total["cache_creation_tokens"] = total.get("cache_creation_tokens", 0) + result.cache_creation_tokens


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
    cache_read = u.get("cache_read_tokens", 0)
    cache_create = u.get("cache_creation_tokens", 0)
    fresh = inp - cache_read - cache_create

    _status_color = _C_CODER if result.status == "completed" else "\033[31m"  # green / red
    _blog(f"\n{_TAG_BUILDER} ════════════════════════════════════")
    _blog(f"{_TAG_BUILDER} FILE {_status_color}{_C_BOLD}{'COMPLETE' if result.status == 'completed' else 'FAILED'}{_C_RESET}")
    _blog(f"{_TAG_BUILDER}   File:       {result.file_path}")
    _blog(f"{_TAG_BUILDER}   Status:     {_status_color}{result.status}{_C_RESET}")
    if result.error:
        _blog(f"{_TAG_BUILDER}   Error:      \033[31m{result.error}{_C_RESET}")
    _blog(f"{_TAG_BUILDER}   Iterations: {result.iterations}  (sub-agent calls)")
    _blog(f"{_C_TOKENS}[TOKENS]   Input:      {inp:>10,}  (fresh: {fresh:,} | cached: {cache_read:,} | cache-create: {cache_create:,}){_C_RESET}")
    _blog(f"{_C_TOKENS}[TOKENS]   Output:     {out:>10,}{_C_RESET}")
    _blog(f"{_TAG_BUILDER} ════════════════════════════════════")


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
    build_mode: str = "full",
    verbose: bool = True,
    turn_callback: "callable | None" = None,
    stop_event: "threading.Event | None" = None,
    integration_check: "callable | None" = None,
    lessons_learned: str = "",
    phase_index: int = -1,
    tier_scout_context: dict | None = None,
    project_id: str = "",
    context_files: dict[str, str] | None = None,
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

    _project_uuid: UUID | None = None
    if project_id:
        try:
            _project_uuid = UUID(project_id)
        except ValueError:
            pass  # non-UUID project_id — tools will get empty string

    # Shared state across the pipeline
    total_usage: dict[str, int] = {"input_tokens": 0, "output_tokens": 0}
    sub_agent_results: list[SubAgentResult] = []
    deliverables_text = _phase_deliverables_text(phase_deliverables)

    if verbose:
        _blog(f"\n{_TAG_BUILDER} ── Building: {_C_BOLD}{file_path}{_C_RESET} ──")

    # ────────────────────────────────────────────────────────────────────────
    # (0) PRE-CHECK STOP SIGNAL
    # ────────────────────────────────────────────────────────────────────────
    if stop_event is not None and stop_event.is_set():
        raise BuilderError(f"Builder interrupted by stop signal before starting {file_path}")

    file_purpose = file_entry.get("purpose", "")

    # ────────────────────────────────────────────────────────────────────────
    # (0a) TRIVIAL FILE FAST PATH — deterministic generation, zero LLM calls
    # ────────────────────────────────────────────────────────────────────────
    if _is_trivial_file(file_path, file_purpose):
        if verbose:
            _blog(f"{_TAG_BUILDER} {_C_DIM}TRIVIAL: {file_path} — deterministic generation (0 tokens){_C_RESET}")
        file_abs = working_dir / file_path
        file_abs.parent.mkdir(parents=True, exist_ok=True)
        file_abs.write_text("", encoding="utf-8")
        result = BuilderResult(
            file_path=file_path,
            content="",
            status="completed",
            token_usage={"input_tokens": 0, "output_tokens": 0},
            sub_agent_results=[],
            iterations=0,
        )
        if verbose:
            _print_summary(result)
        return result

    # Determine whether to skip LLM Scout
    _has_tier_scout = tier_scout_context is not None
    _skip_scout = (
        phase_index == 0                                    # Phase 0: empty workspace, nothing to scan
        or _is_init_with_exports(file_path, file_purpose)   # __init__.py with re-exports
        or _is_config_file(file_path)                       # config/static files don't need scanning
        or _has_tier_scout                                   # tier-level deterministic scout replaces per-file LLM scout
    )

    # ────────────────────────────────────────────────────────────────────────
    # (1) SCOUT — gather context before coding begins (skipped for Phase 0
    #     and trivial __init__.py files — nothing to scan)
    # ────────────────────────────────────────────────────────────────────────
    scout_result: SubAgentResult | None = None

    if _skip_scout:
        if _has_tier_scout:
            # Tier-level deterministic scout — synthesize per-file result (0 tokens)
            scout_result = _synthesize_file_scout(tier_scout_context, file_entry)
            if verbose:
                _blog(f"{_TAG_SCOUT}   [1/3+] TIER-SCOUT (deterministic, 0 tokens) for {file_path}")
            if turn_callback is not None:
                turn_callback({"role": "scout", "status": "tier-scout", "tokens": 0})
        else:
            if verbose:
                if phase_index == 0:
                    _reason = "Phase 0 (empty workspace)"
                elif _is_config_file(file_path):
                    _reason = "config/static file"
                else:
                    _reason = "init-with-exports"
                _blog(f"{_TAG_SCOUT}   [1/3+] SKIPPED ({_reason}) for {file_path}")
            if turn_callback is not None:
                turn_callback({"role": "scout", "status": "skipped", "tokens": 0})
    else:
        if verbose:
            _blog(f"{_TAG_SCOUT}   [1/3+] Mapping context for {file_path}")

        scout_context: dict[str, str] = {}
        if lessons_learned:
            scout_context["lessons_learned.md"] = lessons_learned

        scout_handoff = SubAgentHandoff(
            role=SubAgentRole.SCOUT,
            build_id=_build_uuid,
            user_id=_user_uuid,
            assignment=(
                f"Map context for {file_path}. "
                f"Purpose: {file_entry.get('purpose', 'unknown')}. "
                "Read the project structure and identify patterns relevant to writing this file."
            ),
            project_id=_project_uuid,
            files=[file_path],
            context_files=scout_context,
            contracts_text="",
            phase_deliverables=deliverables_text,
            build_mode=build_mode,
        )

        scout_result = await run_sub_agent(
            scout_handoff,
            str(working_dir),
            api_key,
            stop_event=stop_event,
        )
        sub_agent_results.append(scout_result)
        _accumulate_tokens(total_usage, scout_result)

        if verbose:
            status_str = scout_result.status.value if hasattr(scout_result.status, "value") else str(scout_result.status)
            _blog(f"{_TAG_SCOUT}   Done: {status_str} {_C_TOKENS}({scout_result.input_tokens + scout_result.output_tokens} tokens){_C_RESET}")

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
        _blog(f"{_TAG_CODER}   [2/3+] Generating {file_path}")

    # Merge scout's structured output + provided context into coder's context_files.
    # Labeled context_files dict preserves contract names (contract_stack.md etc.)
    # so agents can identify pre-loaded contracts and skip redundant tool fetches.
    coder_context: dict[str, str] = {}
    if scout_result is not None and scout_result.structured_output:
        coder_context["scout_analysis.json"] = json.dumps(
            scout_result.structured_output, indent=2
        )
    if context_files:
        coder_context.update(context_files)
    elif context:
        for i, ctx_content in enumerate(context):
            coder_context[f"context_{i + 1}.txt"] = ctx_content
    if lessons_learned:
        coder_context["lessons_learned.md"] = lessons_learned

    coder_handoff = SubAgentHandoff(
        role=SubAgentRole.CODER,
        build_id=_build_uuid,
        user_id=_user_uuid,
        assignment=(
            f"Write {file_path}: {file_entry.get('purpose', 'see deliverables')}. "
            f"Estimated lines: {file_entry.get('estimated_lines', 'unknown')}. "
            f"Language: {file_entry.get('language', 'unknown')}."
        ),
        project_id=_project_uuid,
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
        stop_event=stop_event,
    )
    sub_agent_results.append(coder_result)
    _accumulate_tokens(total_usage, coder_result)

    if verbose:
        status_str = coder_result.status.value if hasattr(coder_result.status, "value") else str(coder_result.status)
        files_written = coder_result.files_written
        _blog(f"{_TAG_CODER}   Done: {status_str} (wrote {files_written}) {_C_TOKENS}({coder_result.input_tokens + coder_result.output_tokens} tokens){_C_RESET}")

    if turn_callback is not None:
        turn_callback({
            "role": "coder",
            "status": coder_result.status.value if hasattr(coder_result.status, "value") else str(coder_result.status),
            "files_written": coder_result.files_written,
            "tokens": coder_result.input_tokens + coder_result.output_tokens,
        })

    # If CODER failed entirely (e.g., parse error), bail early — BUT for __init__.py
    # files, fall back to an empty file instead of failing.  In Phase 0 especially,
    # there is nothing to re-export yet so the CODER may return empty content which
    # the Anthropic client surfaces as "Empty response from Anthropic API".
    if coder_result.status == HandoffStatus.FAILED:
        if Path(file_path).name == "__init__.py":
            if verbose:
                _blog(f"{_TAG_CODER}   {_C_DIM}Empty/failed for {file_path} — fallback to empty __init__.py{_C_RESET}")
            file_abs = working_dir / file_path
            file_abs.parent.mkdir(parents=True, exist_ok=True)
            file_abs.write_text("", encoding="utf-8")
            return BuilderResult(
                file_path=file_path,
                content="",
                status="completed",
                token_usage=total_usage,
                sub_agent_results=sub_agent_results,
                iterations=len(sub_agent_results),
            )
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
    _fixer_ran = False
    _last_combined_findings = ""          # what FIXER was asked to fix

    # Auto-pass: test files and trivially small files skip the LLM auditor
    # (matches verification.py:599-620 background audit fast-path)
    from app.services.build.verification import _is_test_file
    _file_abs_for_audit = working_dir / file_path
    _file_text_for_audit = ""
    if _file_abs_for_audit.exists():
        try:
            _file_text_for_audit = _file_abs_for_audit.read_text(encoding="utf-8")
        except Exception:
            pass
    _audit_auto_pass = (
        len(_file_text_for_audit.strip()) < 50
        or _is_test_file(file_path)
        or _is_init_with_exports(file_path, file_purpose)
        or _is_config_file(file_path)
    )
    if _audit_auto_pass:
        _reason = (
            "test file" if _is_test_file(file_path)
            else "trivial content" if len(_file_text_for_audit.strip()) < 50
            else "config/static file" if _is_config_file(file_path)
            else "init-with-exports"
        )
        if verbose:
            _blog(f"{_TAG_AUDITOR}   Auto-pass ({_reason}) for {file_path}")
        last_audit_findings = f"auto-pass ({_reason})"
        if turn_callback is not None:
            turn_callback({"role": "auditor", "status": "auto-pass", "verdict": "PASS", "tokens": 0})
    else:
        # --- Full LLM audit loop ---
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

            # Pre-load contracts into auditor context to eliminate redundant
            # MCP/HTTP tool fetches (which 404 — see contract_fetching plan).
            if context_files:
                for k, v in context_files.items():
                    if k.startswith("contract_") and k not in auditor_context:
                        auditor_context[k] = v

            # --- AUDITOR ---
            if verbose:
                label = f"[{fix_attempt + 3}/3+]" if fix_attempt == 0 else f"[re-audit {fix_attempt}]"
                _blog(f"{_TAG_AUDITOR}   {label} Reviewing {file_path}")

            auditor_handoff = SubAgentHandoff(
                role=SubAgentRole.AUDITOR,
                build_id=_build_uuid,
                user_id=_user_uuid,
                assignment=f"Audit {file_path} for structural issues and contract compliance.",
                project_id=_project_uuid,
                files=[file_path],
                context_files=auditor_context,
                contracts_text="",
                phase_deliverables=deliverables_text,
                build_mode=build_mode,
            )

            auditor_result = await run_sub_agent(
                auditor_handoff,
                str(working_dir),
                audit_api_key or api_key,
                stop_event=stop_event,
            )
            sub_agent_results.append(auditor_result)
            _accumulate_tokens(total_usage, auditor_result)

            audit_verdict = auditor_result.structured_output.get("verdict", "")
            _audit_skipped = False

            if not audit_verdict:
                _text = auditor_result.text_output or ""
                _m = re.search(r'\bverdict["\s:]*\b(PASS|FAIL)\b', _text, re.IGNORECASE)
                if _m:
                    audit_verdict = _m.group(1).upper()
                    logger.info("Auditor verdict recovered from text: %s (file: %s)", audit_verdict, file_path)
                else:
                    audit_verdict = "PASS"
                    _audit_skipped = True
                    logger.warning(
                        "Auditor produced no parseable verdict for %s — audit skipped "
                        "(text_output length: %d chars)",
                        file_path, len(_text),
                    )

            last_audit_findings = _format_audit_findings(auditor_result.structured_output)
            if _audit_skipped:
                last_audit_findings = (
                    "⚠ AUDIT_SKIPPED: Auditor did not produce structured JSON verdict. "
                    "File was not formally reviewed.\n" + last_audit_findings
                )

            if verbose:
                _skip_tag = " [AUDIT_SKIPPED]" if _audit_skipped else ""
                status_str = auditor_result.status.value if hasattr(auditor_result.status, "value") else str(auditor_result.status)
                _v_color = _C_CODER if audit_verdict == "PASS" else "\033[31m"
                _blog(f"{_TAG_AUDITOR}   Done: {_v_color}{_C_BOLD}verdict={audit_verdict}{_C_RESET}{_skip_tag} {_C_TOKENS}({auditor_result.input_tokens + auditor_result.output_tokens} tokens){_C_RESET}")

            if turn_callback is not None:
                turn_callback({
                    "role": "auditor",
                    "status": auditor_result.status.value if hasattr(auditor_result.status, "value") else str(auditor_result.status),
                    "verdict": audit_verdict,
                    "tokens": auditor_result.input_tokens + auditor_result.output_tokens,
                })

            # --- INTEGRATION CHECK (cross-file validation) ---
            integration_findings = ""
            _integ_error_count = 0
            if integration_check is not None:
                if verbose:
                    _blog(f"{_TAG_INTEG}   Cross-file check for {file_path}")
                try:
                    _integ_issues = await integration_check(file_path)
                    _integ_errors = [i for i in _integ_issues if i.severity == "error"]
                    _integ_error_count = len(_integ_errors)
                    if _integ_errors:
                        integration_findings = "Integration audit findings:\n" + "\n".join(
                            f"  [{i.severity}] {i.message}"
                            + (f" (related: {i.related_file})" if i.related_file else "")
                            for i in _integ_errors
                        )
                        if verbose:
                            _blog(f"{_TAG_INTEG}   \033[31m{_integ_error_count} error(s) found{_C_RESET}")
                    elif verbose:
                        _blog(f"{_TAG_INTEG}   {_C_CODER}Passed{_C_RESET}")
                except Exception as _integ_exc:
                    logger.warning("Integration check failed for %s: %s", file_path, _integ_exc)

                if turn_callback is not None:
                    turn_callback({
                        "role": "integration",
                        "status": "failed" if integration_findings else "passed",
                        "error_count": _integ_error_count,
                        "findings": integration_findings[:500] if integration_findings else "",
                        "tokens": 0,
                    })

            # Combine auditor + integration findings for FIXER
            needs_fix = audit_verdict == "FAIL" or bool(integration_findings)
            combined_findings = last_audit_findings
            if integration_findings:
                combined_findings = (combined_findings + "\n\n" + integration_findings).strip()

            # PASS on both -> done
            if not needs_fix:
                break

            # FAIL + retries exhausted -> give up
            if fix_attempt >= MAX_FIX_RETRIES:
                if verbose:
                    _blog(f"{_TAG_FIXER}   \033[31mMax fix retries ({MAX_FIX_RETRIES}) reached for {file_path}{_C_RESET}")
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
            _fixer_ran = True
            _last_combined_findings = combined_findings

            if verbose:
                _fix_sources = []
                if audit_verdict == "FAIL":
                    _fix_sources.append("audit")
                if integration_findings:
                    _fix_sources.append("integration")
                _blog(f"{_TAG_FIXER}   Applying fixes from {'+'.join(_fix_sources)} (attempt {fix_attempt + 1}/{MAX_FIX_RETRIES})")

            fixer_handoff = SubAgentHandoff(
                role=SubAgentRole.FIXER,
                build_id=_build_uuid,
                user_id=_user_uuid,
                assignment=(
                    f"Fix {file_path}: apply surgical edits to resolve the findings below. "
                    "Use edit_file only — do NOT rewrite the entire file with write_file."
                ),
                project_id=_project_uuid,
                files=[file_path],
                context_files=auditor_context,
                contracts_text="",
                phase_deliverables=deliverables_text,
                error_context=combined_findings,
                build_mode=build_mode,
            )

            fixer_result = await run_sub_agent(
                fixer_handoff,
                str(working_dir),
                audit_api_key or api_key,
                stop_event=stop_event,
            )
            sub_agent_results.append(fixer_result)
            _accumulate_tokens(total_usage, fixer_result)

            if verbose:
                status_str = fixer_result.status.value if hasattr(fixer_result.status, "value") else str(fixer_result.status)
                _blog(f"{_TAG_FIXER}   Done: {status_str} {_C_TOKENS}({fixer_result.input_tokens + fixer_result.output_tokens} tokens){_C_RESET}")

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

    # Capture what FIXER resolved (only if it ran AND final status is good)
    _resolved_findings = ""
    if _fixer_ran and audit_verdict != "FAIL" and _last_combined_findings:
        _resolved_findings = _last_combined_findings

    if audit_verdict == "FAIL":
        result = BuilderResult(
            file_path=file_path,
            content=final_content,
            status="failed",
            error=f"Audit failed after {MAX_FIX_RETRIES} fix attempt(s): {last_audit_findings[:200]}",
            token_usage=total_usage,
            sub_agent_results=sub_agent_results,
            iterations=iterations,
            fixed_findings=_last_combined_findings if _fixer_ran else "",
        )
    else:
        result = BuilderResult(
            file_path=file_path,
            content=final_content,
            status="completed",
            token_usage=total_usage,
            sub_agent_results=sub_agent_results,
            iterations=iterations,
            fixed_findings=_resolved_findings,
        )

    if verbose:
        _print_summary(result)

    return result
