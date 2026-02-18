"""Reliability scorer — measure stability and dependability of Forge output.

Pure-function scorer that analyses build history and audit trends to
determine how reliably the system produces working, stable code.
No DB or IO calls — fully deterministic.

Signals measured:
  - Fix loop rate (lower is better — got it right without thrashing)
  - Audit trend (are audits staying passed or oscillating?)
  - Build completion rate (builds completed / builds started)
  - Error recovery (errors encountered vs successfully resolved)
  - Test pass stability (consistent green, not flaky)
"""

from __future__ import annotations

from typing import Any

# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

ReliabilityScore = dict[str, Any]
# {
#   "score": 0–100,
#   "grade": "A" | "B" | "C" | "D" | "F",
#   "details": [ str, ... ],
#   "sub_scores": {
#     "fix_loop_rate":      { "score": 0–100, "detail": str },
#     "audit_trend":        { "score": 0–100, "detail": str },
#     "build_completion":   { "score": 0–100, "detail": str },
#     "error_recovery":     { "score": 0–100, "detail": str },
#     "test_stability":     { "score": 0–100, "detail": str },
#   },
# }


# Sub-score weights — must sum to 1.0
_WEIGHTS = {
    "fix_loop_rate": 0.25,
    "audit_trend": 0.25,
    "build_completion": 0.20,
    "error_recovery": 0.15,
    "test_stability": 0.15,
}


# ---------------------------------------------------------------------------
# Main scorer
# ---------------------------------------------------------------------------


def compute_reliability_score(data: dict) -> ReliabilityScore:
    """Compute reliability score from aggregated certificate data.

    Parameters
    ----------
    data : CertificateData dict from certificate_aggregator.

    Returns
    -------
    ReliabilityScore with overall score, grade, and per-signal sub-scores.
    """
    sub_scores = {
        "fix_loop_rate": _score_fix_loop_rate(data),
        "audit_trend": _score_audit_trend(data),
        "build_completion": _score_build_completion(data),
        "error_recovery": _score_error_recovery(data),
        "test_stability": _score_test_stability(data),
    }

    # Weighted overall
    overall = 0.0
    for key, sub in sub_scores.items():
        weight = _WEIGHTS.get(key, 0)
        overall += sub["score"] * weight

    overall = round(overall, 1)

    # Grade
    grade = _letter_grade(overall)

    # Summary details
    details: list[str] = []
    for key, sub in sub_scores.items():
        label = key.replace("_", " ").title()
        details.append(f"{label}: {sub['score']}/100 — {sub['detail']}")

    return {
        "score": overall,
        "grade": grade,
        "details": details,
        "sub_scores": sub_scores,
    }


# ---------------------------------------------------------------------------
# Sub-scorers
# ---------------------------------------------------------------------------


def _score_fix_loop_rate(data: dict) -> dict[str, Any]:
    """Score: did Forge get it right without excessive fix loops?

    Lower loop_count relative to phases = more deterministic output.
    First-time pass (loop_count ≤ 1) is ideal.
    """
    build = data.get("build")

    if build is None:
        return {"score": 50, "detail": "No build data available"}

    loop_count = build.get("loop_count", 0)
    status = build.get("status", "unknown")

    # If build didn't complete, score is penalised
    if status != "completed":
        base = 30
        detail = f"Build not completed (status: {status})"
        if loop_count > 0:
            detail += f", {loop_count} loops attempted"
        return {"score": base, "detail": detail}

    # Scoring: first-time pass is 100, degrades with more loops
    if loop_count <= 1:
        score = 100
        detail = "First-pass success — no fix loops needed"
    elif loop_count == 2:
        score = 90
        detail = "2 loops — minor corrections needed"
    elif loop_count == 3:
        score = 80
        detail = "3 loops — some iteration required"
    elif loop_count <= 5:
        score = 65
        detail = f"{loop_count} loops — moderate iteration"
    elif loop_count <= 8:
        score = 45
        detail = f"{loop_count} loops — significant iteration"
    elif loop_count <= 12:
        score = 30
        detail = f"{loop_count} loops — high iteration"
    else:
        score = max(10, 100 - loop_count * 7)
        detail = f"{loop_count} loops — excessive iteration"

    # Bonus for completing despite loops
    if loop_count > 3 and status == "completed":
        score = min(100, score + 10)
        detail += " (completed successfully)"

    return {"score": max(0, min(100, score)), "detail": detail}


