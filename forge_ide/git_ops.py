"""Structured git operations returning ``ToolResponse`` objects.

Wraps the low-level helpers in ``app.clients.git_client`` and returns
typed, structured ``ToolResponse`` payloads instead of raw strings.
Every function is async (matching the underlying git client) and
catches exceptions to produce ``ToolResponse.fail`` results.
"""

from __future__ import annotations

import logging
from pathlib import Path

from app.clients import git_client
from forge_ide.contracts import ToolResponse

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Clone / Init
# ---------------------------------------------------------------------------


async def git_clone(
    url: str,
    dest: str | Path,
    *,
    branch: str | None = None,
    shallow: bool = True,
    access_token: str | None = None,
) -> ToolResponse:
    """Clone a repository and return a structured result.

    Returns
    -------
    ToolResponse
        On success: ``{path, branch, shallow}``
    """
    try:
        result_path = await git_client.clone_repo(
            url, dest, branch=branch, shallow=shallow, access_token=access_token
        )
        return ToolResponse.ok(
            {"path": str(result_path), "branch": branch, "shallow": shallow}
        )
    except Exception as exc:
        logger.warning("git_clone failed: %s", exc)
        return ToolResponse.fail(str(exc))


async def git_init(path: str | Path) -> ToolResponse:
    """Initialise a new git repository.

    Returns
    -------
    ToolResponse
        On success: ``{path, branch: "main"}``
    """
    try:
        result_path = await git_client.init_repo(path)
        return ToolResponse.ok({"path": str(result_path), "branch": "main"})
    except Exception as exc:
        logger.warning("git_init failed: %s", exc)
        return ToolResponse.fail(str(exc))


# ---------------------------------------------------------------------------
# Branching
# ---------------------------------------------------------------------------


async def git_branch_create(repo_path: str | Path, name: str) -> ToolResponse:
    """Create and checkout a new branch.

    Returns
    -------
    ToolResponse
        On success: ``{name, from_ref: "HEAD"}``
    """
    try:
        await git_client.create_branch(repo_path, name)
        return ToolResponse.ok({"name": name, "from_ref": "HEAD"})
    except Exception as exc:
        logger.warning("git_branch_create failed: %s", exc)
        return ToolResponse.fail(str(exc))


async def git_branch_checkout(repo_path: str | Path, name: str) -> ToolResponse:
    """Checkout an existing branch.

    Returns
    -------
    ToolResponse
        On success: ``{name}``
    """
    try:
        await git_client.checkout_branch(repo_path, name)
        return ToolResponse.ok({"name": name})
    except Exception as exc:
        logger.warning("git_branch_checkout failed: %s", exc)
        return ToolResponse.fail(str(exc))


# ---------------------------------------------------------------------------
# Status / Diff
# ---------------------------------------------------------------------------


async def git_status(repo_path: str | Path) -> ToolResponse:
    """Get structured repository status.

    Parses ``git status --porcelain`` output into staged, unstaged,
    and untracked file lists.

    Returns
    -------
    ToolResponse
        On success: ``{staged: [str], unstaged: [str], untracked: [str]}``
    """
    try:
        raw = await git_client._run_git(["status", "--porcelain"], cwd=repo_path)
        staged: list[str] = []
        unstaged: list[str] = []
        untracked: list[str] = []

        for line in raw.splitlines():
            if len(line) < 3:
                continue
            x_code = line[0]  # index (staging area)
            y_code = line[1]  # work tree
            filepath = line[3:].strip()

            # Handle renames: "R  old -> new"
            if " -> " in filepath:
                filepath = filepath.split(" -> ")[-1]

            # Untracked
            if x_code == "?" and y_code == "?":
                untracked.append(filepath)
                continue

            # Staged changes (index column is non-space, non-?)
            if x_code not in (" ", "?"):
                staged.append(filepath)

            # Unstaged changes (work-tree column is non-space, non-?)
            if y_code not in (" ", "?"):
                unstaged.append(filepath)

        return ToolResponse.ok(
            {"staged": staged, "unstaged": unstaged, "untracked": untracked}
        )
    except Exception as exc:
        logger.warning("git_status failed: %s", exc)
        return ToolResponse.fail(str(exc))


async def git_diff(
    repo_path: str | Path,
    *,
    from_ref: str = "HEAD~1",
    to_ref: str = "HEAD",
) -> ToolResponse:
    """Get structured diff between two refs.

    Uses ``git diff --numstat`` for per-file insertion/deletion counts.

    Returns
    -------
    ToolResponse
        On success: ``{files_changed, insertions, deletions,
        files: [{path, insertions, deletions}]}``
    """
    try:
        raw = await git_client._run_git(
            ["diff", "--numstat", from_ref, to_ref], cwd=repo_path
        )

        files: list[dict[str, object]] = []
        total_ins = 0
        total_del = 0

        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue
            parts = line.split("\t")
            if len(parts) < 3:
                continue

            ins_str, del_str, fpath = parts[0], parts[1], parts[2]
            # Binary files show "-" instead of numbers
            ins = int(ins_str) if ins_str != "-" else 0
            dels = int(del_str) if del_str != "-" else 0
            total_ins += ins
            total_del += dels
            files.append({"path": fpath, "insertions": ins, "deletions": dels})

        return ToolResponse.ok(
            {
                "files_changed": len(files),
                "insertions": total_ins,
                "deletions": total_del,
                "files": files,
            }
        )
    except Exception as exc:
        logger.warning("git_diff failed: %s", exc)
        return ToolResponse.fail(str(exc))


# ---------------------------------------------------------------------------
# Commit / Push / Pull
# ---------------------------------------------------------------------------


async def git_commit(repo_path: str | Path, message: str) -> ToolResponse:
    """Commit staged changes.

    Returns
    -------
    ToolResponse
        On success: ``{sha, message, committed}``
        ``committed`` is False if there was nothing to commit.
    """
    try:
        sha = await git_client.commit(repo_path, message)
        return ToolResponse.ok(
            {"sha": sha, "message": message, "committed": sha is not None}
        )
    except Exception as exc:
        logger.warning("git_commit failed: %s", exc)
        return ToolResponse.fail(str(exc))


async def git_push(
    repo_path: str | Path,
    *,
    remote: str = "origin",
    branch: str = "main",
    access_token: str | None = None,
    force_with_lease: bool = False,
) -> ToolResponse:
    """Push commits to a remote.

    Returns
    -------
    ToolResponse
        On success: ``{remote, branch, pushed: True}``
    """
    try:
        await git_client.push(
            repo_path,
            remote=remote,
            branch=branch,
            access_token=access_token,
            force_with_lease=force_with_lease,
        )
        return ToolResponse.ok(
            {"remote": remote, "branch": branch, "pushed": True}
        )
    except Exception as exc:
        logger.warning("git_push failed: %s", exc)
        return ToolResponse.fail(str(exc))


async def git_pull(
    repo_path: str | Path,
    *,
    remote: str = "origin",
    branch: str = "main",
    access_token: str | None = None,
) -> ToolResponse:
    """Pull with rebase from a remote.

    Returns
    -------
    ToolResponse
        On success: ``{remote, branch, updated: True}``
    """
    try:
        await git_client.pull_rebase(
            repo_path,
            remote=remote,
            branch=branch,
            access_token=access_token,
        )
        return ToolResponse.ok(
            {"remote": remote, "branch": branch, "updated": True}
        )
    except Exception as exc:
        logger.warning("git_pull failed: %s", exc)
        return ToolResponse.fail(str(exc))
