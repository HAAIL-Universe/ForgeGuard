"""Upgrade service — orchestrate Forge Upgrade Advisor pipeline.

Chains version-currency checks, pattern analysis, and migration advice
into a single RenovationPlan.  Optionally generates an LLM executive
brief and a Forge-compatible spec for automatable tasks.
"""

from __future__ import annotations

import json
import logging
from typing import Any
from uuid import UUID

from app.clients.llm_client import chat
from app.config import settings, get_model_for_role
from app.repos.scout_repo import get_scout_run, update_scout_run
from app.services.migration_advisor import recommend_migrations
from app.services.pattern_analyzer import analyze_patterns
from app.services.version_db import check_all_dependencies

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# LLM prompt for executive renovation brief
# ---------------------------------------------------------------------------

_RENOVATION_SYSTEM_PROMPT = """\
You are ForgeGuard's Upgrade Advisor. Given a project's version report, \
detected anti-patterns, and migration recommendations, write a concise \
executive brief (JSON) summarising the renovation state.

Respond ONLY with valid JSON matching this schema:
{
  "health_grade": "A" | "B" | "C" | "D" | "F",
  "headline": "<one-sentence summary>",
  "top_priorities": ["<priority 1>", "<priority 2>", "<priority 3>"],
  "estimated_total_effort": "low" | "medium" | "high",
  "risk_summary": "<1-2 sentences about risk if nothing changes>",
  "forge_automation_note": "<note about which tasks Forge can automate>"
}
"""


# ---------------------------------------------------------------------------
# Helpers — extract deps from StackProfile
# ---------------------------------------------------------------------------


def _extract_python_deps(stack: dict) -> dict[str, str | None]:
    """Pull Python dependencies + versions from stack_profile."""
    deps: dict[str, str | None] = {}
    backend = stack.get("backend") or {}

    # Framework
    fw = backend.get("framework")
    fw_ver = backend.get("framework_version")
    if fw:
        deps[fw.lower()] = fw_ver

    # Runtime version
    lang = backend.get("language", "").lower()
    lang_ver = backend.get("language_version")
    if lang == "python":
        deps["python"] = lang_ver

    # ORM
    orm = backend.get("orm")
    if orm:
        deps[orm.lower()] = None

    # Testing
    test = stack.get("testing") or {}
    if test.get("framework"):
        deps[test["framework"].lower()] = None

    # Extra: scan for known packages in dep list
    for dep_list_key in ("python_deps", "dependencies"):
        dep_list = stack.get(dep_list_key) or backend.get(dep_list_key) or {}
        if isinstance(dep_list, dict):
            deps.update(dep_list)
        elif isinstance(dep_list, list):
            for d in dep_list:
                if isinstance(d, str):
                    parts = d.split("==")
                    deps[parts[0].strip().lower()] = parts[1].strip() if len(parts) > 1 else None

    return deps


def _extract_node_deps(stack: dict) -> dict[str, str | None]:
    """Pull Node.js / frontend dependencies + versions from stack_profile."""
    deps: dict[str, str | None] = {}
    fe = stack.get("frontend") or {}

    fw = fe.get("framework")
    fw_ver = fe.get("framework_version")
    if fw:
        deps[fw.lower()] = fw_ver

    lang = fe.get("language", "").lower()
    if lang in ("typescript", "javascript"):
        deps["typescript" if lang == "typescript" else "node"] = None

    bundler = fe.get("bundler")
    if bundler:
        deps[bundler.lower()] = None

    css = fe.get("css_framework")
    if css:
        deps[css.lower()] = None

    # Package.json dependencies if embedded
    for dep_list_key in ("node_deps", "dependencies"):
        dep_list = stack.get(dep_list_key) or fe.get(dep_list_key) or {}
        if isinstance(dep_list, dict):
            deps.update(dep_list)

    return deps


# ---------------------------------------------------------------------------
# Forge spec generator (37.5)
# ---------------------------------------------------------------------------


def generate_forge_spec(
    repo_name: str,
    migrations: list[dict],
) -> dict[str, Any] | None:
    """Build a forge.json-compatible spec for automatable migrations.

    Only includes migrations flagged ``forge_automatable: True``.
    Returns None if nothing is automatable.
    """
    automatable = [m for m in migrations if m.get("forge_automatable")]
    if not automatable:
        return None

    tasks: list[dict[str, Any]] = []
    for mig in automatable:
        tasks.append({
            "id": mig["id"].lower(),
            "name": mig.get("from_state", "") + " → " + mig.get("to_state", ""),
            "category": mig.get("category", "modernization"),
            "steps": mig.get("steps", []),
            "effort": mig.get("effort", "medium"),
            "risk": mig.get("risk", "low"),
        })

    return {
        "schema": "forge-upgrade-spec/v1",
        "repo": repo_name,
        "tasks": tasks,
        "total_automatable": len(tasks),
    }


# ---------------------------------------------------------------------------
# Main orchestrator (37.4)
# ---------------------------------------------------------------------------


