"""Tool executor -- sandboxed tool handlers for builder agent tool use.

Each tool handler enforces path sandboxing (no traversal outside working_dir),
size limits, and returns string results (required by Anthropic tool API).
All handlers catch exceptions and return error strings rather than raising.

Phase 18 tools: read_file, list_directory, search_code, write_file (sync)
Phase 19 tools: run_tests, check_syntax, run_command (async -- subprocess)
Phase 55 tools: forge_get_contract, forge_get_phase_window,
                forge_list_contracts, forge_get_summary, forge_scratchpad (sync)
"""

import asyncio
import ast
import fnmatch
import json
import os
import re
from pathlib import Path

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MAX_READ_FILE_BYTES = 50_000  # 50KB max for read_file
MAX_SEARCH_RESULTS = 50
MAX_WRITE_FILE_BYTES = 500_000  # 500KB max for write_file
MAX_STDOUT_BYTES = 50_000  # 50KB max for subprocess stdout
MAX_STDERR_BYTES = 10_000  # 10KB max for subprocess stderr
DEFAULT_RUN_TESTS_TIMEOUT = 120  # seconds
DEFAULT_CHECK_SYNTAX_TIMEOUT = 30
DEFAULT_RUN_COMMAND_TIMEOUT = 60
MAX_TEST_RUNS_WARNING = 5  # warn after this many test runs per phase

# Dependency manifests that trigger auto-install when written
_DEPENDENCY_MANIFESTS: dict[str, list[str]] = {
    "requirements.txt": ["pip", "install", "-r", "requirements.txt"],
    "requirements-dev.txt": ["pip", "install", "-r", "requirements-dev.txt"],
    "pyproject.toml": ["pip", "install", "-e", "."],
    "package.json": ["npm", "install"],
}

SKIP_DIRS = frozenset({
    ".git", "__pycache__", "node_modules", ".venv", "venv",
    ".tox", "dist", "build", ".mypy_cache", ".pytest_cache",
})

# Allowlists for command safety
RUN_TESTS_PREFIXES = (
    "pytest", "python -m pytest", "python3 -m pytest",
    "npm test", "npm run test", "npx vitest", "npx jest",
)
RUN_COMMAND_PREFIXES = (
    "pip install", "pip3 install",
    "npm install", "npx ",
    "python -m ", "python3 -m ",
    "cat ", "head ", "tail ", "wc ", "find ", "ls ",
    "dir ",  # Windows
    "type ",  # Windows cat equivalent
)
BLOCKED_COMMANDS = frozenset({
    "rm", "del", "rmdir", "curl", "wget", "ssh", "scp",
    "git push", "git remote", "shutdown", "reboot",
    "format", "mkfs", "dd ", "chmod", "chown",
})


# ---------------------------------------------------------------------------
# Path sandboxing (delegates to Workspace.resolve — Phase 23)
# ---------------------------------------------------------------------------

# Module-level workspace cache: one Workspace per working_dir (≈ one per build)
_workspace_cache: dict[str, object] = {}


def _get_workspace(working_dir: str) -> object:
    """Return a cached ``Workspace`` for *working_dir*.

    Uses lazy imports to avoid circular references (adapters → tool_executor).
    """
    if working_dir not in _workspace_cache:
        from forge_ide.workspace import Workspace

        _workspace_cache[working_dir] = Workspace(working_dir)
    return _workspace_cache[working_dir]


def _resolve_sandboxed(rel_path: str, working_dir: str) -> Path | None:
    """Resolve a relative path within working_dir, enforcing sandbox.

    Returns the resolved absolute Path if safe, or None if the path
    would escape the sandbox (e.g. via `..` traversal or absolute paths).

    Delegates to ``Workspace.resolve()`` for the actual enforcement logic.
    """
    if not rel_path or not working_dir:
        return None

    try:
        ws = _get_workspace(working_dir)
        return ws.resolve(rel_path)  # type: ignore[union-attr]
    except Exception:
        # SandboxViolation, ValueError, or any other error → None
        return None


# ---------------------------------------------------------------------------
# Tool handlers
# ---------------------------------------------------------------------------


def execute_tool(tool_name: str, tool_input: dict, working_dir: str) -> str:
    """Dispatch a synchronous tool call to the correct handler.

    Args:
        tool_name: Name of the tool to execute.
        tool_input: Input parameters for the tool.
        working_dir: Absolute path to the build working directory.

    Returns:
        String result of the tool execution.
    """
    sync_handlers = {
        "read_file": _exec_read_file,
        "list_directory": _exec_list_directory,
        "search_code": _exec_search_code,
        "write_file": _exec_write_file,
        "edit_file": _exec_edit_file,
        # Forge governance tools (Phase 55)
        "forge_get_contract": _exec_forge_get_contract,
        "forge_get_phase_window": _exec_forge_get_phase_window,
        "forge_list_contracts": _exec_forge_list_contracts,
        "forge_get_summary": _exec_forge_get_summary,
        "forge_scratchpad": _exec_forge_scratchpad,
    }
    handler = sync_handlers.get(tool_name)
    if handler is None:
        return f"Error: Unknown tool '{tool_name}'"
    try:
        return handler(tool_input, working_dir)
    except Exception as exc:
        return f"Error executing {tool_name}: {exc}"


