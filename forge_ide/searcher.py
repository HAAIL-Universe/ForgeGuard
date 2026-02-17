"""Code search — ripgrep-powered with Python fallback.

Searches across workspace files, returning structured ``Match`` objects
with context lines.  Uses ripgrep (``rg``) when available for speed;
falls back to a pure-Python ``re`` + ``os.walk`` implementation.
"""

from __future__ import annotations

import asyncio
import fnmatch
import json
import os
import re
import shutil
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from forge_ide.contracts import ToolResponse
from forge_ide.workspace import DEFAULT_SKIP_DIRS, Workspace

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_MAX_RESULTS: int = 100
DEFAULT_CONTEXT_LINES: int = 2
_MAX_SNIPPET_LEN: int = 200

# Module-level cache for ripgrep availability
_rg_available: bool | None = None

# Binary extensions to skip during search
_BINARY_SKIP: frozenset[str] = frozenset(
    {
        ".png", ".jpg", ".jpeg", ".gif", ".ico", ".bmp", ".webp",
        ".woff", ".woff2", ".ttf", ".eot", ".otf",
        ".zip", ".tar", ".gz", ".bz2", ".xz", ".7z", ".rar",
        ".exe", ".dll", ".so", ".dylib",
        ".pyc", ".pyo", ".class", ".o", ".a", ".lib",
        ".bin", ".dat", ".pdf", ".doc", ".docx",
        ".sqlite", ".db",
    }
)


# ---------------------------------------------------------------------------
# Match model
# ---------------------------------------------------------------------------


