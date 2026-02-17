"""Scout deterministic metrics engine & smell detector.

Pure-function module that computes hard quality numbers from deep scan
artifacts — no LLM, no IO, fully reproducible.  Feeds into the dossier
prompt so the LLM narrates measured facts instead of guessing.
"""

from __future__ import annotations

import re
from typing import Any

# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

SmellReport = dict[str, Any]
# { "id": str, "name": str, "severity": "LOW"|"MEDIUM"|"HIGH",
#   "detail": str, "files": list[str], "count": int }

RepoMetrics = dict[str, Any]
# { "scores": { dim: {"score": 0-20, "details": [...]} },
#   "computed_score": 0-100,
#   "file_stats": { ... },
#   "smells": list[SmellReport] }

# ---------------------------------------------------------------------------
# Source file extensions
# ---------------------------------------------------------------------------

_SOURCE_EXTS = {
    ".py", ".ts", ".tsx", ".js", ".jsx", ".rs", ".go", ".java",
    ".rb", ".php", ".cs", ".cpp", ".c", ".h", ".hpp", ".swift",
    ".kt", ".scala", ".ex", ".exs", ".erl", ".hs", ".ml",
}

_TEST_PATTERNS = [
    re.compile(r"(^|/)test_[^/]+$", re.IGNORECASE),
    re.compile(r"(^|/)[^/]+_test\.[^/]+$", re.IGNORECASE),
    re.compile(r"(^|/)[^/]+\.test\.[^/]+$", re.IGNORECASE),
    re.compile(r"(^|/)[^/]+\.spec\.[^/]+$", re.IGNORECASE),
    re.compile(r"(^|/)__tests__/", re.IGNORECASE),
    re.compile(r"(^|/)tests?/", re.IGNORECASE),
]

_DOC_PATTERNS = [
    re.compile(r"(^|/)readme", re.IGNORECASE),
    re.compile(r"(^|/)docs?/", re.IGNORECASE),
    re.compile(r"(^|/)documentation/", re.IGNORECASE),
    re.compile(r"(^|/)changelog", re.IGNORECASE),
    re.compile(r"(^|/)contributing", re.IGNORECASE),
    re.compile(r"\.(md|rst|txt|adoc)$", re.IGNORECASE),
]

# ---------------------------------------------------------------------------
# Secrets regexes
# ---------------------------------------------------------------------------

_SECRET_PATTERNS = [
    # AWS access key
    re.compile(r"AKIA[0-9A-Z]{16}", re.IGNORECASE),
    # Generic API keys / tokens
    re.compile(r"""(?:api[_-]?key|api[_-]?secret|secret[_-]?key|access[_-]?token)\s*[:=]\s*['"][A-Za-z0-9/+=]{16,}['"]""", re.IGNORECASE),
    # OpenAI
    re.compile(r"sk-[A-Za-z0-9]{20,}"),
    # GitHub PAT
    re.compile(r"ghp_[A-Za-z0-9]{36,}"),
    # Anthropic
    re.compile(r"sk-ant-[A-Za-z0-9_-]{20,}"),
    # Slack
    re.compile(r"xox[bprs]-[A-Za-z0-9\-]+"),
    # Password in assignment
    re.compile(r"""(?:password|passwd|pwd)\s*[:=]\s*['"][^'"]{4,}['"]""", re.IGNORECASE),
    # Private key header
    re.compile(r"-----BEGIN (?:RSA |EC |DSA )?PRIVATE KEY-----"),
]

# SQL injection patterns (f-string / format string SQL)
_RAW_SQL_PATTERNS = [
    re.compile(r"""f['"].*\b(?:SELECT|INSERT|UPDATE|DELETE|DROP)\b.*\{""", re.IGNORECASE),
    re.compile(r"""['"].*\b(?:SELECT|INSERT|UPDATE|DELETE|DROP)\b.*['"]\.format\(""", re.IGNORECASE),
    re.compile(r"""%s.*\b(?:SELECT|INSERT|UPDATE|DELETE|DROP)\b""", re.IGNORECASE),
]

