"""Diagnostics utilities — merge and aggregate diagnostic results.

Pure functions for combining ``Diagnostic`` lists from multiple sources
into a single ``DiagnosticReport``, plus a language detection helper.
"""

from __future__ import annotations

from forge_ide.contracts import Diagnostic
from forge_ide.lang import DiagnosticReport

# ---------------------------------------------------------------------------
# Language detection
# ---------------------------------------------------------------------------

_EXTENSION_LANGUAGE: dict[str, str] = {
    ".py": "python",
    ".pyi": "python",
    ".pyw": "python",
    ".ts": "typescript",
    ".tsx": "typescriptreact",
    ".js": "javascript",
    ".jsx": "javascriptreact",
    ".json": "json",
    ".md": "markdown",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".toml": "toml",
    ".html": "html",
    ".htm": "html",
    ".css": "css",
    ".scss": "scss",
    ".sql": "sql",
    ".sh": "shell",
    ".bash": "shell",
    ".ps1": "powershell",
    ".xml": "xml",
    ".txt": "plaintext",
    ".cfg": "ini",
    ".ini": "ini",
    ".env": "dotenv",
    ".rs": "rust",
    ".go": "go",
    ".java": "java",
    ".c": "c",
    ".cpp": "cpp",
    ".h": "c",
    ".hpp": "cpp",
}


def detect_language(path: str) -> str:
    """Detect programming language from file extension.

    Returns ``"unknown"`` for unrecognised extensions.
    """
    # Find last dot in the filename (not path separators)
    dot_idx = path.rfind(".")
    if dot_idx == -1:
        return "unknown"

    ext = path[dot_idx:].lower()
    return _EXTENSION_LANGUAGE.get(ext, "unknown")


# ---------------------------------------------------------------------------
# Merge diagnostics
# ---------------------------------------------------------------------------


def merge_diagnostics(*diag_lists: list[Diagnostic]) -> DiagnosticReport:
    """Merge multiple diagnostic lists into a single ``DiagnosticReport``.

    Groups diagnostics by file path and tallies severity counts.
    """
    files: dict[str, list[Diagnostic]] = {}
    error_count = 0
    warning_count = 0
    info_count = 0
    hint_count = 0

    for diag_list in diag_lists:
        for diag in diag_list:
            files.setdefault(diag.file, []).append(diag)

            if diag.severity == "error":
                error_count += 1
            elif diag.severity == "warning":
                warning_count += 1
            elif diag.severity == "info":
                info_count += 1
            elif diag.severity == "hint":
                hint_count += 1

    return DiagnosticReport(
        files=files,
        error_count=error_count,
        warning_count=warning_count,
        info_count=info_count,
        hint_count=hint_count,
    )


__all__ = [
    "detect_language",
    "merge_diagnostics",
]
