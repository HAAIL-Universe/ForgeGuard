"""run_audit.py — Forge AEM audit script (Python port of run_audit.ps1).

Runs 9 blocking checks (A1-A9) and 3 non-blocking warnings (W1-W3).
Reads layer boundaries from Contracts/boundaries.json.
Appends results to evidence/audit_ledger.md.

Usage:
    python Forge/scripts/run_audit.py --claimed-files file1.py,file2.py
    python Forge/scripts/run_audit.py --claimed-files file1.py --phase "Phase 1"

Exit codes:
    0 -- All blocking checks PASS.
    1 -- One or more blocking checks FAIL.
    2 -- Script execution error (missing dependencies, unreadable files, etc.).
"""

from __future__ import annotations

import argparse
import glob
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _info(msg: str) -> None:
    print(f"[run_audit] {msg}", flush=True)


def _warn(msg: str) -> None:
    print(f"[run_audit] WARN: {msg}", flush=True)


def _err(msg: str) -> None:
    print(f"[run_audit] ERROR: {msg}", file=sys.stderr, flush=True)


def _git(*args: str) -> tuple[int, str]:
    """Run a git command, return (exit_code, output)."""
    try:
        result = subprocess.run(
            ["git", *args],
            capture_output=True,
            text=True,
        )
        return result.returncode, (result.stdout + result.stderr).strip()
    except FileNotFoundError:
        return 127, "git not found on PATH"


def _require_git() -> str:
    """Verify we're inside a git repo and return repo root. Raises on failure."""
    code, out = _git("rev-parse", "--is-inside-work-tree")
    if code != 0 or out.strip() != "true":
        raise RuntimeError("Not inside a git repo.")
    _, root = _git("rev-parse", "--show-toplevel")
    return root.strip()


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------

def _check_a1(claimed: list[str]) -> str:
    """A1: Scope compliance — git diff vs claimed files."""
    try:
        _, staged = _git("diff", "--cached", "--name-only")
        _, unstaged = _git("diff", "--name-only")

        diff_set: set[str] = set()
        for line in (staged + "\n" + unstaged).splitlines():
            f = line.strip().replace("\\", "/")
            if f:
                diff_set.add(f)

        claimed_set = set(claimed)
        unclaimed = diff_set - claimed_set
        phantom = claimed_set - diff_set

        if unclaimed or phantom:
            parts = []
            if unclaimed:
                parts.append(f"Unclaimed in diff: {', '.join(sorted(unclaimed))}")
            if phantom:
                parts.append(f"Claimed but not in diff: {', '.join(sorted(phantom))}")
            return "FAIL -- " + ". ".join(parts)
        return f"PASS -- git diff matches claimed files exactly ({len(diff_set)} files)."
    except Exception as exc:
        return f"FAIL -- Error running git diff: {exc}"


def _check_a2() -> str:
    """A2: Minimal-diff — no renames in diff."""
    try:
        _, staged_sum = _git("diff", "--cached", "--summary")
        _, unstaged_sum = _git("diff", "--summary")
        combined = (staged_sum + "\n" + unstaged_sum).lower()
        renames = [ln for ln in combined.splitlines() if "rename" in ln]
        if renames:
            return f"FAIL -- Rename detected: {'; '.join(renames[:5])}"
        return "PASS -- No renames; diff is minimal."
    except Exception as exc:
        return f"FAIL -- Error checking minimal-diff: {exc}"


def _check_a3(test_runs_latest: Path, diff_log: Path) -> str:
    """A3: Evidence completeness — test_runs_latest.md=PASS + diff_log.md present."""
    failures = []
    if not test_runs_latest.exists():
        failures.append("test_runs_latest.md missing")
    else:
        first_line = test_runs_latest.read_text(encoding="utf-8").splitlines()[0].strip() if test_runs_latest.stat().st_size > 0 else ""
        if not re.match(r"^Status:\s*PASS", first_line, re.IGNORECASE):
            failures.append(f"test_runs_latest.md line 1 is '{first_line}', expected 'Status: PASS'")

    if not diff_log.exists():
        failures.append("diff_log.md missing")
    elif diff_log.stat().st_size == 0:
        failures.append("diff_log.md is empty")

    if failures:
        return "FAIL -- " + "; ".join(failures)
    return "PASS -- test_runs_latest.md=PASS, diff_log.md present."


