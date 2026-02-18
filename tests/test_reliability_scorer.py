"""Tests for reliability_scorer — stability and dependability measurement."""

import pytest

from app.services.reliability_scorer import (
    compute_reliability_score,
    _score_fix_loop_rate,
    _score_audit_trend,
    _score_build_completion,
    _score_error_recovery,
    _score_test_stability,
    _detect_trend,
    _letter_grade,
    _WEIGHTS,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _base_data(**overrides) -> dict:
    """Minimal valid CertificateData for reliability scoring."""
    base = {
        "project": {"id": "p-1", "name": "TestProject", "status": "active"},
        "build": {
            "id": "b-1",
            "status": "completed",
            "loop_count": 1,
            "phase": "implementation",
            "completed_phases": ["setup", "implementation", "testing"],
            "error_detail": None,
            "stats": {
                "files_written_count": 15,
                "git_commits_made": 8,
                "total_turns": 20,
            },
            "cost": {"total_cost_usd": 2.50},
        },
        "builds_total": 5,
        "audit": {
            "runs_total": 10,
            "recent_runs": [
                {"id": "r1", "overall_result": "PASS"},
                {"id": "r2", "overall_result": "PASS"},
                {"id": "r3", "overall_result": "PASS"},
                {"id": "r4", "overall_result": "PASS"},
                {"id": "r5", "overall_result": "PASS"},
            ],
            "pass_rate": 0.95,
            "latest_result": "PASS",
        },
        "governance": {
            "checks": [
                {"code": "A1", "name": "Safety", "result": "PASS"},
                {"code": "A2", "name": "Style", "result": "PASS"},
            ],
            "pass_count": 2,
            "fail_count": 0,
            "warn_count": 0,
            "total": 2,
        },
        "scout": {
            "stack_profile": {"primary_language": "Python"},
            "architecture": {},
            "quality_score": 85,
            "health_grade": "A",
            "checks_passed": 10,
            "checks_failed": 0,
            "checks_warned": 1,
            "files_analysed": 20,
            "tree_size": 30,
        },
        "contracts": {"count": 3, "types": ["phases"]},
    }
    base.update(overrides)
    return base


def _empty_data() -> dict:
    """Data with no build, no scout, no audit."""
    return {
        "project": {"id": "p-1", "name": "Empty"},
        "build": None,
        "builds_total": 0,
        "audit": {"runs_total": 0, "recent_runs": [], "pass_rate": 0.0, "latest_result": None},
        "governance": None,
        "scout": None,
        "contracts": {"count": 0, "types": []},
    }


# ---------------------------------------------------------------------------
# compute_reliability_score
# ---------------------------------------------------------------------------


class TestComputeReliabilityScore:
    def test_returns_all_keys(self):
        result = compute_reliability_score(_base_data())
        assert "score" in result
        assert "grade" in result
        assert "details" in result
        assert "sub_scores" in result

    def test_ideal_data_scores_high(self):
        result = compute_reliability_score(_base_data())
        assert result["score"] >= 80
        assert result["grade"] in ("A", "B")

    def test_empty_data_moderate(self):
        result = compute_reliability_score(_empty_data())
        assert result["score"] >= 20
        assert result["score"] <= 60

    def test_sub_scores_present(self):
        result = compute_reliability_score(_base_data())
        for key in _WEIGHTS:
            assert key in result["sub_scores"]
            assert "score" in result["sub_scores"][key]
            assert "detail" in result["sub_scores"][key]

    def test_weights_sum_to_one(self):
        total = sum(_WEIGHTS.values())
        assert abs(total - 1.0) < 0.001

    def test_details_readable(self):
        result = compute_reliability_score(_base_data())
        assert len(result["details"]) == len(_WEIGHTS)


# ---------------------------------------------------------------------------
# _score_fix_loop_rate
# ---------------------------------------------------------------------------


class TestFixLoopRate:
    def test_first_pass_perfect(self):
        result = _score_fix_loop_rate(_base_data())
        assert result["score"] == 100
        assert "first-pass" in result["detail"].lower()

    def test_two_loops(self):
        data = _base_data()
        data["build"]["loop_count"] = 2
        result = _score_fix_loop_rate(data)
        assert result["score"] == 90

    def test_three_loops(self):
        data = _base_data()
        data["build"]["loop_count"] = 3
        result = _score_fix_loop_rate(data)
        assert result["score"] == 80

    def test_five_loops(self):
        data = _base_data()
        data["build"]["loop_count"] = 5
        result = _score_fix_loop_rate(data)
        assert result["score"] >= 60 and result["score"] <= 80

    def test_many_loops_low_score(self):
        data = _base_data()
        data["build"]["loop_count"] = 15
        result = _score_fix_loop_rate(data)
        assert result["score"] < 30

    def test_no_build(self):
        result = _score_fix_loop_rate(_empty_data())
        assert result["score"] == 50

    def test_error_status_penalised(self):
        data = _base_data()
        data["build"]["status"] = "error"
        result = _score_fix_loop_rate(data)
        assert result["score"] == 30

    def test_high_loops_but_completed_bonus(self):
        data = _base_data()
        data["build"]["loop_count"] = 6
        result = _score_fix_loop_rate(data)
        assert "completed" in result["detail"].lower()


# ---------------------------------------------------------------------------
# _score_audit_trend
# ---------------------------------------------------------------------------


class TestAuditTrend:
    def test_all_passing(self):
        result = _score_audit_trend(_base_data())
        assert result["score"] >= 90
        assert "consistently" in result["detail"].lower()

    def test_all_failing(self):
        data = _base_data()
        data["audit"]["recent_runs"] = [
            {"id": "r1", "overall_result": "FAIL"},
            {"id": "r2", "overall_result": "FAIL"},
            {"id": "r3", "overall_result": "FAIL"},
        ]
        data["audit"]["pass_rate"] = 0
        result = _score_audit_trend(data)
        assert result["score"] < 30

    def test_improving_trend(self):
        data = _base_data()
        data["audit"]["recent_runs"] = [
            {"id": "r1", "overall_result": "PASS"},
            {"id": "r2", "overall_result": "PASS"},
            {"id": "r3", "overall_result": "FAIL"},
            {"id": "r4", "overall_result": "FAIL"},
        ]
        data["audit"]["pass_rate"] = 0.5
        result = _score_audit_trend(data)
        assert "improving" in result["detail"].lower()

    def test_degrading_trend(self):
        data = _base_data()
        data["audit"]["recent_runs"] = [
            {"id": "r1", "overall_result": "FAIL"},
            {"id": "r2", "overall_result": "FAIL"},
            {"id": "r3", "overall_result": "PASS"},
            {"id": "r4", "overall_result": "PASS"},
        ]
        data["audit"]["pass_rate"] = 0.5
        result = _score_audit_trend(data)
        assert "degrading" in result["detail"].lower()

    def test_no_audit_runs(self):
        result = _score_audit_trend(_empty_data())
        assert result["score"] == 50

    def test_single_pass(self):
        data = _base_data()
        data["audit"]["recent_runs"] = [{"id": "r1", "overall_result": "PASS"}]
        data["audit"]["pass_rate"] = 1.0
        result = _score_audit_trend(data)
        assert result["score"] >= 70


# ---------------------------------------------------------------------------
# _score_build_completion
# ---------------------------------------------------------------------------


class TestBuildCompletion:
    def test_completed_build_high(self):
        result = _score_build_completion(_base_data())
        assert result["score"] >= 80

    def test_no_builds(self):
        result = _score_build_completion(_empty_data())
        assert result["score"] == 50

    def test_error_build_penalised(self):
        data = _base_data()
        data["build"]["status"] = "error"
        result = _score_build_completion(data)
        passing = _score_build_completion(_base_data())
        assert result["score"] < passing["score"]

    def test_many_builds_bonus(self):
        data = _base_data()
        data["builds_total"] = 10
        result = _score_build_completion(data)
        assert result["score"] >= 80

    def test_files_and_commits_bonus(self):
        data = _base_data()
        data["build"]["stats"]["files_written_count"] = 20
        data["build"]["stats"]["git_commits_made"] = 10
        result = _score_build_completion(data)
        assert result["score"] >= 80


# ---------------------------------------------------------------------------
# _score_error_recovery
# ---------------------------------------------------------------------------


class TestErrorRecovery:
    def test_no_errors_perfect(self):
        result = _score_error_recovery(_base_data())
        assert result["score"] >= 85

    def test_error_but_recovered(self):
        data = _base_data()
        data["build"]["error_detail"] = "Some transient error"
        data["build"]["status"] = "completed"
        result = _score_error_recovery(data)
        assert result["score"] >= 55

    def test_unrecovered_error(self):
        data = _base_data()
        data["build"]["error_detail"] = "Fatal: out of memory"
        data["build"]["status"] = "error"
        result = _score_error_recovery(data)
        assert result["score"] < 40

    def test_no_build_data(self):
        result = _score_error_recovery(_empty_data())
        assert result["score"] == 50

    def test_loop_recovery_bonus(self):
        data = _base_data()
        data["build"]["loop_count"] = 5
        data["build"]["status"] = "completed"
        result = _score_error_recovery(data)
        assert "recovered" in result["detail"].lower()

    def test_governance_clean_bonus(self):
        data = _base_data()
        result = _score_error_recovery(data)
        assert "governance" in result["detail"].lower()


# ---------------------------------------------------------------------------
# _score_test_stability
# ---------------------------------------------------------------------------


class TestTestStability:
    def test_stable_tests_high(self):
        result = _score_test_stability(_base_data())
        assert result["score"] >= 80

    def test_no_data(self):
        result = _score_test_stability(_empty_data())
        assert result["score"] >= 20

    def test_all_audits_passing_bonus(self):
        result = _score_test_stability(_base_data())
        assert "all" in result["detail"].lower() or "passed" in result["detail"].lower()

    def test_failed_checks_lower(self):
        data = _base_data()
        data["scout"]["checks_passed"] = 3
        data["scout"]["checks_failed"] = 7
        data["scout"]["checks_warned"] = 0
        result = _score_test_stability(data)
        full = _score_test_stability(_base_data())
        assert result["score"] < full["score"]

    def test_mixed_audit_results(self):
        data = _base_data()
        data["audit"]["recent_runs"] = [
            {"id": "r1", "overall_result": "PASS"},
            {"id": "r2", "overall_result": "FAIL"},
            {"id": "r3", "overall_result": "PASS"},
        ]
        result = _score_test_stability(data)
        assert result["score"] < 90  # Not perfect


# ---------------------------------------------------------------------------
# _detect_trend
# ---------------------------------------------------------------------------


class TestDetectTrend:
    def test_all_pass(self):
        assert _detect_trend(["PASS", "PASS", "PASS", "PASS"]) == "stable_pass"

    def test_all_fail(self):
        assert _detect_trend(["FAIL", "FAIL", "FAIL", "FAIL"]) == "stable_fail"

    def test_improving(self):
        # Most recent first — recent PASS, old FAIL
        result = _detect_trend(["PASS", "PASS", "FAIL", "FAIL"])
        assert result == "improving"

    def test_degrading(self):
        # Most recent first — recent FAIL, old PASS
        result = _detect_trend(["FAIL", "FAIL", "PASS", "PASS"])
        assert result == "degrading"

    def test_oscillating(self):
        result = _detect_trend(["PASS", "FAIL", "PASS", "FAIL", "PASS"])
        assert result == "oscillating"

    def test_empty_list(self):
        assert _detect_trend([]) == "stable_mixed"

    def test_single_pass(self):
        assert _detect_trend(["PASS"]) == "stable_pass"

    def test_single_fail(self):
        assert _detect_trend(["FAIL"]) == "stable_fail"

    def test_mixed_no_clear_trend(self):
        # [PASS, PASS, FAIL, PASS] — mostly passing, no strong trend
        result = _detect_trend(["PASS", "PASS", "FAIL", "PASS"])
        assert result in ("stable_mixed", "improving", "degrading")


# ---------------------------------------------------------------------------
# _letter_grade
# ---------------------------------------------------------------------------


class TestLetterGrade:
    def test_a(self):
        assert _letter_grade(95) == "A"

    def test_b(self):
        assert _letter_grade(85) == "B"

    def test_c(self):
        assert _letter_grade(75) == "C"

    def test_d(self):
        assert _letter_grade(65) == "D"

    def test_f(self):
        assert _letter_grade(50) == "F"

    def test_boundary_90(self):
        assert _letter_grade(90) == "A"

    def test_boundary_80(self):
        assert _letter_grade(80) == "B"

    def test_boundary_70(self):
        assert _letter_grade(70) == "C"

    def test_boundary_60(self):
        assert _letter_grade(60) == "D"
