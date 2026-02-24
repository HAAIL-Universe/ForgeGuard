"""Typed pipeline state for agent intercommunication.

Inspired by LangGraph's Annotated[type, reducer] pattern.
Each field has either OVERWRITE (default) or APPEND semantics.
Agents receive only the keys declared in their role's read scope.

Three layers:
  - FilePipelineState  — per-file SCOUT→CODER→AUDITOR→FIXER pipeline
  - TierState          — shared across all files in a build tier
  - LessonsState       — structured cross-phase memory (replaces 2KB string)

Also provides:
  - PipelineStateManager  — manages typed state with reducer semantics
  - HandoffData + filters — composable context filters at agent boundaries
  - _state_to_context_files() — bridges typed state → SubAgentHandoff.context_files
  - _extract_exports()  — parses public exports from Python source
  - _extract_lessons_from_result() — structured lesson extraction
  - _lessons_to_context() — serializes LessonsState for agent consumption
"""

from __future__ import annotations

import copy
import json
import re
from dataclasses import dataclass, replace as dc_replace
from typing import Annotated, Any, Callable, get_type_hints, get_origin, get_args


# ---------------------------------------------------------------------------
# Reducer functions
# ---------------------------------------------------------------------------

def _append_list(old: list, new: list) -> list:
    """Append reducer — accumulates items across agents."""
    return old + new


def _merge_dict(old: dict, new: dict) -> dict:
    """Shallow merge — new keys overwrite, old keys preserved."""
    merged = dict(old)
    merged.update(new)
    return merged


def _append_capped(cap: int):
    """Factory for capped append — keeps last ``cap`` items."""
    def reducer(old: list, new: list) -> list:
        combined = old + new
        return combined[-cap:]
    reducer.__qualname__ = f"_append_capped({cap})"
    return reducer


# ---------------------------------------------------------------------------
# Pipeline State — typed shared state for one file build
# ---------------------------------------------------------------------------

class FilePipelineState:
    """State for a single file's SCOUT → CODER → AUDITOR → FIXER pipeline.

    Fields with Annotated[type, reducer] use append/merge semantics.
    Fields without annotation use overwrite semantics (last writer wins).

    NOTE: We use a plain class with __annotations__ rather than TypedDict
    because Python's TypedDict + Annotated introspection is fragile across
    versions. The PipelineStateManager inspects annotations directly.
    """

    # ── Immutable context (set once at pipeline entry, read by all) ────────
    file_path: str
    file_entry: dict
    build_id: str
    project_id: str
    phase_index: int
    contracts: dict
    phase_deliverables: str

    # ── Scout output (written by SCOUT, read by CODER) ────────────────────
    scout_analysis: dict
    scout_directives: list
    scout_interfaces: list
    scout_patterns: dict
    scout_imports_map: dict

    # ── Coder output (written by CODER, read by AUDITOR + FIXER) ──────────
    generated_code: str
    coder_decisions: str
    coder_known_issues: str
    files_written: list

    # ── Audit results (written by AUDITOR, read by FIXER) ─────────────────
    audit_verdict: str
    audit_findings: Annotated[list, _append_list]
    audit_cycle: int

    # ── Fixer output (written by FIXER, read by AUDITOR on re-audit) ──────
    fixed_code: str
    fixes_applied: Annotated[list, _append_list]

    # ── Integration check results ─────────────────────────────────────────
    integration_findings: Annotated[list, _append_list]

    # ── Cross-file context (from prior files/tiers) ───────────────────────
    prior_file_summaries: Annotated[list, _append_list]

    # ── Artifact trail (tracks every file operation) ──────────────────────
    artifact_trail: Annotated[dict, _merge_dict]

    # ── Error history ─────────────────────────────────────────────────────
    errors: Annotated[list, _append_capped(20)]


# ---------------------------------------------------------------------------
# Tier-level state — shared across all files in a tier
# ---------------------------------------------------------------------------

class TierState:
    """State shared across all files in a build tier."""

    tier_index: int
    phase_index: int
    build_id: str

    # File summaries (append as files complete)
    completed_files: Annotated[list, _append_list]

    # Structured lessons (replaces free-text lessons_learned)
    lessons: dict  # LessonsState dict — managed by TierStateManager

    # Tier-level scout context
    tier_scout: dict

    # Contracts cache
    contracts: dict


