"""Tests for app/clients/git_client.py -- thin async git wrapper."""

import subprocess
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
# Tests: init_repo
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@patch("app.clients.git_client._run_git", new_callable=AsyncMock)
async def test_init_repo(mock_run):
    """init_repo runs git init and sets committer identity."""
    await git_client.init_repo("/tmp/test")

    # Should make multiple calls (init + config)
    assert mock_run.call_count >= 1
    # First call should be 'init'
    first_args = mock_run.call_args_list[0][0][0]
    assert "init" in first_args


# ---------------------------------------------------------------------------
# Tests: add_all
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@patch("app.clients.git_client._run_git", new_callable=AsyncMock)
async def test_add_all(mock_run):
    """add_all stages all changes."""
    await git_client.add_all("/tmp/test")

    mock_run.assert_called_once()
    args = mock_run.call_args[0][0]  # First positional arg is the command list
    assert "add" in args


# ---------------------------------------------------------------------------
# Tests: commit
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@patch("app.clients.git_client._run_git", new_callable=AsyncMock)
async def test_commit(mock_run):
    """commit runs git commit with the given message."""
    await git_client.commit("/tmp/test", "test commit")

    # commit may call _run_git multiple times (status check + commit)
    assert mock_run.call_count >= 1
    # Find the call that includes 'commit'
    commit_calls = [c for c in mock_run.call_args_list if "commit" in c[0][0]]
    assert len(commit_calls) >= 1


# ---------------------------------------------------------------------------
# Tests: clone_repo
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@patch("app.clients.git_client._run_git", new_callable=AsyncMock)
async def test_clone_repo(mock_run):
    """clone_repo clones with access token injected into URL."""
    await git_client.clone_repo("https://github.com/owner/repo.git", "/tmp/dest", access_token="ghp_testtoken")

    mock_run.assert_called_once()
    args = mock_run.call_args[0][0]  # command list
    assert "clone" in args
    # Token should be in the URL arg
    url_arg = [a for a in args if "github.com" in str(a)]
    assert len(url_arg) > 0
    assert "ghp_testtoken" in str(url_arg[0])


# ---------------------------------------------------------------------------
# Tests: push
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@patch("app.clients.git_client._run_git", new_callable=AsyncMock)
async def test_push(mock_run):
    """push runs git push."""
    await git_client.push("/tmp/test")

    mock_run.assert_called_once()
    args = mock_run.call_args[0][0]
    assert "push" in args


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
