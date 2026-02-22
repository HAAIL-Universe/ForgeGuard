#!/usr/bin/env python3
"""boot.py -- ForgeGuard one-click setup and run script.

Brings up the full stack from a fresh clone:
  1. Validates prerequisites (Python 3.12+, Node 18+, psql)
  2. Creates Python venv and installs backend deps
  3. Installs frontend deps
  4. Validates .env (fails fast if missing required vars)
  5. Runs database migrations
  6. Starts backend + frontend dev servers

Usage:
    python boot.py
    python boot.py --skip-frontend
    python boot.py --migrate-only
    python boot.py --test-only       # run pytest then exit
    python boot.py --check           # ruff + mypy then exit
"""
from __future__ import annotations

import argparse
import os
import re
import socket
import subprocess
import sys
import time
from pathlib import Path
from threading import Thread


# ---------------------------------------------------------------------------
# Coloured output
# ---------------------------------------------------------------------------

def _enable_ansi() -> bool:
    """Enable VT100 ANSI escape processing on Windows 10+."""
    if os.name != "nt":
        return True
    try:
        import ctypes
        kernel = ctypes.windll.kernel32
        # ENABLE_VIRTUAL_TERMINAL_PROCESSING = 0x0004
        handle = kernel.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
        mode = ctypes.c_ulong()
        kernel.GetConsoleMode(handle, ctypes.byref(mode))
        kernel.SetConsoleMode(handle, mode.value | 0x0004)
        return True
    except Exception:
        return False


_ANSI_OK = _enable_ansi()


def _c(text: str, code: str) -> str:
    return f"\033[{code}m{text}\033[0m" if _ANSI_OK else text


def info(msg: str) -> None:
    print(_c(f"[boot] {msg}", "36"))   # cyan


def warn(msg: str) -> None:
    print(_c(f"[boot] {msg}", "33"))   # yellow


def err(msg: str) -> None:
    print(_c(f"[boot] {msg}", "31"), file=sys.stderr)  # red


def ok(msg: str) -> None:
    print(_c(f"[boot] {msg}", "32"))   # green


# ---------------------------------------------------------------------------
# Root directory
# ---------------------------------------------------------------------------

ROOT = Path(__file__).parent.resolve()


# ---------------------------------------------------------------------------
# Prerequisite checks
# ---------------------------------------------------------------------------

def find_python() -> str:
    """Return a Python 3.12+ executable path, or exit."""
    for candidate in ("python3", "python", sys.executable):
        try:
            result = subprocess.run(
                [candidate, "--version"],
                capture_output=True, text=True,
            )
            m = re.search(r"Python 3\.(\d+)", result.stdout + result.stderr)
            if m and int(m.group(1)) >= 12:
                info(f"Found Python 3.{m.group(1)}")
                return candidate
        except FileNotFoundError:
            continue
    err("Python 3.12+ is required but was not found.")
    sys.exit(1)


def check_node() -> None:
    result = subprocess.run(["node", "--version"], capture_output=True, text=True)
    if result.returncode != 0:
        err("Node.js 18+ is required for frontend. Use --skip-frontend to skip.")
        sys.exit(1)
    info(f"Node: {result.stdout.strip()}")


def check_psql() -> bool:
    result = subprocess.run(["psql", "--version"], capture_output=True)
    if result.returncode == 0:
        info("psql: found on PATH")
        return True
    warn("psql not on PATH -- you may need to run migrations manually.")
    return False


# ---------------------------------------------------------------------------
# Virtual environment
# ---------------------------------------------------------------------------

def setup_venv(python_cmd: str) -> str:
    """Create .venv if it doesn't exist; return path to venv Python executable."""
    venv_dir = ROOT / ".venv"
    if not venv_dir.exists():
        info("Creating virtual environment...")
        subprocess.run([python_cmd, "-m", "venv", str(venv_dir)], check=True)
        ok("Virtual environment created.")
    else:
        info("Virtual environment already exists.")

    for candidate in (
        venv_dir / "Scripts" / "python.exe",   # Windows
        venv_dir / "bin" / "python",            # Unix/macOS
    ):
        if candidate.exists():
            return str(candidate)
    return python_cmd


