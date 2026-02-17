"""Tests for pattern_analyzer — anti-pattern detection."""

import pytest

from app.services.pattern_analyzer import analyze_patterns


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _base_stack(**overrides) -> dict:
    """Minimal stack profile for testing."""
    base = {
        "languages": {"Python": 80, "JavaScript": 20},
        "primary_language": "Python",
        "backend": {
            "framework": "FastAPI",
            "runtime": "python",
            "orm": "SQLAlchemy",
            "db": "PostgreSQL",
            "language": "python",
        },
        "frontend": {
            "framework": "React",
            "bundler": "vite",
            "language": "typescript",
            "ui_library": None,
        },
        "infrastructure": {
            "containerized": True,
            "ci_cd": "GitHub Actions",
            "hosting": None,
        },
        "testing": {
            "backend_framework": "pytest",
            "frontend_framework": "vitest",
            "has_tests": True,
            "framework": "pytest",
        },
        "project_type": "full_stack",
        "manifest_files": ["requirements.txt", "package.json"],
    }
    base.update(overrides)
    return base


def _base_arch(**overrides) -> dict:
    """Minimal architecture map."""
    base = {
        "structure_type": "modular",
        "entry_points": ["main.py"],
        "data_models": [],
        "external_integrations": [],
        "has_env_example": True,
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestAnalyzePatterns:
    """Integration tests running the full detector suite."""

    def test_clean_project_no_findings(self):
        """A well-structured project should produce zero or very few findings."""
        stack = _base_stack()
        arch = _base_arch()
        files = {
            "main.py": 'def main() -> None:\n    pass\n',
            ".github/workflows/ci.yml": "name: CI\n",
            "Dockerfile": "FROM python:3.12\n",
            "README.md": "# My Project\n",
            "LICENSE": "MIT\n",
            ".env.example": "DB_URL=\n",
            "requirements.txt": "fastapi==0.115.0\nuvicorn==0.30.0\n",
        }
        findings = analyze_patterns(stack, arch, files)
        assert isinstance(findings, list)
        # Clean project should have very few if any findings
        high_findings = [f for f in findings if f["severity"] == "high"]
        assert len(high_findings) == 0

    def test_detects_no_typescript(self):
        """AP01: JS frontend should be flagged."""
        stack = _base_stack(
            frontend={"framework": "React", "language": "javascript", "bundler": "webpack", "ui_library": None},
        )
        findings = analyze_patterns(stack, _base_arch(), {})
        ids = [f["id"] for f in findings]
        assert "AP01" in ids

    def test_detects_no_tests(self):
        """AP02: No test framework should be flagged."""
        stack = _base_stack(
            testing={"backend_framework": None, "frontend_framework": None, "has_tests": False, "framework": None},
        )
        findings = analyze_patterns(stack, _base_arch(), {})
        ids = [f["id"] for f in findings]
        assert "AP02" in ids

    def test_detects_no_ci(self):
        """AP03: No CI/CD should be flagged."""
        stack = _base_stack(
            infrastructure={"containerized": True, "ci_cd": None, "hosting": None},
        )
        files = {}  # No CI config files
        findings = analyze_patterns(stack, _base_arch(), files)
        ids = [f["id"] for f in findings]
        assert "AP03" in ids

    def test_detects_no_docker(self):
        """AP04: No container should be flagged."""
        stack = _base_stack(
            infrastructure={"containerized": False, "ci_cd": "GitHub Actions", "hosting": None},
        )
        findings = analyze_patterns(stack, _base_arch(), {})
        ids = [f["id"] for f in findings]
        assert "AP04" in ids

    def test_detects_secrets_in_code(self):
        """AP11: Hardcoded secrets should be detected."""
        stack = _base_stack()
        files = {
            "config.py": 'API_KEY = "sk-1234567890abcdef1234567890abcdef"\nDB_URL = "postgres://user:pass@host/db"\n',
        }
        findings = analyze_patterns(stack, _base_arch(), files)
        ids = [f["id"] for f in findings]
        assert "AP11" in ids

    def test_detects_no_readme(self):
        """AP12: No README should be flagged (requires >2 files)."""
        stack = _base_stack()
        arch = _base_arch()
        files = {"main.py": "pass\n", "config.py": "x=1\n", "utils.py": "pass\n"}  # No README, >2 files
        findings = analyze_patterns(stack, arch, files)
        ids = [f["id"] for f in findings]
        assert "AP12" in ids

    def test_detects_no_license(self):
        """AP13: No LICENSE should be flagged."""
        stack = _base_stack()
        files = {"main.py": "pass\n", "README.md": "# Hi\n"}  # No LICENSE
        findings = analyze_patterns(stack, _base_arch(), files)
        ids = [f["id"] for f in findings]
        assert "AP13" in ids

    def test_findings_sorted_by_severity(self):
        """Findings should be sorted high → medium → low."""
        stack = _base_stack(
            frontend={"framework": "React", "language": "javascript", "bundler": "webpack", "ui_library": None},
            testing={"backend_framework": None, "frontend_framework": None, "has_tests": False, "framework": None},
            infrastructure={"containerized": False, "ci_cd": None, "hosting": None},
        )
        findings = analyze_patterns(stack, _base_arch(), {"main.py": "pass\n"})
        severities = [f["severity"] for f in findings]
        severity_order = {"high": 0, "medium": 1, "low": 2}
        for i in range(len(severities) - 1):
            assert severity_order[severities[i]] <= severity_order[severities[i + 1]]

    def test_empty_inputs(self):
        """Empty inputs should not crash."""
        findings = analyze_patterns({}, {}, {})
        assert isinstance(findings, list)

    def test_finding_structure(self):
        """Each finding should have the required keys."""
        stack = _base_stack(
            testing={"backend_framework": None, "frontend_framework": None, "has_tests": False, "framework": None},
        )
        findings = analyze_patterns(stack, _base_arch(), {})
        for f in findings:
            assert "id" in f
            assert "name" in f
            assert "severity" in f
            assert "category" in f
            assert "detail" in f

    def test_detects_unpinned_deps(self):
        """AP15: Unpinned dependencies in requirements.txt."""
        stack = _base_stack()
        files = {
            "requirements.txt": "fastapi\nuvicorn\nhttpx\npydantic\n",
            "README.md": "# Hi\n",
            "LICENSE": "MIT\n",
        }
        findings = analyze_patterns(stack, _base_arch(), files)
        ids = [f["id"] for f in findings]
        assert "AP15" in ids

    def test_detector_isolation(self):
        """A single detector failure should not break others."""
        # This just verifies the try/except inside analyze_patterns works
        findings = analyze_patterns({"backend": None}, {}, {})
        assert isinstance(findings, list)

    def test_class_components_detected(self):
        """AP08: React class components should be flagged."""
        stack = _base_stack()
        files = {
            "App.tsx": "class App extends React.Component {\n  render() { return <div /> }\n}\n",
            "README.md": "x\n",
            "LICENSE": "MIT\n",
        }
        findings = analyze_patterns(stack, _base_arch(), files)
        ids = [f["id"] for f in findings]
        assert "AP08" in ids
