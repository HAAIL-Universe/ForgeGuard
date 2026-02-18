"""Workspace — centralised workspace management with sandbox enforcement.

Provides a single ``Workspace`` instance per build that handles:
- Safe path resolution (replaces scattered ``_resolve_sandboxed`` calls)
- Recursive file-tree listing with caching + configurable ignores
- Workspace metadata summaries (file count, languages, sizes)
- Constant-time sandbox membership checks
- Unified ``WorkspaceSnapshot`` with symbol table, dependency graph,
  test inventory, and schema inventory

Thread-safe for reads; cache invalidation is cooperative (call
``invalidate_cache()`` after writes).
"""

from __future__ import annotations

import ast
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Any

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


# ---------------------------------------------------------------------------
# Workspace Snapshot — unified reconnaissance artefact
# ---------------------------------------------------------------------------

_TEST_FILE_PATTERNS = re.compile(
    r"(^|/)tests?/|test_[^/]+\.py$|_test\.py$|\.test\.[jt]sx?$|\.spec\.[jt]sx?$|/__tests__/"
)
_TEST_FUNC_PATTERN = re.compile(r"^\s*(?:async\s+)?def\s+(test_\w+)", re.MULTILINE)
_SQL_TABLE_PATTERN = re.compile(
    r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(\w+)", re.IGNORECASE
)
_SQL_COLUMN_PATTERN = re.compile(
    r"^\s+(\w+)\s+(TEXT|VARCHAR|INTEGER|BIGINT|SERIAL|UUID|BOOLEAN|TIMESTAMP|JSONB|REAL|FLOAT|NUMERIC|INT|SMALLINT|BYTEA)",
    re.IGNORECASE | re.MULTILINE,
)


class TestInventory(BaseModel):
    """Inventory of test files and test functions in the workspace."""

    model_config = ConfigDict(frozen=True)

    test_files: tuple[str, ...] = ()
    test_count: int = Field(0, ge=0, description="Total test functions found")
    frameworks: tuple[str, ...] = ()


class SchemaInventory(BaseModel):
    """Inventory of database tables and migrations."""

    model_config = ConfigDict(frozen=True)

    tables: tuple[str, ...] = ()
    columns: dict[str, tuple[str, ...]] = Field(
        default_factory=dict,
        description="table_name -> (col1, col2, ...)",
    )
    migration_files: tuple[str, ...] = ()


class WorkspaceSnapshot(BaseModel):
    """Unified reconnaissance snapshot of an entire workspace.

    Combines file tree, symbol table, dependency graph, test
    inventory, and schema inventory into a single artefact that
    the planner and per-file generation calls can reference.
    """

    model_config = ConfigDict(frozen=True)

    file_tree: Annotated[str, Field(description="Indented directory listing")] = ""
    symbol_table: dict[str, str] = Field(
        default_factory=dict,
        description="dotted.path -> kind (class/function/const)",
    )
    dependency_graph: dict[str, tuple[str, ...]] = Field(
        default_factory=dict,
        description="file -> (imported_modules, ...)",
    )
    test_inventory: TestInventory = Field(default_factory=TestInventory)
    schema_inventory: SchemaInventory = Field(default_factory=SchemaInventory)
    total_files: Annotated[int, Field(ge=0)] = 0
    total_lines: Annotated[int, Field(ge=0)] = 0
    languages: dict[str, int] = Field(
        default_factory=dict,
        description="language -> line count",
    )
    captured_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
    )


