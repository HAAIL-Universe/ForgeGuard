"""Tests for upgrade_service — Renovation Plan orchestrator."""

import json
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from app.services.upgrade_service import (
    _extract_node_deps,
    _extract_python_deps,
    generate_forge_spec,
    generate_renovation_plan,
    get_renovation_plan,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _deep_scan_run(*, status="completed", scan_type="deep", results=None):
    """Build a mock scout run dict."""
    uid = uuid4()
    return {
        "id": uuid4(),
        "user_id": uid,
        "repo_id": uuid4(),
        "repo_name": "test-org/test-repo",
        "status": status,
        "scan_type": scan_type,
        "checks_passed": 5,
        "checks_failed": 1,
        "checks_warned": 2,
        "results": results or {
            "stack_profile": {
                "languages": {"Python": 80, "TypeScript": 20},
                "primary_language": "Python",
                "backend": {
                    "framework": "FastAPI",
                    "framework_version": "0.115",
                    "runtime": "python",
                    "language": "python",
                    "language_version": "3.12",
                    "orm": "SQLAlchemy",
                    "db": "PostgreSQL",
                },
                "frontend": {
                    "framework": "React",
                    "framework_version": "18.3",
                    "language": "typescript",
                    "bundler": "vite",
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
            },
            "architecture": {
                "structure_type": "modular",
                "entry_points": ["main.py"],
                "data_models": [],
                "external_integrations": [],
                "has_env_example": True,
            },
            "file_contents": {
                "main.py": "def main() -> None:\n    pass\n",
                "README.md": "# Test\n",
                "LICENSE": "MIT\n",
                ".env.example": "DB=\n",
                "requirements.txt": "fastapi==0.115.0\n",
            },
        },
    }


# ---------------------------------------------------------------------------
# extract_python_deps
# ---------------------------------------------------------------------------


class TestExtractPythonDeps:
    def test_extracts_framework(self):
        stack = {
            "backend": {"framework": "FastAPI", "framework_version": "0.115", "language": "python", "language_version": "3.12"},
        }
        deps = _extract_python_deps(stack)
        assert "fastapi" in deps
        assert deps["fastapi"] == "0.115"

    def test_extracts_python_version(self):
        stack = {
            "backend": {"language": "python", "language_version": "3.12"},
        }
        deps = _extract_python_deps(stack)
        assert deps.get("python") == "3.12"

    def test_empty_stack(self):
        deps = _extract_python_deps({})
        assert isinstance(deps, dict)


class TestExtractNodeDeps:
    def test_extracts_framework(self):
        stack = {
            "frontend": {"framework": "React", "framework_version": "18.3", "language": "typescript", "bundler": "vite"},
        }
        deps = _extract_node_deps(stack)
        assert "react" in deps
        assert deps["react"] == "18.3"

    def test_extracts_bundler(self):
        stack = {
            "frontend": {"framework": "React", "language": "typescript", "bundler": "vite"},
        }
        deps = _extract_node_deps(stack)
        assert "vite" in deps

    def test_empty_stack(self):
        deps = _extract_node_deps({})
        assert isinstance(deps, dict)


# ---------------------------------------------------------------------------
# generate_forge_spec
# ---------------------------------------------------------------------------


class TestGenerateForgeSpec:
    def test_with_automatable_migrations(self):
        migrations = [
            {"id": "MIG03", "from_state": "No CI", "to_state": "GitHub Actions",
             "category": "devops", "steps": ["Step 1"], "effort": "low",
             "risk": "low", "forge_automatable": True},
            {"id": "MIG01", "from_state": "JS", "to_state": "TS",
             "category": "quality", "steps": ["Step 1"], "effort": "medium",
             "risk": "medium", "forge_automatable": False},
        ]
        spec = generate_forge_spec("test-org/repo", migrations)
        assert spec is not None
        assert spec["schema"] == "forge-upgrade-spec/v1"
        assert spec["total_automatable"] == 1
        assert len(spec["tasks"]) == 1
        assert spec["tasks"][0]["id"] == "mig03"

    def test_no_automatable(self):
        migrations = [
            {"id": "MIG01", "from_state": "JS", "to_state": "TS",
             "steps": [], "forge_automatable": False},
        ]
        spec = generate_forge_spec("test-org/repo", migrations)
        assert spec is None

    def test_empty_migrations(self):
        spec = generate_forge_spec("test-org/repo", [])
        assert spec is None


# ---------------------------------------------------------------------------
# generate_renovation_plan (integration)
# ---------------------------------------------------------------------------


class TestGenerateRenovationPlan:
    @pytest.mark.asyncio
    async def test_successful_plan_generation(self):
        run = _deep_scan_run()
        user_id = run["user_id"]
        run_id = run["id"]

        with patch("app.services.upgrade_service.get_scout_run", new_callable=AsyncMock) as mock_get, \
             patch("app.services.upgrade_service.update_scout_run", new_callable=AsyncMock) as mock_update:
            mock_get.return_value = run
            mock_update.return_value = run

            plan = await generate_renovation_plan(user_id, run_id, include_llm=False)

            assert "version_report" in plan
            assert "pattern_findings" in plan
            assert "migration_recommendations" in plan
            assert "summary_stats" in plan
            assert "forge_spec" in plan
            assert isinstance(plan["version_report"], list)
            assert isinstance(plan["pattern_findings"], list)
            assert isinstance(plan["migration_recommendations"], list)

    @pytest.mark.asyncio
    async def test_plan_summary_stats(self):
        run = _deep_scan_run()
        user_id = run["user_id"]
        run_id = run["id"]

        with patch("app.services.upgrade_service.get_scout_run", new_callable=AsyncMock) as mock_get, \
             patch("app.services.upgrade_service.update_scout_run", new_callable=AsyncMock) as mock_update:
            mock_get.return_value = run
            mock_update.return_value = run

            plan = await generate_renovation_plan(user_id, run_id, include_llm=False)
            stats = plan["summary_stats"]

            assert "dependencies_checked" in stats
            assert "eol_count" in stats
            assert "outdated_count" in stats
            assert "current_count" in stats
            assert "patterns_detected" in stats
            assert "migrations_recommended" in stats
            assert "forge_automatable" in stats

    @pytest.mark.asyncio
    async def test_rejects_non_deep_scan(self):
        run = _deep_scan_run(scan_type="quick")
        with patch("app.services.upgrade_service.get_scout_run", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = run
            with pytest.raises(ValueError, match="deep scan"):
                await generate_renovation_plan(run["user_id"], run["id"])

    @pytest.mark.asyncio
    async def test_rejects_incomplete_run(self):
        run = _deep_scan_run(status="running")
        with patch("app.services.upgrade_service.get_scout_run", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = run
            with pytest.raises(ValueError, match="not completed"):
                await generate_renovation_plan(run["user_id"], run["id"])

    @pytest.mark.asyncio
    async def test_rejects_wrong_user(self):
        run = _deep_scan_run()
        wrong_user = uuid4()
        with patch("app.services.upgrade_service.get_scout_run", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = run
            with pytest.raises(ValueError, match="not found"):
                await generate_renovation_plan(wrong_user, run["id"])

    @pytest.mark.asyncio
    async def test_persists_plan_to_results(self):
        run = _deep_scan_run()
        user_id = run["user_id"]
        run_id = run["id"]

        with patch("app.services.upgrade_service.get_scout_run", new_callable=AsyncMock) as mock_get, \
             patch("app.services.upgrade_service.update_scout_run", new_callable=AsyncMock) as mock_update:
            mock_get.return_value = run
            mock_update.return_value = run

            await generate_renovation_plan(user_id, run_id, include_llm=False)

            # Should have called update with results containing renovation_plan
            mock_update.assert_called_once()
            call_kwargs = mock_update.call_args
            results = call_kwargs.kwargs.get("results") or call_kwargs[1].get("results")
            assert "renovation_plan" in results


# ---------------------------------------------------------------------------
# get_renovation_plan
# ---------------------------------------------------------------------------


class TestGetRenovationPlan:
    @pytest.mark.asyncio
    async def test_returns_plan_when_exists(self):
        run = _deep_scan_run()
        run["results"]["renovation_plan"] = {"version_report": [], "summary_stats": {}}

        with patch("app.services.upgrade_service.get_scout_run", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = run
            plan = await get_renovation_plan(run["user_id"], run["id"])
            assert plan is not None
            assert "version_report" in plan

    @pytest.mark.asyncio
    async def test_returns_none_when_no_plan(self):
        run = _deep_scan_run()
        # results exist but no renovation_plan key
        with patch("app.services.upgrade_service.get_scout_run", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = run
            plan = await get_renovation_plan(run["user_id"], run["id"])
            assert plan is None

    @pytest.mark.asyncio
    async def test_rejects_wrong_user(self):
        run = _deep_scan_run()
        with patch("app.services.upgrade_service.get_scout_run", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = run
            with pytest.raises(ValueError, match="not found"):
                await get_renovation_plan(uuid4(), run["id"])
