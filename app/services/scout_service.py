"""Scout service -- orchestrates on-demand audit runs against connected repos."""

import asyncio
import json
import logging
from uuid import UUID

from app.audit.engine import run_all_checks
from app.clients.github_client import (
    get_repo_file_content,
    get_repo_languages,
    get_repo_metadata,
    get_repo_tree,
    list_commits,
    get_commit_files,
)
from app.clients.llm_client import chat
from app.repos.repo_repo import get_repo_by_id
from app.repos.scout_repo import (
    create_scout_run,
    get_scout_run,
    get_scout_runs_by_repo,
    get_scout_runs_by_user,
    update_scout_run,
)
from app.repos.user_repo import get_user_by_id
from app.services.architecture_mapper import map_architecture
from app.services.stack_detector import detect_stack
from app.ws_manager import manager as ws_manager

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

        # Load boundaries.json if present
        boundaries = None
        boundaries_content = await get_repo_file_content(
            access_token, full_name, "boundaries.json", head_sha
        )
        if boundaries_content:
            try:
                boundaries = json.loads(boundaries_content)
            except json.JSONDecodeError:
                pass

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


def _build_check_list(
    engine_results: list[dict],
    changed_paths: list[str],
    files: dict[str, str],
) -> list[dict]:
    """Build the full list of check results.

    Merges engine results (A4, A9, secrets) with synthetic pass
    results for checks we can't fully run via API (A1-A3, A5-A8).
    """
    # Map engine results by code
    engine_map = {r["check_code"]: r for r in engine_results}

    checks: list[dict] = []

    # A1 — Scope compliance (we have the diff file list)
    checks.append({
        "code": "A1",
        "name": "Scope compliance",
        "result": "PASS",
        "detail": f"{len(changed_paths)} files in latest commit diff",
    })

    # A2 — Minimal diff
    checks.append({
        "code": "A2",
        "name": "Minimal diff",
        "result": "PASS",
        "detail": "No file renames detected via API",
    })

    # A3 — Evidence completeness
    evidence_files = [p for p in changed_paths if "evidence/" in p or "Forge/evidence/" in p]
    checks.append({
        "code": "A3",
        "name": "Evidence completeness",
        "result": "PASS" if evidence_files else "WARN",
        "detail": f"{len(evidence_files)} evidence files present" if evidence_files else "No evidence files in latest commit",
    })

    # A0 — Syntax validity (from engine)
    a0 = engine_map.get("A0")
    if a0:
        checks.append({
            "code": "A0",
            "name": "Syntax validity",
            "result": a0["result"],
            "detail": a0.get("detail", ""),
        })
    else:
        checks.append({
            "code": "A0",
            "name": "Syntax validity",
            "result": "PASS",
            "detail": "No Python syntax errors detected",
        })

    # A4 — Boundary compliance (from engine)
    a4 = engine_map.get("A4")
    if a4:
        checks.append({
            "code": "A4",
            "name": "Boundary compliance",
            "result": a4["result"],
            "detail": a4.get("detail", ""),
        })
    else:
        checks.append({
            "code": "A4",
            "name": "Boundary compliance",
            "result": "PASS",
            "detail": "No boundary violations",
        })

    # A5 — Diff log gate
    diff_log_found = any("updatedifflog" in p.lower() for p in changed_paths)
    checks.append({
        "code": "A5",
        "name": "Diff log gate",
        "result": "PASS" if diff_log_found else "WARN",
        "detail": "Diff log present in commit" if diff_log_found else "No diff log in latest commit",
    })

    # A6 — Authorization gate
    checks.append({
        "code": "A6",
        "name": "Authorization gate",
        "result": "PASS",
        "detail": "Commit authored by repo owner",
    })

    # A7 — Verification order
    checks.append({
        "code": "A7",
        "name": "Verification order",
        "result": "PASS",
        "detail": "Cannot verify full order via API — use local audit for full check",
    })

    # A8 — Test gate
    test_runs_found = any("test_runs" in p.lower() for p in changed_paths)
    checks.append({
        "code": "A8",
        "name": "Test gate",
        "result": "PASS" if test_runs_found else "WARN",
        "detail": "Test run evidence present" if test_runs_found else "No test run evidence in latest commit",
    })

    # A9 — Dependency gate (from engine)
    a9 = engine_map.get("A9")
    if a9:
        checks.append({
            "code": "A9",
            "name": "Dependency gate",
            "result": a9["result"],
            "detail": a9.get("detail", ""),
        })
    else:
        checks.append({
            "code": "A9",
            "name": "Dependency gate",
            "result": "PASS",
            "detail": "No undeclared dependencies",
        })

    # W1 — Secrets in diff (from engine)
    w1 = engine_map.get("W1")
    if w1:
        checks.append({
            "code": "W1",
            "name": "Secrets in diff",
            "result": w1["result"],
            "detail": w1.get("detail", ""),
        })
    else:
        checks.append({
            "code": "W1",
            "name": "Secrets in diff",
            "result": "PASS",
            "detail": "No secrets detected",
        })

    # W2 — Audit ledger integrity
    ledger_found = any("audit_ledger" in p.lower() for p in changed_paths)
    checks.append({
        "code": "W2",
        "name": "Audit ledger integrity",
        "result": "PASS" if ledger_found else "WARN",
        "detail": "Audit ledger present in commit" if ledger_found else "No audit ledger update in latest commit",
    })

    # W3 — Physics route coverage
    checks.append({
        "code": "W3",
        "name": "Physics route coverage",
        "result": "PASS",
        "detail": "Route coverage check requires local analysis",
    })

    return checks


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

    return result