# eval/exec
_EVAL_EXEC_PATTERN = re.compile(r"\b(?:eval|exec)\s*\(", re.IGNORECASE)


# ---------------------------------------------------------------------------
# File classification helpers
# ---------------------------------------------------------------------------

def _ext(path: str) -> str:
    dot = path.rfind(".")
    return path[dot:].lower() if dot >= 0 else ""


def _is_source(path: str) -> bool:
    return _ext(path) in _SOURCE_EXTS


def _is_test(path: str) -> bool:
    return any(p.search(path) for p in _TEST_PATTERNS)


def _is_doc(path: str) -> bool:
    return any(p.search(path) for p in _DOC_PATTERNS)


def _depth(path: str) -> int:
    return path.count("/")


# ---------------------------------------------------------------------------
# 39.2 — Smell Detector
# ---------------------------------------------------------------------------

def detect_smells(
    tree_paths: list[str],
    file_contents: dict[str, str],
) -> list[SmellReport]:
    """Scan tree + file contents for concrete, verifiable problems.

    Returns a list of SmellReport dicts sorted by severity (HIGH first).
    """
    smells: list[SmellReport] = []
    path_set = {p.lower() for p in tree_paths}
    root_files = {p.lower() for p in tree_paths if "/" not in p}

    # ── .env committed ───────────────────────────────────────────
    env_files = [p for p in tree_paths if p.lower().endswith(".env") or "/.env" in p.lower()]
    if env_files:
        smells.append({
            "id": "env_committed",
            "name": ".env file committed",
            "severity": "HIGH",
            "detail": "Environment files containing secrets should not be committed to version control.",
            "files": env_files,
            "count": len(env_files),
        })

    # ── No .gitignore ────────────────────────────────────────────
    if ".gitignore" not in root_files:
        smells.append({
            "id": "no_gitignore",
            "name": "Missing .gitignore",
            "severity": "MEDIUM",
            "detail": "No .gitignore found at repository root. Build artifacts and secrets may be tracked.",
            "files": [],
            "count": 0,
        })

    # ── No license ───────────────────────────────────────────────
    license_names = {"license", "licence", "license.md", "licence.md", "license.txt", "licence.txt"}
    if not any(f in license_names for f in root_files):
        smells.append({
            "id": "no_license",
            "name": "Missing LICENSE",
            "severity": "LOW",
            "detail": "No license file found. The project has no explicit license terms.",
            "files": [],
            "count": 0,
        })

    # ── Missing README ───────────────────────────────────────────
    readme_names = {"readme.md", "readme.rst", "readme.txt", "readme"}
    if not any(f in readme_names for f in root_files):
        smells.append({
            "id": "missing_readme",
            "name": "Missing README",
            "severity": "MEDIUM",
            "detail": "No README file found at repository root.",
            "files": [],
            "count": 0,
        })

    # ── No test files ────────────────────────────────────────────
    source_files = [p for p in tree_paths if _is_source(p)]
    test_files = [p for p in source_files if _is_test(p)]
    if source_files and not test_files:
        smells.append({
            "id": "no_tests",
            "name": "No test files",
            "severity": "HIGH",
            "detail": f"{len(source_files)} source files with zero test files detected.",
            "files": [],
            "count": 0,
        })

    # ── Unpinned dependencies ────────────────────────────────────
    unpinned = _check_unpinned_deps(file_contents)
    if unpinned:
        smells.append(unpinned)

    # ── Large files (>500 lines) ─────────────────────────────────
    large = []
    for fpath, content in file_contents.items():
        if _is_source(fpath) and not _is_test(fpath):
            lines = content.count("\n") + 1
            if lines > 500:
                large.append(f"{fpath} ({lines} lines)")
    if large:
        smells.append({
            "id": "large_files",
            "name": "Complex files (>500 lines)",
            "severity": "MEDIUM",
            "detail": f"{len(large)} source file(s) exceed 500 lines, indicating high complexity.",
            "files": [f.split(" (")[0] for f in large],
            "count": len(large),
        })

    # ── Secrets in source ────────────────────────────────────────
    secret_hits: list[str] = []
    for fpath, content in file_contents.items():
        if _is_source(fpath) or fpath.lower().endswith((".env", ".yml", ".yaml", ".json", ".toml")):
            for pat in _SECRET_PATTERNS:
                if pat.search(content):
                    if fpath not in secret_hits:
                        secret_hits.append(fpath)
    if secret_hits:
        smells.append({
            "id": "secrets_in_source",
            "name": "Potential secrets in source",
            "severity": "HIGH",
            "detail": f"Potential secrets or credentials detected in {len(secret_hits)} file(s).",
            "files": secret_hits,
            "count": len(secret_hits),
        })

    # ── Raw SQL construction ─────────────────────────────────────
    sql_hits: list[str] = []
    for fpath, content in file_contents.items():
        if _is_source(fpath):
            for pat in _RAW_SQL_PATTERNS:
                if pat.search(content):
                    if fpath not in sql_hits:
                        sql_hits.append(fpath)
    if sql_hits:
        smells.append({
            "id": "raw_sql",
            "name": "Raw SQL construction",
            "severity": "HIGH",
            "detail": f"Dynamic SQL string building detected in {len(sql_hits)} file(s). Risk of SQL injection.",
            "files": sql_hits,
            "count": len(sql_hits),
        })

    # ── eval() / exec() usage ────────────────────────────────────
    eval_hits: list[str] = []
    for fpath, content in file_contents.items():
        if _is_source(fpath):
            if _EVAL_EXEC_PATTERN.search(content):
                eval_hits.append(fpath)
    if eval_hits:
        smells.append({
            "id": "eval_exec",
            "name": "eval()/exec() usage",
            "severity": "HIGH",
            "detail": f"Dynamic code execution via eval/exec found in {len(eval_hits)} file(s).",
            "files": eval_hits,
            "count": len(eval_hits),
        })

    # ── TODO/FIXME density ───────────────────────────────────────
    todo_pattern = re.compile(r"\b(?:TODO|FIXME|HACK|XXX)\b", re.IGNORECASE)
    total_lines = 0
    todo_count = 0
    todo_files: list[str] = []
    for fpath, content in file_contents.items():
        if _is_source(fpath):
            lines = content.count("\n") + 1
            total_lines += lines
            matches = len(todo_pattern.findall(content))
            if matches > 0:
                todo_count += matches
                todo_files.append(fpath)
    if total_lines > 0:
        density = (todo_count / total_lines) * 1000
        if density > 10:
            smells.append({
                "id": "todo_fixme_density",
                "name": "High TODO/FIXME density",
                "severity": "LOW",
                "detail": f"{todo_count} TODO/FIXME/HACK markers across {len(todo_files)} files ({density:.1f} per 1K lines).",
                "files": todo_files[:10],
                "count": todo_count,
            })

    # Sort: HIGH first, then MEDIUM, then LOW
    severity_order = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
    smells.sort(key=lambda s: severity_order.get(s["severity"], 9))

    return smells


