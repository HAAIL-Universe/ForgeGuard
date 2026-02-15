"""Tool executor -- sandboxed tool handlers for builder agent tool use.

Each tool handler enforces path sandboxing (no traversal outside working_dir),
size limits, and returns string results (required by Anthropic tool API).
All handlers catch exceptions and return error strings rather than raising.
"""

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
SKIP_DIRS = frozenset({
    ".git", "__pycache__", "node_modules", ".venv", "venv",
    ".tox", "dist", "build", ".mypy_cache", ".pytest_cache",
})


# ---------------------------------------------------------------------------
# Path sandboxing
# ---------------------------------------------------------------------------


def _resolve_sandboxed(rel_path: str, working_dir: str) -> Path | None:
    """Resolve a relative path within working_dir, enforcing sandbox.

    Returns the resolved absolute Path if safe, or None if the path
    would escape the sandbox (e.g. via `..` traversal or absolute paths).
    """
    if not rel_path or not working_dir:
        return None

    # Reject absolute paths
    if os.path.isabs(rel_path):
        return None

    # Reject path traversal
    if ".." in rel_path.split(os.sep) or ".." in rel_path.split("/"):
        return None

    root = Path(working_dir).resolve()
    target = (root / rel_path).resolve()

    # Ensure the resolved path is within the working directory
    try:
        target.relative_to(root)
    except ValueError:
        return None

    return target


# ---------------------------------------------------------------------------
# Tool handlers
# ---------------------------------------------------------------------------


def execute_tool(tool_name: str, tool_input: dict, working_dir: str) -> str:
    """Dispatch a tool call to the correct handler.

    Args:
        tool_name: Name of the tool to execute.
        tool_input: Input parameters for the tool.
        working_dir: Absolute path to the build working directory.

    Returns:
        String result of the tool execution.
    """
    handlers = {
        "read_file": _exec_read_file,
        "list_directory": _exec_list_directory,
        "search_code": _exec_search_code,
        "write_file": _exec_write_file,
    }
    handler = handlers.get(tool_name)
    if handler is None:
        return f"Error: Unknown tool '{tool_name}'"
    try:
        return handler(tool_input, working_dir)
    except Exception as exc:
        return f"Error executing {tool_name}: {exc}"


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

    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return f"OK: Wrote {len(content)} bytes to {rel_path}"
    except Exception as exc:
        return f"Error writing '{rel_path}': {exc}"


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
]