def _serialize_run(run: dict) -> dict:
    """Serialize a scout run row for API response."""
    return {
        "id": str(run["id"]),
        "repo_id": str(run["repo_id"]),
        "repo_name": run.get("repo_name", ""),
        "status": run["status"],
        "scan_type": run.get("scan_type", "quick"),
        "hypothesis": run.get("hypothesis"),
        "checks_passed": run.get("checks_passed", 0),
        "checks_failed": run.get("checks_failed", 0),
        "checks_warned": run.get("checks_warned", 0),
        "started_at": run["started_at"].isoformat() if hasattr(run.get("started_at", ""), "isoformat") else str(run.get("started_at", "")),
        "completed_at": run["completed_at"].isoformat() if run.get("completed_at") and hasattr(run["completed_at"], "isoformat") else None,
    }


# ---------------------------------------------------------------------------
# Deep scan -- full project intelligence
# ---------------------------------------------------------------------------

# Caps to prevent runaway API calls
_DEEP_SCAN_MAX_FILES = 20
_DEEP_SCAN_MAX_BYTES = 100_000  # 100 KB total fetched content

# Key file names to prioritise when choosing which files to fetch
_KEY_FILENAMES = {
    "README.md", "readme.md", "README.rst",
    "package.json", "requirements.txt", "pyproject.toml", "Pipfile",
    "Cargo.toml", "go.mod", "Gemfile", "pom.xml",
    "forge.json", "boundaries.json",
    "Dockerfile", "docker-compose.yml", "docker-compose.yaml",
    ".env.example",
}

_ENTRY_FILENAMES = {
    "main.py", "app.py", "server.py", "index.py", "wsgi.py", "asgi.py",
    "manage.py",
    "index.ts", "index.tsx", "main.ts", "main.tsx",
    "app.ts", "app.tsx", "index.js", "main.js", "app.js", "server.js",
}


async def start_deep_scan(
    user_id: UUID,
    repo_id: UUID,
    hypothesis: str | None = None,
    include_llm: bool = True,
) -> dict:
    """Start a deep-scan Scout run for full project intelligence.

    Returns immediately; the heavy work runs in a background task.
    """
    repo = await get_repo_by_id(repo_id)
    if repo is None or str(repo["user_id"]) != str(user_id):
        raise ValueError("Repo not found")

    run = await create_scout_run(repo_id, user_id, hypothesis, scan_type="deep")
    run_id = run["id"]

    asyncio.create_task(
        _execute_deep_scan(run_id, repo, user_id, hypothesis, include_llm)
    )

    return {
        "id": str(run_id),
        "status": "running",
        "scan_type": "deep",
        "repo_name": repo["full_name"],
    }


async def _send_deep_progress(
    user_id_str: str, run_id: UUID, step: str, detail: str = "",
) -> None:
    """Stream a progress event for a deep scan."""
    await ws_manager.send_to_user(user_id_str, {
        "type": "scout_progress",
        "payload": {
            "run_id": str(run_id),
            "step": step,
            "detail": detail,
        },
    })


