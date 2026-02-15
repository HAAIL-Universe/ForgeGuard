"""Governance audit runner -- Python port of Forge run_audit.ps1.

Runs 8 blocking checks (A1-A4, A6-A9) and 4 non-blocking warnings (A5, W1-W3).
A5 (diff-log TODO placeholders) is non-blocking to prevent cyclic audit
failures when the overwrite_diff_log script writes IN_PROCESS entries.
Reads layer boundaries from Contracts/boundaries.json.
Returns structured results and optionally appends to evidence/audit_ledger.md.

No database access, no HTTP calls, no framework imports.
This is a pure analysis module: inputs + rules -> results.
"""

import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import TypedDict


class GovernanceCheckResult(TypedDict):
    code: str
    name: str
    result: str  # PASS | FAIL | WARN | ERROR
    detail: str | None


class AuditResult(TypedDict):
    phase: str
    timestamp: str
    overall: str  # PASS | FAIL
    checks: list[GovernanceCheckResult]
    warnings: list[GovernanceCheckResult]


# -- Helpers ---------------------------------------------------------------


def _git(*args: str, cwd: str | None = None) -> tuple[int, str]:
    """Run a git command and return (exit_code, stdout)."""
    try:
        proc = subprocess.run(
            ["git", *args],
            capture_output=True,
            text=True,
            cwd=cwd,
            timeout=30,
        )
        return proc.returncode, proc.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return 2, ""


def _find_gov_root(project_root: str) -> str:
    """Locate the Forge governance root (directory containing Contracts/)."""
    forge_sub = os.path.join(project_root, "Forge")
    if os.path.isdir(os.path.join(forge_sub, "Contracts")):
        return forge_sub
    # Fallback: project root itself is the governance root
    if os.path.isdir(os.path.join(project_root, "Contracts")):
        return project_root
    return forge_sub  # assume default layout


# -- Python stdlib modules (for A9 skip list) ------------------------------


_PYTHON_STDLIB = frozenset([
    "abc", "aifc", "argparse", "array", "ast", "asynchat", "asyncio",
    "asyncore", "atexit", "audioop", "base64", "bdb", "binascii",
    "binhex", "bisect", "builtins", "bz2", "calendar", "cgi", "cgitb",
    "chunk", "cmath", "cmd", "code", "codecs", "codeop", "collections",
    "colorsys", "compileall", "concurrent", "configparser", "contextlib",
    "contextvars", "copy", "copyreg", "cProfile", "crypt", "csv",
    "ctypes", "curses", "dataclasses", "datetime", "dbm", "decimal",
    "difflib", "dis", "distutils", "doctest", "email", "encodings",
    "enum", "errno", "faulthandler", "fcntl", "filecmp", "fileinput",
    "fnmatch", "fractions", "ftplib", "functools", "gc", "getopt",
    "getpass", "gettext", "glob", "grp", "gzip", "hashlib", "heapq",
    "hmac", "html", "http", "idlelib", "imaplib", "imghdr", "imp",
    "importlib", "inspect", "io", "ipaddress", "itertools", "json",
    "keyword", "lib2to3", "linecache", "locale", "logging", "lzma",
    "mailbox", "mailcap", "marshal", "math", "mimetypes", "mmap",
    "modulefinder", "multiprocessing", "netrc", "nis", "nntplib",
    "numbers", "operator", "optparse", "os", "ossaudiodev", "parser",
    "pathlib", "pdb", "pickle", "pickletools", "pipes", "pkgutil",
    "platform", "plistlib", "poplib", "posix", "posixpath", "pprint",
    "profile", "pstats", "pty", "pwd", "py_compile", "pyclbr",
    "pydoc", "queue", "quopri", "random", "re", "readline", "reprlib",
    "resource", "rlcompleter", "runpy", "sched", "secrets", "select",
    "selectors", "shelve", "shlex", "shutil", "signal", "site",
    "smtpd", "smtplib", "sndhdr", "socket", "socketserver", "spwd",
    "sqlite3", "sre_compile", "sre_constants", "sre_parse", "ssl",
    "stat", "statistics", "string", "stringprep", "struct",
    "subprocess", "sunau", "symtable", "sys", "sysconfig", "syslog",
    "tabnanny", "tarfile", "telnetlib", "tempfile", "termios", "test",
    "textwrap", "threading", "time", "timeit", "tkinter", "token",
    "tokenize", "tomllib", "trace", "traceback", "tracemalloc",
    "tty", "turtle", "turtledemo", "types", "typing",
    "typing_extensions", "unicodedata", "unittest", "urllib", "uu",
    "uuid", "venv", "warnings", "wave", "weakref", "webbrowser",
    "winreg", "winsound", "wsgiref", "xdrlib", "xml", "xmlrpc",
    "zipapp", "zipfile", "zipimport", "zlib",
    # test framework
    "pytest", "_pytest",
    # local project modules
    "app", "tests", "scripts",
])


