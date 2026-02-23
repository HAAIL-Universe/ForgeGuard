"""Stack resolver — detect actual project stack from contracts + filesystem.

Used by sign-off generators to produce accurate README, instructions, and
boot scripts based on what was actually built (not just what was planned).
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class StackInfo:
    """Normalized stack information for sign-off generators."""

    project_name: str = "Project"
    project_description: str | None = None
    primary_language: str = "javascript"  # "javascript" | "python" | "go"
    backend_framework: str | None = None  # "express" | "fastapi" | etc.
    frontend_framework: str | None = None  # "react" | "vue" | None
    database: str | None = None  # "sqlite" | "postgresql" | etc.
    has_frontend: bool = False
    has_backend: bool = False
    has_docker: bool = False
    dev_platform: str = "unix"  # "windows_cmd" | "windows_ps" | "unix"
    env_vars: list[dict] = field(default_factory=list)  # [{name, default, required}]
    install_commands: list[dict] = field(default_factory=list)  # [{dir, cmd}]
    run_commands: dict[str, str] = field(default_factory=dict)  # {service: command}
    test_command: str | None = None
    node_version: str = "18"
    python_version: str = "3.12"


def resolve_stack(
    contracts: list[dict],
    working_dir: str,
    dev_platform: str = "unix",
) -> StackInfo:
    """Resolve the actual stack from contracts and the built project filesystem.

    Examines:
    1. The 'stack' contract for declared stack
    2. forge_plan.json for stack metadata
    3. The actual filesystem (package.json, requirements.txt, .env.example)
    """
    wd = Path(working_dir)
    info = StackInfo(dev_platform=dev_platform)

    # --- Extract from contracts ---
    _extract_from_contracts(contracts, info)

    # --- Extract from forge_plan.json ---
    plan_path = wd / "forge_plan.json"
    if plan_path.exists():
        _extract_from_plan(plan_path, info)

    # --- Detect from filesystem (overrides/supplements contract data) ---
    _detect_from_filesystem(wd, info)

    return info


def _extract_from_contracts(contracts: list[dict], info: StackInfo) -> None:
    """Extract stack info from contract content."""
    for c in contracts:
        ctype = c.get("contract_type", "")
        content = c.get("content", "")

        if ctype == "blueprint":
            # Extract project name from first H1
            for line in content.splitlines():
                if line.startswith("# "):
                    info.project_name = line[2:].strip()
                    break
            # Extract description from first non-heading non-empty line after title
            past_title = False
            for line in content.splitlines():
                if line.startswith("# "):
                    past_title = True
                    continue
                if past_title and line.strip() and not line.startswith("#"):
                    info.project_description = line.strip()
                    break

        elif ctype == "stack":
            low = content.lower()
            # Language detection
            if any(x in low for x in ("python", "fastapi", "django", "flask")):
                info.primary_language = "python"
            elif any(x in low for x in ("node", "express", "javascript", "typescript")):
                info.primary_language = "javascript"
            elif "go" in low.split():
                info.primary_language = "go"

            # Framework detection
            for fw in ("fastapi", "django", "flask"):
                if fw in low:
                    info.backend_framework = fw
                    break
            for fw in ("express", "fastify", "nestjs", "koa"):
                if fw in low:
                    info.backend_framework = fw
                    break
            for fw in ("react", "vue", "angular", "svelte"):
                if fw in low:
                    info.frontend_framework = fw
                    break

            # Database detection
            for db in ("postgresql", "postgres"):
                if db in low:
                    info.database = "postgresql"
                    break
            if "sqlite" in low:
                info.database = "sqlite"
            if "mongodb" in low or "mongo" in low:
                info.database = "mongodb"


def _extract_from_plan(plan_path: Path, info: StackInfo) -> None:
    """Extract stack info from forge_plan.json."""
    try:
        data = json.loads(plan_path.read_text(encoding="utf-8"))
        stack = data.get("stack", {})
        summary = data.get("summary", {})

        if stack.get("backend_language"):
            info.primary_language = stack["backend_language"]
        if stack.get("backend_framework"):
            info.backend_framework = stack["backend_framework"]
        if stack.get("frontend"):
            info.frontend_framework = stack["frontend"]
            info.has_frontend = True
        if stack.get("database"):
            info.database = stack["database"]

        # Project name from summary
        if not info.project_name or info.project_name == "Project":
            meta = data.get("metadata", {})
            if meta.get("project_name"):
                info.project_name = meta["project_name"]

        if summary.get("one_liner"):
            info.project_description = summary["one_liner"]

    except Exception:
        logger.debug("Could not parse forge_plan.json at %s", plan_path)


def _detect_from_filesystem(wd: Path, info: StackInfo) -> None:
    """Detect stack from actual files on disk."""

    # --- Backend detection ---
    # Python backend
    if (wd / "requirements.txt").exists() or (wd / "pyproject.toml").exists():
        info.has_backend = True
        if info.primary_language != "python":
            info.primary_language = "python"
        info.install_commands.append({"dir": ".", "cmd": "pip install -r requirements.txt"})
        info.test_command = info.test_command or "pytest"

    # Node backend (root or backend/)
    for backend_dir in ("backend", "server", "api", "."):
        pkg = wd / backend_dir / "package.json"
        if pkg.exists():
            try:
                data = json.loads(pkg.read_text(encoding="utf-8"))
                scripts = data.get("scripts", {})
                deps = data.get("dependencies", {})

                info.has_backend = True
                dir_label = backend_dir if backend_dir != "." else "."
                info.install_commands.append({"dir": dir_label, "cmd": "npm install"})

                if "express" in deps:
                    info.backend_framework = info.backend_framework or "express"
                if scripts.get("start"):
                    info.run_commands[dir_label] = f"npm start"
                elif scripts.get("dev"):
                    info.run_commands[dir_label] = f"npm run dev"

                if scripts.get("test") and "no test" not in scripts["test"]:
                    info.test_command = info.test_command or f"npm test"
            except Exception:
                pass
            if backend_dir != ".":
                break

    # --- Frontend detection ---
    for fe_dir in ("frontend", "client", "web", "ui"):
        pkg = wd / fe_dir / "package.json"
        if pkg.exists():
            try:
                data = json.loads(pkg.read_text(encoding="utf-8"))
                scripts = data.get("scripts", {})
                deps = data.get("dependencies", {})

                info.has_frontend = True
                info.install_commands.append({"dir": fe_dir, "cmd": "npm install"})

                if "react" in deps or "react-dom" in deps:
                    info.frontend_framework = info.frontend_framework or "react"
                elif "vue" in deps:
                    info.frontend_framework = info.frontend_framework or "vue"

                if scripts.get("dev"):
                    info.run_commands[fe_dir] = "npm run dev"
                elif scripts.get("start"):
                    info.run_commands[fe_dir] = "npm start"
            except Exception:
                pass
            break

    # --- Env vars from .env.example ---
    for env_file in (".env.example", ".env.template"):
        for search_dir in (".", "backend", "frontend"):
            env_path = wd / search_dir / env_file
            if env_path.exists():
                _parse_env_example(env_path, search_dir, info)

    # --- Docker detection ---
    info.has_docker = (
        (wd / "Dockerfile").exists()
        or (wd / "docker-compose.yml").exists()
        or (wd / "docker-compose.yaml").exists()
        or any((wd / d / "Dockerfile").exists() for d in ("backend", "frontend"))
    )

    # --- Node version from engines ---
    for search_dir in (".", "frontend", "backend"):
        pkg = wd / search_dir / "package.json"
        if pkg.exists():
            try:
                data = json.loads(pkg.read_text(encoding="utf-8"))
                engines = data.get("engines", {})
                if engines.get("node"):
                    m = re.search(r"(\d+)", engines["node"])
                    if m:
                        info.node_version = m.group(1)
            except Exception:
                pass
            break


def _parse_env_example(env_path: Path, search_dir: str, info: StackInfo) -> None:
    """Parse .env.example and extract variable names + defaults."""
    try:
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip()
            # Don't duplicate
            if any(v["name"] == key for v in info.env_vars):
                continue
            info.env_vars.append({
                "name": key,
                "default": value if value else None,
                "required": not bool(value),
                "dir": search_dir if search_dir != "." else "root",
            })
    except Exception:
        pass
