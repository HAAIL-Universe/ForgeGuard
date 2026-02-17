"""Workspace — centralised workspace management with sandbox enforcement.

Provides a single ``Workspace`` instance per build that handles:
- Safe path resolution (replaces scattered ``_resolve_sandboxed`` calls)
- Recursive file-tree listing with caching + configurable ignores
- Workspace metadata summaries (file count, languages, sizes)
- Constant-time sandbox membership checks

Thread-safe for reads; cache invalidation is cooperative (call
``invalidate_cache()`` after writes).
"""

from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from forge_ide.errors import SandboxViolation

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_CACHE_TTL_SECS: float = 30.0

DEFAULT_SKIP_DIRS: frozenset[str] = frozenset(
    {
        ".git",
        "__pycache__",
        "node_modules",
        ".venv",
        "venv",
        ".tox",
        "dist",
        "build",
        ".mypy_cache",
        ".pytest_cache",
    }
)

_EXTENSION_LANGUAGE: dict[str, str] = {
    ".py": "python",
    ".pyi": "python",
    ".ts": "typescript",
    ".tsx": "typescriptreact",
    ".js": "javascript",
    ".jsx": "javascriptreact",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".json": "json",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".md": "markdown",
    ".html": "html",
    ".htm": "html",
    ".css": "css",
    ".scss": "css",
    ".sql": "sql",
    ".toml": "toml",
    ".txt": "text",
    ".cfg": "text",
    ".ini": "text",
    ".ps1": "powershell",
    ".sh": "shell",
    ".bash": "shell",
    ".bat": "batch",
    ".cmd": "batch",
    ".xml": "xml",
    ".svg": "xml",
    ".rs": "rust",
    ".go": "go",
    ".java": "java",
    ".c": "c",
    ".h": "c",
    ".cpp": "cpp",
    ".hpp": "cpp",
    ".rb": "ruby",
    ".php": "php",
    ".swift": "swift",
    ".kt": "kotlin",
    ".r": "r",
    ".R": "r",
    ".lock": "text",
    ".env": "text",
    ".gitignore": "text",
    ".dockerignore": "text",
    "Dockerfile": "docker",
    "Makefile": "makefile",
}


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class FileEntry(BaseModel):
    """A single file or directory entry in the workspace tree."""

    model_config = ConfigDict(frozen=True)

    path: str = Field(..., description="Relative path from workspace root")
    is_dir: bool = Field(..., description="True if this entry is a directory")
    size_bytes: int = Field(0, ge=0, description="File size in bytes (0 for dirs)")
    language: str = Field("unknown", description="Detected language from extension")
    last_modified: datetime | None = Field(
        None, description="Last modification time (UTC)"
    )


class WorkspaceSummary(BaseModel):
    """Aggregate metadata about an entire workspace."""

    model_config = ConfigDict(frozen=True)

    file_count: int = Field(..., ge=0, description="Total number of files")
    total_size_bytes: int = Field(..., ge=0, description="Sum of all file sizes")
    languages: dict[str, int] = Field(
        default_factory=dict, description="Language → file count"
    )
    last_modified: datetime | None = Field(
        None, description="Most recent modification across all files"
    )


# ---------------------------------------------------------------------------
# Workspace class
# ---------------------------------------------------------------------------