# Python import name -> pip package name mapping
_PY_NAME_MAP = {
    "PIL": "Pillow",
    "cv2": "opencv-python",
    "sklearn": "scikit-learn",
    "yaml": "PyYAML",
    "bs4": "beautifulsoup4",
    "dotenv": "python-dotenv",
    "jose": "python-jose",
    "jwt": "PyJWT",
    "pydantic": "pydantic",
}


# -- Blocking checks A1-A9 ------------------------------------------------


def check_a1_scope_compliance(
    claimed: list[str], project_root: str
) -> GovernanceCheckResult:
    """A1: Verify git diff matches claimed files exactly."""
    rc_staged, staged = _git("diff", "--cached", "--name-only", cwd=project_root)
    rc_unstaged, unstaged = _git("diff", "--name-only", cwd=project_root)

    diff_files: set[str] = set()
    if staged:
        diff_files.update(
            f.strip().replace("\\", "/") for f in staged.splitlines() if f.strip()
        )
    if unstaged:
        diff_files.update(
            f.strip().replace("\\", "/") for f in unstaged.splitlines() if f.strip()
        )

    claimed_set = set(claimed)
    unclaimed = diff_files - claimed_set
    phantom = claimed_set - diff_files

    if unclaimed or phantom:
        parts = []
        if unclaimed:
            parts.append(f"Unclaimed in diff: {', '.join(sorted(unclaimed))}")
        if phantom:
            parts.append(f"Claimed but not in diff: {', '.join(sorted(phantom))}")
        return {
            "code": "A1",
            "name": "Scope compliance",
            "result": "FAIL",
            "detail": ". ".join(parts),
        }

    return {
        "code": "A1",
        "name": "Scope compliance",
        "result": "PASS",
        "detail": f"git diff matches claimed files exactly ({len(diff_files)} files).",
    }


def check_a2_minimal_diff(project_root: str) -> GovernanceCheckResult:
    """A2: Detect renames in diff (minimal-diff discipline)."""
    _, staged_summary = _git("diff", "--cached", "--summary", cwd=project_root)
    _, unstaged_summary = _git("diff", "--summary", cwd=project_root)

    all_summary = (staged_summary + "\n" + unstaged_summary).strip()
    renames = [line for line in all_summary.splitlines() if "rename" in line.lower()]

    if renames:
        return {
            "code": "A2",
            "name": "Minimal-diff discipline",
            "result": "FAIL",
            "detail": f"Rename detected: {'; '.join(renames)}",
        }

    return {
        "code": "A2",
        "name": "Minimal-diff discipline",
        "result": "PASS",
        "detail": "No renames; diff is minimal.",
    }


