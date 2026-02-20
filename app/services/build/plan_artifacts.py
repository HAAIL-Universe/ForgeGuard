"""Phase plan-artifact storage — structured memory across context resets.

Bridges the build orchestrator with the MCP artifact store so that:

1. The planner for Phase N+1 knows what Phase N built and any issues.
2. Plans survive the ``all_files_written.clear()`` context reset between phases.
3. Plans persist to disk for build-resume scenarios.
4. Per-file generators know what other files are being built in the same phase.

All artefacts use ``build_id`` as the project-id scope in the artifact store,
which means each build gets a clean namespace.  Resume reloads the same
build_id and therefore the same artifacts.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import UUID

from forge_ide.mcp.artifact_store import (
    clear_artifacts,
    get_artifact,
    store_artifact,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Store helpers
# ---------------------------------------------------------------------------


def store_phase_plan(
    build_id: UUID,
    phase: dict,
    manifest: list[dict],
) -> dict:
    """Store the file manifest (plan) for a phase as an artifact.

    Called immediately after the Sonnet planner produces the manifest.
    """
    content = {
        "phase_number": phase["number"],
        "phase_name": phase.get("name", ""),
        "objective": phase.get("objective", ""),
        "deliverables": phase.get("deliverables", []),
        "manifest": [
            {
                "path": f["path"],
                "action": f.get("action", "create"),
                "purpose": f.get("purpose", ""),
                "depends_on": f.get("depends_on", []),
                "language": f.get("language", "python"),
                "estimated_lines": f.get("estimated_lines", 100),
            }
            for f in manifest
        ],
        "file_count": len(manifest),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    result = store_artifact(
        project_id=str(build_id),
        artifact_type="phase",
        key=f"plan_phase_{phase['number']}",
        content=content,
        ttl_hours=72.0,  # 3 days — generous for long builds
        persist=True,
    )
    logger.info(
        "[plan:store] Phase %d plan stored — %d files, %d chars",
        phase["number"],
        len(manifest),
        result.get("size_chars", 0),
    )
    return result


def store_phase_outcome(
    build_id: UUID,
    phase: dict,
    *,
    status: str,
    files_written: dict[str, Any],
    audit_verdict: str = "PASS",
    audit_attempts: int = 1,
    fixes_applied: int = 0,
    verification: dict | None = None,
    governance: dict | None = None,
) -> dict:
    """Store the outcome of a phase after it completes.

    Called at the end of each phase (pass or partial) BEFORE the context
    reset so that the next phase's planner can inspect it.
    """
    _lang_map = {
        ".py": "python", ".ts": "typescript", ".tsx": "typescript",
        ".js": "javascript", ".jsx": "javascript", ".json": "json",
        ".css": "css", ".scss": "scss", ".html": "html", ".md": "markdown",
    }
    file_list = []
    for p, c in files_written.items():
        size = 0
        if c is not None:
            try:
                size = len(c.encode("utf-8")) if isinstance(c, str) else len(str(c))
            except Exception:
                pass
        _ext = Path(p).suffix.lower() if "." in p else ""
        entry: dict[str, Any] = {"path": p, "size_bytes": size}
        lang = _lang_map.get(_ext)
        if lang:
            entry["language"] = lang
        file_list.append(entry)

    content = {
        "phase_number": phase["number"],
        "phase_name": phase.get("name", ""),
        "status": status,
        "files_written": file_list,
        "file_count": len(files_written),
        "audit_verdict": audit_verdict,
        "audit_attempts": audit_attempts,
        "fixes_applied": fixes_applied,
        "verification": {
            "syntax_errors": (verification or {}).get("syntax_errors", 0),
            "tests_failed": (verification or {}).get("tests_failed", 0),
        },
        "governance": {
            "passed": (governance or {}).get("passed", True),
        },
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }
    result = store_artifact(
        project_id=str(build_id),
        artifact_type="phase",
        key=f"outcome_phase_{phase['number']}",
        content=content,
        ttl_hours=72.0,
        persist=True,
    )
    logger.info(
        "[plan:outcome] Phase %d outcome stored — status=%s, %d files, %d chars",
        phase["number"],
        status,
        len(files_written),
        result.get("size_chars", 0),
    )
    return result


# ---------------------------------------------------------------------------
# Retrieval helpers
# ---------------------------------------------------------------------------


def get_prior_phase_context(build_id: UUID, current_phase_number: int) -> str:
    """Build a context string summarising all previously completed phases.

    Returns a markdown section suitable for injection into the planner
    and per-file generation prompts.  Returns ``""`` when no prior phases
    have stored artefacts.
    """
    sections: list[str] = []

    for prev_num in range(current_phase_number):
        plan = get_artifact(str(build_id), "phase", f"plan_phase_{prev_num}")
        outcome = get_artifact(str(build_id), "phase", f"outcome_phase_{prev_num}")

        if "error" in plan and "error" in outcome:
            continue  # No data for this phase at all — skip

        lines: list[str] = []

        # --- Plan info ---
        if "error" not in plan:
            pc = plan["content"]
            lines.append(f"### Phase {prev_num} — {pc.get('phase_name', '')}")
            lines.append(f"**Objective:** {pc.get('objective', '')}")
            lines.append(f"**Files planned:** {pc.get('file_count', 0)}")
            for f in pc.get("manifest", []):
                lines.append(f"- `{f['path']}` — {f.get('purpose', '')}")
        else:
            lines.append(f"### Phase {prev_num}")

        # --- Outcome info ---
        if "error" not in outcome:
            oc = outcome["content"]
            lines.append(f"\n**Outcome:** {oc.get('status', 'unknown')}")
            lines.append(f"**Files written:** {oc.get('file_count', 0)}")
            if oc.get("audit_attempts", 1) > 1:
                lines.append(
                    f"**Audit attempts:** {oc['audit_attempts']}"
                )
            if oc.get("fixes_applied", 0) > 0:
                lines.append(
                    f"**Auto-fixes applied:** {oc['fixes_applied']}"
                )
            verif = oc.get("verification", {})
            if verif.get("syntax_errors", 0) > 0:
                lines.append(
                    f"**Remaining syntax errors:** {verif['syntax_errors']}"
                )
            if verif.get("tests_failed", 0) > 0:
                lines.append(
                    f"**Remaining test failures:** {verif['tests_failed']}"
                )

        sections.append("\n".join(lines))

    if not sections:
        return ""

    return "## Prior Phase Summary\n\n" + "\n\n".join(sections) + "\n"


def get_current_phase_plan_context(
    build_id: UUID, phase_number: int
) -> str:
    """Get the current phase's manifest as context for per-file generation.

    Lets each file generator know what other files are being created in
    the same phase, improving cross-file coherence (imports, interfaces).
    """
    plan = get_artifact(str(build_id), "phase", f"plan_phase_{phase_number}")
    if "error" in plan:
        return ""

    pc = plan["content"]
    phase_name = pc.get("phase_name", "")
    name_suffix = f": {phase_name}" if phase_name else ""
    lines = [
        f"## Current Phase Plan — Phase {phase_number}{name_suffix}\n",
        f"**Objective:** {pc.get('objective', '')}",
        f"**Files being created in this phase:**\n",
    ]
    for f in pc.get("manifest", []):
        deps = (
            ", ".join(f.get("depends_on", []))
            if f.get("depends_on")
            else "none"
        )
        lines.append(
            f"- `{f['path']}` ({f.get('language', '')}) "
            f"— {f.get('purpose', '')} [depends: {deps}]"
        )
    return "\n".join(lines) + "\n"


def clear_build_artifacts(build_id: UUID) -> dict:
    """Clear all phase artefacts for a build (call at fresh build start)."""
    result = clear_artifacts(str(build_id), "phase")
    logger.info(
        "[plan:clear] Cleared phase artifacts for build %s — %d memory, %d disk",
        build_id,
        result.get("cleared_memory", 0),
        result.get("cleared_disk", 0),
    )
    return result
