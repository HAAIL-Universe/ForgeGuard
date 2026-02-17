"""Architecture mapper -- heuristic analysis of project structure.

Analyses a repo's file tree and key file contents to produce an
ArchitectureMap describing the project's structure, routes, models,
and external integrations.  No LLM calls -- pure pattern matching.
"""

from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Known integration libraries → service names
# ---------------------------------------------------------------------------

_INTEGRATION_IMPORTS: list[tuple[str, str]] = [
    ("httpx", "HTTP Client (httpx)"),
    ("requests", "HTTP Client (requests)"),
    ("aiohttp", "HTTP Client (aiohttp)"),
    ("asyncpg", "PostgreSQL"),
    ("psycopg2", "PostgreSQL"),
    ("psycopg", "PostgreSQL"),
    ("sqlalchemy", "SQL Database"),
    ("pymongo", "MongoDB"),
    ("motor", "MongoDB"),
    ("redis", "Redis"),
    ("boto3", "AWS"),
    ("botocore", "AWS"),
    ("google.cloud", "Google Cloud"),
    ("stripe", "Stripe"),
    ("twilio", "Twilio"),
    ("sendgrid", "SendGrid"),
    ("openai", "OpenAI API"),
    ("anthropic", "Anthropic API"),
    ("slack_sdk", "Slack"),
]

# Regex patterns for route detection in different frameworks
_ROUTE_PATTERNS: list[tuple[str, re.Pattern]] = [
    # FastAPI / Starlette
    ("fastapi", re.compile(
        r'@(?:router|app)\.(get|post|put|patch|delete|head|options)\(\s*["\']([^"\']+)["\']',
        re.IGNORECASE,
    )),
    # Flask
    ("flask", re.compile(
        r'@(?:app|bp|blueprint)\.(route)\(\s*["\']([^"\']+)["\']',
        re.IGNORECASE,
    )),
    # Express / Fastify
    ("express", re.compile(
        r'(?:app|router)\.(get|post|put|patch|delete)\(\s*["\']([^"\']+)["\']',
        re.IGNORECASE,
    )),
    # Django urlpatterns
    ("django", re.compile(
        r'path\(\s*["\']([^"\']+)["\']',
        re.IGNORECASE,
    )),
]

# Directory role heuristics (directory name → role description)
_DIR_ROLES: dict[str, str] = {
    "api": "API layer",
    "routers": "API route handlers",
    "routes": "Route handlers",
    "controllers": "Controllers",
    "views": "Views / Controllers",
    "services": "Business logic layer",
    "service": "Business logic layer",
    "models": "Data models",
    "schemas": "Data schemas / validation",
    "repos": "Data access / repository layer",
    "repositories": "Data access / repository layer",
    "middleware": "Middleware",
    "utils": "Utilities",
    "helpers": "Helper functions",
    "lib": "Library / shared code",
    "config": "Configuration",
    "tests": "Test suite",
    "test": "Test suite",
    "migrations": "Database migrations",
    "db": "Database layer",
    "static": "Static assets",
    "public": "Public assets",
    "templates": "Templates",
    "components": "UI components",
    "pages": "Page components",
    "hooks": "React hooks",
    "context": "React context / state",
    "store": "State management",
    "styles": "Stylesheets",
    "assets": "Assets",
    "scripts": "Scripts",
    "clients": "External service clients",
    "adapters": "Adapters / integrations",
    "audit": "Audit / compliance",
    "auth": "Authentication",
    "core": "Core / shared logic",
}


# ---------------------------------------------------------------------------
# Main mapper
# ---------------------------------------------------------------------------


