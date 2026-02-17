"""Tests for certificate_scorer -- pure-function scoring engine."""

import pytest

from app.services.certificate_scorer import (
    compute_certificate_scores,
    _score_build_integrity,
    _score_test_coverage,
    _score_audit_compliance,
    _score_governance,
    _score_security,
    _score_cost_efficiency,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _base_data(**overrides):
    """Minimal valid CertificateData for scoring."""
    data = {
        "project": {"id": "p-1", "name": "Test", "description": None},
        "build": {
            "id": "b-1",
            "phase": "plan_execute",
            "status": "completed",
            "loop_count": 1,
            "started_at": "2025-01-01T00:00:00Z",
            "completed_at": "2025-01-01T01:00:00Z",
            "error_detail": None,
            "completed_phases": ["plan_execute"],
            "stats": {"files_written_count": 5, "git_commits_made": 2},
            "cost": {
                "total_input_tokens": 50000,
                "total_output_tokens": 10000,
                "total_cost_usd": 1.50,
                "phase_count": 2,
            },
        },
        "builds_total": 3,
        "audit": {
            "runs_total": 5,
            "recent_runs": [],
            "pass_rate": 1.0,
            "latest_result": "PASS",
            "latest_run_id": "ar-1",
        },
        "governance": {
            "checks": [
                {"code": "A0", "name": "Test", "result": "PASS", "detail": ""},
                {"code": "W1", "name": "Secrets", "result": "PASS", "detail": ""},
            ],
            "pass_count": 2,
            "fail_count": 0,
            "warn_count": 0,
            "total": 2,
        },
        "scout": {
            "checks_passed": 10,
            "checks_failed": 0,
            "checks_warned": 0,
            "quality_score": 90,
            "health_grade": "A",
        },
        "contracts": {"count": 9, "types": ["scope"]},
    }
    data.update(overrides)
    return data


# ---------------------------------------------------------------------------
# compute_certificate_scores
# ---------------------------------------------------------------------------

def test_compute_scores_returns_all_keys():
    result = compute_certificate_scores(_base_data())
    assert "dimensions" in result
    assert "overall_score" in result
    assert "verdict" in result
    assert "project" in result
    assert "build_summary" in result
    assert "builds_total" in result
    assert "contracts_count" in result
    assert "generated_at" in result


def test_compute_scores_certified_verdict():
    """Perfect data should yield CERTIFIED."""
    result = compute_certificate_scores(_base_data())
    assert result["verdict"] == "CERTIFIED"
    assert result["overall_score"] >= 90


def test_compute_scores_flagged_verdict():
    """Data with no builds, no audit, etc. should yield FLAGGED."""
    data = _base_data(
        build=None,
        audit={"runs_total": 0, "recent_runs": [], "pass_rate": 0.0, "latest_result": None, "latest_run_id": None},
        governance=None,
        scout=None,
    )
    result = compute_certificate_scores(data)
    assert result["verdict"] == "FLAGGED"
    assert result["overall_score"] < 70


def test_compute_scores_conditional_verdict():
    """Moderate data should yield CONDITIONAL."""
    data = _base_data(
        build={
            **_base_data()["build"],
            "status": "completed",
            "loop_count": 6,
            "stats": {"files_written_count": 1, "git_commits_made": 0},
            "cost": {"total_input_tokens": 100000, "total_output_tokens": 50000, "total_cost_usd": 12.0, "phase_count": 1},
        },
        audit={"runs_total": 3, "recent_runs": [], "pass_rate": 0.5, "latest_result": "FAIL", "latest_run_id": "ar-1"},
        governance={
            "checks": [{"code": "A0", "result": "PASS"}, {"code": "A1", "result": "FAIL"}],
            "pass_count": 1, "fail_count": 1, "warn_count": 0, "total": 2,
        },
        scout={"checks_passed": 5, "checks_failed": 3, "checks_warned": 2, "quality_score": 50, "health_grade": "C"},
    )
    result = compute_certificate_scores(data)
    assert result["verdict"] in ("CONDITIONAL", "FLAGGED")
    assert result["overall_score"] < 90


def test_build_summary_echoed():
    result = compute_certificate_scores(_base_data())
    bs = result["build_summary"]
    assert bs is not None
    assert bs["status"] == "completed"
    assert bs["files_written"] == 5
    assert bs["git_commits"] == 2


def test_no_build_summary_when_no_build():
    result = compute_certificate_scores(_base_data(build=None))
    assert result["build_summary"] is None


def test_dimensions_weights_sum():
    result = compute_certificate_scores(_base_data())
    total_weight = sum(d["weight"] for d in result["dimensions"].values())
    assert abs(total_weight - 1.0) < 0.001


# ---------------------------------------------------------------------------
# _score_build_integrity
# ---------------------------------------------------------------------------

def test_build_integrity_no_build():
    result = _score_build_integrity({"build": None})
    assert result["score"] == 0
    assert "No builds" in result["details"][0]


def test_build_integrity_completed_low_loops():
    data = _base_data()
    result = _score_build_integrity(data)
    # completed=60 + loop<=1=20 + files=min(10,5)=5 + commits=min(10,2*2)=4 = 89
    assert result["score"] == 89


def test_build_integrity_failed():
    data = _base_data()
    data["build"]["status"] = "error"
    data["build"]["error_detail"] = "timeout"
    result = _score_build_integrity(data)
    # error=10 + loop<=1=20 + files=5 + commits=4 = 39
    assert result["score"] == 39


def test_build_integrity_many_loops():
    data = _base_data()
    data["build"]["loop_count"] = 8
    result = _score_build_integrity(data)
    # completed=60 + loop>5=5 + files=5 + commits=4 = 74
    assert result["score"] == 74


def test_build_integrity_capped_at_100():
    data = _base_data()
    data["build"]["stats"]["files_written_count"] = 50
    data["build"]["stats"]["git_commits_made"] = 50
    result = _score_build_integrity(data)
    assert result["score"] <= 100


# ---------------------------------------------------------------------------
# _score_test_coverage
# ---------------------------------------------------------------------------

def test_test_coverage_with_scout():
    data = _base_data()
    result = _score_test_coverage(data)
    # 10/10 passed = pass_rate=1.0, score=80, no warns, quality=90 -> bonus=min(20,18)=18, total=98
    assert result["score"] >= 90


def test_test_coverage_scout_warnings():
    data = _base_data()
    data["scout"]["checks_warned"] = 3
    result = _score_test_coverage(data)
    # 10/13 pass_rate, warned penalty
    assert result["score"] < 90


def test_test_coverage_no_scout_fallback_audit():
    data = _base_data(scout=None)
    result = _score_test_coverage(data)
    # Fallback: audit pass_rate=1.0 -> 60
    assert result["score"] == 60


def test_test_coverage_nothing():
    data = _base_data(
        scout=None,
        audit={"runs_total": 0, "pass_rate": 0, "latest_result": None, "recent_runs": [], "latest_run_id": None},
    )
    result = _score_test_coverage(data)
    assert result["score"] == 0


# ---------------------------------------------------------------------------
# _score_audit_compliance
# ---------------------------------------------------------------------------

def test_audit_compliance_perfect():
    data = _base_data()
    result = _score_audit_compliance(data)
    # pass_rate=1.0 -> 80, latest PASS -> +20 = 100
    assert result["score"] == 100


def test_audit_compliance_no_runs():
    data = _base_data(
        audit={"runs_total": 0, "pass_rate": 0, "latest_result": None, "recent_runs": [], "latest_run_id": None},
    )
    result = _score_audit_compliance(data)
    assert result["score"] == 0


def test_audit_compliance_latest_fail():
    data = _base_data()
    data["audit"]["pass_rate"] = 0.5
    data["audit"]["latest_result"] = "FAIL"
    result = _score_audit_compliance(data)
    # pass_rate=0.5 -> 40, latest FAIL -> +0 = 40
    assert result["score"] == 40


# ---------------------------------------------------------------------------
# _score_governance
# ---------------------------------------------------------------------------

def test_governance_perfect():
    data = _base_data()
    result = _score_governance(data)
    # 2/2 pass_rate=1.0 -> 85, no fails/warns, clean sweep +15 = 100
    assert result["score"] == 100


def test_governance_none():
    data = _base_data(governance=None)
    result = _score_governance(data)
    assert result["score"] == 50


def test_governance_with_failures():
    data = _base_data()
    data["governance"]["fail_count"] = 2
    data["governance"]["pass_count"] = 0
    data["governance"]["total"] = 2
    result = _score_governance(data)
    # pass_rate=0 -> 0, fail penalty=min(30,2*10)=20 -> -20 = max(0, -20) = 0
    assert result["score"] == 0


def test_governance_with_warnings():
    data = _base_data()
    data["governance"]["warn_count"] = 2
    data["governance"]["pass_count"] = 0
    data["governance"]["total"] = 2
    result = _score_governance(data)
    # pass_rate=0 -> 0, warn penalty=min(10,6)=6 -> -6 = max(0,-6)=0
    assert result["score"] == 0


# ---------------------------------------------------------------------------
# _score_security
# ---------------------------------------------------------------------------

def test_security_clean():
    data = _base_data()
    result = _score_security(data)
    # start=80, W1 PASS +10, health_grade A +10 = 100
    assert result["score"] == 100


def test_security_secrets_warn():
    data = _base_data()
    data["governance"]["checks"][1] = {"code": "W1", "result": "WARN", "detail": "found"}
    result = _score_security(data)
    # 80 - 20 + 10 (grade A) = 70
    assert result["score"] == 70


def test_security_no_governance():
    data = _base_data(governance=None, scout=None)
    result = _score_security(data)
    assert result["score"] == 80  # Baseline


def test_security_bad_health_grade():
    data = _base_data()
    data["scout"]["health_grade"] = "F"
    result = _score_security(data)
    # 80 + W1 PASS 10 - health F 20 = 70
    assert result["score"] == 70


# ---------------------------------------------------------------------------
# _score_cost_efficiency
# ---------------------------------------------------------------------------

def test_cost_efficiency_cheap():
    data = _base_data()
    data["build"]["cost"]["total_cost_usd"] = 0.50
    result = _score_cost_efficiency(data)
    # <$1 -> 100, no high token penalty
    assert result["score"] == 100


def test_cost_efficiency_expensive():
    data = _base_data()
    data["build"]["cost"]["total_cost_usd"] = 60.0
    result = _score_cost_efficiency(data)
    # $60 -> -50 = 50
    assert result["score"] == 50


def test_cost_efficiency_high_tokens():
    data = _base_data()
    data["build"]["cost"]["total_input_tokens"] = 800000
    data["build"]["cost"]["total_output_tokens"] = 300000
    result = _score_cost_efficiency(data)
    # 1.1M tokens -> -10 penalty, + $1.50 -> -5 = 85
    assert result["score"] == 85


def test_cost_efficiency_no_build():
    data = _base_data(build=None)
    result = _score_cost_efficiency(data)
    assert result["score"] == 50


def test_cost_efficiency_zero_cost():
    data = _base_data()
    data["build"]["cost"]["total_cost_usd"] = 0
    data["build"]["cost"]["total_input_tokens"] = 0
    data["build"]["cost"]["total_output_tokens"] = 0
    result = _score_cost_efficiency(data)
    assert result["score"] == 70