async def execute_tool_async(
    tool_name: str, tool_input: dict, working_dir: str, **kwargs: object
) -> str:
    """Dispatch a tool call -- supports both sync and async handlers.

    This is the main dispatcher used by the build loop. Sync tools (read_file,
    list_directory, search_code, write_file, edit_file) are called directly. Async tools
    (run_tests, check_syntax, run_command, project-scoped forge tools) use subprocess or
    HTTP execution.

    Args:
        tool_name: Name of the tool to execute.
        tool_input: Input parameters for the tool.
        working_dir: Absolute path to the build working directory.
        **kwargs: Extra keyword arguments (e.g. build_id) accepted but unused here.

    Returns:
        String result of the tool execution.
    """
    sync_handlers = {
        "read_file": _exec_read_file,
        "list_directory": _exec_list_directory,
        "search_code": _exec_search_code,
        "write_file": _exec_write_file,
        "edit_file": _exec_edit_file,
        # Forge governance tools (Phase 55)
        "forge_get_contract": _exec_forge_get_contract,
        "forge_get_phase_window": _exec_forge_get_phase_window,
        "forge_list_contracts": _exec_forge_list_contracts,
        "forge_get_summary": _exec_forge_get_summary,
        "forge_scratchpad": _exec_forge_scratchpad,
    }
    async_handlers = {
        "run_tests": _exec_run_tests,
        "check_syntax": _exec_check_syntax,
        "run_command": _exec_run_command,
        # Project-scoped forge tools (Phase F) — proxy to MCP dispatch → ForgeGuard API
        "forge_get_project_context": _exec_forge_get_project_context,
        "forge_list_project_contracts": _exec_forge_list_project_contracts,
        "forge_get_project_contract": _exec_forge_get_project_contract,
        "forge_get_build_contracts": _exec_forge_get_build_contracts,
    }

    if tool_name in sync_handlers:
        try:
            result = sync_handlers[tool_name](tool_input, working_dir)
        except Exception as exc:
            return f"Error executing {tool_name}: {exc}"

        # Post-write hook: auto-install dependencies when a manifest is written
        if tool_name == "write_file" and result.startswith("OK:"):
            rel_path = tool_input.get("path", "")
            install_msg = await _auto_install_deps(rel_path, working_dir)
            if install_msg:
                result = f"{result}\n\n{install_msg}"

        return result

    if tool_name in async_handlers:
        try:
            return await async_handlers[tool_name](tool_input, working_dir)
        except Exception as exc:
            return f"Error executing {tool_name}: {exc}"

    return f"Error: Unknown tool '{tool_name}'"


def _exec_read_file(inp: dict, working_dir: str) -> str:
    """Read a file from the working directory.

    Input: { "path": "relative/path/to/file.py" }
    Returns: File content (truncated at 50KB).
    """
    rel_path = inp.get("path", "")
    target = _resolve_sandboxed(rel_path, working_dir)
    if target is None:
        return f"Error: Invalid or disallowed path '{rel_path}'"
    if not target.exists():
        return f"Error: File not found '{rel_path}'"
    if not target.is_file():
        return f"Error: '{rel_path}' is not a file"

    try:
        content = target.read_text(encoding="utf-8", errors="replace")
    except Exception as exc:
        return f"Error reading '{rel_path}': {exc}"

    if len(content) > MAX_READ_FILE_BYTES:
        content = content[:MAX_READ_FILE_BYTES] + f"\n\n[... truncated at {MAX_READ_FILE_BYTES} bytes ...]"

    return content


def _exec_list_directory(inp: dict, working_dir: str) -> str:
    """List files and folders in a directory within the working directory.

    Input: { "path": "relative/path" }  (defaults to "." for root)
    Returns: Names with '/' suffix for directories.
    """
    rel_path = inp.get("path", ".")
    target = _resolve_sandboxed(rel_path, working_dir)
    if target is None:
        # Special-case: "." should always resolve to working_dir
        if rel_path == ".":
            target = Path(working_dir).resolve()
        else:
            return f"Error: Invalid or disallowed path '{rel_path}'"
    if not target.exists():
        return f"Error: Directory not found '{rel_path}'"
    if not target.is_dir():
        return f"Error: '{rel_path}' is not a directory"

    entries = []
    try:
        for child in sorted(target.iterdir()):
            name = child.name
            if name in SKIP_DIRS:
                continue
            if child.is_dir():
                entries.append(f"{name}/")
            else:
                entries.append(name)
    except Exception as exc:
        return f"Error listing '{rel_path}': {exc}"

    if not entries:
        return "(empty directory)"

    return "\n".join(entries)


def _exec_search_code(inp: dict, working_dir: str) -> str:
    """Search for a pattern across files in the working directory.

    Input: { "pattern": "search string or regex", "glob": "*.py" (optional) }
    Returns: Matching file paths and line snippets (max 50 results).
    """
    pattern = inp.get("pattern", "")
    if not pattern:
        return "Error: 'pattern' is required"

    file_glob = inp.get("glob", "*")
    root = Path(working_dir).resolve()
    results: list[str] = []

    try:
        regex = re.compile(pattern, re.IGNORECASE)
    except re.error:
        # Fall back to literal search
        regex = re.compile(re.escape(pattern), re.IGNORECASE)

    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in sorted(dirnames) if d not in SKIP_DIRS]
        for fname in sorted(filenames):
            if not fnmatch.fnmatch(fname, file_glob):
                continue
            full_path = Path(dirpath) / fname
            rel = full_path.relative_to(root)
            try:
                lines = full_path.read_text(encoding="utf-8", errors="replace").splitlines()
            except Exception:
                continue

            for i, line in enumerate(lines, 1):
                if regex.search(line):
                    snippet = line.strip()[:120]
                    results.append(f"{rel}:{i}: {snippet}")
                    if len(results) >= MAX_SEARCH_RESULTS:
                        results.append(f"... (truncated at {MAX_SEARCH_RESULTS} results)")
                        return "\n".join(results)

    if not results:
        return f"No matches found for '{pattern}'"

    return "\n".join(results)


