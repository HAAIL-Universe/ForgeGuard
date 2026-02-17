"""Python language intelligence — parse tool outputs and extract symbols.

All functions are **pure** (string in → model out).  No subprocess execution,
no filesystem access.  The caller runs the external tools and feeds their
raw stdout/stderr text into these parsers.

Parsers
-------
- ``parse_ruff_json``   — ruff ``--output-format json`` stdout
- ``parse_pyright_json`` — pyright ``--outputjson`` stdout
- ``parse_python_ast_errors`` — ``ast.parse`` fallback for syntax errors

Extractors
----------
- ``extract_symbols``   — ``ast``-based class/function/method/variable outline
- ``resolve_imports``   — import statement classification (stdlib / workspace / third-party)
"""

from __future__ import annotations

import ast
import json
import os
from typing import TYPE_CHECKING

from forge_ide.contracts import Diagnostic
from forge_ide.errors import ParseError
from forge_ide.lang import ImportInfo, Symbol

if TYPE_CHECKING:
    pass

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Python 3.12 standard library top-level modules (compiled from sys.stdlib_module_names)
PYTHON_STDLIB_MODULES: frozenset[str] = frozenset({
    "__future__", "_thread", "abc", "aifc", "argparse", "array", "ast",
    "asyncio", "atexit", "base64", "bdb", "binascii", "binhex", "bisect",
    "builtins", "bz2", "calendar", "cgi", "cgitb", "chunk", "cmath",
    "cmd", "code", "codecs", "codeop", "collections", "colorsys", "compileall",
    "concurrent", "configparser", "contextlib", "contextvars", "copy",
    "copyreg", "cProfile", "crypt", "csv", "ctypes", "curses", "dataclasses",
    "datetime", "dbm", "decimal", "difflib", "dis", "distutils", "doctest",
    "email", "encodings", "enum", "errno", "faulthandler", "fcntl",
    "filecmp", "fileinput", "fnmatch", "fractions", "ftplib", "functools",
    "gc", "getopt", "getpass", "gettext", "glob", "graphlib", "grp",
    "gzip", "hashlib", "heapq", "hmac", "html", "http", "idlelib", "imaplib",
    "imghdr", "imp", "importlib", "inspect", "io", "ipaddress", "itertools",
    "json", "keyword", "lib2to3", "linecache", "locale", "logging",
    "lzma", "mailbox", "mailcap", "marshal", "math", "mimetypes", "mmap",
    "modulefinder", "multiprocessing", "netrc", "nis", "nntplib", "numbers",
    "operator", "optparse", "os", "ossaudiodev", "pathlib", "pdb",
    "pickle", "pickletools", "pipes", "pkgutil", "platform", "plistlib",
    "poplib", "posix", "posixpath", "pprint", "profile", "pstats", "pty",
    "pwd", "py_compile", "pyclbr", "pydoc", "queue", "quopri", "random",
    "re", "readline", "reprlib", "resource", "rlcompleter", "runpy",
    "sched", "secrets", "select", "selectors", "shelve", "shlex", "shutil",
    "signal", "site", "smtpd", "smtplib", "sndhdr", "socket", "socketserver",
    "spwd", "sqlite3", "sre_compile", "sre_constants", "sre_parse",
    "ssl", "stat", "statistics", "string", "stringprep", "struct",
    "subprocess", "sunau", "symtable", "sys", "sysconfig", "syslog",
    "tabnanny", "tarfile", "telnetlib", "tempfile", "termios", "test",
    "textwrap", "threading", "time", "timeit", "tkinter", "token",
    "tokenize", "tomllib", "trace", "traceback", "tracemalloc", "tty",
    "turtle", "turtledemo", "types", "typing", "unicodedata", "unittest",
    "urllib", "uu", "uuid", "venv", "warnings", "wave", "weakref",
    "webbrowser", "winreg", "winsound", "wsgiref", "xdrlib", "xml",
    "xmlrpc", "zipapp", "zipfile", "zipimport", "zlib",
    # Common sub-packages that appear as top-level imports
    "collections.abc", "concurrent.futures", "email.mime",
    "http.client", "http.server", "importlib.metadata",
    "multiprocessing.pool", "os.path", "typing.io",
    "unittest.mock", "urllib.parse", "urllib.request",
    "xml.etree", "xml.etree.ElementTree",
})


# ---------------------------------------------------------------------------
# Ruff JSON parser
# ---------------------------------------------------------------------------


