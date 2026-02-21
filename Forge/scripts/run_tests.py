"""run_tests.py — Stack-aware test runner for Forge-managed projects.

Python port of run_tests.ps1.  Reads forge.json for stack configuration,
runs static checks and tests, then writes evidence/test_runs.md and
evidence/test_runs_latest.md.

Usage:
    python Forge/scripts/run_tests.py
    python Forge/scripts/run_tests.py --scope backend
    python Forge/scripts/run_tests.py --scope frontend
    python Forge/scripts/run_tests.py --no-venv

Exit codes:
    0 -- All test phases PASS.
    1 -- One or more test phases FAIL.
    2 -- Script execution error.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _info(msg: str) -> None:
    print(f"[run_tests] {msg}", flush=True)


def _err(msg: str) -> None:
    print(f"[run_tests] ERROR: {msg}", file=sys.stderr, flush=True)


def _git(*args: str) -> str:
    """Run git command, return output or 'git unavailable' on failure."""
    try:
        r = subprocess.run(["git", *args], capture_output=True, text=True, timeout=15)
        return r.stdout.strip() if r.returncode == 0 else "git unavailable"
    except Exception:
        return "git unavailable"


def _run_cmd(
    cmd: list[str],
    cwd: str | None = None,
    env: dict | None = None,
    timeout: int = 300,
) -> tuple[int, str, str]:
    """Run a command, return (exit_code, stdout, stderr)."""
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            cwd=cwd,
            env=env or os.environ.copy(),
            timeout=timeout,
        )
        return result.returncode, result.stdout, result.stderr
    except subprocess.TimeoutExpired:
        return -1, "", f"Command timed out after {timeout}s"
    except FileNotFoundError as exc:
        return 127, "", f"Command not found: {exc}"
    except Exception as exc:
        return -1, "", str(exc)


def _resolve_python(root: Path, venv_path: str, no_venv: bool) -> str:
    """Find the Python executable — prefer venv, fall back to system python."""
    if not no_venv and venv_path:
        for suffix in [
            f"{venv_path}/Scripts/python.exe",  # Windows venv
            f"{venv_path}/bin/python",           # Unix venv
            f"{venv_path}/bin/python3",
        ]:
            candidate = root / suffix
            if candidate.exists():
                return str(candidate)
    return "python"


def _load_dotenv(root: Path) -> None:
    """Load .env file into os.environ (best-effort)."""
    env_file = root / ".env"
    if not env_file.exists():
        return
    try:
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = val
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Evidence writers
# ---------------------------------------------------------------------------

def _append_test_run_log(
    gov_root: Path,
    status: str,
    runtime_path: str,
    start_utc: str,
    end_utc: str,
    exit_codes: dict[str, int],
    summaries: dict[str, str],
    git_branch: str,
    git_head: str,
    git_status: str,
    git_diff_stat: str,
    failure_payload: str = "",
) -> None:
    log_path = gov_root / "evidence" / "test_runs.md"
    log_path.parent.mkdir(parents=True, exist_ok=True)

    lines = [
        f"## Test Run {start_utc}",
        f"- Status: {status}",
        f"- Start: {start_utc}",
        f"- End: {end_utc}",
        f"- Runtime: {runtime_path}",
        f"- Branch: {git_branch}",
        f"- HEAD: {git_head}",
    ]
    for k, v in exit_codes.items():
        lines.append(f"- {k} exit: {v}")
    for k, v in summaries.items():
        lines.append(f"- {k} summary: {v}")
    lines += [
        "- git status -sb:",
        "```",
        git_status,
        "```",
        "- git diff --stat:",
        "```",
        git_diff_stat,
        "```",
    ]
    if status == "FAIL" and failure_payload:
        lines += [
            "- Failure payload:",
            "```",
            failure_payload,
            "```",
        ]
    lines.append("")

    with log_path.open("a", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def _write_test_run_latest(
    gov_root: Path,
    status: str,
    runtime_path: str,
    start_utc: str,
    end_utc: str,
    exit_codes: dict[str, int],
    summaries: dict[str, str],
    failing_tests: str,
    git_branch: str,
    git_head: str,
    git_status: str,
    git_diff_stat: str,
    failure_payload: str = "",
) -> None:
    log_path = gov_root / "evidence" / "test_runs_latest.md"
    log_path.parent.mkdir(parents=True, exist_ok=True)

    lines = [
        f"Status: {status}",
        f"Start: {start_utc}",
        f"End: {end_utc}",
        f"Branch: {git_branch}",
        f"HEAD: {git_head}",
        f"Runtime: {runtime_path}",
    ]
    for k, v in exit_codes.items():
        lines.append(f"{k} exit: {v}")
    for k, v in summaries.items():
        lines.append(f"{k} summary: {v}")

    if status == "FAIL":
        lines.append("Failing tests:")
        lines.append(failing_tests if failing_tests else "(see console output)")
        if failure_payload:
            lines += ["Failure payload:", "```", failure_payload, "```"]

    lines += [
        "git status -sb:",
        "```",
        git_status,
        "```",
        "git diff --stat:",
        "```",
        git_diff_stat,
        "```",
        "",
    ]

    log_path.write_text("\n".join(lines), encoding="utf-8")


# ---------------------------------------------------------------------------
# Test runners per stack
# ---------------------------------------------------------------------------

def _run_python_backend(
    root: Path,
    forge_cfg: dict,
    no_venv: bool,
    exit_codes: dict,
    summaries: dict,
    output_captures: dict,
) -> tuple[str, str]:
    """Run Python static checks + pytest. Returns (runtime_path, failing_tests)."""
    venv_path = forge_cfg.get("backend", {}).get("venv_path", ".venv")
    py = _resolve_python(root, venv_path, no_venv)
    runtime_path = py
    _info(f"Python: {py}")

    failing_tests = ""

    # Static: compileall
    code, out, err = _run_cmd([py, "-m", "compileall", "app"], cwd=str(root))
    exit_codes["compileall"] = code
    output_captures["compileall"] = (out + err).splitlines()
    if code == 0:
        _info("compileall: ok")
    else:
        _err(f"compileall failed ({code})")
    summaries["compileall"] = "ok" if code == 0 else f"FAIL (exit {code})"

    # Static: import sanity
    entry_module = forge_cfg.get("backend", {}).get("entry_module", "app.main")
    code, out, err = _run_cmd(
        [py, "-c", f"import {entry_module}; print('import ok')"],
        cwd=str(root),
    )
    exit_codes["import_sanity"] = code
    output_captures["import_sanity"] = (out + err).splitlines()
    if code == 0:
        _info(f"import {entry_module}: ok")
    else:
        _err(f"import {entry_module} failed ({code})")
    summaries["import_sanity"] = "ok" if code == 0 else f"FAIL (exit {code})"

    # Tests: pytest
    test_dir = forge_cfg.get("backend", {}).get("test_dir", "tests")
    _info(f"pytest scope: {test_dir}")
    code, out, err = _run_cmd([py, "-m", "pytest", test_dir, "-q"], cwd=str(root))
    exit_codes["pytest"] = code
    combined = (out + err).strip()
    output_captures["pytest"] = combined.splitlines()
    non_empty = [l for l in combined.splitlines() if l.strip()]
    summaries["pytest"] = non_empty[-1] if non_empty else "(no output)"
    if code != 0:
        failing_tests = "\n".join(
            l for l in non_empty if "FAILED" in l or "::" in l
        )
    if code == 0:
        _info("pytest: ok")
    else:
        _err(f"pytest failed ({code})")

    return runtime_path, failing_tests


def _run_typescript_backend(
    root: Path,
    forge_cfg: dict,
    exit_codes: dict,
    summaries: dict,
    output_captures: dict,
) -> tuple[str, str]:
    """Run TypeScript static checks + npm test."""
    runtime_path = "node"
    _info("Runtime: Node.js (TypeScript)")

    # Static: tsc --noEmit
    code, out, err = _run_cmd(["npx", "tsc", "--noEmit"], cwd=str(root))
    exit_codes["tsc"] = code
    output_captures["tsc"] = (out + err).splitlines()
    summaries["tsc"] = "ok" if code == 0 else f"FAIL (exit {code})"
    if code == 0:
        _info("tsc --noEmit: ok")
    else:
        _err(f"tsc --noEmit failed ({code})")

    # Tests
    framework = forge_cfg.get("backend", {}).get("test_framework", "jest")
    code, out, err = _run_cmd(["npm", "test"], cwd=str(root))
    exit_codes[framework] = code
    combined = (out + err).strip()
    output_captures[framework] = combined.splitlines()
    non_empty = [l for l in combined.splitlines() if l.strip()]
    summaries[framework] = non_empty[-1] if non_empty else "(no output)"
    if code == 0:
        _info(f"{framework}: ok")
    else:
        _err(f"{framework} failed ({code})")

    return runtime_path, ""


def _run_go_backend(
    root: Path,
    exit_codes: dict,
    summaries: dict,
    output_captures: dict,
) -> tuple[str, str]:
    """Run Go static checks + go test."""
    runtime_path = "go"
    _info("Runtime: Go")

    # Static: go vet
    code, out, err = _run_cmd(["go", "vet", "./..."], cwd=str(root))
    exit_codes["go_vet"] = code
    output_captures["go_vet"] = (out + err).splitlines()
    summaries["go_vet"] = "ok" if code == 0 else f"FAIL (exit {code})"
    if code == 0:
        _info("go vet: ok")
    else:
        _err(f"go vet failed ({code})")

    # Tests
    code, out, err = _run_cmd(["go", "test", "./...", "-v"], cwd=str(root))
    exit_codes["go_test"] = code
    combined = (out + err).strip()
    output_captures["go_test"] = combined.splitlines()
    non_empty = [l for l in combined.splitlines() if l.strip()]
    summaries["go_test"] = non_empty[-1] if non_empty else "(no output)"
    if code == 0:
        _info("go test: ok")
    else:
        _err(f"go test failed ({code})")

    return runtime_path, ""


def _run_javascript_backend(
    root: Path,
    forge_cfg: dict,
    exit_codes: dict,
    summaries: dict,
    output_captures: dict,
) -> tuple[str, str]:
    """Run JavaScript npm test."""
    runtime_path = "node"
    _info("Runtime: Node.js")

    framework = forge_cfg.get("backend", {}).get("test_framework", "jest")
    code, out, err = _run_cmd(["npm", "test"], cwd=str(root))
    exit_codes[framework] = code
    combined = (out + err).strip()
    output_captures[framework] = combined.splitlines()
    non_empty = [l for l in combined.splitlines() if l.strip()]
    summaries[framework] = non_empty[-1] if non_empty else "(no output)"
    if code == 0:
        _info(f"{framework}: ok")
    else:
        _err(f"{framework} failed ({code})")

    return runtime_path, ""


def _run_frontend(
    root: Path,
    forge_cfg: dict,
    exit_codes: dict,
    summaries: dict,
    output_captures: dict,
) -> None:
    """Run frontend build + tests if enabled."""
    frontend = forge_cfg.get("frontend", {})
    if not frontend.get("enabled", False):
        _info("Frontend disabled in forge.json; skipping frontend tests.")
        return

    frontend_dir = root / frontend.get("dir", "web")
    _info(f"Frontend: {frontend_dir}")

    build_cmd = frontend.get("build_cmd", "")
    if build_cmd:
        _info(f"Running frontend build: {build_cmd}")
        parts = build_cmd.split()
        script_name = parts[-1] if len(parts) > 1 else build_cmd
        code, out, err = _run_cmd(
            ["npm", "--prefix", str(frontend_dir), "run", script_name],
            cwd=str(root),
        )
        exit_codes["frontend_build"] = code
        output_captures["frontend_build"] = (out + err).splitlines()
        summaries["frontend_build"] = "ok" if code == 0 else f"FAIL (exit {code})"
        if code == 0:
            _info("frontend build: ok")
        else:
            _err(f"frontend build failed ({code})")

    test_cmd = frontend.get("test_cmd", "")
    if test_cmd:
        _info(f"Running frontend tests: {test_cmd}")
        parts = test_cmd.split()
        if parts[0] == "npm":
            cmd = ["npm", "--prefix", str(frontend_dir)] + parts[1:]
        else:
            cmd = parts
        code, out, err = _run_cmd(cmd, cwd=str(root))
        exit_codes["frontend_test"] = code
        combined = (out + err).strip()
        output_captures["frontend_test"] = combined.splitlines()
        non_empty = [l for l in combined.splitlines() if l.strip()]
        summaries["frontend_test"] = non_empty[-1] if non_empty else "(no output)"
        if code == 0:
            _info("frontend tests: ok")
        else:
            _err(f"frontend tests failed ({code})")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description="Forge stack-aware test runner")
    parser.add_argument(
        "--scope",
        choices=["all", "backend", "frontend"],
        default="all",
    )
    parser.add_argument("--no-venv", action="store_true", default=False)
    args = parser.parse_args()

    # Governance root: parent of scripts/ dir
    script_dir = Path(__file__).resolve().parent
    gov_root = script_dir.parent

    # Project root: git root if available, else governance root
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, timeout=10,
        )
        root = Path(result.stdout.strip()) if result.returncode == 0 else gov_root
    except Exception:
        root = gov_root

    _load_dotenv(root)

    start_utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # Gather git info
    git_branch = _git("rev-parse", "--abbrev-ref", "HEAD")
    git_head = _git("rev-parse", "HEAD")
    git_status = _git("status", "-sb")
    git_diff_stat = _git("diff", "--stat")

    # Load forge.json
    forge_json_path = root / "forge.json"
    if forge_json_path.exists():
        forge_cfg = json.loads(forge_json_path.read_text(encoding="utf-8"))
        _info(f"forge.json loaded: {forge_cfg.get('project_name', '?')} ({forge_cfg.get('backend', {}).get('language', '?')})")
    else:
        _info("forge.json not found. Using defaults (Python).")
        forge_cfg = {
            "project_name": "unknown",
            "backend": {
                "language": "python",
                "entry_module": "app.main",
                "test_framework": "pytest",
                "test_dir": "tests",
                "dependency_file": "requirements.txt",
                "venv_path": ".venv",
            },
            "frontend": {"enabled": False, "dir": "web"},
        }

    exit_codes: dict[str, int] = {}
    summaries: dict[str, str] = {}
    output_captures: dict[str, list[str]] = {}
    runtime_path = ""
    failing_tests = ""
    failure_payload = ""

    lang = forge_cfg.get("backend", {}).get("language", "python")

    # Backend tests
    if args.scope in ("all", "backend"):
        try:
            if lang == "python":
                runtime_path, failing_tests = _run_python_backend(
                    root, forge_cfg, args.no_venv, exit_codes, summaries, output_captures
                )
            elif lang == "typescript":
                runtime_path, failing_tests = _run_typescript_backend(
                    root, forge_cfg, exit_codes, summaries, output_captures
                )
            elif lang == "go":
                runtime_path, failing_tests = _run_go_backend(
                    root, exit_codes, summaries, output_captures
                )
            elif lang == "javascript":
                runtime_path, failing_tests = _run_javascript_backend(
                    root, forge_cfg, exit_codes, summaries, output_captures
                )
            else:
                _info(f"Unknown backend language: {lang}. Skipping backend tests.")
                runtime_path = "unknown"
        except Exception as exc:
            _err(f"Backend test runner error: {exc}")
            exit_codes["backend_error"] = 1
            summaries["backend_error"] = str(exc)
    else:
        _info(f"Skipping backend tests (scope={args.scope})")

    # Frontend tests
    if args.scope in ("all", "frontend"):
        try:
            _run_frontend(root, forge_cfg, exit_codes, summaries, output_captures)
        except Exception as exc:
            _err(f"Frontend test runner error: {exc}")
            exit_codes["frontend_error"] = 1

    end_utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # Determine overall status
    if not exit_codes:
        overall = 1
        summaries["note"] = "No test phases executed"
    else:
        overall = 0
        for v in exit_codes.values():
            if v != 0:
                overall = 1
                break

    status_text = "PASS" if overall == 0 else "FAIL"

    # Build failure payload (last 200 lines of failing outputs)
    if status_text == "FAIL":
        sections = []
        for k, v in exit_codes.items():
            if v != 0 and k in output_captures:
                lines = output_captures[k]
                tail = lines[-200:] if len(lines) > 200 else lines
                sections.append(f"=== {k} (exit {v}) ===")
                sections.extend(tail)
        failure_payload = "\n".join(sections).strip()

    # Write evidence
    _append_test_run_log(
        gov_root, status_text, runtime_path, start_utc, end_utc,
        exit_codes, summaries, git_branch, git_head, git_status, git_diff_stat,
        failure_payload,
    )
    _write_test_run_latest(
        gov_root, status_text, runtime_path, start_utc, end_utc,
        exit_codes, summaries, failing_tests, git_branch, git_head,
        git_status, git_diff_stat, failure_payload,
    )

    _info(f"Status: {status_text}")
    return overall


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        _err(f"SCRIPT ERROR: {exc}")
        import traceback
        traceback.print_exc()
        sys.exit(2)
