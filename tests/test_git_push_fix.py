"""Tests for the git-push / workspace-persistence bug fix.

Covers:
- _get_workspace_dir() uses WORKSPACE_DIR env var, not tempfile.gettempdir()
- commit_plan_to_git() calls git_client.commit and git_client.push
- approve_plan() triggers commit_plan_to_git on approval and returns sha
- approve_plan() is non-fatal when commit_plan_to_git raises
- /push slash command: fetch is called before force-with-lease push
- pull_rebase no longer uses --allow-unrelated-histories
- git_client.fetch() function exists and calls git fetch
"""

import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.build._state import register_plan_review
from app.services import build_service

_USER_ID = uuid.uuid4()
_PROJECT_ID = uuid.uuid4()
_BUILD_ID = uuid.uuid4()


# ---------------------------------------------------------------------------
# _get_workspace_dir
# ---------------------------------------------------------------------------


class TestGetWorkspaceDir:
    def test_uses_workspace_dir_env_var(self, tmp_path):
        """When WORKSPACE_DIR is set, workspace is inside that directory."""
        with patch("app.services.build_service.settings") as mock_settings:
            mock_settings.WORKSPACE_DIR = str(tmp_path)
            result = build_service._get_workspace_dir(_PROJECT_ID)
        assert result == tmp_path / str(_PROJECT_ID)

    def test_defaults_to_home_forgeguard_when_not_set(self):
        """When WORKSPACE_DIR is blank, falls back to ~/.forgeguard/workspaces."""
        with patch("app.services.build_service.settings") as mock_settings:
            mock_settings.WORKSPACE_DIR = ""
            result = build_service._get_workspace_dir(_PROJECT_ID)
        expected = Path.home() / ".forgeguard" / "workspaces" / str(_PROJECT_ID)
        assert result == expected

    def test_does_not_use_tempfile_gettempdir(self):
        """Workspace must never be inside the system temp directory."""
        import tempfile
        with patch("app.services.build_service.settings") as mock_settings:
            mock_settings.WORKSPACE_DIR = ""
            result = build_service._get_workspace_dir(_PROJECT_ID)
        assert not str(result).startswith(tempfile.gettempdir())


# ---------------------------------------------------------------------------
# commit_plan_to_git
# ---------------------------------------------------------------------------


class TestCommitPlanToGit:
    @pytest.mark.asyncio
    @patch("app.services.build_service.git_client")
    @patch("app.services.build_service.get_user_by_id")
    @patch("app.services.build_service.build_repo")
    @patch("app.services.build_service.project_repo")
    async def test_commits_and_pushes(
        self,
        mock_project_repo,
        mock_build_repo,
        mock_get_user,
        mock_git,
        tmp_path,
    ):
        """commit_plan_to_git writes plan file, commits it, and pushes to GitHub."""
        mock_project_repo.get_project_by_id = AsyncMock(
            return_value={"id": _PROJECT_ID, "user_id": _USER_ID, "repo_full_name": "acme/repo"}
        )
        mock_project_repo.get_cached_plan = AsyncMock(return_value={"phases": []})
        mock_build_repo.get_latest_build_for_project = AsyncMock(
            return_value={"id": _BUILD_ID, "target_ref": "acme/repo", "branch": "main"}
        )
        mock_build_repo.append_build_log = AsyncMock()
        mock_get_user.return_value = {"id": _USER_ID, "access_token": "gh_token_123"}
        mock_git.add_all = AsyncMock()
        mock_git.commit = AsyncMock(return_value="abc1234567890")
        mock_git.set_remote = AsyncMock()
        mock_git.push = AsyncMock()

        with patch("app.services.build_service._get_workspace_dir", return_value=tmp_path):
            (tmp_path).mkdir(parents=True, exist_ok=True)
            result = await build_service.commit_plan_to_git(_PROJECT_ID, _USER_ID)

        assert result["ok"] is True
        assert result["sha"] == "abc1234567890"
        assert result["pushed"] is True
        mock_git.commit.assert_awaited_once()
        mock_git.push.assert_awaited_once()

    @pytest.mark.asyncio
    @patch("app.services.build_service.git_client")
    @patch("app.services.build_service.get_user_by_id")
    @patch("app.services.build_service.build_repo")
    @patch("app.services.build_service.project_repo")
    async def test_push_failure_is_non_fatal(
        self,
        mock_project_repo,
        mock_build_repo,
        mock_get_user,
        mock_git,
        tmp_path,
    ):
        """If the git push fails, commit_plan_to_git still returns ok with the local SHA."""
        mock_project_repo.get_project_by_id = AsyncMock(
            return_value={"id": _PROJECT_ID, "user_id": _USER_ID, "repo_full_name": "acme/repo"}
        )
        mock_project_repo.get_cached_plan = AsyncMock(return_value={"phases": []})
        mock_build_repo.get_latest_build_for_project = AsyncMock(
            return_value={"id": _BUILD_ID, "target_ref": "acme/repo", "branch": "main"}
        )
        mock_build_repo.append_build_log = AsyncMock()
        mock_get_user.return_value = {"id": _USER_ID, "access_token": "gh_token_123"}
        mock_git.add_all = AsyncMock()
        mock_git.commit = AsyncMock(return_value="deadbeef1234")
        mock_git.set_remote = AsyncMock()
        mock_git.push = AsyncMock(side_effect=RuntimeError("network error"))

        with patch("app.services.build_service._get_workspace_dir", return_value=tmp_path):
            (tmp_path).mkdir(parents=True, exist_ok=True)
            result = await build_service.commit_plan_to_git(_PROJECT_ID, _USER_ID)

        assert result["ok"] is True
        assert result["sha"] == "deadbeef1234"
        assert result["pushed"] is False


