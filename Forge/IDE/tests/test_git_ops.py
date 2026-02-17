"""Tests for forge_ide.git_ops — structured git operation wrappers.

Covers:
- All 9 git operations: success + failure paths
- Status parsing (staged / unstaged / untracked)
- Diff numstat parsing
- Commit with / without changes
- Push / pull with various options
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from forge_ide import git_ops


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _async_return(value):
    """Create an AsyncMock that returns *value*."""
    m = AsyncMock(return_value=value)
    return m


def _async_raise(exc):
    """Create an AsyncMock that raises *exc*."""
    m = AsyncMock(side_effect=exc)
    return m


# ---------------------------------------------------------------------------
# git_clone
# ---------------------------------------------------------------------------


class TestGitClone:
    @pytest.mark.asyncio
    async def test_success(self) -> None:
        with patch.object(
            git_ops.git_client, "clone_repo", _async_return("/tmp/dest")
        ):
            resp = await git_ops.git_clone("https://github.com/a/b.git", "/tmp/dest")
        assert resp.success is True
        assert resp.data["path"] == "/tmp/dest"
        assert resp.data["branch"] is None
        assert resp.data["shallow"] is True

    @pytest.mark.asyncio
    async def test_with_branch(self) -> None:
        with patch.object(
            git_ops.git_client, "clone_repo", _async_return("/tmp/dest")
        ):
            resp = await git_ops.git_clone(
                "https://github.com/a/b.git", "/tmp/dest", branch="dev"
            )
        assert resp.success is True
        assert resp.data["branch"] == "dev"

    @pytest.mark.asyncio
    async def test_failure(self) -> None:
        with patch.object(
            git_ops.git_client,
            "clone_repo",
            _async_raise(RuntimeError("clone failed")),
        ):
            resp = await git_ops.git_clone("https://bad.git", "/tmp/dest")
        assert resp.success is False
        assert "clone failed" in resp.error

    @pytest.mark.asyncio
    async def test_non_shallow(self) -> None:
        with patch.object(
            git_ops.git_client, "clone_repo", _async_return("/tmp/dest")
        ):
            resp = await git_ops.git_clone(
                "https://github.com/a/b.git", "/tmp/dest", shallow=False
            )
        assert resp.data["shallow"] is False


# ---------------------------------------------------------------------------
# git_init
# ---------------------------------------------------------------------------


class TestGitInit:
    @pytest.mark.asyncio
    async def test_success(self) -> None:
        with patch.object(
            git_ops.git_client, "init_repo", _async_return("/tmp/new")
        ):
            resp = await git_ops.git_init("/tmp/new")
        assert resp.success is True
        assert resp.data["path"] == "/tmp/new"
        assert resp.data["branch"] == "main"

    @pytest.mark.asyncio
    async def test_failure(self) -> None:
        with patch.object(
            git_ops.git_client,
            "init_repo",
            _async_raise(RuntimeError("init failed")),
        ):
            resp = await git_ops.git_init("/bad")
        assert resp.success is False
        assert "init failed" in resp.error


# ---------------------------------------------------------------------------
# git_branch_create
# ---------------------------------------------------------------------------


class TestGitBranchCreate:
    @pytest.mark.asyncio
    async def test_success(self) -> None:
        with patch.object(
            git_ops.git_client, "create_branch", _async_return(None)
        ):
            resp = await git_ops.git_branch_create("/repo", "feature-x")
        assert resp.success is True
        assert resp.data["name"] == "feature-x"
        assert resp.data["from_ref"] == "HEAD"

    @pytest.mark.asyncio
    async def test_failure(self) -> None:
        with patch.object(
            git_ops.git_client,
            "create_branch",
            _async_raise(RuntimeError("branch exists")),
        ):
            resp = await git_ops.git_branch_create("/repo", "dup")
        assert resp.success is False
        assert "branch exists" in resp.error


# ---------------------------------------------------------------------------
# git_branch_checkout
# ---------------------------------------------------------------------------


class TestGitBranchCheckout:
    @pytest.mark.asyncio
    async def test_success(self) -> None:
        with patch.object(
            git_ops.git_client, "checkout_branch", _async_return(None)
        ):
            resp = await git_ops.git_branch_checkout("/repo", "main")
        assert resp.success is True
        assert resp.data["name"] == "main"

    @pytest.mark.asyncio
    async def test_failure(self) -> None:
        with patch.object(
            git_ops.git_client,
            "checkout_branch",
            _async_raise(RuntimeError("no such branch")),
        ):
            resp = await git_ops.git_branch_checkout("/repo", "nope")
        assert resp.success is False
        assert "no such branch" in resp.error


# ---------------------------------------------------------------------------
# git_status
# ---------------------------------------------------------------------------


class TestGitStatus:
    @pytest.mark.asyncio
    async def test_empty_status(self) -> None:
        with patch.object(
            git_ops.git_client, "_run_git", _async_return("")
        ):
            resp = await git_ops.git_status("/repo")
        assert resp.success is True
        assert resp.data["staged"] == []
        assert resp.data["unstaged"] == []
        assert resp.data["untracked"] == []

    @pytest.mark.asyncio
    async def test_staged_only(self) -> None:
        raw = "M  src/main.py\nA  new_file.py"
        with patch.object(
            git_ops.git_client, "_run_git", _async_return(raw)
        ):
            resp = await git_ops.git_status("/repo")
        assert resp.success is True
        assert "src/main.py" in resp.data["staged"]
        assert "new_file.py" in resp.data["staged"]
        assert resp.data["unstaged"] == []

    @pytest.mark.asyncio
    async def test_unstaged_only(self) -> None:
        raw = " M src/main.py"
        with patch.object(
            git_ops.git_client, "_run_git", _async_return(raw)
        ):
            resp = await git_ops.git_status("/repo")
        assert resp.success is True
        assert "src/main.py" in resp.data["unstaged"]
        assert resp.data["staged"] == []

    @pytest.mark.asyncio
    async def test_untracked_only(self) -> None:
        raw = "?? new.txt\n?? temp.log"
        with patch.object(
            git_ops.git_client, "_run_git", _async_return(raw)
        ):
            resp = await git_ops.git_status("/repo")
        assert resp.success is True
        assert "new.txt" in resp.data["untracked"]
        assert "temp.log" in resp.data["untracked"]
        assert resp.data["staged"] == []
        assert resp.data["unstaged"] == []

    @pytest.mark.asyncio
    async def test_mixed_statuses(self) -> None:
        raw = "M  staged.py\n M unstaged.py\n?? untracked.py\nAM both.py"
        with patch.object(
            git_ops.git_client, "_run_git", _async_return(raw)
        ):
            resp = await git_ops.git_status("/repo")
        assert resp.success is True
        assert "staged.py" in resp.data["staged"]
        assert "unstaged.py" in resp.data["unstaged"]
        assert "untracked.py" in resp.data["untracked"]
        # AM = staged + unstaged
        assert "both.py" in resp.data["staged"]
        assert "both.py" in resp.data["unstaged"]

    @pytest.mark.asyncio
    async def test_renamed_files(self) -> None:
        raw = "R  old.py -> new.py"
        with patch.object(
            git_ops.git_client, "_run_git", _async_return(raw)
        ):
            resp = await git_ops.git_status("/repo")
        assert resp.success is True
        assert "new.py" in resp.data["staged"]

    @pytest.mark.asyncio
    async def test_failure(self) -> None:
        with patch.object(
            git_ops.git_client,
            "_run_git",
            _async_raise(RuntimeError("not a repo")),
        ):
            resp = await git_ops.git_status("/bad")
        assert resp.success is False
        assert "not a repo" in resp.error


# ---------------------------------------------------------------------------
# git_diff
# ---------------------------------------------------------------------------


class TestGitDiff:
    @pytest.mark.asyncio
    async def test_with_changes(self) -> None:
        raw = "10\t5\tsrc/main.py\n3\t0\tREADME.md"
        with patch.object(
            git_ops.git_client, "_run_git", _async_return(raw)
        ):
            resp = await git_ops.git_diff("/repo")
        assert resp.success is True
        assert resp.data["files_changed"] == 2
        assert resp.data["insertions"] == 13
        assert resp.data["deletions"] == 5
        assert len(resp.data["files"]) == 2
        assert resp.data["files"][0]["path"] == "src/main.py"
        assert resp.data["files"][0]["insertions"] == 10
        assert resp.data["files"][0]["deletions"] == 5

    @pytest.mark.asyncio
    async def test_no_changes(self) -> None:
        with patch.object(
            git_ops.git_client, "_run_git", _async_return("")
        ):
            resp = await git_ops.git_diff("/repo")
        assert resp.success is True
        assert resp.data["files_changed"] == 0
        assert resp.data["insertions"] == 0
        assert resp.data["deletions"] == 0

    @pytest.mark.asyncio
    async def test_binary_files(self) -> None:
        raw = "-\t-\timage.png\n5\t2\tmain.py"
        with patch.object(
            git_ops.git_client, "_run_git", _async_return(raw)
        ):
            resp = await git_ops.git_diff("/repo")
        assert resp.success is True
        assert resp.data["files_changed"] == 2
        # Binary file shows 0/0
        binary = [f for f in resp.data["files"] if f["path"] == "image.png"][0]
        assert binary["insertions"] == 0
        assert binary["deletions"] == 0

    @pytest.mark.asyncio
    async def test_failure(self) -> None:
        with patch.object(
            git_ops.git_client,
            "_run_git",
            _async_raise(RuntimeError("bad refs")),
        ):
            resp = await git_ops.git_diff("/repo", from_ref="abc", to_ref="def")
        assert resp.success is False
        assert "bad refs" in resp.error


# ---------------------------------------------------------------------------
# git_commit
# ---------------------------------------------------------------------------


class TestGitCommit:
    @pytest.mark.asyncio
    async def test_success(self) -> None:
        with patch.object(
            git_ops.git_client, "commit", _async_return("abc1234")
        ):
            resp = await git_ops.git_commit("/repo", "fix bug")
        assert resp.success is True
        assert resp.data["sha"] == "abc1234"
        assert resp.data["message"] == "fix bug"
        assert resp.data["committed"] is True

    @pytest.mark.asyncio
    async def test_nothing_to_commit(self) -> None:
        with patch.object(
            git_ops.git_client, "commit", _async_return(None)
        ):
            resp = await git_ops.git_commit("/repo", "no change")
        assert resp.success is True
        assert resp.data["sha"] is None
        assert resp.data["committed"] is False

    @pytest.mark.asyncio
    async def test_failure(self) -> None:
        with patch.object(
            git_ops.git_client,
            "commit",
            _async_raise(RuntimeError("commit error")),
        ):
            resp = await git_ops.git_commit("/repo", "msg")
        assert resp.success is False
        assert "commit error" in resp.error


# ---------------------------------------------------------------------------
# git_push
# ---------------------------------------------------------------------------


class TestGitPush:
    @pytest.mark.asyncio
    async def test_success(self) -> None:
        with patch.object(
            git_ops.git_client, "push", _async_return(None)
        ):
            resp = await git_ops.git_push("/repo")
        assert resp.success is True
        assert resp.data["remote"] == "origin"
        assert resp.data["branch"] == "main"
        assert resp.data["pushed"] is True

    @pytest.mark.asyncio
    async def test_custom_remote_branch(self) -> None:
        with patch.object(
            git_ops.git_client, "push", _async_return(None)
        ):
            resp = await git_ops.git_push(
                "/repo", remote="upstream", branch="dev"
            )
        assert resp.data["remote"] == "upstream"
        assert resp.data["branch"] == "dev"

    @pytest.mark.asyncio
    async def test_force_with_lease(self) -> None:
        mock_push = AsyncMock(return_value=None)
        with patch.object(git_ops.git_client, "push", mock_push):
            resp = await git_ops.git_push("/repo", force_with_lease=True)
        assert resp.success is True
        mock_push.assert_called_once_with(
            "/repo",
            remote="origin",
            branch="main",
            access_token=None,
            force_with_lease=True,
        )

    @pytest.mark.asyncio
    async def test_failure(self) -> None:
        with patch.object(
            git_ops.git_client,
            "push",
            _async_raise(RuntimeError("push rejected")),
        ):
            resp = await git_ops.git_push("/repo")
        assert resp.success is False
        assert "push rejected" in resp.error


# ---------------------------------------------------------------------------
# git_pull
# ---------------------------------------------------------------------------


class TestGitPull:
    @pytest.mark.asyncio
    async def test_success(self) -> None:
        with patch.object(
            git_ops.git_client, "pull_rebase", _async_return(None)
        ):
            resp = await git_ops.git_pull("/repo")
        assert resp.success is True
        assert resp.data["remote"] == "origin"
        assert resp.data["branch"] == "main"
        assert resp.data["updated"] is True

    @pytest.mark.asyncio
    async def test_custom_remote_branch(self) -> None:
        with patch.object(
            git_ops.git_client, "pull_rebase", _async_return(None)
        ):
            resp = await git_ops.git_pull(
                "/repo", remote="upstream", branch="dev"
            )
        assert resp.data["remote"] == "upstream"
        assert resp.data["branch"] == "dev"

    @pytest.mark.asyncio
    async def test_failure(self) -> None:
        with patch.object(
            git_ops.git_client,
            "pull_rebase",
            _async_raise(RuntimeError("conflict")),
        ):
            resp = await git_ops.git_pull("/repo")
        assert resp.success is False
        assert "conflict" in resp.error