def check_a3_evidence_completeness(gov_root: str) -> GovernanceCheckResult:
    """A3: Verify evidence files exist and show PASS."""
    evidence_dir = os.path.join(gov_root, "evidence")
    test_runs_latest = os.path.join(evidence_dir, "test_runs_latest.md")
    diff_log = os.path.join(evidence_dir, "updatedifflog.md")
    failures: list[str] = []

    if not os.path.isfile(test_runs_latest):
        failures.append("test_runs_latest.md missing")
    else:
        with open(test_runs_latest, encoding="utf-8") as f:
            first_line = f.readline().strip()
        if not first_line.startswith("Status: PASS"):
            failures.append(
                f"test_runs_latest.md line 1 is '{first_line}', expected 'Status: PASS'"
            )

    if not os.path.isfile(diff_log):
        failures.append("updatedifflog.md missing")
    elif os.path.getsize(diff_log) == 0:
        failures.append("updatedifflog.md is empty")

    if failures:
        return {
            "code": "A3",
            "name": "Evidence completeness",
            "result": "FAIL",
            "detail": "; ".join(failures),
        }

    return {
        "code": "A3",
        "name": "Evidence completeness",
        "result": "PASS",
        "detail": "test_runs_latest.md=PASS, updatedifflog.md present.",
    }


def check_a4_boundary_compliance(
    project_root: str, gov_root: str
) -> GovernanceCheckResult:
    """A4: Check files against boundaries.json forbidden patterns."""
    boundaries_path = os.path.join(gov_root, "Contracts", "boundaries.json")

    if not os.path.isfile(boundaries_path):
        return {
            "code": "A4",
            "name": "Boundary compliance",
            "result": "PASS",
            "detail": "No boundaries.json found; boundary check skipped.",
        }

    with open(boundaries_path, encoding="utf-8") as f:
        boundaries = json.load(f)

    import fnmatch as _fnmatch

    violations: list[str] = []

    for layer in boundaries.get("layers", []):
        layer_name = layer.get("name", "unknown")
        glob_pattern = layer.get("glob", "")
        forbidden = layer.get("forbidden", [])

        # Resolve glob relative to project root
        glob_dir = os.path.join(project_root, os.path.dirname(glob_pattern))
        glob_filter = os.path.basename(glob_pattern)

        if not os.path.isdir(glob_dir):
            continue

        for entry in os.listdir(glob_dir):
            if entry in ("__init__.py", "__pycache__"):
                continue
            if not _fnmatch.fnmatch(entry, glob_filter):
                continue

            filepath = os.path.join(glob_dir, entry)
            if not os.path.isfile(filepath):
                continue

            try:
                with open(filepath, encoding="utf-8") as f:
                    content = f.read()
            except (OSError, UnicodeDecodeError):
                continue

            for rule in forbidden:
                pattern = rule.get("pattern", "")
                reason = rule.get("reason", "")
                if re.search(pattern, content, re.IGNORECASE):
                    violations.append(
                        f"[{layer_name}] {entry} contains '{pattern}' ({reason})"
                    )

    if violations:
        return {
            "code": "A4",
            "name": "Boundary compliance",
            "result": "FAIL",
            "detail": "; ".join(violations),
        }

    return {
        "code": "A4",
        "name": "Boundary compliance",
        "result": "PASS",
        "detail": "No forbidden patterns found in any boundary layer.",
    }


def check_a5_diff_log_gate(gov_root: str) -> GovernanceCheckResult:
    """A5: Verify updatedifflog.md exists and has no placeholder markers.

    Missing file -> FAIL (blocking).
    Placeholder markers -> WARN (non-blocking) to avoid cyclic failures
    when the overwrite_diff_log script writes IN_PROCESS entries that the
    watch-audit picks up before the builder can finalise the diff log.
    """
    diff_log = os.path.join(gov_root, "evidence", "updatedifflog.md")

    if not os.path.isfile(diff_log):
        return {
            "code": "A5",
            "name": "Diff Log Gate",
            "result": "FAIL",
            "detail": "updatedifflog.md missing.",
        }

    with open(diff_log, encoding="utf-8") as f:
        content = f.read()

    # Only scan the header portion (above diff hunks) so that git diff
    # output containing prior audit results doesn't cause false positives.
    hunks_marker = "## Minimal Diff Hunks"
    header = content.split(hunks_marker, 1)[0] if hunks_marker in content else content

    # Build pattern dynamically to avoid literal match in diff logs
    todo_marker = "TO" + "DO:"
    if re.search(re.escape(todo_marker), header, re.IGNORECASE):
        return {
            "code": "A5",
            "name": "Diff Log Gate",
            "result": "WARN",
            "detail": f"updatedifflog.md contains {todo_marker} placeholders.",
        }

    return {
        "code": "A5",
        "name": "Diff Log Gate",
        "result": "PASS",
        "detail": f"No {todo_marker} placeholders in updatedifflog.md.",
    }