def capture_snapshot(workspace: Workspace) -> WorkspaceSnapshot:
    """Build a full ``WorkspaceSnapshot`` from a workspace.

    Performs a single pass over the file tree, extracting Python
    symbols/imports via ``ast`` and counting test functions, lines,
    and detecting frameworks.  Schema information is extracted from
    SQL migration files.

    This is a synchronous function (file I/O only, no network).
    """
    tree = workspace.file_tree()
    files_only = [e for e in tree if not e.is_dir]

    symbol_table: dict[str, str] = {}
    dependency_graph: dict[str, tuple[str, ...]] = {}
    languages: dict[str, int] = {}
    total_lines = 0

    test_files: list[str] = []
    test_count = 0
    frameworks: set[str] = set()

    tables: list[str] = []
    columns: dict[str, tuple[str, ...]] = {}
    migration_files: list[str] = []

    tree_lines: list[str] = []
    _build_tree_lines(tree, tree_lines)

    for entry in files_only:
        abs_path = workspace.root / entry.path
        lang = entry.language

        # Count lines
        try:
            source = abs_path.read_text(encoding="utf-8", errors="replace")
            line_count = source.count("\n") + (1 if source and not source.endswith("\n") else 0)
        except OSError:
            source = ""
            line_count = 0

        total_lines += line_count
        languages[lang] = languages.get(lang, 0) + line_count

        # Detect test files
        if _TEST_FILE_PATTERNS.search(entry.path):
            test_files.append(entry.path)
            # Count test functions
            test_count += len(_TEST_FUNC_PATTERN.findall(source))
            # Detect frameworks
            if "pytest" in source or "import pytest" in source:
                frameworks.add("pytest")
            if "vitest" in source or "from vitest" in source or "@vitest" in source:
                frameworks.add("vitest")
            if "from jest" in source or "describe(" in source:
                frameworks.add("jest")

        # Python symbol + import extraction
        if lang == "python" and source:
            module_path = entry.path.replace("/", ".").removesuffix(".py")
            try:
                parsed = ast.parse(source)
            except SyntaxError:
                parsed = None

            if parsed is not None:
                imports: list[str] = []
                for node in ast.walk(parsed):
                    if isinstance(node, ast.Import):
                        for alias in node.names:
                            imports.append(alias.name)
                    elif isinstance(node, ast.ImportFrom):
                        if node.level and node.level > 0:
                            prefix = "." * node.level
                            if node.module:
                                imports.append(prefix + node.module)
                            else:
                                for alias in node.names:
                                    imports.append(prefix + alias.name)
                        elif node.module:
                            imports.append(node.module)
                if imports:
                    dependency_graph[entry.path] = tuple(imports)

                for node in ast.iter_child_nodes(parsed):
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        if not node.name.startswith("_"):
                            symbol_table[f"{module_path}.{node.name}"] = "function"
                    elif isinstance(node, ast.ClassDef):
                        if not node.name.startswith("_"):
                            symbol_table[f"{module_path}.{node.name}"] = "class"
                    elif isinstance(node, ast.Assign):
                        for target in node.targets:
                            if isinstance(target, ast.Name) and target.id.isupper():
                                symbol_table[f"{module_path}.{target.id}"] = "constant"

        # TypeScript/JavaScript export extraction (regex-based)
        elif lang in ("typescript", "typescriptreact", "javascript", "javascriptreact") and source:
            module_path = entry.path.removesuffix(".tsx").removesuffix(".ts").removesuffix(".jsx").removesuffix(".js")
            module_path = module_path.replace("/", ".")
            for match in re.finditer(
                r"export\s+(?:default\s+)?(?:async\s+)?(?:function|class|const|let|var|interface|type|enum)\s+(\w+)",
                source,
            ):
                name = match.group(1)
                # Infer kind from keyword
                line = match.group(0)
                if "class " in line:
                    kind = "class"
                elif "function " in line:
                    kind = "function"
                elif "interface " in line or "type " in line:
                    kind = "type"
                elif "enum " in line:
                    kind = "enum"
                else:
                    kind = "variable"
                symbol_table[f"{module_path}.{name}"] = kind

        # SQL migration file parsing
        if lang == "sql" and ("migration" in entry.path.lower() or "alembic" in entry.path.lower()):
            migration_files.append(entry.path)
            for tbl_match in _SQL_TABLE_PATTERN.finditer(source):
                tbl_name = tbl_match.group(1)
                if tbl_name not in tables:
                    tables.append(tbl_name)
                # Extract columns for this table
                # Find the block after CREATE TABLE ... (
                tbl_start = tbl_match.end()
                # Find matching closing paren
                paren_depth = 0
                block_end = tbl_start
                for i in range(tbl_start, len(source)):
                    if source[i] == "(":
                        paren_depth += 1
                    elif source[i] == ")":
                        if paren_depth == 0:
                            block_end = i
                            break
                        paren_depth -= 1
                block = source[tbl_start:block_end]
                cols = tuple(
                    m.group(1) for m in _SQL_COLUMN_PATTERN.finditer(block)
                )
                if cols:
                    columns[tbl_name] = cols

    # Also check for Python migration files (Alembic)
    for entry in files_only:
        if entry.path not in [m for m in migration_files] and "alembic" in entry.path.lower() and entry.language == "python":
            migration_files.append(entry.path)
            try:
                source = (workspace.root / entry.path).read_text(encoding="utf-8", errors="replace")
                for tbl_match in re.finditer(r"op\.create_table\(\s*['\"](\w+)['\"]", source):
                    tbl_name = tbl_match.group(1)
                    if tbl_name not in tables:
                        tables.append(tbl_name)
            except OSError:
                pass

    return WorkspaceSnapshot(
        file_tree="\n".join(tree_lines),
        symbol_table=symbol_table,
        dependency_graph=dependency_graph,
        test_inventory=TestInventory(
            test_files=tuple(sorted(test_files)),
            test_count=test_count,
            frameworks=tuple(sorted(frameworks)),
        ),
        schema_inventory=SchemaInventory(
            tables=tuple(tables),
            columns=columns,
            migration_files=tuple(sorted(migration_files)),
        ),
        total_files=len(files_only),
        total_lines=total_lines,
        languages=languages,
    )