def parse_ruff_json(raw: str) -> list[Diagnostic]:
    """Parse ruff ``--output-format json`` output into diagnostics.

    Expected shape (list of objects)::

        [{"code": "E501", "message": "...", "filename": "...",
          "location": {"row": 1, "column": 1},
          "end_location": {"row": 1, "column": 80}}]

    Returns an empty list for empty input.
    Raises ``ParseError`` on invalid JSON.
    """
    if not raw or not raw.strip():
        return []

    try:
        entries = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ParseError(raw, "ruff_json") from exc

    if not isinstance(entries, list):
        raise ParseError(raw, "ruff_json")

    diagnostics: list[Diagnostic] = []
    for entry in entries:
        loc = entry.get("location", {})
        code = entry.get("code") or entry.get("rule")
        diagnostics.append(Diagnostic(
            file=entry.get("filename", ""),
            line=loc.get("row", 1),
            column=loc.get("column", 0),
            message=entry.get("message", ""),
            severity=_ruff_severity(code),
            code=str(code) if code else None,
        ))

    return diagnostics


def _ruff_severity(code: str | None) -> str:
    """Map ruff rule codes to severity levels.

    - E / F / W codes → varying severity
    - Default: ``"warning"``
    """
    if not code:
        return "warning"
    first = code[0].upper()
    if first == "F":
        return "error"       # pyflakes: undefined name, etc.
    if first == "E":
        return "warning"     # pycodestyle errors
    if first == "W":
        return "warning"     # pycodestyle warnings
    if first == "I":
        return "info"        # isort
    return "warning"


# ---------------------------------------------------------------------------
# Pyright JSON parser
# ---------------------------------------------------------------------------


def parse_pyright_json(raw: str) -> list[Diagnostic]:
    """Parse pyright ``--outputjson`` output into diagnostics.

    Expected shape::

        {"generalDiagnostics": [
            {"file": "...", "severity": "error", "message": "...",
             "range": {"start": {"line": 0, "character": 0}},
             "rule": "reportMissingImports"}
        ]}

    Returns an empty list for empty input.
    Raises ``ParseError`` on invalid JSON.
    """
    if not raw or not raw.strip():
        return []

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ParseError(raw, "pyright_json") from exc

    if not isinstance(data, dict):
        raise ParseError(raw, "pyright_json")

    entries = data.get("generalDiagnostics", [])
    if not isinstance(entries, list):
        entries = []

    diagnostics: list[Diagnostic] = []
    for entry in entries:
        rng = entry.get("range", {})
        start = rng.get("start", {})
        sev_raw = entry.get("severity", "error")
        diagnostics.append(Diagnostic(
            file=entry.get("file", ""),
            line=start.get("line", 0) + 1,  # pyright uses 0-based lines
            column=start.get("character", 0),
            message=entry.get("message", ""),
            severity=_pyright_severity(sev_raw),
            code=entry.get("rule"),
        ))

    return diagnostics


def _pyright_severity(raw: str) -> str:
    """Map pyright severity string to our severity levels."""
    mapping = {
        "error": "error",
        "warning": "warning",
        "information": "info",
        "hint": "hint",
    }
    return mapping.get(raw.lower(), "warning")


# ---------------------------------------------------------------------------
# AST fallback parser
# ---------------------------------------------------------------------------


def parse_python_ast_errors(source: str, *, path: str = "") -> list[Diagnostic]:
    """Try ``ast.parse`` and return syntax errors as diagnostics.

    Returns an empty list if the source parses successfully.
    """
    if not source or not source.strip():
        return []

    try:
        ast.parse(source, filename=path or "<string>")
    except SyntaxError as exc:
        return [Diagnostic(
            file=path,
            line=exc.lineno or 1,
            column=exc.offset or 0,
            message=exc.msg,
            severity="error",
            code="SyntaxError",
        )]

    return []


# ---------------------------------------------------------------------------
# Symbol extraction
# ---------------------------------------------------------------------------