def _exec_write_file(inp: dict, working_dir: str) -> str:
    """Write/overwrite a file at a relative path in the working directory.

    Input: { "path": "relative/path/to/file.py", "content": "file contents" }
    Returns: Confirmation with file size.
    """
    rel_path = inp.get("path", "")
    content = inp.get("content", "")

    target = _resolve_sandboxed(rel_path, working_dir)
    if target is None:
        return f"Error: Invalid or disallowed path '{rel_path}'"

    if len(content) > MAX_WRITE_FILE_BYTES:
        return f"Error: Content exceeds {MAX_WRITE_FILE_BYTES} byte limit ({len(content)} bytes)"

    # Duplicate detection: skip write if target already has identical content
    if target.exists() and target.is_file():
        try:
            existing = target.read_text(encoding="utf-8", errors="replace")
            if existing == content:
                return (
                    f"SKIP: {rel_path} already exists with identical content "
                    f"({len(content)} bytes). Move on to the next file."
                )
        except Exception:
            pass  # If we can't read it, overwrite

    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return f"OK: Wrote {len(content)} bytes to {rel_path}"
    except Exception as exc:
        return f"Error writing '{rel_path}': {exc}"


def _exec_edit_file(inp: dict, working_dir: str) -> str:
    """Apply surgical edits to an existing file.

    Input: {
        "path": "relative/path/to/file.py",
        "edits": [
            {
                "old_text": "text to find and replace",
                "new_text": "replacement text",
                "anchor": "context lines above the edit (optional)",
                "explanation": "why this change (optional)"
            }
        ]
    }
    Returns: Summary of applied/failed edits.
    """
    from forge_ide.contracts import Edit as _Edit
    from forge_ide.patcher import apply_edits as _apply_edits

    rel_path = inp.get("path", "")
    raw_edits = inp.get("edits", [])

    target = _resolve_sandboxed(rel_path, working_dir)
    if target is None:
        return f"Error: Invalid or disallowed path '{rel_path}'"

    if not target.exists() or not target.is_file():
        return f"Error: File '{rel_path}' does not exist — use write_file to create it"

    if not raw_edits:
        return "Error: No edits provided"

    try:
        content = target.read_text(encoding="utf-8", errors="replace")
    except Exception as exc:
        return f"Error reading '{rel_path}': {exc}"

    edits = [
        _Edit(
            old_text=e.get("old_text", ""),
            new_text=e.get("new_text", ""),
            anchor=e.get("anchor", ""),
            explanation=e.get("explanation", ""),
        )
        for e in raw_edits
    ]

    result = _apply_edits(content, edits, file_path=rel_path)

    if result.success:
        try:
            target.write_text(result.final_content, encoding="utf-8")
        except Exception as exc:
            return f"Error writing '{rel_path}': {exc}"

        parts = [f"OK: Applied {len(result.applied)}/{len(edits)} edits to {rel_path}"]
        if result.retargeted > 0:
            parts.append(f" ({result.retargeted} retargeted via fuzzy match)")
        return "".join(parts)
    else:
        parts = [f"PARTIAL: {len(result.applied)}/{len(edits)} edits applied to {rel_path}"]
        for preview, reason in result.failed:
            parts.append(f"  FAILED: {preview} — {reason}")
        if result.applied:
            # Write partial result
            try:
                target.write_text(result.final_content, encoding="utf-8")
                parts.append(f"  (partial result written — {len(result.applied)} edits applied)")
            except Exception:
                parts.append("  (partial result NOT written due to I/O error)")
        return "\n".join(parts)


# ---------------------------------------------------------------------------
# Command validation helpers
# ---------------------------------------------------------------------------


def _validate_command(command: str, allowed_prefixes: tuple[str, ...]) -> str | None:
    """Validate a command against an allowlist.

    Returns None if the command is allowed, or an error message string if blocked.
    """
    if not command or not command.strip():
        return "Error: Command is empty"

    cmd = command.strip()

    # Check for command injection characters
    for char in (";", "|", "&", "`", "$", "(", ")", "{", "}"):
        if char in cmd:
            return f"Error: Command contains disallowed character '{char}'"

    # Check against blocked commands
    cmd_lower = cmd.lower()
    for blocked in BLOCKED_COMMANDS:
        if cmd_lower.startswith(blocked):
            return f"Error: Command '{blocked}' is not allowed"

    # Check against allowlist
    if not any(cmd_lower.startswith(prefix) for prefix in allowed_prefixes):
        return f"Error: Command not in allowlist. Allowed prefixes: {', '.join(allowed_prefixes)}"

    return None


def _truncate_output(text: str, max_bytes: int) -> str:
    """Truncate output to max_bytes, appending a truncation notice."""
    if len(text) <= max_bytes:
        return text
    return text[:max_bytes] + f"\n\n[... truncated at {max_bytes} bytes ...]"


def _build_project_env(working_dir: str) -> dict[str, str]:
    """Build subprocess env dict with venv activation + .env loading.

    Shared by ``_run_subprocess`` and ``_auto_install_deps``.
    """
    env: dict[str, str] = {"PATH": os.environ.get("PATH", "")}
    for key in ("SYSTEMROOT", "TEMP", "TMP", "HOME", "USERPROFILE",
                "APPDATA", "LOCALAPPDATA", "COMSPEC"):
        val = os.environ.get(key)
        if val:
            env[key] = val

    # Activate project .venv
    venv_dir = Path(working_dir) / ".venv" if working_dir else None
    if venv_dir and venv_dir.is_dir():
        env["VIRTUAL_ENV"] = str(venv_dir)
        bin_dir = venv_dir / ("Scripts" if os.name == "nt" else "bin")
        env["PATH"] = f"{bin_dir}{os.pathsep}{env.get('PATH', '')}"
    else:
        host_venv = os.environ.get("VIRTUAL_ENV")
        if host_venv:
            env["VIRTUAL_ENV"] = host_venv

    # Load project .env variables
    dotenv_path = Path(working_dir) / ".env" if working_dir else None
    if dotenv_path and dotenv_path.is_file():
        try:
            for line in dotenv_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    k, _, v = line.partition("=")
                    k = k.strip()
                    v = v.strip().strip('"').strip("'")
                    if k:
                        env[k] = v
        except Exception:
            pass  # non-fatal

    return env