def _check_a4(project_root: Path, boundaries_json: Path, claimed: list[str]) -> str:
    """A4: Boundary compliance — boundaries.json patterns vs source files."""
    try:
        if not boundaries_json.exists():
            return "PASS -- No boundaries.json found; boundary check skipped."

        boundaries = json.loads(boundaries_json.read_text(encoding="utf-8"))
        violations = []

        for layer in boundaries.get("layers", []):
            layer_name = layer.get("name", "?")
            layer_glob = layer.get("glob", "")
            forbidden_rules = layer.get("forbidden", [])

            if not layer_glob or not forbidden_rules:
                continue

            # Resolve glob pattern relative to project root
            pattern = str(project_root / layer_glob)
            matched_files = glob.glob(pattern, recursive=True)

            for filepath_str in matched_files:
                filepath = Path(filepath_str)
                if filepath.name in ("__init__.py", "__pycache__"):
                    continue
                try:
                    content = filepath.read_text(encoding="utf-8", errors="replace")
                except Exception:
                    continue

                for rule in forbidden_rules:
                    pat = rule.get("pattern", "")
                    reason = rule.get("reason", "")
                    if pat and re.search(pat, content, re.IGNORECASE):
                        violations.append(
                            f"[{layer_name}] {filepath.name} contains '{pat}' ({reason})"
                        )

        if violations:
            return "FAIL -- " + "; ".join(violations[:10])
        return "PASS -- No forbidden patterns found in any boundary layer."
    except Exception as exc:
        return f"FAIL -- Error checking boundaries: {exc}"


def _check_a5(diff_log: Path) -> str:
    """A5: Diff log gate — no TODO: in diff_log.md."""
    try:
        if not diff_log.exists():
            return "FAIL -- diff_log.md missing."
        content = diff_log.read_text(encoding="utf-8")
        if re.search(r"TODO:", content, re.IGNORECASE):
            return "FAIL -- diff_log.md contains TODO: placeholders."
        return "PASS -- No TODO: placeholders in diff_log.md."
    except Exception as exc:
        return f"FAIL -- Error checking diff log: {exc}"


def _check_a6(audit_ledger: Path) -> str:
    """A6: Authorization gate — no unauthorized commits since last AUTHORIZED."""
    try:
        last_auth_hash: str | None = None
        if audit_ledger.exists():
            content = audit_ledger.read_text(encoding="utf-8")
            matches = re.findall(r"commit[:\s]+([0-9a-f]{7,40})", content, re.IGNORECASE)
            if matches:
                last_auth_hash = matches[-1]

        if last_auth_hash:
            code, recent = _git("log", "--oneline", f"{last_auth_hash}..HEAD")
            if code != 0:
                return "PASS -- Could not resolve last AUTHORIZED hash; assuming clean."
            recent = recent.strip()
            if recent:
                count = len([l for l in recent.splitlines() if l.strip()])
                return f"FAIL -- {count} unauthorized commit(s) since last AUTHORIZED ({last_auth_hash})."
            return f"PASS -- No unauthorized commits since {last_auth_hash}."
        return "PASS -- No prior AUTHORIZED entry; first AEM cycle."
    except Exception as exc:
        return f"FAIL -- Error checking authorization: {exc}"


def _check_a7(diff_log: Path) -> str:
    """A7: Verification hierarchy — Static/Runtime/Behavior/Contract in order."""
    try:
        if not diff_log.exists():
            return "FAIL -- diff_log.md missing; cannot verify order."

        text = diff_log.read_text(encoding="utf-8")
        keywords = ["Static", "Runtime", "Behavior", "Contract"]
        positions = []
        missing = []

        for kw in keywords:
            idx = text.lower().find(kw.lower())
            if idx < 0:
                missing.append(kw)
            else:
                positions.append(idx)

        if missing:
            return f"FAIL -- Missing verification keywords: {', '.join(missing)}."

        ordered = all(positions[i] < positions[i + 1] for i in range(len(positions) - 1))
        if ordered:
            return "PASS -- Verification keywords appear in correct order (Static > Runtime > Behavior > Contract)."
        return "FAIL -- Verification keywords are out of order."
    except Exception as exc:
        return f"FAIL -- Error checking verification order: {exc}"


