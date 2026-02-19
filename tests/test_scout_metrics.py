"""Tests for app.services.scout_metrics — Phase 39 (updated for Phase 58).

Phase 58 replaced the 5-dimension 0-20 scoring with 9-dimension 0-100
weighted scoring.  The detect_smells tests remain unchanged.  Dimension
scorer tests are now in test_rich_dossier_metrics.py.
"""

import pytest
from app.services.scout_metrics import (
    compute_repo_metrics,
    detect_smells,
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
    "README.md": "# My Project\n\nA long description with more than 500 characters. " + "x" * 600,
    "requirements.txt": "fastapi==0.100.0\nuvicorn==0.22.0\npydantic>=2.0\n",
    "app/__init__.py": '"""Package init."""\n',
    "app/main.py": '"""Main entry point."""\n\nfrom fastapi import FastAPI\napp = FastAPI()\n\n@app.get("/")\ndef root():\n    return {"ok": True}\n',
    "app/config.py": "import os\n\nDB_URL = os.getenv('DATABASE_URL')\n",
    "app/routes.py": "from fastapi import APIRouter\nrouter = APIRouter()\n",
    "tests/test_main.py": "def test_root():\n    assert 1 == 1\n",
    "tests/test_config.py": "def test_config():\n    pass\n",
    "docs/setup.md": "## Setup\nRun install.\n",
}


# ---------------------------------------------------------------------------
# detect_smells tests
# ---------------------------------------------------------------------------

class TestDetectSmells:
    def test_clean_repo_no_smells(self):
        smells = detect_smells(BASIC_TREE, BASIC_FILES)
        ids = {s["id"] for s in smells}
        # Should NOT have env_committed, secrets_in_source, etc.
        assert "env_committed" not in ids
        assert "secrets_in_source" not in ids
        assert "no_gitignore" not in ids
        assert "missing_readme" not in ids
        assert "no_license" not in ids
        assert "no_tests" not in ids

    def test_env_committed(self):
        tree = BASIC_TREE + [".env"]
        smells = detect_smells(tree, BASIC_FILES)
        assert any(s["id"] == "env_committed" for s in smells)

    def test_no_gitignore(self):
        tree = [p for p in BASIC_TREE if p != ".gitignore"]
        smells = detect_smells(tree, BASIC_FILES)
        assert any(s["id"] == "no_gitignore" for s in smells)

    def test_no_license(self):
        tree = [p for p in BASIC_TREE if p != "LICENSE"]
        smells = detect_smells(tree, BASIC_FILES)
        assert any(s["id"] == "no_license" for s in smells)

    def test_missing_readme(self):
        tree = [p for p in BASIC_TREE if p != "README.md"]
        files = {k: v for k, v in BASIC_FILES.items() if k != "README.md"}
        smells = detect_smells(tree, files)
        assert any(s["id"] == "missing_readme" for s in smells)

    def test_no_tests_detected(self):
        tree = [p for p in BASIC_TREE if not p.startswith("tests/")]
        smells = detect_smells(tree, BASIC_FILES)
        assert any(s["id"] == "no_tests" for s in smells)

    def test_secrets_in_source(self):
        files = dict(BASIC_FILES)
        files["app/config.py"] = 'API_KEY = "sk-ant-1234567890abcdefGHIJKLMNOP"\n'
        smells = detect_smells(BASIC_TREE, files)
        assert any(s["id"] == "secrets_in_source" for s in smells)
        secret_smell = next(s for s in smells if s["id"] == "secrets_in_source")
        assert "app/config.py" in secret_smell["files"]

    def test_raw_sql_detection(self):
        files = dict(BASIC_FILES)
        files["app/routes.py"] = 'q = f"SELECT * FROM users WHERE id = {user_id}"\n'
        smells = detect_smells(BASIC_TREE, files)
        assert any(s["id"] == "raw_sql" for s in smells)

    def test_eval_exec_detection(self):
        files = dict(BASIC_FILES)
        files["app/routes.py"] = "result = eval(user_input)\n"
        smells = detect_smells(BASIC_TREE, files)
        assert any(s["id"] == "eval_exec" for s in smells)

    def test_unpinned_deps(self):
        files = dict(BASIC_FILES)
        files["requirements.txt"] = "fastapi\nuvicorn\npydantic\n"
        smells = detect_smells(BASIC_TREE, files)
        assert any(s["id"] == "unpinned_deps" for s in smells)

    def test_large_files(self):
        files = dict(BASIC_FILES)
        files["app/main.py"] = "x = 1\n" * 600
        smells = detect_smells(BASIC_TREE, files)
        assert any(s["id"] == "large_files" for s in smells)

    def test_severity_sort_order(self):
        tree = BASIC_TREE + [".env"]
        tree_no_lic = [p for p in tree if p != "LICENSE"]
        files = dict(BASIC_FILES)
        files["app/routes.py"] = "result = eval(user_input)\n"
        smells = detect_smells(tree_no_lic, files)
        if len(smells) >= 2:
            sev_order = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
            for i in range(len(smells) - 1):
                assert sev_order[smells[i]["severity"]] <= sev_order[smells[i + 1]["severity"]]

    def test_todo_fixme_density(self):
        files = dict(BASIC_FILES)
        # Create a file with high TODO density (>10 per 1K lines)
        files["app/routes.py"] = "\n".join(
            [f"# TODO: fix line {i}" for i in range(50)]
        ) + "\n"
        smells = detect_smells(BASIC_TREE, files)
        # Density = 50 todos / ~60 lines * 1000 ≈ 833 per 1K — way above threshold
        assert any(s["id"] == "todo_fixme_density" for s in smells)