async def _auto_install_deps(rel_path: str, working_dir: str) -> str:
    """Auto-install dependencies when a known manifest file is written.

    Called as a post-hook from ``execute_tool_async`` after ``write_file``
    succeeds.  Only triggers for files in ``_DEPENDENCY_MANIFESTS``.

    Returns an informational string to append to the write_file result,
    or empty string if no install was needed.
    """
    from app.config import settings as _settings
    if not getattr(_settings, "AUTO_INSTALL_DEPS", True):
        return ""

    filename = Path(rel_path).name
    if filename not in _DEPENDENCY_MANIFESTS:
        return ""

    venv_dir = Path(working_dir) / ".venv"
    if not venv_dir.is_dir() and filename in ("requirements.txt", "requirements-dev.txt", "pyproject.toml"):
        return "(skipped auto-install: no .venv found — run_command to install manually)"

    cmd_parts = _DEPENDENCY_MANIFESTS[filename]
    # Resolve pip/npm to the venv binary
    if cmd_parts[0] == "pip" and venv_dir.is_dir():
        pip_exe = str(venv_dir / ("Scripts" if os.name == "nt" else "bin") / "pip")
        cmd = f"{pip_exe} {' '.join(cmd_parts[1:])}"
    else:
        cmd = " ".join(cmd_parts)

    try:
        exit_code, stdout, stderr = await _run_subprocess(
            cmd, working_dir, timeout=120,
        )
        if exit_code == 0:
            # Summarise: count installed packages
            installed_count = stdout.lower().count("successfully installed")
            if installed_count:
                return f"\u2714 Auto-installed dependencies ({filename})"
            return f"\u2714 Auto-install ran for {filename} (exit 0)"
        else:
            short_err = (stderr or stdout or "")[:200]
            return f"\u26a0 Auto-install for {filename} failed (exit {exit_code}): {short_err}"
    except Exception as exc:
        return f"\u26a0 Auto-install for {filename} error: {exc}"


async def _run_subprocess(
    command: str, working_dir: str, timeout: int,
) -> tuple[int, str, str]:
    """Run a subprocess command with timeout and output limits.

    Uses subprocess.run in a thread to avoid asyncio event-loop limitations
    on Windows (ProactorEventLoop requirement for create_subprocess_shell).

    If *working_dir* contains a ``.venv`` directory, the subprocess
    environment is configured to use that project-local virtual environment
    (``VIRTUAL_ENV`` set, venv ``Scripts/`` or ``bin/`` prepended to ``PATH``).
    Also loads the project's ``.env`` file if present.

    Returns (exit_code, stdout, stderr).
    """
    env = _build_project_env(working_dir)

    import subprocess as _sp

    def _sync() -> tuple[int, str, str]:
        try:
            result = _sp.run(
                command,
                capture_output=True,
                text=True,
                cwd=working_dir,
                env=env,
                shell=True,
                timeout=timeout,
            )
            stdout = _truncate_output(result.stdout or "", MAX_STDOUT_BYTES)
            stderr = _truncate_output(result.stderr or "", MAX_STDERR_BYTES)
            return result.returncode, stdout, stderr
        except _sp.TimeoutExpired:
            return -1, "", f"Error: Command timed out after {timeout}s"

    return await asyncio.to_thread(_sync)


# ---------------------------------------------------------------------------
# Async tool handlers (Phase 19)
# ---------------------------------------------------------------------------


async def _exec_run_tests(inp: dict, working_dir: str) -> str:
    """Run a test command in the working directory.

    Input: { "command": "pytest tests/ -v", "timeout": 120 }
    Returns: Exit code, stdout, and stderr.
    """
    command = inp.get("command", "")
    timeout = min(int(inp.get("timeout", DEFAULT_RUN_TESTS_TIMEOUT)), 300)

    error = _validate_command(command, RUN_TESTS_PREFIXES)
    if error:
        return error

    exit_code, stdout, stderr = await _run_subprocess(command, working_dir, timeout)

    parts = [f"Exit code: {exit_code}"]
    if stdout:
        parts.append(f"--- stdout ---\n{stdout}")
    if stderr:
        parts.append(f"--- stderr ---\n{stderr}")

    return "\n".join(parts)


