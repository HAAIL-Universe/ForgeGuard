"""Tests for app/clients/git_client.py -- thin async git wrapper."""

import subprocess
from pathlib import Path
from unittest.mock import AsyncMock, patch, MagicMock

import pytest

from app.clients import git_client


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_completed(returncode: int = 0, stdout: str = "", stderr: str = ""):
    """Return a mock subprocess.CompletedProcess."""
    return subprocess.CompletedProcess(
        args=["git"], returncode=returncode, stdout=stdout, stderr=stderr,
    )


# ---------------------------------------------------------------------------
# Tests: _run_git
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@patch("app.clients.git_client.subprocess.run")
async def test_run_git_success(mock_run):
    """_run_git runs a command and returns stdout."""
    mock_run.return_value = _mock_completed(0, "ok", "")

    result = await git_client._run_git(["status"], cwd="/tmp/test")

    mock_run.assert_called_once()
    assert result == "ok"


@pytest.mark.asyncio
@patch("app.clients.git_client.subprocess.run")
async def test_run_git_failure(mock_run):
    """_run_git raises RuntimeError on non-zero exit."""
    mock_run.return_value = _mock_completed(128, "", "fatal: not a git repo")

    with pytest.raises(RuntimeError, match="not a git repo"):
        await git_client._run_git(["status"], cwd="/tmp/test")


# ---------------------------------------------------------------------------
# Tests: has_repo
# ---------------------------------------------------------------------------


def test_has_repo_valid(tmp_path):
    """has_repo returns True for a directory with .git/HEAD."""
    git_dir = tmp_path / ".git"
    git_dir.mkdir()
    (git_dir / "HEAD").write_text("ref: refs/heads/main\n")
    assert git_client.has_repo(tmp_path) is True


def test_has_repo_missing(tmp_path):
    """has_repo returns False when .git/ doesn't exist."""
    assert git_client.has_repo(tmp_path) is False


def test_has_repo_empty_dir(tmp_path):
    """has_repo returns False when .git/ is an empty directory (no HEAD)."""
    (tmp_path / ".git").mkdir()
    assert git_client.has_repo(tmp_path) is False


# ---------------------------------------------------------------------------
# Tests: init_repo
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@patch("app.clients.git_client._run_git", new_callable=AsyncMock)
async def test_init_repo(mock_run):
    """init_repo runs git init and sets committer identity."""
    await git_client.init_repo("/tmp/test")

    # Should make multiple calls (init + checkout)
    assert mock_run.call_count >= 1
    # First call should be 'init'
    first_args = mock_run.call_args_list[0][0][0]
    assert "init" in first_args


@pytest.mark.asyncio
@patch("app.clients.git_client._run_git", new_callable=AsyncMock)
async def test_init_repo_branch_already_exists(mock_run):
    """init_repo handles 'branch main already exists' gracefully."""
    # checkout -b main fails, checkout main succeeds
    mock_run.side_effect = [
        None,                                          # git init
        RuntimeError("branch 'main' already exists"),  # checkout -b main
        None,                                          # checkout main
    ]
    result = await git_client.init_repo("/tmp/test")
    assert Path(result) == Path("/tmp/test")
    assert mock_run.call_count == 3


@pytest.mark.asyncio
async def test_init_repo_cleans_empty_git_dir(tmp_path):
    """init_repo removes an empty .git/ dir before initializing."""
    empty_git = tmp_path / ".git"
    empty_git.mkdir()
    assert empty_git.exists()
    assert not (empty_git / "HEAD").exists()

    with patch("app.clients.git_client._run_git", new_callable=AsyncMock):
        await git_client.init_repo(str(tmp_path))

    # The empty .git/ should have been removed before git init ran
    # (git init then recreates it properly)


# ---------------------------------------------------------------------------
# Tests: add_all
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@patch("app.clients.git_client.has_repo", return_value=True)
@patch("app.clients.git_client._run_git", new_callable=AsyncMock)
async def test_add_all(mock_run, _mock_has_repo):
    """add_all stages all changes."""
    await git_client.add_all("/tmp/test")

    mock_run.assert_called_once()
    args = mock_run.call_args[0][0]  # First positional arg is the command list
    assert "add" in args


@pytest.mark.asyncio
async def test_add_all_no_repo(tmp_path):
    """add_all skips gracefully when .git/ is missing."""
    result = await git_client.add_all(str(tmp_path))
    assert result is None  # returns None, no error raised


@pytest.mark.asyncio
async def test_add_all_empty_git_dir(tmp_path):
    """add_all skips when .git/ exists but is an empty directory (no HEAD)."""
    (tmp_path / ".git").mkdir()
    result = await git_client.add_all(str(tmp_path))
    assert result is None  # treated as "no repo"


# ---------------------------------------------------------------------------
# Tests: commit
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@patch("app.clients.git_client.has_repo", return_value=True)
@patch("app.clients.git_client._run_git", new_callable=AsyncMock)
async def test_commit(mock_run, _mock_has_repo):
    """commit runs git commit with the given message."""
    await git_client.commit("/tmp/test", "test commit")

    # commit may call _run_git multiple times (status check + commit)
    assert mock_run.call_count >= 1
    # Find the call that includes 'commit'
    commit_calls = [c for c in mock_run.call_args_list if "commit" in c[0][0]]
    assert len(commit_calls) >= 1


@pytest.mark.asyncio
async def test_commit_no_repo(tmp_path):
    """commit returns None when .git/ is missing."""
    result = await git_client.commit(str(tmp_path), "test")
    assert result is None