def install_deps(venv_python: str) -> None:
    info("Installing Python dependencies...")
    subprocess.run(
        [venv_python, "-m", "pip", "install", "-r", str(ROOT / "requirements.txt"), "--quiet"],
        check=True,
    )
    ok("Backend dependencies installed.")


# ---------------------------------------------------------------------------
# .env validation
# ---------------------------------------------------------------------------

_REQUIRED_VARS = [
    "DATABASE_URL",
    "GITHUB_CLIENT_ID",
    "GITHUB_CLIENT_SECRET",
    "GITHUB_WEBHOOK_SECRET",
    "JWT_SECRET",
]


def validate_env() -> None:
    env_file = ROOT / ".env"
    env_example = ROOT / ".env.example"

    if not env_file.exists():
        if env_example.exists():
            import shutil
            shutil.copy(env_example, env_file)
            warn(".env created from .env.example -- fill in your secrets before continuing.")
        else:
            err("No .env file found. Create one with the required variables.")
            err(f"Required: {', '.join(_REQUIRED_VARS)}")
            sys.exit(1)

    content = env_file.read_text(encoding="utf-8")
    missing = [
        v for v in _REQUIRED_VARS
        if not re.search(rf"^{v}\s*=\s*.+", content, re.MULTILINE)
    ]
    if missing:
        err(f"Missing or empty vars in .env: {', '.join(missing)}")
        err("Edit .env and fill in these values, then re-run boot.py.")
        sys.exit(1)

    ok(".env validated -- all required variables present.")


def _get_db_url() -> str:
    content = (ROOT / ".env").read_text(encoding="utf-8")
    m = re.search(r'^DATABASE_URL\s*=\s*(.+)', content, re.MULTILINE)
    return m.group(1).strip().strip('"').strip("'") if m else ""


# ---------------------------------------------------------------------------
# Database migrations
# ---------------------------------------------------------------------------

_INLINE_MIGRATE = """\
import sys, asyncio, asyncpg

async def main():
    url, files = sys.argv[1], sys.argv[2:]
    conn = await asyncpg.connect(url)
    fail = 0
    for f in files:
        try:
            await conn.execute(open(f, encoding='utf-8').read())
        except Exception as e:
            print(f'  [warn] {f}: {e}')
            fail += 1
    await conn.close()
    if fail:
        print(f'{fail} migration(s) may have already been applied.')
    else:
        print(f'All {len(files)} migrations applied.')

asyncio.run(main())
"""


def run_migrations(venv_python: str) -> None:
    migration_dir = ROOT / "db" / "migrations"
    migration_files = sorted(migration_dir.glob("*.sql")) if migration_dir.exists() else []

    if not migration_files:
        warn("No migration files found in db/migrations/")
        return

    info(f"Running database migrations ({len(migration_files)} files)...")
    db_url = _get_db_url()
    psql_ok = subprocess.run(["psql", "--version"], capture_output=True).returncode == 0

    if db_url and psql_ok:
        fail_count = sum(
            1
            for mf in migration_files
            if subprocess.run(["psql", db_url, "-f", str(mf)], capture_output=True).returncode != 0
        )
        if fail_count == 0:
            ok(f"All {len(migration_files)} migrations applied.")
        else:
            warn(f"{fail_count} migration(s) may have already been applied.")

    elif db_url:
        info("psql not found -- running migrations via Python...")
        py_migrate = ROOT / "_migrate.py"
        args = [venv_python, str(py_migrate) if py_migrate.exists() else "-c", db_url,
                *[str(f) for f in migration_files]]
        if not py_migrate.exists():
            args = [venv_python, "-c", _INLINE_MIGRATE, db_url, *[str(f) for f in migration_files]]
        r = subprocess.run(args, check=False)
        if r.returncode == 0:
            ok("All migrations applied via Python.")
        else:
            warn("Some migrations may have failed -- check output above.")
    else:
        warn("Cannot run migrations -- DATABASE_URL not found in .env")


# ---------------------------------------------------------------------------
# Frontend
# ---------------------------------------------------------------------------