async def _exec_check_syntax(inp: dict, working_dir: str) -> str:
    """Check syntax of a file in the working directory.

    Input: { "file_path": "app/services/foo.py" }
    Returns: Syntax errors with line numbers, or "No errors" if clean.
    """
    file_path = inp.get("file_path", "")
    target = _resolve_sandboxed(file_path, working_dir)
    if target is None:
        return f"Error: Invalid or disallowed path '{file_path}'"
    if not target.exists():
        return f"Error: File not found '{file_path}'"
    if not target.is_file():
        return f"Error: '{file_path}' is not a file"

    ext = target.suffix.lower()

    # Python files: use ast.parse for syntax checking
    if ext == ".py":
        try:
            source = target.read_text(encoding="utf-8", errors="replace")
            ast.parse(source, filename=file_path)
            return f"No syntax errors in {file_path}"
        except SyntaxError as e:
            return f"Syntax error in {file_path}:{e.lineno}: {e.msg}"
        except Exception as e:
            return f"Error checking {file_path}: {e}"

    # TypeScript/JavaScript: use tsc --noEmit or node --check
    if ext in (".ts", ".tsx"):
        timeout = int(inp.get("timeout", DEFAULT_CHECK_SYNTAX_TIMEOUT))
        cmd = f"npx tsc --noEmit --pretty false {target}"
        exit_code, stdout, stderr = await _run_subprocess(cmd, working_dir, timeout)
        if exit_code == 0:
            return f"No syntax errors in {file_path}"
        output = (stdout + "\n" + stderr).strip()
        return f"TypeScript errors in {file_path}:\n{output}"

    if ext in (".js", ".jsx", ".mjs"):
        timeout = int(inp.get("timeout", DEFAULT_CHECK_SYNTAX_TIMEOUT))
        cmd = f"node --check {target}"
        exit_code, stdout, stderr = await _run_subprocess(cmd, working_dir, timeout)
        if exit_code == 0:
            return f"No syntax errors in {file_path}"
        output = (stdout + "\n" + stderr).strip()
        return f"JavaScript errors in {file_path}:\n{output}"

    return f"Error: Unsupported file type '{ext}' for syntax checking"


async def _exec_run_command(inp: dict, working_dir: str) -> str:
    """Run a sandboxed shell command in the working directory.

    Input: { "command": "pip install -r requirements.txt", "timeout": 60 }
    Returns: Exit code + stdout/stderr.
    """
    command = inp.get("command", "")
    timeout = min(int(inp.get("timeout", DEFAULT_RUN_COMMAND_TIMEOUT)), 300)

    error = _validate_command(command, RUN_COMMAND_PREFIXES)
    if error:
        return error

    exit_code, stdout, stderr = await _run_subprocess(command, working_dir, timeout)

    parts = [f"Exit code: {exit_code}"]
    if stdout:
        parts.append(f"--- stdout ---\n{stdout}")
    if stderr:
        parts.append(f"--- stderr ---\n{stderr}")

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Forge governance tool handlers (Phase 55)
# ---------------------------------------------------------------------------

# In-memory scratchpad store: keyed by working_dir (one per build)
_scratchpads: dict[str, dict[str, str]] = {}


def _exec_forge_get_contract(inp: dict, working_dir: str) -> str:
    """Read a governance contract from the build's working directory.

    Input: { "name": "blueprint" }
    Blocks "phases" (too large) -- directs to forge_get_phase_window.
    """
    name = (inp.get("name") or "").strip()
    if not name:
        return "Error: 'name' is required (e.g. 'blueprint', 'stack', 'schema')"

    if name == "phases":
        return (
            "Error: The full phases contract is too large for context. "
            "Use forge_get_phase_window(phase_number) to get the current + "
            "next phase deliverables instead."
        )

    contracts_dir = Path(working_dir) / "Forge" / "Contracts"
    if not contracts_dir.is_dir():
        return f"Error: Forge/Contracts/ directory not found in working directory"

    # Extension map — same as forge_ide/mcp/config.py CONTRACT_MAP
    ext_map: dict[str, str] = {
        "boundaries": "boundaries.json",
        "physics": "physics.yaml",
    }
    filename = ext_map.get(name, f"{name}.md")
    path = contracts_dir / filename

    if not path.exists():
        # List available files to help the builder
        available = [f.stem for f in contracts_dir.iterdir() if f.is_file()]
        return f"Error: Contract '{name}' not found. Available: {', '.join(sorted(available))}"

    try:
        content = path.read_text(encoding="utf-8")
        # Cap at 50KB to avoid blowing up context
        if len(content) > 50_000:
            content = content[:50_000] + "\n\n[... truncated at 50KB ...]"
        return content
    except Exception as exc:
        return f"Error reading contract '{name}': {exc}"


def _exec_forge_get_phase_window(inp: dict, working_dir: str) -> str:
    """Extract current + next phase from phases.md in the working directory.

    Input: { "phase_number": 0 }
    Returns ~1-3K tokens of phase deliverables instead of the full 230K phases file.
    """
    phase_num = int(inp.get("phase_number", 0))

    phases_path = Path(working_dir) / "Forge" / "Contracts" / "phases.md"
    if not phases_path.exists():
        return "Error: Forge/Contracts/phases.md not found in working directory"

    try:
        content = phases_path.read_text(encoding="utf-8")
    except Exception as exc:
        return f"Error reading phases.md: {exc}"

    # Split on ## Phase headers and extract current + next
    phase_blocks = re.split(r"(?=^## Phase )", content, flags=re.MULTILINE)
    target_nums = {phase_num, phase_num + 1}
    selected: list[str] = []
    max_phase = -1

    for block in phase_blocks:
        header = re.match(
            r"^## Phase\s+(\d+)\s*[-—–]+\s*(.+)", block, re.MULTILINE,
        )
        if header:
            pnum = int(header.group(1))
            max_phase = max(max_phase, pnum)
            if pnum in target_nums:
                selected.append(block.strip())

    if not selected:
        if max_phase >= 0:
            return f"Error: Phase {phase_num} not found. Phases range from 0 to {max_phase}."
        return "Error: No phases found in phases.md"

    return (
        f"## Phase Window (Phase {phase_num}"
        + (f" + {phase_num + 1})" if phase_num + 1 <= max_phase else " — final phase)")
        + "\n\n"
        + "\n\n---\n\n".join(selected)
    )


def _exec_forge_list_contracts(inp: dict, working_dir: str) -> str:
    """List available governance contracts in the working directory.

    Input: {} (no parameters required)
    Returns JSON array of contract names and filenames.
    """
    contracts_dir = Path(working_dir) / "Forge" / "Contracts"
    if not contracts_dir.is_dir():
        return json.dumps({"error": "Forge/Contracts/ directory not found", "contracts": []})

    items = []
    for f in sorted(contracts_dir.iterdir()):
        if not f.is_file():
            continue
        items.append({
            "name": f.stem,
            "filename": f.name,
            "size_kb": round(f.stat().st_size / 1024, 1),
        })

    return json.dumps({"contracts": items, "total": len(items)}, indent=2)