def map_architecture(
    *,
    tree_paths: list[str],
    stack_profile: dict[str, Any],
    file_contents: dict[str, str],
) -> dict[str, Any]:
    """Map the architecture of a project.

    Parameters
    ----------
    tree_paths : list of all file paths in the repo
    stack_profile : output from detect_stack()
    file_contents : {path: content} for key files that were fetched

    Returns
    -------
    ArchitectureMap dict
    """
    structure_type = _classify_structure(tree_paths)
    entry_points = _find_entry_points(tree_paths, stack_profile)
    directories = _map_directories(tree_paths)
    route_map = _extract_routes(file_contents)
    data_models = _find_data_models(tree_paths, file_contents)
    integrations = _find_integrations(file_contents)
    config_sources = _find_config_sources(tree_paths)
    boundaries = _check_for_boundaries(tree_paths)

    # File stats
    file_count = sum(1 for p in tree_paths if not _is_ignored_path(p))
    total_size = 0  # can't compute lines without content, estimate later

    # Test coverage indicator
    test_files = [p for p in tree_paths if _is_test_file(p)]
    source_files = [p for p in tree_paths if _is_source_file(p) and not _is_test_file(p)]
    coverage_indicator = _estimate_test_coverage(len(test_files), len(source_files))

    return {
        "structure_type": structure_type,
        "entry_points": entry_points,
        "directories": directories,
        "route_map": route_map,
        "data_models": data_models,
        "external_integrations": integrations,
        "config_sources": config_sources,
        "boundaries": boundaries,
        "file_count": file_count,
        "test_coverage_indicator": coverage_indicator,
    }


# ---------------------------------------------------------------------------
# Structure classification
# ---------------------------------------------------------------------------


def _classify_structure(tree_paths: list[str]) -> str:
    """Classify the directory structure type."""
    top_dirs = set()
    for p in tree_paths:
        parts = p.split("/")
        if len(parts) > 1:
            top_dirs.add(parts[0])

    # Monorepo indicators
    if any(d in ("packages", "apps", "services", "modules") for d in top_dirs):
        return "monorepo"

    # Layered: has distinct layers (api/services/repos, or controllers/models/views)
    layered_indicators = {"api", "services", "repos", "controllers", "models", "views", "routers"}
    # Count how many are present at any depth
    all_dirs = set()
    for p in tree_paths:
        parts = p.split("/")
        for part in parts[:-1]:
            all_dirs.add(part)
    if len(layered_indicators & all_dirs) >= 2:
        return "layered"

    # Deep nesting
    max_depth = max((p.count("/") for p in tree_paths), default=0)
    if max_depth <= 1:
        return "flat"

    return "layered"


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------


def _find_entry_points(tree_paths: list[str], stack_profile: dict) -> list[str]:
    """Identify likely entry points."""
    candidates = []
    entry_filenames = {
        "main.py", "app.py", "server.py", "index.py", "wsgi.py", "asgi.py",
        "manage.py", "__main__.py",
        "index.ts", "index.tsx", "main.ts", "main.tsx", "app.ts", "app.tsx",
        "index.js", "main.js", "app.js", "server.js",
        "index.html",
    }
    for p in tree_paths:
        filename = p.split("/")[-1]
        if filename in entry_filenames:
            # Prefer shorter paths (closer to root)
            candidates.append(p)

    # Sort by depth (shallowest first), then alphabetically
    candidates.sort(key=lambda p: (p.count("/"), p))
    return candidates[:10]  # cap at 10


# ---------------------------------------------------------------------------
# Directory mapping
# ---------------------------------------------------------------------------


def _map_directories(tree_paths: list[str]) -> dict[str, str]:
    """Map directories to their likely roles."""
    dirs: set[str] = set()
    for p in tree_paths:
        parts = p.split("/")
        for i in range(len(parts) - 1):
            dir_path = "/".join(parts[: i + 1]) + "/"
            dirs.add(dir_path)

    result: dict[str, str] = {}
    for d in sorted(dirs):
        last_segment = d.rstrip("/").split("/")[-1].lower()
        if last_segment in _DIR_ROLES:
            result[d] = _DIR_ROLES[last_segment]
    return result


# ---------------------------------------------------------------------------
# Route extraction
# ---------------------------------------------------------------------------


def _extract_routes(file_contents: dict[str, str]) -> list[dict[str, str]]:
    """Extract API routes from file contents."""
    routes: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()

    for fpath, content in file_contents.items():
        for _framework, pattern in _ROUTE_PATTERNS:
            for match in pattern.finditer(content):
                groups = match.groups()
                if len(groups) == 2:
                    method, path = groups[0].upper(), groups[1]
                else:
                    method, path = "ANY", groups[0]
                key = (method, path)
                if key not in seen:
                    seen.add(key)
                    routes.append({
                        "method": method,
                        "path": path,
                        "handler": fpath,
                    })

    return routes


