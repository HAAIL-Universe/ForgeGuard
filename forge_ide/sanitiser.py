"""Output sanitisation — deterministic sorting, noise filtering, path normalisation.

Pure functions for normalising IDE tool outputs so that the same logical
result always produces identical textual output, regardless of platform,
timing, or process IDs.  All operations are pure — no I/O or side effects.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from forge_ide.contracts import Diagnostic
    from forge_ide.lang import Symbol
    from forge_ide.searcher import Match


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_SEVERITY_RANK: dict[str, int] = {
    "error": 0,
    "warning": 1,
    "info": 2,
    "hint": 3,
}

# Noise patterns (compiled once)
_ISO_TIMESTAMP = re.compile(
    r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}"
    r"(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?"
)

_PID_PATTERNS = re.compile(
    r"(?i)(?:pid|process)\s*[=:]\s*\d+|"
    r"PID\s*[=:]\s*\d+"
)

_TMPDIR_PATTERNS = re.compile(
    # Linux/macOS /tmp/...
    r"/tmp/\S+|"
    # macOS /var/folders/...
    r"/var/folders/\S+|"
    # Windows Temp dirs (both forward and backslash)
    r"[A-Za-z]:[/\\](?:Users|USERS)[/\\][^/\\\s]+[/\\]AppData[/\\]Local[/\\]Temp[/\\]\S+|"
    # Windows %TEMP% style
    r"[A-Za-z]:[/\\](?:Windows[/\\])?Temp[/\\]\S+"
)


# ---------------------------------------------------------------------------
# Path normalisation
# ---------------------------------------------------------------------------


def normalise_path(path: str) -> str:
    r"""Normalise a file path: backslash → forward slash.

    >>> normalise_path(r"src\\utils\\helper.py")
    'src/utils/helper.py'
    """
    return path.replace("\\", "/")


# ---------------------------------------------------------------------------
# Sorting functions
# ---------------------------------------------------------------------------


def sort_file_list(paths: list[str]) -> list[str]:
    """Sort file paths alphabetically (case-insensitive, forward-slash normalised)."""
    return sorted(paths, key=lambda p: normalise_path(p).lower())


def sort_matches(matches: list[Match]) -> list[Match]:
    """Sort search matches by (path, line, column)."""
    return sorted(
        matches,
        key=lambda m: (normalise_path(m.path).lower(), m.line, m.column),
    )


def sort_diagnostics(diagnostics: list[Diagnostic]) -> list[Diagnostic]:
    """Sort diagnostics by (file, line, severity_rank, message).

    Severity ranking: error < warning < info < hint.
    """
    return sorted(
        diagnostics,
        key=lambda d: (
            normalise_path(d.file).lower(),
            d.line,
            _SEVERITY_RANK.get(d.severity, 99),
            d.message,
        ),
    )


def sort_symbols(symbols: list[Symbol]) -> list[Symbol]:
    """Sort symbols by start_line."""
    return sorted(symbols, key=lambda s: s.start_line)


# ---------------------------------------------------------------------------
# Noise filtering functions
# ---------------------------------------------------------------------------


def strip_timestamps(text: str) -> str:
    """Replace ISO-8601 and common log timestamps with ``[timestamp]``."""
    return _ISO_TIMESTAMP.sub("[timestamp]", text)


def strip_pids(text: str) -> str:
    """Replace process/thread IDs with ``[pid]``.

    Handles patterns like ``pid=12345``, ``PID: 9999``, ``process=42``.
    """

    def _replace_pid(m: re.Match[str]) -> str:
        matched = m.group(0)
        # Preserve the prefix, replace just the numeric part
        return re.sub(r"\d+", "[pid]", matched)

    return _PID_PATTERNS.sub(_replace_pid, text)


def strip_tmpdir(text: str) -> str:
    """Replace temp-directory paths with ``[tmpdir]``."""
    return _TMPDIR_PATTERNS.sub("[tmpdir]", text)


def normalise_paths(text: str, workspace_root: str) -> str:
    """Replace absolute workspace paths with relative and normalise slashes.

    If *workspace_root* is empty, only backslash normalisation is applied.
    """
    if not workspace_root:
        return text

    # Normalize the root for matching (forward slash, ensure trailing slash)
    norm_root = normalise_path(workspace_root)
    if not norm_root.endswith("/"):
        norm_root += "/"

    # Also try the backslash version on Windows
    bs_root = workspace_root.replace("/", "\\")
    if not bs_root.endswith("\\"):
        bs_root += "\\"

    result = text
    # Replace backslash variant first (more specific)
    if "\\" in result:
        result = result.replace(bs_root, "")
    # Replace forward-slash variant
    result = result.replace(norm_root, "")

    return result


def sanitise_output(text: str, *, workspace_root: str = "") -> str:
    """Pipeline: strip_timestamps → strip_pids → strip_tmpdir → normalise_paths.

    Applies all noise-filtering stages in order.
    """
    result = strip_timestamps(text)
    result = strip_pids(result)
    result = strip_tmpdir(result)
    result = normalise_paths(result, workspace_root)
    return result


__all__ = [
    "normalise_path",
    "sort_file_list",
    "sort_matches",
    "sort_diagnostics",
    "sort_symbols",
    "strip_timestamps",
    "strip_pids",
    "strip_tmpdir",
    "normalise_paths",
    "sanitise_output",
]