def _exec_forge_get_summary(inp: dict, working_dir: str) -> str:
    """Return a compact governance framework overview.

    Input: {} (no parameters required)
    Summarises available contracts, architecture layers, and key rules
    without loading any full contract content.
    """
    contracts_dir = Path(working_dir) / "Forge" / "Contracts"
    contract_names: list[str] = []
    if contracts_dir.is_dir():
        contract_names = sorted(
            f.stem for f in contracts_dir.iterdir() if f.is_file()
        )

    # Try to extract layer names from boundaries.json
    layers: list[str] = []
    boundaries_path = contracts_dir / "boundaries.json" if contracts_dir.is_dir() else None
    if boundaries_path and boundaries_path.exists():
        try:
            data = json.loads(boundaries_path.read_text(encoding="utf-8"))
            layers = [layer.get("name", "") for layer in data.get("layers", [])]
        except Exception:
            pass

    summary = {
        "framework": "Forge Governance",
        "description": (
            "Governance-as-code framework for AI-driven software builds. "
            "Enforces architectural boundaries, invariant gates, and "
            "phased delivery with audit trails."
        ),
        "available_contracts": contract_names,
        "architectural_layers": layers,
        "key_tools": [
            "forge_get_contract(name) — read a specific contract",
            "forge_get_phase_window(phase_number) — current + next phase deliverables",
            "forge_list_contracts() — list all contracts",
            "forge_scratchpad(op, key?, value?) — persistent notes across phases",
        ],
        "critical_rules": [
            "Build phases in strict order — no skipping",
            "Routers → Services → Repos — no layer skipping",
            "Never include Forge content in committed source files",
            "Run tests after every code change",
            "Emit === PHASE SIGN-OFF: PASS === when done",
        ],
    }
    return json.dumps(summary, indent=2)


def _exec_forge_scratchpad(inp: dict, working_dir: str) -> str:
    """Persistent key-value scratchpad scoped to the build working directory.

    Input: { "operation": "read|write|append|list", "key": "...", "value": "..." }
    Persists to Forge/.scratchpad.json for durability across restarts.
    """
    op = (inp.get("operation") or "list").strip().lower()
    key = (inp.get("key") or "").strip()
    value = inp.get("value", "")

    # Load from disk if not yet in memory
    if working_dir not in _scratchpads:
        scratchpad_path = Path(working_dir) / "Forge" / ".scratchpad.json"
        if scratchpad_path.exists():
            try:
                _scratchpads[working_dir] = json.loads(
                    scratchpad_path.read_text(encoding="utf-8")
                )
            except Exception:
                _scratchpads[working_dir] = {}
        else:
            _scratchpads[working_dir] = {}

    pad = _scratchpads[working_dir]

    if op == "list":
        return json.dumps({"keys": sorted(pad.keys()), "count": len(pad)})

    if not key:
        return "Error: 'key' is required for read/write/append operations"

    if op == "read":
        if key not in pad:
            return f"Error: Key '{key}' not found. Use 'list' to see available keys."
        return pad[key]

    if op == "write":
        pad[key] = str(value)
        _persist_scratchpad(working_dir, pad)
        return f"OK: Wrote key '{key}' ({len(str(value))} chars)"

    if op == "append":
        existing = pad.get(key, "")
        pad[key] = existing + str(value)
        _persist_scratchpad(working_dir, pad)
        return f"OK: Appended to key '{key}' (now {len(pad[key])} chars)"

    return f"Error: Unknown operation '{op}'. Use: read, write, append, list"


def _persist_scratchpad(working_dir: str, pad: dict[str, str]) -> None:
    """Write scratchpad to disk for durability."""
    try:
        scratchpad_path = Path(working_dir) / "Forge" / ".scratchpad.json"
        scratchpad_path.parent.mkdir(parents=True, exist_ok=True)
        scratchpad_path.write_text(
            json.dumps(pad, indent=2), encoding="utf-8",
        )
    except Exception:
        pass  # Best-effort — in-memory is the source of truth


# ---------------------------------------------------------------------------
# Project-scoped forge tool handlers (Phase F)
# These proxy to the MCP dispatch layer → ForgeGuard API → PostgreSQL.
# They respect forge_ide.mcp.session so the build orchestrator only needs
# to call forge_set_session() once before starting sub-agents.
# ---------------------------------------------------------------------------


async def _exec_forge_get_project_context(inp: dict, working_dir: str) -> str:
    """Return combined project manifest — metadata only, no full contract content.

    Input: { "project_id": "<uuid>" }  (optional if session is set)
    Endpoint: GET /api/mcp/context/{project_id}
    """
    try:
        from forge_ide.mcp.tools import dispatch as _mcp_dispatch
        result = await _mcp_dispatch("forge_get_project_context", inp)
        if "error" in result:
            return f"Error: {result['error']}"
        return json.dumps(result, indent=2)
    except Exception as exc:
        return f"Error calling forge_get_project_context: {exc}"


async def _exec_forge_list_project_contracts(inp: dict, working_dir: str) -> str:
    """List all generated contracts for the current project.

    Input: { "project_id": "<uuid>" }  (optional if session is set)
    Endpoint: GET /api/mcp/context/{project_id}  (returns contract listing)
    """
    try:
        from forge_ide.mcp.tools import dispatch as _mcp_dispatch
        result = await _mcp_dispatch("forge_list_project_contracts", inp)
        if "error" in result:
            return f"Error: {result['error']}"
        return json.dumps(result, indent=2)
    except Exception as exc:
        return f"Error calling forge_list_project_contracts: {exc}"