# ---------------------------------------------------------------------------
# Structured lessons (replaces free-text lessons_learned)
# ---------------------------------------------------------------------------

class LessonsState:
    """Structured cross-phase memory. Replaces the 2KB lessons_learned string.

    Each section accumulates independently with caps.
    """

    # Patterns confirmed by audit pass — "use this, it works"
    confirmed_patterns: Annotated[list, _append_capped(15)]

    # Fixes applied by fixer — "don't do this, it fails audit"
    fix_patterns: Annotated[list, _append_capped(15)]

    # Import conventions discovered — "import X from Y"
    import_conventions: Annotated[dict, _merge_dict]

    # Errors encountered and resolutions
    error_resolutions: Annotated[list, _append_capped(10)]

    # Architecture decisions — "chose X because Y"
    decisions: Annotated[list, _append_capped(10)]


# ---------------------------------------------------------------------------
# State Manager — handles reducer semantics and scoped reads
# ---------------------------------------------------------------------------

def _extract_reducers(state_class: type) -> dict[str, Any]:
    """Inspect Annotated hints to find reducer functions per field."""
    hints = get_type_hints(state_class, include_extras=True)
    reducers: dict[str, Any] = {}
    for key, hint in hints.items():
        if get_origin(hint) is Annotated:
            args = get_args(hint)
            reducers[key] = args[1] if len(args) > 1 and callable(args[1]) else None
        else:
            reducers[key] = None  # overwrite semantics
    return reducers


class PipelineStateManager:
    """Manages typed state with reducer semantics for a single file pipeline.

    Usage::

        mgr = PipelineStateManager(FilePipelineState, initial_state)
        mgr.apply_update({"scout_analysis": {...}, "audit_findings": [new_finding]})
        coder_view = mgr.scoped_read(CODER_READ_KEYS)
    """

    def __init__(self, state_class: type, initial: dict):
        self._reducers = _extract_reducers(state_class)
        self._state: dict = dict(initial)

    @property
    def state(self) -> dict:
        """Shallow copy for reads."""
        return dict(self._state)

    def snapshot(self) -> dict:
        """Deep copy for checkpointing."""
        return copy.deepcopy(self._state)

    def apply_update(self, update: dict) -> None:
        """Merge a partial update using per-field reducer semantics."""
        for key, value in update.items():
            reducer = self._reducers.get(key)
            if reducer is not None and key in self._state:
                self._state[key] = reducer(self._state[key], value)
            else:
                self._state[key] = value

    def scoped_read(self, keys: frozenset[str]) -> dict:
        """Return a filtered view containing only the specified keys."""
        return {k: v for k, v in self._state.items() if k in keys}


# ---------------------------------------------------------------------------
# Per-role read scopes — what each agent can see
# ---------------------------------------------------------------------------

SCOUT_READ_KEYS = frozenset({
    "file_path", "file_entry", "phase_deliverables", "contracts",
    "phase_index",
    "prior_file_summaries",
})

CODER_READ_KEYS = frozenset({
    "file_path", "file_entry", "phase_deliverables", "contracts",
    "scout_analysis", "scout_directives", "scout_interfaces",
    "scout_patterns", "scout_imports_map",
    "prior_file_summaries",
})

AUDITOR_READ_KEYS = frozenset({
    "file_path", "file_entry", "contracts",
    "generated_code", "coder_decisions", "coder_known_issues",
    "audit_findings", "fixes_applied",
    "prior_file_summaries",
})

FIXER_READ_KEYS = frozenset({
    "file_path", "file_entry", "contracts",
    "generated_code",
    "audit_findings", "integration_findings",
    "coder_decisions", "coder_known_issues",
})


# ---------------------------------------------------------------------------
# State-to-context bridge — converts typed state → SubAgentHandoff.context_files
# ---------------------------------------------------------------------------