def _check_unpinned_deps(file_contents: dict[str, str]) -> SmellReport | None:
    """Check for unpinned dependencies in requirements.txt or package.json."""
    unpinned_files: list[str] = []
    unpinned_count = 0

    for fpath, content in file_contents.items():
        fname = fpath.split("/")[-1].lower()

        if fname == "requirements.txt":
            for line in content.splitlines():
                line = line.strip()
                if not line or line.startswith("#") or line.startswith("-"):
                    continue
                # Pinned has ==, >=, ~=, etc.
                if not re.search(r"[=<>~!]", line):
                    unpinned_count += 1
            if unpinned_count > 0:
                unpinned_files.append(fpath)

        elif fname == "package.json":
            # Check for * or latest in dependencies
            if '"*"' in content or '"latest"' in content:
                unpinned_count += content.count('"*"') + content.count('"latest"')
                unpinned_files.append(fpath)

    if unpinned_count > 0:
        return {
            "id": "unpinned_deps",
            "name": "Unpinned dependencies",
            "severity": "MEDIUM",
            "detail": f"{unpinned_count} dependencies without version constraints.",
            "files": unpinned_files,
            "count": unpinned_count,
        }
    return None


# ---------------------------------------------------------------------------
# 39.1 — Metrics Engine
# ---------------------------------------------------------------------------