# ---------------------------------------------------------------------------
# Data model detection
# ---------------------------------------------------------------------------


def _find_data_models(
    tree_paths: list[str], file_contents: dict[str, str]
) -> list[str]:
    """Find data model / table names."""
    models: set[str] = set()

    for fpath, content in file_contents.items():
        # SQL CREATE TABLE
        for match in re.finditer(
            r'CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(\w+)',
            content, re.IGNORECASE,
        ):
            models.add(match.group(1))

        # ALTER TABLE
        for match in re.finditer(
            r'ALTER\s+TABLE\s+(\w+)', content, re.IGNORECASE,
        ):
            models.add(match.group(1))

        # Django / SQLAlchemy model classes
        for match in re.finditer(
            r'class\s+(\w+)\(.*(?:Model|Base|db\.Model)', content,
        ):
            models.add(match.group(1))

        # __tablename__
        for match in re.finditer(
            r'__tablename__\s*=\s*["\'](\w+)["\']', content,
        ):
            models.add(match.group(1))

    return sorted(models)


# ---------------------------------------------------------------------------
# Integration detection
# ---------------------------------------------------------------------------


def _find_integrations(file_contents: dict[str, str]) -> list[str]:
    """Detect external integrations from import statements."""
    found: set[str] = set()
    for content in file_contents.values():
        for import_name, service_name in _INTEGRATION_IMPORTS:
            # Python: import X, from X import
            if re.search(rf'(?:^|\n)\s*(?:import|from)\s+{re.escape(import_name)}\b', content):
                found.add(service_name)
            # Node: require('X'), import ... from 'X'
            if re.search(rf"(?:require|from)\s*\(?\s*['\"](?:@?{re.escape(import_name)})", content):
                found.add(service_name)
    return sorted(found)


# ---------------------------------------------------------------------------
# Config & boundaries
# ---------------------------------------------------------------------------


def _find_config_sources(tree_paths: list[str]) -> list[str]:
    """Find configuration files."""
    config_files = []
    config_names = {
        ".env", ".env.example", ".env.local",
        "config.py", "config.ts", "config.js",
        "settings.py", "settings.json",
        "appsettings.json", "application.yml",
        "forge.json", "render.yaml", "vercel.json",
    }
    for p in tree_paths:
        filename = p.split("/")[-1]
        if filename in config_names:
            config_files.append(p)
    return sorted(config_files)


def _check_for_boundaries(tree_paths: list[str]) -> dict | None:
    """Check if the project has governance/contract files."""
    has_boundaries = any("boundaries.json" in p for p in tree_paths)
    has_physics = any("physics" in p.lower() and p.endswith(".json") for p in tree_paths)
    has_forge = any(p.endswith("forge.json") for p in tree_paths)

    if not (has_boundaries or has_physics or has_forge):
        return None

    return {
        "has_boundaries": has_boundaries,
        "has_physics": has_physics,
        "has_forge_json": has_forge,
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _is_ignored_path(path: str) -> bool:
    """Check if path should be ignored for counting."""
    ignore = (
        "node_modules/", "__pycache__/", ".git/", ".venv/",
        "venv/", ".tox/", "dist/", "build/", ".next/",
    )
    return any(seg in path for seg in ignore)


def _is_source_file(path: str) -> bool:
    """Check if path is a source code file."""
    return path.endswith((".py", ".ts", ".tsx", ".js", ".jsx", ".rs", ".go", ".rb", ".java"))


def _is_test_file(path: str) -> bool:
    """Check if path is likely a test file."""
    filename = path.split("/")[-1]
    return (
        filename.startswith("test_")
        or filename.endswith(("_test.py", ".test.ts", ".test.tsx", ".test.js", ".spec.ts", ".spec.js"))
        or "/tests/" in path
        or "/__tests__/" in path
    )


def _estimate_test_coverage(test_count: int, source_count: int) -> str:
    """Rough estimate of test coverage level."""
    if source_count == 0:
        return "none"
    ratio = test_count / source_count
    if ratio >= 0.5:
        return "high"
    if ratio >= 0.2:
        return "medium"
    if test_count > 0:
        return "low"
    return "none"