async def _exec_forge_get_project_contract(inp: dict, working_dir: str) -> str:
    """Fetch a single generated contract for the current project from the database.

    Input: { "contract_type": "stack", "project_id": "<uuid>" }
    project_id is optional if forge_set_session() was called.
    Endpoint: GET /api/mcp/context/{project_id}/{contract_type}
    """
    try:
        from forge_ide.mcp.tools import dispatch as _mcp_dispatch
        result = await _mcp_dispatch("forge_get_project_contract", inp)
        if "error" in result:
            return f"Error: {result['error']}"
        # Return just the content string for a cleaner builder experience
        content = result.get("content", "")
        if isinstance(content, str):
            return content
        return json.dumps(result, indent=2)
    except Exception as exc:
        return f"Error calling forge_get_project_contract: {exc}"


async def _exec_forge_get_build_contracts(inp: dict, working_dir: str) -> str:
    """Fetch the pinned contract snapshot for a specific build.

    Input: { "build_id": "<uuid>" }  (optional if session is set)
    Returns all contracts frozen when the build started (immutable).
    Endpoint: GET /api/mcp/build/{build_id}/contracts
    """
    try:
        from forge_ide.mcp.tools import dispatch as _mcp_dispatch
        result = await _mcp_dispatch("forge_get_build_contracts", inp)
        if "error" in result:
            return f"Error: {result['error']}"
        return json.dumps(result, indent=2)
    except Exception as exc:
        return f"Error calling forge_get_build_contracts: {exc}"


# ---------------------------------------------------------------------------
# Tool Specifications (Anthropic format)
# ---------------------------------------------------------------------------

BUILDER_TOOLS = [
    {
        "name": "read_file",
        "description": (
            "Read a file from the project working directory. "
            "Returns the file content (truncated at 50KB). "
            "Use this to check existing code, understand the current state, and verify your work."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Relative path to the file within the working directory.",
                },
            },
            "required": ["path"],
        },
    },
    {
        "name": "list_directory",
        "description": (
            "List files and folders in a directory within the working directory. "
            "Returns names with '/' suffix for directories. "
            "Use this to understand the project structure before making changes."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Relative path to the directory. Use '.' for the project root.",
                },
            },
            "required": ["path"],
        },
    },
    {
        "name": "search_code",
        "description": (
            "Search for a pattern across files in the working directory. "
            "Returns matching file paths and line snippets (max 50 results). "
            "Use this to find existing implementations, imports, or patterns."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "Search string or regex pattern to look for.",
                },
                "glob": {
                    "type": "string",
                    "description": "Optional file glob filter (e.g. '*.py'). Defaults to all files.",
                },
            },
            "required": ["pattern"],
        },
    },
    {
        "name": "write_file",
        "description": (
            "Write or overwrite a file at a relative path in the working directory. "
            "Creates parent directories automatically. "
            "Preferred over the === FILE: ... === block format for creating files."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Relative path for the file within the working directory.",
                },
                "content": {
                    "type": "string",
                    "description": "The full content to write to the file.",
                },
            },
            "required": ["path", "content"],
        },
    },
    {
        "name": "edit_file",
        "description": (
            "Apply surgical edits to an existing file. Preferred over write_file "
            "when modifying an existing file — avoids rewriting the entire file "
            "and reduces the risk of regressions in unchanged code. "
            "Each edit specifies old_text to find and new_text to replace it with. "
            "Optionally provide anchor (context lines above the edit) to help "
            "locate the edit point if the file has shifted."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Relative path to the existing file to edit.",
                },
                "edits": {
                    "type": "array",
                    "description": "List of surgical edits to apply in order.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "old_text": {
                                "type": "string",
                                "description": (
                                    "The exact text to find and replace. Include 2-3 lines "
                                    "of surrounding context for unambiguous matching."
                                ),
                            },
                            "new_text": {
                                "type": "string",
                                "description": "The replacement text.",
                            },
                            "anchor": {
                                "type": "string",
                                "description": (
                                    "Optional: 2-3 lines of unchanged code ABOVE the edit "
                                    "point. Used to locate the edit if exact match fails."
                                ),
                            },
                            "explanation": {
                                "type": "string",
                                "description": "Optional: brief reason for this edit.",
                            },
                        },
                        "required": ["old_text", "new_text"],
                    },
                },
            },
            "required": ["path", "edits"],
        },
    },
    {
        "name": "run_tests",
        "description": (
            "Run the project test suite or a subset of tests in the working directory. "
            "Returns exit code, stdout, and stderr. Use this after writing code to verify "
            "it works before signing off a phase. Only sign off when tests pass."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": (
                        "Test command to run. Must start with an allowed prefix: "
                        "pytest, python -m pytest, npm test, npx vitest, npx jest."
                    ),
                },
                "timeout": {
                    "type": "integer",
                    "description": "Timeout in seconds (default 120, max 300).",
                },
            },
            "required": ["command"],
        },
    },
    {
        "name": "check_syntax",
        "description": (
            "Check a file for syntax errors. For Python files, uses ast.parse. "
            "For TypeScript/JavaScript, uses tsc/node --check. "
            "Use this after writing files to catch syntax errors immediately."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "Relative path to the file to check.",
                },
            },
            "required": ["file_path"],
        },
    },
    {
        "name": "run_command",
        "description": (
            "Run a sandboxed shell command in the working directory. "
            "Allowed commands include: pip install, npm install, python -m, npx, "
            "cat, head, tail, wc, find, ls. Destructive commands are blocked."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "Shell command to run. Must start with an allowed prefix.",
                },
                "timeout": {
                    "type": "integer",
                    "description": "Timeout in seconds (default 60, max 300).",
                },
            },
            "required": ["command"],
        },
    },
]

