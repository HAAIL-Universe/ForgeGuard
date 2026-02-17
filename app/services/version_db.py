"""Version currency database — known latest stable versions of major packages.

A static, in-memory registry mapping package/framework names to their
latest stable versions, minimum recommended versions, and EOL dates.
No external API calls — curated and updated periodically.
"""

from __future__ import annotations

from typing import Any

# ---------------------------------------------------------------------------
# Version registry
# ---------------------------------------------------------------------------

# Each entry:
#   "latest"           – latest stable version string
#   "min_recommended"  – oldest version still considered acceptable
#   "eol"              – dict of version → EOL date (optional)
#   "category"         – runtime | backend | frontend | database | orm |
#                        testing | bundler | styling | language | server |
#                        validation | devops

LATEST_VERSIONS: dict[str, dict[str, Any]] = {
    # ── Python ecosystem ──────────────────────────────────────────────
    "python": {
        "latest": "3.12",
        "min_recommended": "3.10",
        "eol": {"3.7": "2023-06", "3.8": "2024-10", "3.9": "2025-10"},
        "category": "runtime",
    },
    "fastapi": {
        "latest": "0.115",
        "min_recommended": "0.100",
        "category": "backend",
    },
    "django": {
        "latest": "5.1",
        "min_recommended": "4.2",
        "eol": {"3.2": "2024-04", "2.2": "2022-04"},
        "category": "backend",
    },
    "flask": {
        "latest": "3.1",
        "min_recommended": "2.3",
        "category": "backend",
    },
    "starlette": {
        "latest": "0.41",
        "min_recommended": "0.30",
        "category": "backend",
    },
    "sqlalchemy": {
        "latest": "2.0",
        "min_recommended": "2.0",
        "eol": {"1.4": "2024-01"},
        "category": "orm",
    },
    "pydantic": {
        "latest": "2.10",
        "min_recommended": "2.0",
        "eol": {"1.10": "2024-01"},
        "category": "validation",
    },
    "pytest": {
        "latest": "8.3",
        "min_recommended": "7.0",
        "category": "testing",
    },
    "asyncpg": {
        "latest": "0.30",
        "min_recommended": "0.28",
        "category": "database",
    },
    "uvicorn": {
        "latest": "0.32",
        "min_recommended": "0.20",
        "category": "server",
    },
    "ruff": {
        "latest": "0.8",
        "min_recommended": "0.1",
        "category": "devops",
    },
    "black": {
        "latest": "24.10",
        "min_recommended": "23.0",
        "category": "devops",
    },
    "celery": {
        "latest": "5.4",
        "min_recommended": "5.2",
        "category": "backend",
    },
    "gunicorn": {
        "latest": "22.0",
        "min_recommended": "21.0",
        "category": "server",
    },
    "httpx": {
        "latest": "0.28",
        "min_recommended": "0.24",
        "category": "backend",
    },

    # ── Node ecosystem ────────────────────────────────────────────────
    "node": {
        "latest": "22",
        "min_recommended": "20",
        "eol": {"16": "2023-09", "18": "2025-04"},
        "category": "runtime",
    },
    "react": {
        "latest": "19.0",
        "min_recommended": "18.0",
        "eol": {"16": "2022-01", "17": "2023-01"},
        "category": "frontend",
    },
    "next": {
        "latest": "15.1",
        "min_recommended": "14.0",
        "category": "frontend",
    },
    "vue": {
        "latest": "3.5",
        "min_recommended": "3.3",
        "eol": {"2": "2023-12"},
        "category": "frontend",
    },
    "angular": {
        "latest": "19.0",
        "min_recommended": "17.0",
        "category": "frontend",
    },
    "svelte": {
        "latest": "5.0",
        "min_recommended": "4.0",
        "category": "frontend",
    },
    "vite": {
        "latest": "6.1",
        "min_recommended": "5.0",
        "category": "bundler",
    },
    "webpack": {
        "latest": "5.97",
        "min_recommended": "5.70",
        "eol": {"4": "2023-01"},
        "category": "bundler",
    },
    "typescript": {
        "latest": "5.7",
        "min_recommended": "5.0",
        "category": "language",
    },
    "express": {
        "latest": "5.0",
        "min_recommended": "4.18",
        "category": "backend",
    },
    "fastify": {
        "latest": "5.2",
        "min_recommended": "4.20",
        "category": "backend",
    },
    "nestjs": {
        "latest": "10.4",
        "min_recommended": "10.0",
        "category": "backend",
    },
    "vitest": {
        "latest": "3.0",
        "min_recommended": "1.0",
        "category": "testing",
    },
    "jest": {
        "latest": "29.7",
        "min_recommended": "29.0",
        "category": "testing",
    },
    "tailwindcss": {
        "latest": "4.0",
        "min_recommended": "3.4",
        "category": "styling",
    },
    "eslint": {
        "latest": "9.17",
        "min_recommended": "8.50",
        "category": "devops",
    },
    "prettier": {
        "latest": "3.4",
        "min_recommended": "3.0",
        "category": "devops",
    },

    # ── Databases ─────────────────────────────────────────────────────
    "postgresql": {
        "latest": "17",
        "min_recommended": "15",
        "eol": {"12": "2024-11", "13": "2025-11"},
        "category": "database",
    },
    "mysql": {
        "latest": "8.4",
        "min_recommended": "8.0",
        "category": "database",
    },
    "redis": {
        "latest": "7.4",
        "min_recommended": "7.0",
        "category": "database",
    },
    "mongodb": {
        "latest": "8.0",
        "min_recommended": "7.0",
        "category": "database",
    },
}

