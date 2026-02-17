"""Stack detector -- heuristic-based project stack identification.

Analyses a repo's file tree, language byte counts, and manifest contents
to produce a StackProfile describing the project's technology stack.
No LLM calls -- pure pattern matching.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Framework detection registries
# ---------------------------------------------------------------------------

# (dependency_name, framework_label)
_PYTHON_FRAMEWORKS: list[tuple[str, str]] = [
    ("fastapi", "FastAPI"),
    ("django", "Django"),
    ("flask", "Flask"),
    ("tornado", "Tornado"),
    ("starlette", "Starlette"),
    ("sanic", "Sanic"),
    ("bottle", "Bottle"),
    ("falcon", "Falcon"),
    ("aiohttp", "aiohttp"),
    ("litestar", "Litestar"),
]

_PYTHON_ORMS: list[tuple[str, str]] = [
    ("sqlalchemy", "SQLAlchemy"),
    ("django", "Django ORM"),
    ("tortoise-orm", "Tortoise ORM"),
    ("peewee", "Peewee"),
    ("asyncpg", "raw SQL (asyncpg)"),
    ("psycopg2", "raw SQL (psycopg2)"),
    ("psycopg", "raw SQL (psycopg)"),
    ("aiosqlite", "SQLite (aiosqlite)"),
]

_PYTHON_DBS: list[tuple[str, str]] = [
    ("asyncpg", "PostgreSQL"),
    ("psycopg2", "PostgreSQL"),
    ("psycopg", "PostgreSQL"),
    ("aiosqlite", "SQLite"),
    ("pymongo", "MongoDB"),
    ("motor", "MongoDB"),
    ("redis", "Redis"),
]

_NODE_FRAMEWORKS: list[tuple[str, str]] = [
    ("next", "Next.js"),
    ("nuxt", "Nuxt"),
    ("express", "Express"),
    ("fastify", "Fastify"),
    ("koa", "Koa"),
    ("hapi", "Hapi"),
    ("nest", "NestJS"),
    ("@nestjs/core", "NestJS"),
    ("hono", "Hono"),
]

_NODE_FRONTEND_FRAMEWORKS: list[tuple[str, str]] = [
    ("next", "Next.js"),
    ("nuxt", "Nuxt"),
    ("react", "React"),
    ("vue", "Vue"),
    ("svelte", "Svelte"),
    ("@angular/core", "Angular"),
    ("solid-js", "SolidJS"),
    ("preact", "Preact"),
]

_NODE_BUNDLERS: list[tuple[str, str]] = [
    ("vite", "Vite"),
    ("webpack", "Webpack"),
    ("esbuild", "esbuild"),
    ("rollup", "Rollup"),
    ("parcel", "Parcel"),
    ("turbopack", "Turbopack"),
]

_NODE_UI_LIBS: list[tuple[str, str]] = [
    ("tailwindcss", "Tailwind CSS"),
    ("@mui/material", "MUI"),
    ("@chakra-ui/react", "Chakra UI"),
    ("antd", "Ant Design"),
    ("bootstrap", "Bootstrap"),
    ("@radix-ui", "Radix UI"),
    ("shadcn", "shadcn/ui"),
]

_NODE_TEST_FRAMEWORKS: list[tuple[str, str]] = [
    ("vitest", "Vitest"),
    ("jest", "Jest"),
    ("mocha", "Mocha"),
    ("cypress", "Cypress"),
    ("playwright", "Playwright"),
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_requirements_txt(content: str) -> list[str]:
    """Extract package names from a requirements.txt string."""
    deps: list[str] = []
    for line in content.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("-"):
            continue
        # strip version specifiers
        name = re.split(r"[><=!~;\[]", line)[0].strip().lower()
        if name:
            deps.append(name)
    return deps


def _parse_pyproject_deps(content: str) -> list[str]:
    """Best-effort extraction of dependency names from pyproject.toml."""
    deps: list[str] = []
    in_deps = False
    for line in content.splitlines():
        stripped = line.strip()
        if stripped in ("dependencies = [", "[project.dependencies]", "[tool.poetry.dependencies]"):
            in_deps = True
            continue
        if in_deps:
            if stripped.startswith("]") or stripped.startswith("["):
                in_deps = False
                continue
            name = re.split(r"[><=!~;\[\"',]", stripped.strip('"').strip("'"))[0].strip().lower()
            if name and name != "python":
                deps.append(name)
    return deps


def _get_pkg_json_deps(pkg_json: dict) -> list[str]:
    """Collect all dependency names from a parsed package.json."""
    deps: list[str] = []
    for section in ("dependencies", "devDependencies", "peerDependencies"):
        deps.extend(pkg_json.get(section, {}).keys())
    return [d.lower() for d in deps]


def _first_match(deps: list[str], registry: list[tuple[str, str]]) -> str | None:
    """Return the label of the first matching dependency."""
    dep_set = set(deps)
    for dep_name, label in registry:
        if dep_name in dep_set:
            return label
    return None


def _all_matches(deps: list[str], registry: list[tuple[str, str]]) -> list[str]:
    """Return all matching labels."""
    dep_set = set(deps)
    return [label for dep_name, label in registry if dep_name in dep_set]


# ---------------------------------------------------------------------------
# Main detection
# ---------------------------------------------------------------------------


def detect_stack(
    *,
    tree_paths: list[str],
    language_bytes: dict[str, int],
    requirements_txt: str | None = None,
    pyproject_toml: str | None = None,
    package_json: str | None = None,
) -> dict[str, Any]:
    """Detect the technology stack from repo analysis data.

    Parameters
    ----------
    tree_paths : list of file paths in the repo
    language_bytes : GitHub language byte counts
    requirements_txt : raw requirements.txt content (if present)
    pyproject_toml : raw pyproject.toml content (if present)
    package_json : raw package.json string (if present)

    Returns
    -------
    StackProfile dict
    """
    # Language percentages
    total_bytes = sum(language_bytes.values()) or 1
    languages = {
        lang: round(b / total_bytes * 100, 1)
        for lang, b in sorted(language_bytes.items(), key=lambda x: -x[1])
    }
    primary_language = max(language_bytes, key=language_bytes.get) if language_bytes else None

    # Parse manifests
    py_deps: list[str] = []
    if requirements_txt:
        py_deps = _parse_requirements_txt(requirements_txt)
    if pyproject_toml and not py_deps:
        py_deps = _parse_pyproject_deps(pyproject_toml)

    pkg_json_data: dict = {}
    node_deps: list[str] = []
    if package_json:
        try:
            pkg_json_data = json.loads(package_json)
            node_deps = _get_pkg_json_deps(pkg_json_data)
        except (json.JSONDecodeError, TypeError):
            pass

    # Determine manifest files present
    path_set = set(tree_paths)
    manifest_files = []
    for mf in ("requirements.txt", "pyproject.toml", "Pipfile", "setup.py",
               "package.json", "Cargo.toml", "go.mod", "Gemfile",
               "pom.xml", "build.gradle", "composer.json"):
        # Check root + any nested
        if mf in path_set or any(p.endswith(f"/{mf}") for p in tree_paths):
            manifest_files.append(mf)

    # Backend detection
    backend = None
    if py_deps:
        framework = _first_match(py_deps, _PYTHON_FRAMEWORKS)
        orm = _first_match(py_deps, _PYTHON_ORMS)
        db = _first_match(py_deps, _PYTHON_DBS)
        runtime = _detect_python_runtime(tree_paths, pyproject_toml)
        if framework:
            backend = {
                "framework": framework,
                "version": None,  # would need to parse pinned versions
                "runtime": runtime,
                "orm": orm,
                "db": db,
            }
    if backend is None and node_deps:
        # Check for Node backend frameworks (not just frontend)
        node_backend = _first_match(node_deps, _NODE_FRAMEWORKS)
        if node_backend and node_backend not in ("Next.js", "Nuxt"):
            backend = {
                "framework": node_backend,
                "version": None,
                "runtime": "Node.js",
                "orm": None,
                "db": None,
            }

    # Frontend detection
    frontend = None
    if node_deps:
        fw = _first_match(node_deps, _NODE_FRONTEND_FRAMEWORKS)
        if fw:
            bundler = _first_match(node_deps, _NODE_BUNDLERS)
            ui_lib = _first_match(node_deps, _NODE_UI_LIBS)
            has_ts = "TypeScript" in language_bytes or "tsconfig.json" in path_set or any(
                p.endswith(".ts") or p.endswith(".tsx") for p in tree_paths
            )
            frontend = {
                "framework": fw,
                "version": None,
                "bundler": bundler,
                "language": "TypeScript" if has_ts else "JavaScript",
                "ui_library": ui_lib,
            }

    # If no backend detected but there are Python files with no framework
    if backend is None and primary_language == "Python":
        backend = {
            "framework": None,
            "version": None,
            "runtime": _detect_python_runtime(tree_paths, pyproject_toml),
            "orm": None,
            "db": None,
        }

    # If no frontend but there's HTML/CSS and no Python backend framework
    if frontend is None:
        html_files = [p for p in tree_paths if p.endswith(".html")]
        if html_files and not node_deps:
            frontend = {
                "framework": "Static HTML",
                "version": None,
                "bundler": None,
                "language": "HTML/CSS",
                "ui_library": None,
            }

    # Infrastructure
    infrastructure = _detect_infrastructure(tree_paths, path_set)

    # Testing
    testing = _detect_testing(tree_paths, py_deps, node_deps)

    # Project type
    project_type = _classify_project_type(tree_paths, path_set, backend, frontend, node_deps)

    return {
        "languages": languages,
        "primary_language": primary_language,
        "backend": backend,
        "frontend": frontend,
        "infrastructure": infrastructure,
        "testing": testing,
        "project_type": project_type,
        "manifest_files": manifest_files,
    }


def _detect_python_runtime(
    tree_paths: list[str], pyproject_toml: str | None
) -> str:
    """Best-effort Python runtime version detection."""
    if pyproject_toml:
        match = re.search(r'python_requires\s*=\s*"[><=!~]*(\d+\.\d+)', pyproject_toml)
        if match:
            return f"Python {match.group(1)}+"
        match = re.search(r'python\s*=\s*"[><=!^~]*(\d+\.\d+)', pyproject_toml)
        if match:
            return f"Python {match.group(1)}+"
    return "Python 3"


def _detect_infrastructure(
    tree_paths: list[str], path_set: set[str]
) -> dict[str, Any]:
    """Detect infrastructure setup from file tree."""
    containerized = "Dockerfile" in path_set or any(
        p.endswith("Dockerfile") or p == "docker-compose.yml" or p == "docker-compose.yaml"
        for p in tree_paths
    )
    ci_cd = None
    if any(p.startswith(".github/workflows/") for p in tree_paths):
        ci_cd = "GitHub Actions"
    elif ".gitlab-ci.yml" in path_set:
        ci_cd = "GitLab CI"
    elif "Jenkinsfile" in path_set:
        ci_cd = "Jenkins"
    elif ".circleci/" in path_set or any(p.startswith(".circleci/") for p in tree_paths):
        ci_cd = "CircleCI"

    hosting = None
    if "render.yaml" in path_set:
        hosting = "Render"
    elif "vercel.json" in path_set:
        hosting = "Vercel"
    elif "netlify.toml" in path_set:
        hosting = "Netlify"
    elif "fly.toml" in path_set:
        hosting = "Fly.io"
    elif any("heroku" in p.lower() for p in tree_paths):
        hosting = "Heroku"

    return {
        "containerized": containerized,
        "ci_cd": ci_cd,
        "hosting": hosting,
    }


def _detect_testing(
    tree_paths: list[str],
    py_deps: list[str],
    node_deps: list[str],
) -> dict[str, Any]:
    """Detect testing setup."""
    has_tests = any(
        "test" in p.lower() and (p.endswith(".py") or p.endswith(".ts") or p.endswith(".js") or p.endswith(".tsx"))
        for p in tree_paths
    )
    backend_fw = None
    if "pytest" in py_deps:
        backend_fw = "pytest"
    elif "unittest" in py_deps:
        backend_fw = "unittest"
    elif any(p.endswith("test.py") or "/test_" in p for p in tree_paths):
        backend_fw = "pytest"  # likely pytest if test files exist

    frontend_fw = _first_match(node_deps, _NODE_TEST_FRAMEWORKS)

    return {
        "backend_framework": backend_fw,
        "frontend_framework": frontend_fw,
        "has_tests": has_tests,
    }


def _classify_project_type(
    tree_paths: list[str],
    path_set: set[str],
    backend: dict | None,
    frontend: dict | None,
    node_deps: list[str],
) -> str:
    """Classify the overall project type."""
    # Monorepo: multiple package.json files or workspace config
    pkg_jsons = [p for p in tree_paths if p.endswith("package.json")]
    if len(pkg_jsons) > 2:
        return "monorepo"
    if any("workspaces" in p.lower() for p in tree_paths):
        return "monorepo"

    # Library: setup.py or pyproject.toml with no web framework, or package with main in index
    if backend and backend.get("framework") is None and "setup.py" in path_set:
        return "library"

    # CLI: entry point scripts, console_scripts in pyproject
    if any(p.endswith("__main__.py") for p in tree_paths) and not backend:
        return "cli"

    # Web app: has both backend and frontend
    if backend and frontend:
        return "web_app"

    # API: has backend framework but no frontend
    if backend and backend.get("framework") and not frontend:
        return "api"

    # Static site
    if frontend and frontend.get("framework") == "Static HTML":
        return "static_site"

    # Single-page app (frontend only with framework)
    if frontend and not backend:
        return "spa"

    return "unknown"
