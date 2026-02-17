"""Git client -- thin wrapper around subprocess for git operations.

Handles clone, add, commit, push for build targets. No database access,
no business logic, no HTTP framework imports.
"""

import asyncio
import logging
import os
import subprocess
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
    if access_token and "github.com" in clone_url:
        # Inject token for HTTPS auth: https://TOKEN@github.com/owner/repo.git
        url = clone_url.replace("https://", f"https://{access_token}@")

    args = ["clone"]
    if shallow:
        args.extend(["--depth", "1"])
    if branch:
        args.extend(["--branch", branch])
    args.extend([url, str(dest)])

    parent = Path(dest).parent
    parent.mkdir(parents=True, exist_ok=True)
    await _run_git(args, cwd=str(parent))
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


async def add_all(repo_path: str | Path) -> None:
    """Stage all changes in the repo."""
    await _run_git(["add", "-A"], cwd=repo_path)


async def commit(repo_path: str | Path, message: str) -> str | None:
    """Commit staged changes. Returns commit hash or None if nothing to commit."""
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
    env = {}
    if access_token:
        # Use credential helper to inject token
        env["GIT_ASKPASS"] = "echo"
        # Set the remote URL with token for auth
        try:
            remote_url = await _run_git(["remote", "get-url", remote], cwd=repo_path)
            if "github.com" in remote_url and "@" not in remote_url:
                authed_url = remote_url.replace(
                    "https://", f"https://{access_token}@"
                )
                await _run_git(
                    ["remote", "set-url", remote, authed_url], cwd=repo_path
                )
        except RuntimeError:
            pass

    cmd = ["push", "-u", remote, branch]
    if force_with_lease:
        cmd.insert(1, "--force-with-lease")
    await _run_git(cmd, cwd=repo_path, env=env if env else None)


async def pull_rebase(
    repo_path: str | Path,
    *,
    remote: str = "origin",
    branch: str = "main",
    access_token: str | None = None,
) -> None:
    """Pull with rebase to integrate remote changes before pushing."""
    env = {}
    if access_token:
        env["GIT_ASKPASS"] = "echo"
        try:
            remote_url = await _run_git(["remote", "get-url", remote], cwd=repo_path)
            if "github.com" in remote_url and "@" not in remote_url:
                authed_url = remote_url.replace(
                    "https://", f"https://{access_token}@"
                )
                await _run_git(
                    ["remote", "set-url", remote, authed_url], cwd=repo_path
                )
        except RuntimeError:
            pass

    await _run_git(
        ["pull", "--rebase", "--allow-unrelated-histories", remote, branch],
        cwd=repo_path,
        env=env if env else None,
    )


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
