"""Structured file reader — read files, line ranges, and symbols.

All functions take a ``Workspace`` for sandbox-safe path resolution
and return ``ToolResponse`` with structured JSON data.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

from forge_ide.contracts import ToolResponse
from forge_ide.errors import SandboxViolation
from forge_ide.workspace import Workspace, _detect_language

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_MAX_FILE_BYTES: int = 100_000  # 100 KB

_BINARY_EXTENSIONS: frozenset[str] = frozenset(
    {
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".ico",
        ".bmp",
        ".webp",
        ".woff",
        ".woff2",
        ".ttf",
        ".eot",
        ".otf",
        ".zip",
        ".tar",
        ".gz",
        ".bz2",
        ".xz",
        ".7z",
        ".rar",
        ".exe",
        ".dll",
        ".so",
        ".dylib",
        ".pyc",
        ".pyo",
        ".class",
        ".o",
        ".a",
        ".lib",
        ".bin",
        ".dat",
        ".pdf",
        ".doc",
        ".docx",
        ".xls",
        ".xlsx",
        ".ppt",
        ".pptx",
        ".sqlite",
        ".db",
    }
)


# ---------------------------------------------------------------------------
# Encoding helpers
# ---------------------------------------------------------------------------


def detect_encoding(raw: bytes) -> str:
    """Detect encoding of raw bytes.

    Checks for UTF-8 BOM, then tries UTF-8, falls back to latin-1.
    """
    if raw[:3] == b"\xef\xbb\xbf":
        return "utf-8-sig"
    try:
        raw.decode("utf-8")
        return "utf-8"
    except UnicodeDecodeError:
        return "latin-1"


def is_binary(path: Path) -> bool:
    """Check if a file is binary by extension or null-byte probe."""
    if path.suffix.lower() in _BINARY_EXTENSIONS:
        return True
    try:
        chunk = path.read_bytes()[:512]
        return b"\x00" in chunk
    except OSError:
        return False


# ---------------------------------------------------------------------------
# read_file
# ---------------------------------------------------------------------------


def read_file(
    workspace: Workspace,
    rel_path: str,
    *,
    max_bytes: int = DEFAULT_MAX_FILE_BYTES,
) -> ToolResponse:
    """Read an entire file and return structured metadata.

    Parameters
    ----------
    workspace : Workspace
        The workspace for sandbox resolution.
    rel_path : str
        Relative path within the workspace.
    max_bytes : int
        Maximum allowed file size in bytes.

    Returns
    -------
    ToolResponse
        On success: ``{path, content, line_count, size_bytes, language, encoding}``
    """
    try:
        target = workspace.resolve(rel_path)
    except SandboxViolation as exc:
        return ToolResponse.fail(str(exc))

    if not target.exists():
        return ToolResponse.fail(f"File not found: '{rel_path}'")
    if not target.is_file():
        return ToolResponse.fail(f"Not a file: '{rel_path}'")
    if is_binary(target):
        return ToolResponse.fail(f"Binary file cannot be read as text: '{rel_path}'")

    try:
        raw = target.read_bytes()
    except OSError as exc:
        return ToolResponse.fail(f"Error reading '{rel_path}': {exc}")

    if len(raw) > max_bytes:
        return ToolResponse.fail(
            f"File exceeds size limit: {len(raw)} bytes > {max_bytes} bytes"
        )

    encoding = detect_encoding(raw)
    content = raw.decode(encoding, errors="replace")
    line_count = content.count("\n") + (1 if content and not content.endswith("\n") else 0)
    if not content:
        line_count = 0

    language = _detect_language(target.suffix)

    return ToolResponse.ok(
        {
            "path": rel_path,
            "content": content,
            "line_count": line_count,
            "size_bytes": len(raw),
            "language": language,
            "encoding": encoding,
        }
    )


# ---------------------------------------------------------------------------
# read_range
# ---------------------------------------------------------------------------


def read_range(
    workspace: Workspace,
    rel_path: str,
    start_line: int,
    end_line: int,
    *,
    max_bytes: int = DEFAULT_MAX_FILE_BYTES,
) -> ToolResponse:
    """Read a range of lines from a file.

    Lines are 1-based inclusive.  *end_line* is clamped to the actual
    line count (no error on beyond-EOF).

    Returns
    -------
    ToolResponse
        On success: ``{path, start_line, end_line, content, lines}``
    """
    if start_line < 1:
        return ToolResponse.fail("start_line must be >= 1")
    if end_line < start_line:
        return ToolResponse.fail("end_line must be >= start_line")

    try:
        target = workspace.resolve(rel_path)
    except SandboxViolation as exc:
        return ToolResponse.fail(str(exc))

    if not target.exists():
        return ToolResponse.fail(f"File not found: '{rel_path}'")
    if not target.is_file():
        return ToolResponse.fail(f"Not a file: '{rel_path}'")
    if is_binary(target):
        return ToolResponse.fail(f"Binary file cannot be read as text: '{rel_path}'")

    try:
        raw = target.read_bytes()
    except OSError as exc:
        return ToolResponse.fail(f"Error reading '{rel_path}': {exc}")

    if len(raw) > max_bytes:
        return ToolResponse.fail(
            f"File exceeds size limit: {len(raw)} bytes > {max_bytes} bytes"
        )

    encoding = detect_encoding(raw)
    content = raw.decode(encoding, errors="replace")
    all_lines = content.splitlines()

    if not all_lines:
        return ToolResponse.ok(
            {
                "path": rel_path,
                "start_line": start_line,
                "end_line": start_line,
                "content": "",
                "lines": [],
            }
        )

    # Clamp end_line to actual line count
    actual_end = min(end_line, len(all_lines))
    selected = all_lines[start_line - 1 : actual_end]

    return ToolResponse.ok(
        {
            "path": rel_path,
            "start_line": start_line,
            "end_line": actual_end,
            "content": "\n".join(selected),
            "lines": selected,
        }
    )


# ---------------------------------------------------------------------------
# read_symbol
# ---------------------------------------------------------------------------

# Regex for TS/JS symbol declarations
_TS_SYMBOL_RE = re.compile(
    r"^(?:export\s+)?(?:default\s+)?"
    r"(?:(?:async\s+)?function\*?\s+|class\s+|"
    r"(?:const|let|var)\s+)"
    r"(?P<name>\w+)",
    re.MULTILINE,
)

_LANG_PYTHON = {"python"}
_LANG_TS_JS = {"typescript", "typescriptreact", "javascript", "javascriptreact"}


def read_symbol(
    workspace: Workspace,
    rel_path: str,
    symbol_name: str,
) -> ToolResponse:
    """Extract a named symbol (function, class, variable) from a file.

    Uses ``ast.parse`` for Python files and regex for TS/JS files.

    Returns
    -------
    ToolResponse
        On success: ``{path, symbol, kind, start_line, end_line, content}``
    """
    try:
        target = workspace.resolve(rel_path)
    except SandboxViolation as exc:
        return ToolResponse.fail(str(exc))

    if not target.exists():
        return ToolResponse.fail(f"File not found: '{rel_path}'")
    if not target.is_file():
        return ToolResponse.fail(f"Not a file: '{rel_path}'")
    if is_binary(target):
        return ToolResponse.fail(f"Binary file cannot be read as text: '{rel_path}'")

    language = _detect_language(target.suffix)

    try:
        content = target.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return ToolResponse.fail(f"Error reading '{rel_path}': {exc}")

    if not content.strip():
        return ToolResponse.fail(f"File is empty: '{rel_path}'")

    if language in _LANG_PYTHON:
        return _read_symbol_python(rel_path, content, symbol_name)
    elif language in _LANG_TS_JS:
        return _read_symbol_ts_js(rel_path, content, symbol_name)
    else:
        return ToolResponse.fail(
            f"Unsupported language for symbol extraction: '{language}'"
        )


def _read_symbol_python(
    rel_path: str, source: str, symbol_name: str
) -> ToolResponse:
    """Extract a Python symbol using ``ast.parse``."""
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return ToolResponse.fail(f"Syntax error in '{rel_path}': {exc}")

    lines = source.splitlines()

    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == symbol_name:
            kind = "async_function"
        elif isinstance(node, ast.FunctionDef) and node.name == symbol_name:
            kind = "function"
        elif isinstance(node, ast.ClassDef) and node.name == symbol_name:
            kind = "class"
        else:
            continue

        start = node.lineno
        end = node.end_lineno or node.lineno
        snippet = "\n".join(lines[start - 1 : end])
        return ToolResponse.ok(
            {
                "path": rel_path,
                "symbol": symbol_name,
                "kind": kind,
                "start_line": start,
                "end_line": end,
                "content": snippet,
            }
        )

    return ToolResponse.fail(f"Symbol '{symbol_name}' not found in '{rel_path}'")


def _read_symbol_ts_js(
    rel_path: str, source: str, symbol_name: str
) -> ToolResponse:
    """Extract a TS/JS symbol using regex + brace counting."""
    lines = source.splitlines()

    for i, line in enumerate(lines):
        m = _TS_SYMBOL_RE.match(line)
        if m and m.group("name") == symbol_name:
            start_line = i + 1  # 1-based

            # Determine kind from the declaration
            stripped = line.lstrip()
            if stripped.startswith("export "):
                stripped = stripped[len("export "):].lstrip()
            if stripped.startswith("default "):
                stripped = stripped[len("default "):].lstrip()

            if stripped.startswith("class"):
                kind = "class"
            elif "function" in stripped.split(symbol_name)[0]:
                if "async" in line.split("function")[0]:
                    kind = "async_function"
                else:
                    kind = "function"
            else:
                kind = "variable"

            # Find the end by brace counting (or semicolon for variables)
            end_line = _find_block_end(lines, i)

            snippet = "\n".join(lines[i : end_line])
            return ToolResponse.ok(
                {
                    "path": rel_path,
                    "symbol": symbol_name,
                    "kind": kind,
                    "start_line": start_line,
                    "end_line": end_line,
                    "content": snippet,
                }
            )

    return ToolResponse.fail(f"Symbol '{symbol_name}' not found in '{rel_path}'")


def _find_block_end(lines: list[str], start_idx: int) -> int:
    """Find the end line of a brace-delimited block or semicolon-terminated statement."""
    depth = 0
    found_open = False
    for i in range(start_idx, len(lines)):
        for ch in lines[i]:
            if ch == "{":
                depth += 1
                found_open = True
            elif ch == "}":
                depth -= 1
                if found_open and depth == 0:
                    return i + 1  # 1-based
            elif ch == ";" and not found_open:
                return i + 1  # 1-based

    # If no closing brace found, return last line
    return len(lines)
