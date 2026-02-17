"""Tests for migration_advisor — upgrade path recommendations."""

import pytest

from app.services.migration_advisor import recommend_migrations


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _base_stack(**overrides) -> dict:
    base = {
        "languages": {"Python": 80},
        "primary_language": "Python",
        "backend": {
            "framework": "FastAPI",
            "runtime": "python",
            "orm": None,
            "db": None,
            "language": "python",
        },
        "frontend": None,
        "infrastructure": {"containerized": True, "ci_cd": "GitHub Actions", "hosting": None},
        "testing": {"backend_framework": "pytest", "frontend_framework": None, "has_tests": True, "framework": "pytest"},
        "project_type": "backend",
        "manifest_files": ["requirements.txt"],
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestRecommendMigrations:
    """Tests for migration recommendation engine."""

    def test_clean_project_few_migrations(self):
        """Well-maintained project → few or no migrations."""
        stack = _base_stack()
        patterns: list[dict] = []  # No issues
        versions = [
            {"package": "fastapi", "current": "0.115", "latest": "0.115", "status": "current"},
            {"package": "python", "current": "3.12", "latest": "3.12", "status": "current"},
        ]
        recs = recommend_migrations(stack, patterns, versions)
        assert isinstance(recs, list)
        # Shouldn't generate many migrations for a clean project
        high_priority = [r for r in recs if r["priority"] == "high"]
        assert len(high_priority) == 0

    def test_js_to_ts_migration(self):
        """MIG01: JS → TS should be recommended when AP01 is present."""
        stack = _base_stack(
            frontend={"framework": "React", "language": "javascript", "bundler": "webpack", "ui_library": None},
        )
        patterns = [{"id": "AP01", "name": "No TypeScript", "severity": "medium", "category": "quality"}]
        recs = recommend_migrations(stack, patterns, [])
        ids = [r["id"] for r in recs]
        assert "MIG01" in ids

    def test_add_tests_migration(self):
        """MIG02: Add tests when AP02 is found."""
        stack = _base_stack(
            testing={"backend_framework": None, "frontend_framework": None, "has_tests": False, "framework": None},
        )
        patterns = [{"id": "AP02", "name": "No tests", "severity": "high", "category": "quality"}]
        recs = recommend_migrations(stack, patterns, [])
        ids = [r["id"] for r in recs]
        assert "MIG02" in ids

    def test_add_ci_migration(self):
        """MIG03: Add CI when AP03 is found."""
        stack = _base_stack(
            infrastructure={"containerized": False, "ci_cd": None, "hosting": None},
        )
        patterns = [{"id": "AP03", "name": "No CI/CD", "severity": "high", "category": "devops"}]
        recs = recommend_migrations(stack, patterns, [])
        ids = [r["id"] for r in recs]
        assert "MIG03" in ids

    def test_add_docker_migration(self):
        """MIG04: Add Docker when AP04 is found."""
        stack = _base_stack(
            infrastructure={"containerized": False, "ci_cd": "GitHub Actions", "hosting": None},
        )
        patterns = [{"id": "AP04", "name": "No Docker", "severity": "medium", "category": "devops"}]
        recs = recommend_migrations(stack, patterns, [])
        ids = [r["id"] for r in recs]
        assert "MIG04" in ids

    def test_outdated_upgrade(self):
        """MIG10: Outdated deps should trigger upgrade recommendation."""
        stack = _base_stack()
        versions = [
            {"package": "django", "current": "3.1", "latest": "5.1", "status": "outdated"},
        ]
        recs = recommend_migrations(stack, [], versions)
        ids = [r["id"] for r in recs]
        assert "MIG10" in ids

    def test_eol_replacement(self):
        """MIG11: EOL deps should trigger replacement recommendation."""
        stack = _base_stack()
        versions = [
            {"package": "python", "current": "3.7.5", "latest": "3.12", "status": "eol"},
        ]
        recs = recommend_migrations(stack, [], versions)
        ids = [r["id"] for r in recs]
        assert "MIG11" in ids

    def test_secrets_migration(self):
        """MIG13: Hardcoded secrets should trigger removal recommendation."""
        stack = _base_stack()
        patterns = [{"id": "AP11", "name": "Secrets in code", "severity": "high", "category": "security"}]
        recs = recommend_migrations(stack, patterns, [])
        ids = [r["id"] for r in recs]
        assert "MIG13" in ids

    def test_recommendations_sorted_by_priority(self):
        """Output should be sorted high → medium → low."""
        stack = _base_stack(
            frontend={"framework": "React", "language": "javascript", "bundler": "webpack", "ui_library": None},
            infrastructure={"containerized": False, "ci_cd": None, "hosting": None},
        )
        patterns = [
            {"id": "AP01", "name": "No TypeScript", "severity": "medium", "category": "quality"},
            {"id": "AP03", "name": "No CI/CD", "severity": "high", "category": "devops"},
            {"id": "AP04", "name": "No Docker", "severity": "medium", "category": "devops"},
            {"id": "AP11", "name": "Secrets", "severity": "high", "category": "security"},
        ]
        versions = [
            {"package": "python", "current": "3.7.5", "latest": "3.12", "status": "eol"},
        ]
        recs = recommend_migrations(stack, patterns, versions)
        priorities = [r["priority"] for r in recs]
        priority_order = {"high": 0, "medium": 1, "low": 2}
        for i in range(len(priorities) - 1):
            assert priority_order[priorities[i]] <= priority_order[priorities[i + 1]]

    def test_recommendation_structure(self):
        """Each recommendation should have required keys."""
        stack = _base_stack(
            infrastructure={"containerized": False, "ci_cd": None, "hosting": None},
        )
        patterns = [{"id": "AP03", "name": "No CI/CD", "severity": "high", "category": "devops"}]
        recs = recommend_migrations(stack, patterns, [])
        for r in recs:
            assert "id" in r
            assert "from_state" in r
            assert "to_state" in r
            assert "effort" in r
            assert "risk" in r
            assert "priority" in r
            assert "category" in r
            assert "steps" in r
            assert "forge_automatable" in r

    def test_empty_inputs(self):
        """Empty inputs should not crash."""
        recs = recommend_migrations({}, [], [])
        assert isinstance(recs, list)

    def test_forge_automatable_flag(self):
        """Some migrations should be marked as Forge-automatable."""
        stack = _base_stack(
            infrastructure={"containerized": False, "ci_cd": None, "hosting": None},
        )
        patterns = [
            {"id": "AP03", "name": "No CI/CD", "severity": "high", "category": "devops"},
            {"id": "AP04", "name": "No Docker", "severity": "medium", "category": "devops"},
        ]
        recs = recommend_migrations(stack, patterns, [])
        automatable = [r for r in recs if r.get("forge_automatable")]
        assert len(automatable) > 0
