"""Git client -- thin wrapper around subprocess for git operations.

Handles clone, add, commit, push for build targets. No database access,
no business logic, no HTTP framework imports.

Credential strategy
-------------------
GitHub access tokens are **never** persisted in ``.git/config``.
Instead they are injected via the ``GIT_ASKPASS`` env-var at push / pull
time and stripped from remote URLs immediately after clone.
"""

import asyncio
import logging
import os
import subprocess
import sys
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)


async def _run_git(args: list[str], cwd: str | Path, env: dict | None = None) -> str:
    """Run a git command and return stdout. Raises on non-zero exit.

    Uses subprocess.run in a thread to avoid asyncio event-loop limitations
    on Windows (ProactorEventLoop requirement for create_subprocess_exec).
    """
    merged_env = {**os.environ, **(env or {})}

    def _sync() -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", *args],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            env=merged_env,
        )

    result = await asyncio.to_thread(_sync)
    out = (result.stdout or "").strip()
    err = (result.stderr or "").strip()

    if result.returncode != 0:
        logger.error("git %s failed (rc=%d): %s", " ".join(args), result.returncode, err)
        raise RuntimeError(f"git {args[0]} failed: {err}")

    return out


def _make_askpass_env(access_token: str) -> dict[str, str]:
    """Build env-vars that inject *access_token* via ``GIT_ASKPASS``.

    On Windows we write a tiny ``.cmd`` script; on Unix a shell script.
    The script simply echoes the token when git prompts for a password.
    The file is placed in the system temp dir and is reused across calls.

    This avoids persisting the token in ``.git/config`` remote URLs.
    """
    if sys.platform == "win32":
        script_path = Path(tempfile.gettempdir()) / "_forge_askpass.cmd"
        if not script_path.exists() or access_token not in script_path.read_text():
            script_path.write_text(f"@echo {access_token}\n")
    else:
        script_path = Path(tempfile.gettempdir()) / "_forge_askpass.sh"
        if not script_path.exists() or access_token not in script_path.read_text():
            script_path.write_text(f"#!/bin/sh\necho '{access_token}'\n")
            script_path.chmod(0o700)

    return {
        "GIT_ASKPASS": str(script_path),
        "GIT_TERMINAL_PROMPT": "0",
    }


async def _strip_token_from_remote(
    repo_path: str | Path, remote: str = "origin"
) -> None:
    """Remove any embedded token from the remote URL in .git/config."""
    try:
        url = await _run_git(["remote", "get-url", remote], cwd=repo_path)
        # Pattern: https://TOKEN@github.com/...
        if "@github.com" in url:
            clean = "https://github.com" + url.split("@github.com", 1)[1]
            await _run_git(
                ["remote", "set-url", remote, clean], cwd=repo_path
            )
    except RuntimeError:
        pass


async def clone_repo(
    clone_url: str,
    dest: str | Path,
    *,
    branch: str | None = None,
    shallow: bool = True,
    access_token: str | None = None,
) -> str:
    """Clone a git repository to dest. Returns the dest path.

    For GitHub repos, inject the access token into the URL for auth.
    """
    url = clone_url
    env: dict[str, str] = {}
    if access_token and "github.com" in clone_url:
        # Use ephemeral env-based auth so the token is never in .git/config
        url = clone_url if clone_url.startswith("https://") else clone_url
        env = _make_askpass_env(access_token)

    args = ["clone"]
    if shallow:
        args.extend(["--depth", "1"])
    if branch:
        args.extend(["--branch", branch])
    args.extend([url, str(dest)])

    parent = Path(dest).parent
    parent.mkdir(parents=True, exist_ok=True)
    await _run_git(args, cwd=str(parent), env=env or None)

    # Belt-and-suspenders: ensure no token leaked into .git/config
    await _strip_token_from_remote(dest)
    return str(dest)


async def init_repo(dest: str | Path) -> str:
    """Initialize a new git repo at dest. Returns the dest path."""
    path = Path(dest)
    path.mkdir(parents=True, exist_ok=True)
    await _run_git(["init"], cwd=path)
    # Set default branch to main
    await _run_git(["checkout", "-b", "main"], cwd=path)
    return str(path)


