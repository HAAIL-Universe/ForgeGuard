"""Boot script generator — template-driven, no LLM required.

Generates platform-specific startup scripts per builder_contract §9.8.
Each script implements the 8-step setup-and-run sequence adapted to
the project's actual stack (Node, Python, multi-service).
"""

from __future__ import annotations

from .stack_resolver import StackInfo


def generate_boot_script(project_name: str, stack: StackInfo) -> tuple[str, str]:
    """Generate the appropriate boot script for the user's platform.

    Returns (filename, content) tuple.
    """
    if stack.dev_platform == "windows_cmd":
        return "boot.bat", generate_boot_bat(project_name, stack)
    elif stack.dev_platform == "windows_ps":
        return "boot.ps1", generate_boot_ps1(project_name, stack)
    else:
        return "boot.sh", generate_boot_sh(project_name, stack)


# ---------------------------------------------------------------------------
# Bash (macOS / Linux)
# ---------------------------------------------------------------------------


def generate_boot_sh(project_name: str, stack: StackInfo) -> str:
    """Generate boot.sh for macOS/Linux (bash)."""
    sections = [
        "#!/usr/bin/env bash",
        "set -e",
        f'echo "=== {project_name} — Setup & Run ==="',
        "",
    ]

    # Step 1: Check prerequisites
    sections.append("# Step 1: Check prerequisites")
    if stack.primary_language == "javascript":
        sections.append(f'command -v node >/dev/null 2>&1 || {{ echo "ERROR: Node.js {stack.node_version}+ is required but not installed. Download from https://nodejs.org"; exit 1; }}')
        sections.append(f'echo "  Node.js $(node --version) found"')
    elif stack.primary_language == "python":
        sections.append(f'command -v python3 >/dev/null 2>&1 || {{ echo "ERROR: Python {stack.python_version}+ is required but not installed. Download from https://python.org"; exit 1; }}')
        sections.append(f'echo "  Python $(python3 --version) found"')
    sections.append("")

    # Step 2: Create virtual environment (Python only)
    sections.append("# Step 2: Create virtual environment")
    if stack.primary_language == "python":
        sections.extend([
            'if [ ! -d ".venv" ]; then',
            '    echo "  Creating Python virtual environment..."',
            '    python3 -m venv .venv',
            'fi',
        ])
    else:
        sections.append("# (Skipped — not a Python project)")
    sections.append("")

    # Step 3: Activate environment
    sections.append("# Step 3: Activate environment")
    if stack.primary_language == "python":
        sections.append("source .venv/bin/activate")
        sections.append('echo "  Virtual environment activated"')
    else:
        sections.append("# (No virtual environment needed)")
    sections.append("")

    # Step 4: Install dependencies
    sections.append("# Step 4: Install dependencies")
    for cmd_info in stack.install_commands:
        d = cmd_info["dir"]
        cmd = cmd_info["cmd"]
        if d and d != ".":
            sections.append(f'echo "  Installing dependencies in {d}/..."')
            sections.append(f"(cd {d} && {cmd})")
        else:
            sections.append(f'echo "  Installing dependencies..."')
            sections.append(cmd)
    if not stack.install_commands:
        sections.append('echo "  No dependencies to install"')
    sections.append("")

    # Step 5: Prompt for required credentials
    sections.append("# Step 5: Check environment configuration")
    required_vars = [v for v in stack.env_vars if v.get("required")]
    if required_vars:
        sections.append("# Check if .env files exist; if not, copy from examples")
        _env_dirs = set()
        for v in stack.env_vars:
            _env_dirs.add(v.get("dir", "root"))
        for d in sorted(_env_dirs):
            actual_dir = "." if d == "root" else d
            sections.append(f'if [ ! -f "{actual_dir}/.env" ] && [ -f "{actual_dir}/.env.example" ]; then')
            sections.append(f'    cp "{actual_dir}/.env.example" "{actual_dir}/.env"')
            sections.append(f'    echo "  Created {actual_dir}/.env from example (review and update values)"')
            sections.append("fi")
    else:
        sections.append("# No required credentials detected")
    sections.append("")

    # Step 6: Write .env (handled in step 5 via copy)
    sections.append("# Step 6: Environment file ready")
    sections.append('echo "  Environment configured"')
    sections.append("")

    # Step 7: Run migrations
    sections.append("# Step 7: Run migrations")
    if stack.database and stack.database != "sqlite":
        sections.append('echo "  Running database migrations..."')
        if stack.primary_language == "python":
            sections.append("python -m alembic upgrade head 2>/dev/null || echo '  (No migrations configured)'")
        else:
            sections.append('echo "  (Database will be initialized on first run)"')
    else:
        sections.append("# SQLite / no database — auto-initialized on first run")
    sections.append("")

    # Step 8: Start the app
    sections.append("# Step 8: Start the app")
    sections.append('echo ""')
    sections.append(f'echo "=== Starting {project_name} ==="')
    if len(stack.run_commands) > 1:
        # Multi-service: start backend in background, frontend in foreground
        items = list(stack.run_commands.items())
        for i, (svc_dir, cmd) in enumerate(items):
            if i < len(items) - 1:
                sections.append(f'echo "  Starting {svc_dir}..."')
                sections.append(f"(cd {svc_dir} && {cmd}) &")
            else:
                sections.append(f'echo "  Starting {svc_dir}..."')
                sections.append(f"cd {svc_dir} && {cmd}")
    elif stack.run_commands:
        svc_dir, cmd = next(iter(stack.run_commands.items()))
        if svc_dir and svc_dir != ".":
            sections.append(f"cd {svc_dir} && {cmd}")
        else:
            sections.append(cmd)
    else:
        if stack.primary_language == "python":
            sections.append("python -m app.main")
        else:
            sections.append("npm start")

    return "\n".join(sections) + "\n"


