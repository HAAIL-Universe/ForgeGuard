"""Tests for architecture_baseline — baseline capture, signal extraction, and comparison."""

import json
import pytest
from pathlib import Path
from unittest.mock import patch

from app.services.architecture_baseline import (
    ARCHITECTURE_RULES,
    capture_baseline,
    compare_against_baseline,
    detect_stack_key,
    extract_structural_signals,
    load_baseline,
    save_baseline,
    _check_layer_separation,
    _check_service_layer,
    _check_data_access_layer,
    _check_client_abstraction,
    _check_config_externalisation,
    _check_error_types,
    _check_test_parallel,
    _check_test_coverage_ratio,
    _check_middleware_crosscut,
    _empty_signals,
    _FORGEGUARD_FALLBACK_SIGNALS,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _scout_data(**overrides) -> dict:
    """Minimal Scout data resembling a well-structured Python project."""
    base = {
        "stack_profile": {"primary_language": "Python", "frameworks": ["FastAPI"]},
        "architecture": {
            "app": {
                "api": {"routers": {}},
                "services": {"build_service.py": {}, "audit_service.py": {}},
                "repos": {"build_repo.py": {}, "project_repo.py": {}},
                "clients": {"git_client.py": {}, "llm_client.py": {}},
                "middleware": {"auth.py": {}, "rate_limit.py": {}},
                "config.py": {},
                "errors.py": {},
            },
            "tests": {
                "test_build_service.py": {},
                "test_audit_service.py": {},
                "test_build_repo.py": {},
                "conftest.py": {},
            },
        },
        "dossier_available": True,
        "quality_score": 85,
        "health_grade": "A",
        "checks_passed": 12,
        "checks_failed": 0,
        "checks_warned": 1,
        "files_analysed": 30,
        "tree_size": 45,
    }
    base.update(overrides)
    return base


def _minimal_scout(**overrides) -> dict:
    """Bare-minimum Scout data (e.g. small project)."""
    base = {
        "stack_profile": {"primary_language": "JavaScript"},
        "architecture": {"src": {"index.js": {}}},
        "quality_score": None,
        "health_grade": None,
        "checks_passed": 0,
        "checks_failed": 0,
        "checks_warned": 0,
        "files_analysed": 3,
        "tree_size": 5,
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# detect_stack_key
# ---------------------------------------------------------------------------


class TestDetectStackKey:
    def test_python_detected(self):
        assert detect_stack_key({"primary_language": "Python"}) == "python"

    def test_typescript_detected(self):
        assert detect_stack_key({"primary_language": "TypeScript"}) == "typescript"

    def test_javascript_detected(self):
        assert detect_stack_key({"primary_language": "JavaScript"}) == "javascript"

    def test_go_detected(self):
        assert detect_stack_key({"primary_language": "Go"}) == "go"

    def test_golang_variant(self):
        assert detect_stack_key({"primary_language": "Golang"}) == "go"

    def test_framework_fallback_fastapi(self):
        profile = {"primary_language": "unknown", "frameworks": ["FastAPI"]}
        assert detect_stack_key(profile) == "python"

    def test_framework_fallback_react(self):
        profile = {"primary_language": "unknown", "frameworks": ["React", "Next.js"]}
        assert detect_stack_key(profile) == "typescript"

    def test_default_when_none(self):
        assert detect_stack_key(None) == "default"

    def test_default_when_unknown(self):
        assert detect_stack_key({"primary_language": "COBOL"}) == "default"


# ---------------------------------------------------------------------------
# extract_structural_signals
# ---------------------------------------------------------------------------


class TestExtractStructuralSignals:
    def test_full_project_signals(self):
        signals = extract_structural_signals(_scout_data())
        assert signals["has_service_layer"] is True
        assert signals["has_data_access_layer"] is True
        assert signals["has_client_abstraction"] is True
        assert signals["has_middleware_layer"] is True
        assert signals["has_config_external"] is True
        assert signals["has_error_types"] is True
        assert signals["has_test_parallel"] is True
        assert signals["layer_count"] >= 3
        assert signals["stack_key"] == "python"

    def test_minimal_project_signals(self):
        signals = extract_structural_signals(_minimal_scout())
        assert signals["has_service_layer"] is False
        assert signals["has_data_access_layer"] is False
        assert signals["layer_count"] <= 1

    def test_none_returns_empty(self):
        signals = extract_structural_signals(None)
        assert signals == _empty_signals()

    def test_stack_key_override(self):
        signals = extract_structural_signals(_scout_data(), stack_key="typescript")
        assert signals["stack_key"] == "typescript"

    def test_tree_size_echoed(self):
        signals = extract_structural_signals(_scout_data(tree_size=99))
        assert signals["tree_size"] == 99

    def test_files_analysed_echoed(self):
        signals = extract_structural_signals(_scout_data(files_analysed=50))
        assert signals["files_analysed"] == 50


# ---------------------------------------------------------------------------
# Individual rule checks
# ---------------------------------------------------------------------------


class TestRuleChecks:
    def test_layer_separation_pass(self):
        passed, detail = _check_layer_separation({"layer_count": 4}, {})
        assert passed is True

    def test_layer_separation_minimal(self):
        passed, detail = _check_layer_separation({"layer_count": 2}, {})
        assert passed is True
        assert "minimal" in detail.lower()

    def test_layer_separation_fail(self):
        passed, detail = _check_layer_separation({"layer_count": 1}, {})
        assert passed is False

    def test_service_layer_pass(self):
        passed, _ = _check_service_layer({"has_service_layer": True}, {})
        assert passed is True

    def test_service_layer_fail(self):
        passed, _ = _check_service_layer({"has_service_layer": False}, {})
        assert passed is False

    def test_data_access_small_project_exempt(self):
        passed, detail = _check_data_access_layer(
            {"has_data_access_layer": False, "tree_size": 8}, {}
        )
        assert passed is True
        assert "small" in detail.lower()

    def test_data_access_large_project_fail(self):
        passed, _ = _check_data_access_layer(
            {"has_data_access_layer": False, "tree_size": 50}, {}
        )
        assert passed is False

    def test_client_abstraction_small_exempt(self):
        passed, _ = _check_client_abstraction(
            {"has_client_abstraction": False, "tree_size": 5}, {}
        )
        assert passed is True

    def test_config_externalisation_pass(self):
        passed, _ = _check_config_externalisation({"has_config_external": True}, {})
        assert passed is True

    def test_error_types_fail(self):
        passed, _ = _check_error_types({"has_error_types": False}, {})
        assert passed is False

    def test_test_parallel_pass(self):
        passed, _ = _check_test_parallel({"has_test_parallel": True}, {})
        assert passed is True

    def test_test_parallel_fail_with_ratio(self):
        passed, detail = _check_test_parallel(
            {"has_test_parallel": False, "test_to_source_ratio": 0.3}, {}
        )
        assert passed is False
        assert "ratio" in detail.lower()

    def test_test_coverage_ratio_pass(self):
        passed, _ = _check_test_coverage_ratio(
            {"test_to_source_ratio": 0.8}, {"test_to_source_ratio": 0.85}
        )
        assert passed is True

    def test_test_coverage_ratio_fail(self):
        passed, _ = _check_test_coverage_ratio(
            {"test_to_source_ratio": 0.1}, {"test_to_source_ratio": 0.85}
        )
        assert passed is False

    def test_test_coverage_ratio_zero(self):
        passed, _ = _check_test_coverage_ratio(
            {"test_to_source_ratio": 0}, {"test_to_source_ratio": 0.85}
        )
        assert passed is False

    def test_middleware_crosscut_small_exempt(self):
        passed, _ = _check_middleware_crosscut(
            {"has_middleware_layer": False, "tree_size": 5}, {}
        )
        assert passed is True

    def test_middleware_crosscut_large_fail(self):
        passed, _ = _check_middleware_crosscut(
            {"has_middleware_layer": False, "tree_size": 50}, {}
        )
        assert passed is False


# ---------------------------------------------------------------------------
# capture_baseline
# ---------------------------------------------------------------------------


class TestCaptureBaseline:
    def test_captures_all_fields(self):
        baseline = capture_baseline(_scout_data(), source_name="TestProject")
        assert baseline["source"] == "TestProject"
        assert "captured_at" in baseline
        assert "structural_signals" in baseline
        assert "rules" in baseline
        assert len(baseline["rules"]) == len(ARCHITECTURE_RULES)

    def test_captures_stack_profile(self):
        baseline = capture_baseline(_scout_data())
        assert baseline["stack_profile"]["primary_language"] == "Python"

    def test_captures_health_grade(self):
        baseline = capture_baseline(_scout_data())
        assert baseline["health_grade"] == "A"

    def test_structural_signals_populated(self):
        baseline = capture_baseline(_scout_data())
        signals = baseline["structural_signals"]
        assert signals["has_service_layer"] is True
        assert isinstance(signals["layer_count"], int)


# ---------------------------------------------------------------------------
# save_baseline / load_baseline
# ---------------------------------------------------------------------------


class TestBaselinePersistence:
    def test_round_trip(self, tmp_path):
        baseline = capture_baseline(_scout_data())
        path = tmp_path / "baseline.json"
        save_baseline(baseline, path)
        loaded = load_baseline(path)
        assert loaded["source"] == baseline["source"]
        assert loaded["structural_signals"] == baseline["structural_signals"]

    def test_load_missing_file_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            load_baseline(tmp_path / "nonexistent.json")

    def test_save_creates_parent_dirs(self, tmp_path):
        path = tmp_path / "deep" / "nested" / "baseline.json"
        baseline = capture_baseline(_scout_data())
        save_baseline(baseline, path)
        assert path.exists()


# ---------------------------------------------------------------------------
# compare_against_baseline
# ---------------------------------------------------------------------------


class TestCompareAgainstBaseline:
    def test_ideal_project_scores_high(self):
        result = compare_against_baseline(_scout_data())
        assert result["score"] >= 80
        assert result["grade"] in ("A", "B")
        assert result["rules_passed"] >= 7

    def test_empty_project_scores_low(self):
        result = compare_against_baseline(_minimal_scout())
        assert result["score"] < 70
        assert result["rules_passed"] < len(ARCHITECTURE_RULES)

    def test_no_scout_data_returns_result(self):
        result = compare_against_baseline(None)
        assert result["score"] == 0 or result["score"] > 0  # Should not raise
        assert "grade" in result
        assert "rule_results" in result

    def test_uses_custom_baseline(self):
        baseline = capture_baseline(_scout_data())
        result = compare_against_baseline(_scout_data(), baseline=baseline)
        assert result["score"] >= 80

    def test_uses_fallback_when_no_baseline(self):
        result = compare_against_baseline(_scout_data(), baseline=None)
        assert "rules_evaluated" in result
        assert result["rules_evaluated"] == len(ARCHITECTURE_RULES)

    def test_health_grade_bonus(self):
        scout_a = _scout_data(health_grade="A")
        scout_f = _scout_data(health_grade="F")
        score_a = compare_against_baseline(scout_a)["score"]
        score_f = compare_against_baseline(scout_f)["score"]
        assert score_a > score_f

    def test_result_has_rule_results(self):
        result = compare_against_baseline(_scout_data())
        assert len(result["rule_results"]) == len(ARCHITECTURE_RULES)
        for rr in result["rule_results"]:
            assert "rule" in rr
            assert "passed" in rr
            assert "detail" in rr

    def test_details_contain_summary(self):
        result = compare_against_baseline(_scout_data())
        assert any("architecture rules" in d for d in result["details"])
        assert any("Stack:" in d for d in result["details"])

    def test_critical_failure_noted_in_details(self):
        # Minimal scout won't have service layer — which is critical (weight 3)
        result = compare_against_baseline(_minimal_scout())
        # Should mention critical failures if any weight-3 rules failed
        failed_critical = [
            r for r in result["rule_results"]
            if not r["passed"] and r["weight"] >= 3
        ]
        if failed_critical:
            assert any("Critical" in d for d in result["details"])


# ---------------------------------------------------------------------------
# Architecture rules integrity
# ---------------------------------------------------------------------------


class TestArchitectureRules:
    def test_all_rules_have_required_fields(self):
        for rule in ARCHITECTURE_RULES:
            assert "id" in rule
            assert "name" in rule
            assert "weight" in rule
            assert "description" in rule
            assert "check" in rule
            assert callable(rule["check"])

    def test_rule_ids_unique(self):
        ids = [r["id"] for r in ARCHITECTURE_RULES]
        assert len(ids) == len(set(ids))

    def test_weights_are_positive(self):
        for rule in ARCHITECTURE_RULES:
            assert rule["weight"] > 0

    def test_all_checks_return_tuple(self):
        target = _empty_signals()
        baseline = _FORGEGUARD_FALLBACK_SIGNALS
        for rule in ARCHITECTURE_RULES:
            result = rule["check"](target, baseline)
            assert isinstance(result, tuple)
            assert len(result) == 2
            assert isinstance(result[0], bool)
            assert isinstance(result[1], str)
