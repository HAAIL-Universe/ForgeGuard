"""Audit service -- orchestrates audit execution triggered by webhooks."""

import json
from uuid import UUID

from app.audit.engine import run_all_checks
from app.clients.github_client import get_commit_files, get_repo_file_content
from app.repos.audit_repo import (
    create_audit_run,
    insert_audit_checks,
    update_audit_run,
)
from app.repos.repo_repo import get_repo_by_github_id
from app.repos.user_repo import get_user_by_id
from app.ws_manager import manager as ws_manager


async def process_push_event(payload: dict) -> dict | None:
    """Process a GitHub push webhook payload.

    Creates an audit run, fetches changed files, runs checks, stores results.
    Returns the audit run dict, or None if the repo is not connected.
    """
    # Extract repo info from payload
    repository = payload.get("repository", {})
    github_repo_id = repository.get("id")
    full_name = repository.get("full_name", "")

    if not github_repo_id:
        return None

    # Find connected repo
    repo = await get_repo_by_github_id(github_repo_id)
    if repo is None:
        return None

    # Extract commit info from the head commit
    head_commit = payload.get("head_commit") or {}
    commit_sha = head_commit.get("id", payload.get("after", ""))
    commit_message = head_commit.get("message", "")
    commit_author = ""
    author_data = head_commit.get("author", {})
    if author_data:
        commit_author = author_data.get("name", author_data.get("username", ""))

    ref = payload.get("ref", "")
    branch = ref.replace("refs/heads/", "") if ref.startswith("refs/heads/") else ref

    if not commit_sha:
        return None

    # Create audit run
    audit_run = await create_audit_run(
        repo_id=repo["id"],
        commit_sha=commit_sha,
        commit_message=commit_message,
        commit_author=commit_author,
        branch=branch,
    )

    # Mark as running
    await update_audit_run(
        audit_run_id=audit_run["id"],
        status="running",
        overall_result=None,
        files_checked=0,
    )

    try:
        # Get user access token for API calls
        user = await get_user_by_id(repo["user_id"])
        if user is None:
            await update_audit_run(
                audit_run_id=audit_run["id"],
                status="error",
                overall_result="ERROR",
                files_checked=0,
            )
            return audit_run

        access_token = user["access_token"]

        # Collect changed files from the push event
        changed_paths: list[str] = []
        for commit in payload.get("commits", []):
            changed_paths.extend(commit.get("added", []))
            changed_paths.extend(commit.get("modified", []))
        # Deduplicate
        changed_paths = list(set(changed_paths))

        if not changed_paths:
            # Fallback to API
            changed_paths = await get_commit_files(access_token, full_name, commit_sha)

        # Fetch file contents
        files: dict[str, str] = {}
        for path in changed_paths:
            content = await get_repo_file_content(
                access_token, full_name, path, commit_sha
            )
            if content is not None:
                files[path] = content

        # Try to load boundaries.json from the repo
        boundaries = None
        boundaries_content = await get_repo_file_content(
            access_token, full_name, "boundaries.json", commit_sha
        )
        if boundaries_content:
            try:
                boundaries = json.loads(boundaries_content)
            except json.JSONDecodeError:
                boundaries = None

        # Run audit checks
        check_results = run_all_checks(files, boundaries)

        # Store results
        await insert_audit_checks(audit_run["id"], check_results)

        # Determine overall result
        has_fail = any(c["result"] == "FAIL" for c in check_results)
        has_error = any(c["result"] == "ERROR" for c in check_results)
        if has_error:
            overall = "ERROR"
        elif has_fail:
            overall = "FAIL"
        else:
            overall = "PASS"

        await update_audit_run(
            audit_run_id=audit_run["id"],
            status="completed",
            overall_result=overall,
            files_checked=len(files),
        )

        # Broadcast real-time update via WebSocket
        user_id_str = str(repo["user_id"])
        await ws_manager.broadcast_audit_update(user_id_str, {
            "id": str(audit_run["id"]),
            "repo_id": str(repo["id"]),
            "commit_sha": commit_sha,
            "commit_message": commit_message,
            "commit_author": commit_author,
            "branch": branch,
            "status": "completed",
            "overall_result": overall,
            "started_at": audit_run["started_at"].isoformat() if audit_run.get("started_at") else None,
            "completed_at": None,
            "files_checked": len(files),
        })

    except Exception:
        await update_audit_run(
            audit_run_id=audit_run["id"],
            status="error",
            overall_result="ERROR",
            files_checked=0,
        )

    return audit_run


async def get_repo_audits(
    repo_id: UUID,
    user_id: UUID,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[dict], int]:
    """Get audit runs for a repo, verifying ownership."""
    from app.repos.audit_repo import get_audit_runs_by_repo
    from app.repos.repo_repo import get_repo_by_id

    repo = await get_repo_by_id(repo_id)
    if repo is None or repo["user_id"] != user_id:
        raise ValueError("Repo not found or access denied")

    items, total = await get_audit_runs_by_repo(repo_id, limit, offset)

    result = []
    for item in items:
        result.append({
            "id": str(item["id"]),
            "commit_sha": item["commit_sha"],
            "commit_message": item["commit_message"],
            "commit_author": item["commit_author"],
            "branch": item["branch"],
            "status": item["status"],
            "overall_result": item["overall_result"],
            "started_at": item["started_at"].isoformat() if item["started_at"] else None,
            "completed_at": item["completed_at"].isoformat() if item["completed_at"] else None,
            "files_checked": item["files_checked"],
            "check_summary": item.get("check_summary"),
        })
    return result, total


async def get_audit_detail(
    repo_id: UUID,
    audit_id: UUID,
    user_id: UUID,
) -> dict:
    """Get full audit detail including checks, verifying ownership."""
    from app.repos.audit_repo import get_audit_run_detail
    from app.repos.repo_repo import get_repo_by_id

    repo = await get_repo_by_id(repo_id)
    if repo is None or repo["user_id"] != user_id:
        raise ValueError("Repo not found or access denied")

    detail = await get_audit_run_detail(audit_id)
    if detail is None or detail["repo_id"] != repo_id:
        raise ValueError("Audit run not found")

    checks = []
    for c in detail.get("checks", []):
        checks.append({
            "id": str(c["id"]),
            "check_code": c["check_code"],
            "check_name": c["check_name"],
            "result": c["result"],
            "detail": c.get("detail"),
        })

    return {
        "id": str(detail["id"]),
        "commit_sha": detail["commit_sha"],
        "commit_message": detail["commit_message"],
        "commit_author": detail["commit_author"],
        "branch": detail["branch"],
        "status": detail["status"],
        "overall_result": detail["overall_result"],
        "started_at": detail["started_at"].isoformat() if detail["started_at"] else None,
        "completed_at": detail["completed_at"].isoformat() if detail["completed_at"] else None,
        "files_checked": detail["files_checked"],
        "checks": checks,
    }
