"""Phase 58.6 tests — README lifecycle generator."""

from unittest.mock import AsyncMock, patch, MagicMock

import pytest
from app.services.readme_generator import (
    is_greenfield,
    generate_contract_readme,
    generate_project_readme,
)


# ---------------------------------------------------------------------------
# is_greenfield tests
# ---------------------------------------------------------------------------


class TestIsGreenfield:
    def test_none_dossier_is_greenfield(self):
        assert is_greenfield(None) is True

    def test_empty_metrics_is_greenfield(self):
        assert is_greenfield({"metrics": {}}) is True

    def test_zero_source_files_is_greenfield(self):
        assert is_greenfield({
            "metrics": {"file_stats": {"source_files": 0, "total_files": 3}}
        }) is True

    def test_few_files_is_greenfield(self):
        assert is_greenfield({
            "metrics": {"file_stats": {"source_files": 2, "total_files": 5}}
        }) is True

    def test_many_files_is_not_greenfield(self):
        assert is_greenfield({
            "metrics": {"file_stats": {"source_files": 20, "total_files": 50}}
        }) is False

    def test_threshold_boundary(self):
        """6 total files with some source = not greenfield."""
        assert is_greenfield({
            "metrics": {"file_stats": {"source_files": 3, "total_files": 6}}
        }) is False


# ---------------------------------------------------------------------------
# generate_contract_readme tests
# ---------------------------------------------------------------------------


class TestGenerateContractReadme:
    @pytest.mark.asyncio
    async def test_no_api_key_returns_none(self):
        """Without API key, should return None gracefully."""
        with patch("app.config.settings.ANTHROPIC_API_KEY", ""):
            result = await generate_contract_readme(
                project_name="Test Project",
                project_description="A test",
                stack_profile=None,
                architecture=None,
                contracts=[],
            )
            assert result is None

    @pytest.mark.asyncio
    async def test_llm_call_returns_readme(self):
        """When LLM responds, we get markdown back."""
        with patch("app.config.settings.ANTHROPIC_API_KEY", "test-key"), \
             patch("app.config.settings.LLM_PLANNER_MODEL", "test-model"), \
             patch("app.services.readme_generator.chat", new_callable=AsyncMock) as mock_chat:
            mock_chat.return_value = {"text": "# Test Project\n\nA README."}

            result = await generate_contract_readme(
                project_name="Test Project",
                project_description="A test project",
                stack_profile={"languages": {"Python": 100}},
                architecture={"structure_type": "monolith"},
                contracts=[
                    {"name": "Setup", "description": "Initial setup"},
                    {"name": "Build", "description": "Build the app"},
                ],
            )
            assert result is not None
            assert "# Test Project" in result
            mock_chat.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_llm_error_returns_none(self):
        """LLM failure should return None, not raise."""
        with patch("app.config.settings.ANTHROPIC_API_KEY", "test-key"), \
             patch("app.config.settings.LLM_PLANNER_MODEL", "test-model"), \
             patch("app.services.readme_generator.chat", new_callable=AsyncMock) as mock_chat:
            mock_chat.side_effect = Exception("LLM failed")

            result = await generate_contract_readme(
                project_name="Test",
                project_description=None,
                stack_profile=None,
                architecture=None,
                contracts=[],
            )
            assert result is None


# ---------------------------------------------------------------------------
# generate_project_readme tests
# ---------------------------------------------------------------------------


class TestGenerateProjectReadme:
    @pytest.mark.asyncio
    async def test_no_api_key_returns_none(self):
        with patch("app.config.settings.ANTHROPIC_API_KEY", ""):
            result = await generate_project_readme(
                project_name="Test",
                project_description=None,
                stack_profile=None,
                architecture=None,
                file_structure=None,
                build_summary=None,
            )
            assert result is None

    @pytest.mark.asyncio
    async def test_llm_call_returns_full_readme(self):
        with patch("app.config.settings.ANTHROPIC_API_KEY", "test-key"), \
             patch("app.config.settings.LLM_PLANNER_MODEL", "test-model"), \
             patch("app.services.readme_generator.chat", new_callable=AsyncMock) as mock_chat:
            mock_chat.return_value = {"text": "# Full Project\n\nOverview.\n\n## Setup\n..."}

            result = await generate_project_readme(
                project_name="Full Project",
                project_description="A production app",
                stack_profile={"languages": {"Python": 80, "TypeScript": 20}},
                architecture={"structure_type": "modular"},
                file_structure=["app/main.py", "web/index.html", "tests/test_main.py"],
                build_summary={"phases_completed": 3, "total_commits": 12},
            )
            assert result is not None
            assert "Full Project" in result

    @pytest.mark.asyncio
    async def test_file_tree_truncation(self):
        """Files beyond 50 should be truncated with '...and N more'."""
        with patch("app.config.settings.ANTHROPIC_API_KEY", "test-key"), \
             patch("app.config.settings.LLM_PLANNER_MODEL", "test-model"), \
             patch("app.services.readme_generator.chat", new_callable=AsyncMock) as mock_chat:
            mock_chat.return_value = {"text": "# Big Project\n\nLots of files."}

            big_tree = [f"src/file_{i}.py" for i in range(100)]
            result = await generate_project_readme(
                project_name="Big",
                project_description=None,
                stack_profile=None,
                architecture=None,
                file_structure=big_tree,
                build_summary=None,
            )
            assert result is not None
            # Verify the chat was called (the truncation happens in the prompt)
            call_args = mock_chat.call_args
            # chat() is called with keyword args
            user_msg = call_args.kwargs["messages"][0]["content"]
            assert "50 more" in user_msg