def _check_a8(test_runs_latest: Path) -> str:
    """A8: Test gate — test_runs_latest.md line 1 = 'Status: PASS'."""
    try:
        if not test_runs_latest.exists():
            return "FAIL -- test_runs_latest.md missing."
        text = test_runs_latest.read_text(encoding="utf-8")
        first_line = text.splitlines()[0].strip() if text.strip() else ""
        if re.match(r"^Status:\s*PASS", first_line, re.IGNORECASE):
            return "PASS -- test_runs_latest.md reports PASS."
        return f"FAIL -- test_runs_latest.md line 1: '{first_line}'."
    except Exception as exc:
        return f"FAIL -- Error checking test gate: {exc}"


# Python stdlib modules to skip in A9
_PYTHON_STDLIB = frozenset({
    "abc", "asyncio", "base64", "collections", "contextlib", "copy",
    "csv", "dataclasses", "datetime", "decimal", "enum", "functools",
    "glob", "hashlib", "html", "http", "importlib", "inspect", "io",
    "itertools", "json", "logging", "math", "mimetypes", "operator",
    "os", "pathlib", "pickle", "platform", "pprint", "random", "re",
    "secrets", "shutil", "signal", "socket", "sqlite3", "string",
    "struct", "subprocess", "sys", "tempfile", "textwrap", "threading",
    "time", "timeit", "traceback", "types", "typing", "unittest",
    "urllib", "uuid", "warnings", "xml", "zipfile", "typing_extensions",
    "pytest", "app", "tests", "scripts", "forge_ide", "__future__",
})

_PY_NAME_MAP = {
    "PIL": "Pillow",
    "cv2": "opencv-python",
    "sklearn": "scikit-learn",
    "yaml": "PyYAML",
    "bs4": "beautifulsoup4",
    "dotenv": "python-dotenv",
    "jose": "python-jose",
    "jwt": "PyJWT",
}

_NODE_BUILTINS = frozenset({
    "fs", "path", "http", "https", "url", "util", "os",
    "stream", "crypto", "events", "buffer", "child_process",
    "net", "tls", "dns", "cluster", "zlib", "readline", "assert", "querystring",
})


def _check_a9(project_root: Path, forge_json: Path, claimed: list[str]) -> str:
    """A9: Dependency gate — new imports present in requirements.txt/package.json."""
    try:
        if not forge_json.exists():
            return "PASS -- No forge.json found; dependency check skipped (Phase 0?)."

        forge = json.loads(forge_json.read_text(encoding="utf-8"))
        backend = forge.get("backend", {})
        dep_file = backend.get("dependency_file", "requirements.txt")
        lang = backend.get("language", "python")

        dep_path = project_root / dep_file
        if not dep_path.exists():
            return f"FAIL -- Dependency file '{dep_file}' not found."

        dep_content = dep_path.read_text(encoding="utf-8")
        ext_map = {
            "python": {".py"},
            "typescript": {".ts", ".tsx"},
            "javascript": {".js", ".jsx"},
            "go": {".go"},
        }
        source_exts = ext_map.get(lang, set())
        failures = []

        for cf in claimed:
            if Path(cf).suffix not in source_exts:
                continue
            cf_path = project_root / cf
            if not cf_path.exists():
                continue
            try:
                file_content = cf_path.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue

            imports: set[str] = set()
            if lang == "python":
                for m in re.finditer(r"(?m)^(?:from\s+(\S+)|import\s+(\S+))", file_content):
                    mod = m.group(1) or m.group(2)
                    top = mod.split(".")[0]
                    imports.add(top)
                for imp in imports:
                    if imp in _PYTHON_STDLIB:
                        continue
                    if (project_root / imp).is_dir():
                        continue
                    look_for = _PY_NAME_MAP.get(imp, imp)
                    if not re.search(re.escape(look_for), dep_content, re.IGNORECASE):
                        failures.append(f"{cf} imports '{imp}' (looked for '{look_for}' in {dep_file})")

            elif lang in ("typescript", "javascript"):
                for m in re.finditer(r"""(?:import|require)\s*\(?['"](@?[\w][^'"]*)['"]\)?""", file_content):
                    pkg = m.group(1)
                    if pkg.startswith(".") or pkg.startswith("/"):
                        continue
                    if pkg.startswith("@"):
                        parts = pkg.split("/")
                        top = "/".join(parts[:2]) if len(parts) >= 2 else pkg
                    else:
                        top = pkg.split("/")[0]
                    if top in _NODE_BUILTINS:
                        continue
                    if not re.search(re.escape(top), dep_content):
                        failures.append(f"{cf} imports '{top}' not found in {dep_file}")

        if failures:
            return "FAIL -- " + "; ".join(failures[:10])
        return "PASS -- All imports in changed files have declared dependencies."
    except Exception as exc:
        return f"FAIL -- Error checking dependencies: {exc}"