def _state_to_context_files(scoped_state: dict) -> dict[str, str]:
    """Convert typed state keys into labeled context_files for SubAgentHandoff.

    Bridges the typed state system with the existing context injection
    mechanism. Each state key becomes a labeled file in the agent's context.
    """
    ctx: dict[str, str] = {}

    # Contracts are already strings
    for k, v in scoped_state.get("contracts", {}).items():
        ctx[k] = v

    # Scout findings as structured JSON
    if "scout_analysis" in scoped_state:
        ctx["scout_analysis.json"] = json.dumps({
            "directives": scoped_state.get("scout_directives", []),
            "key_interfaces": scoped_state.get("scout_interfaces", []),
            "patterns": scoped_state.get("scout_patterns", {}),
            "imports_map": scoped_state.get("scout_imports_map", {}),
        }, indent=2)

    # Generated code
    if "generated_code" in scoped_state:
        file_path = scoped_state.get("file_path", "file.py")
        ctx[file_path] = scoped_state["generated_code"]

    # Coder intent
    if "coder_decisions" in scoped_state:
        ctx["coder_intent.md"] = (
            f"## Coder Decisions\n{scoped_state.get('coder_decisions', '')}\n\n"
            f"## Known Issues\n{scoped_state.get('coder_known_issues', 'none')}"
        )

    # Prior audit findings (for re-audit)
    findings = scoped_state.get("audit_findings", [])
    if findings:
        ctx["prior_audit_findings.json"] = json.dumps(findings, indent=2)

    # Fixes already applied (for re-audit context)
    fixes = scoped_state.get("fixes_applied", [])
    if fixes:
        ctx["fixes_applied.json"] = json.dumps(fixes, indent=2)

    # Prior file summaries (cross-file context)
    summaries = scoped_state.get("prior_file_summaries", [])
    if summaries:
        lines = []
        for s in summaries[-10:]:  # last 10 files max
            exports = ", ".join(s.get("key_exports", [])[:5])
            lines.append(f"- `{s['path']}`: {s.get('purpose', '')} — exports: {exports}")
        ctx["prior_files.md"] = "## Previously Built Files\n" + "\n".join(lines)

    # Phase deliverables
    if "phase_deliverables" in scoped_state:
        ctx["phase_deliverables.md"] = scoped_state["phase_deliverables"]

    return ctx


# ---------------------------------------------------------------------------
# Export extraction — parses public exports from Python source
# ---------------------------------------------------------------------------

def _extract_exports(source_code: str) -> list[str]:
    """Extract public exports from Python source code.

    Looks for: __all__, class definitions, function definitions.
    Returns a list of export names (max 10).
    """
    if not source_code:
        return []

    # Check for explicit __all__
    all_match = re.search(r'__all__\s*=\s*\[([^\]]+)\]', source_code)
    if all_match:
        items = re.findall(r'"([^"]+)"|\'([^\']+)\'', all_match.group(1))
        return [a or b for a, b in items][:10]

    # Fall back to class and function definitions
    exports = []
    for match in re.finditer(
        r'^(?:class|def|async def)\s+([A-Za-z]\w*)',
        source_code, re.MULTILINE,
    ):
        name = match.group(1)
        if not name.startswith('_'):
            exports.append(name)

    return exports[:10]


# ---------------------------------------------------------------------------
# Handoff Filters — transform context at agent boundaries
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class HandoffData:
    """Context passed between pipeline stages."""
    pipeline_state: dict
    prior_stage_output: dict
    tool_call_log: list
    text_output: str

    def clone(self, **kwargs) -> HandoffData:
        return dc_replace(self, **kwargs)


# Type alias for filter functions
HandoffFilter = Callable[[HandoffData], HandoffData]


def strip_tool_calls(data: HandoffData) -> HandoffData:
    """Remove tool call log — receiving agent doesn't need to see tool usage."""
    return data.clone(tool_call_log=[])


def strip_raw_text(data: HandoffData) -> HandoffData:
    """Remove raw text output — structured_output carries the useful data."""
    return data.clone(text_output="")


def cap_prior_output(max_chars: int = 2000) -> HandoffFilter:
    """Factory: truncate prior stage output values to cap."""
    def _filter(data: HandoffData) -> HandoffData:
        capped = {}
        for k, v in data.prior_stage_output.items():
            if isinstance(v, str) and len(v) > max_chars:
                capped[k] = v[:max_chars] + "\n[truncated]"
            else:
                capped[k] = v
        return data.clone(prior_stage_output=capped)
    return _filter