def extract_symbols(source: str) -> list[Symbol]:
    """Extract symbol outline from Python source using ``ast.parse``.

    Extracts:
    - Module-level functions (``function``) and async functions
    - Classes (``class``)
    - Methods within classes (``method``, with ``parent``)
    - Module-level variable assignments (``variable``)

    Returns an empty list on parse failure or empty source.
    """
    if not source or not source.strip():
        return []

    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []

    symbols: list[Symbol] = []

    for node in ast.iter_child_nodes(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            symbols.append(Symbol(
                name=node.name,
                kind="function",
                start_line=node.lineno,
                end_line=node.end_lineno or node.lineno,
            ))
        elif isinstance(node, ast.ClassDef):
            class_end = node.end_lineno or node.lineno
            symbols.append(Symbol(
                name=node.name,
                kind="class",
                start_line=node.lineno,
                end_line=class_end,
            ))
            # Extract methods
            for child in ast.iter_child_nodes(node):
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    symbols.append(Symbol(
                        name=child.name,
                        kind="method",
                        start_line=child.lineno,
                        end_line=child.end_lineno or child.lineno,
                        parent=node.name,
                    ))
                elif isinstance(child, ast.ClassDef):
                    symbols.append(Symbol(
                        name=child.name,
                        kind="class",
                        start_line=child.lineno,
                        end_line=child.end_lineno or child.lineno,
                        parent=node.name,
                    ))
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    name = target.id
                    if name.startswith("_"):
                        continue
                    kind = "constant" if name.isupper() else "variable"
                    symbols.append(Symbol(
                        name=name,
                        kind=kind,
                        start_line=node.lineno,
                        end_line=node.end_lineno or node.lineno,
                    ))
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            name = node.target.id
            if not name.startswith("_"):
                kind = "constant" if name.isupper() else "variable"
                symbols.append(Symbol(
                    name=name,
                    kind=kind,
                    start_line=node.lineno,
                    end_line=node.end_lineno or node.lineno,
                ))

    return symbols


# ---------------------------------------------------------------------------
# Import resolution
# ---------------------------------------------------------------------------


def resolve_imports(
    source: str,
    file_path: str,
    workspace_files: list[str],
    *,
    stdlib_modules: frozenset[str] | None = None,
) -> list[ImportInfo]:
    """Parse and classify imports from Python source.

    Parameters
    ----------
    source:
        Python source code.
    file_path:
        Workspace-relative path of the file (for relative import resolution).
    workspace_files:
        List of all workspace-relative file paths (e.g. ``["app/main.py", ...]``).
    stdlib_modules:
        Override stdlib module set (defaults to ``PYTHON_STDLIB_MODULES``).

    Returns a list of ``ImportInfo`` objects with ``is_stdlib`` and
    ``resolved_path`` populated where possible.
    """
    if not source or not source.strip():
        return []

    stdlib = stdlib_modules if stdlib_modules is not None else PYTHON_STDLIB_MODULES

    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []

    # Build lookup set for workspace resolution
    # Map module paths to file paths:  "app.main" → "app/main.py"
    ws_lookup: dict[str, str] = {}
    for ws_path in workspace_files:
        # Convert file path to potential module path
        if ws_path.endswith(".py"):
            mod_path = ws_path[:-3].replace(os.sep, ".").replace("/", ".")
            ws_lookup[mod_path] = ws_path
            # Also store __init__ package resolution
            if mod_path.endswith(".__init__"):
                ws_lookup[mod_path[:-9]] = ws_path

    file_dir = os.path.dirname(file_path).replace(os.sep, "/")

    imports: list[ImportInfo] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                module = alias.name
                top = module.split(".")[0]
                is_std = top in stdlib or module in stdlib
                resolved = ws_lookup.get(module)
                imports.append(ImportInfo(
                    module=module,
                    names=[alias.asname] if alias.asname else [],
                    resolved_path=resolved,
                    is_stdlib=is_std,
                ))

        elif isinstance(node, ast.ImportFrom):
            level = node.level or 0
            module = node.module or ""
            names = [a.name for a in (node.names or [])]

            if level > 0:
                # Relative import
                parts = file_dir.split("/") if file_dir else []
                # Go up `level - 1` directories
                base_parts = parts[:max(0, len(parts) - (level - 1))]
                if module:
                    abs_module = ".".join(base_parts + [module]) if base_parts else module
                else:
                    abs_module = ".".join(base_parts)

                resolved = None
                # For "from . import X" (no module), try individual names first
                if not module:
                    for name in names:
                        candidate = ".".join(base_parts + [name]) if base_parts else name
                        if candidate in ws_lookup:
                            resolved = ws_lookup[candidate]
                            break

                # Fall back to package-level resolution
                if resolved is None:
                    if module:
                        abs_module = ".".join(base_parts + [module]) if base_parts else module
                    else:
                        abs_module = ".".join(base_parts)
                    resolved = ws_lookup.get(abs_module)

                imports.append(ImportInfo(
                    module=f"{'.' * level}{module}" if module else "." * level,
                    names=names,
                    resolved_path=resolved,
                    is_stdlib=False,  # relative imports are never stdlib
                ))
            else:
                top = module.split(".")[0]
                is_std = top in stdlib or module in stdlib
                resolved = ws_lookup.get(module)
                imports.append(ImportInfo(
                    module=module,
                    names=names,
                    resolved_path=resolved,
                    is_stdlib=is_std,
                ))

    return imports


__all__ = [
    "PYTHON_STDLIB_MODULES",
    "extract_symbols",
    "parse_pyright_json",
    "parse_python_ast_errors",
    "parse_ruff_json",
    "resolve_imports",
]
