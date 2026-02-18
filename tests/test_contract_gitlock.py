"""Tests for Phase 46 — Contracts Server-Side Lock & Git Exclusion.

Verifies that:
  1. inject_forge_gitignore is idempotent (running twice = no duplication)
  2. Forge/ directory contents are never included in staged files
  3. Existing .gitignore content is preserved when appending
  4. A fresh repo with no .gitignore gets one created
  5. Builder directives include the contract-exclusion instruction
  6. The add_all + commit pipeline excludes Forge/ via belt-and-suspenders
"""

import subprocess
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from app.services.build.context import (
    inject_forge_gitignore,
    _FORGE_GITIGNORE_MARKER,
    _FORGE_GITIGNORE_RULES,
)
from app.clients import git_client


# ---------------------------------------------------------------------------
# inject_forge_gitignore
# ---------------------------------------------------------------------------


class TestInjectForgeGitignore:
    """Tests for the .gitignore injection function."""

    def test_creates_gitignore_when_missing(self, tmp_path: Path):
        """If .gitignore does not exist, it is created with Forge rules."""
        result = inject_forge_gitignore(tmp_path)

        gi = tmp_path / ".gitignore"
        assert gi.exists()
        assert result is True

        content = gi.read_text(encoding="utf-8")
        assert _FORGE_GITIGNORE_MARKER in content
        assert "Forge/" in content
        assert "*.forge-contract" in content
        assert ".forge/" in content

    def test_appends_to_existing_gitignore(self, tmp_path: Path):
        """If .gitignore exists without Forge rules, they are appended."""
        gi = tmp_path / ".gitignore"
        gi.write_text("node_modules/\n.env\n", encoding="utf-8")

        result = inject_forge_gitignore(tmp_path)

        assert result is True
        content = gi.read_text(encoding="utf-8")
        # Existing content preserved
        assert "node_modules/" in content
        assert ".env" in content
        # Forge rules added
        assert _FORGE_GITIGNORE_MARKER in content
        assert "Forge/" in content

    def test_idempotent_no_duplication(self, tmp_path: Path):
        """Running twice does not duplicate the Forge rules."""
        gi = tmp_path / ".gitignore"
        gi.write_text("node_modules/\n", encoding="utf-8")

        inject_forge_gitignore(tmp_path)
        inject_forge_gitignore(tmp_path)  # second call

        content = gi.read_text(encoding="utf-8")
        assert content.count(_FORGE_GITIGNORE_MARKER) == 1
        assert content.count("Forge/") == 1

    def test_preserves_existing_content_exactly(self, tmp_path: Path):
        """Existing .gitignore content is preserved byte-for-byte (minus appended rules)."""
        original = "# My project\nnode_modules/\n*.pyc\n__pycache__/\n"
        gi = tmp_path / ".gitignore"
        gi.write_text(original, encoding="utf-8")

        inject_forge_gitignore(tmp_path)

        content = gi.read_text(encoding="utf-8")
        assert content.startswith(original)
        assert _FORGE_GITIGNORE_MARKER in content

    def test_no_double_newline_when_existing_ends_with_newline(self, tmp_path: Path):
        """If the existing .gitignore ends with \\n, no extra blank line is inserted."""
        gi = tmp_path / ".gitignore"
        gi.write_text("node_modules/\n", encoding="utf-8")

        inject_forge_gitignore(tmp_path)

        content = gi.read_text(encoding="utf-8")
        # Should not start with double newline before marker
        assert "\n\n\n" not in content

    def test_handles_existing_without_trailing_newline(self, tmp_path: Path):
        """If the existing .gitignore has no trailing newline, adds one before rules."""
        gi = tmp_path / ".gitignore"
        gi.write_text("node_modules/", encoding="utf-8")  # no trailing \n

        inject_forge_gitignore(tmp_path)

        content = gi.read_text(encoding="utf-8")
        assert "node_modules/\n" in content
        assert _FORGE_GITIGNORE_MARKER in content

    def test_returns_false_when_already_present(self, tmp_path: Path):
        """Returns False if the Forge rules are already present."""
        gi = tmp_path / ".gitignore"
        gi.write_text(
            "node_modules/\n" + "\n".join(_FORGE_GITIGNORE_RULES) + "\n",
            encoding="utf-8",
        )

        result = inject_forge_gitignore(tmp_path)
        assert result is False

    def test_string_path_argument(self, tmp_path: Path):
        """Accepts a string path as well as a Path object."""
        result = inject_forge_gitignore(str(tmp_path))

        gi = tmp_path / ".gitignore"
        assert gi.exists()
        assert result is True


# ---------------------------------------------------------------------------
# exclude_contracts_from_staging
# ---------------------------------------------------------------------------


