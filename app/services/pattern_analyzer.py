"""Pattern analyzer — detect anti-patterns and missing best practices.

Scans the StackProfile, ArchitectureMap, and file contents to identify
code quality, security, maintainability, and devops gaps. Pure heuristic
analysis — no LLM calls.
"""

from __future__ import annotations

import re
from typing import Any

# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

PatternFinding = dict[str, Any]
# {
#   "id": "AP01",
#   "name": "...",
#   "severity": "low" | "medium" | "high",
#   "category": "quality" | "security" | "maintainability" | "devops",
#   "detail": "...",
#   "affected_files": [...] | None,
# }


# ---------------------------------------------------------------------------
# Individual detectors
# ---------------------------------------------------------------------------


def _ap01_no_typescript(
    stack: dict, _arch: dict, _files: dict[str, str],
) -> PatternFinding | None:
    """AP01: Frontend uses plain JavaScript instead of TypeScript."""
    fe = stack.get("frontend")
    if fe is None:
        return None
    if fe.get("language", "").lower() == "javascript":
        return {
            "id": "AP01",
            "name": "No TypeScript",
            "severity": "medium",
            "category": "quality",
            "detail": (
                "Frontend uses plain JavaScript. TypeScript catches type "
                "errors at compile time and improves code navigation."
            ),
            "affected_files": None,
        }
    return None


def _ap02_no_tests(
    stack: dict, _arch: dict, _files: dict[str, str],
) -> PatternFinding | None:
    """AP02: No test framework detected."""
    testing = stack.get("testing", {})
    if not testing.get("has_tests", False):
        return {
            "id": "AP02",
            "name": "No tests detected",
            "severity": "high",
            "category": "quality",
            "detail": (
                "No test files or test framework detected. Automated "
                "tests are essential for maintainable software."
            ),
            "affected_files": None,
        }
    return None


def _ap03_no_ci(
    stack: dict, _arch: dict, _files: dict[str, str],
) -> PatternFinding | None:
    """AP03: No CI/CD pipeline detected."""
    infra = stack.get("infrastructure", {})
    if infra.get("ci_cd") is None:
        return {
            "id": "AP03",
            "name": "No CI/CD pipeline",
            "severity": "high",
            "category": "devops",
            "detail": (
                "No continuous integration pipeline detected. CI automates "
                "testing and catches regressions before merge."
            ),
            "affected_files": None,
        }
    return None


def _ap04_no_docker(
    stack: dict, _arch: dict, _files: dict[str, str],
) -> PatternFinding | None:
    """AP04: No containerization."""
    infra = stack.get("infrastructure", {})
    if not infra.get("containerized", False):
        return {
            "id": "AP04",
            "name": "No containerization",
            "severity": "medium",
            "category": "devops",
            "detail": (
                "No Dockerfile or docker-compose detected. Containers "
                "ensure consistent environments across dev/staging/prod."
            ),
            "affected_files": None,
        }
    return None


def _ap05_raw_sql(
    stack: dict, _arch: dict, _files: dict[str, str],
) -> PatternFinding | None:
    """AP05: Using raw SQL without an ORM."""
    be = stack.get("backend")
    if be is None:
        return None
    orm = be.get("orm")
    if orm and "raw sql" in orm.lower():
        return {
            "id": "AP05",
            "name": "Raw SQL without ORM",
            "severity": "low",
            "category": "maintainability",
            "detail": (
                f"Using {orm}. An ORM like SQLAlchemy provides query "
                "building, migrations, and protection against SQL injection."
            ),
            "affected_files": None,
        }
    return None


def _ap06_no_env_example(
    stack: dict, _arch: dict, _files: dict[str, str],
) -> PatternFinding | None:
    """AP06: Has .env but no .env.example."""
    manifests = stack.get("manifest_files", [])
    # We need to check the architecture config sources
    return None  # Handled by arch check below


def _ap06_no_env_example_arch(
    _stack: dict, arch: dict, _files: dict[str, str],
) -> PatternFinding | None:
    """AP06: Has .env but no .env.example (via architecture config sources)."""
    configs = arch.get("config_sources", [])
    has_env = any(c.endswith(".env") or c == ".env" for c in configs)
    has_example = any(".env.example" in c or ".env.local" in c for c in configs)
    if has_env and not has_example:
        return {
            "id": "AP06",
            "name": "Missing .env.example",
            "severity": "medium",
            "category": "devops",
            "detail": (
                "Project uses .env for configuration but has no "
                ".env.example template for new developers."
            ),
            "affected_files": [".env"],
        }
    return None