# ---------------------------------------------------------------------------
# Warnings
# ---------------------------------------------------------------------------

def _warn_w1() -> str:
    """W1: No secrets in diff."""
    try:
        _, staged_diff = _git("diff", "--cached")
        _, unstaged_diff = _git("diff")
        all_diff = staged_diff + "\n" + unstaged_diff

        secret_patterns = ["sk-", "AKIA", "-----BEGIN", "password=", "secret=", "token="]
        found = [sp for sp in secret_patterns if sp in all_diff]
        if found:
            return f"WARN -- Potential secrets found: {', '.join(found)}"
        return "PASS -- No secret patterns detected."
    except Exception as exc:
        return f"WARN -- Error scanning for secrets: {exc}"


def _warn_w2(audit_ledger: Path) -> str:
    """W2: Audit ledger integrity."""
    try:
        if not audit_ledger.exists():
            return "WARN -- audit_ledger.md does not exist yet."
        if audit_ledger.stat().st_size == 0:
            return "WARN -- audit_ledger.md is empty."
        return "PASS -- audit_ledger.md exists and is non-empty."
    except Exception as exc:
        return f"WARN -- Error checking audit ledger: {exc}"


def _warn_w3(project_root: Path, forge_json: Path, physics_yaml: Path) -> str:
    """W3: Physics route coverage."""
    try:
        if not physics_yaml.exists():
            return "WARN -- physics.yaml not found."

        yaml_text = physics_yaml.read_text(encoding="utf-8")
        # Extract paths like "  /endpoint:" from YAML
        physics_paths = re.findall(r"^\s{2}(/[^:]+):", yaml_text, re.MULTILINE)

        # Determine router directory from forge.json or convention
        router_dir: Path | None = None
        if forge_json.exists():
            forge = json.loads(forge_json.read_text(encoding="utf-8"))
            lang = forge.get("backend", {}).get("language", "python")
            candidates = {
                "python": project_root / "app" / "api" / "routers",
                "typescript": project_root / "src" / "routes",
                "go": project_root / "handlers",
            }
            router_dir = candidates.get(lang)

        if router_dir is None or not router_dir.exists():
            # Try common fallbacks
            for candidate in [
                project_root / "app" / "api" / "routers",
                project_root / "src" / "routes",
                project_root / "handlers",
            ]:
                if candidate.exists():
                    router_dir = candidate
                    break

        if router_dir is None:
            return "WARN -- No router/handler directory found."

        router_files = {f.name for f in router_dir.iterdir() if f.is_file()}
        uncovered = []

        for path in physics_paths:
            if path == "/" or "/static/" in path:
                continue
            parts = path.strip("/").split("/")
            segment = parts[0] if parts else ""
            if not segment:
                continue
            expected = {f"{segment}.py", f"{segment}.ts", f"{segment}.js", f"{segment}.go"}
            if not expected & router_files:
                uncovered.append(f"{path} (expected handler for '{segment}')")

        if uncovered:
            return f"WARN -- Uncovered routes: {'; '.join(uncovered[:5])}"
        return "PASS -- All physics paths have corresponding handler files."
    except Exception as exc:
        return f"WARN -- Error checking physics coverage: {exc}"


