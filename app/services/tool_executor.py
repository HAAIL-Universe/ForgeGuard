"""Tool executor -- sandboxed tool handlers for builder agent tool use.

Each tool handler enforces path sandboxing (no traversal outside working_dir),
size limits, and returns string results (required by Anthropic tool API).
All handlers catch exceptions and return error strings rather than raising.

Phase 18 tools: read_file, list_directory, search_code, write_file (sync)
Phase 19 tools: run_tests, check_syntax, run_command (async -- subprocess)
"""

import asyncio
import ast
import fnmatch
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
    }
    handler = sync_handlers.get(tool_name)
    if handler is None:
        return f"Error: Unknown tool '{tool_name}'"
    try:
        return handler(tool_input, working_dir)
    except Exception as exc:
        return f"Error executing {tool_name}: {exc}"


async def execute_tool_async(tool_name: str, tool_input: dict, working_dir: str) -> str:
    """Dispatch a tool call -- supports both sync and async handlers.

    This is the main dispatcher used by the build loop. Sync tools (read_file,
    list_directory, search_code, write_file) are called directly. Async tools
    (run_tests, check_syntax, run_command) use subprocess execution.

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
    }
    async_handlers = {
        "run_tests": _exec_run_tests,
        "check_syntax": _exec_check_syntax,
        "run_command": _exec_run_command,
    }

    if tool_name in sync_handlers:
        try:
            return sync_handlers[tool_name](tool_input, working_dir)
        except Exception as exc:
            return f"Error executing {tool_name}: {exc}"

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


async def _run_subprocess(
    command: str, working_dir: str, timeout: int,
) -> tuple[int, str, str]:
    """Run a subprocess command with timeout and output limits.

    Uses subprocess.run in a thread to avoid asyncio event-loop limitations
    on Windows (ProactorEventLoop requirement for create_subprocess_shell).

    Returns (exit_code, stdout, stderr).
    """
    # Restricted environment: only PATH
    env = {"PATH": os.environ.get("PATH", "")}
    # Add minimal env vars needed for Python/Node
    for key in ("SYSTEMROOT", "TEMP", "TMP", "HOME", "USERPROFILE", "VIRTUAL_ENV"):
        val = os.environ.get(key)
        if val:
            env[key] = val

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

    return await asyncio.get_event_loop().run_in_executor(None, _sync)


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