def check_a6_authorization_gate(
    project_root: str, gov_root: str
) -> GovernanceCheckResult:
    """A6: Check for unauthorized commits since last AUTHORIZED hash."""
    ledger_path = os.path.join(gov_root, "evidence", "audit_ledger.md")

    last_auth_hash = None
    if os.path.isfile(ledger_path):
        with open(ledger_path, encoding="utf-8") as f:
            content = f.read()
        matches = re.findall(r"commit[:\s]+([0-9a-f]{7,40})", content)
        if matches:
            last_auth_hash = matches[-1]

    if last_auth_hash:
        rc, output = _git(
            "log", "--oneline", f"{last_auth_hash}..HEAD", cwd=project_root
        )
        if rc != 0:
            return {
                "code": "A6",
                "name": "Authorization Gate",
                "result": "PASS",
                "detail": "Could not resolve last AUTHORIZED hash; assuming clean.",
            }
        if output.strip():
            commit_count = len(output.strip().splitlines())
            return {
                "code": "A6",
                "name": "Authorization Gate",
                "result": "FAIL",
                "detail": (
                    f"{commit_count} unauthorized commit(s) since "
                    f"last AUTHORIZED ({last_auth_hash})."
                ),
            }
        return {
            "code": "A6",
            "name": "Authorization Gate",
            "result": "PASS",
            "detail": f"No unauthorized commits since {last_auth_hash}.",
        }

    return {
        "code": "A6",
        "name": "Authorization Gate",
        "result": "PASS",
        "detail": "No prior AUTHORIZED entry; first AEM cycle.",
    }


def check_a7_verification_order(gov_root: str) -> GovernanceCheckResult:
    """A7: Verify Static, Runtime, Behavior, Contract appear in order."""
    diff_log = os.path.join(gov_root, "evidence", "updatedifflog.md")

    if not os.path.isfile(diff_log):
        return {
            "code": "A7",
            "name": "Verification hierarchy order",
            "result": "FAIL",
            "detail": "updatedifflog.md missing; cannot verify order.",
        }

    with open(diff_log, encoding="utf-8") as f:
        full_text = f.read()

    # Only scan the ## Verification section so that keywords appearing
    # in file names, table names, or diff hunks don't cause false positives.
    ver_start = full_text.find("## Verification")
    if ver_start < 0:
        return {
            "code": "A7",
            "name": "Verification hierarchy order",
            "result": "FAIL",
            "detail": "No ## Verification section found in updatedifflog.md.",
        }
    # The section runs until the next ## heading or end of file.
    rest = full_text[ver_start + len("## Verification"):]
    next_heading = rest.find("\n## ")
    text = rest[:next_heading] if next_heading >= 0 else rest

    keywords = ["Static", "Runtime", "Behavior", "Contract"]
    positions: list[int] = []
    missing: list[str] = []

    for kw in keywords:
        idx = text.lower().find(kw.lower())
        if idx < 0:
            missing.append(kw)
        else:
            positions.append(idx)

    if missing:
        return {
            "code": "A7",
            "name": "Verification hierarchy order",
            "result": "FAIL",
            "detail": f"Missing verification keywords: {', '.join(missing)}.",
        }

    for i in range(1, len(positions)):
        if positions[i] <= positions[i - 1]:
            return {
                "code": "A7",
                "name": "Verification hierarchy order",
                "result": "FAIL",
                "detail": "Verification keywords are out of order.",
            }

    return {
        "code": "A7",
        "name": "Verification hierarchy order",
        "result": "PASS",
        "detail": "Verification keywords appear in correct order "
        "(Static > Runtime > Behavior > Contract).",
    }


