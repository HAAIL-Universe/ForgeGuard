"""watch_audit.py — Forge AEM audit watcher (Python port of watch_audit.ps1).

Polls Forge/evidence/diff_log.md for changes and auto-triggers run_audit.py
when Status: COMPLETE is detected.

Usage:
    python Forge/scripts/watch_audit.py
    python Forge/scripts/watch_audit.py --trigger diff_log.md
    python Forge/scripts/watch_audit.py --poll-interval 5
    python Forge/scripts/watch_audit.py --dry-run

What it does:
  1. Watches Forge/evidence/diff_log.md for file modification.
  2. When the file changes and contains 'Status: COMPLETE', it:
     a. Parses claimed files from Files Changed/Created/Modified tables.
     b. Parses the phase identifier from the file header.
     c. Runs run_audit.py with those parameters.
     d. Logs the result with a timestamp.
  3. Debounces rapid writes (2s default) to avoid duplicate triggers.
  4. Runs indefinitely until Ctrl+C.

No external dependencies beyond Python stdlib.
"""

from __future__ import annotations

import argparse
import os
import re
import signal
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ts() -> str:
    return datetime.now().strftime("%H:%M:%S")


def _info(msg: str) -> None:
    print(f"[watch] {_ts()} {msg}", flush=True)


def _warn(msg: str) -> None:
    print(f"[watch] {_ts()} WARN: {msg}", flush=True)


def _good(msg: str) -> None:
    print(f"[watch] {_ts()} OK: {msg}", flush=True)


def _bad(msg: str) -> None:
    print(f"[watch] {_ts()} FAIL: {msg}", file=sys.stderr, flush=True)


# ---------------------------------------------------------------------------
# Diff log parsing
# ---------------------------------------------------------------------------

def _parse_files_from_diff_log(diff_log_path: Path) -> list[str]:
    """Extract file list from ## Files Changed/Created/Modified sections."""
    if not diff_log_path.exists():
        return []

    files: list[str] = []
    in_section = False

    for line in diff_log_path.read_text(encoding="utf-8", errors="replace").splitlines():
        # Section header detection
        if re.match(r"^\s*##\s+Files\s+(Changed|Created|Modified)", line, re.IGNORECASE):
            in_section = True
            continue

        if in_section:
            # Hit next section
            if re.match(r"^\s*##\s", line):
                in_section = False
                continue
            # Skip table header/separator rows
            if re.match(r"^\s*\|\s*(File|-{4})", line, re.IGNORECASE):
                continue
            # Bullet list: - filename
            m = re.match(r"^\s*-\s+(.+)$", line)
            if m:
                f = m.group(1).strip()
                if f not in ("(none detected)", "(none)"):
                    files.append(f)
                continue
            # Table row: | filename | desc |
            m = re.match(r"^\s*\|\s*([^|]+)\s*\|", line)
            if m:
                f = m.group(1).strip()
                if f and f not in ("File", "----") and not re.match(r"^-+$", f):
                    files.append(f)

    return files


def _parse_phase_from_diff_log(diff_log_path: Path) -> str:
    """Extract phase identifier from diff_log.md."""
    if not diff_log_path.exists():
        return "unknown"
    content = diff_log_path.read_text(encoding="utf-8", errors="replace")
    m = re.search(r"(?i)Phase\s+(\d+)\s*[-–—:]?\s*([^\r\n]*)", content)
    if m:
        num = m.group(1)
        name = m.group(2).strip()
        return f"Phase {num} -- {name}" if name else f"Phase {num}"
    return "unknown"


def _is_complete(diff_log_path: Path) -> bool:
    """Check if diff_log.md has Status: COMPLETE."""
    if not diff_log_path.exists():
        return False
    content = diff_log_path.read_text(encoding="utf-8", errors="replace")
    return bool(re.search(r"(?i)Status:\s*COMPLETE", content))


# ---------------------------------------------------------------------------
# Single-instance guard
# ---------------------------------------------------------------------------

def _lock_path(gov_root: Path) -> Path:
    return gov_root.parent / ".forge_watcher.lock"


def _acquire_lock(gov_root: Path) -> bool:
    """Write PID lock file. Return False if another live instance holds the lock."""
    lock = _lock_path(gov_root)
    if lock.exists():
        try:
            pid_str = lock.read_text().strip()
            pid = int(pid_str)
            # Check if PID is alive
            if pid != os.getpid():
                try:
                    os.kill(pid, 0)
                    _info(f"Another watcher is already running (PID {pid}). Exiting.")
                    return False
                except OSError:
                    _info(f"Removing stale lock (PID {pid} is dead).")
        except Exception:
            pass
    lock.write_text(str(os.getpid()), encoding="utf-8")
    return True


def _release_lock(gov_root: Path) -> None:
    lock = _lock_path(gov_root)
    try:
        if lock.exists():
            content = lock.read_text().strip()
            if content == str(os.getpid()):
                lock.unlink()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Audit runner
# ---------------------------------------------------------------------------