def update_snapshot(
    snapshot: WorkspaceSnapshot,
    changed_files: list[str],
    workspace: Workspace,
) -> WorkspaceSnapshot:
    """Incrementally update a snapshot by re-scanning only changed files.

    Rebuilds the full symbol table and dependency graph, replacing
    entries for *changed_files* with fresh data while preserving
    entries for all other files.

    Parameters
    ----------
    snapshot : WorkspaceSnapshot
        The existing snapshot to update.
    changed_files : list[str]
        Workspace-relative paths (forward-slash) of files that changed.
    workspace : Workspace
        The workspace instance for path resolution / file reading.

    Returns
    -------
    WorkspaceSnapshot
        A new snapshot with updated entries for the changed files.
    """
    if not changed_files:
        return snapshot

    # Mutable copies of the things we update
    symbol_table = dict(snapshot.symbol_table)
    dep_graph = {k: v for k, v in snapshot.dependency_graph.items()}
    languages = dict(snapshot.languages)
    total_lines = snapshot.total_lines
    test_files = list(snapshot.test_inventory.test_files)
    test_count = snapshot.test_inventory.test_count
    frameworks = set(snapshot.test_inventory.frameworks)

    changed_set = set(changed_files)

    # Remove old entries for changed files
    for cf in changed_set:
        # Remove symbols that start with the module path
        module_path = cf.replace("/", ".").removesuffix(".py").removesuffix(".ts").removesuffix(".tsx").removesuffix(".js").removesuffix(".jsx")
        to_remove = [k for k in symbol_table if k.startswith(module_path + ".")]
        for k in to_remove:
            del symbol_table[k]

        dep_graph.pop(cf, None)

        if cf in test_files:
            test_files.remove(cf)

    # Re-scan changed files using capture_snapshot logic on individual files
    for cf in changed_set:
        abs_path = workspace.root / cf
        if not abs_path.is_file():
            continue

        ext = abs_path.suffix
        lang = _detect_language(ext)

        try:
            source = abs_path.read_text(encoding="utf-8", errors="replace")
            line_count = source.count("\n") + (1 if source and not source.endswith("\n") else 0)
        except OSError:
            continue

        # Update line counts (approximate — we don't track per-file old counts)
        languages[lang] = languages.get(lang, 0) + line_count

        # Test detection on changed file
        if _TEST_FILE_PATTERNS.search(cf):
            if cf not in test_files:
                test_files.append(cf)
            tc = len(_TEST_FUNC_PATTERN.findall(source))
            test_count += tc
            if "pytest" in source:
                frameworks.add("pytest")
            if "vitest" in source:
                frameworks.add("vitest")

        # Python analysis
        if lang == "python" and source:
            module_path = cf.replace("/", ".").removesuffix(".py")
            try:
                parsed = ast.parse(source)
            except SyntaxError:
                continue

            imports: list[str] = []
            for node in ast.walk(parsed):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        imports.append(alias.name)
                elif isinstance(node, ast.ImportFrom):
                    if node.level and node.level > 0:
                        prefix = "." * node.level
                        if node.module:
                            imports.append(prefix + node.module)
                        else:
                            for alias in node.names:
                                imports.append(prefix + alias.name)
                    elif node.module:
                        imports.append(node.module)
            if imports:
                dep_graph[cf] = tuple(imports)

            for node in ast.iter_child_nodes(parsed):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    if not node.name.startswith("_"):
                        symbol_table[f"{module_path}.{node.name}"] = "function"
                elif isinstance(node, ast.ClassDef):
                    if not node.name.startswith("_"):
                        symbol_table[f"{module_path}.{node.name}"] = "class"
                elif isinstance(node, ast.Assign):
                    for target in node.targets:
                        if isinstance(target, ast.Name) and target.id.isupper():
                            symbol_table[f"{module_path}.{target.id}"] = "constant"

    # Rebuild tree
    tree = workspace.file_tree()
    tree_lines: list[str] = []
    _build_tree_lines(tree, tree_lines)
    files_only = [e for e in tree if not e.is_dir]

    return WorkspaceSnapshot(
        file_tree="\n".join(tree_lines),
        symbol_table=symbol_table,
        dependency_graph=dep_graph,
        test_inventory=TestInventory(
            test_files=tuple(sorted(test_files)),
            test_count=test_count,
            frameworks=tuple(sorted(frameworks)),
        ),
        schema_inventory=snapshot.schema_inventory,  # schema rarely changes mid-build
        total_files=len(files_only),
        total_lines=total_lines,
        languages=languages,
        captured_at=datetime.now(timezone.utc),
    )