async def create_branch(repo_path: str | Path, branch_name: str) -> None:
    """Create and checkout a new branch."""
    await _run_git(["checkout", "-b", branch_name], cwd=repo_path)


async def checkout_branch(repo_path: str | Path, branch_name: str) -> None:
    """Checkout an existing branch."""
    await _run_git(["checkout", branch_name], cwd=repo_path)


async def exclude_contracts_from_staging(repo_path: str | Path) -> None:
    """Remove Forge contract files from the git index (belt-and-suspenders).

    Even though ``.gitignore`` should prevent staging, this explicitly
    un-stages ``Forge/`` so contract files are **never** committed.
    Silently succeeds if nothing is staged or the directory doesn't exist.
    """
    forge_dir = Path(repo_path) / "Forge"
    if not forge_dir.exists():
        return
    try:
        await _run_git(
            ["rm", "-r", "--cached", "--ignore-unmatch", "Forge/"],
            cwd=repo_path,
        )
    except RuntimeError:
        # Non-fatal — .gitignore is the primary guard
        logger.debug("git rm --cached Forge/ failed (non-fatal) in %s", repo_path)


async def add_all(repo_path: str | Path, *, include_contracts: bool = False) -> None:
    """Stage all changes in the repo.

    By default, Forge contract files are excluded from staging
    (belt-and-suspenders guard for builds).  Pass ``include_contracts=True``
    when the intent is to commit contracts themselves (e.g. push-to-repo).
    """
    await _run_git(["add", "-A"], cwd=repo_path)
    if include_contracts:
        # Force-add contracts past .gitignore so they are always staged
        forge_dir = Path(repo_path) / "Forge"
        if forge_dir.exists():
            await _run_git(["add", "-f", "Forge/"], cwd=repo_path)
    else:
        await exclude_contracts_from_staging(repo_path)


async def commit(
    repo_path: str | Path,
    message: str,
    *,
    include_contracts: bool = False,
) -> str | None:
    """Commit staged changes. Returns commit hash or None if nothing to commit.

    Pass ``include_contracts=True`` when the commit should include Forge
    contract files (e.g. the push-contracts-to-repo flow).
    """
    # Check if there are staged changes
    try:
        status = await _run_git(["status", "--porcelain"], cwd=repo_path)
        if not status.strip():
            logger.info("Nothing to commit in %s", repo_path)
            return None
    except RuntimeError:
        return None

    # Configure committer identity for the repo
    try:
        await _run_git(["config", "user.email", "forge@forgeguard.dev"], cwd=repo_path)
        await _run_git(["config", "user.name", "ForgeGuard Builder"], cwd=repo_path)
    except RuntimeError:
        pass  # May already be configured

    await _run_git(["add", "-A"], cwd=repo_path)
    if include_contracts:
        forge_dir = Path(repo_path) / "Forge"
        if forge_dir.exists():
            await _run_git(["add", "-f", "Forge/"], cwd=repo_path)
    else:
        await exclude_contracts_from_staging(repo_path)
    await _run_git(["commit", "-m", message], cwd=repo_path)
    sha = await _run_git(["rev-parse", "HEAD"], cwd=repo_path)
    return sha


async def push(
    repo_path: str | Path,
    *,
    remote: str = "origin",
    branch: str = "main",
    access_token: str | None = None,
    force_with_lease: bool = False,
) -> None:
    """Push all commits to the remote."""
    env: dict[str, str] = {}
    if access_token:
        env = _make_askpass_env(access_token)

    cmd = ["push", "-u", remote, branch]
    if force_with_lease:
        cmd.insert(1, "--force-with-lease")
    await _run_git(cmd, cwd=repo_path, env=env or None)

    # Ensure no token leaked into .git/config after push
    await _strip_token_from_remote(repo_path, remote)


async def fetch(
    repo_path: str | Path,
    *,
    remote: str = "origin",
    access_token: str | None = None,
) -> None:
    """Fetch from remote to update tracking refs.

    Non-fatal if the remote has no branches yet (new empty repo).
    Must be called before ``--force-with-lease`` so git has a tracking ref
    to compare against.
    """
    env: dict[str, str] = {}
    if access_token:
        env = _make_askpass_env(access_token)

    await _run_git(["fetch", remote], cwd=repo_path, env=env or None)
    await _strip_token_from_remote(repo_path, remote)