def _score_audit_trend(data: dict) -> dict[str, Any]:
    """Score: are audits consistently passing or oscillating?

    Analyses the recent_runs list to detect:
    - Stable passing (all recent = PASS) → high score
    - Improving trend (failures early, passes late) → good score
    - Oscillating (PASS/FAIL/PASS/FAIL) → concerning
    - Degrading trend (passes early, failures late) → bad
    """
    audit = data.get("audit", {})
    recent_runs = audit.get("recent_runs", [])

    if not recent_runs:
        if audit.get("runs_total", 0) == 0:
            return {"score": 50, "detail": "No audit runs recorded"}
        return {"score": 40, "detail": "No recent audit data available"}

    # Extract pass/fail sequence (most recent first)
    results = [r.get("overall_result", "UNKNOWN") for r in recent_runs]
    pass_count = sum(1 for r in results if r == "PASS")
    fail_count = sum(1 for r in results if r == "FAIL")
    total = len(results)

    # Overall pass rate
    pass_rate = pass_count / total if total > 0 else 0

    # Trend analysis — is it getting better or worse?
    trend = _detect_trend(results)

    # Base score from pass rate
    score = round(pass_rate * 70)

    detail_parts: list[str] = []
    detail_parts.append(f"{pass_count}/{total} recent audits passed")

    if trend == "stable_pass":
        score += 30
        detail_parts.append("consistently passing")
    elif trend == "improving":
        score += 25
        detail_parts.append("improving trend")
    elif trend == "stable_mixed":
        score += 10
        detail_parts.append("mixed but stable")
    elif trend == "oscillating":
        score += 5
        detail_parts.append("oscillating pass/fail")
    elif trend == "degrading":
        score += 0
        detail_parts.append("degrading trend")
    elif trend == "stable_fail":
        # Already penalised by low pass_rate
        detail_parts.append("consistently failing")

    # Latest result bonus
    if results and results[0] == "PASS":
        score += 5
        detail_parts.append("latest: PASS")

    return {
        "score": max(0, min(100, score)),
        "detail": "; ".join(detail_parts),
    }


def _score_build_completion(data: dict) -> dict[str, Any]:
    """Score: does the system reliably complete builds?

    Uses builds_total and current build status as signals.
    A high completion rate means the system doesn't abandon work.
    """
    builds_total = data.get("builds_total", 0)
    build = data.get("build")

    if builds_total == 0 and build is None:
        return {"score": 50, "detail": "No build history"}

    score = 0.0
    detail_parts: list[str] = []

    # Current build status
    if build:
        status = build.get("status", "unknown")
        if status == "completed":
            score += 60
            detail_parts.append("Latest build: completed")
        elif status == "building":
            score += 40
            detail_parts.append("Latest build: in progress")
        elif status == "paused":
            score += 35
            detail_parts.append("Latest build: paused")
        elif status == "error":
            score += 15
            detail_parts.append("Latest build: error")
        elif status == "cancelled":
            score += 10
            detail_parts.append("Latest build: cancelled")
        else:
            score += 20
            detail_parts.append(f"Latest build: {status}")
    else:
        detail_parts.append("No active build")
        score += 30

    # Build history depth — more builds = more data = more confidence
    if builds_total >= 5:
        score += 25
        detail_parts.append(f"{builds_total} builds in history (strong data)")
    elif builds_total >= 3:
        score += 20
        detail_parts.append(f"{builds_total} builds in history")
    elif builds_total >= 1:
        score += 15
        detail_parts.append(f"{builds_total} build(s) in history")
    else:
        detail_parts.append("No build history")

    # Stats-based completion evidence
    if build:
        stats = build.get("stats") or {}
        files_written = stats.get("files_written_count", 0)
        commits_made = stats.get("git_commits_made", 0)

        if files_written > 0 and commits_made > 0:
            score += 15
            detail_parts.append(f"{files_written} files, {commits_made} commits")
        elif files_written > 0:
            score += 10
            detail_parts.append(f"{files_written} files written")

    return {
        "score": max(0, min(100, round(score))),
        "detail": "; ".join(detail_parts),
    }


def _score_error_recovery(data: dict) -> dict[str, Any]:
    """Score: when errors occur, are they resolved gracefully?

    Looks at:
    - Build error_detail (None = no errors = good)
    - Loop count vs completion (high loops but completed = good recovery)
    - Governance fail → auto-fix patterns
    """
    build = data.get("build")
    gov = data.get("governance")

    if build is None:
        return {"score": 50, "detail": "No build data for error analysis"}

    score = 0.0
    detail_parts: list[str] = []
    status = build.get("status", "unknown")
    error_detail = build.get("error_detail")
    loop_count = build.get("loop_count", 0)

    # No errors at all — perfect
    if error_detail is None and status == "completed":
        score += 70
        detail_parts.append("No errors encountered")
    elif error_detail and status == "completed":
        # Had errors but recovered
        score += 55
        detail_parts.append("Errors encountered but build completed (recovered)")
    elif error_detail and status != "completed":
        # Errors and didn't recover
        score += 15
        detail_parts.append(f"Unrecovered error: {str(error_detail)[:80]}")
    else:
        score += 40
        detail_parts.append(f"Build status: {status}")

    # Loop recovery signal — completing after loops shows resilience
    if loop_count > 1 and status == "completed":
        recovery_bonus = min(20, loop_count * 3)
        score += recovery_bonus
        detail_parts.append(f"Recovered from {loop_count} fix loops")
    elif loop_count <= 1 and status == "completed":
        score += 15
        detail_parts.append("Clean execution, no recovery needed")

    # Governance error recovery — checks that failed then passed after fixes
    if gov:
        fail_count = gov.get("fail_count", 0)
        pass_count = gov.get("pass_count", 0)
        total = gov.get("total", 0)
        if total > 0 and fail_count == 0:
            score += 15
            detail_parts.append("All governance checks passing")
        elif total > 0 and pass_count > fail_count:
            score += 8
            detail_parts.append(f"Governance: {pass_count} pass, {fail_count} fail")

    return {
        "score": max(0, min(100, round(score))),
        "detail": "; ".join(detail_parts),
    }