# ---------------------------------------------------------------------------
# Output + ledger
# ---------------------------------------------------------------------------

def _build_output(
    timestamp: str,
    phase: str,
    claimed: list[str],
    results: dict[str, str],
    warnings: dict[str, str],
    any_fail: bool,
) -> str:
    overall = "FAIL" if any_fail else "PASS"
    lines = [
        "=== AUDIT SCRIPT RESULT ===",
        f"Timestamp: {timestamp}",
        f"Phase: {phase}",
        f"Claimed files: {', '.join(claimed)}",
        "",
        f"A1 Scope compliance:       {results.get('A1', 'SKIP')}",
        f"A2 Minimal-diff:           {results.get('A2', 'SKIP')}",
        f"A3 Evidence completeness:  {results.get('A3', 'SKIP')}",
        f"A4 Boundary compliance:    {results.get('A4', 'SKIP')}",
        f"A5 Diff Log Gate:          {results.get('A5', 'SKIP')}",
        f"A6 Authorization Gate:     {results.get('A6', 'SKIP')}",
        f"A7 Verification order:     {results.get('A7', 'SKIP')}",
        f"A8 Test gate:              {results.get('A8', 'SKIP')}",
        f"A9 Dependency gate:        {results.get('A9', 'SKIP')}",
        "",
        f"W1 No secrets in diff:     {warnings.get('W1', 'SKIP')}",
        f"W2 Audit ledger integrity: {warnings.get('W2', 'SKIP')}",
        f"W3 Physics route coverage: {warnings.get('W3', 'SKIP')}",
        "",
        f"Overall: {overall}",
        "=== END AUDIT SCRIPT RESULT ===",
    ]
    return "\n".join(lines)