def _ap07_no_type_hints(
    stack: dict, _arch: dict, files: dict[str, str],
) -> PatternFinding | None:
    """AP07: Python functions without type hints."""
    if stack.get("primary_language") != "Python":
        return None

    untyped_files: list[str] = []
    untyped_count = 0
    total_defs = 0

    for fpath, content in files.items():
        if not fpath.endswith(".py"):
            continue
        for line in content.splitlines():
            stripped = line.strip()
            if stripped.startswith("def ") or stripped.startswith("async def "):
                total_defs += 1
                # Check for -> return annotation or : param annotation
                if "->" not in stripped and ":" not in stripped.split("(", 1)[-1].split(")")[0]:
                    untyped_count += 1
                    if fpath not in untyped_files:
                        untyped_files.append(fpath)

    if total_defs > 0 and untyped_count / total_defs > 0.5:
        return {
            "id": "AP07",
            "name": "Missing type hints",
            "severity": "medium",
            "category": "quality",
            "detail": (
                f"{untyped_count}/{total_defs} functions lack type annotations. "
                "Type hints improve readability and enable static analysis."
            ),
            "affected_files": untyped_files[:5],
        }
    return None


def _ap08_class_components(
    stack: dict, _arch: dict, files: dict[str, str],
) -> PatternFinding | None:
    """AP08: React class components instead of functional + hooks."""
    fe = stack.get("frontend")
    if fe is None or fe.get("framework") not in ("React", "Next.js"):
        return None

    affected: list[str] = []
    pattern = re.compile(r"extends\s+(?:React\.)?Component")
    for fpath, content in files.items():
        if fpath.endswith((".tsx", ".jsx", ".ts", ".js")):
            if pattern.search(content):
                affected.append(fpath)

    if affected:
        return {
            "id": "AP08",
            "name": "React class components",
            "severity": "medium",
            "category": "modernization",
            "detail": (
                f"{len(affected)} file(s) use React class components. "
                "Functional components with hooks are the modern standard."
            ),
            "affected_files": affected,
        }
    return None


def _ap09_callback_hell(
    _stack: dict, _arch: dict, files: dict[str, str],
) -> PatternFinding | None:
    """AP09: Deep callback nesting in JavaScript/TypeScript."""
    affected: list[str] = []
    pattern = re.compile(r"function\s*\(\s*err")

    for fpath, content in files.items():
        if not fpath.endswith((".js", ".ts")):
            continue
        matches = pattern.findall(content)
        if len(matches) >= 3:
            affected.append(fpath)

    if affected:
        return {
            "id": "AP09",
            "name": "Callback hell",
            "severity": "medium",
            "category": "maintainability",
            "detail": (
                f"{len(affected)} file(s) show heavy callback patterns. "
                "Use async/await or Promises for cleaner async code."
            ),
            "affected_files": affected,
        }
    return None


def _ap10_no_error_handling(
    stack: dict, _arch: dict, files: dict[str, str],
) -> PatternFinding | None:
    """AP10: No error handling middleware detected."""
    be = stack.get("backend")
    if be is None:
        return None

    framework = (be.get("framework") or "").lower()
    has_handler = False

    for content in files.values():
        if framework in ("fastapi", "starlette"):
            if "exception_handler" in content or "HTTPException" in content:
                has_handler = True
                break
        elif framework == "flask":
            if "errorhandler" in content:
                has_handler = True
                break
        elif framework in ("express", "fastify", "koa"):
            if "errorHandler" in content or "error" in content.lower() and "middleware" in content.lower():
                has_handler = True
                break
        elif framework == "django":
            # Django has built-in error handling
            has_handler = True
            break
        else:
            # For unknown frameworks, skip this check
            has_handler = True
            break

    if not has_handler:
        return {
            "id": "AP10",
            "name": "No error handling middleware",
            "severity": "medium",
            "category": "quality",
            "detail": (
                "No global error/exception handler detected. Unhandled "
                "errors can crash the application or leak sensitive data."
            ),
            "affected_files": None,
        }
    return None


def _ap11_secrets_in_code(
    _stack: dict, _arch: dict, files: dict[str, str],
) -> PatternFinding | None:
    """AP11: Potential secrets hardcoded in source code."""
    secret_patterns = [
        re.compile(r'(?:api_key|apikey|secret_key|password|token)\s*=\s*["\'][^"\']{8,}["\']', re.IGNORECASE),
        re.compile(r'sk-[a-zA-Z0-9]{20,}'),  # OpenAI-style keys
        re.compile(r'ghp_[a-zA-Z0-9]{30,}'),  # GitHub PATs
    ]
    affected: list[str] = []

    for fpath, content in files.items():
        # Skip config files that might legitimately reference env vars
        if fpath.endswith((".env", ".env.example", ".env.local")):
            continue
        for pattern in secret_patterns:
            if pattern.search(content):
                if fpath not in affected:
                    affected.append(fpath)

    if affected:
        return {
            "id": "AP11",
            "name": "Potential secrets in code",
            "severity": "high",
            "category": "security",
            "detail": (
                f"Potential hardcoded secrets found in {len(affected)} file(s). "
                "Use environment variables or a secrets manager."
            ),
            "affected_files": affected,
        }
    return None