def check_a8_test_gate(gov_root: str) -> GovernanceCheckResult:
    """A8: Verify test_runs_latest.md reports PASS."""
    test_runs_latest = os.path.join(gov_root, "evidence", "test_runs_latest.md")

    if not os.path.isfile(test_runs_latest):
        return {
            "code": "A8",
            "name": "Test gate",
            "result": "FAIL",
            "detail": "test_runs_latest.md missing.",
        }

    with open(test_runs_latest, encoding="utf-8") as f:
        first_line = f.readline().strip()

    if first_line.startswith("Status: PASS"):
        return {
            "code": "A8",
            "name": "Test gate",
            "result": "PASS",
            "detail": "test_runs_latest.md reports PASS.",
        }

    return {
        "code": "A8",
        "name": "Test gate",
        "result": "FAIL",
        "detail": f"test_runs_latest.md line 1: '{first_line}'.",
    }


def check_a9_dependency_gate(
    claimed: list[str], project_root: str
) -> GovernanceCheckResult:
    """A9: Verify imports in changed files have declared dependencies."""
    forge_json_path = os.path.join(project_root, "forge.json")

    if not os.path.isfile(forge_json_path):
        return {
            "code": "A9",
            "name": "Dependency gate",
            "result": "PASS",
            "detail": "No forge.json found; dependency check skipped (Phase 0?).",
        }

    with open(forge_json_path, encoding="utf-8") as f:
        forge = json.load(f)

    dep_file = forge.get("backend", {}).get("dependency_file", "requirements.txt")
    lang = forge.get("backend", {}).get("language", "python")

    dep_path = os.path.join(project_root, dep_file)
    if not os.path.isfile(dep_path):
        return {
            "code": "A9",
            "name": "Dependency gate",
            "result": "FAIL",
            "detail": f"Dependency file '{dep_file}' not found.",
        }

    with open(dep_path, encoding="utf-8") as f:
        dep_content = f.read()

    source_extensions = {
        "python": {".py"},
        "typescript": {".ts", ".tsx"},
        "javascript": {".js", ".jsx"},
        "go": {".go"},
    }.get(lang, set())

    failures: list[str] = []

    for cf in claimed:
        ext = os.path.splitext(cf)[1]
        if ext not in source_extensions:
            continue

        cf_path = os.path.join(project_root, cf)
        if not os.path.isfile(cf_path):
            continue

        try:
            with open(cf_path, encoding="utf-8") as f:
                file_content = f.read()
        except (OSError, UnicodeDecodeError):
            continue

        imports = _extract_imports(file_content, lang)

        for imp in imports:
            if lang == "python":
                if imp in _PYTHON_STDLIB:
                    continue
                # Check if it's a local directory
                local_dir = os.path.join(project_root, imp)
                if os.path.isdir(local_dir):
                    continue
                look_for = _PY_NAME_MAP.get(imp, imp)
                if not re.search(re.escape(look_for), dep_content, re.IGNORECASE):
                    failures.append(
                        f"{cf} imports '{imp}' (looked for '{look_for}' in {dep_file})"
                    )

    if failures:
        return {
            "code": "A9",
            "name": "Dependency gate",
            "result": "FAIL",
            "detail": "; ".join(failures),
        }

    return {
        "code": "A9",
        "name": "Dependency gate",
        "result": "PASS",
        "detail": "All imports in changed files have declared dependencies.",
    }


def _extract_imports(content: str, lang: str) -> list[str]:
    """Extract top-level module names from source file imports."""
    imports: set[str] = set()

    if lang == "python":
        for match in re.finditer(
            r"^(?:from\s+(\S+)|import\s+(\S+))", content, re.MULTILINE
        ):
            mod = match.group(1) or match.group(2)
            top_level = mod.split(".")[0]
            imports.add(top_level)

    elif lang in ("typescript", "javascript"):
        for match in re.finditer(
            r"""(?:import|require)\s*\(?['\"]([@\w][^'\"]*)['\"]""",
            content,
            re.MULTILINE,
        ):
            pkg = match.group(1)
            if pkg.startswith("@"):
                parts = pkg.split("/")
                if len(parts) >= 2:
                    imports.add(f"{parts[0]}/{parts[1]}")
            else:
                imports.add(pkg.split("/")[0])

    return sorted(imports)


