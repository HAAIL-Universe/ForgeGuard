# Diff Log (overwrite each cycle)

## Cycle Metadata
- Timestamp: 2026-02-15T22:52:18+00:00
- Branch: master
- HEAD: d5e487517142cf64a83ccfa8a2a27a4b1c29bde3
- BASE_HEAD: 284a46d72a95b699f6256e9713b03a673d1335a9
- Diff basis: staged

## Cycle Status
- Status: IN_PROCESS

## Summary
- TODO: 1-5 bullets (what changed, why, scope).

## Files Changed (staged)
- Forge/Contracts/physics.yaml
- app/services/build_service.py
- app/services/tool_executor.py
- tests/test_build_service.py
- tests/test_tool_executor.py
- web/src/pages/BuildProgress.tsx

## git status -sb
    ## master...origin/master [ahead 12]
    M  Forge/Contracts/physics.yaml
    M  app/services/build_service.py
    M  app/services/tool_executor.py
    M  tests/test_build_service.py
    M  tests/test_tool_executor.py
    M  web/src/pages/BuildProgress.tsx

## Verification
- TODO: verification evidence (static -> runtime -> behavior -> contract).

## Notes (optional)
- TODO: blockers, risks, constraints.

## Next Steps
- TODO: next actions (small, specific).