# Aliases — alternative package names that map to the same entry
_ALIASES: dict[str, str] = {
    "@nestjs/core": "nestjs",
    "@angular/core": "angular",
    "psycopg2": "postgresql",
    "psycopg": "postgresql",
    "pg": "postgresql",
    "mysql2": "mysql",
    "pymongo": "mongodb",
    "motor": "mongodb",
    "ioredis": "redis",
    "tailwind": "tailwindcss",
    "solid-js": "svelte",  # similar generation, map to closest
}


# ---------------------------------------------------------------------------
# Version comparison helpers
# ---------------------------------------------------------------------------


def _parse_version(v: str) -> tuple[int, ...]:
    """Parse a version string into comparable tuple of ints."""
    parts: list[int] = []
    for segment in v.split("."):
        # Strip non-numeric suffixes (e.g. "0.115" → (0, 115))
        num = ""
        for ch in segment:
            if ch.isdigit():
                num += ch
            else:
                break
        if num:
            parts.append(int(num))
    return tuple(parts) if parts else (0,)


def _version_gte(a: str, b: str) -> bool:
    """Return True if version a >= version b."""
    return _parse_version(a) >= _parse_version(b)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def check_version_currency(
    dep_name: str, detected_version: str | None,
) -> dict[str, str | None]:
    """Check whether a dependency is current, outdated, or end-of-life.

    Parameters
    ----------
    dep_name : normalised dependency name (lowercase)
    detected_version : version string detected from manifests, or None

    Returns
    -------
    {
        "package": dep_name,
        "current": detected_version or "unknown",
        "latest": latest version or None,
        "status": "current" | "outdated" | "eol" | "unknown",
        "detail": human-readable explanation,
    }
    """
    canonical = _ALIASES.get(dep_name.lower(), dep_name.lower())
    info = LATEST_VERSIONS.get(canonical)

    if info is None:
        return {
            "package": dep_name,
            "current": detected_version or "unknown",
            "latest": None,
            "status": "unknown",
            "detail": f"No version data available for {dep_name}",
        }

    latest = info["latest"]
    min_rec = info.get("min_recommended", latest)

    if detected_version is None:
        return {
            "package": dep_name,
            "current": "unknown",
            "latest": latest,
            "status": "unknown",
            "detail": f"Version not detected; latest is {latest}",
        }

    # Check EOL
    eol_map = info.get("eol", {})
    for eol_ver, eol_date in eol_map.items():
        # If detected version starts with or matches an EOL version
        if detected_version.startswith(eol_ver):
            return {
                "package": dep_name,
                "current": detected_version,
                "latest": latest,
                "status": "eol",
                "detail": f"{dep_name} {detected_version} reached end-of-life ({eol_date}). Latest: {latest}",
            }

    # Check if current
    if _version_gte(detected_version, min_rec):
        return {
            "package": dep_name,
            "current": detected_version,
            "latest": latest,
            "status": "current",
            "detail": f"{dep_name} {detected_version} is up to date",
        }

    # Outdated
    return {
        "package": dep_name,
        "current": detected_version,
        "latest": latest,
        "status": "outdated",
        "detail": f"{dep_name} {detected_version} is outdated. Recommended: >={min_rec}, latest: {latest}",
    }


def check_all_dependencies(
    py_deps: dict[str, str | None] | None = None,
    node_deps: dict[str, str | None] | None = None,
) -> list[dict[str, str | None]]:
    """Check version currency for all provided dependencies.

    Parameters
    ----------
    py_deps : {package_name: version_or_none} from requirements/pyproject
    node_deps : {package_name: version_or_none} from package.json

    Returns
    -------
    List of CurrencyResult dicts, sorted by status priority (eol > outdated > current > unknown).
    """
    results: list[dict[str, str | None]] = []

    for deps in (py_deps, node_deps):
        if deps is None:
            continue
        for name, version in deps.items():
            result = check_version_currency(name, version)
            results.append(result)

    # Sort: eol first, then outdated, then unknown, then current
    priority = {"eol": 0, "outdated": 1, "unknown": 2, "current": 3}
    results.sort(key=lambda r: priority.get(r.get("status", "unknown"), 2))

    return results


def get_all_version_info() -> dict[str, dict[str, Any]]:
    """Return the full version registry (for UI display)."""
    return dict(LATEST_VERSIONS)