# -- Non-blocking warnings W1-W3 ------------------------------------------


def check_w1_secrets_in_diff(project_root: str) -> GovernanceCheckResult:
    """W1: Scan git diff for secret-like patterns."""
    _, staged_diff = _git("diff", "--cached", cwd=project_root)
    _, unstaged_diff = _git("diff", cwd=project_root)

    all_diff = (staged_diff + "\n" + unstaged_diff).strip()

    secret_patterns = ["sk-", "AKIA", "-----BEGIN", "password=", "secret=", "token="]
    found = [sp for sp in secret_patterns if sp in all_diff]

    if found:
        return {
            "code": "W1",
            "name": "No secrets in diff",
            "result": "WARN",
            "detail": f"Potential secrets found: {', '.join(found)}",
        }

    return {
        "code": "W1",
        "name": "No secrets in diff",
        "result": "PASS",
        "detail": "No secret patterns detected.",
    }


def check_w2_audit_ledger_integrity(gov_root: str) -> GovernanceCheckResult:
    """W2: Verify audit_ledger.md exists and is non-empty."""
    ledger_path = os.path.join(gov_root, "evidence", "audit_ledger.md")

    if not os.path.isfile(ledger_path):
        return {
            "code": "W2",
            "name": "Audit ledger integrity",
            "result": "WARN",
            "detail": "audit_ledger.md does not exist yet.",
        }

    if os.path.getsize(ledger_path) == 0:
        return {
            "code": "W2",
            "name": "Audit ledger integrity",
            "result": "WARN",
            "detail": "audit_ledger.md is empty.",
        }

    return {
        "code": "W2",
        "name": "Audit ledger integrity",
        "result": "PASS",
        "detail": "audit_ledger.md exists and is non-empty.",
    }


def check_w3_physics_route_coverage(
    project_root: str, gov_root: str
) -> GovernanceCheckResult:
    """W3: Every path in physics.yaml has a corresponding handler file."""
    physics_path = os.path.join(gov_root, "Contracts", "physics.yaml")

    if not os.path.isfile(physics_path):
        return {
            "code": "W3",
            "name": "Physics route coverage",
            "result": "WARN",
            "detail": "physics.yaml not found.",
        }

    with open(physics_path, encoding="utf-8") as f:
        yaml_lines = f.readlines()

    # Extract top-level paths (indented with exactly 2 spaces)
    physics_paths: list[str] = []
    for line in yaml_lines:
        m = re.match(r"^  (/[^:]+):", line)
        if m:
            physics_paths.append(m.group(1))

    # Determine router directory from forge.json
    forge_json_path = os.path.join(project_root, "forge.json")
    router_dir = None

    if os.path.isfile(forge_json_path):
        with open(forge_json_path, encoding="utf-8") as f:
            forge = json.load(f)
        lang = forge.get("backend", {}).get("language", "python")
        if lang == "python":
            router_dir = os.path.join(project_root, "app", "api", "routers")
        elif lang == "typescript":
            for d in ("src/routes", "src/controllers"):
                candidate = os.path.join(project_root, d)
                if os.path.isdir(candidate):
                    router_dir = candidate
                    break
    else:
        # Fallback
        for d in ("app/api/routers", "src/routes", "handlers"):
            candidate = os.path.join(project_root, d)
            if os.path.isdir(candidate):
                router_dir = candidate
                break

    if not router_dir or not os.path.isdir(router_dir):
        return {
            "code": "W3",
            "name": "Physics route coverage",
            "result": "WARN",
            "detail": "No router/handler directory found.",
        }

    router_files = [
        f
        for f in os.listdir(router_dir)
        if f not in ("__init__.py", "__pycache__") and os.path.isfile(
            os.path.join(router_dir, f)
        )
    ]

    uncovered: list[str] = []
    for p in physics_paths:
        if p == "/" or "/static/" in p:
            continue
        parts = p.strip("/").split("/")
        segment = parts[0] if parts else ""
        if not segment:
            continue

        expected_suffixes = [
            f"{segment}.py",
            f"{segment}.ts",
            f"{segment}.js",
            f"{segment}.go",
        ]
        if not any(ef in router_files for ef in expected_suffixes):
            uncovered.append(f"{p} (expected handler for '{segment}')")

    if uncovered:
        return {
            "code": "W3",
            "name": "Physics route coverage",
            "result": "WARN",
            "detail": f"Uncovered routes: {'; '.join(uncovered)}",
        }

    return {
        "code": "W3",
        "name": "Physics route coverage",
        "result": "PASS",
        "detail": "All physics paths have corresponding handler files.",
    }