async def pull_rebase(
    repo_path: str | Path,
    *,
    remote: str = "origin",
    branch: str = "main",
    access_token: str | None = None,
) -> None:
    """Pull with rebase to integrate remote changes before pushing.

    Note: ``--allow-unrelated-histories`` is a merge flag and is NOT valid for
    ``git rebase`` — it was removed to prevent silent failures across git versions.
    """
    env: dict[str, str] = {}
    if access_token:
        env = _make_askpass_env(access_token)

    await _run_git(
        ["pull", "--rebase", remote, branch],
        cwd=repo_path,
        env=env or None,
    )

    # Ensure no token leaked into .git/config after pull
    await _strip_token_from_remote(repo_path, remote)


async def set_remote(
    repo_path: str | Path,
    remote_url: str,
    *,
    remote_name: str = "origin",
) -> None:
    """Set or update the remote URL for a repo."""
    try:
        await _run_git(["remote", "add", remote_name, remote_url], cwd=repo_path)
    except RuntimeError:
        # Remote already exists, update it
        await _run_git(["remote", "set-url", remote_name, remote_url], cwd=repo_path)


async def get_file_list(repo_path: str | Path) -> list[str]:
    """List all tracked + untracked files in the repo (relative paths)."""
    try:
        # Get tracked files
        tracked = await _run_git(["ls-files"], cwd=repo_path)
        # Get untracked files
        untracked = await _run_git(
            ["ls-files", "--others", "--exclude-standard"], cwd=repo_path
        )
        files = set()
        for line in tracked.splitlines():
            if line.strip():
                files.add(line.strip())
        for line in untracked.splitlines():
            if line.strip():
                files.add(line.strip())
        return sorted(files)
    except RuntimeError:
        return []


async def diff_summary(
    repo_path: str | Path,
    *,
    from_ref: str = "HEAD~1",
    to_ref: str = "HEAD",
    max_bytes: int = 4000,
) -> str:
    """Return a compact diff --stat + abbreviated diff between two refs.

    Falls back gracefully if the repo has only one commit or the refs
    don't exist.  Output is capped at *max_bytes* to keep prompts lean.
    """
    try:
        stat = await _run_git(
            ["diff", "--stat", from_ref, to_ref], cwd=repo_path,
        )
    except RuntimeError:
        return "(no diff available — single commit or refs missing)"

    try:
        diff = await _run_git(
            ["diff", "--no-color", "-U2", from_ref, to_ref], cwd=repo_path,
        )
    except RuntimeError:
        diff = ""

    combined = f"--- diff --stat {from_ref}..{to_ref} ---\n{stat}\n"
    if diff:
        remaining = max_bytes - len(combined)
        if remaining > 0:
            combined += f"\n--- diff (abbreviated) ---\n{diff[:remaining]}"
            if len(diff) > remaining:
                combined += "\n... (truncated)"
    return combined


async def rev_parse_head(repo_path: str | Path) -> str:
    """Return the current HEAD commit SHA."""
    return await _run_git(["rev-parse", "HEAD"], cwd=repo_path)


async def force_push_ref(
    repo_path: str | Path,
    ref_sha: str,
    branch: str,
    access_token: str,
    *,
    remote: str = "origin",
) -> None:
    """Force-push *branch* to a specific commit SHA.

    Used by the nuke feature to revert a branch back to its base commit
    when the branch is the default (main/master) and can't be deleted.
    """
    env = _make_askpass_env(access_token)
    await _run_git(
        ["push", "--force", remote, f"{ref_sha}:{branch}"],
        cwd=repo_path,
        env=env,
    )
    await _strip_token_from_remote(repo_path, remote)


async def log_oneline(
    repo_path: str | Path,
    *,
    max_count: int = 50,
) -> list[str]:
    """Return recent commit messages (one per line, newest first).

    Each entry is the full ``--format=%s`` subject line.
    """
    try:
        raw = await _run_git(
            ["log", f"--max-count={max_count}", "--format=%s"],
            cwd=repo_path,
        )
        return [line.strip() for line in raw.splitlines() if line.strip()]
    except RuntimeError:
        return []
