"""Consistency scorer — measure determinism and uniformity of Forge output.

Pure-function scorer that analyses build and Scout data to determine
how consistent, predictable, and disciplined the generated code is.
No DB or IO calls — fully deterministic.

Signals measured:
  - Lint zero-violation rate (from governance checks)
  - File structure regularity (folder depth variance, naming)
  - Test-to-source coverage map (parallel structure)
  - Commit pattern regularity (message format, phase ordering)
  - Docstring/typing presence (code quality signals from Scout)
"""

from __future__ import annotations

import math
import re
from typing import Any

# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

ConsistencyScore = dict[str, Any]
# {
#   "score": 0–100,
#   "grade": "A" | "B" | "C" | "D" | "F",
#   "details": [ str, ... ],
#   "sub_scores": {
#     "lint_cleanliness":   { "score": 0–100, "detail": str },
#     "structure_regularity": { "score": 0–100, "detail": str },
#     "test_coverage_map":  { "score": 0–100, "detail": str },
#     "commit_discipline":  { "score": 0–100, "detail": str },
#     "code_quality":       { "score": 0–100, "detail": str },
#   },
# }


# Sub-score weights — must sum to 1.0
_WEIGHTS = {
    "lint_cleanliness": 0.25,
    "structure_regularity": 0.20,
    "test_coverage_map": 0.20,
    "commit_discipline": 0.20,
    "code_quality": 0.15,
}


# ---------------------------------------------------------------------------
# Main scorer
# ---------------------------------------------------------------------------