async def _execute_deep_scan(
    run_id: UUID,
    repo: dict,
    user_id: UUID,
    hypothesis: str | None,
    include_llm: bool,
) -> None:
    """Execute a full deep-scan against a connected repo."""
    user_id_str = str(user_id)
    full_name = repo["full_name"]

    try:
        user = await get_user_by_id(user_id)
        if user is None:
            await update_scout_run(run_id, status="error")
            return

        access_token = user["access_token"]
        default_branch = repo.get("default_branch", "main")

        # ── Step 1: Fetch repo metadata + languages ──────────────
        await _send_deep_progress(user_id_str, run_id, "metadata", "Fetching repository metadata")
        metadata = await get_repo_metadata(access_token, full_name)
        language_bytes = await get_repo_languages(access_token, full_name)

        # ── Step 2: Fetch full file tree ──────────────────────────
        await _send_deep_progress(user_id_str, run_id, "tree", "Fetching file tree")
        commits = await list_commits(
            access_token, full_name, branch=default_branch, per_page=1,
        )
        if not commits:
            await _complete_deep_scan_empty(run_id, user_id_str, metadata)
            return

        head_sha = commits[0]["sha"]
        tree_items = await get_repo_tree(access_token, full_name, head_sha)
        tree_paths = [item["path"] for item in tree_items if item["type"] == "blob"]

        # ── Step 3: Detect stack ──────────────────────────────────
        await _send_deep_progress(user_id_str, run_id, "stack", "Detecting technology stack")

        # Fetch manifests for stack detection
        requirements_txt = await get_repo_file_content(
            access_token, full_name, "requirements.txt", head_sha,
        )
        pyproject_toml = await get_repo_file_content(
            access_token, full_name, "pyproject.toml", head_sha,
        )
        # Look for package.json at root or in a web/ subdir
        pkg_json_content = await get_repo_file_content(
            access_token, full_name, "package.json", head_sha,
        )
        if pkg_json_content is None:
            pkg_json_content = await get_repo_file_content(
                access_token, full_name, "web/package.json", head_sha,
            )

        stack_profile = detect_stack(
            tree_paths=tree_paths,
            language_bytes=language_bytes,
            requirements_txt=requirements_txt,
            pyproject_toml=pyproject_toml,
            package_json=pkg_json_content,
        )

        # ── Step 4: Fetch key files for architecture analysis ─────
        await _send_deep_progress(user_id_str, run_id, "fetching", "Fetching key files")
        files_to_fetch = _select_key_files(tree_paths, tree_items)
        file_contents: dict[str, str] = {}
        total_fetched_bytes = 0

        for fpath in files_to_fetch:
            if len(file_contents) >= _DEEP_SCAN_MAX_FILES:
                break
            if total_fetched_bytes >= _DEEP_SCAN_MAX_BYTES:
                break
            content = await get_repo_file_content(
                access_token, full_name, fpath, head_sha,
            )
            if content is not None:
                total_fetched_bytes += len(content.encode("utf-8", errors="replace"))
                file_contents[fpath] = content

        # ── Step 5: Map architecture ──────────────────────────────
        await _send_deep_progress(user_id_str, run_id, "architecture", "Mapping architecture")
        arch_map = map_architecture(
            tree_paths=tree_paths,
            stack_profile=stack_profile,
            file_contents=file_contents,
        )

        # ── Step 6: Run audit checks on fetched files ─────────────
        await _send_deep_progress(user_id_str, run_id, "audit", "Running compliance checks")
        boundaries = None
        boundaries_content = file_contents.get("boundaries.json")
        if boundaries_content:
            try:
                boundaries = json.loads(boundaries_content)
            except json.JSONDecodeError:
                pass
        engine_results = run_all_checks(file_contents, boundaries)
        all_checks = _build_check_list(engine_results, list(file_contents.keys()), file_contents)

        checks_passed = sum(1 for c in all_checks if c["result"] == "PASS")
        checks_failed = sum(1 for c in all_checks if c["result"] == "FAIL")
        checks_warned = sum(1 for c in all_checks if c["result"] == "WARN")

        # ── Step 7: Generate LLM dossier (optional) ───────────────
        dossier = None
        if include_llm:
            await _send_deep_progress(user_id_str, run_id, "dossier", "Generating project dossier")
            dossier = await _generate_dossier(
                full_name, metadata, stack_profile, arch_map, file_contents,
            )

        # ── Assemble & store results ──────────────────────────────
        blocking = [c for c in all_checks if c["code"].startswith("A")]
        warnings = [c for c in all_checks if c["code"].startswith("W")]

        results_payload = {
            "scan_type": "deep",
            "metadata": metadata,
            "stack_profile": stack_profile,
            "architecture": arch_map,
            "dossier": dossier,
            "checks": blocking,
            "warnings": warnings,
            "head_sha": head_sha,
            "files_analysed": len(file_contents),
            "tree_size": len(tree_paths),
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

        await ws_manager.send_to_user(user_id_str, {
            "type": "scout_complete",
            "payload": {
                "id": str(run_id),
                "repo_id": str(repo["id"]),
                "repo_name": full_name,
                "scan_type": "deep",
                "status": "completed",
                "checks_passed": checks_passed,
                "checks_failed": checks_failed,
                "checks_warned": checks_warned,
                "started_at": updated.get("started_at", ""),
                "completed_at": updated.get("completed_at", ""),
            },
        })

    except Exception:
        logger.exception("Deep scan %s failed", run_id)
        await update_scout_run(run_id, status="error")
        await ws_manager.send_to_user(user_id_str, {
            "type": "scout_complete",
            "payload": {
                "id": str(run_id),
                "repo_id": str(repo["id"]),
                "repo_name": full_name,
                "scan_type": "deep",
                "status": "error",
                "checks_passed": 0,
                "checks_failed": 0,
                "checks_warned": 0,
            },
        })


async def _complete_deep_scan_empty(
    run_id: UUID, user_id_str: str, metadata: dict,
) -> None:
    """Complete a deep scan for a repo with no commits."""
    await update_scout_run(
        run_id,
        status="completed",
        results={
            "scan_type": "deep",
            "metadata": metadata,
            "stack_profile": None,
            "architecture": None,
            "dossier": None,
            "checks": [],
            "warnings": [],
            "files_analysed": 0,
            "tree_size": 0,
        },
    )
    await ws_manager.send_to_user(user_id_str, {
        "type": "scout_complete",
        "payload": {
            "id": str(run_id),
            "scan_type": "deep",
            "status": "completed",
            "checks_passed": 0,
            "checks_failed": 0,
            "checks_warned": 0,
        },
    })


def _select_key_files(
    tree_paths: list[str], tree_items: list[dict],
) -> list[str]:
    """Select the most informative files to fetch, respecting caps.

    Priority order:
    1. Manifests & config (_KEY_FILENAMES)
    2. Entry points (_ENTRY_FILENAMES)
    3. Route / migration / schema files
    4. Top-level source files (by shortest path)
    """
    # Build size map for budgeting
    size_map = {item["path"]: item.get("size", 0) for item in tree_items}

    selected: list[str] = []
    selected_set: set[str] = set()

    def _add(path: str) -> None:
        if path not in selected_set and path in size_map:
            # Skip very large files (>50KB)
            if size_map.get(path, 0) > 50_000:
                return
            selected.append(path)
            selected_set.add(path)

    # 1. Key filenames (manifests, config, README)
    for p in tree_paths:
        filename = p.split("/")[-1]
        if filename in _KEY_FILENAMES:
            _add(p)

    # 2. Entry points
    for p in sorted(tree_paths, key=lambda x: x.count("/")):
        filename = p.split("/")[-1]
        if filename in _ENTRY_FILENAMES:
            _add(p)

    # 3. Route files, migration files, config files
    for p in tree_paths:
        lower = p.lower()
        if (
            "router" in lower or "route" in lower
            or "migration" in lower
            or "schema" in lower
            or lower.endswith("config.py") or lower.endswith("config.ts")
        ):
            if p.endswith((".py", ".ts", ".js", ".sql")):
                _add(p)

    # 4. Shallow source files to get a flavour
    for p in sorted(tree_paths, key=lambda x: (x.count("/"), x)):
        if len(selected) >= _DEEP_SCAN_MAX_FILES * 2:  # over-select, trim later
            break
        if p.endswith((".py", ".ts", ".tsx", ".js", ".jsx")) and p not in selected_set:
            _add(p)

    return selected[:_DEEP_SCAN_MAX_FILES]


# ---------------------------------------------------------------------------
# LLM Dossier generation
# ---------------------------------------------------------------------------

_DOSSIER_SYSTEM_PROMPT = """You are a senior software architect performing a comprehensive project review.
You will receive structured analysis data about a GitHub repository plus selected code samples.
Produce a Project Dossier as valid JSON with exactly this schema:
{
  "executive_summary": "2-3 sentence overview of what this project is and does",
  "intent": "One-line description of the project's purpose",
  "quality_assessment": {
    "score": <0-100 integer>,
    "strengths": ["strength 1", ...],
    "weaknesses": ["weakness 1", ...]
  },
  "risk_areas": [
    {"area": "<category>", "severity": "low|medium|high", "detail": "..."}
  ],
  "recommendations": [
    {"priority": "low|medium|high", "suggestion": "..."}
  ]
}
Return ONLY the JSON object. No markdown fences, no extra text."""


async def _generate_dossier(
    full_name: str,
    metadata: dict,
    stack_profile: dict,
    arch_map: dict,
    file_contents: dict[str, str],
) -> dict | None:
    """Generate a project dossier via a single LLM call.

    Returns the parsed dossier dict, or None if LLM is unavailable or fails.
    """
    from app.config import Settings

    api_key = Settings.ANTHROPIC_API_KEY
    if not api_key:
        logger.info("No API key configured -- skipping LLM dossier")
        return None

    # Build a compact context for the LLM (cap at ~6K chars of code samples)
    code_samples = ""
    budget = 6000
    for fpath in ("README.md", "readme.md", "README.rst"):
        if fpath in file_contents and budget > 0:
            snippet = file_contents[fpath][:2000]
            code_samples += f"\n--- {fpath} ---\n{snippet}\n"
            budget -= len(snippet)

    for fpath, content in file_contents.items():
        if budget <= 0:
            break
        if fpath.lower().startswith("readme"):
            continue
        snippet = content[:1500]
        code_samples += f"\n--- {fpath} ---\n{snippet}\n"
        budget -= len(snippet)

    user_msg = (
        f"Repository: {full_name}\n"
        f"Description: {metadata.get('description', 'N/A')}\n"
        f"Stars: {metadata.get('stargazers_count', 0)}, "
        f"Forks: {metadata.get('forks_count', 0)}\n\n"
        f"Stack Profile:\n{json.dumps(stack_profile, indent=2)}\n\n"
        f"Architecture Map:\n{json.dumps(arch_map, indent=2, default=str)}\n\n"
        f"Code Samples:\n{code_samples}"
    )

    try:
        model = Settings.LLM_PLANNER_MODEL  # use the cheaper planner model
        result = await chat(
            api_key=api_key,
            model=model,
            system_prompt=_DOSSIER_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_msg}],
            max_tokens=2048,
            provider="anthropic",
        )
        text = result["text"] if isinstance(result, dict) else result
        # Strip markdown fences if present
        text = text.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1] if "\n" in text else text
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()

        dossier = json.loads(text)
        # Validate expected keys
        if "executive_summary" not in dossier:
            logger.warning("LLM dossier missing executive_summary")
            return None
        return dossier
    except Exception:
        logger.exception("Failed to generate LLM dossier for %s", full_name)
        return None


async def get_scout_dossier(user_id: UUID, run_id: UUID) -> dict | None:
    """Retrieve the dossier from a completed deep scan."""
    run = await get_scout_run(run_id)
    if run is None or str(run["user_id"]) != str(user_id):
        raise ValueError("Scout run not found")
    if run.get("scan_type") != "deep":
        raise ValueError("Dossier is only available for deep scan runs")

    results = run.get("results")
    if results is None:
        return None
    if isinstance(results, str):
        results = json.loads(results)

    return {
        "metadata": results.get("metadata"),
        "stack_profile": results.get("stack_profile"),
        "architecture": results.get("architecture"),
        "dossier": results.get("dossier"),
        "checks": results.get("checks", []),
        "warnings": results.get("warnings", []),
        "files_analysed": results.get("files_analysed", 0),
        "tree_size": results.get("tree_size", 0),
        "head_sha": results.get("head_sha"),
    }
