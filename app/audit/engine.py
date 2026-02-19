"""Audit engine -- pure analysis layer.

Runs boundary, dependency, and secrets checks against file content.
Input: files + rules -> Output: results.
No database access, no HTTP calls, no framework imports.
"""

import re
from typing import TypedDict


class CheckResult(TypedDict):
    check_code: str
    check_name: str
    result: str  # PASS | FAIL | WARN | ERROR
    detail: str | None


def run_all_checks(
    files: dict[str, str],
    boundaries: dict | None = None,
) -> list[CheckResult]:
    """Run all registered audit checks against the provided files.

    Args:
        files: mapping of file path -> file content
        boundaries: parsed boundaries.json (optional)

    Returns:
        List of CheckResult dicts.
    """
    results: list[CheckResult] = []
    results.append(check_python_syntax(files))
    results.append(check_boundary_compliance(files, boundaries))
    results.append(check_dependency_gate(files))
    results.append(check_secrets_scan(files))
    return results


def check_python_syntax(files: dict[str, str]) -> CheckResult:
    """A0 -- Syntax validity for Python files.

    Compiles each Python file to catch obvious syntax errors so audits fail
    on real parse issues instead of surfacing phantom file paths.
    """
    errors: list[str] = []

    for filepath, content in files.items():
        if not filepath.endswith(".py"):
            continue
        try:
            compile(content, filepath, "exec")
        except SyntaxError as exc:  # pragma: no cover - exercised via tests
            detail = exc.msg or "Syntax error"
            loc = f"{filepath}:{exc.lineno}"
            errors.append(f"{loc} — {detail}")

    if errors:
        return {
            "check_code": "A0",
            "check_name": "Syntax validity",
            "result": "FAIL",
            "detail": "; ".join(errors),
        }

    return {
        "check_code": "A0",
        "check_name": "Syntax validity",
        "result": "PASS",
        "detail": None,
    }


def check_boundary_compliance(
    files: dict[str, str],
    boundaries: dict | None = None,
) -> CheckResult:
    """A4 -- Boundary compliance check.

    Verifies that files in each architectural layer don't contain
    forbidden patterns as defined in boundaries.json.
    """
    if boundaries is None or "layers" not in boundaries:
        return {
            "check_code": "A4",
            "check_name": "Boundary compliance",
            "result": "PASS",
            "detail": "No boundary rules provided; skipping.",
        }

    violations: list[str] = []

    for layer in boundaries["layers"]:
        glob_pattern = layer.get("glob", "")
        forbidden = layer.get("forbidden", [])

        matching_files = _match_glob(files.keys(), glob_pattern)

        for filepath in matching_files:
            content = files[filepath]
            for rule in forbidden:
                pattern = rule.get("pattern", "")
                reason = rule.get("reason", "")
                if re.search(pattern, content):
                    violations.append(
                        f"{filepath}: contains '{pattern}' ({reason})"
                    )

    if violations:
        return {
            "check_code": "A4",
            "check_name": "Boundary compliance",
            "result": "FAIL",
            "detail": "; ".join(violations),
        }

    return {
        "check_code": "A4",
        "check_name": "Boundary compliance",
        "result": "PASS",
        "detail": None,
    }


def check_dependency_gate(files: dict[str, str]) -> CheckResult:
    """A9 -- Dependency gate check.

    Looks for imports of common problematic packages that should
    be declared in requirements.txt or package.json.
    This is a simplified version for the MVP.
    """
    # For MVP, just verify no wildcard imports
    wildcard_imports: list[str] = []
    for filepath, content in files.items():
        if filepath.endswith(".py"):
            for line in content.splitlines():
                stripped = line.strip()
                if stripped.startswith("from ") and "import *" in stripped:
                    wildcard_imports.append(f"{filepath}: {stripped}")

    if wildcard_imports:
        return {
            "check_code": "A9",
            "check_name": "Dependency gate",
            "result": "FAIL",
            "detail": "Wildcard imports found: " + "; ".join(wildcard_imports),
        }

    return {
        "check_code": "A9",
        "check_name": "Dependency gate",
        "result": "PASS",
        "detail": None,
    }


# Patterns that suggest embedded secrets
_SECRET_PATTERNS = [
    (r"(?i)(password|passwd|pwd)\s*=\s*['\"][^'\"]+['\"]", "Hardcoded password"),
    (r"(?i)(api[_-]?key|apikey)\s*=\s*['\"][^'\"]+['\"]", "Hardcoded API key"),
    (r"AKIA[0-9A-Z]{16}", "AWS access key"),
    (r"-----BEGIN (RSA |DSA |EC )?PRIVATE KEY-----", "Private key"),
    (r"ghp_[0-9a-zA-Z]{36}", "GitHub personal access token"),
    (r"gho_[0-9a-zA-Z]{36}", "GitHub OAuth token"),
]


def check_secrets_scan(files: dict[str, str]) -> CheckResult:
    """W1 -- Secrets scan.

    Looks for patterns that suggest hardcoded secrets in source files.
    """
    findings: list[str] = []

    for filepath, content in files.items():
        # Skip binary-like files, lockfiles, etc.
        if any(filepath.endswith(ext) for ext in (".lock", ".png", ".jpg", ".ico")):
            continue
        if filepath.endswith("-lock.json") or filepath.endswith(".lock.json"):
            continue
        # Skip test files — fixture data with fake keys is not a real secret
        if filepath.startswith("tests/") or filepath.startswith("test_"):
            continue

        for pattern, description in _SECRET_PATTERNS:
            matches = re.findall(pattern, content)
            if matches:
                findings.append(f"{filepath}: {description}")

    if findings:
        return {
            "check_code": "W1",
            "check_name": "Secrets scan",
            "result": "WARN",
            "detail": "; ".join(findings),
        }

    return {
        "check_code": "W1",
        "check_name": "Secrets scan",
        "result": "PASS",
        "detail": None,
    }


def _match_glob(filepaths: object, glob_pattern: str) -> list[str]:
    """Simple glob-style matching for file paths.

    Supports patterns like 'app/api/routers/*.py'.
    """
    import fnmatch

    return [fp for fp in filepaths if fnmatch.fnmatch(fp, glob_pattern)]