# ---------------------------------------------------------------------------
# approve_plan — wired to commit_plan_to_git
# ---------------------------------------------------------------------------


class TestApprovePlanCommitsToGit:
    @pytest.mark.asyncio
    @patch("app.services.build_service.commit_plan_to_git", new_callable=AsyncMock)
    @patch("app.services.build_service.build_repo")
    @patch("app.services.build_service.project_repo")
    async def test_approve_plan_triggers_commit(
        self, mock_project_repo, mock_build_repo, mock_commit_plan
    ):
        """Approving the plan calls commit_plan_to_git and returns the SHA."""
        mock_project_repo.get_project_by_id = AsyncMock(
            return_value={"id": _PROJECT_ID, "user_id": _USER_ID}
        )
        mock_build_repo.get_latest_build_for_project = AsyncMock(
            return_value={"id": _BUILD_ID, "user_id": _USER_ID, "status": "running"}
        )
        mock_build_repo.append_build_log = AsyncMock()
        mock_commit_plan.return_value = {"ok": True, "sha": "cafebabe99", "pushed": True}

        register_plan_review(str(_BUILD_ID))
        result = await build_service.approve_plan(_PROJECT_ID, _USER_ID, action="approve")

        mock_commit_plan.assert_awaited_once_with(_PROJECT_ID, _USER_ID)
        assert result["ok"] is True
        assert result["sha"] == "cafebabe99"

    @pytest.mark.asyncio
    @patch("app.services.build_service.commit_plan_to_git", new_callable=AsyncMock)
    @patch("app.services.build_service.build_repo")
    @patch("app.services.build_service.project_repo")
    async def test_approve_plan_commit_failure_is_non_fatal(
        self, mock_project_repo, mock_build_repo, mock_commit_plan
    ):
        """If commit_plan_to_git raises, approve_plan still succeeds with empty sha."""
        mock_project_repo.get_project_by_id = AsyncMock(
            return_value={"id": _PROJECT_ID, "user_id": _USER_ID}
        )
        mock_build_repo.get_latest_build_for_project = AsyncMock(
            return_value={"id": _BUILD_ID, "user_id": _USER_ID, "status": "running"}
        )
        mock_build_repo.append_build_log = AsyncMock()
        mock_commit_plan.side_effect = Exception("workspace not ready")

        register_plan_review(str(_BUILD_ID))
        result = await build_service.approve_plan(_PROJECT_ID, _USER_ID, action="approve")

        assert result["ok"] is True
        assert result["sha"] == ""  # empty because commit failed

    @pytest.mark.asyncio
    @patch("app.services.build_service.commit_plan_to_git", new_callable=AsyncMock)
    @patch("app.services.build_service.build_repo")
    @patch("app.services.build_service.project_repo")
    async def test_reject_plan_does_not_commit(
        self, mock_project_repo, mock_build_repo, mock_commit_plan
    ):
        """Rejecting the plan must NOT trigger a git commit."""
        mock_project_repo.get_project_by_id = AsyncMock(
            return_value={"id": _PROJECT_ID, "user_id": _USER_ID}
        )
        mock_build_repo.get_latest_build_for_project = AsyncMock(
            return_value={"id": _BUILD_ID, "user_id": _USER_ID, "status": "running"}
        )
        mock_build_repo.append_build_log = AsyncMock()

        register_plan_review(str(_BUILD_ID))
        result = await build_service.approve_plan(_PROJECT_ID, _USER_ID, action="reject")

        mock_commit_plan.assert_not_awaited()
        assert result["action"] == "reject"


