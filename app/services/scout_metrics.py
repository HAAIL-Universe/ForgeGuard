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
# { "scores": { dim: {"score": 0-100, "weight": float, "details": [...]} },
#   "computed_score": 0-100 (weighted),
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
# 58.2 — 9-Dimension Metrics Engine (Seal-aligned)
# ---------------------------------------------------------------------------

# Dimension weights — must sum to 1.0 (identical to certificate_scorer)
_WEIGHTS: dict[str, float] = {
    "build_integrity": 0.15,
    "test_coverage": 0.15,
    "audit_compliance": 0.10,
    "governance": 0.10,
    "security": 0.10,
    "cost_efficiency": 0.05,
    "reliability": 0.15,
    "consistency": 0.10,
    "architecture": 0.10,
}

# Neutral score for dimensions that have no data at dossier time
_NEUTRAL = 50


def compute_repo_metrics(
    tree_paths: list[str],
    file_contents: dict[str, str],
    stack_profile: dict | None = None,
    architecture: dict | None = None,
    checks: list[dict] | None = None,
) -> RepoMetrics:
    """Compute deterministic quality metrics from deep scan artifacts.

    Phase 58 — 9 Seal-aligned dimensions, each 0-100, weighted to a
    single computed_score 0-100.  Dimensions that lack data at dossier
    time score neutral (50) rather than 0, with a note explaining why.

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

    # Build scout-like data for architecture scorer
    scout_data_for_arch = {
        "stack_profile": stack_profile,
        "architecture": architecture,
        "tree_size": len(tree_paths),
        "files_analysed": len(file_contents),
        "checks_passed": sum(1 for c in checks if c.get("result") == "PASS"),
        "checks_failed": sum(1 for c in checks if c.get("result") == "FAIL"),
        "checks_warned": sum(1 for c in checks if c.get("result") == "WARN"),
    }

    # Score each dimension (all 0-100 with weight)
    scores = {
        "build_integrity": _score_build_integrity_baseline(),
        "test_coverage": _score_test_coverage(
            source_files, test_files_list, file_contents, tree_paths,
        ),
        "audit_compliance": _score_audit_compliance(checks),
        "governance": _score_governance(checks),
        "security": _score_security(smells, checks, file_contents),
        "cost_efficiency": _score_cost_efficiency_baseline(),
        "reliability": _score_reliability_baseline(),
        "consistency": _score_consistency(
            tree_paths, file_contents, source_files, non_test_source, stack_profile,
        ),
        "architecture": _score_architecture(scout_data_for_arch),
    }

    # Weighted overall
    computed_score = round(
        sum(d["score"] * d["weight"] for d in scores.values())
    )

    return {
        "scores": scores,
        "computed_score": computed_score,
        "file_stats": file_stats,
        "smells": smells,
    }


# ---------------------------------------------------------------------------
# Dimension scorers (each returns {"score": 0-100, "weight": float, "details": [...]})
# ---------------------------------------------------------------------------


def _dim(score: float, key: str, details: list[str]) -> dict:
    """Helper to build a dimension result dict."""
    return {
        "score": max(0, min(100, round(score))),
        "weight": _WEIGHTS[key],
        "details": details,
    }


# ── 1. Build Integrity ──────────────────────────────────────────

def _score_build_integrity_baseline() -> dict:
    """Neutral at dossier time — no build exists yet."""
    return _dim(_NEUTRAL, "build_integrity", [
        "No build history at dossier baseline — neutral score applied",
    ])


# ── 2. Test Coverage ────────────────────────────────────────────

def _score_test_coverage(
    source_files: list[str],
    test_files: list[str],
    file_contents: dict[str, str],
    tree_paths: list[str],
) -> dict:
    """Score 0-100 based on test file coverage, structure, and depth."""
    details: list[str] = []

    if not source_files:
        details.append("No source files found")
        return _dim(0, "test_coverage", details)

    total = len(source_files)
    tests = len(test_files)
    ratio = tests / total if total > 0 else 0.0
    details.append(f"{tests} test files / {total} source files (ratio: {ratio:.2f})")

    # Base score from ratio (continuous, up to 50 points)
    ratio_score = min(50, ratio * 166.7)  # 0.30 ratio → 50 pts
    details.append(f"Test ratio contributes {ratio_score:.0f}/50 points")

    # Test structure bonus (up to 20 points)
    structure_score = 0.0
    test_dirs = {p.split("/")[0] for p in test_files if "/" in p}
    src_dirs = {p.split("/")[0] for p in source_files if "/" in p and not _is_test(p)}
    if test_dirs:
        structure_score += 10
        details.append(f"Test directories: {', '.join(sorted(test_dirs)[:3])}")
    if test_dirs & src_dirs:
        structure_score += 5
        details.append("Test dirs overlap source dirs (co-located)")
    # Check for test naming conventions
    named_tests = [p for p in test_files if re.search(r"test_|_test\.|\.test\.|\.spec\.", p)]
    if named_tests:
        name_ratio = len(named_tests) / max(len(test_files), 1)
        structure_score += min(5, name_ratio * 5)
        details.append(f"{len(named_tests)}/{len(test_files)} follow naming conventions")

    # Documentation/README bonus (up to 15 points)
    doc_score = 0.0
    root_files = {p.lower() for p in tree_paths if "/" not in p}
    readme_names = {"readme.md", "readme.rst", "readme.txt", "readme"}
    if any(f in readme_names for f in root_files):
        doc_score += 5
        details.append("README present")
        for fpath, content in file_contents.items():
            if fpath.lower().split("/")[-1] in readme_names:
                chars = len(content)
                if chars > 2000:
                    doc_score += 5
                    details.append(f"README substantial ({chars:,} chars)")
                elif chars > 500:
                    doc_score += 2
    has_docs_dir = any(p.lower().startswith("docs/") for p in tree_paths)
    if has_docs_dir:
        doc_score += 5
        details.append("docs/ directory found")

    # Dependency management bonus (up to 15 points)
    dep_score = 0.0
    manifests = {"requirements.txt", "pyproject.toml", "package.json", "go.mod",
                 "cargo.toml", "gemfile", "build.gradle", "pom.xml"}
    found_manifests = [f for f in root_files if f in manifests]
    if found_manifests:
        dep_score += 8
        details.append(f"Manifest: {', '.join(found_manifests)}")
    locks = {"poetry.lock", "pipfile.lock", "package-lock.json",
             "yarn.lock", "pnpm-lock.yaml", "go.sum", "cargo.lock"}
    if any(f in locks for f in root_files):
        dep_score += 7
        details.append("Lock file present")

    total_score = ratio_score + structure_score + doc_score + dep_score
    return _dim(total_score, "test_coverage", details)


# ── 3. Audit Compliance ─────────────────────────────────────────

def _score_audit_compliance(checks: list[dict]) -> dict:
    """Score 0-100 based on audit check pass rate from the deep scan."""
    details: list[str] = []

    if not checks:
        details.append("No audit checks available — neutral score applied")
        return _dim(_NEUTRAL, "audit_compliance", details)

    passed = sum(1 for c in checks if c.get("result") == "PASS")
    failed = sum(1 for c in checks if c.get("result") == "FAIL")
    warned = sum(1 for c in checks if c.get("result") == "WARN")
    total = len(checks)

    pass_rate = passed / total if total > 0 else 0
    base_score = pass_rate * 80
    details.append(f"{passed}/{total} checks passed ({pass_rate:.0%})")

    # Penalty for warnings
    if warned > 0:
        warn_penalty = min(10, warned * 2)
        base_score -= warn_penalty
        details.append(f"{warned} warnings (−{warn_penalty} pts)")

    # Bonus for zero failures
    if failed == 0 and total > 0:
        base_score += 20
        details.append("All blocking checks passed (+20 pts)")
    elif failed > 0:
        details.append(f"{failed} blocking failures")

    return _dim(base_score, "audit_compliance", details)


# ── 4. Governance ────────────────────────────────────────────────

def _score_governance(checks: list[dict]) -> dict:
    """Score 0-100 based on per-check governance breakdown (A1-A9, W1-W3)."""
    details: list[str] = []

    if not checks:
        details.append("No governance check data — neutral score applied")
        return _dim(_NEUTRAL, "governance", details)

    # Separate blocking (A*) from warning (W*)
    blocking = [c for c in checks if c.get("code", "").startswith("A")]
    warnings = [c for c in checks if c.get("code", "").startswith("W")]

    b_passed = sum(1 for c in blocking if c.get("result") == "PASS")
    b_failed = sum(1 for c in blocking if c.get("result") == "FAIL")
    w_passed = sum(1 for c in warnings if c.get("result") == "PASS")
    w_warned = sum(1 for c in warnings if c.get("result") == "WARN")

    # Blocking checks worth 70% of score
    if blocking:
        b_rate = b_passed / len(blocking)
        b_score = b_rate * 70
        details.append(f"Blocking: {b_passed}/{len(blocking)} passed")
    else:
        b_score = 35  # Half credit if no blocking checks
        details.append("No blocking checks found")

    # Warning checks worth 30% of score
    if warnings:
        w_rate = w_passed / len(warnings)
        w_score = w_rate * 30
        details.append(f"Warnings: {w_passed}/{len(warnings)} clean")
    else:
        w_score = 15  # Half credit
        details.append("No warning checks found")

    # Per-check detail
    for c in checks:
        code = c.get("code", "?")
        result = c.get("result", "?")
        if result in ("FAIL", "WARN"):
            name = c.get("name", c.get("check_name", code))
            details.append(f"  {code}: {result} — {name}")

    return _dim(b_score + w_score, "governance", details)


# ── 5. Security ──────────────────────────────────────────────────

def _score_security(
    smells: list[SmellReport],
    checks: list[dict],
    file_contents: dict[str, str],
) -> dict:
    """Score 0-100 based on security signals from smells, checks, and patterns."""
    details: list[str] = []
    score = 100.0  # Start perfect, deduct for problems

    # Deductions from detected smells
    smell_ids = {s["id"] for s in smells}

    high_security_smells = {"secrets_in_source", "raw_sql", "eval_exec", "env_committed"}
    medium_security_smells = {"no_gitignore", "unpinned_deps"}

    high_found = high_security_smells & smell_ids
    medium_found = medium_security_smells & smell_ids

    if high_found:
        deduction = min(40, len(high_found) * 12)
        score -= deduction
        details.append(f"{len(high_found)} high-severity security issue(s) (−{deduction} pts)")
        for sid in high_found:
            smell = next(s for s in smells if s["id"] == sid)
            details.append(f"  • {smell['name']}")

    if medium_found:
        deduction = min(15, len(medium_found) * 5)
        score -= deduction
        details.append(f"{len(medium_found)} medium-severity issue(s) (−{deduction} pts)")

    # W1 governance check
    if checks:
        w1 = next((c for c in checks if c.get("code") == "W1"), None)
        if w1 and w1.get("result") == "PASS":
            details.append("W1 secrets scan: clean")
        elif w1 and w1.get("result") == "FAIL":
            score -= 15
            details.append("W1 secrets scan: FAILED (−15 pts)")
        elif w1 and w1.get("result") == "WARN":
            score -= 8
            details.append("W1 secrets scan: WARNING (−8 pts)")

    # Auth pattern detection (bonus signals)
    auth_files = 0
    for fpath, content in file_contents.items():
        if _is_source(fpath):
            lower_content = content.lower()
            if any(pat in lower_content for pat in (
                "jwt", "oauth", "bearer", "authenticate", "authorization",
                "password_hash", "bcrypt", "argon2",
            )):
                auth_files += 1
    if auth_files > 0:
        score = min(100, score + 5)
        details.append(f"Auth patterns found in {auth_files} file(s) (+5 pts)")

    if round(score) >= 95:
        details.append("No significant security issues detected")

    return _dim(score, "security", details)


# ── 6. Cost Efficiency ──────────────────────────────────────────

def _score_cost_efficiency_baseline() -> dict:
    """Neutral at dossier time — no build cost data exists yet."""
    return _dim(_NEUTRAL, "cost_efficiency", [
        "No build cost data at dossier baseline — neutral score applied",
    ])


# ── 7. Reliability ──────────────────────────────────────────────

def _score_reliability_baseline() -> dict:
    """Neutral at dossier time — no build/audit history trend available."""
    return _dim(_NEUTRAL, "reliability", [
        "No build/audit trend at dossier baseline — neutral score applied",
    ])


# ── 8. Consistency ──────────────────────────────────────────────

def _score_consistency(
    tree_paths: list[str],
    file_contents: dict[str, str],
    source_files: list[str],
    non_test_source: list[str],
    stack_profile: dict,
) -> dict:
    """Score 0-100 based on code consistency, style regularity, and structure."""
    details: list[str] = []
    score = 50.0  # Start neutral

    # ── File length uniformity (up to +15 / -10) ─────────────────
    line_counts: list[int] = []
    long_files = 0
    for fpath, content in file_contents.items():
        if _is_source(fpath) and not _is_test(fpath):
            lines = content.count("\n") + 1
            line_counts.append(lines)
            if lines > 500:
                long_files += 1

    if line_counts:
        avg = sum(line_counts) / len(line_counts)
        max_lines = max(line_counts)
        if max_lines <= 300:
            score += 15
            details.append(f"Largest file: {max_lines} lines (well-sized)")
        elif max_lines <= 500:
            score += 8
            details.append(f"Largest file: {max_lines} lines (reasonable)")
        else:
            score -= min(10, long_files * 3)
            details.append(f"Largest file: {max_lines} lines ({long_files} >500 lines)")

        # Variance check
        if len(line_counts) >= 3:
            variance = sum((x - avg) ** 2 for x in line_counts) / len(line_counts)
            std_dev = variance ** 0.5
            cv = std_dev / avg if avg > 0 else 0
            if cv < 0.5:
                score += 5
                details.append(f"Low file-size variance (CV: {cv:.2f})")
            elif cv > 1.5:
                score -= 5
                details.append(f"High file-size variance (CV: {cv:.2f})")

    # ── Directory structure (up to +15 / -5) ──────────────────────
    if non_test_source:
        depths = [_depth(p) for p in non_test_source]
        avg_depth = sum(depths) / len(depths)
        max_depth_val = max(depths)
        if 1 <= avg_depth <= 4:
            score += 10
            details.append(f"Good directory depth (avg: {avg_depth:.1f}, max: {max_depth_val})")
        elif avg_depth < 1:
            score -= 5
            details.append("Flat structure — all files at root")
        else:
            score -= 3
            details.append(f"Deep nesting (avg: {avg_depth:.1f})")

        top_dirs = set()
        for p in non_test_source:
            parts = p.split("/")
            if len(parts) > 1:
                top_dirs.add(parts[0])
        if len(top_dirs) >= 3:
            score += 5
            details.append(f"{len(top_dirs)} top-level source directories (good separation)")
        elif len(top_dirs) >= 2:
            score += 2
            details.append(f"{len(top_dirs)} top-level source directories")

    # ── Naming convention consistency (up to +10) ─────────────────
    snake_count = 0
    camel_count = 0
    for p in source_files:
        fname = p.split("/")[-1]
        base = fname.rsplit(".", 1)[0] if "." in fname else fname
        if "_" in base:
            snake_count += 1
        elif any(c.isupper() for c in base[1:]):
            camel_count += 1
    total_named = snake_count + camel_count
    if total_named > 0:
        dominant = max(snake_count, camel_count)
        consistency_ratio = dominant / total_named
        if consistency_ratio >= 0.9:
            score += 10
            convention = "snake_case" if snake_count > camel_count else "camelCase"
            details.append(f"Naming: {consistency_ratio:.0%} {convention} (consistent)")
        elif consistency_ratio >= 0.7:
            score += 5
            details.append(f"Naming: {consistency_ratio:.0%} consistent")
        else:
            score -= 3
            details.append(f"Mixed naming conventions ({snake_count} snake, {camel_count} camel)")

    # ── Docstring presence in source files (+5) ───────────────────
    docstring_files = 0
    checked_files = 0
    for fpath, content in file_contents.items():
        if _is_source(fpath) and not _is_test(fpath):
            checked_files += 1
            if '"""' in content or "'''" in content or "/**" in content:
                docstring_files += 1
    if checked_files > 0:
        ds_ratio = docstring_files / checked_files
        if ds_ratio >= 0.5:
            score += 5
            details.append(f"Docstrings in {docstring_files}/{checked_files} source files")
        elif ds_ratio > 0:
            score += 2
            details.append(f"Some docstrings ({docstring_files}/{checked_files} files)")

    return _dim(score, "consistency", details)


# ── 9. Architecture ─────────────────────────────────────────────

def _score_architecture(scout_data: dict) -> dict:
    """Score 0-100 using the architecture baseline comparison.

    Delegates to ``architecture_baseline.compare_against_baseline()``
    which evaluates 9 stack-aware rules and returns a weighted score.
    """
    from app.services.architecture_baseline import compare_against_baseline

    result = compare_against_baseline(scout_data)
    return {
        "score": max(0, min(100, result["score"])),
        "weight": _WEIGHTS["architecture"],
        "details": result.get("details", []),
        "grade": result.get("grade", "?"),
        "rules_passed": result.get("rules_passed", 0),
        "rules_evaluated": result.get("rules_evaluated", 0),
    }
