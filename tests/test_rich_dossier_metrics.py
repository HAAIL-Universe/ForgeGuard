"""Phase 58.2 tests — Rich 9-dimension dossier metrics.

Validates the rewritten ``compute_repo_metrics()`` with 9 Seal-aligned
dimensions scored 0-100, weighted average, and neutral baselines.
"""

import pytest
from app.services.scout_metrics import (
    compute_repo_metrics,
    detect_smells,
    _WEIGHTS,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

BASIC_TREE = [
    "README.md",
    ".gitignore",
    "LICENSE",
    "requirements.txt",
    "app/__init__.py",
    "app/main.py",
    "app/config.py",
    "app/routes.py",
    "tests/test_main.py",
    "tests/test_config.py",
    "docs/setup.md",
]

BASIC_FILES = {
    "README.md": "# My Project\n\nDescription. " + "x" * 600,
    "requirements.txt": "fastapi==0.100.0\nuvicorn==0.22.0\npydantic>=2.0\n",
    "app/__init__.py": '"""Package init."""\n',
    "app/main.py": (
        '"""Main entry point."""\n\nfrom fastapi import FastAPI\napp = FastAPI()\n\n'
        '@app.get("/")\ndef root():\n    """Root endpoint."""\n    return {"ok": True}\n'
    ),
    "app/config.py": "import os\n\nDB_URL = os.getenv('DATABASE_URL')\n",
    "app/routes.py": "from fastapi import APIRouter\nrouter = APIRouter()\n",
    "tests/test_main.py": "def test_root():\n    assert 1 == 1\n",
    "tests/test_config.py": "def test_config():\n    pass\n",
    "docs/setup.md": "## Setup\nRun install.\n",
}

NINE_DIMENSIONS = {
    "build_integrity",
    "test_coverage",
    "audit_compliance",
    "governance",
    "security",
    "cost_efficiency",
    "reliability",
    "consistency",
    "architecture",
}


# ---------------------------------------------------------------------------
# Dimension structure tests
# ---------------------------------------------------------------------------


class TestNineDimensionStructure:
    """Ensure the new 9-dimension output has the correct shape."""

    def test_returns_nine_dimensions(self):
        m = compute_repo_metrics(BASIC_TREE, BASIC_FILES)
        dims = set(m["scores"].keys())
        assert dims == NINE_DIMENSIONS, f"Expected 9 dimensions, got {dims}"

    def test_each_dim_has_score_weight_details(self):
        m = compute_repo_metrics(BASIC_TREE, BASIC_FILES)
        for dim, info in m["scores"].items():
            assert "score" in info, f"{dim} missing score"
            assert "weight" in info, f"{dim} missing weight"
            assert "details" in info, f"{dim} missing details"

    def test_scores_are_0_to_100(self):
        m = compute_repo_metrics(BASIC_TREE, BASIC_FILES)
        for dim, info in m["scores"].items():
            assert 0 <= info["score"] <= 100, f"{dim} score out of range: {info['score']}"

    def test_weights_match_seal(self):
        """Weights in scores must match _WEIGHTS constant."""
        m = compute_repo_metrics(BASIC_TREE, BASIC_FILES)
        for dim, info in m["scores"].items():
            assert info["weight"] == _WEIGHTS[dim], f"{dim} weight mismatch"

    def test_weights_sum_to_one(self):
        total = sum(_WEIGHTS.values())
        assert abs(total - 1.0) < 0.001, f"Weights sum to {total}, expected 1.0"


# ---------------------------------------------------------------------------
# Computed score tests
# ---------------------------------------------------------------------------


class TestComputedScore:
    """Test the weighted-average computed_score."""

    def test_computed_score_is_weighted_average(self):
        m = compute_repo_metrics(BASIC_TREE, BASIC_FILES)
        expected = sum(
            info["score"] * info["weight"]
            for info in m["scores"].values()
        )
        assert m["computed_score"] == round(expected)

    def test_computed_score_is_integer(self):
        m = compute_repo_metrics(BASIC_TREE, BASIC_FILES)
        assert isinstance(m["computed_score"], int)

    def test_computed_score_in_valid_range(self):
        m = compute_repo_metrics(BASIC_TREE, BASIC_FILES)
        assert 0 <= m["computed_score"] <= 100


# ---------------------------------------------------------------------------
# Neutral-at-baseline dimensions
# ---------------------------------------------------------------------------


class TestNeutralBaselines:
    """Dimensions without dossier-time data should score 50 (neutral)."""

    def test_build_integrity_is_neutral(self):
        m = compute_repo_metrics(BASIC_TREE, BASIC_FILES)
        assert m["scores"]["build_integrity"]["score"] == 50

    def test_cost_efficiency_is_neutral(self):
        m = compute_repo_metrics(BASIC_TREE, BASIC_FILES)
        assert m["scores"]["cost_efficiency"]["score"] == 50

    def test_reliability_is_neutral(self):
        m = compute_repo_metrics(BASIC_TREE, BASIC_FILES)
        assert m["scores"]["reliability"]["score"] == 50


# ---------------------------------------------------------------------------
# Dimension scoring behaviour
# ---------------------------------------------------------------------------


class TestTestCoverage:
    """Test the test_coverage dimension."""

    def test_no_tests_scores_low(self):
        tree = ["app/main.py", "app/config.py"]
        files = {"app/main.py": "print('hi')\n"}
        m = compute_repo_metrics(tree, files)
        assert m["scores"]["test_coverage"]["score"] < 30

    def test_good_test_ratio_scores_high(self):
        m = compute_repo_metrics(BASIC_TREE, BASIC_FILES)
        # With 2 test files and 4 source files, ratio is decent
        assert m["scores"]["test_coverage"]["score"] >= 30


class TestSecurity:
    """Test the security dimension."""

    def test_clean_repo_high_security(self):
        m = compute_repo_metrics(BASIC_TREE, BASIC_FILES)
        assert m["scores"]["security"]["score"] >= 80

    def test_secrets_reduce_security(self):
        files = dict(BASIC_FILES)
        files["app/config.py"] = 'API_KEY = "sk-ant-1234567890abcdefGHIJKLMNOP"\n'
        m = compute_repo_metrics(BASIC_TREE, files)
        clean = compute_repo_metrics(BASIC_TREE, BASIC_FILES)
        # Secrets should reduce the security score vs a clean repo
        assert m["scores"]["security"]["score"] < clean["scores"]["security"]["score"]


class TestConsistency:
    """Test the consistency dimension."""

    def test_basic_repo_has_consistency_score(self):
        m = compute_repo_metrics(BASIC_TREE, BASIC_FILES)
        assert 0 <= m["scores"]["consistency"]["score"] <= 100


class TestArchitecture:
    """Test the architecture dimension."""

    def test_basic_repo_has_architecture_score(self):
        m = compute_repo_metrics(BASIC_TREE, BASIC_FILES)
        assert 0 <= m["scores"]["architecture"]["score"] <= 100


class TestAuditCompliance:
    """Test audit_compliance with governance check data."""

    def test_with_no_checks(self):
        m = compute_repo_metrics(BASIC_TREE, BASIC_FILES)
        # Without checks, audit_compliance defaults to neutral
        assert m["scores"]["audit_compliance"]["score"] == 50

    def test_with_all_pass_checks(self):
        checks = [
            {"code": "A1", "result": "PASS"},
            {"code": "A2", "result": "PASS"},
            {"code": "A3", "result": "PASS"},
        ]
        m = compute_repo_metrics(BASIC_TREE, BASIC_FILES, checks=checks)
        assert m["scores"]["audit_compliance"]["score"] > 50

    def test_with_failing_checks(self):
        checks = [
            {"code": "A1", "result": "FAIL"},
            {"code": "A2", "result": "FAIL"},
            {"code": "A3", "result": "PASS"},
        ]
        m = compute_repo_metrics(BASIC_TREE, BASIC_FILES, checks=checks)
        # Some fails should lower the score
        assert m["scores"]["audit_compliance"]["score"] < 100


class TestGovernance:
    """Test governance dimension."""

    def test_with_no_checks(self):
        m = compute_repo_metrics(BASIC_TREE, BASIC_FILES)
        assert m["scores"]["governance"]["score"] == 50

    def test_all_pass(self):
        checks = [
            {"code": "A1", "result": "PASS"},
            {"code": "A2", "result": "PASS"},
        ]
        m = compute_repo_metrics(BASIC_TREE, BASIC_FILES, checks=checks)
        assert m["scores"]["governance"]["score"] > 50


# ---------------------------------------------------------------------------
# File stats tests
# ---------------------------------------------------------------------------


class TestFileStats:
    """Verify file classification is correct."""

    def test_file_stats_present(self):
        m = compute_repo_metrics(BASIC_TREE, BASIC_FILES)
        fs = m["file_stats"]
        assert "total_files" in fs
        assert "source_files" in fs
        assert "test_files" in fs
        assert "doc_files" in fs
        assert fs["total_files"] == len(BASIC_TREE)

    def test_test_files_count(self):
        m = compute_repo_metrics(BASIC_TREE, BASIC_FILES)
        assert m["file_stats"]["test_files"] >= 2

    def test_source_files_count(self):
        m = compute_repo_metrics(BASIC_TREE, BASIC_FILES)
        assert m["file_stats"]["source_files"] >= 4


# ---------------------------------------------------------------------------
# Smells still work
# ---------------------------------------------------------------------------


class TestSmellsUnchanged:
    """Ensure detect_smells still works after the metrics rewrite."""

    def test_clean_repo(self):
        smells = detect_smells(BASIC_TREE, BASIC_FILES)
        ids = {s["id"] for s in smells}
        assert "env_committed" not in ids
        assert "secrets_in_source" not in ids

    def test_env_committed(self):
        tree = BASIC_TREE + [".env"]
        smells = detect_smells(tree, BASIC_FILES)
        assert any(s["id"] == "env_committed" for s in smells)

    def test_secrets(self):
        files = dict(BASIC_FILES)
        files["app/config.py"] = 'API_KEY = "sk-ant-1234567890abcdefGHIJKLMNOP"\n'
        smells = detect_smells(BASIC_TREE, files)
        assert any(s["id"] == "secrets_in_source" for s in smells)


# ---------------------------------------------------------------------------
# Bad vs good repo contrast
# ---------------------------------------------------------------------------


class TestScoreContrast:
    """Verify that different repos produce different scores."""

    def test_bad_repo_low_score(self):
        tree = ["main.py"]
        files = {"main.py": "result = eval(input())\n" * 600}
        m = compute_repo_metrics(tree, files)
        assert m["computed_score"] < 60

    def test_good_repo_reasonable_score(self):
        m = compute_repo_metrics(BASIC_TREE, BASIC_FILES)
        assert m["computed_score"] >= 40