def install_frontend() -> None:
    web_dir = ROOT / "web"
    if not web_dir.exists():
        return
    info("Installing frontend dependencies...")
    subprocess.run(["npm", "install", "--silent"], cwd=str(web_dir), check=True)
    ok("Frontend dependencies installed.")


def start_frontend() -> subprocess.Popen:
    web_dir = ROOT / "web"
    info("Starting frontend dev server on port 5174...")
    proc = subprocess.Popen(
        ["npm", "run", "dev"],
        cwd=str(web_dir),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    info(f"Frontend started (PID {proc.pid}).")
    return proc


# ---------------------------------------------------------------------------
# Misc helpers
# ---------------------------------------------------------------------------

def _check_port_8000() -> None:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.3)
            if s.connect_ex(("127.0.0.1", 8000)) == 0:
                warn("Port 8000 is already in use.")
                warn("This usually means a previous server session is still running.")
                warn("Stop it before running boot.py, or the new server may not start.")
    except Exception:
        pass


def _open_browser_delayed() -> None:
    def _open():
        time.sleep(3)
        import webbrowser
        webbrowser.open("http://localhost:5174")
    Thread(target=_open, daemon=True).start()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        prog="boot.py",
        description="ForgeGuard one-click setup and run script.",
    )
    parser.add_argument("--skip-frontend", action="store_true",
                        help="Skip frontend setup and server.")
    parser.add_argument("--migrate-only", action="store_true",
                        help="Run migrations then exit.")
    parser.add_argument("--test-only", action="store_true",
                        help="Run pytest then exit.")
    parser.add_argument("--check", action="store_true",
                        help="Run ruff + mypy then exit.")
    args = parser.parse_args()

    # -- 1. Prerequisites --------------------------------------------------
    info("Checking prerequisites...")
    python_cmd = find_python()

    if not args.skip_frontend:
        check_node()

    check_psql()

    # -- 2. Virtual environment --------------------------------------------
    venv_python = setup_venv(python_cmd)

    # -- 3. Backend dependencies -------------------------------------------
    install_deps(venv_python)

    # -- 4. .env validation ------------------------------------------------
    validate_env()

    # -- 5. Database migrations --------------------------------------------
    run_migrations(venv_python)

    if args.migrate_only:
        ok("Migration complete. Exiting.")
        return

    # -- 5b. --test-only ---------------------------------------------------
    if args.test_only:
        info("Running test suite...")
        result = subprocess.run(
            [venv_python, "-m", "pytest", "tests/", "-q"], cwd=str(ROOT),
        )
        if result.returncode == 0:
            ok("All tests passed.")
        else:
            err(f"Tests failed (exit code {result.returncode}).")
        sys.exit(result.returncode)

    # -- 5c. --check -------------------------------------------------------
    if args.check:
        failed = False
        for cmd_suffix in [
            ["ruff", "check", "."],
            ["ruff", "format", "--check", "."],
            ["mypy", "app/", "--ignore-missing-imports"],
        ]:
            cmd = [venv_python, "-m"] + cmd_suffix
            info(f"Running {' '.join(cmd_suffix)}...")
            r = subprocess.run(cmd, cwd=str(ROOT))
            if r.returncode != 0:
                failed = True
        if failed:
            err("Quality checks failed.")
            sys.exit(1)
        ok("All quality checks passed.")
        return

    # -- 6. Frontend setup -------------------------------------------------
    if not args.skip_frontend:
        install_frontend()

    # -- 7. Start servers --------------------------------------------------
    info("")
    info("Starting ForgeGuard...")

    frontend_proc = None
    if not args.skip_frontend and (ROOT / "web").exists():
        frontend_proc = start_frontend()

    _check_port_8000()
    _open_browser_delayed()

    info("Starting backend server on port 8000...")
    info("Press Ctrl+C to stop.")

    try:
        subprocess.run(
            [venv_python, "-m", "uvicorn", "app.main:app",
             "--host", "0.0.0.0", "--port", "8000",
             "--reload", "--reload-dir", "app"],
            cwd=str(ROOT),
        )
    except KeyboardInterrupt:
        ok("Shutting down...")
    finally:
        if frontend_proc:
            frontend_proc.terminate()


if __name__ == "__main__":
    main()