# ---------------------------------------------------------------------------
# compute_repo_metrics integration (updated for Phase 58 nine dimensions)
# ---------------------------------------------------------------------------

class TestComputeRepoMetrics:
    def test_returns_all_keys(self):
        m = compute_repo_metrics(BASIC_TREE, BASIC_FILES)
        assert "scores" in m
        assert "computed_score" in m
        assert "file_stats" in m
        assert "smells" in m
        assert isinstance(m["computed_score"], int)
        assert 0 <= m["computed_score"] <= 100

    def test_nine_dimensions(self):
        m = compute_repo_metrics(BASIC_TREE, BASIC_FILES)
        dims = set(m["scores"].keys())
        assert dims == {"build_integrity", "test_coverage", "audit_compliance",
                        "governance", "security", "cost_efficiency",
                        "reliability", "consistency", "architecture"}

    def test_each_dim_has_score_weight_and_details(self):
        m = compute_repo_metrics(BASIC_TREE, BASIC_FILES)
        for dim, info in m["scores"].items():
            assert "score" in info, f"{dim} missing score"
            assert "weight" in info, f"{dim} missing weight"
            assert "details" in info, f"{dim} missing details"
            assert 0 <= info["score"] <= 100, f"{dim} score out of range: {info['score']}"

    def test_computed_score_is_weighted_average(self):
        m = compute_repo_metrics(BASIC_TREE, BASIC_FILES)
        expected = round(sum(d["score"] * d["weight"] for d in m["scores"].values()))
        assert m["computed_score"] == expected

    def test_file_stats(self):
        m = compute_repo_metrics(BASIC_TREE, BASIC_FILES)
        fs = m["file_stats"]
        assert fs["total_files"] == len(BASIC_TREE)
        assert fs["test_files"] >= 2
        assert fs["source_files"] >= 4

    def test_smells_are_list(self):
        m = compute_repo_metrics(BASIC_TREE, BASIC_FILES)
        assert isinstance(m["smells"], list)

    def test_bad_repo_low_score(self):
        tree = ["main.py"]
        files = {"main.py": "result = eval(input())\n" * 600}
        m = compute_repo_metrics(tree, files)
        assert m["computed_score"] < 60

    def test_good_repo_reasonable_score(self):
        m = compute_repo_metrics(BASIC_TREE, BASIC_FILES)
        assert m["computed_score"] >= 40
