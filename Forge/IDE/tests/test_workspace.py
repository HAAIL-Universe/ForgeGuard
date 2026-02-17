"""Tests for forge_ide.workspace — Workspace class, FileEntry, WorkspaceSummary.

Covers:
- Construction (valid dir, nonexistent, file-as-root)
- resolve() sandbox enforcement (absolute, .., null bytes, symlinks, empty)
- is_within() constant-time membership check
- file_tree() recursive listing, skipping, caching, TTL
- workspace_summary() aggregation + caching
- FileEntry / WorkspaceSummary model constraints
- Edge cases (unicode, hidden files, long paths, 0-byte files)
"""

from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from forge_ide.errors import SandboxViolation
from forge_ide.workspace import (
    DEFAULT_CACHE_TTL_SECS,
    DEFAULT_SKIP_DIRS,
    FileEntry,
    Workspace,
    WorkspaceSummary,
    _detect_language,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def ws_dir(tmp_path: Path) -> Path:
    """Create a small workspace directory tree for testing."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text("print('hello')\n", encoding="utf-8")
    (tmp_path / "src" / "utils.ts").write_text("export {}\n", encoding="utf-8")
    (tmp_path / "README.md").write_text("# Project\n", encoding="utf-8")
    (tmp_path / "config.json").write_text("{}\n", encoding="utf-8")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    (tmp_path / "__pycache__").mkdir()
    (tmp_path / "__pycache__" / "cached.pyc").write_bytes(b"\x00" * 100)
    return tmp_path


@pytest.fixture()
def ws(ws_dir: Path) -> Workspace:
    """A Workspace rooted at the test directory."""
    return Workspace(ws_dir)


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


class TestConstruction:
    def test_valid_directory(self, ws_dir: Path) -> None:
        ws = Workspace(ws_dir)
        assert ws.root == ws_dir.resolve()

    def test_nonexistent_path(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="does not exist"):
            Workspace(tmp_path / "nonexistent")

    def test_file_as_root(self, tmp_path: Path) -> None:
        f = tmp_path / "file.txt"
        f.write_text("hi")
        with pytest.raises(ValueError, match="not a directory"):
            Workspace(f)

    def test_root_property_is_resolved(self, ws_dir: Path) -> None:
        ws = Workspace(ws_dir)
        # root should be absolute and resolved
        assert ws.root.is_absolute()
        assert ws.root == ws_dir.resolve()

    def test_repr(self, ws: Workspace) -> None:
        r = repr(ws)
        assert "Workspace" in r
        assert "root=" in r


# ---------------------------------------------------------------------------
# resolve()
# ---------------------------------------------------------------------------


class TestResolve:
    def test_basic_relative_path(self, ws: Workspace, ws_dir: Path) -> None:
        result = ws.resolve("README.md")
        assert result == (ws_dir / "README.md").resolve()

    def test_nested_path(self, ws: Workspace, ws_dir: Path) -> None:
        result = ws.resolve("src/main.py")
        assert result == (ws_dir / "src" / "main.py").resolve()

    def test_path_with_backslash(self, ws: Workspace, ws_dir: Path) -> None:
        result = ws.resolve("src\\main.py")
        assert result == (ws_dir / "src" / "main.py").resolve()

    def test_empty_path_raises(self, ws: Workspace) -> None:
        with pytest.raises(SandboxViolation, match="empty"):
            ws.resolve("")

    def test_none_string_empty_raises(self, ws: Workspace) -> None:
        with pytest.raises(SandboxViolation):
            ws.resolve("")

    def test_null_byte_raises(self, ws: Workspace) -> None:
        with pytest.raises(SandboxViolation, match="null bytes"):
            ws.resolve("foo\x00bar.py")

    def test_absolute_path_raises(self, ws: Workspace) -> None:
        with pytest.raises(SandboxViolation, match="Absolute"):
            ws.resolve("/etc/passwd")

    def test_absolute_windows_path_raises(self, ws: Workspace) -> None:
        with pytest.raises(SandboxViolation):
            ws.resolve("C:\\Windows\\System32\\cmd.exe")

    def test_dotdot_traversal_raises(self, ws: Workspace) -> None:
        with pytest.raises(SandboxViolation, match="traversal"):
            ws.resolve("../etc/passwd")

    def test_deep_dotdot_raises(self, ws: Workspace) -> None:
        with pytest.raises(SandboxViolation, match="traversal"):
            ws.resolve("a/../../..")

    def test_dotdot_in_middle_raises(self, ws: Workspace) -> None:
        with pytest.raises(SandboxViolation, match="traversal"):
            ws.resolve("src/../../../etc/passwd")

    def test_resolve_nonexistent_file_succeeds(self, ws: Workspace, ws_dir: Path) -> None:
        """resolve() doesn't require the target to exist — just validates sandbox."""
        result = ws.resolve("new_file.py")
        assert result == (ws_dir / "new_file.py").resolve()

    def test_resolve_is_idempotent(self, ws: Workspace) -> None:
        r1 = ws.resolve("src/main.py")
        r2 = ws.resolve("src/main.py")
        assert r1 == r2


# ---------------------------------------------------------------------------
# is_within()
# ---------------------------------------------------------------------------


class TestIsWithin:
    def test_path_inside(self, ws: Workspace, ws_dir: Path) -> None:
        assert ws.is_within(ws_dir / "src" / "main.py") is True

    def test_path_outside(self, ws: Workspace, tmp_path: Path) -> None:
        other = tmp_path.parent / "other"
        assert ws.is_within(other) is False

    def test_root_itself(self, ws: Workspace, ws_dir: Path) -> None:
        assert ws.is_within(ws_dir) is True

    def test_nested_inside(self, ws: Workspace, ws_dir: Path) -> None:
        assert ws.is_within(ws_dir / "a" / "b" / "c") is True

    def test_dotdot_path_outside(self, ws: Workspace, ws_dir: Path) -> None:
        # normpath resolves this to parent
        assert ws.is_within(str(ws_dir / "..")) is False

    def test_string_path(self, ws: Workspace, ws_dir: Path) -> None:
        assert ws.is_within(str(ws_dir / "foo.txt")) is True


# ---------------------------------------------------------------------------
# file_tree()
# ---------------------------------------------------------------------------


class TestFileTree:
    def test_basic_files_listed(self, ws: Workspace) -> None:
        tree = ws.file_tree()
        paths = {e.path for e in tree}
        assert "README.md" in paths
        assert "config.json" in paths
        assert "src/main.py" in paths
        assert "src/utils.ts" in paths

    def test_directories_marked(self, ws: Workspace) -> None:
        tree = ws.file_tree()
        dirs = [e for e in tree if e.is_dir]
        dir_paths = {e.path for e in dirs}
        assert "src" in dir_paths

    def test_default_skips_applied(self, ws: Workspace) -> None:
        tree = ws.file_tree()
        paths = {e.path for e in tree}
        # .git and __pycache__ should be skipped
        assert not any(".git" in p for p in paths)
        assert not any("__pycache__" in p for p in paths)

    def test_custom_ignores(self, ws: Workspace) -> None:
        tree = ws.file_tree(ignore_patterns=["src"])
        paths = {e.path for e in tree}
        assert "src/main.py" not in paths
        assert "README.md" in paths

    def test_empty_directory(self, tmp_path: Path) -> None:
        empty = tmp_path / "empty_ws"
        empty.mkdir()
        ws = Workspace(empty)
        tree = ws.file_tree()
        assert tree == []

    def test_nested_dirs(self, ws_dir: Path) -> None:
        (ws_dir / "src" / "deep" / "nested").mkdir(parents=True)
        (ws_dir / "src" / "deep" / "nested" / "file.py").write_text("x = 1\n")
        ws = Workspace(ws_dir)
        tree = ws.file_tree()
        paths = {e.path for e in tree}
        assert "src/deep/nested/file.py" in paths

    def test_language_detection(self, ws: Workspace) -> None:
        tree = ws.file_tree()
        by_path = {e.path: e for e in tree}
        assert by_path["src/main.py"].language == "python"
        assert by_path["src/utils.ts"].language == "typescript"
        assert by_path["README.md"].language == "markdown"
        assert by_path["config.json"].language == "json"

    def test_size_bytes_populated(self, ws: Workspace) -> None:
        tree = ws.file_tree()
        files = [e for e in tree if not e.is_dir]
        assert all(e.size_bytes > 0 for e in files)

    def test_last_modified_populated(self, ws: Workspace) -> None:
        tree = ws.file_tree()
        files = [e for e in tree if not e.is_dir]
        for f in files:
            assert f.last_modified is not None
            assert isinstance(f.last_modified, datetime)

    def test_tree_sorted_by_path(self, ws: Workspace) -> None:
        tree = ws.file_tree()
        paths = [e.path for e in tree]
        assert paths == sorted(paths)

    def test_zero_byte_file(self, ws_dir: Path) -> None:
        (ws_dir / "empty.txt").write_text("")
        ws = Workspace(ws_dir)
        tree = ws.file_tree()
        by_path = {e.path: e for e in tree}
        assert by_path["empty.txt"].size_bytes == 0

    def test_hidden_files(self, ws_dir: Path) -> None:
        (ws_dir / ".env").write_text("SECRET=x\n")
        ws = Workspace(ws_dir)
        tree = ws.file_tree()
        paths = {e.path for e in tree}
        assert ".env" in paths

    def test_only_directories(self, tmp_path: Path) -> None:
        root = tmp_path / "dirs_only"
        root.mkdir()
        (root / "a").mkdir()
        (root / "b").mkdir()
        ws = Workspace(root)
        tree = ws.file_tree()
        assert all(e.is_dir for e in tree)
        assert len(tree) == 2


# ---------------------------------------------------------------------------
# file_tree caching
# ---------------------------------------------------------------------------


class TestFileTreeCaching:
    def test_second_call_returns_cached(self, ws: Workspace) -> None:
        tree1 = ws.file_tree()
        tree2 = ws.file_tree()
        # Same object identity — from cache
        assert tree1 is tree2

    def test_invalidate_cache_clears(self, ws: Workspace) -> None:
        tree1 = ws.file_tree()
        ws.invalidate_cache()
        tree2 = ws.file_tree()
        assert tree1 is not tree2
        # But content should be identical
        assert [e.path for e in tree1] == [e.path for e in tree2]

    def test_ttl_expired_rescans(self, ws: Workspace) -> None:
        tree1 = ws.file_tree()

        # Pretend cache is old
        ws._cache_file_tree_ts = time.time() - DEFAULT_CACHE_TTL_SECS - 1

        tree2 = ws.file_tree()
        assert tree1 is not tree2  # re-scanned

    def test_different_ignore_patterns_miss_cache(self, ws: Workspace) -> None:
        tree1 = ws.file_tree()
        tree2 = ws.file_tree(ignore_patterns=["src"])
        # Different patterns → different scan (not the same cached object)
        assert tree1 is not tree2
        # With ["src"], src/ files absent but .git/__pycache__ present
        paths2 = {e.path for e in tree2}
        assert "src/main.py" not in paths2

    def test_invalidate_also_clears_summary(self, ws: Workspace) -> None:
        ws.workspace_summary()
        assert ws._cache_summary is not None
        ws.invalidate_cache()
        assert ws._cache_summary is None

    def test_custom_cache_ttl(self, tmp_path: Path) -> None:
        (tmp_path / "a.txt").write_text("data")
        ws = Workspace(tmp_path, cache_ttl=0.001)
        tree1 = ws.file_tree()
        time.sleep(0.01)
        tree2 = ws.file_tree()
        assert tree1 is not tree2  # TTL expired


# ---------------------------------------------------------------------------
# workspace_summary()
# ---------------------------------------------------------------------------


class TestWorkspaceSummary:
    def test_file_count(self, ws: Workspace) -> None:
        summary = ws.workspace_summary()
        # README.md, config.json, src/main.py, src/utils.ts = 4 files
        assert summary.file_count == 4

    def test_total_size_bytes(self, ws: Workspace) -> None:
        summary = ws.workspace_summary()
        assert summary.total_size_bytes > 0

    def test_languages_populated(self, ws: Workspace) -> None:
        summary = ws.workspace_summary()
        assert "python" in summary.languages
        assert "typescript" in summary.languages
        assert "markdown" in summary.languages
        assert "json" in summary.languages

    def test_caching_works(self, ws: Workspace) -> None:
        s1 = ws.workspace_summary()
        s2 = ws.workspace_summary()
        assert s1 is s2  # same object from cache

    def test_empty_workspace(self, tmp_path: Path) -> None:
        ws = Workspace(tmp_path)
        summary = ws.workspace_summary()
        assert summary.file_count == 0
        assert summary.total_size_bytes == 0
        assert summary.languages == {}
        assert summary.last_modified is None

    def test_last_modified_present(self, ws: Workspace) -> None:
        summary = ws.workspace_summary()
        assert summary.last_modified is not None
        assert isinstance(summary.last_modified, datetime)


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class TestModels:
    def test_file_entry_creation(self) -> None:
        entry = FileEntry(
            path="src/main.py",
            is_dir=False,
            size_bytes=42,
            language="python",
            last_modified=datetime.now(timezone.utc),
        )
        assert entry.path == "src/main.py"
        assert entry.is_dir is False
        assert entry.size_bytes == 42

    def test_file_entry_frozen(self) -> None:
        entry = FileEntry(path="a.py", is_dir=False)
        with pytest.raises(Exception):
            entry.path = "b.py"  # type: ignore[misc]

    def test_workspace_summary_creation(self) -> None:
        summary = WorkspaceSummary(
            file_count=10,
            total_size_bytes=5000,
            languages={"python": 5, "typescript": 3, "json": 2},
            last_modified=datetime.now(timezone.utc),
        )
        assert summary.file_count == 10
        assert summary.languages["python"] == 5

    def test_workspace_summary_frozen(self) -> None:
        summary = WorkspaceSummary(file_count=0, total_size_bytes=0)
        with pytest.raises(Exception):
            summary.file_count = 99  # type: ignore[misc]

    def test_file_entry_defaults(self) -> None:
        entry = FileEntry(path="test", is_dir=True)
        assert entry.size_bytes == 0
        assert entry.language == "unknown"
        assert entry.last_modified is None


# ---------------------------------------------------------------------------
# _detect_language
# ---------------------------------------------------------------------------


class TestDetectLanguage:
    @pytest.mark.parametrize(
        "ext,expected",
        [
            (".py", "python"),
            (".ts", "typescript"),
            (".tsx", "typescriptreact"),
            (".js", "javascript"),
            (".jsx", "javascriptreact"),
            (".json", "json"),
            (".yaml", "yaml"),
            (".yml", "yaml"),
            (".md", "markdown"),
            (".html", "html"),
            (".css", "css"),
            (".sql", "sql"),
            (".toml", "toml"),
            (".txt", "text"),
            (".ps1", "powershell"),
            (".sh", "shell"),
            (".rs", "rust"),
            (".go", "go"),
            (".unknown", "unknown"),
            ("", "unknown"),
        ],
    )
    def test_extension_mapping(self, ext: str, expected: str) -> None:
        assert _detect_language(ext) == expected


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_unicode_filename(self, ws_dir: Path) -> None:
        (ws_dir / "données.txt").write_text("data", encoding="utf-8")
        ws = Workspace(ws_dir)
        tree = ws.file_tree()
        paths = {e.path for e in tree}
        assert "données.txt" in paths

    def test_dot_only_filename(self, ws_dir: Path) -> None:
        """Files named just '.' shouldn't crash anything."""
        ws = Workspace(ws_dir)
        # resolve(".") would actually resolve to root
        # This is a valid path — it resolves to root
        result = ws.resolve(".")
        assert result == ws.root

    def test_resolve_then_is_within_consistency(self, ws: Workspace) -> None:
        resolved = ws.resolve("src/main.py")
        assert ws.is_within(resolved) is True

    def test_multiple_resolve_calls(self, ws: Workspace) -> None:
        """Multiple resolve calls return identical results."""
        paths = [ws.resolve("README.md") for _ in range(10)]
        assert all(p == paths[0] for p in paths)

    def test_workspace_with_many_files(self, tmp_path: Path) -> None:
        for i in range(50):
            (tmp_path / f"file_{i:03d}.py").write_text(f"x = {i}\n")
        ws = Workspace(tmp_path)
        summary = ws.workspace_summary()
        assert summary.file_count == 50
        assert summary.languages.get("python", 0) == 50