async def generate_renovation_plan(
    user_id: UUID,
    run_id: UUID,
    include_llm: bool = True,
) -> dict[str, Any]:
    """Generate a full Renovation Plan from a completed deep-scan run.

    Pipeline
    --------
    1. Load deep-scan results from the DB.
    2. Extract dependencies → version currency check.
    3. Run pattern analysis on stack + architecture + files.
    4. Generate migration recommendations.
    5. Optionally call the LLM for an executive brief.
    6. Build a Forge automation spec.
    7. Assemble the RenovationPlan and persist it.

    Parameters
    ----------
    user_id : UUID
        Authenticated user (for ownership validation).
    run_id : UUID
        The deep-scan run to analyse.
    include_llm : bool
        Whether to generate the LLM executive brief (default True).

    Returns
    -------
    dict – the complete RenovationPlan payload.

    Raises
    ------
    ValueError – if the run doesn't exist, isn't owned, or isn't a deep scan.
    """

    # ── Load the deep-scan results ────────────────────────────────
    run = await get_scout_run(run_id)
    if run is None or str(run["user_id"]) != str(user_id):
        raise ValueError("Scout run not found")
    if run.get("scan_type") != "deep":
        raise ValueError("Upgrade plan requires a deep scan run")
    if run.get("status") != "completed":
        raise ValueError("Scout run has not completed yet")

    results = run.get("results")
    if results is None:
        raise ValueError("No results available for this run")
    if isinstance(results, str):
        results = json.loads(results)

    stack_profile = results.get("stack_profile") or {}
    arch_map = results.get("architecture") or {}
    file_contents = results.get("file_contents") or {}
    repo_name = run.get("repo_name", "unknown")

    # ── 1. Version currency ───────────────────────────────────────
    py_deps = _extract_python_deps(stack_profile)
    node_deps = _extract_node_deps(stack_profile)
    version_report = check_all_dependencies(py_deps, node_deps)

    # ── 2. Pattern analysis ───────────────────────────────────────
    pattern_findings = analyze_patterns(stack_profile, arch_map, file_contents)

    # ── 3. Migration recommendations ─────────────────────────────
    migration_recs = recommend_migrations(stack_profile, pattern_findings, version_report)

    # ── 4. Summary stats ─────────────────────────────────────────
    eol_count = sum(1 for v in version_report if v.get("status") == "eol")
    outdated_count = sum(1 for v in version_report if v.get("status") == "outdated")
    current_count = sum(1 for v in version_report if v.get("status") == "current")
    high_patterns = sum(1 for p in pattern_findings if p.get("severity") == "high")
    high_priority_migs = sum(1 for m in migration_recs if m.get("priority") == "high")
    automatable_count = sum(1 for m in migration_recs if m.get("forge_automatable"))

    summary_stats = {
        "dependencies_checked": len(version_report),
        "eol_count": eol_count,
        "outdated_count": outdated_count,
        "current_count": current_count,
        "patterns_detected": len(pattern_findings),
        "high_severity_patterns": high_patterns,
        "migrations_recommended": len(migration_recs),
        "high_priority_migrations": high_priority_migs,
        "forge_automatable": automatable_count,
    }

    # ── 5. LLM executive brief (optional) ────────────────────────
    executive_brief = None
    if include_llm and settings.ANTHROPIC_API_KEY:
        executive_brief = await _generate_executive_brief(
            version_report, pattern_findings, migration_recs, summary_stats,
        )

    # ── 6. Forge spec ────────────────────────────────────────────
    forge_spec = generate_forge_spec(repo_name, migration_recs)

    # ── 7. Assemble the plan ──────────────────────────────────────
    renovation_plan: dict[str, Any] = {
        "version_report": version_report,
        "pattern_findings": pattern_findings,
        "migration_recommendations": migration_recs,
        "summary_stats": summary_stats,
        "executive_brief": executive_brief,
        "forge_spec": forge_spec,
    }

    # ── 8. Persist back into scout_runs.results ───────────────────
    results["renovation_plan"] = renovation_plan
    await _update_run_results(run_id, results, run)

    logger.info("Renovation plan generated for run %s (%d migrations)", run_id, len(migration_recs))

    return renovation_plan


async def get_renovation_plan(user_id: UUID, run_id: UUID) -> dict | None:
    """Retrieve a previously generated renovation plan."""
    run = await get_scout_run(run_id)
    if run is None or str(run["user_id"]) != str(user_id):
        raise ValueError("Scout run not found")

    results = run.get("results")
    if results is None:
        return None
    if isinstance(results, str):
        results = json.loads(results)

    return results.get("renovation_plan")


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


async def _update_run_results(run_id: UUID, results: dict, run: dict) -> None:
    """Persist updated results JSONB without changing status or counts."""
    await update_scout_run(
        run_id,
        status=run.get("status", "completed"),
        results=results,
        checks_passed=run.get("checks_passed", 0),
        checks_failed=run.get("checks_failed", 0),
        checks_warned=run.get("checks_warned", 0),
    )


async def _generate_executive_brief(
    version_report: list[dict],
    pattern_findings: list[dict],
    migration_recs: list[dict],
    summary_stats: dict,
) -> dict | None:
    """Call the LLM to produce a renovation executive brief."""
    try:
        user_msg = json.dumps({
            "summary_stats": summary_stats,
            "version_issues": [
                v for v in version_report if v.get("status") in ("eol", "outdated")
            ],
            "patterns": [
                {"id": p["id"], "name": p["name"], "severity": p["severity"]}
                for p in pattern_findings
            ],
            "migrations": [
                {
                    "id": m["id"],
                    "from_state": m.get("from_state"),
                    "to_state": m.get("to_state"),
                    "priority": m.get("priority"),
                    "effort": m.get("effort"),
                    "forge_automatable": m.get("forge_automatable", False),
                }
                for m in migration_recs
            ],
        }, indent=2)

        result = await chat(
            api_key=settings.ANTHROPIC_API_KEY,
            model=get_model_for_role("planner"),
            system_prompt=_RENOVATION_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_msg}],
            max_tokens=1024,
            provider="anthropic",
        )

        text = result["text"] if isinstance(result, dict) else result
        text = text.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1] if "\n" in text else text
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()

        brief = json.loads(text)
        if "health_grade" not in brief:
            logger.warning("LLM renovation brief missing health_grade")
            return None
        return brief
    except Exception:
        logger.exception("Failed to generate executive renovation brief")
        return None