def _append_ledger(
    audit_ledger: Path,
    timestamp: str,
    phase: str,
    claimed: list[str],
    results: dict[str, str],
    warnings: dict[str, str],
    any_fail: bool,
) -> None:
    # Determine iteration number
    iteration = 1
    if audit_ledger.exists():
        content = audit_ledger.read_text(encoding="utf-8")
        iter_matches = re.findall(r"(?m)^## Audit Entry:.*Iteration (\d+)", content)
        if iter_matches:
            iteration = int(iter_matches[-1]) + 1

    outcome = "FAIL" if any_fail else "SIGNED-OFF (awaiting AUTHORIZED)"

    checklist_lines = [
        "### Checklist",
        f"- A1 Scope compliance:      {results.get('A1', 'SKIP')}",
        f"- A2 Minimal-diff:          {results.get('A2', 'SKIP')}",
        f"- A3 Evidence completeness: {results.get('A3', 'SKIP')}",
        f"- A4 Boundary compliance:   {results.get('A4', 'SKIP')}",
        f"- A5 Diff Log Gate:         {results.get('A5', 'SKIP')}",
        f"- A6 Authorization Gate:    {results.get('A6', 'SKIP')}",
        f"- A7 Verification order:    {results.get('A7', 'SKIP')}",
        f"- A8 Test gate:             {results.get('A8', 'SKIP')}",
        f"- A9 Dependency gate:       {results.get('A9', 'SKIP')}",
    ]

    fix_plan_lines: list[str] = []
    if any_fail:
        fix_plan_lines = ["", "### Fix Plan (FAIL items)"]
        for key in ("A1", "A2", "A3", "A4", "A5", "A6", "A7", "A8", "A9"):
            val = results.get(key, "")
            if val.startswith("FAIL"):
                fix_plan_lines.append(f"- {key}: {val}")

    files_lines = ["- " + f for f in claimed]

    entry_parts = [
        "",
        "---",
        f"## Audit Entry: {phase} -- Iteration {iteration}",
        f"Timestamp: {timestamp}",
        f"AEM Cycle: {phase}",
        f"Outcome: {outcome}",
        "",
        *checklist_lines,
        *fix_plan_lines,
        "",
        "### Files Changed",
        *files_lines,
        "",
        "### Notes",
        f"W1: {warnings.get('W1', 'SKIP')}",
        f"W2: {warnings.get('W2', 'SKIP')}",
        f"W3: {warnings.get('W3', 'SKIP')}",
    ]

    entry = "\n".join(entry_parts)

    if not audit_ledger.exists():
        audit_ledger.parent.mkdir(parents=True, exist_ok=True)
        audit_ledger.write_text(
            "# Audit Ledger -- Forge AEM\n"
            "Append-only record of all Internal Audit Pass results.\n"
            "Do not overwrite or truncate this file.\n",
            encoding="utf-8",
        )
        _info("Created audit_ledger.md.")

    with audit_ledger.open("a", encoding="utf-8") as f:
        f.write(entry + "\n")

    _info(f"Appended audit entry (Iteration {iteration}, Outcome: {outcome}).")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description="Forge AEM audit script")
    parser.add_argument(
        "--claimed-files", "-c",
        required=True,
        help="Comma-separated list of files changed in this cycle",
    )
    parser.add_argument(
        "--phase", "-p",
        default="unknown",
        help="Phase identifier (e.g. 'Phase 1 -- Auth')",
    )
    args = parser.parse_args()

    # Parse claimed files
    claimed = sorted({
        f.strip().replace("\\", "/")
        for f in args.claimed_files.split(",")
        if f.strip()
    })
    if not claimed:
        _err("--claimed-files is empty.")
        return 2

    try:
        project_root = Path(_require_git())
    except RuntimeError as exc:
        _err(str(exc))
        return 2

    # Governance root: parent of scripts/ dir (where Contracts/, evidence/ live)
    script_dir = Path(__file__).resolve().parent
    gov_root = script_dir.parent

    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    evidence_dir = gov_root / "evidence"
    test_runs_latest = evidence_dir / "test_runs_latest.md"
    diff_log = evidence_dir / "diff_log.md"
    audit_ledger = evidence_dir / "audit_ledger.md"
    physics_yaml = gov_root / "Contracts" / "physics.yaml"
    boundaries_json = gov_root / "Contracts" / "boundaries.json"
    forge_json = project_root / "forge.json"

    results: dict[str, str] = {}
    warnings_: dict[str, str] = {}
    any_fail = False

    _info(f"Running audit for phase='{args.phase}' files={claimed}")

    # Blocking checks
    results["A1"] = _check_a1(claimed)
    results["A2"] = _check_a2()
    results["A3"] = _check_a3(test_runs_latest, diff_log)
    results["A4"] = _check_a4(project_root, boundaries_json, claimed)
    results["A5"] = _check_a5(diff_log)
    results["A6"] = _check_a6(audit_ledger)
    results["A7"] = _check_a7(diff_log)
    results["A8"] = _check_a8(test_runs_latest)
    results["A9"] = _check_a9(project_root, forge_json, claimed)

    for val in results.values():
        if val.startswith("FAIL"):
            any_fail = True
            break

    # Non-blocking warnings
    warnings_["W1"] = _warn_w1()
    warnings_["W2"] = _warn_w2(audit_ledger)
    warnings_["W3"] = _warn_w3(project_root, forge_json, physics_yaml)

    # Print structured output
    output = _build_output(timestamp, args.phase, claimed, results, warnings_, any_fail)
    print(output, flush=True)

    # Append to audit ledger
    try:
        _append_ledger(audit_ledger, timestamp, args.phase, claimed, results, warnings_, any_fail)
    except Exception as exc:
        _warn(f"Could not append to audit ledger: {exc}")

    return 1 if any_fail else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        _err(f"SCRIPT ERROR: {exc}")
        import traceback
        traceback.print_exc()
        sys.exit(2)