class Match(BaseModel):
    """A single search match with context."""

    model_config = ConfigDict(frozen=True)

    path: str = Field(..., description="Relative path from workspace root")
    line: int = Field(..., ge=1, description="1-based line number")
    column: int = Field(..., ge=0, description="0-based column offset")
    snippet: str = Field(..., description="The matching line (trimmed)")
    context_before: list[str] = Field(
        default_factory=list, description="Lines before the match"
    )
    context_after: list[str] = Field(
        default_factory=list, description="Lines after the match"
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def search(
    workspace: Workspace,
    pattern: str,
    *,
    glob: str | None = None,
    is_regex: bool = False,
    max_results: int = DEFAULT_MAX_RESULTS,
    context_lines: int = DEFAULT_CONTEXT_LINES,
    case_sensitive: bool = False,
) -> ToolResponse:
    """Search workspace files for a pattern.

    Parameters
    ----------
    workspace : Workspace
        The workspace to search within.
    pattern : str
        Search string or regex pattern.
    glob : str | None
        Optional file glob filter (e.g. ``"*.py"``).
    is_regex : bool
        If True, treat *pattern* as a regex.
    max_results : int
        Maximum number of matches to return.
    context_lines : int
        Number of context lines before/after each match.
    case_sensitive : bool
        If True, search is case-sensitive.

    Returns
    -------
    ToolResponse
        ``{matches: [...], total_count: int, truncated: bool}``
    """
    if not pattern:
        return ToolResponse.fail("Search pattern must not be empty")

    if _ripgrep_available():
        try:
            matches, total, truncated = await _search_ripgrep(
                workspace, pattern, glob, is_regex,
                max_results, context_lines, case_sensitive,
            )
        except Exception:
            # Fallback to Python on any ripgrep error
            matches, total, truncated = _search_python(
                workspace, pattern, glob, is_regex,
                max_results, context_lines, case_sensitive,
            )
    else:
        matches, total, truncated = _search_python(
            workspace, pattern, glob, is_regex,
            max_results, context_lines, case_sensitive,
        )

    return ToolResponse.ok(
        {
            "matches": [m.model_dump() for m in matches],
            "total_count": total,
            "truncated": truncated,
        }
    )


# ---------------------------------------------------------------------------
# Ripgrep detection
# ---------------------------------------------------------------------------


def _ripgrep_available() -> bool:
    """Check if ripgrep (``rg``) is available on PATH.  Cached."""
    global _rg_available  # noqa: PLW0603
    if _rg_available is None:
        _rg_available = shutil.which("rg") is not None
    return _rg_available


def _reset_rg_cache() -> None:
    """Reset the ripgrep availability cache (for testing)."""
    global _rg_available  # noqa: PLW0603
    _rg_available = None


# ---------------------------------------------------------------------------
# Ripgrep implementation
# ---------------------------------------------------------------------------


async def _search_ripgrep(
    workspace: Workspace,
    pattern: str,
    glob: str | None,
    is_regex: bool,
    max_results: int,
    context_lines: int,
    case_sensitive: bool,
) -> tuple[list[Match], int, bool]:
    """Run ripgrep and parse JSON output."""
    cmd: list[str] = [
        "rg", "--json",
        "--max-count", str(max_results + 1),
    ]

    if case_sensitive:
        cmd.append("--case-sensitive")
    else:
        cmd.append("--ignore-case")

    if not is_regex:
        cmd.append("--fixed-strings")

    if context_lines > 0:
        cmd.extend(["-C", str(context_lines)])

    if glob:
        cmd.extend(["--glob", glob])

    cmd.append("--")
    cmd.append(pattern)

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=str(workspace.root),
    )
    stdout, _ = await proc.communicate()

    matches: list[Match] = []
    # Collect context lines keyed by (path, line_number)
    context_before_buf: list[str] = []
    last_match: Match | None = None
    after_count = 0

    for raw_line in stdout.decode("utf-8", errors="replace").splitlines():
        try:
            entry = json.loads(raw_line)
        except json.JSONDecodeError:
            continue

        etype = entry.get("type")

        if etype == "context":
            ctx_data = entry.get("data", {})
            text = ctx_data.get("lines", {}).get("text", "").rstrip("\n")
            if last_match is not None and after_count < context_lines:
                # This is context_after for the previous match
                # We need to rebuild the match with updated context
                after_count += 1
                # Store for post-processing
            else:
                context_before_buf.append(text)
                # Keep only the last `context_lines` entries
                if len(context_before_buf) > context_lines:
                    context_before_buf = context_before_buf[-context_lines:]

        elif etype == "match":
            match_data = entry.get("data", {})
            path_data = match_data.get("path", {}).get("text", "")
            line_num = match_data.get("line_number", 1)
            line_text = match_data.get("lines", {}).get("text", "").rstrip("\n")

            # Column from first submatch
            submatches = match_data.get("submatches", [])
            col = submatches[0].get("start", 0) if submatches else 0

            snippet = line_text.strip()[:_MAX_SNIPPET_LEN]

            m = Match(
                path=path_data.replace("\\", "/"),
                line=line_num,
                column=col,
                snippet=snippet,
                context_before=list(context_before_buf),
                context_after=[],
            )
            matches.append(m)
            context_before_buf.clear()
            last_match = m
            after_count = 0

        elif etype == "end":
            # Reset between files
            context_before_buf.clear()
            last_match = None

    total = len(matches)
    truncated = total > max_results
    if truncated:
        matches = matches[:max_results]

    return matches, min(total, max_results) if not truncated else total, truncated


# ---------------------------------------------------------------------------
# Python fallback
# ---------------------------------------------------------------------------


