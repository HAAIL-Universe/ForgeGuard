"""Deep scan — full project intelligence with architecture mapping."""

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
)
from app.repos.repo_repo import get_repo_by_id
from app.repos.scout_repo import create_scout_run, update_scout_run
from app.repos.user_repo import get_user_by_id
from app.services.architecture_mapper import map_architecture
from app.services.scout_metrics import compute_repo_metrics
from app.services.stack_detector import detect_stack
from app.ws_manager import manager as ws_manager

from ._utils import _build_check_list

logger = logging.getLogger(__name__)

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
    from .dossier_builder import _generate_dossier

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

        # ── Step 6.5: Compute deterministic metrics ─────────────
        await _send_deep_progress(user_id_str, run_id, "metrics", "Computing quality metrics")
        repo_metrics = compute_repo_metrics(
            tree_paths=tree_paths,
            file_contents=file_contents,
            stack_profile=stack_profile,
            architecture=arch_map,
            checks=all_checks,
        )

        # ── Step 7: Generate LLM dossier (optional) ───────────────
        dossier = None
        if include_llm:
            await _send_deep_progress(user_id_str, run_id, "dossier", "Generating project dossier")
            dossier = await _generate_dossier(
                full_name, metadata, stack_profile, arch_map, file_contents,
                metrics=repo_metrics,
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
            "metrics": repo_metrics,
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
            computed_score=repo_metrics.get("computed_score"),
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
