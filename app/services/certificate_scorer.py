"""Certificate scoring engine — compute weighted quality scores.

Pure-function scorer that takes aggregated CertificateData and produces
scores across 6 dimensions, an overall weighted score, and a verdict.
No DB or IO calls — fully deterministic.
"""

from __future__ import annotations

from typing import Any

from app.services.reliability_scorer import compute_reliability_score
from app.services.consistency_scorer import compute_consistency_score
from app.services.architecture_baseline import compare_against_baseline

# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

CertificateScores = dict[str, Any]
# {
#   "dimensions": {
#     "build_integrity":  { "score": 0-100, "weight": 0.20, "details": [...] },
#     "test_coverage":    { "score": 0-100, "weight": 0.20, "details": [...] },
#     "audit_compliance": { "score": 0-100, "weight": 0.20, "details": [...] },
#     "governance":       { "score": 0-100, "weight": 0.15, "details": [...] },
#     "security":         { "score": 0-100, "weight": 0.15, "details": [...] },
#     "cost_efficiency":  { "score": 0-100, "weight": 0.10, "details": [...] },
#   },
#   "overall_score": 0-100,
#   "verdict": "CERTIFIED" | "CONDITIONAL" | "FLAGGED",
#   "project": { ... },     # echoed from input
#   "build_summary": { ... },
#   "generated_at": str,
# }

# Dimension weights — must sum to 1.0
_WEIGHTS = {
    "build_integrity": 0.15,
    "test_coverage": 0.15,
    "audit_compliance": 0.10,
    "governance": 0.10,
    "security": 0.10,
    "cost_efficiency": 0.05,
    "reliability": 0.15,
    "consistency": 0.10,
    "architecture": 0.10,
}


# ---------------------------------------------------------------------------
# Main scorer
# ---------------------------------------------------------------------------