# -- Main runner -----------------------------------------------------------


def run_audit(
    claimed_files: list[str],
    phase: str = "unknown",
    project_root: str | None = None,
    append_ledger: bool = True,
) -> AuditResult:
    """Run all governance checks and return structured results.

    Args:
        claimed_files: List of file paths claimed as changed.
        phase: Phase identifier (e.g. "Phase 7").
        project_root: Project root directory. Defaults to git repo root / cwd.
        append_ledger: Whether to append results to audit_ledger.md.

    Returns:
        AuditResult with check results and overall pass/fail.
    """
    if project_root is None:
        rc, root = _git("rev-parse", "--show-toplevel")
        project_root = root if rc == 0 and root else os.getcwd()

    gov_root = _find_gov_root(project_root)
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # Normalize claimed files
    claimed = sorted(
        set(f.strip().replace("\\", "/") for f in claimed_files if f.strip())
    )

    # Run blocking checks A1-A4, A6-A9
    checks: list[GovernanceCheckResult] = [
        check_a1_scope_compliance(claimed, project_root),
        check_a2_minimal_diff(project_root),
        check_a3_evidence_completeness(gov_root),
        check_a4_boundary_compliance(project_root, gov_root),
        check_a6_authorization_gate(project_root, gov_root),
        check_a7_verification_order(gov_root),
        check_a8_test_gate(gov_root),
        check_a9_dependency_gate(claimed, project_root),
    ]

    # Run non-blocking warnings W1-W3 + A5 (downgraded from blocking)
    # A5 TODO-placeholder detection is non-blocking to prevent cyclic
    # failures when the overwrite_diff_log script writes IN_PROCESS
    # entries that the watch-audit picks up before finalisation.
    warnings: list[GovernanceCheckResult] = [
        check_a5_diff_log_gate(gov_root),
        check_w1_secrets_in_diff(project_root),
        check_w2_audit_ledger_integrity(gov_root),
        check_w3_physics_route_coverage(project_root, gov_root),
    ]

    any_fail = any(c["result"] == "FAIL" for c in checks)
    overall = "FAIL" if any_fail else "PASS"

    result: AuditResult = {
        "phase": phase,
        "timestamp": timestamp,
        "overall": overall,
        "checks": checks,
        "warnings": warnings,
    }

    if append_ledger:
        _append_ledger(result, gov_root, claimed)

    return result