def _score_test_stability(data: dict) -> dict[str, Any]:
    """Score: are tests consistently passing across runs?

    Uses Scout check data and audit runs to assess whether test results
    are stable (always green) or flaky (oscillating).
    """
    scout = data.get("scout")
    audit = data.get("audit", {})

    score = 0.0
    detail_parts: list[str] = []

    # Scout check pass rate as a stability signal
    if scout:
        checks_passed = scout.get("checks_passed", 0)
        checks_failed = scout.get("checks_failed", 0)
        checks_warned = scout.get("checks_warned", 0)
        total = checks_passed + checks_failed + checks_warned

        if total > 0:
            pass_rate = checks_passed / total
            score += round(pass_rate * 50)
            detail_parts.append(f"Scout: {checks_passed}/{total} checks passed")

            # Warnings are noise but not failures
            if checks_warned > 0:
                detail_parts.append(f"{checks_warned} warnings")
        else:
            score += 25
            detail_parts.append("Scout scan completed (no check data)")
    else:
        score += 20
        detail_parts.append("No Scout check data")

    # Audit consistency — multiple passing runs = stable tests
    recent_runs = audit.get("recent_runs", [])
    if len(recent_runs) >= 2:
        pass_results = [r for r in recent_runs if r.get("overall_result") == "PASS"]
        if len(pass_results) == len(recent_runs):
            score += 40
            detail_parts.append(f"All {len(recent_runs)} recent audits passed")
        elif len(pass_results) > len(recent_runs) * 0.7:
            score += 25
            detail_parts.append(f"{len(pass_results)}/{len(recent_runs)} recent audits passed")
        else:
            score += 10
            detail_parts.append(f"Only {len(pass_results)}/{len(recent_runs)} recent audits passed")
    elif len(recent_runs) == 1:
        if recent_runs[0].get("overall_result") == "PASS":
            score += 30
            detail_parts.append("Single audit run: PASS")
        else:
            score += 10
            detail_parts.append(f"Single audit run: {recent_runs[0].get('overall_result')}")
    else:
        score += 15
        detail_parts.append("No audit run data for stability analysis")

    # Quality score from dossier as bonus
    if scout and scout.get("quality_score") is not None:
        quality = scout["quality_score"]
        if quality >= 80:
            score += 10
            detail_parts.append(f"Quality: {quality}/100")

    return {
        "score": max(0, min(100, round(score))),
        "detail": "; ".join(detail_parts),
    }


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------


def _letter_grade(score: float) -> str:
    """Convert numeric score to letter grade."""
    if score >= 90:
        return "A"
    if score >= 80:
        return "B"
    if score >= 70:
        return "C"
    if score >= 60:
        return "D"
    return "F"


def _detect_trend(results: list[str]) -> str:
    """Detect the trend in a sequence of audit results (most recent first).

    Returns one of:
      - "stable_pass"   — all results are PASS
      - "stable_fail"   — all results are FAIL
      - "improving"     — failures early (older), passes recently
      - "degrading"     — passes early (older), failures recently
      - "oscillating"   — alternating pass/fail
      - "stable_mixed"  — no clear trend but consistent mix
    """
    if not results:
        return "stable_mixed"

    passes = [r == "PASS" for r in results]

    # All same
    if all(passes):
        return "stable_pass"
    if not any(passes):
        return "stable_fail"

    # Split into halves: recent (first half) vs older (second half)
    mid = max(1, len(passes) // 2)
    recent = passes[:mid]
    older = passes[mid:]

    recent_rate = sum(recent) / len(recent) if recent else 0
    older_rate = sum(older) / len(older) if older else 0

    # Improving: older has more failures, recent has more passes
    if recent_rate > older_rate + 0.3:
        return "improving"

    # Degrading: older has more passes, recent has more failures
    if older_rate > recent_rate + 0.3:
        return "degrading"

    # Oscillating: frequent alternation
    alternations = sum(1 for i in range(1, len(passes)) if passes[i] != passes[i - 1])
    if alternations >= len(passes) * 0.6:
        return "oscillating"

    return "stable_mixed"
