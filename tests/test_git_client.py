"""Tests for app/clients/git_client.py -- thin async git wrapper."""

import asyncio
from unittest.mock import AsyncMock, patch, MagicMock

import pytest

from app.clients import git_client


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_process(returncode: int = 0, stdout: str = "", stderr: str = ""):
    proc = AsyncMock()
    proc.returncode = returncode
    proc.communicate = AsyncMock(return_value=(stdout.encode(), stderr.encode()))
    return proc


# ---------------------------------------------------------------------------
# Tests: _run_git
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@patch("app.clients.git_client.asyncio.create_subprocess_exec")
async def test_run_git_success(mock_exec):
    """_run_git runs a command and returns stdout."""
    mock_exec.return_value = _mock_process(0, "ok", "")

    result = await git_client._run_git(["status"], cwd="/tmp/test")

    mock_exec.assert_called_once()
    assert result == "ok"


@pytest.mark.asyncio
@patch("app.clients.git_client.asyncio.create_subprocess_exec")
async def test_run_git_failure(mock_exec):
    """_run_git raises RuntimeError on non-zero exit."""
    mock_exec.return_value = _mock_process(128, "", "fatal: not a git repo")

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