def _ap12_no_readme(
    stack: dict, arch: dict, _files: dict[str, str],
) -> PatternFinding | None:
    """AP12: No README file."""
    configs = arch.get("config_sources", [])
    entry_points = arch.get("entry_points", [])
    # Check all known paths
    all_paths = configs + entry_points
    manifests = stack.get("manifest_files", [])

    # A bit of a hack — we check if README was in the dossier files
    # Actually, architecture mapper checks file tree. Let's use file_count > 0
    # meaning the repo isn't empty, but check directories for README
    dirs = arch.get("directories", {})
    # Best check: look through the original tree (not available here)
    # So this check is skipped if we can't determine — handled by LLM dossier
    return None


def _ap12_no_readme_files(
    _stack: dict, _arch: dict, files: dict[str, str],
) -> PatternFinding | None:
    """AP12: No README detected from fetched files."""
    has_readme = any(
        fpath.lower().split("/")[-1].startswith("readme")
        for fpath in files
    )
    if not has_readme and len(files) > 2:
        return {
            "id": "AP12",
            "name": "No README",
            "severity": "low",
            "category": "maintainability",
            "detail": (
                "No README file detected. A README helps new developers "
                "understand the project's purpose, setup, and usage."
            ),
            "affected_files": None,
        }
    return None


def _ap13_no_license(
    _stack: dict, arch: dict, files: dict[str, str],
) -> PatternFinding | None:
    """AP13: No LICENSE file."""
    has_license = any(
        fpath.lower().split("/")[-1].startswith("license")
        for fpath in files
    )
    configs = arch.get("config_sources", [])
    if not has_license and not any("license" in c.lower() for c in configs):
        return {
            "id": "AP13",
            "name": "No LICENSE file",
            "severity": "low",
            "category": "maintainability",
            "detail": (
                "No license file detected. Without a license, the code "
                "is technically all-rights-reserved."
            ),
            "affected_files": None,
        }
    return None


def _ap14_flat_structure(
    _stack: dict, arch: dict, _files: dict[str, str],
) -> PatternFinding | None:
    """AP14: Flat project structure with many files."""
    if arch.get("structure_type") == "flat" and arch.get("file_count", 0) > 20:
        return {
            "id": "AP14",
            "name": "Flat project structure",
            "severity": "medium",
            "category": "maintainability",
            "detail": (
                f"Project has {arch['file_count']} files in a flat structure. "
                "Organizing into directories (api/, services/, models/) "
                "improves navigability."
            ),
            "affected_files": None,
        }
    return None


def _ap15_no_dep_pinning(
    _stack: dict, _arch: dict, files: dict[str, str],
) -> PatternFinding | None:
    """AP15: Dependencies not pinned to specific versions."""
    for fpath, content in files.items():
        if fpath.split("/")[-1] != "requirements.txt":
            continue
        lines = [
            l.strip() for l in content.splitlines()
            if l.strip() and not l.strip().startswith("#") and not l.strip().startswith("-")
        ]
        if not lines:
            return None
        unpinned = [l for l in lines if not any(c in l for c in ("==", ">=", "<=", "~=", "!=", ">", "<"))]
        if unpinned and len(unpinned) / len(lines) > 0.5:
            return {
                "id": "AP15",
                "name": "Unpinned dependencies",
                "severity": "medium",
                "category": "quality",
                "detail": (
                    f"{len(unpinned)}/{len(lines)} dependencies lack version "
                    "constraints. Pinning prevents unexpected breaking changes."
                ),
                "affected_files": [fpath],
            }
    return None


# ---------------------------------------------------------------------------
# Detector registry
# ---------------------------------------------------------------------------

_DETECTORS: list = [
    _ap01_no_typescript,
    _ap02_no_tests,
    _ap03_no_ci,
    _ap04_no_docker,
    _ap05_raw_sql,
    _ap06_no_env_example_arch,
    _ap07_no_type_hints,
    _ap08_class_components,
    _ap09_callback_hell,
    _ap10_no_error_handling,
    _ap11_secrets_in_code,
    _ap12_no_readme_files,
    _ap13_no_license,
    _ap14_flat_structure,
    _ap15_no_dep_pinning,
]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def analyze_patterns(
    stack_profile: dict[str, Any],
    arch_map: dict[str, Any],
    file_contents: dict[str, str],
) -> list[PatternFinding]:
    """Run all anti-pattern detectors and return findings.

    Parameters
    ----------
    stack_profile : output from detect_stack()
    arch_map : output from map_architecture()
    file_contents : {path: content} for key files

    Returns
    -------
    List of PatternFinding dicts, sorted by severity (high > medium > low).
    """
    findings: list[PatternFinding] = []

    for detector in _DETECTORS:
        try:
            result = detector(stack_profile, arch_map, file_contents)
            if result is not None:
                findings.append(result)
        except Exception:
            # Individual detector failures shouldn't break the whole analysis
            pass

    # Sort by severity
    severity_order = {"high": 0, "medium": 1, "low": 2}
    findings.sort(key=lambda f: severity_order.get(f.get("severity", "low"), 2))

    return findings