def compose_filters(*filters: HandoffFilter) -> HandoffFilter:
    """Compose multiple filters into a single pipeline."""
    def _composed(data: HandoffData) -> HandoffData:
        for f in filters:
            data = f(data)
        return data
    return _composed


# Pre-built composite filters per handoff boundary
SCOUT_TO_CODER_FILTER = compose_filters(
    strip_tool_calls,
    strip_raw_text,
)

CODER_TO_AUDITOR_FILTER = compose_filters(
    strip_tool_calls,
    strip_raw_text,
    cap_prior_output(1000),
)

AUDITOR_TO_FIXER_FILTER = compose_filters(
    strip_tool_calls,
    strip_raw_text,
)


# ---------------------------------------------------------------------------
# Lessons extraction + serialization
# ---------------------------------------------------------------------------

def make_empty_lessons() -> dict:
    """Return a fresh empty LessonsState dict."""
    return {
        "confirmed_patterns": [],
        "fix_patterns": [],
        "import_conventions": {},
        "error_resolutions": [],
        "decisions": [],
    }


def _extract_lessons_from_result(
    result: Any,
    state_mgr: PipelineStateManager,
) -> dict:
    """Extract structured lesson entries from a completed file pipeline.

    Parameters
    ----------
    result:
        A BuilderResult (or compatible) with .audit_verdict and .fixed_findings.
    state_mgr:
        The PipelineStateManager for this file's pipeline.

    Returns
    -------
    dict matching LessonsState structure — ready for TierStateManager.apply_update.
    """
    lessons_update: dict = make_empty_lessons()
    state = state_mgr.state

    # If audit passed first try → patterns are confirmed
    if getattr(result, "audit_verdict", "") == "PASS" and not getattr(result, "fixed_findings", ""):
        patterns = state.get("scout_patterns", {})
        for pattern_type, pattern_desc in patterns.items():
            if pattern_desc:
                lessons_update["confirmed_patterns"].append(
                    f"{pattern_type}: {pattern_desc}"
                )

    # If fixer ran → extract anti-patterns
    if getattr(result, "fixed_findings", ""):
        for finding in state.get("audit_findings", []):
            if finding.get("severity") == "error":
                lessons_update["fix_patterns"].append(
                    f"{finding.get('message', '')}"
                )
        for fix in state.get("fixes_applied", []):
            lessons_update["error_resolutions"].append({
                "error": fix.get("finding_ref", ""),
                "resolution": fix.get("change", ""),
                "file": state.get("file_path", ""),
            })

    # Import conventions from scout
    imports_map = state.get("scout_imports_map", {})
    if imports_map:
        lessons_update["import_conventions"] = imports_map

    # Coder decisions
    decisions = state.get("coder_decisions", "")
    if decisions and decisions != "none":
        lessons_update["decisions"].append(
            f"[{state.get('file_path', '?')}] {decisions[:200]}"
        )

    return lessons_update


def _lessons_to_context(lessons: dict) -> str:
    """Serialize LessonsState into a concise context string for agents."""
    parts = []

    confirmed = lessons.get("confirmed_patterns", [])
    if confirmed:
        parts.append("## Confirmed Patterns (use these)")
        for p in confirmed[-10:]:
            parts.append(f"- {p}")

    fixes = lessons.get("fix_patterns", [])
    if fixes:
        parts.append("\n## Anti-Patterns (avoid these)")
        for f in fixes[-10:]:
            parts.append(f"- {f}")

    imports = lessons.get("import_conventions", {})
    if imports:
        parts.append("\n## Import Conventions")
        for module, exp_list in list(imports.items())[-10:]:
            if isinstance(exp_list, list):
                parts.append(f"- `from {module} import {', '.join(exp_list)}`")
            else:
                parts.append(f"- `{module}`: {exp_list}")

    errors = lessons.get("error_resolutions", [])
    if errors:
        parts.append("\n## Resolved Errors")
        for e in errors[-5:]:
            parts.append(
                f"- {e.get('file', '?')}: {e.get('error', '')} → {e.get('resolution', '')}"
            )

    decisions = lessons.get("decisions", [])
    if decisions:
        parts.append("\n## Architecture Decisions")
        for d in decisions[-5:]:
            parts.append(f"- {d}")

    return "\n".join(parts)