class TestExcludeContractsFromStaging:
    """Tests for the git rm --cached belt-and-suspenders."""

    @pytest.mark.asyncio
    @patch("app.clients.git_client._run_git", new_callable=AsyncMock)
    async def test_runs_git_rm_cached_when_forge_dir_exists(
        self, mock_run, tmp_path: Path
    ):
        """Should run git rm --cached on Forge/ when the directory exists."""
        forge_dir = tmp_path / "Forge"
        forge_dir.mkdir()
        (forge_dir / "Contracts").mkdir()
        (forge_dir / "Contracts" / "blueprint.md").write_text("x")

        await git_client.exclude_contracts_from_staging(tmp_path)

        mock_run.assert_called_once()
        args = mock_run.call_args[0][0]
        assert "rm" in args
        assert "--cached" in args
        assert "--ignore-unmatch" in args
        assert "Forge/" in args

    @pytest.mark.asyncio
    @patch("app.clients.git_client._run_git", new_callable=AsyncMock)
    async def test_skips_when_no_forge_dir(self, mock_run, tmp_path: Path):
        """Should be a no-op when Forge/ directory doesn't exist."""
        await git_client.exclude_contracts_from_staging(tmp_path)

        mock_run.assert_not_called()

    @pytest.mark.asyncio
    @patch("app.clients.git_client._run_git", new_callable=AsyncMock)
    async def test_non_fatal_on_error(self, mock_run, tmp_path: Path):
        """Should not raise if git rm --cached fails."""
        forge_dir = tmp_path / "Forge"
        forge_dir.mkdir()

        mock_run.side_effect = RuntimeError("git rm failed")

        # Should not raise
        await git_client.exclude_contracts_from_staging(tmp_path)


# ---------------------------------------------------------------------------
# add_all now calls exclude_contracts_from_staging
# ---------------------------------------------------------------------------


class TestAddAllExcludesContracts:
    """Verify add_all calls exclude_contracts_from_staging after staging."""

    @pytest.mark.asyncio
    @patch("app.clients.git_client.exclude_contracts_from_staging", new_callable=AsyncMock)
    @patch("app.clients.git_client._run_git", new_callable=AsyncMock)
    async def test_add_all_excludes_contracts(self, mock_run, mock_exclude):
        """add_all should call _run_git(['add', '-A']) then exclude_contracts."""
        await git_client.add_all("/tmp/test")

        # _run_git called for git add -A
        mock_run.assert_called_once()
        args = mock_run.call_args[0][0]
        assert args == ["add", "-A"]

        # exclude_contracts_from_staging called after
        mock_exclude.assert_called_once_with("/tmp/test")


# ---------------------------------------------------------------------------
# commit also excludes contracts
# ---------------------------------------------------------------------------


class TestCommitExcludesContracts:
    """Verify commit unstages Forge/ before committing."""

    @pytest.mark.asyncio
    @patch("app.clients.git_client._run_git", new_callable=AsyncMock)
    async def test_commit_calls_exclude_contracts(self, mock_run):
        """commit() should call exclude_contracts between add -A and commit."""
        # When exclude_contracts_from_staging is NOT mocked, _run_git handles
        # all calls including the git rm --cached.  Mock side effects:
        mock_run.side_effect = [
            "M  README.md",       # status --porcelain
            None,                  # config user.email
            None,                  # config user.name
            None,                  # add -A
            None,                  # rm --cached (from exclude_contracts — Forge/ absent so skipped)
            None,                  # commit -m
            "abc123def",           # rev-parse HEAD
        ]

        # Patch exclude_contracts so we can assert it's called,
        # but we also need to adjust the side_effect count since
        # the patched function won't consume a _run_git call.
        mock_run.side_effect = [
            "M  README.md",       # status --porcelain
            None,                  # config user.email
            None,                  # config user.name
            None,                  # add -A
            None,                  # commit -m
            "abc123def",           # rev-parse HEAD
        ]

        with patch(
            "app.clients.git_client.exclude_contracts_from_staging",
            new_callable=AsyncMock,
        ) as mock_exclude:
            sha = await git_client.commit("/tmp/test", "test commit")

        mock_exclude.assert_called_once()
        assert sha == "abc123def"


# ---------------------------------------------------------------------------
# Builder directive contract exclusion
# ---------------------------------------------------------------------------


class TestBuilderDirectiveContractExclusion:
    """Verify builder system prompts include contract exclusion rules."""

    def test_build_service_system_prompt_has_exclusion(self):
        """The main builder system prompt mentions contract exclusion."""
        # Import to get access to the module
        from app.services import build_service

        # Read the file and check for the directive text
        import inspect
        source = inspect.getsource(build_service)
        assert "Contract Exclusion" in source
        assert "NEVER include Forge contract" in source

    def test_upgrade_executor_prompt_has_exclusion(self):
        """The upgrade executor builder prompt mentions contract exclusion."""
        from app.services.upgrade_executor import _BUILDER_SYSTEM_PROMPT

        assert "CONTRACT EXCLUSION" in _BUILDER_SYSTEM_PROMPT
        assert "Forge/" in _BUILDER_SYSTEM_PROMPT
        assert "NEVER" in _BUILDER_SYSTEM_PROMPT

    def test_builder_directive_template_has_exclusion(self):
        """The builder directive template includes contract exclusion."""
        template_path = Path(__file__).parent.parent / "app" / "templates" / "contracts" / "builder_directive.md"
        content = template_path.read_text(encoding="utf-8")
        assert "Contract Exclusion" in content
        assert "Forge/" in content
