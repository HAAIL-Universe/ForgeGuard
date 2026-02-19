"""Quick scan — GitHub-API-based shallow audit runs."""

import asyncio
import json
import logging
from uuid import UUID

from app.audit.engine import run_all_checks
from app.clients.github_client import (
    get_commit_files,
    get_repo_file_content,
    list_commits,
)
from app.repos.repo_repo import get_repo_by_id
from app.repos.scout_repo import (
    create_scout_run,
    get_scout_run,
    get_scout_runs_by_repo,
    get_scout_runs_by_user,
    update_scout_run,
)
from app.repos.user_repo import get_user_by_id
from app.ws_manager import manager as ws_manager

from ._utils import _build_check_list, _serialize_run

logger = logging.getLogger(__name__)


async def start_scout_run(
    user_id: UUID,
    repo_id: UUID,
    hypothesis: str | None = None,
) -> dict:
    """Start a scout run against a connected repo.

    Validates ownership, creates the run record, kicks off the audit
    in a background task, and returns immediately.
    """
    repo = await get_repo_by_id(repo_id)
    if repo is None or str(repo["user_id"]) != str(user_id):
        raise ValueError("Repo not found")

    run = await create_scout_run(repo_id, user_id, hypothesis)
    run_id = run["id"]

    # Fire and forget — the task streams progress via WS
    asyncio.create_task(_execute_scout(run_id, repo, user_id, hypothesis))

    return {
        "id": str(run_id),
        "status": "running",
        "repo_name": repo["full_name"],
    }


async def _execute_scout(
    run_id: UUID,
    repo: dict,
    user_id: UUID,
    hypothesis: str | None,
) -> None:
    """Execute a full scout run against a repo.

    Fetches the latest commit, retrieves changed files,
    runs all audit checks, streams results via WS, updates the DB.
    """
    user_id_str = str(user_id)
    full_name = repo["full_name"]

    try:
        # Get user's access token
        user = await get_user_by_id(user_id)
        if user is None:
            await update_scout_run(run_id, status="error")
            return

        access_token = user["access_token"]
        default_branch = repo.get("default_branch", "main")

        # Fetch recent commits to get changed files
        commits = await list_commits(
            access_token, full_name, branch=default_branch, per_page=1
        )
        if not commits:
            await _complete_with_no_changes(run_id, user_id_str)
            return

        head_sha = commits[0]["sha"]

        # Get files changed in the latest commit
        changed_paths = await get_commit_files(access_token, full_name, head_sha)
        if not changed_paths:
            await _complete_with_no_changes(run_id, user_id_str)
            return

        # Fetch file contents
        files: dict[str, str] = {}
        for path in changed_paths:
            content = await get_repo_file_content(
                access_token, full_name, path, head_sha
            )
            if content is not None:
                files[path] = content

        # Load boundaries.json if present (check common locations)
        boundaries = None
        for _bpath in ("boundaries.json", "Forge/Contracts/boundaries.json"):
            boundaries_content = await get_repo_file_content(
                access_token, full_name, _bpath, head_sha
            )
            if boundaries_content:
                try:
                    boundaries = json.loads(boundaries_content)
                except json.JSONDecodeError:
                    pass
                break

        # Run the engine checks (A4, A9, secrets)
        engine_results = run_all_checks(files, boundaries)

        # Build full check list with all standard check codes
        all_checks = _build_check_list(engine_results, changed_paths, files)

        # Stream each check result via WS
        for check in all_checks:
            await ws_manager.send_to_user(user_id_str, {
                "type": "scout_progress",
                "payload": {
                    "run_id": str(run_id),
                    "check_code": check["code"],
                    "check_name": check["name"],
                    "result": check["result"],
                    "detail": check.get("detail", ""),
                },
            })
            # Small delay so the frontend can render each check
            await asyncio.sleep(0.15)

        # Tally results
        checks_passed = sum(1 for c in all_checks if c["result"] == "PASS")
        checks_failed = sum(1 for c in all_checks if c["result"] == "FAIL")
        checks_warned = sum(1 for c in all_checks if c["result"] == "WARN")

        # Separate into blocking checks and warnings
        blocking = [c for c in all_checks if c["code"].startswith("A")]
        warnings = [c for c in all_checks if c["code"].startswith("W")]

        results_payload = {
            "checks": blocking,
            "warnings": warnings,
            "head_sha": head_sha,
            "files_analysed": len(files),
        }
        if hypothesis:
            results_payload["hypothesis"] = hypothesis

        updated = await update_scout_run(
            run_id,
            status="completed",
            results=results_payload,
            checks_passed=checks_passed,
            checks_failed=checks_failed,
            checks_warned=checks_warned,
        )

        # Send completion event
        await ws_manager.send_to_user(user_id_str, {
            "type": "scout_complete",
            "payload": {
                "id": str(run_id),
                "repo_id": str(repo["id"]),
                "repo_name": full_name,
                "status": "completed",
                "checks_passed": checks_passed,
                "checks_failed": checks_failed,
                "checks_warned": checks_warned,
                "started_at": updated.get("started_at", ""),
                "completed_at": updated.get("completed_at", ""),
            },
        })

    except Exception:
        logger.exception("Scout run %s failed", run_id)
        await update_scout_run(run_id, status="error")
        await ws_manager.send_to_user(user_id_str, {
            "type": "scout_complete",
            "payload": {
                "id": str(run_id),
                "repo_id": str(repo["id"]),
                "repo_name": full_name,
                "status": "error",
                "checks_passed": 0,
                "checks_failed": 0,
                "checks_warned": 0,
            },
        })


async def _complete_with_no_changes(run_id: UUID, user_id_str: str) -> None:
    """Complete a scout run when no changes are found."""
    await update_scout_run(
        run_id,
        status="completed",
        results={"checks": [], "warnings": [], "files_analysed": 0},
    )
    await ws_manager.send_to_user(user_id_str, {
        "type": "scout_complete",
        "payload": {
            "id": str(run_id),
            "status": "completed",
            "checks_passed": 0,
            "checks_failed": 0,
            "checks_warned": 0,
        },
    })


async def get_scout_history(
    user_id: UUID,
    repo_id: UUID | None = None,
) -> list[dict]:
    """Get scout run history for a user, optionally filtered by repo."""
    if repo_id:
        runs = await get_scout_runs_by_repo(repo_id, user_id)
    else:
        runs = await get_scout_runs_by_user(user_id)

    return [_serialize_run(r) for r in runs]


async def get_scout_detail(
    user_id: UUID,
    run_id: UUID,
) -> dict:
    """Get full detail for a scout run."""
    from .deep_scan import get_deep_scan_progress

    run = await get_scout_run(run_id)
    if run is None or str(run["user_id"]) != str(user_id):
        raise ValueError("Scout run not found")

    result = _serialize_run(run)

    # Parse the stored results JSON
    results_data = run.get("results")
    if results_data:
        if isinstance(results_data, str):
            results_data = json.loads(results_data)
        result["checks"] = results_data.get("checks", [])
        result["warnings"] = results_data.get("warnings", [])
        result["files_analysed"] = results_data.get("files_analysed", 0)
        result["hypothesis"] = results_data.get("hypothesis")

    # For running deep scans, include step progress so polling can update UI
    if result.get("scan_type") == "deep" and result.get("status") == "running":
        result["deep_steps"] = get_deep_scan_progress(run_id)

    return result
