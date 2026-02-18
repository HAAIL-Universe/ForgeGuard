"""Tests for consistency_scorer — determinism and uniformity measurement."""

import pytest

from app.services.consistency_scorer import (
    compute_consistency_score,
    _score_lint_cleanliness,
    _score_structure_regularity,
    _score_test_coverage_map,
    _score_commit_discipline,
    _score_code_quality,
    _extract_folders,
    _letter_grade,
    _WEIGHTS,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _base_data(**overrides) -> dict:
    """Minimal valid CertificateData for consistency scoring."""
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
        "builds_total": 3,
        "audit": {
            "runs_total": 10,
            "recent_runs": [
                {"id": "r1", "overall_result": "PASS"},
                {"id": "r2", "overall_result": "PASS"},
                {"id": "r3", "overall_result": "PASS"},
            ],
            "pass_rate": 0.95,
            "latest_result": "PASS",
        },
        "governance": {
            "checks": [
                {"code": "A2", "name": "Style", "result": "PASS"},
                {"code": "A3", "name": "Structure", "result": "PASS"},
                {"code": "A4", "name": "Imports", "result": "PASS"},
                {"code": "A6", "name": "Formatting", "result": "PASS"},
                {"code": "A1", "name": "Safety", "result": "PASS"},
            ],
            "pass_count": 5,
            "fail_count": 0,
            "warn_count": 0,
            "total": 5,
        },
        "scout": {
            "stack_profile": {"primary_language": "Python"},
            "architecture": {
                "app": {
                    "services": {"build.py": {}},
                    "repos": {"db.py": {}},
                },
                "tests": {
                    "test_build.py": {},
                    "test_db.py": {},
                },
            },
            "quality_score": 85,
            "health_grade": "A",
            "checks_passed": 10,
            "checks_failed": 0,
            "checks_warned": 1,
            "files_analysed": 20,
            "tree_size": 30,
        },
        "contracts": {"count": 3, "types": ["phases", "architecture", "standards"]},
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
# compute_consistency_score
# ---------------------------------------------------------------------------


class TestComputeConsistencyScore:
    def test_returns_all_keys(self):
        result = compute_consistency_score(_base_data())
        assert "score" in result
        assert "grade" in result
        assert "details" in result
        assert "sub_scores" in result

    def test_ideal_data_scores_high(self):
        result = compute_consistency_score(_base_data())
        assert result["score"] >= 70
        assert result["grade"] in ("A", "B", "C")

    def test_empty_data_scores_moderate(self):
        result = compute_consistency_score(_empty_data())
        assert result["score"] >= 20  # Not zero — baseline scores
        assert result["score"] <= 60

    def test_sub_scores_present(self):
        result = compute_consistency_score(_base_data())
        for key in _WEIGHTS:
            assert key in result["sub_scores"]
            assert "score" in result["sub_scores"][key]
            assert "detail" in result["sub_scores"][key]

    def test_details_readable(self):
        result = compute_consistency_score(_base_data())
        assert len(result["details"]) == len(_WEIGHTS)
        for d in result["details"]:
            assert "/100" in d

    def test_weights_sum_to_one(self):
        total = sum(_WEIGHTS.values())
        assert abs(total - 1.0) < 0.001


# ---------------------------------------------------------------------------
# _score_lint_cleanliness
# ---------------------------------------------------------------------------


class TestLintCleanliness:
    def test_all_lint_checks_pass(self):
        result = _score_lint_cleanliness(_base_data())
        assert result["score"] >= 80

    def test_no_governance_data(self):
        data = _base_data(governance=None)
        result = _score_lint_cleanliness(data)
        assert result["score"] >= 30  # Baseline from audit

    def test_no_governance_no_audit(self):
        result = _score_lint_cleanliness(_empty_data())
        assert result["score"] == 50

    def test_failed_lint_checks_lower_score(self):
        data = _base_data()
        data["governance"]["checks"] = [
            {"code": "A2", "name": "Style", "result": "FAIL"},
            {"code": "A3", "name": "Structure", "result": "FAIL"},
        ]
        data["governance"]["pass_count"] = 0
        data["governance"]["fail_count"] = 2
        result = _score_lint_cleanliness(data)
        passing_result = _score_lint_cleanliness(_base_data())
        assert result["score"] < passing_result["score"]

    def test_high_audit_pass_rate_bonus(self):
        data = _base_data()
        data["audit"]["pass_rate"] = 0.95
        result = _score_lint_cleanliness(data)
        assert result["score"] >= 80


# ---------------------------------------------------------------------------
# _score_structure_regularity
# ---------------------------------------------------------------------------


class TestStructureRegularity:
    def test_structured_project(self):
        result = _score_structure_regularity(_base_data())
        assert result["score"] >= 50

    def test_no_scout_data(self):
        result = _score_structure_regularity(_empty_data())
        assert result["score"] == 50

    def test_health_grade_affects_score(self):
        data_a = _base_data()
        data_a["scout"]["health_grade"] = "A"
        data_f = _base_data()
        data_f["scout"]["health_grade"] = "F"
        score_a = _score_structure_regularity(data_a)["score"]
        score_f = _score_structure_regularity(data_f)["score"]
        assert score_a > score_f

    def test_large_tree_bonus(self):
        data = _base_data()
        data["scout"]["tree_size"] = 100
        result = _score_structure_regularity(data)
        assert result["score"] >= 40


# ---------------------------------------------------------------------------
# _score_test_coverage_map
# ---------------------------------------------------------------------------


class TestTestCoverageMap:
    def test_good_coverage(self):
        result = _score_test_coverage_map(_base_data())
        assert result["score"] >= 60

    def test_no_scout_data(self):
        result = _score_test_coverage_map(_empty_data())
        assert result["score"] == 30

    def test_zero_checks(self):
        data = _base_data()
        data["scout"]["checks_passed"] = 0
        data["scout"]["checks_failed"] = 0
        data["scout"]["checks_warned"] = 0
        result = _score_test_coverage_map(data)
        assert result["score"] <= 70

    def test_high_quality_bonus(self):
        data = _base_data()
        data["scout"]["quality_score"] = 95
        result = _score_test_coverage_map(data)
        assert result["score"] >= 60


# ---------------------------------------------------------------------------
# _score_commit_discipline
# ---------------------------------------------------------------------------


class TestCommitDiscipline:
    def test_good_discipline(self):
        result = _score_commit_discipline(_base_data())
        assert result["score"] >= 80

    def test_no_build_data(self):
        result = _score_commit_discipline(_empty_data())
        assert result["score"] == 30

    def test_few_commits_lower_score(self):
        data = _base_data()
        data["build"]["stats"]["git_commits_made"] = 1
        result = _score_commit_discipline(data)
        full = _score_commit_discipline(_base_data())
        assert result["score"] <= full["score"]

    def test_error_status_penalised(self):
        data = _base_data()
        data["build"]["status"] = "error"
        result = _score_commit_discipline(data)
        assert result["score"] < 80

    def test_high_loop_count_penalised(self):
        data = _base_data()
        data["build"]["loop_count"] = 10
        result = _score_commit_discipline(data)
        ok = _score_commit_discipline(_base_data())
        assert result["score"] < ok["score"]

    def test_completed_phases_list(self):
        data = _base_data()
        data["build"]["completed_phases"] = ["a", "b", "c", "d"]
        result = _score_commit_discipline(data)
        assert result["score"] >= 70

    def test_completed_phases_int(self):
        data = _base_data()
        data["build"]["completed_phases"] = 5
        result = _score_commit_discipline(data)
        assert result["score"] >= 70


# ---------------------------------------------------------------------------
# _score_code_quality
# ---------------------------------------------------------------------------


class TestCodeQuality:
    def test_good_quality(self):
        result = _score_code_quality(_base_data())
        assert result["score"] >= 60

    def test_no_data(self):
        result = _score_code_quality(_empty_data())
        assert result["score"] >= 20

    def test_high_quality_score(self):
        data = _base_data()
        data["scout"]["quality_score"] = 100
        data["scout"]["health_grade"] = "A"
        result = _score_code_quality(data)
        assert result["score"] >= 70

    def test_low_quality_score(self):
        data = _base_data()
        data["scout"]["quality_score"] = 20
        data["scout"]["health_grade"] = "F"
        result = _score_code_quality(data)
        high = _score_code_quality(_base_data())
        assert result["score"] < high["score"]


# ---------------------------------------------------------------------------
# _extract_folders
# ---------------------------------------------------------------------------


class TestExtractFolders:
    def test_extracts_from_nested_dict(self):
        arch = {"app": {"services": {}, "repos": {}}}
        folders = _extract_folders(arch)
        assert len(folders) >= 2

    def test_extracts_from_string_paths(self):
        arch = {"files": ["src/index.ts", "src/utils/helpers.ts"]}
        folders = _extract_folders(arch)
        assert len(folders) >= 1

    def test_empty_architecture(self):
        folders = _extract_folders({})
        assert folders == []

    def test_handles_list_of_dicts(self):
        arch = {"modules": [{"name": "app/services"}, {"name": "app/repos"}]}
        folders = _extract_folders(arch)
        assert len(folders) >= 1


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