# ---------------------------------------------------------------------------
# Windows CMD
# ---------------------------------------------------------------------------


def generate_boot_bat(project_name: str, stack: StackInfo) -> str:
    """Generate boot.bat for Windows CMD."""
    sections = [
        "@echo off",
        f'echo === {project_name} — Setup ^& Run ===',
        "",
    ]

    # Step 1: Check prerequisites
    sections.append("REM Step 1: Check prerequisites")
    if stack.primary_language == "javascript":
        sections.extend([
            "where node >nul 2>nul",
            f'if errorlevel 1 (echo ERROR: Node.js {stack.node_version}+ is required. Download from https://nodejs.org & exit /b 1)',
            "for /f %%v in ('node --version') do echo   Node.js %%v found",
        ])
    elif stack.primary_language == "python":
        sections.extend([
            "where python >nul 2>nul",
            f'if errorlevel 1 (echo ERROR: Python {stack.python_version}+ is required. Download from https://python.org & exit /b 1)',
            "for /f %%v in ('python --version') do echo   %%v found",
        ])
    sections.append("")

    # Step 2: Create virtual environment
    sections.append("REM Step 2: Create virtual environment")
    if stack.primary_language == "python":
        sections.extend([
            'if not exist ".venv" (',
            "    echo   Creating Python virtual environment...",
            "    python -m venv .venv",
            ")",
        ])
    else:
        sections.append("REM (Skipped — not a Python project)")
    sections.append("")

    # Step 3: Activate environment
    sections.append("REM Step 3: Activate environment")
    if stack.primary_language == "python":
        sections.append("call .venv\\Scripts\\activate.bat")
        sections.append("echo   Virtual environment activated")
    sections.append("")

    # Step 4: Install dependencies
    sections.append("REM Step 4: Install dependencies")
    for cmd_info in stack.install_commands:
        d = cmd_info["dir"]
        cmd = cmd_info["cmd"]
        if d and d != ".":
            sections.append(f"echo   Installing dependencies in {d}\\...")
            sections.append(f"pushd {d}")
            sections.append(cmd)
            sections.append("popd")
        else:
            sections.append("echo   Installing dependencies...")
            sections.append(cmd)
    sections.append("")

    # Step 5: Check environment
    sections.append("REM Step 5: Check environment configuration")
    _env_dirs = set()
    for v in stack.env_vars:
        _env_dirs.add(v.get("dir", "root"))
    for d in sorted(_env_dirs):
        actual_dir = "." if d == "root" else d
        sections.append(f'if not exist "{actual_dir}\\.env" if exist "{actual_dir}\\.env.example" (')
        sections.append(f'    copy "{actual_dir}\\.env.example" "{actual_dir}\\.env" >nul')
        sections.append(f"    echo   Created {actual_dir}\\.env from example")
        sections.append(")")
    sections.append("")

    # Step 6: Environment ready
    sections.append("REM Step 6: Environment file ready")
    sections.append("echo   Environment configured")
    sections.append("")

    # Step 7: Migrations
    sections.append("REM Step 7: Run migrations")
    if stack.database and stack.database != "sqlite":
        sections.append("echo   Running database migrations...")
        if stack.primary_language == "python":
            sections.append("python -m alembic upgrade head 2>nul || echo   (No migrations configured)")
    else:
        sections.append("REM SQLite / no database — auto-initialized on first run")
    sections.append("")

    # Step 8: Start
    sections.append("REM Step 8: Start the app")
    sections.append("echo.")
    sections.append(f"echo === Starting {project_name} ===")
    if len(stack.run_commands) > 1:
        items = list(stack.run_commands.items())
        for i, (svc_dir, cmd) in enumerate(items):
            if i < len(items) - 1:
                sections.append(f"echo   Starting {svc_dir}...")
                sections.append(f"start /b cmd /c \"cd {svc_dir} && {cmd}\"")
            else:
                sections.append(f"echo   Starting {svc_dir}...")
                sections.append(f"pushd {svc_dir}")
                sections.append(cmd)
                sections.append("popd")
    elif stack.run_commands:
        svc_dir, cmd = next(iter(stack.run_commands.items()))
        if svc_dir and svc_dir != ".":
            sections.append(f"pushd {svc_dir}")
            sections.append(cmd)
            sections.append("popd")
        else:
            sections.append(cmd)
    else:
        if stack.primary_language == "python":
            sections.append("python -m app.main")
        else:
            sections.append("npm start")

    return "\r\n".join(sections) + "\r\n"