def compute_repo_metrics(
    tree_paths: list[str],
    file_contents: dict[str, str],
    stack_profile: dict | None = None,
    architecture: dict | None = None,
    checks: list[dict] | None = None,
) -> RepoMetrics:
    """Compute deterministic quality metrics from deep scan artifacts.

    Parameters
    ----------
    tree_paths : Full list of file paths in the repo tree.
    file_contents : Dict of filepath → content for fetched key files.
    stack_profile : Stack detection output (optional).
    architecture : Architecture map output (optional).
    checks : Governance check results (optional).

    Returns
    -------
    RepoMetrics dict with per-dimension scores, computed_score, file_stats,
    and detected smells.
    """
    stack_profile = stack_profile or {}
    architecture = architecture or {}
    checks = checks or []

    # Classify files
    source_files = [p for p in tree_paths if _is_source(p)]
    test_files_list = [p for p in source_files if _is_test(p)]
    non_test_source = [p for p in source_files if not _is_test(p)]
    doc_files = [p for p in tree_paths if _is_doc(p)]

    # File stats from contents we have
    line_counts: list[int] = []
    for fpath, content in file_contents.items():
        if _is_source(fpath) and not _is_test(fpath):
            line_counts.append(content.count("\n") + 1)

    file_stats = {
        "total_files": len(tree_paths),
        "source_files": len(source_files),
        "test_files": len(test_files_list),
        "non_test_source": len(non_test_source),
        "doc_files": len(doc_files),
        "max_file_lines": max(line_counts) if line_counts else 0,
        "avg_file_lines": round(sum(line_counts) / len(line_counts), 1) if line_counts else 0,
    }

    # Compute smells
    smells = detect_smells(tree_paths, file_contents)

    # Score each dimension
    scores = {
        "test_file_ratio": _score_test_ratio(source_files, test_files_list),
        "doc_coverage": _score_doc_coverage(tree_paths, file_contents),
        "dependency_health": _score_dependency_health(tree_paths, file_contents, stack_profile),
        "code_organization": _score_code_organization(tree_paths, file_contents, architecture),
        "security_posture": _score_security_posture(smells, checks),
    }

    computed_score = sum(d["score"] for d in scores.values())

    return {
        "scores": scores,
        "computed_score": computed_score,
        "file_stats": file_stats,
        "smells": smells,
    }


# ---------------------------------------------------------------------------
# Dimension scorers (each returns {"score": 0-20, "details": [...]})
# ---------------------------------------------------------------------------

def _score_test_ratio(
    source_files: list[str],
    test_files: list[str],
) -> dict:
    """Score 0-20 based on test file coverage."""
    details: list[str] = []

    if not source_files:
        details.append("No source files found")
        return {"score": 0, "details": details}

    total = len(source_files)
    tests = len(test_files)
    ratio = tests / total if total > 0 else 0.0

    details.append(f"{tests} test files / {total} source files (ratio: {ratio:.2f})")

    # Score brackets
    if ratio >= 0.3:
        score = 20
        details.append("Excellent test coverage ratio")
    elif ratio >= 0.2:
        score = 16
        details.append("Good test coverage ratio")
    elif ratio >= 0.1:
        score = 12
        details.append("Moderate test coverage ratio")
    elif ratio > 0:
        score = 6
        details.append("Low test coverage ratio")
    else:
        score = 0
        details.append("No test files found")

    return {"score": score, "details": details}