def compute_certificate_scores(data: dict) -> CertificateScores:
    """Compute quality scores from aggregated certificate data.

    Parameters
    ----------
    data : CertificateData dict from certificate_aggregator.

    Returns
    -------
    CertificateScores dict with per-dimension scores, overall score, and verdict.
    """
    from datetime import datetime, timezone

    reliability_result = compute_reliability_score(data)
    consistency_result = compute_consistency_score(data)
    architecture_result = compare_against_baseline(data.get("scout"))

    dimensions = {
        "build_integrity": _score_build_integrity(data),
        "test_coverage": _score_test_coverage(data),
        "audit_compliance": _score_audit_compliance(data),
        "governance": _score_governance(data),
        "security": _score_security(data),
        "cost_efficiency": _score_cost_efficiency(data),
        "reliability": {
            "score": reliability_result["score"],
            "weight": _WEIGHTS["reliability"],
            "details": reliability_result["details"],
        },
        "consistency": {
            "score": consistency_result["score"],
            "weight": _WEIGHTS["consistency"],
            "details": consistency_result["details"],
        },
        "architecture": {
            "score": architecture_result["score"],
            "weight": _WEIGHTS["architecture"],
            "details": architecture_result["details"],
        },
    }

    # Weighted overall
    overall = 0.0
    for dim_key, dim_data in dimensions.items():
        weight = _WEIGHTS.get(dim_key, 0)
        overall += dim_data["score"] * weight

    overall = round(overall, 1)

    # Verdict
    if overall >= 90:
        verdict = "CERTIFIED"
    elif overall >= 70:
        verdict = "CONDITIONAL"
    else:
        verdict = "FLAGGED"

    # ── Phase 58: Dossier baseline delta ──────────────────────────
    dossier_baseline = data.get("dossier_baseline")
    baseline_score = None
    delta_json = None
    if dossier_baseline and isinstance(dossier_baseline, dict):
        baseline_score = dossier_baseline.get("computed_score")
        baseline_dims = dossier_baseline.get("dimensions", {})
        delta_json = {}
        for dim_key, dim_data in dimensions.items():
            b_dim = baseline_dims.get(dim_key, {})
            b_score = b_dim.get("score") if isinstance(b_dim, dict) else None
            if b_score is not None:
                delta_json[dim_key] = {
                    "baseline": b_score,
                    "final": dim_data["score"],
                    "delta": round(dim_data["score"] - b_score, 1),
                }
            else:
                delta_json[dim_key] = {
                    "baseline": None,
                    "final": dim_data["score"],
                    "delta": None,
                }

    # Build summary for certificate
    build = data.get("build")
    build_summary = None
    if build:
        build_summary = {
            "id": build.get("id"),
            "phase": build.get("phase"),
            "status": build.get("status"),
            "loop_count": build.get("loop_count", 0),
            "files_written": build.get("stats", {}).get("files_written_count", 0),
            "git_commits": build.get("stats", {}).get("git_commits_made", 0),
            "cost_usd": build.get("cost", {}).get("total_cost_usd", 0),
            "total_tokens": (
                build.get("cost", {}).get("total_input_tokens", 0)
                + build.get("cost", {}).get("total_output_tokens", 0)
            ),
        }

    return {
        "dimensions": dimensions,
        "overall_score": overall,
        "verdict": verdict,
        "project": data.get("project"),
        "build_summary": build_summary,
        "builds_total": data.get("builds_total", 0),
        "contracts_count": data.get("contracts", {}).get("count", 0),
        "baseline_score": baseline_score,
        "delta": delta_json,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


# ---------------------------------------------------------------------------
# Dimension scorers
# ---------------------------------------------------------------------------


def _score_build_integrity(data: dict) -> dict:
    """Score based on build completion and quality signals."""
    build = data.get("build")
    details: list[str] = []

    if build is None:
        details.append("No builds found")
        return {"score": 0, "weight": _WEIGHTS["build_integrity"], "details": details}

    score = 0.0
    status = build.get("status", "unknown")

    # Completion: 60 points for completed
    if status == "completed":
        score += 60
        details.append("Build completed successfully")
    elif status == "error":
        score += 10
        details.append(f"Build failed: {build.get('error_detail', 'unknown error')}")
    elif status == "cancelled":
        score += 15
        details.append("Build was cancelled")
    elif status == "paused":
        score += 30
        details.append("Build is paused")
    else:
        score += 20
        details.append(f"Build status: {status}")

    # Loop count bonus: fewer loops = better (max 20 points)
    loop_count = build.get("loop_count", 0)
    if loop_count <= 1:
        score += 20
        details.append("Completed in 1 loop (first-time pass)")
    elif loop_count <= 3:
        score += 15
        details.append(f"Completed in {loop_count} loops")
    elif loop_count <= 5:
        score += 10
        details.append(f"Required {loop_count} loops")
    else:
        score += 5
        details.append(f"Required {loop_count} loops (high iteration)")

    # Stats bonus: evidence of meaningful work (max 20 points)
    stats = build.get("stats", {})
    files_written = stats.get("files_written_count", 0)
    commits_made = stats.get("git_commits_made", 0)

    if files_written > 0:
        score += min(10, files_written)
        details.append(f"{files_written} files written")

    if commits_made > 0:
        score += min(10, commits_made * 2)
        details.append(f"{commits_made} git commits made")

    return {"score": min(100, round(score)), "weight": _WEIGHTS["build_integrity"], "details": details}


def _score_test_coverage(data: dict) -> dict:
    """Score based on scout check results and audit test gate."""
    details: list[str] = []

    # Primary source: Scout deep scan checks
    scout = data.get("scout")
    if scout:
        passed = scout.get("checks_passed", 0)
        failed = scout.get("checks_failed", 0)
        warned = scout.get("checks_warned", 0)
        total = passed + failed + warned

        if total > 0:
            pass_rate = passed / total
            score = round(pass_rate * 80)  # Up to 80 from pass rate
            details.append(f"Scout: {passed}/{total} checks passed ({round(pass_rate*100)}%)")

            if warned > 0:
                warn_penalty = min(10, warned * 2)
                score -= warn_penalty
                details.append(f"{warned} warnings (−{warn_penalty} pts)")
        else:
            score = 40
            details.append("Scout scan completed but no check data")

        # Quality score bonus (from dossier)
        quality = scout.get("quality_score")
        if quality is not None:
            bonus = min(20, round(quality / 5))
            score += bonus
            details.append(f"Dossier quality score: {quality}/100 (+{bonus} pts)")
        else:
            score += 10  # Neutral if no dossier
            details.append("No dossier quality score available")

        return {"score": max(0, min(100, score)), "weight": _WEIGHTS["test_coverage"], "details": details}

    # No scout data — fall back to audit compliance
    audit = data.get("audit", {})
    if audit.get("runs_total", 0) > 0:
        pass_rate = audit.get("pass_rate", 0)
        score = round(pass_rate * 60)
        details.append(f"No Scout scan. Audit pass rate: {round(pass_rate*100)}%")
        return {"score": score, "weight": _WEIGHTS["test_coverage"], "details": details}

    details.append("No test or scan data available")
    return {"score": 0, "weight": _WEIGHTS["test_coverage"], "details": details}


def _score_audit_compliance(data: dict) -> dict:
    """Score based on audit run history."""
    audit = data.get("audit", {})
    details: list[str] = []

    runs_total = audit.get("runs_total", 0)
    if runs_total == 0:
        details.append("No audit runs recorded")
        return {"score": 0, "weight": _WEIGHTS["audit_compliance"], "details": details}

    pass_rate = audit.get("pass_rate", 0)
    score = round(pass_rate * 80)  # Up to 80 from pass rate
    details.append(f"Audit pass rate: {round(pass_rate*100)}% across {runs_total} runs")

    # Latest result bonus
    latest = audit.get("latest_result")
    if latest == "PASS":
        score += 20
        details.append("Latest audit: PASS (+20 pts)")
    elif latest == "FAIL":
        details.append("Latest audit: FAIL")
    elif latest:
        score += 5
        details.append(f"Latest audit: {latest}")

    return {"score": min(100, score), "weight": _WEIGHTS["audit_compliance"], "details": details}


def _score_governance(data: dict) -> dict:
    """Score based on per-check governance breakdown."""
    gov = data.get("governance")
    details: list[str] = []

    if gov is None:
        details.append("No governance check data available")
        return {"score": 50, "weight": _WEIGHTS["governance"], "details": details}

    total = gov.get("total", 0)
    if total == 0:
        details.append("No governance checks recorded")
        return {"score": 50, "weight": _WEIGHTS["governance"], "details": details}

    pass_count = gov.get("pass_count", 0)
    fail_count = gov.get("fail_count", 0)
    warn_count = gov.get("warn_count", 0)

    # Base score from pass rate
    pass_rate = pass_count / total
    score = round(pass_rate * 85)
    details.append(f"Governance: {pass_count}/{total} checks passed")

    # Penalty for blocking failures
    if fail_count > 0:
        fail_penalty = min(30, fail_count * 10)
        score -= fail_penalty
        details.append(f"{fail_count} blocking failures (−{fail_penalty} pts)")

    # Lighter penalty for warnings
    if warn_count > 0:
        warn_penalty = min(10, warn_count * 3)
        score -= warn_penalty
        details.append(f"{warn_count} warnings (−{warn_penalty} pts)")

    # Clean sweep bonus
    if fail_count == 0 and warn_count == 0:
        score += 15
        details.append("All checks clean (+15 pts)")

    return {"score": max(0, min(100, score)), "weight": _WEIGHTS["governance"], "details": details}


def _score_security(data: dict) -> dict:
    """Score based on secrets detection and Scout security patterns."""
    details: list[str] = []
    score = 80  # Start optimistic

    # Check governance for W1 secrets scan
    gov = data.get("governance")
    if gov:
        checks = gov.get("checks", [])
        w1 = next((c for c in checks if c.get("code") == "W1"), None)
        if w1:
            if w1.get("result") == "PASS":
                score += 10
                details.append("Secrets scan: clean")
            elif w1.get("result") == "WARN":
                score -= 20
                details.append("Secrets scan: potential secrets detected (−20 pts)")
            elif w1.get("result") == "FAIL":
                score -= 40
                details.append("Secrets scan: secrets exposed (−40 pts)")
        else:
            details.append("No secrets scan in governance checks")

    # Scout health grade
    scout = data.get("scout")
    if scout:
        health_grade = scout.get("health_grade")
        if health_grade:
            grade_bonus = {"A": 10, "B": 5, "C": 0, "D": -10, "F": -20}.get(health_grade, 0)
            score += grade_bonus
            details.append(f"Scout health grade: {health_grade} ({'+' if grade_bonus >= 0 else ''}{grade_bonus} pts)")
    else:
        details.append("No Scout security analysis available")

    if not details:
        details.append("Security baseline applied")

    return {"score": max(0, min(100, score)), "weight": _WEIGHTS["security"], "details": details}


def _score_cost_efficiency(data: dict) -> dict:
    """Score based on build cost and token usage."""
    build = data.get("build")
    details: list[str] = []

    if build is None:
        details.append("No build cost data available")
        return {"score": 50, "weight": _WEIGHTS["cost_efficiency"], "details": details}

    cost = build.get("cost", {})
    cost_usd = cost.get("total_cost_usd", 0)
    total_tokens = cost.get("total_input_tokens", 0) + cost.get("total_output_tokens", 0)

    if cost_usd == 0 and total_tokens == 0:
        details.append("No cost data recorded")
        return {"score": 70, "weight": _WEIGHTS["cost_efficiency"], "details": details}

    score = 100  # Start at perfect and deduct

    # Cost brackets (USD)
    if cost_usd < 1.0:
        details.append(f"Build cost: ${cost_usd:.2f} (very efficient)")
    elif cost_usd < 5.0:
        score -= 5
        details.append(f"Build cost: ${cost_usd:.2f} (efficient)")
    elif cost_usd < 15.0:
        score -= 15
        details.append(f"Build cost: ${cost_usd:.2f} (moderate)")
    elif cost_usd < 50.0:
        score -= 30
        details.append(f"Build cost: ${cost_usd:.2f} (high)")
    else:
        score -= 50
        details.append(f"Build cost: ${cost_usd:.2f} (very high)")

    # Token count signal
    if total_tokens > 0:
        details.append(f"Total tokens: {total_tokens:,}")
        if total_tokens > 1_000_000:
            score -= 10
            details.append("High token usage (−10 pts)")

    # Phase count efficiency
    phase_count = cost.get("phase_count", 0)
    if phase_count > 0:
        cost_per_phase = cost_usd / phase_count
        details.append(f"${cost_per_phase:.2f}/phase across {phase_count} phases")

    return {"score": max(0, min(100, score)), "weight": _WEIGHTS["cost_efficiency"], "details": details}
