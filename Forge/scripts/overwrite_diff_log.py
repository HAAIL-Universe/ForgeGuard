"""overwrite_diff_log.py — Overwrite the diff log with a structured Markdown entry.

Python port of overwrite_diff_log.ps1. Writes Forge/evidence/diff_log.md
with the current git state and user-supplied section content.

Usage:
    python Forge/scripts/overwrite_diff_log.py --status IN_PROCESS
    python Forge/scripts/overwrite_diff_log.py --status COMPLETE \\
        --summary "Did X" "Did Y" \\
        --verification "compileall: pass" "pytest: pass" \\
        --notes "None" \\
        --next-steps "Next: do Z"
    python Forge/scripts/overwrite_diff_log.py --finalize
    python Forge/scripts/overwrite_diff_log.py --include-unstaged

Exit codes:
    0 -- Success.
    1 -- Validation failure (finalize: TODO found; COMPLETE: missing sections).
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _info(msg: str) -> None:
    print(f"[overwrite_diff_log] {msg}", flush=True)


def _warn(msg: str) -> None:
    print(f"[overwrite_diff_log] WARN: {msg}", flush=True)


def _err(msg: str) -> None:
    print(f"[overwrite_diff_log] ERROR: {msg}", file=sys.stderr, flush=True)


def _git(*args: str) -> tuple[int, str]:
    """Run git command. Returns (exit_code, output)."""
    try:
        r = subprocess.run(["git", *args], capture_output=True, text=True, timeout=15)
        return r.returncode, (r.stdout + r.stderr).strip()
    except FileNotFoundError:
        return 127, "git not found on PATH"


def _require_git() -> str:
    """Return repo root or raise RuntimeError."""
    code, out = _git("rev-parse", "--is-inside-work-tree")
    if code != 0 or out.strip() != "true":
        raise RuntimeError("Not inside a git repo. Run from the repo (or a subdir).")
    _, root = _git("rev-parse", "--show-toplevel")
    return root.strip()


def _bullets(items: list[str], todo: str) -> list[str]:
    if not items:
        return [f"- {todo}"]
    return [f"- {item}" for item in items]


def _indent4(lines: list[str]) -> list[str]:
    if not lines:
        return ["    (none)"]
    return [f"    {l}" for l in lines]


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description="Overwrite Forge diff_log.md with current git state")
    parser.add_argument(
        "--status",
        choices=["IN_PROCESS", "COMPLETE", "BLOCKED"],
        default="IN_PROCESS",
    )
    parser.add_argument("--summary", nargs="*", default=[], metavar="LINE")
    parser.add_argument("--verification", nargs="*", default=[], metavar="LINE")
    parser.add_argument("--notes", nargs="*", default=[], metavar="LINE")
    parser.add_argument("--next-steps", nargs="*", default=[], metavar="LINE")
    parser.add_argument(
        "--finalize", action="store_true",
        help="Check for remaining TODO: placeholders and exit 1 if found",
    )
    parser.add_argument(
        "--include-unstaged", action="store_true",
        help="Include unstaged (working tree) changes instead of staged only",
    )
    args = parser.parse_args()

    try:
        root = Path(_require_git())
    except RuntimeError as exc:
        _err(str(exc))
        return 1

    # Governance root: parent of scripts/ dir
    script_dir = Path(__file__).resolve().parent
    gov_root = script_dir.parent

    log_path = gov_root / "evidence" / "diff_log.md"
    log_path.parent.mkdir(parents=True, exist_ok=True)

    # ── Finalize mode ────────────────────────────────────────────────────────
    if args.finalize:
        if not log_path.exists():
            _err(f"Finalize failed: evidence/diff_log.md not found at {log_path}")
            return 1
        content = log_path.read_text(encoding="utf-8")
        # Only scan header (above Minimal Diff Hunks) to avoid false positives from git diff
        hunks_idx = content.find("## Minimal Diff Hunks")
        header = content[:hunks_idx] if hunks_idx >= 0 else content
        todo_matches = re.findall(r"TODO:", header, re.IGNORECASE)
        if todo_matches:
            _err(f"Finalize failed: {len(todo_matches)} TODO placeholder(s) remain in diff log header.")
            return 1
        _info("Finalize passed: no TODO placeholders found in header.")
        return 0

    # ── COMPLETE validation ──────────────────────────────────────────────────
    if args.status == "COMPLETE":
        missing = []
        if not args.summary:
            missing.append("--summary")
        if not args.verification:
            missing.append("--verification")
        if not args.notes:
            missing.append("--notes")
        if not args.next_steps:
            missing.append("--next-steps")
        if missing:
            _err(f"Status is COMPLETE but required parameters are empty: {', '.join(missing)}")
            _err("A COMPLETE diff log cannot contain TODO: placeholders. Supply all section parameters.")
            return 1

    # ── Gather git info ──────────────────────────────────────────────────────
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    _, branch = _git("rev-parse", "--abbrev-ref", "HEAD")
    _, head = _git("rev-parse", "HEAD")
    _, base_head_raw = _git("rev-parse", "HEAD^")
    base_head = base_head_raw if "^" not in base_head_raw and base_head_raw else ""
    base_head_label = base_head if base_head else "N/A (no parent)"

    staged_only = not args.include_unstaged
    basis = "staged" if staged_only else "unstaged (working tree)"

    # Get changed files
    if staged_only:
        _, files_raw = _git("diff", "--name-only", "--staged")
    else:
        _, files_raw = _git("diff", "--name-only")
    changed_files = [f.strip() for f in files_raw.splitlines() if f.strip()]

    if staged_only and not changed_files:
        _warn("No staged changes found. Recommended: git add <scoped files> then re-run.")
        _warn("If you truly want unstaged, re-run with --include-unstaged.")

    # Get diff patch
    if staged_only:
        _, patch_raw = _git("diff", "--unified=3", "--staged")
    else:
        _, patch_raw = _git("diff", "--unified=3")
    patch_lines = patch_raw.splitlines()

    # Git status
    _, status_raw = _git("status", "-sb")
    status_lines = status_raw.splitlines()

    # ── Build content ────────────────────────────────────────────────────────
    summary_todo = "TODO: 1-5 bullets (what changed, why, scope)."
    verification_todo = "TODO: verification evidence (static -> runtime -> behavior -> contract)."
    notes_todo = "TODO: blockers, risks, constraints."
    next_steps_todo = "TODO: next actions (small, specific)."

    summary_lines = _bullets(args.summary, summary_todo)
    verification_lines = _bullets(args.verification, verification_todo)
    notes_lines = _bullets(args.notes, notes_todo)
    next_steps_lines = _bullets(args.next_steps, next_steps_todo)
    files_lines = [f"- {f}" for f in changed_files] if changed_files else ["- (none detected)"]
    status_indented = _indent4(status_lines)
    patch_indented = _indent4(patch_lines)

    out: list[str] = [
        "# Diff Log (overwrite each cycle)",
        "",
        "## Cycle Metadata",
        f"- Timestamp: {timestamp}",
        f"- Branch: {branch}",
        f"- HEAD: {head}",
        f"- BASE_HEAD: {base_head_label}",
        f"- Diff basis: {basis}",
        "",
        "## Cycle Status",
        f"- Status: {args.status}",
        "",
        "## Summary",
        *summary_lines,
        "",
        f"## Files Changed ({basis})",
        *files_lines,
        "",
        "## git status -sb",
        *status_indented,
        "",
        "## Verification",
        *verification_lines,
        "",
        "## Notes (optional)",
        *notes_lines,
        "",
        "## Next Steps",
        *next_steps_lines,
        "",
        "## Minimal Diff Hunks",
        *patch_indented,
        "",
    ]

    log_path.write_text("\n".join(out), encoding="utf-8")
    _info(f"Wrote diff log (overwritten): {log_path}")
    _info(f"Files listed: {len(changed_files)}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        _err(str(exc))
        sys.exit(1)