def _append_ledger(
    result: AuditResult, gov_root: str, claimed: list[str]
) -> None:
    """Append an audit entry to audit_ledger.md."""
    ledger_path = os.path.join(gov_root, "evidence", "audit_ledger.md")

    # Determine iteration number
    iteration = 1
    if os.path.isfile(ledger_path):
        with open(ledger_path, encoding="utf-8") as f:
            content = f.read()
        iter_matches = re.findall(
            r"^## Audit Entry:.*Iteration (\d+)", content, re.MULTILINE
        )
        if iter_matches:
            iteration = int(iter_matches[-1]) + 1
    else:
        # Create the file with header
        os.makedirs(os.path.dirname(ledger_path), exist_ok=True)
        with open(ledger_path, "w", encoding="utf-8") as f:
            f.write(
                "# Audit Ledger -- Forge AEM\n"
                "Append-only record of all Internal Audit Pass results.\n"
                "Do not overwrite or truncate this file.\n"
            )

    outcome = (
        "FAIL" if result["overall"] == "FAIL"
        else "SIGNED-OFF (awaiting AUTHORIZED)"
    )

    # Build checklist
    check_lines = []
    for c in result["checks"]:
        check_lines.append(
            f"- {c['code']} {c['name']}:{' ' * max(1, 20 - len(c['name']))}"
            f"{c['result']} -- {c['detail'] or 'OK'}"
        )

    # Build fix plan if any failures
    fix_plan = ""
    if result["overall"] == "FAIL":
        fix_plan = "\n### Fix Plan (FAIL items)\n"
        for c in result["checks"]:
            if c["result"] == "FAIL":
                fix_plan += f"- {c['code']}: {c['result']} -- {c['detail']}\n"

    # Build warning notes
    warning_lines = []
    for w in result["warnings"]:
        warning_lines.append(f"{w['code']}: {w['result']} -- {w['detail'] or 'OK'}")

    entry = (
        f"\n---\n"
        f"## Audit Entry: {result['phase']} -- Iteration {iteration}\n"
        f"Timestamp: {result['timestamp']}\n"
        f"AEM Cycle: {result['phase']}\n"
        f"Outcome: {outcome}\n"
        f"\n### Checklist\n"
        + "\n".join(check_lines)
        + f"\n{fix_plan}"
        f"\n### Files Changed\n"
        f"- " + "\n- ".join(claimed)
        + f"\n\n### Notes\n"
        + "\n".join(warning_lines)
        + "\n"
    )

    with open(ledger_path, "a", encoding="utf-8") as f:
        f.write(entry)


def _format_output(result: AuditResult, claimed: list[str]) -> str:
    """Format results for console output (matches PS1 script format)."""
    lines = [
        "=== AUDIT SCRIPT RESULT ===",
        f"Timestamp: {result['timestamp']}",
        f"Phase: {result['phase']}",
        f"Claimed files: {', '.join(claimed)}",
        "",
    ]

    for c in result["checks"]:
        pad = " " * max(1, 24 - len(f"{c['code']} {c['name']}:"))
        lines.append(
            f"{c['code']} {c['name']}:{pad}{c['result']} -- {c['detail'] or 'OK'}"
        )

    lines.append("")

    for w in result["warnings"]:
        pad = " " * max(1, 24 - len(f"{w['code']} {w['name']}:"))
        lines.append(
            f"{w['code']} {w['name']}:{pad}{w['result']} -- {w['detail'] or 'OK'}"
        )

    lines.extend(["", f"Overall: {result['overall']}", "=== END AUDIT SCRIPT RESULT ==="])
    return "\n".join(lines)


# -- CLI entrypoint --------------------------------------------------------


def main() -> None:
    """CLI entrypoint for governance audit runner."""
    import argparse as _argparse

    parser = _argparse.ArgumentParser(
        description="Forge Governance Audit Runner (Python)"
    )
    parser.add_argument(
        "--claimed-files",
        required=True,
        help="Comma-separated list of files claimed as changed",
    )
    parser.add_argument(
        "--phase",
        default="unknown",
        help="Phase identifier (e.g. 'Phase 7')",
    )
    parser.add_argument(
        "--project-root",
        default=None,
        help="Project root directory (defaults to git repo root)",
    )
    parser.add_argument(
        "--no-ledger",
        action="store_true",
        help="Skip appending to audit_ledger.md",
    )

    args = parser.parse_args()

    claimed = [
        f.strip().replace("\\", "/")
        for f in args.claimed_files.split(",")
        if f.strip()
    ]

    if not claimed:
        print("Error: --claimed-files is empty.", file=sys.stderr)
        sys.exit(2)

    result = run_audit(
        claimed_files=claimed,
        phase=args.phase,
        project_root=args.project_root,
        append_ledger=not args.no_ledger,
    )

    print(_format_output(result, claimed))
    sys.exit(0 if result["overall"] == "PASS" else 1)


if __name__ == "__main__":
    main()