## Minimal Diff Hunks
    diff --git a/Forge/Contracts/physics.yaml b/Forge/Contracts/physics.yaml
    index 0902857..2b9f2e1 100644
    --- a/Forge/Contracts/physics.yaml
    +++ b/Forge/Contracts/physics.yaml
    @@ -183,6 +183,8 @@ paths:
             payload: RecoveryPlanEvent
             type: "tool_use"
             payload: ToolUseEvent
    +        type: "test_run"
    +        payload: TestRunEvent
     
       # -- Projects (Phase 8) -------------------------------------------
     
    @@ -570,3 +572,9 @@ schemas:
         tool_name: string
         input_summary: string
         result_summary: string
    +
    +  TestRunEvent:
    +    command: string
    +    exit_code: integer
    +    passed: boolean
    +    summary: string
    diff --git a/app/services/build_service.py b/app/services/build_service.py
    index d3a4925..9c6e81c 100644
    --- a/app/services/build_service.py
    +++ b/app/services/build_service.py
    @@ -26,7 +26,7 @@ from app.config import settings
     from app.repos import build_repo
     from app.repos import project_repo
     from app.repos.user_repo import get_user_by_id
    -from app.services.tool_executor import BUILDER_TOOLS, execute_tool
    +from app.services.tool_executor import BUILDER_TOOLS, execute_tool_async
     from app.ws_manager import manager
     
     logger = logging.getLogger(__name__)
    @@ -692,13 +692,20 @@ async def _run_build(
                 "- **read_file**: Read a file to check existing code or verify your work.\n"
                 "- **list_directory**: List files/folders to understand project structure before making changes.\n"
                 "- **search_code**: Search for patterns across files to find implementations or imports.\n"
    -            "- **write_file**: Write or overwrite a file. Preferred over === FILE: ... === blocks.\n\n"
    +            "- **write_file**: Write or overwrite a file. Preferred over === FILE: ... === blocks.\n"
    +            "- **run_tests**: Run the test suite to verify your code works.\n"
    +            "- **check_syntax**: Check a file for syntax errors immediately after writing it.\n"
    +            "- **run_command**: Run safe shell commands (pip install, npm install, etc.).\n\n"
                 "Guidelines for tool use:\n"
                 "1. Use list_directory at the start of each phase to understand the current state.\n"
                 "2. Use read_file to examine existing code before modifying it.\n"
                 "3. Prefer write_file tool over === FILE: path === blocks for creating/updating files.\n"
                 "4. Use search_code to find existing patterns, imports, or implementations.\n"
    -            "5. After writing files, use read_file to verify the content was written correctly.\n"
    +            "5. After writing files, use check_syntax to catch syntax errors immediately.\n"
    +            "6. ALWAYS run tests with run_tests before emitting the phase sign-off signal.\n"
    +            "7. If tests fail, read the error output, fix the code with write_file, and re-run.\n"
    +            "8. Only emit === PHASE SIGN-OFF: PASS === when all tests pass.\n"
    +            "9. Use run_command for setup tasks like 'pip install -r requirements.txt' when needed.\n"
             )
     
             # Emit build overview (high-level phase list) at build start
    @@ -794,7 +801,7 @@ async def _run_build(
                 ):
                     if isinstance(item, ToolCall):
                         # --- Tool call detected ---
    -                    tool_result = execute_tool(item.name, item.input, working_dir or "")
    +                    tool_result = await execute_tool_async(item.name, item.input, working_dir or "")
     
                         # Log the tool call
                         input_summary = json.dumps(item.input)[:200]
    @@ -834,6 +841,28 @@ async def _run_build(
                                 }
                             )
     
    +                    # Track run_tests calls ÔÇö emit test_run event
    +                    if item.name == "run_tests":
    +                        exit_code_str = tool_result.split("\n")[0] if tool_result else ""
    +                        exit_code = 0
    +                        try:
    +                            exit_code = int(exit_code_str.split(":")[-1].strip())
    +                        except (ValueError, IndexError):
    +                            pass
    +                        await _broadcast_build_event(
    +                            user_id, build_id, "test_run", {
    +                                "command": item.input.get("command", ""),
    +                                "exit_code": exit_code,
    +                                "passed": exit_code == 0,
    +                                "summary": result_summary,
    +                            }
    +                        )
    +                        await build_repo.append_build_log(
    +                            build_id,
    +                            f"Test run: {item.input.get('command', '')} ÔåÆ exit {exit_code}",
    +                            source="test", level="info" if exit_code == 0 else "warn",
    +                        )
    +
                         tool_calls_this_turn.append({
                             "id": item.id,
                             "name": item.name,
    diff --git a/app/services/tool_executor.py b/app/services/tool_executor.py
    index 1f3f523..2b136c6 100644
    --- a/app/services/tool_executor.py
    +++ b/app/services/tool_executor.py
    @@ -3,8 +3,13 @@
     Each tool handler enforces path sandboxing (no traversal outside working_dir),
     size limits, and returns string results (required by Anthropic tool API).
     All handlers catch exceptions and return error strings rather than raising.
    +
    +Phase 18 tools: read_file, list_directory, search_code, write_file (sync)
    +Phase 19 tools: run_tests, check_syntax, run_command (async -- subprocess)
     """
     
    +import asyncio
    +import ast
     import fnmatch
     import os
     import re
    @@ -17,11 +22,36 @@ from pathlib import Path
     MAX_READ_FILE_BYTES = 50_000  # 50KB max for read_file
     MAX_SEARCH_RESULTS = 50
     MAX_WRITE_FILE_BYTES = 500_000  # 500KB max for write_file
    +MAX_STDOUT_BYTES = 50_000  # 50KB max for subprocess stdout
    +MAX_STDERR_BYTES = 10_000  # 10KB max for subprocess stderr
    +DEFAULT_RUN_TESTS_TIMEOUT = 120  # seconds
    +DEFAULT_CHECK_SYNTAX_TIMEOUT = 30
    +DEFAULT_RUN_COMMAND_TIMEOUT = 60
    +MAX_TEST_RUNS_WARNING = 5  # warn after this many test runs per phase
     SKIP_DIRS = frozenset({
         ".git", "__pycache__", "node_modules", ".venv", "venv",
         ".tox", "dist", "build", ".mypy_cache", ".pytest_cache",
     })
     
    +# Allowlists for command safety
    +RUN_TESTS_PREFIXES = (
    +    "pytest", "python -m pytest", "python3 -m pytest",
    +    "npm test", "npm run test", "npx vitest", "npx jest",
    +)
    +RUN_COMMAND_PREFIXES = (
    +    "pip install", "pip3 install",
    +    "npm install", "npx ",
    +    "python -m ", "python3 -m ",
    +    "cat ", "head ", "tail ", "wc ", "find ", "ls ",
    +    "dir ",  # Windows
    +    "type ",  # Windows cat equivalent
    +)
    +BLOCKED_COMMANDS = frozenset({
    +    "rm", "del", "rmdir", "curl", "wget", "ssh", "scp",
    +    "git push", "git remote", "shutdown", "reboot",
    +    "format", "mkfs", "dd ", "chmod", "chown",
    +})
    +
     
     # ---------------------------------------------------------------------------
     # Path sandboxing
    @@ -63,7 +93,7 @@ def _resolve_sandboxed(rel_path: str, working_dir: str) -> Path | None:
     
     
     def execute_tool(tool_name: str, tool_input: dict, working_dir: str) -> str:
    -    """Dispatch a tool call to the correct handler.
    +    """Dispatch a synchronous tool call to the correct handler.
     
         Args:
             tool_name: Name of the tool to execute.
    @@ -73,13 +103,13 @@ def execute_tool(tool_name: str, tool_input: dict, working_dir: str) -> str:
         Returns:
             String result of the tool execution.
         """
    -    handlers = {
    +    sync_handlers = {
             "read_file": _exec_read_file,
             "list_directory": _exec_list_directory,
             "search_code": _exec_search_code,
             "write_file": _exec_write_file,
         }
    -    handler = handlers.get(tool_name)
    +    handler = sync_handlers.get(tool_name)
         if handler is None:
             return f"Error: Unknown tool '{tool_name}'"
         try:
    @@ -88,6 +118,48 @@ def execute_tool(tool_name: str, tool_input: dict, working_dir: str) -> str:
             return f"Error executing {tool_name}: {exc}"
     
     
    +async def execute_tool_async(tool_name: str, tool_input: dict, working_dir: str) -> str:
    +    """Dispatch a tool call -- supports both sync and async handlers.
    +
    +    This is the main dispatcher used by the build loop. Sync tools (read_file,
    +    list_directory, search_code, write_file) are called directly. Async tools
    +    (run_tests, check_syntax, run_command) use subprocess execution.
    +
    +    Args:
    +        tool_name: Name of the tool to execute.
    +        tool_input: Input parameters for the tool.
    +        working_dir: Absolute path to the build working directory.
    +
    +    Returns:
    +        String result of the tool execution.
    +    """
    +    sync_handlers = {
    +        "read_file": _exec_read_file,
    +        "list_directory": _exec_list_directory,
    +        "search_code": _exec_search_code,
    +        "write_file": _exec_write_file,
    +    }
    +    async_handlers = {
    +        "run_tests": _exec_run_tests,
    +        "check_syntax": _exec_check_syntax,
    +        "run_command": _exec_run_command,
    +    }
    +
    +    if tool_name in sync_handlers:
    +        try:
    +            return sync_handlers[tool_name](tool_input, working_dir)
    +        except Exception as exc:
    +            return f"Error executing {tool_name}: {exc}"
    +
    +    if tool_name in async_handlers:
    +        try:
    +            return await async_handlers[tool_name](tool_input, working_dir)
    +        except Exception as exc:
    +            return f"Error executing {tool_name}: {exc}"
    +
    +    return f"Error: Unknown tool '{tool_name}'"
    +
    +
     def _exec_read_file(inp: dict, working_dir: str) -> str:
         """Read a file from the working directory.
     
    @@ -222,6 +294,189 @@ def _exec_write_file(inp: dict, working_dir: str) -> str:
             return f"Error writing '{rel_path}': {exc}"
     
     
    +# ---------------------------------------------------------------------------
    +# Command validation helpers
    +# ---------------------------------------------------------------------------
    +
    +
    +def _validate_command(command: str, allowed_prefixes: tuple[str, ...]) -> str | None:
    +    """Validate a command against an allowlist.
    +
    +    Returns None if the command is allowed, or an error message string if blocked.
    +    """
    +    if not command or not command.strip():
    +        return "Error: Command is empty"
    +
    +    cmd = command.strip()
    +
    +    # Check for command injection characters
    +    for char in (";", "|", "&", "`", "$", "(", ")", "{", "}"):
    +        if char in cmd:
    +            return f"Error: Command contains disallowed character '{char}'"
    +
    +    # Check against blocked commands
    +    cmd_lower = cmd.lower()
    +    for blocked in BLOCKED_COMMANDS:
    +        if cmd_lower.startswith(blocked):
    +            return f"Error: Command '{blocked}' is not allowed"
    +
    +    # Check against allowlist
    +    if not any(cmd_lower.startswith(prefix) for prefix in allowed_prefixes):
    +        return f"Error: Command not in allowlist. Allowed prefixes: {', '.join(allowed_prefixes)}"
    +
    +    return None
    +
    +
    +def _truncate_output(text: str, max_bytes: int) -> str:
    +    """Truncate output to max_bytes, appending a truncation notice."""
    +    if len(text) <= max_bytes:
    +        return text
    +    return text[:max_bytes] + f"\n\n[... truncated at {max_bytes} bytes ...]"
    +
    +
    +async def _run_subprocess(
    +    command: str, working_dir: str, timeout: int,
    +) -> tuple[int, str, str]:
    +    """Run a subprocess command with timeout and output limits.
    +
    +    Returns (exit_code, stdout, stderr).
    +    """
    +    # Restricted environment: only PATH
    +    env = {"PATH": os.environ.get("PATH", "")}
    +    # Add minimal env vars needed for Python/Node
    +    for key in ("SYSTEMROOT", "TEMP", "TMP", "HOME", "USERPROFILE", "VIRTUAL_ENV"):
    +        val = os.environ.get(key)
    +        if val:
    +            env[key] = val
    +
    +    proc = await asyncio.create_subprocess_shell(
    +        command,
    +        stdout=asyncio.subprocess.PIPE,
    +        stderr=asyncio.subprocess.PIPE,
    +        cwd=working_dir,
    +        env=env,
    +    )
    +    try:
    +        stdout_bytes, stderr_bytes = await asyncio.wait_for(
    +            proc.communicate(), timeout=timeout,
    +        )
    +    except asyncio.TimeoutError:
    +        proc.kill()
    +        await proc.wait()
    +        return -1, "", f"Error: Command timed out after {timeout}s"
    +
    +    stdout = stdout_bytes.decode("utf-8", errors="replace") if stdout_bytes else ""
    +    stderr = stderr_bytes.decode("utf-8", errors="replace") if stderr_bytes else ""
    +
    +    stdout = _truncate_output(stdout, MAX_STDOUT_BYTES)
    +    stderr = _truncate_output(stderr, MAX_STDERR_BYTES)
    +
    +    return proc.returncode or 0, stdout, stderr
    +
    +
    +# ---------------------------------------------------------------------------
    +# Async tool handlers (Phase 19)
    +# ---------------------------------------------------------------------------
    +
    +
    +async def _exec_run_tests(inp: dict, working_dir: str) -> str:
    +    """Run a test command in the working directory.
    +
    +    Input: { "command": "pytest tests/ -v", "timeout": 120 }
    +    Returns: Exit code, stdout, and stderr.
    +    """
    +    command = inp.get("command", "")
    +    timeout = min(int(inp.get("timeout", DEFAULT_RUN_TESTS_TIMEOUT)), 300)
    +
    +    error = _validate_command(command, RUN_TESTS_PREFIXES)
    +    if error:
    +        return error
    +
    +    exit_code, stdout, stderr = await _run_subprocess(command, working_dir, timeout)
    +
    +    parts = [f"Exit code: {exit_code}"]
    +    if stdout:
    +        parts.append(f"--- stdout ---\n{stdout}")
    +    if stderr:
    +        parts.append(f"--- stderr ---\n{stderr}")
    +
    +    return "\n".join(parts)
    +
    +
    +async def _exec_check_syntax(inp: dict, working_dir: str) -> str:
    +    """Check syntax of a file in the working directory.
    +
    +    Input: { "file_path": "app/services/foo.py" }
    +    Returns: Syntax errors with line numbers, or "No errors" if clean.
    +    """
    +    file_path = inp.get("file_path", "")
    +    target = _resolve_sandboxed(file_path, working_dir)
    +    if target is None:
    +        return f"Error: Invalid or disallowed path '{file_path}'"
    +    if not target.exists():
    +        return f"Error: File not found '{file_path}'"
    +    if not target.is_file():
    +        return f"Error: '{file_path}' is not a file"
    +
    +    ext = target.suffix.lower()
    +
    +    # Python files: use ast.parse for syntax checking
    +    if ext == ".py":
    +        try:
    +            source = target.read_text(encoding="utf-8", errors="replace")
    +            ast.parse(source, filename=file_path)
    +            return f"No syntax errors in {file_path}"
    +        except SyntaxError as e:
    +            return f"Syntax error in {file_path}:{e.lineno}: {e.msg}"
    +        except Exception as e:
    +            return f"Error checking {file_path}: {e}"
    +
    +    # TypeScript/JavaScript: use tsc --noEmit or node --check
    +    if ext in (".ts", ".tsx"):
    +        timeout = int(inp.get("timeout", DEFAULT_CHECK_SYNTAX_TIMEOUT))
    +        cmd = f"npx tsc --noEmit --pretty false {target}"
    +        exit_code, stdout, stderr = await _run_subprocess(cmd, working_dir, timeout)
    +        if exit_code == 0:
    +            return f"No syntax errors in {file_path}"
    +        output = (stdout + "\n" + stderr).strip()
    +        return f"TypeScript errors in {file_path}:\n{output}"
    +
    +    if ext in (".js", ".jsx", ".mjs"):
    +        timeout = int(inp.get("timeout", DEFAULT_CHECK_SYNTAX_TIMEOUT))
    +        cmd = f"node --check {target}"
    +        exit_code, stdout, stderr = await _run_subprocess(cmd, working_dir, timeout)
    +        if exit_code == 0:
    +            return f"No syntax errors in {file_path}"
    +        output = (stdout + "\n" + stderr).strip()
    +        return f"JavaScript errors in {file_path}:\n{output}"
    +
    +    return f"Error: Unsupported file type '{ext}' for syntax checking"
    +
    +
    +async def _exec_run_command(inp: dict, working_dir: str) -> str:
    +    """Run a sandboxed shell command in the working directory.
    +
    +    Input: { "command": "pip install -r requirements.txt", "timeout": 60 }
    +    Returns: Exit code + stdout/stderr.
    +    """
    +    command = inp.get("command", "")
    +    timeout = min(int(inp.get("timeout", DEFAULT_RUN_COMMAND_TIMEOUT)), 300)
    +
    +    error = _validate_command(command, RUN_COMMAND_PREFIXES)
    +    if error:
    +        return error
    +
    +    exit_code, stdout, stderr = await _run_subprocess(command, working_dir, timeout)
    +
    +    parts = [f"Exit code: {exit_code}"]
    +    if stdout:
    +        parts.append(f"--- stdout ---\n{stdout}")
    +    if stderr:
    +        parts.append(f"--- stderr ---\n{stderr}")
    +
    +    return "\n".join(parts)
    +
    +
     # ---------------------------------------------------------------------------
     # Tool Specifications (Anthropic format)
     # ---------------------------------------------------------------------------
    @@ -307,4 +562,69 @@ BUILDER_TOOLS = [
                 "required": ["path", "content"],
             },
         },
    +    {
    +        "name": "run_tests",
    +        "description": (
    +            "Run the project test suite or a subset of tests in the working directory. "
    +            "Returns exit code, stdout, and stderr. Use this after writing code to verify "
    +            "it works before signing off a phase. Only sign off when tests pass."
    +        ),
    +        "input_schema": {
    +            "type": "object",
    +            "properties": {
    +                "command": {
    +                    "type": "string",
    +                    "description": (
    +                        "Test command to run. Must start with an allowed prefix: "
    +                        "pytest, python -m pytest, npm test, npx vitest, npx jest."
    +                    ),
    +                },
    +                "timeout": {
    +                    "type": "integer",
    +                    "description": "Timeout in seconds (default 120, max 300).",
    +                },
    +            },
    +            "required": ["command"],
    +        },
    +    },
    +    {
    +        "name": "check_syntax",
    +        "description": (
    +            "Check a file for syntax errors. For Python files, uses ast.parse. "
    +            "For TypeScript/JavaScript, uses tsc/node --check. "
    +            "Use this after writing files to catch syntax errors immediately."
    +        ),
    +        "input_schema": {
    +            "type": "object",
    +            "properties": {
    +                "file_path": {
    +                    "type": "string",
    +                    "description": "Relative path to the file to check.",
    +                },
    +            },
    +            "required": ["file_path"],
    +        },
    +    },
    +    {
    +        "name": "run_command",
    +        "description": (
    +            "Run a sandboxed shell command in the working directory. "
    +            "Allowed commands include: pip install, npm install, python -m, npx, "
    +            "cat, head, tail, wc, find, ls. Destructive commands are blocked."
    +        ),
    +        "input_schema": {
    +            "type": "object",
    +            "properties": {
    +                "command": {
    +                    "type": "string",
    +                    "description": "Shell command to run. Must start with an allowed prefix.",
    +                },
    +                "timeout": {
    +                    "type": "integer",
    +                    "description": "Timeout in seconds (default 60, max 300).",
    +                },
    +            },
    +            "required": ["command"],
    +        },
    +    },
     ]
    diff --git a/tests/test_build_service.py b/tests/test_build_service.py
    index b47c652..a57b5ee 100644
    --- a/tests/test_build_service.py
    +++ b/tests/test_build_service.py
    @@ -2108,7 +2108,7 @@ async def _fake_stream_with_tool_call(*args, **kwargs):
     
     
     @pytest.mark.asyncio
    -@patch("app.services.build_service.execute_tool")
    +@patch("app.services.build_service.execute_tool_async", new_callable=AsyncMock)
     @patch("app.services.build_service.manager")
     @patch("app.services.build_service.project_repo")
     @patch("app.services.build_service.build_repo")
    @@ -2172,7 +2172,7 @@ async def _fake_stream_with_write_tool(*args, **kwargs):
    ... (274 lines truncated, 774 total)