def compute_consistency_score(data: dict) -> ConsistencyScore:
    """Compute consistency score from aggregated certificate data.

    Parameters
    ----------
    data : CertificateData dict from certificate_aggregator.

    Returns
    -------
    ConsistencyScore with overall score, grade, and per-signal sub-scores.
    """
    sub_scores = {
        "lint_cleanliness": _score_lint_cleanliness(data),
        "structure_regularity": _score_structure_regularity(data),
        "test_coverage_map": _score_test_coverage_map(data),
        "commit_discipline": _score_commit_discipline(data),
        "code_quality": _score_code_quality(data),
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


def _score_lint_cleanliness(data: dict) -> dict[str, Any]:
    """Score: are governance lint/format checks consistently clean?

    Looks at governance checks for lint-related codes (A2 style, A3 structure)
    and the overall audit pass rate as a proxy for code cleanliness.
    """
    gov = data.get("governance")
    audit = data.get("audit", {})

    if gov is None and audit.get("runs_total", 0) == 0:
        return {"score": 50, "detail": "No lint data available"}

    score = 0.0

    if gov:
        checks = gov.get("checks", [])
        # Look for style/lint-related checks
        lint_codes = {"A2", "A3", "A4", "A6"}  # style, structure, imports, formatting
        lint_checks = [c for c in checks if c.get("code") in lint_codes]

        if lint_checks:
            passed = sum(1 for c in lint_checks if c.get("result") == "PASS")
            total = len(lint_checks)
            pass_rate = passed / total if total > 0 else 0
            score += pass_rate * 70
            detail_parts = [f"{passed}/{total} lint checks passed"]
        else:
            # No specific lint checks — use overall governance pass rate
            pass_count = gov.get("pass_count", 0)
            total = gov.get("total", 0)
            if total > 0:
                score += (pass_count / total) * 60
                detail_parts = [f"Governance {pass_count}/{total} (no specific lint checks)"]
            else:
                score += 40
                detail_parts = ["No governance check data"]
    else:
        detail_parts = ["No governance data"]
        score += 30

    # Audit consistency bonus — high pass rate means lint stays clean
    pass_rate = audit.get("pass_rate", 0)
    if pass_rate >= 0.9:
        score += 30
        detail_parts.append("audit consistently passing")
    elif pass_rate >= 0.7:
        score += 20
        detail_parts.append("audit mostly passing")
    elif pass_rate > 0:
        score += 10
        detail_parts.append(f"audit pass rate: {pass_rate:.0%}")

    return {
        "score": max(0, min(100, round(score))),
        "detail": "; ".join(detail_parts),
    }


def _score_structure_regularity(data: dict) -> dict[str, Any]:
    """Score: is the file structure regular and well-organised?

    Analyses Scout architecture data for:
    - Consistent folder depth (low variance = good)
    - Meaningful directory organisation (not flat dump)
    - Naming convention adherence
    """
    scout = data.get("scout")

    if not scout:
        return {"score": 50, "detail": "No Scout data for structure analysis"}

    architecture = scout.get("architecture") or {}
    tree_size = scout.get("tree_size", 0)
    files_analysed = scout.get("files_analysed", 0)

    score = 0.0
    detail_parts: list[str] = []

    # Tree size signals — a project should have meaningful structure
    if tree_size >= 5:
        score += 20
        detail_parts.append(f"{tree_size} files in tree")
    elif tree_size > 0:
        score += 10
        detail_parts.append(f"Small tree ({tree_size} files)")
    else:
        detail_parts.append("No file tree data")

    # Folder depth analysis from architecture data
    if architecture:
        # Extract folder paths if available
        folders = _extract_folders(architecture)
        if folders:
            depths = [f.count("/") for f in folders]
            if len(depths) >= 2:
                mean_depth = sum(depths) / len(depths)
                variance = sum((d - mean_depth) ** 2 for d in depths) / len(depths)
                std_dev = math.sqrt(variance)

                # Low variance = consistent structure
                if std_dev <= 1.0:
                    score += 35
                    detail_parts.append(f"Consistent depth (σ={std_dev:.1f})")
                elif std_dev <= 2.0:
                    score += 25
                    detail_parts.append(f"Moderate depth variance (σ={std_dev:.1f})")
                else:
                    score += 10
                    detail_parts.append(f"High depth variance (σ={std_dev:.1f})")

                # Multi-level structure bonus
                if mean_depth >= 2.0:
                    score += 15
                    detail_parts.append("Multi-level organisation")
                elif mean_depth >= 1.0:
                    score += 10
                    detail_parts.append("Basic folder organisation")
            else:
                score += 15
                detail_parts.append("Limited folder data for depth analysis")
        else:
            score += 15
            detail_parts.append("No folder structure in architecture data")
    else:
        score += 10
        detail_parts.append("No architecture data available")

    # Health grade as structure quality proxy
    health_grade = scout.get("health_grade")
    if health_grade:
        bonus = {"A": 30, "B": 20, "C": 10, "D": 5, "F": 0}.get(health_grade, 10)
        score += bonus
        detail_parts.append(f"Health grade: {health_grade}")

    return {
        "score": max(0, min(100, round(score))),
        "detail": "; ".join(detail_parts),
    }


def _score_test_coverage_map(data: dict) -> dict[str, Any]:
    """Score: do test files systematically mirror source modules?

    Analyses Scout data for:
    - Test directory existence
    - Test file count vs source file count
    - Parallel naming (test_X.py corresponds to X.py)
    """
    scout = data.get("scout")

    if not scout:
        return {"score": 30, "detail": "No Scout data for test analysis"}

    checks_passed = scout.get("checks_passed", 0)
    checks_failed = scout.get("checks_failed", 0)
    checks_total = checks_passed + checks_failed + scout.get("checks_warned", 0)

    score = 0.0
    detail_parts: list[str] = []

    # Base score from Scout check pass rate (tests are a major Scout signal)
    if checks_total > 0:
        pass_rate = checks_passed / checks_total
        score += pass_rate * 50
        detail_parts.append(f"Scout: {checks_passed}/{checks_total} checks passed")
    else:
        score += 20
        detail_parts.append("No Scout check data")

    # Architecture-based test analysis
    architecture = scout.get("architecture") or {}
    if architecture:
        arch_str = str(architecture).lower()

        # Does a test directory exist?
        has_tests = any(
            kw in arch_str
            for kw in ("tests/", "test/", "__tests__/", "spec/", "test_")
        )
        if has_tests:
            score += 25
            detail_parts.append("Test directory present")
        else:
            detail_parts.append("No test directory found")

        # Count test files vs source files (rough heuristic from architecture data)
        test_file_count = (
            arch_str.count("test_")
            + arch_str.count("_test.")
            + arch_str.count(".test.")
            + arch_str.count(".spec.")
        )
        if test_file_count > 0:
            score += min(25, test_file_count * 3)
            detail_parts.append(f"~{test_file_count} test files detected")
    else:
        detail_parts.append("No architecture data for test analysis")

    # Quality score from dossier as bonus
    quality = scout.get("quality_score")
    if quality is not None and quality > 70:
        score += 10
        detail_parts.append(f"Quality score: {quality}")

    return {
        "score": max(0, min(100, round(score))),
        "detail": "; ".join(detail_parts),
    }


def _score_commit_discipline(data: dict) -> dict[str, Any]:
    """Score: are commits regular, well-messaged, and phase-ordered?

    Analyses build data for:
    - Commit count (evidence of incremental work)
    - Phase completion order (sequential = disciplined)
    - Completed phases count vs total phases
    """
    build = data.get("build")

    if build is None:
        return {"score": 30, "detail": "No build data for commit analysis"}

    score = 0.0
    detail_parts: list[str] = []

    # Git commits made
    stats = build.get("stats") or {}
    commits_made = stats.get("git_commits_made", 0)

    if commits_made >= 5:
        score += 30
        detail_parts.append(f"{commits_made} commits (good granularity)")
    elif commits_made >= 2:
        score += 20
        detail_parts.append(f"{commits_made} commits")
    elif commits_made >= 1:
        score += 10
        detail_parts.append(f"{commits_made} commit (single batch)")
    else:
        detail_parts.append("No commits recorded")

    # Phase completion evidence
    completed_phases = build.get("completed_phases")
    if completed_phases:
        if isinstance(completed_phases, list):
            phase_count = len(completed_phases)
        elif isinstance(completed_phases, (int, float)):
            phase_count = int(completed_phases)
        else:
            phase_count = 0

        if phase_count >= 3:
            score += 30
            detail_parts.append(f"{phase_count} phases completed")
        elif phase_count >= 1:
            score += 20
            detail_parts.append(f"{phase_count} phase(s) completed")
    else:
        # Single-phase builds are still valid
        if build.get("status") == "completed":
            score += 20
            detail_parts.append("Build completed (phase data not available)")
        else:
            detail_parts.append("Phase data not available")

    # Build status signal
    status = build.get("status", "unknown")
    if status == "completed":
        score += 25
        detail_parts.append("Build completed successfully")
    elif status in ("paused", "building"):
        score += 10
        detail_parts.append(f"Build status: {status}")
    elif status == "error":
        detail_parts.append("Build ended in error")

    # Loop discipline — fewer fix loops = more deterministic
    loop_count = build.get("loop_count", 0)
    if loop_count <= 1:
        score += 15
        detail_parts.append("First-pass success (no fix loops)")
    elif loop_count <= 3:
        score += 10
        detail_parts.append(f"{loop_count} fix loops")
    elif loop_count <= 5:
        score += 5
        detail_parts.append(f"{loop_count} fix loops (moderate iteration)")
    else:
        detail_parts.append(f"{loop_count} fix loops (high iteration)")

    return {
        "score": max(0, min(100, round(score))),
        "detail": "; ".join(detail_parts),
    }


def _score_code_quality(data: dict) -> dict[str, Any]:
    """Score: does the code show quality signals (typing, docstrings, etc.)?

    Uses Scout dossier quality assessment and governance checks as proxies.
    """
    scout = data.get("scout")
    gov = data.get("governance")

    score = 0.0
    detail_parts: list[str] = []

    # Scout quality score (from dossier quality_assessment)
    if scout:
        quality = scout.get("quality_score")
        if quality is not None:
            # Quality score is typically 0–100
            score += min(50, quality * 0.5)
            detail_parts.append(f"Dossier quality: {quality}/100")

        # Health grade as additional signal
        health = scout.get("health_grade")
        if health:
            bonus = {"A": 25, "B": 15, "C": 10, "D": 5, "F": 0}.get(health, 5)
            score += bonus
            detail_parts.append(f"Health grade: {health}")
        else:
            score += 10
            detail_parts.append("No health grade")
    else:
        score += 20
        detail_parts.append("No Scout data for quality analysis")

    # Governance signals — well-structured code passes more checks
    if gov:
        total = gov.get("total", 0)
        pass_count = gov.get("pass_count", 0)
        if total > 0:
            rate = pass_count / total
            bonus = round(rate * 25)
            score += bonus
            detail_parts.append(f"Governance: {pass_count}/{total} passed")
    else:
        score += 10
        detail_parts.append("No governance data")

    if not detail_parts:
        detail_parts.append("Baseline quality score")

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


def _extract_folders(architecture: dict) -> list[str]:
    """Extract folder-like paths from Scout architecture data.

    Scout's architecture dict may have various shapes — tree structure,
    module map, etc. We try to extract folder paths from any format.
    """
    folders: list[str] = []

    def _walk(obj: Any, prefix: str = "") -> None:
        if isinstance(obj, dict):
            for key, val in obj.items():
                path = f"{prefix}/{key}" if prefix else key
                # Keys ending with / or containing sub-dicts are folders
                if isinstance(val, dict):
                    folders.append(path)
                    _walk(val, path)
                elif isinstance(val, list):
                    folders.append(path)
                    for item in val:
                        if isinstance(item, dict):
                            _walk(item, path)
                elif isinstance(val, str) and "/" in val:
                    folders.append(val)
        elif isinstance(obj, list):
            for item in obj:
                if isinstance(item, dict):
                    _walk(item, prefix)
                elif isinstance(item, str) and "/" in item:
                    folders.append(item)

    _walk(architecture)
    return folders