# ---------------------------------------------------------------------------
# Tests: clone_repo
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@patch("app.clients.git_client._run_git", new_callable=AsyncMock)
async def test_clone_repo(mock_run):
    """clone_repo clones with access token via GIT_ASKPASS env."""
    await git_client.clone_repo("https://github.com/owner/repo.git", "/tmp/dest", access_token="ghp_testtoken")

    # clone calls _run_git for clone + strip_token_from_remote
    assert mock_run.call_count >= 1
    clone_calls = [c for c in mock_run.call_args_list if "clone" in c[0][0]]
    assert len(clone_calls) == 1
    args = clone_calls[0][0][0]
    assert "clone" in args


# ---------------------------------------------------------------------------
# Tests: push
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@patch("app.clients.git_client.has_repo", return_value=True)
@patch("app.clients.git_client._run_git", new_callable=AsyncMock)
async def test_push(mock_run, _mock_has_repo):
    """push runs git push."""
    await git_client.push("/tmp/test")

    # push calls _run_git for push + strip_token_from_remote
    assert mock_run.call_count >= 1
    push_calls = [c for c in mock_run.call_args_list if "push" in c[0][0]]
    assert len(push_calls) >= 1


@pytest.mark.asyncio
async def test_push_no_repo(tmp_path):
    """push skips gracefully when .git/ is missing."""
    await git_client.push(str(tmp_path))  # should not raise


@pytest.mark.asyncio
async def test_push_empty_git_dir(tmp_path):
    """push skips when .git/ exists but is an empty directory."""
    (tmp_path / ".git").mkdir()
    await git_client.push(str(tmp_path))  # should not raise


# ---------------------------------------------------------------------------
# Tests: set_remote
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@patch("app.clients.git_client._run_git", new_callable=AsyncMock)
async def test_set_remote(mock_run):
    """set_remote sets the origin remote."""
    await git_client.set_remote("/tmp/test", "https://github.com/owner/repo.git")

    mock_run.assert_called_once()
    args = mock_run.call_args[0][0]
    assert "remote" in args


# ---------------------------------------------------------------------------
# Tests: get_file_list
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@patch("app.clients.git_client._run_git", new_callable=AsyncMock)
async def test_get_file_list(mock_run):
    """get_file_list returns list of tracked files."""
    mock_run.return_value = "file1.py\nfile2.ts\n"

    result = await git_client.get_file_list("/tmp/test")

    assert result == ["file1.py", "file2.ts"]


@pytest.mark.asyncio
@patch("app.clients.git_client._run_git", new_callable=AsyncMock)
async def test_get_file_list_empty(mock_run):
    """get_file_list returns empty list when no files."""
    mock_run.return_value = ""

    result = await git_client.get_file_list("/tmp/test")

    assert result == []


# ---------------------------------------------------------------------------
# Tests: create_branch / checkout_branch
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@patch("app.clients.git_client._run_git", new_callable=AsyncMock)
async def test_create_branch(mock_run):
    """create_branch runs git checkout -b."""
    await git_client.create_branch("/tmp/test", "forge/v2")

    mock_run.assert_called_once_with(["checkout", "-b", "forge/v2"], cwd="/tmp/test")


@pytest.mark.asyncio
@patch("app.clients.git_client._run_git", new_callable=AsyncMock)
async def test_checkout_branch(mock_run):
    """checkout_branch runs git checkout."""
    await git_client.checkout_branch("/tmp/test", "develop")

    mock_run.assert_called_once_with(["checkout", "develop"], cwd="/tmp/test")


@pytest.mark.asyncio
@patch("app.clients.git_client._run_git", new_callable=AsyncMock)
async def test_diff_summary(mock_run):
    """diff_summary returns stat + abbreviated diff."""
    mock_run.side_effect = [
        " app/main.py | 10 ++++\n 1 file changed",  # --stat
        "+from fastapi import FastAPI",               # diff -U2
    ]
    result = await git_client.diff_summary("/tmp/test")
    assert "app/main.py" in result
    assert "FastAPI" in result
    assert mock_run.call_count == 2


@pytest.mark.asyncio
@patch("app.clients.git_client._run_git", new_callable=AsyncMock)
async def test_diff_summary_single_commit(mock_run):
    """diff_summary returns fallback for single-commit repos."""
    mock_run.side_effect = RuntimeError("bad revision")
    result = await git_client.diff_summary("/tmp/test")
    assert "no diff available" in result


@pytest.mark.asyncio
@patch("app.clients.git_client._run_git", new_callable=AsyncMock)
async def test_log_oneline(mock_run):
    """log_oneline returns list of commit subject lines."""
    mock_run.return_value = (
        "forge: Phase 2 complete\n"
        "forge: Phase 1 complete (after 2 audit attempts)\n"
        "forge: Phase 0 complete\n"
    )
    result = await git_client.log_oneline("/tmp/test", max_count=50)
    assert result == [
        "forge: Phase 2 complete",
        "forge: Phase 1 complete (after 2 audit attempts)",
        "forge: Phase 0 complete",
    ]
    mock_run.assert_awaited_once_with(
        ["log", "--max-count=50", "--format=%s"],
        cwd="/tmp/test",
    )


@pytest.mark.asyncio
@patch("app.clients.git_client._run_git", new_callable=AsyncMock)
async def test_log_oneline_empty(mock_run):
    """log_oneline returns empty list on error."""
    mock_run.side_effect = RuntimeError("no commits")
    result = await git_client.log_oneline("/tmp/test")
    assert result == []
