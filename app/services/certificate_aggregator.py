"""Certificate data aggregator — pull data from every ForgeGuard subsystem.

Collects project metadata, build stats, audit history, governance checks,
Scout dossier, and cost data into a single CertificateData dict for scoring.
"""

from __future__ import annotations

import json
import logging
from typing import Any
from uuid import UUID

from app.repos.audit_repo import get_audit_run_detail, get_audit_runs_by_repo
from app.repos.build_repo import (
    get_build_cost_summary,
    get_build_stats,
    get_builds_for_project,
    get_latest_build_for_project,
)
from app.repos.project_repo import (
    get_contracts_by_project,
    get_project_by_id,
)
from app.repos.scout_repo import get_scout_run, get_scout_runs_by_repo

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

CertificateData = dict[str, Any]
# {
#   "project": { id, name, description, status, repo_id, repo_full_name },
#   "build": { id, phase, status, loop_count, started_at, completed_at,
#              error_detail, completed_phases,
#              stats: { total_turns, total_audit_attempts, files_written_count,
#                       git_commits_made, interjections_received },
#              cost: { total_input_tokens, total_output_tokens,
#                      total_cost_usd, phase_count } } | None,
#   "audit": { runs_total, recent_runs: [...], pass_rate, latest_result },
#   "governance": { checks: [{code, name, result, detail}], pass_count,
#                   fail_count, warn_count } | None,
#   "scout": { stack_profile, architecture, dossier, checks_passed,
#              checks_failed, checks_warned, quality_score } | None,
#   "contracts": { count, types: [...] },
#   "builds_total": int,
# }


# ---------------------------------------------------------------------------
# Aggregator
# ---------------------------------------------------------------------------


async def aggregate_certificate_data(
    project_id: UUID,
    user_id: UUID,
) -> CertificateData:
    """Aggregate data from every ForgeGuard subsystem for certificate generation.

    Parameters
    ----------
    project_id : UUID
        The project to generate a certificate for.
    user_id : UUID
        Authenticated user (ownership validation).

    Returns
    -------
    CertificateData dict with sections for project, build, audit,
    governance, scout, and contracts.

    Raises
    ------
    ValueError – if project not found or not owned by user.
    """
    # ── Project ───────────────────────────────────────────────────
    project = await get_project_by_id(project_id)
    if project is None or str(project["user_id"]) != str(user_id):
        raise ValueError("Project not found")

    project_data = {
        "id": str(project["id"]),
        "name": project["name"],
        "description": project.get("description"),
        "status": project.get("status", "unknown"),
        "repo_id": str(project["repo_id"]) if project.get("repo_id") else None,
        "repo_full_name": project.get("repo_full_name"),
    }

    repo_id = project.get("repo_id")

    # ── Build ─────────────────────────────────────────────────────
    build_data = await _aggregate_build(project_id)

    # ── All builds count ──────────────────────────────────────────
    all_builds = await get_builds_for_project(project_id)
    builds_total = len(all_builds)

    # ── Audit ─────────────────────────────────────────────────────
    audit_data = await _aggregate_audit(repo_id) if repo_id else _empty_audit()

    # ── Governance (from latest audit run) ────────────────────────
    governance_data = await _aggregate_governance(audit_data)

    # ── Scout ─────────────────────────────────────────────────────
    scout_data = await _aggregate_scout(repo_id, user_id) if repo_id else None

    # ── Dossier baseline (Phase 58) ──────────────────────────────
    dossier_baseline = None
    if scout_data and scout_data.get("dossier_available"):
        dossier_baseline = await _extract_dossier_baseline(repo_id, user_id)

    # ── Contracts ─────────────────────────────────────────────────
    contracts = await get_contracts_by_project(project_id)
    contracts_data = {
        "count": len(contracts),
        "types": [c.get("contract_type") for c in contracts],
    }

    return {
        "project": project_data,
        "build": build_data,
        "builds_total": builds_total,
        "audit": audit_data,
        "governance": governance_data,
        "scout": scout_data,
        "dossier_baseline": dossier_baseline,
        "contracts": contracts_data,
    }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


async def _aggregate_build(project_id: UUID) -> dict | None:
    """Pull latest build + stats + cost summary."""
    build = await get_latest_build_for_project(project_id)
    if build is None:
        return None

    build_id = build["id"]

    stats = await get_build_stats(build_id)
    cost = await get_build_cost_summary(build_id)

    # Serialise Decimal to float for JSON compatibility
    cost_usd = cost.get("total_cost_usd", 0)
    cost_serialised = {
        "total_input_tokens": int(cost.get("total_input_tokens", 0)),
        "total_output_tokens": int(cost.get("total_output_tokens", 0)),
        "total_cost_usd": float(cost_usd) if cost_usd else 0.0,
        "phase_count": int(cost.get("phase_count", 0)),
    }

    return {
        "id": str(build_id),
        "phase": build.get("phase"),
        "status": build.get("status"),
        "loop_count": build.get("loop_count", 0),
        "started_at": _iso(build.get("started_at")),
        "completed_at": _iso(build.get("completed_at")),
        "error_detail": build.get("error_detail"),
        "completed_phases": build.get("completed_phases"),
        "stats": stats,
        "cost": cost_serialised,
    }