# ---------------------------------------------------------------------------
# git_client.fetch() and pull_rebase() fixes
# ---------------------------------------------------------------------------


class TestGitClientFetch:
    @pytest.mark.asyncio
    async def test_fetch_calls_git_fetch(self):
        """git_client.fetch() runs 'git fetch <remote>'."""
        from app.clients import git_client

        with patch("app.clients.git_client._run_git", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = ""
            await git_client.fetch("/fake/repo", access_token="tok")

        fetch_call = mock_run.call_args_list[0]
        assert fetch_call.args[0] == ["fetch", "origin"]

    @pytest.mark.asyncio
    async def test_pull_rebase_no_longer_uses_allow_unrelated_histories(self):
        """pull_rebase must NOT pass --allow-unrelated-histories to git rebase."""
        from app.clients import git_client

        with patch("app.clients.git_client._run_git", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = ""
            await git_client.pull_rebase("/fake/repo")

        git_args = mock_run.call_args_list[0].args[0]
        assert "--allow-unrelated-histories" not in git_args
        assert "--rebase" in git_args


class TestSlashPushFetchBeforeForce:
    @pytest.mark.asyncio
    @patch("app.services.build_service.git_client")
    @patch("app.services.build_service.get_user_by_id")
    @patch("app.services.build_service.build_repo")
    @patch("app.services.build_service.project_repo")
    async def test_fetch_called_before_push(
        self, mock_project_repo, mock_build_repo, mock_get_user, mock_git, tmp_path
    ):
        """/push calls git_client.fetch before attempting any push."""
        (tmp_path / ".git").mkdir()  # simulate existing repo

        mock_project_repo.get_project_by_id = AsyncMock(
            return_value={"id": _PROJECT_ID, "user_id": _USER_ID, "repo_full_name": "acme/repo"}
        )
        mock_build_repo.get_latest_build_for_project = AsyncMock(
            return_value={
                "id": _BUILD_ID, "user_id": _USER_ID, "status": "running",
                "working_dir": str(tmp_path), "branch": "main", "target_ref": "acme/repo",
            }
        )
        mock_build_repo.append_build_log = AsyncMock()
        mock_get_user.return_value = {"id": _USER_ID, "access_token": "gh_tok"}
        mock_git.add_all = AsyncMock()
        mock_git.commit = AsyncMock(return_value="aabbccdd")
        mock_git.set_remote = AsyncMock()
        mock_git.fetch = AsyncMock()
        mock_git.pull_rebase = AsyncMock()
        mock_git.push = AsyncMock()

        result = await build_service.interject_build(_PROJECT_ID, _USER_ID, "/push")

        mock_git.fetch.assert_awaited_once()
        mock_git.push.assert_awaited_once()
        assert result["status"] == "pushed"

    @pytest.mark.asyncio
    @patch("app.services.build_service.git_client")
    @patch("app.services.build_service.get_user_by_id")
    @patch("app.services.build_service.build_repo")
    @patch("app.services.build_service.project_repo")
    async def test_force_with_lease_used_when_pull_rebase_fails(
        self, mock_project_repo, mock_build_repo, mock_get_user, mock_git, tmp_path
    ):
        """When pull_rebase fails, push uses force_with_lease=True."""
        (tmp_path / ".git").mkdir()

        mock_project_repo.get_project_by_id = AsyncMock(
            return_value={"id": _PROJECT_ID, "user_id": _USER_ID, "repo_full_name": "acme/repo"}
        )
        mock_build_repo.get_latest_build_for_project = AsyncMock(
            return_value={
                "id": _BUILD_ID, "user_id": _USER_ID, "status": "running",
                "working_dir": str(tmp_path), "branch": "main", "target_ref": "acme/repo",
            }
        )
        mock_build_repo.append_build_log = AsyncMock()
        mock_get_user.return_value = {"id": _USER_ID, "access_token": "gh_tok"}
        mock_git.add_all = AsyncMock()
        mock_git.commit = AsyncMock(return_value="deadbeef")
        mock_git.set_remote = AsyncMock()
        mock_git.fetch = AsyncMock()
        mock_git.pull_rebase = AsyncMock(side_effect=RuntimeError("conflict"))
        mock_git.push = AsyncMock()

        await build_service.interject_build(_PROJECT_ID, _USER_ID, "/push")

        push_call_kwargs = mock_git.push.call_args.kwargs
        assert push_call_kwargs.get("force_with_lease") is True