# Forge governance tools (Phase 55) — appended to BUILDER_TOOLS
FORGE_TOOLS = [
    {
        "name": "forge_get_contract",
        "description": (
            "Read a specific governance contract from the Forge/Contracts/ directory. "
            "Returns the full content of the requested contract. "
            "Use this to fetch project specifications (blueprint, stack, schema, physics, "
            "boundaries, manifesto, ui, builder_directive, builder_contract). "
            "Note: 'phases' is blocked — use forge_get_phase_window instead."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": (
                        "Contract name (e.g. 'blueprint', 'stack', 'schema', 'physics', "
                        "'boundaries', 'manifesto', 'ui', 'builder_directive')."
                    ),
                },
            },
            "required": ["name"],
        },
    },
    {
        "name": "forge_get_phase_window",
        "description": (
            "Get the deliverables for a specific build phase (current + next phase). "
            "Call this at the START of each phase to understand what to build. "
            "Returns ~1-3K tokens instead of the full phases contract."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "phase_number": {
                    "type": "integer",
                    "description": (
                        "The phase number to retrieve (0-based). "
                        "Returns this phase + the next phase for context."
                    ),
                },
            },
            "required": ["phase_number"],
        },
    },
    {
        "name": "forge_list_contracts",
        "description": (
            "List all available governance contracts in the Forge/Contracts/ directory. "
            "Returns names, filenames, and sizes. Use this to discover what contracts exist "
            "before fetching specific ones."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "forge_get_summary",
        "description": (
            "Get a compact overview of the Forge governance framework. "
            "Returns available contracts, architecture layers, key rules, and tool descriptions. "
            "Call this FIRST to orient yourself before fetching specific contracts."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "forge_scratchpad",
        "description": (
            "Persistent scratchpad for storing reasoning, decisions, and notes that "
            "survive context compaction. Use this to record architectural decisions, "
            "known issues, and progress notes across phases. "
            "Operations: read (get value), write (set value), append (add to value), "
            "list (show all keys)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "operation": {
                    "type": "string",
                    "enum": ["read", "write", "append", "list"],
                    "description": "Operation to perform.",
                },
                "key": {
                    "type": "string",
                    "description": (
                        "Key name (e.g. 'architecture_decisions', 'phase_2_issues'). "
                        "Required for read/write/append."
                    ),
                },
                "value": {
                    "type": "string",
                    "description": "Value to write or append. Required for write/append.",
                },
            },
            "required": ["operation"],
        },
    },
    # ── Project-scoped contract tools (Phase F) ──────────────────────────────
    {
        "name": "forge_get_project_context",
        "description": (
            "Get a combined manifest for the current project — project info, "
            "list of available generated contracts (types, versions, sizes), "
            "build count, and latest snapshot batch. Returns METADATA only, "
            "not full contract content. Call this first to see what contracts "
            "are available, then fetch specific ones with forge_get_project_contract."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "project_id": {
                    "type": "string",
                    "description": "Project identifier (UUID). Optional if session is set.",
                },
            },
            "required": [],
        },
    },
    {
        "name": "forge_list_project_contracts",
        "description": (
            "List all generated contracts for the current project — contract "
            "types, versions, and last updated timestamps. Lighter than "
            "forge_get_project_context when you only need the contract list."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "project_id": {
                    "type": "string",
                    "description": "Project identifier (UUID). Optional if session is set.",
                },
            },
            "required": [],
        },
    },
    {
        "name": "forge_get_project_contract",
        "description": (
            "Fetch a single generated contract for the current project from "
            "the database. Returns the full content, version, and source. "
            "Use forge_list_project_contracts to see available types first. "
            "Common types: manifesto, stack, physics, boundaries, blueprint, "
            "builder_directive, schema, ui."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "contract_type": {
                    "type": "string",
                    "description": (
                        "Contract type — e.g. manifesto, stack, physics, "
                        "boundaries, blueprint, builder_directive, schema, ui."
                    ),
                },
                "project_id": {
                    "type": "string",
                    "description": "Project identifier (UUID). Optional if session is set.",
                },
            },
            "required": ["contract_type"],
        },
    },
    {
        "name": "forge_get_build_contracts",
        "description": (
            "Fetch the pinned contract snapshot for a specific build. "
            "Returns all contracts that were frozen when the build started. "
            "These are immutable — mid-build edits don't affect them. "
            "Used by the Fixer role to reference the exact contracts the "
            "build was executed against."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "build_id": {
                    "type": "string",
                    "description": "Build identifier (UUID). Optional if session is set.",
                },
            },
            "required": [],
        },
    },
    {
        "name": "forge_ask_clarification",
        "description": (
            "Ask the user a clarifying question when you encounter genuine ambiguity "
            "that cannot be resolved from the available contracts, scout data, or "
            "renovation plan. The build pauses until the user answers. "
            "Use SPARINGLY — only when the implementation direction depends on a "
            "user preference that cannot be inferred. Do NOT ask about obvious "
            "choices or things already specified in contracts. Max 10 per build."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": "The question to ask (max 200 characters, concise).",
                },
                "context": {
                    "type": "string",
                    "description": (
                        "Brief explanation of WHY you need to know (max 300 chars). "
                        "e.g. 'Implementing the login endpoint — choosing auth strategy.'"
                    ),
                },
                "options": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Optional 2–4 suggested answers as chips for the user. "
                        "Always include a 'Let AI decide' option if providing choices."
                    ),
                },
            },
            "required": ["question"],
        },
    },
]

BUILDER_TOOLS = BUILDER_TOOLS + FORGE_TOOLS