def _score_doc_coverage(
    tree_paths: list[str],
    file_contents: dict[str, str],
) -> dict:
    """Score 0-20 based on documentation presence and quality."""
    details: list[str] = []
    score = 0
    root_files = {p.lower() for p in tree_paths if "/" not in p}

    # README exists (+6)
    readme_names = {"readme.md", "readme.rst", "readme.txt", "readme"}
    has_readme = any(f in readme_names for f in root_files)
    if has_readme:
        score += 6
        details.append("README found at root")

        # README size bonus (+4 for substantial README)
        for fpath, content in file_contents.items():
            if fpath.lower().split("/")[-1] in readme_names:
                chars = len(content)
                if chars > 2000:
                    score += 4
                    details.append(f"README is substantial ({chars:,} chars)")
                elif chars > 500:
                    score += 2
                    details.append(f"README is moderate ({chars:,} chars)")
                else:
                    details.append(f"README is minimal ({chars:,} chars)")
                break
    else:
        details.append("No README found")

    # docs/ directory (+4)
    has_docs_dir = any(p.lower().startswith("docs/") or "/docs/" in p.lower() for p in tree_paths)
    if has_docs_dir:
        score += 4
        details.append("docs/ directory found")

    # CHANGELOG / CONTRIBUTING (+3)
    extras = {"changelog", "changelog.md", "contributing", "contributing.md"}
    found_extras = [f for f in root_files if f in extras]
    if found_extras:
        score += 3
        details.append(f"Extra docs: {', '.join(found_extras)}")

    # Docstrings in entry point files (+3)
    docstring_count = 0
    for fpath, content in file_contents.items():
        fname = fpath.split("/")[-1].lower()
        if fname in ("main.py", "app.py", "server.py", "__init__.py", "index.ts"):
            if '"""' in content or "'''" in content or "/**" in content:
                docstring_count += 1
    if docstring_count > 0:
        score += 3
        details.append(f"Docstrings found in {docstring_count} entry point(s)")

    return {"score": min(20, score), "details": details}


def _score_dependency_health(
    tree_paths: list[str],
    file_contents: dict[str, str],
    stack_profile: dict,
) -> dict:
    """Score 0-20 based on dependency management."""
    details: list[str] = []
    score = 10  # Start neutral

    root_files = {p.lower() for p in tree_paths if "/" not in p}

    # Manifest file exists (+4)
    manifests = {"requirements.txt", "pyproject.toml", "package.json", "go.mod",
                 "cargo.toml", "gemfile", "build.gradle", "pom.xml"}
    found_manifests = [f for f in root_files if f in manifests]
    if found_manifests:
        score += 4
        details.append(f"Manifest found: {', '.join(found_manifests)}")
    else:
        score -= 4
        details.append("No dependency manifest found")

    # Lock file exists (+3)
    locks = {"requirements.lock", "poetry.lock", "pipfile.lock", "package-lock.json",
             "yarn.lock", "pnpm-lock.yaml", "go.sum", "cargo.lock", "gemfile.lock"}
    found_locks = [f for f in root_files if f in locks]
    if found_locks:
        score += 3
        details.append(f"Lock file found: {', '.join(found_locks)}")
    else:
        score -= 2
        details.append("No lock file found")

    # Check for pinned deps
    for fpath, content in file_contents.items():
        fname = fpath.split("/")[-1].lower()
        if fname == "requirements.txt":
            lines = [l.strip() for l in content.splitlines()
                     if l.strip() and not l.strip().startswith("#") and not l.strip().startswith("-")]
            pinned = sum(1 for l in lines if re.search(r"==", l))
            if lines:
                pin_rate = pinned / len(lines)
                if pin_rate >= 0.8:
                    score += 3
                    details.append(f"{pinned}/{len(lines)} Python deps pinned ({pin_rate:.0%})")
                elif pin_rate >= 0.5:
                    details.append(f"{pinned}/{len(lines)} Python deps pinned ({pin_rate:.0%})")
                else:
                    score -= 2
                    details.append(f"Only {pinned}/{len(lines)} Python deps pinned ({pin_rate:.0%})")

    # Dep count signal
    dep_count = len(stack_profile.get("manifest_files", []))
    if dep_count > 0:
        details.append(f"{dep_count} manifest file(s) detected by stack profiler")

    return {"score": max(0, min(20, score)), "details": details}