def snapshot_to_workspace_info(snapshot: WorkspaceSnapshot) -> str:
    """Convert a snapshot into the ``workspace_info`` string expected
    by the planner prompt (``## Existing Workspace`` section).

    Produces a richer representation than the old raw file listing,
    including symbol counts, test inventory, and schema summary.
    """
    sections: list[str] = []

    # File tree
    if snapshot.file_tree:
        sections.append(snapshot.file_tree)

    # Stats line
    lang_str = ", ".join(
        f"{lang}: {count}" for lang, count in sorted(
            snapshot.languages.items(), key=lambda x: -x[1]
        )[:8]
    )
    sections.append(
        f"\nTotal: {snapshot.total_files} files, "
        f"{snapshot.total_lines:,} lines"
        + (f" ({lang_str})" if lang_str else "")
    )

    # Symbol summary
    if snapshot.symbol_table:
        classes = sum(1 for v in snapshot.symbol_table.values() if v == "class")
        functions = sum(1 for v in snapshot.symbol_table.values() if v == "function")
        others = len(snapshot.symbol_table) - classes - functions
        sections.append(
            f"Symbols: {classes} classes, {functions} functions"
            + (f", {others} other" if others else "")
        )

    # Test inventory
    ti = snapshot.test_inventory
    if ti.test_files:
        fw = f" ({', '.join(ti.frameworks)})" if ti.frameworks else ""
        sections.append(
            f"Tests: {ti.test_count} test functions in "
            f"{len(ti.test_files)} files{fw}"
        )

    # Schema inventory
    si = snapshot.schema_inventory
    if si.tables:
        sections.append(
            f"Database: {len(si.tables)} tables "
            f"({', '.join(si.tables[:10])}"
            + ("..." if len(si.tables) > 10 else "")
            + f"), {len(si.migration_files)} migrations"
        )

    return "\n".join(sections)


def _build_tree_lines(
    entries: list[FileEntry],
    lines: list[str],
    *,
    max_depth: int = 3,
) -> None:
    """Build an indented tree from FileEntry list.

    Shows directories up to *max_depth* with file counts at leaf
    directories and individual files at the deepest shown level.
    """
    # Group by directory
    dir_files: dict[str, list[str]] = {}
    for e in entries:
        if e.is_dir:
            continue
        parts = e.path.replace("\\", "/").split("/")
        if len(parts) == 1:
            dir_files.setdefault(".", []).append(parts[0])
        else:
            dir_key = "/".join(parts[:-1])
            dir_files.setdefault(dir_key, []).append(parts[-1])

    # Sort directory keys
    sorted_dirs = sorted(dir_files.keys())

    for d in sorted_dirs:
        fnames = dir_files[d]
        depth = 0 if d == "." else d.count("/") + 1
        if depth > max_depth:
            continue
        indent = "  " * depth
        if d != ".":
            lines.append(f"{indent}{d.split('/')[-1]}/ ({len(fnames)} files)")
        # Show files at max_depth or root
        if depth >= max_depth:
            continue
        file_indent = "  " * (depth + (0 if d == "." else 1))
        shown = 0
        for fn in sorted(fnames)[:8]:
            lines.append(f"{file_indent}{fn}")
            shown += 1
        if len(fnames) > 8:
            lines.append(f"{file_indent}... and {len(fnames) - 8} more")