# ---------------------------------------------------------------------------
# Windows PowerShell
# ---------------------------------------------------------------------------


def generate_boot_ps1(project_name: str, stack: StackInfo) -> str:
    """Generate boot.ps1 for Windows PowerShell."""
    sections = [
        "#Requires -Version 5.1",
        "$ErrorActionPreference = 'Stop'",
        f'Write-Host "=== {project_name} — Setup & Run ===" -ForegroundColor Cyan',
        "",
    ]

    # Step 1
    sections.append("# Step 1: Check prerequisites")
    if stack.primary_language == "javascript":
        sections.extend([
            "if (-not (Get-Command node -ErrorAction SilentlyContinue)) {",
            f'    Write-Error "Node.js {stack.node_version}+ is required. Download from https://nodejs.org"',
            "    exit 1",
            "}",
            'Write-Host "  Node.js $(node --version) found" -ForegroundColor Green',
        ])
    elif stack.primary_language == "python":
        sections.extend([
            "if (-not (Get-Command python -ErrorAction SilentlyContinue)) {",
            f'    Write-Error "Python {stack.python_version}+ is required. Download from https://python.org"',
            "    exit 1",
            "}",
            'Write-Host "  $(python --version) found" -ForegroundColor Green',
        ])
    sections.append("")

    # Step 2
    sections.append("# Step 2: Create virtual environment")
    if stack.primary_language == "python":
        sections.extend([
            'if (-not (Test-Path ".venv")) {',
            '    Write-Host "  Creating Python virtual environment..."',
            "    python -m venv .venv",
            "}",
        ])
    sections.append("")

    # Step 3
    sections.append("# Step 3: Activate environment")
    if stack.primary_language == "python":
        sections.append(".venv\\Scripts\\Activate.ps1")
        sections.append('Write-Host "  Virtual environment activated" -ForegroundColor Green')
    sections.append("")

    # Step 4
    sections.append("# Step 4: Install dependencies")
    for cmd_info in stack.install_commands:
        d = cmd_info["dir"]
        cmd = cmd_info["cmd"]
        if d and d != ".":
            sections.append(f'Write-Host "  Installing dependencies in {d}/..."')
            sections.append(f"Push-Location {d}")
            sections.append(cmd)
            sections.append("Pop-Location")
        else:
            sections.append('Write-Host "  Installing dependencies..."')
            sections.append(cmd)
    sections.append("")

    # Step 5
    sections.append("# Step 5: Check environment configuration")
    _env_dirs = set()
    for v in stack.env_vars:
        _env_dirs.add(v.get("dir", "root"))
    for d in sorted(_env_dirs):
        actual_dir = "." if d == "root" else d
        sections.append(f'if ((-not (Test-Path "{actual_dir}/.env")) -and (Test-Path "{actual_dir}/.env.example")) {{')
        sections.append(f'    Copy-Item "{actual_dir}/.env.example" "{actual_dir}/.env"')
        sections.append(f'    Write-Host "  Created {actual_dir}/.env from example" -ForegroundColor Yellow')
        sections.append("}")
    sections.append("")

    # Step 6
    sections.append("# Step 6: Environment file ready")
    sections.append('Write-Host "  Environment configured" -ForegroundColor Green')
    sections.append("")

    # Step 7
    sections.append("# Step 7: Run migrations")
    if stack.database and stack.database != "sqlite":
        sections.append('Write-Host "  Running database migrations..."')
    else:
        sections.append("# SQLite / no database — auto-initialized on first run")
    sections.append("")

    # Step 8
    sections.append("# Step 8: Start the app")
    sections.append("Write-Host ''")
    sections.append(f'Write-Host "=== Starting {project_name} ===" -ForegroundColor Cyan')
    if len(stack.run_commands) > 1:
        items = list(stack.run_commands.items())
        for i, (svc_dir, cmd) in enumerate(items):
            if i < len(items) - 1:
                sections.append(f'Write-Host "  Starting {svc_dir}..."')
                sections.append(f"Start-Process -NoNewWindow -FilePath cmd -ArgumentList '/c cd {svc_dir} && {cmd}'")
            else:
                sections.append(f'Write-Host "  Starting {svc_dir}..."')
                sections.append(f"Push-Location {svc_dir}")
                sections.append(cmd)
                sections.append("Pop-Location")
    elif stack.run_commands:
        svc_dir, cmd = next(iter(stack.run_commands.items()))
        if svc_dir and svc_dir != ".":
            sections.append(f"Push-Location {svc_dir}")
            sections.append(cmd)
            sections.append("Pop-Location")
        else:
            sections.append(cmd)
    else:
        if stack.primary_language == "python":
            sections.append("python -m app.main")
        else:
            sections.append("npm start")

    return "\n".join(sections) + "\n"