class Workspace:
    """Centralised workspace manager with sandbox enforcement and caching.

    Parameters
    ----------
    root : str | Path
        Absolute or relative path to the workspace root directory.
        Must exist and be a directory.

    Raises
    ------
    ValueError
        If *root* does not exist or is not a directory.
    """

    __slots__ = (
        "_root",
        "_root_str",
        "_cache_file_tree",
        "_cache_file_tree_ts",
        "_cache_file_tree_key",
        "_cache_summary",
        "_cache_summary_ts",
        "_cache_ttl",
    )

    def __init__(self, root: str | Path, *, cache_ttl: float = DEFAULT_CACHE_TTL_SECS) -> None:
        path = Path(root).resolve()
        if not path.exists():
            raise ValueError(f"Workspace root does not exist: {root}")
        if not path.is_dir():
            raise ValueError(f"Workspace root is not a directory: {root}")

        self._root: Path = path
        self._root_str: str = str(path)
        self._cache_ttl: float = cache_ttl
        self._cache_file_tree: list[FileEntry] | None = None
        self._cache_file_tree_ts: float = 0.0
        self._cache_file_tree_key: frozenset[str] | None = None
        self._cache_summary: WorkspaceSummary | None = None
        self._cache_summary_ts: float = 0.0

    # -- Properties ---------------------------------------------------------

    @property
    def root(self) -> Path:
        """Resolved absolute path to the workspace root."""
        return self._root

    # -- Path resolution ----------------------------------------------------

    def resolve(self, rel_path: str) -> Path:
        """Resolve a relative path within the sandbox.

        Parameters
        ----------
        rel_path : str
            A relative path (forward-slash or back-slash) to resolve.

        Returns
        -------
        Path
            Resolved absolute path guaranteed to be inside the workspace root.

        Raises
        ------
        SandboxViolation
            If the path is empty, absolute, contains null bytes,
            traverses with ``..``, or resolves outside the root.
        """
        if not rel_path:
            raise SandboxViolation(
                path=rel_path or "", root=self._root_str, reason="Path is empty"
            )

        if "\x00" in rel_path:
            raise SandboxViolation(
                path=rel_path, root=self._root_str, reason="Path contains null bytes"
            )

        if os.path.isabs(rel_path):
            raise SandboxViolation(
                path=rel_path, root=self._root_str, reason="Absolute paths are not allowed"
            )

        # Normalise separators and split into components
        components = rel_path.replace("\\", "/").split("/")
        if ".." in components:
            raise SandboxViolation(
                path=rel_path,
                root=self._root_str,
                reason="Path traversal with '..' is not allowed",
            )

        target = (self._root / rel_path).resolve()

        # Final sandbox check — catches symlink escapes
        try:
            target.relative_to(self._root)
        except ValueError:
            raise SandboxViolation(
                path=rel_path,
                root=self._root_str,
                reason="Resolved path is outside workspace root",
            )

        return target

    # -- Membership check ---------------------------------------------------

    def is_within(self, path: str | Path) -> bool:
        """Constant-time sandbox membership check (no I/O).

        Uses ``os.path.normpath`` + string prefix comparison.
        Does **not** resolve symlinks — use ``resolve()`` for
        security-critical checks.
        """
        normalised = os.path.normpath(str(path))
        root_norm = os.path.normpath(self._root_str)

        # Must be equal to root or start with root + separator
        if normalised == root_norm:
            return True
        return normalised.startswith(root_norm + os.sep)

    # -- File tree ----------------------------------------------------------

    def file_tree(
        self, *, ignore_patterns: list[str] | None = None
    ) -> list[FileEntry]:
        """Recursive directory listing respecting ignore patterns.

        Results are cached for ``cache_ttl`` seconds.  Different
        *ignore_patterns* values are treated as separate cache keys
        and will trigger a re-scan.

        Parameters
        ----------
        ignore_patterns : list[str] | None
            Directory names to skip (default: ``DEFAULT_SKIP_DIRS``).
        """
        skip = frozenset(ignore_patterns) if ignore_patterns is not None else DEFAULT_SKIP_DIRS
        now = time.time()

        # Check cache validity
        if (
            self._cache_file_tree is not None
            and self._cache_file_tree_key == skip
            and (now - self._cache_file_tree_ts) < self._cache_ttl
        ):
            return self._cache_file_tree

        entries: list[FileEntry] = []
        for dirpath_str, dirnames, filenames in os.walk(self._root):
            # Filter out skip dirs in-place
            dirnames[:] = sorted(d for d in dirnames if d not in skip)

            dirpath = Path(dirpath_str)
            rel_dir = dirpath.relative_to(self._root)

            # Add directory entries (except root itself)
            if rel_dir != Path("."):
                entries.append(
                    FileEntry(
                        path=str(rel_dir).replace("\\", "/"),
                        is_dir=True,
                        size_bytes=0,
                        language="unknown",
                        last_modified=_safe_mtime(dirpath),
                    )
                )

            for fname in sorted(filenames):
                fpath = dirpath / fname
                rel = fpath.relative_to(self._root)
                try:
                    stat = fpath.stat()
                    size = stat.st_size
                    mtime = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)
                except OSError:
                    size = 0
                    mtime = None

                entries.append(
                    FileEntry(
                        path=str(rel).replace("\\", "/"),
                        is_dir=False,
                        size_bytes=size,
                        language=_detect_language(fpath.suffix),
                        last_modified=mtime,
                    )
                )

        # Sort entries by path for deterministic output
        entries.sort(key=lambda e: e.path)

        self._cache_file_tree = entries
        self._cache_file_tree_ts = now
        self._cache_file_tree_key = skip
        return entries

    # -- Workspace summary --------------------------------------------------

    def workspace_summary(self) -> WorkspaceSummary:
        """Aggregate metadata about the workspace.  Cached separately."""
        now = time.time()
        if (
            self._cache_summary is not None
            and (now - self._cache_summary_ts) < self._cache_ttl
        ):
            return self._cache_summary

        tree = self.file_tree()  # uses its own cache
        files_only = [e for e in tree if not e.is_dir]
        total_size = sum(e.size_bytes for e in files_only)

        langs: dict[str, int] = {}
        latest: datetime | None = None
        for f in files_only:
            langs[f.language] = langs.get(f.language, 0) + 1
            if f.last_modified is not None:
                if latest is None or f.last_modified > latest:
                    latest = f.last_modified

        summary = WorkspaceSummary(
            file_count=len(files_only),
            total_size_bytes=total_size,
            languages=langs,
            last_modified=latest,
        )

        self._cache_summary = summary
        self._cache_summary_ts = now
        return summary

    # -- Cache management ---------------------------------------------------

    def invalidate_cache(self) -> None:
        """Clear all cached data (file tree + summary)."""
        self._cache_file_tree = None
        self._cache_file_tree_ts = 0.0
        self._cache_file_tree_key = None
        self._cache_summary = None
        self._cache_summary_ts = 0.0

    # -- Repr ---------------------------------------------------------------

    def __repr__(self) -> str:
        return f"Workspace(root={self._root_str!r})"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _detect_language(ext: str) -> str:
    """Map a file extension to a language name."""
    return _EXTENSION_LANGUAGE.get(ext.lower(), "unknown")


def _safe_mtime(path: Path) -> datetime | None:
    """Get the last-modified time for *path*, or None on error."""
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    except OSError:
        return None