async def _aggregate_audit(repo_id: UUID) -> dict:
    """Pull recent audit runs and compute pass rate."""
    try:
        runs, total = await get_audit_runs_by_repo(repo_id, limit=20, offset=0)
    except Exception:
        logger.exception("Failed to fetch audit runs for repo %s", repo_id)
        return _empty_audit()

    if not runs:
        return _empty_audit()

    pass_count = sum(1 for r in runs if r.get("overall_result") == "PASS")
    pass_rate = pass_count / len(runs) if runs else 0.0
    latest_result = runs[0].get("overall_result") if runs else None

    recent = [
        {
            "id": str(r["id"]),
            "commit_sha": r.get("commit_sha", "")[: 8],
            "overall_result": r.get("overall_result"),
            "files_checked": r.get("files_checked", 0),
            "created_at": _iso(r.get("created_at")),
        }
        for r in runs[:5]
    ]

    return {
        "runs_total": total,
        "recent_runs": recent,
        "pass_rate": round(pass_rate, 4),
        "latest_result": latest_result,
        "latest_run_id": str(runs[0]["id"]) if runs else None,
    }


def _empty_audit() -> dict:
    return {
        "runs_total": 0,
        "recent_runs": [],
        "pass_rate": 0.0,
        "latest_result": None,
        "latest_run_id": None,
    }


async def _aggregate_governance(audit_data: dict) -> dict | None:
    """Pull governance check breakdown from the latest audit run."""
    latest_run_id = audit_data.get("latest_run_id")
    if not latest_run_id:
        return None

    try:
        from uuid import UUID as _UUID
        detail = await get_audit_run_detail(_UUID(latest_run_id))
    except Exception:
        logger.exception("Failed to fetch audit detail for %s", latest_run_id)
        return None

    if detail is None:
        return None

    checks = detail.get("checks", [])
    pass_count = sum(1 for c in checks if c.get("result") == "PASS")
    fail_count = sum(1 for c in checks if c.get("result") == "FAIL")
    warn_count = sum(1 for c in checks if c.get("result") == "WARN")

    return {
        "checks": [
            {
                "code": c.get("check_code"),
                "name": c.get("check_name"),
                "result": c.get("result"),
                "detail": c.get("detail"),
            }
            for c in checks
        ],
        "pass_count": pass_count,
        "fail_count": fail_count,
        "warn_count": warn_count,
        "total": len(checks),
    }


async def _aggregate_scout(repo_id: UUID, user_id: UUID) -> dict | None:
    """Pull latest deep-scan Scout dossier data."""
    try:
        runs = await get_scout_runs_by_repo(repo_id, user_id, limit=5)
    except Exception:
        logger.exception("Failed to fetch scout runs for repo %s", repo_id)
        return None

    # Find latest completed deep scan
    deep_run = next(
        (r for r in runs if r.get("scan_type") == "deep" and r.get("status") == "completed"),
        None,
    )
    if deep_run is None:
        return None

    # Fetch full run to get results JSONB
    full_run = await get_scout_run(deep_run["id"])
    if full_run is None:
        return None

    results = full_run.get("results")
    if results is None:
        return None
    if isinstance(results, str):
        results = json.loads(results)

    # Extract quality score from dossier if available
    dossier = results.get("dossier")
    quality_score = None
    if dossier and isinstance(dossier, dict):
        qa = dossier.get("quality_assessment")
        if qa and isinstance(qa, dict):
            quality_score = qa.get("score")

    # Check for renovation plan
    renovation_plan = results.get("renovation_plan")
    health_grade = None
    if renovation_plan:
        brief = renovation_plan.get("executive_brief")
        if brief and isinstance(brief, dict):
            health_grade = brief.get("health_grade")

    return {
        "stack_profile": results.get("stack_profile"),
        "architecture": results.get("architecture"),
        "dossier_available": dossier is not None,
        "quality_score": quality_score,
        "health_grade": health_grade,
        "checks_passed": deep_run.get("checks_passed", 0),
        "checks_failed": deep_run.get("checks_failed", 0),
        "checks_warned": deep_run.get("checks_warned", 0),
        "files_analysed": results.get("files_analysed", 0),
        "tree_size": results.get("tree_size", 0),
    }


def _iso(val: Any) -> str | None:
    """Convert a datetime-like value to ISO string."""
    if val is None:
        return None
    if hasattr(val, "isoformat"):
        return val.isoformat()
    return str(val)


# ---------------------------------------------------------------------------
# Phase 58 — Dossier baseline extraction
# ---------------------------------------------------------------------------


async def _extract_dossier_baseline(
    repo_id: UUID, user_id: UUID,
) -> dict | None:
    """Extract the locked dossier's 9-dimension baseline metrics.

    Returns a dict with ``baseline_sha``, ``computed_score``, and per-dimension
    scores, or *None* if no locked dossier exists.
    """
    try:
        runs = await get_scout_runs_by_repo(repo_id, user_id, limit=5)
    except Exception:
        logger.exception("Failed to fetch scout runs for dossier baseline")
        return None

    # Find the latest completed deep scan with a lock
    for run in runs:
        if run.get("scan_type") != "deep" or run.get("status") != "completed":
            continue
        full_run = await get_scout_run(run["id"])
        if full_run is None:
            continue
        # Check if locked (dossier_locked_at exists)
        if not full_run.get("dossier_locked_at"):
            continue

        results = full_run.get("results")
        if results is None:
            continue
        if isinstance(results, str):
            results = json.loads(results)

        metrics = results.get("metrics")
        if metrics is None:
            continue

        return {
            "dossier_run_id": str(full_run["id"]),
            "baseline_sha": results.get("head_sha"),
            "computed_score": metrics.get("computed_score"),
            "dimensions": metrics.get("scores", {}),
            "locked_at": _iso(full_run.get("dossier_locked_at")),
        }

    return None
