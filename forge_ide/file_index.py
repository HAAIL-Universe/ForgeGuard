"""File index & import graph — in-memory index with Python import analysis.

Builds a per-workspace file index from ``Workspace.file_tree()``,
extracts Python imports/exports via ``ast``, and exposes
forward- and reverse-import queries.
"""

from __future__ import annotations

import ast
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from forge_ide.workspace import Workspace, _detect_language

# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class FileMetadata(BaseModel):
    """Metadata for a single indexed file."""

    model_config = ConfigDict(frozen=True)

    path: str = Field(..., description="Relative path from workspace root")
    language: str = Field("unknown", description="Detected language")
    size_bytes: int = Field(0, ge=0)
    last_modified: datetime | None = None
    imports: tuple[str, ...] = ()
    exports: tuple[str, ...] = ()


# ---------------------------------------------------------------------------
# FileIndex
# ---------------------------------------------------------------------------


class FileIndex:
    """In-memory file index with Python import graph.

    Build from a ``Workspace``, query imports / importers.
    Selective invalidation on file changes.
    """

    __slots__ = ("_workspace", "_index", "_import_graph", "_reverse_graph")

    def __init__(self, workspace: Workspace) -> None:
        self._workspace = workspace
        self._index: dict[str, FileMetadata] = {}
        self._import_graph: dict[str, list[str]] = {}
        self._reverse_graph: dict[str, list[str]] = {}

    # -- Factory ------------------------------------------------------------

    @classmethod
    def build(cls, workspace: Workspace) -> FileIndex:
        """Build a complete file index from a workspace.

        Iterates ``workspace.file_tree()`` and extracts Python
        imports/exports.
        """
        idx = cls(workspace)
        tree = workspace.file_tree()

        for entry in tree:
            if entry.is_dir:
                continue

            language = entry.language
            imports: tuple[str, ...] = ()
            exports: tuple[str, ...] = ()

            if language == "python":
                abs_path = workspace.root / entry.path
                try:
                    source = abs_path.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    source = ""

                imports = tuple(_extract_python_imports(source))
                exports = tuple(_extract_python_exports(source))

            meta = FileMetadata(
                path=entry.path,
                language=language,
                size_bytes=entry.size_bytes,
                last_modified=entry.last_modified,
                imports=imports,
                exports=exports,
            )

            idx._index[entry.path] = meta
            if imports:
                idx._import_graph[entry.path] = list(imports)

        idx._rebuild_reverse_graph()
        return idx

    # -- Queries ------------------------------------------------------------

    def get_imports(self, rel_path: str) -> list[str]:
        """Return the list of modules imported by a file."""
        return list(self._import_graph.get(rel_path, []))

    def get_importers(self, module_name: str) -> list[str]:
        """Return the list of files that import a given module."""
        return list(self._reverse_graph.get(module_name, []))

    def get_metadata(self, rel_path: str) -> FileMetadata | None:
        """Return metadata for a file, or None if not indexed."""
        return self._index.get(rel_path)

    def all_files(self) -> list[str]:
        """Return sorted list of all indexed file paths."""
        return sorted(self._index.keys())

    def languages(self) -> dict[str, int]:
        """Return language → file count mapping."""
        counts: dict[str, int] = {}
        for meta in self._index.values():
            counts[meta.language] = counts.get(meta.language, 0) + 1
        return counts

    # -- Invalidation -------------------------------------------------------

    def invalidate_file(self, rel_path: str) -> None:
        """Re-index a single file (or remove it if deleted).

        Rebuilds the reverse graph after updating.
        """
        # Remove old entry
        self._index.pop(rel_path, None)
        self._import_graph.pop(rel_path, None)

        # Re-read if file still exists
        abs_path = self._workspace.root / rel_path
        if abs_path.is_file():
            try:
                stat = abs_path.stat()
                size = stat.st_size
                from datetime import timezone

                mtime = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)
            except OSError:
                size = 0
                mtime = None

            language = _detect_language(abs_path.suffix)
            imports: tuple[str, ...] = ()
            exports: tuple[str, ...] = ()

            if language == "python":
                try:
                    source = abs_path.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    source = ""
                imports = tuple(_extract_python_imports(source))
                exports = tuple(_extract_python_exports(source))

            meta = FileMetadata(
                path=rel_path,
                language=language,
                size_bytes=size,
                last_modified=mtime,
                imports=imports,
                exports=exports,
            )
            self._index[rel_path] = meta
            if imports:
                self._import_graph[rel_path] = list(imports)

        self._rebuild_reverse_graph()

    # -- Internal -----------------------------------------------------------

    def _rebuild_reverse_graph(self) -> None:
        """Rebuild the reverse import graph from scratch."""
        rev: dict[str, list[str]] = {}
        for file_path, modules in self._import_graph.items():
            for mod in modules:
                if mod not in rev:
                    rev[mod] = []
                rev[mod].append(file_path)
        self._reverse_graph = rev

    def __repr__(self) -> str:
        return f"FileIndex(files={len(self._index)}, workspace={self._workspace!r})"


# ---------------------------------------------------------------------------
# Import / export extraction helpers
# ---------------------------------------------------------------------------


def _extract_python_imports(source: str) -> list[str]:
    """Extract imported module names from Python source.

    Handles ``import x``, ``from x import y``, relative imports.
    Returns empty list on syntax error.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []

    imports: list[str] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level and node.level > 0:
                # Relative import
                prefix = "." * node.level
                if node.module:
                    # from .module import name → module is ".module"
                    imports.append(prefix + node.module)
                else:
                    # from . import name1, name2 → each name is a module
                    for alias in node.names:
                        imports.append(prefix + alias.name)
            elif node.module:
                imports.append(node.module)

    return imports


def _extract_python_exports(source: str) -> list[str]:
    """Extract top-level public names defined in Python source.

    Includes functions, async functions, classes, and simple assignments.
    Skips names starting with ``_``.
    Returns empty list on syntax error.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []

    exports: list[str] = []

    for node in ast.iter_child_nodes(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if not node.name.startswith("_"):
                exports.append(node.name)
        elif isinstance(node, ast.ClassDef):
            if not node.name.startswith("_"):
                exports.append(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and not target.id.startswith("_"):
                    exports.append(target.id)

    return exports