def _run_audit(
    script_dir: Path,
    claimed_files: list[str],
    phase: str,
    dry_run: bool,
) -> int:
    """Invoke run_audit.py with the given files. Returns exit code."""
    audit_script = script_dir / "run_audit.py"
    if not audit_script.exists():
        _bad(f"run_audit.py not found at: {audit_script}")
        return 1

    files_str = ",".join(claimed_files)
    cmd = [sys.executable, str(audit_script), "--claimed-files", files_str, "--phase", phase]

    if dry_run:
        _good(f"DRY RUN -- would run: {' '.join(cmd)}")
        return 0

    try:
        result = subprocess.run(cmd, capture_output=False)
        return result.returncode
    except Exception as exc:
        _bad(f"Error running audit: {exc}")
        return 1


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description="Forge AEM audit watcher")
    parser.add_argument("--trigger", default="diff_log.md", help="File to watch")
    parser.add_argument(
        "--poll-interval", type=float, default=5.0,
        help="Polling interval in seconds (default: 5)",
    )
    parser.add_argument(
        "--debounce", type=float, default=2.0,
        help="Debounce interval in seconds (default: 2)",
    )
    parser.add_argument("--dry-run", action="store_true", help="Parse only, do not run audit")
    args = parser.parse_args()

    script_dir = Path(__file__).resolve().parent
    gov_root = script_dir.parent
    evidence_dir = gov_root / "evidence"
    diff_log = evidence_dir / args.trigger

    # Ensure evidence dir exists
    evidence_dir.mkdir(parents=True, exist_ok=True)

    # Single-instance guard
    if not _acquire_lock(gov_root):
        return 0

    audit_count = pass_count = fail_count = 0
    last_trigger_time = 0.0
    last_mtime: float | None = None

    def _shutdown(signum=None, frame=None) -> None:
        print("", flush=True)
        print("  === FORGE AUDIT WATCHER -- STOPPED ===", flush=True)
        print(f"  Total audits:  {audit_count}", flush=True)
        print(f"  Passed:        {pass_count}", flush=True)
        print(f"  Failed:        {fail_count}", flush=True)
        _release_lock(gov_root)
        sys.exit(0)

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    print("", flush=True)
    print("  === FORGE AUDIT WATCHER -- ACTIVE ===", flush=True)
    print(f"  Watching:  {diff_log}", flush=True)
    print(f"  Trigger:   {args.trigger}", flush=True)
    print(f"  Interval:  {args.poll_interval}s poll / {args.debounce}s debounce", flush=True)
    if args.dry_run:
        print("  Mode:      DRY RUN", flush=True)
    print("  Ctrl+C to stop.", flush=True)
    print("", flush=True)
    _info(f"Watcher ready. Polling for '{args.trigger}'...")

    try:
        while True:
            time.sleep(args.poll_interval)

            # Check if file exists and get mtime
            try:
                current_mtime = diff_log.stat().st_mtime if diff_log.exists() else None
            except Exception:
                current_mtime = None

            if current_mtime is None:
                last_mtime = None
                continue

            # Detect change
            if last_mtime is not None and current_mtime <= last_mtime:
                continue

            # Debounce
            now = time.time()
            if now - last_trigger_time < args.debounce:
                last_mtime = current_mtime
                continue

            last_mtime = current_mtime
            last_trigger_time = now

            _info(f"TRIGGER DETECTED: {args.trigger} changed")

            # Check for IN_PROCESS — skip if builder is mid-cycle
            if not _is_complete(diff_log):
                content = diff_log.read_text(encoding="utf-8", errors="replace") if diff_log.exists() else ""
                if re.search(r"(?i)Status:\s*IN_PROCESS", content):
                    _info("Diff log status is IN_PROCESS -- builder is mid-cycle. Skipping.")
                else:
                    _warn("diff_log.md not Status: COMPLETE yet. Waiting for next change.")
                continue

            # Parse files and phase
            claimed_files = _parse_files_from_diff_log(diff_log)
            phase = _parse_phase_from_diff_log(diff_log)

            if not claimed_files:
                _warn("Could not parse files from diff log. Skipping audit.")
                continue

            _info(f"Phase: {phase}")
            _info(f"Files: {', '.join(claimed_files)}")

            audit_count += 1
            _info(f"Running audit #{audit_count}...")
            exit_code = _run_audit(script_dir, claimed_files, phase, args.dry_run)

            if exit_code == 0:
                pass_count += 1
                _good(f"AUDIT #{audit_count} RESULT: ALL PASS [{pass_count} pass / {fail_count} fail total]")
            else:
                fail_count += 1
                _bad(f"AUDIT #{audit_count} RESULT: FAIL (exit {exit_code}) [{pass_count} pass / {fail_count} fail total]")

            print(f"  Audits: {audit_count}  Passed: {pass_count}  Failed: {fail_count}", flush=True)
            _info("Resuming watch...")

    except KeyboardInterrupt:
        _shutdown()

    return 0


if __name__ == "__main__":
    sys.exit(main())