def _search_python(
    workspace: Workspace,
    pattern: str,
    glob: str | None,
    is_regex: bool,
    max_results: int,
    context_lines: int,
    case_sensitive: bool,
) -> tuple[list[Match], int, bool]:
    """Pure-Python search using ``re`` and ``os.walk``."""
    flags = 0 if case_sensitive else re.IGNORECASE
    try:
        if is_regex:
            regex = re.compile(pattern, flags)
        else:
            regex = re.compile(re.escape(pattern), flags)
    except re.error as exc:
        return [], 0, False  # Invalid regex → no results

    file_glob = glob or "*"
    root = workspace.root
    gitignore_patterns = _parse_gitignore(root)

    matches: list[Match] = []
    total_count = 0
    truncated = False

    for dirpath_str, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in DEFAULT_SKIP_DIRS)
        dirpath = Path(dirpath_str)

        for fname in sorted(filenames):
            if not fnmatch.fnmatch(fname, file_glob):
                continue

            fpath = dirpath / fname
            rel = str(fpath.relative_to(root)).replace("\\", "/")

            # Skip binary files
            if fpath.suffix.lower() in _BINARY_SKIP:
                continue

            # Skip gitignored files
            if _is_gitignored(rel, gitignore_patterns):
                continue

            try:
                content = fpath.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue

            lines = content.splitlines()

            for i, line in enumerate(lines):
                m = regex.search(line)
                if m is None:
                    continue

                total_count += 1

                if len(matches) >= max_results:
                    truncated = True
                    continue  # Keep counting total

                ctx_before = lines[max(0, i - context_lines) : i]
                ctx_after = lines[i + 1 : i + 1 + context_lines]
                snippet = line.strip()[:_MAX_SNIPPET_LEN]

                matches.append(
                    Match(
                        path=rel,
                        line=i + 1,
                        column=m.start(),
                        snippet=snippet,
                        context_before=ctx_before,
                        context_after=ctx_after,
                    )
                )

    return matches, total_count, truncated


# ---------------------------------------------------------------------------
# .gitignore parsing (basic)
# ---------------------------------------------------------------------------


def _parse_gitignore(workspace_root: Path) -> list[re.Pattern[str]]:
    """Parse .gitignore into compiled regex patterns.

    Supports basic glob patterns:
    - ``*`` → match anything except ``/``
    - ``**`` → match anything including ``/``
    - ``?`` → match single char
    - Negation with ``!`` (not supported — skipped)
    - Comment lines starting with ``#`` are ignored
    - Trailing ``/`` means directory only (treated as prefix match)
    """
    gitignore_path = workspace_root / ".gitignore"
    if not gitignore_path.is_file():
        return []

    patterns: list[re.Pattern[str]] = []
    try:
        text = gitignore_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("!"):
            continue  # Negation not supported in basic parser

        # Convert glob to regex
        regex_str = _glob_to_regex(line)
        try:
            patterns.append(re.compile(regex_str))
        except re.error:
            continue

    return patterns


def _glob_to_regex(glob_pattern: str) -> str:
    """Convert a gitignore glob pattern to a regex string."""
    # Remove trailing slash (directory indicator — treat as prefix)
    is_dir = glob_pattern.endswith("/")
    if is_dir:
        glob_pattern = glob_pattern.rstrip("/")

    parts: list[str] = []
    i = 0
    while i < len(glob_pattern):
        ch = glob_pattern[i]
        if ch == "*":
            if i + 1 < len(glob_pattern) and glob_pattern[i + 1] == "*":
                parts.append(".*")
                i += 2
                # Skip following slash
                if i < len(glob_pattern) and glob_pattern[i] == "/":
                    i += 1
                continue
            else:
                parts.append("[^/]*")
        elif ch == "?":
            parts.append("[^/]")
        elif ch == ".":
            parts.append(r"\.")
        elif ch in r"()[]{}+^$|":
            parts.append("\\" + ch)
        elif ch == "/":
            parts.append("/")
        else:
            parts.append(ch)
        i += 1

    regex = "".join(parts)

    # If pattern doesn't contain /, match against basename or full path
    if "/" not in glob_pattern:
        regex = f"(?:^|/){regex}"

    if is_dir:
        regex = regex + "(?:/|$)"
    else:
        regex = regex + "$"

    return regex


def _is_gitignored(rel_path: str, patterns: list[re.Pattern[str]]) -> bool:
    """Check if a relative path matches any gitignore pattern."""
    for pat in patterns:
        if pat.search(rel_path):
            return True
    return False