def _score_code_organization(
    tree_paths: list[str],
    file_contents: dict[str, str],
    architecture: dict,
) -> dict:
    """Score 0-20 based on code structure and organization."""
    details: list[str] = []
    score = 10  # Start neutral

    source_files = [p for p in tree_paths if _is_source(p) and not _is_test(p)]

    # Max file length check
    max_lines = 0
    long_files = 0
    for fpath, content in file_contents.items():
        if _is_source(fpath) and not _is_test(fpath):
            lines = content.count("\n") + 1
            max_lines = max(max_lines, lines)
            if lines > 500:
                long_files += 1

    if max_lines > 0:
        if max_lines <= 300:
            score += 4
            details.append(f"Largest file: {max_lines} lines (well-organized)")
        elif max_lines <= 500:
            score += 2
            details.append(f"Largest file: {max_lines} lines (reasonable)")
        else:
            score -= 2
            details.append(f"Largest file: {max_lines} lines ({long_files} files >500 lines)")

    # Directory structure depth
    if source_files:
        max_depth = max(_depth(p) for p in source_files)
        avg_depth = sum(_depth(p) for p in source_files) / len(source_files)
        if 1 <= avg_depth <= 4:
            score += 3
            details.append(f"Good directory depth (avg: {avg_depth:.1f}, max: {max_depth})")
        elif avg_depth < 1:
            score -= 1
            details.append("Flat structure — all files at root")
        else:
            score -= 1
            details.append(f"Deep nesting (avg: {avg_depth:.1f}, max: {max_depth})")

    # Separation of concerns — check for distinct directories
    top_dirs = set()
    for p in source_files:
        parts = p.split("/")
        if len(parts) > 1:
            top_dirs.add(parts[0])
    if len(top_dirs) >= 3:
        score += 3
        details.append(f"{len(top_dirs)} top-level source directories (good separation)")
    elif len(top_dirs) >= 2:
        score += 1
        details.append(f"{len(top_dirs)} top-level source directories")
    elif source_files:
        details.append("All source in a single directory")

    # Architecture entry points
    entry_points = architecture.get("entry_points", [])
    if entry_points:
        details.append(f"{len(entry_points)} entry point(s) identified")

    return {"score": max(0, min(20, score)), "details": details}


def _score_security_posture(
    smells: list[SmellReport],
    checks: list[dict],
) -> dict:
    """Score 0-20 based on security signals from smells and checks."""
    details: list[str] = []
    score = 20  # Start perfect, deduct for problems

    # Deductions from detected smells
    smell_ids = {s["id"] for s in smells}

    high_security_smells = {"secrets_in_source", "raw_sql", "eval_exec", "env_committed"}
    medium_security_smells = {"no_gitignore"}

    high_found = high_security_smells & smell_ids
    medium_found = medium_security_smells & smell_ids

    if high_found:
        deduction = min(15, len(high_found) * 5)
        score -= deduction
        details.append(f"{len(high_found)} high-severity security issue(s) (−{deduction} pts)")
        for sid in high_found:
            smell = next(s for s in smells if s["id"] == sid)
            details.append(f"  • {smell['name']}")

    if medium_found:
        deduction = min(5, len(medium_found) * 2)
        score -= deduction
        details.append(f"{len(medium_found)} medium-severity issue(s) (−{deduction} pts)")

    # Bonus from governance checks
    if checks:
        w1 = next((c for c in checks if c.get("code") == "W1"), None)
        if w1 and w1.get("result") == "PASS":
            details.append("W1 secrets scan: clean")
        elif w1 and w1.get("result") == "FAIL":
            score -= 5
            details.append("W1 secrets scan: FAILED (−5 pts)")

    if score == 20:
        details.append("No security issues detected")

    return {"score": max(0, min(20, score)), "details": details}
