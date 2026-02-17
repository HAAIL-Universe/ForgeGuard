"""Tests for forge_ide.file_index — file index & import graph."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from forge_ide.file_index import (
    FileIndex,
    FileMetadata,
    _extract_python_exports,
    _extract_python_imports,
)
from forge_ide.workspace import Workspace


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@pytest.fixture()
def ws(tmp_path: Path) -> Workspace:
    """Workspace with sample Python files for indexing."""
    (tmp_path / "main.py").write_text(
        textwrap.dedent("""\
            import os
            import sys
            from pathlib import Path

            def main():
                pass

            class App:
                pass

            VERSION = "1.0"
            _internal = True
        """),
        encoding="utf-8",
    )
    (tmp_path / "utils.py").write_text(
        textwrap.dedent("""\
            from . import main
            from ..pkg import helper

            def util_func():
                return True

            async def async_util():
                pass
        """),
        encoding="utf-8",
    )
    (tmp_path / "circular_a.py").write_text(
        "import circular_b\n\ndef func_a():\n    pass\n",
        encoding="utf-8",
    )
    (tmp_path / "circular_b.py").write_text(
        "import circular_a\n\ndef func_b():\n    pass\n",
        encoding="utf-8",
    )
    (tmp_path / "data.json").write_text('{"key": "value"}', encoding="utf-8")
    (tmp_path / "readme.md").write_text("# Hello\n", encoding="utf-8")
    (tmp_path / "conditional.py").write_text(
        textwrap.dedent("""\
            try:
                import optional_pkg
            except ImportError:
                optional_pkg = None

            if True:
                from conditional_mod import thing
        """),
        encoding="utf-8",
    )
    return Workspace(tmp_path, cache_ttl=0)


@pytest.fixture()
def idx(ws: Workspace) -> FileIndex:
    """Pre-built file index."""
    return FileIndex.build(ws)


# ===================================================================
# FileMetadata model
# ===================================================================


class TestFileMetadata:
    def test_creation(self) -> None:
        m = FileMetadata(path="foo.py", language="python", size_bytes=100)
        assert m.path == "foo.py"
        assert m.language == "python"
        assert m.size_bytes == 100

    def test_frozen(self) -> None:
        m = FileMetadata(path="foo.py")
        with pytest.raises(Exception):
            m.path = "bar.py"  # type: ignore[misc]

    def test_defaults(self) -> None:
        m = FileMetadata(path="foo.py")
        assert m.language == "unknown"
        assert m.size_bytes == 0
        assert m.last_modified is None
        assert m.imports == ()
        assert m.exports == ()

    def test_imports_exports_tuples(self) -> None:
        m = FileMetadata(
            path="foo.py",
            imports=("os", "sys"),
            exports=("main", "App"),
        )
        assert m.imports == ("os", "sys")
        assert m.exports == ("main", "App")


# ===================================================================
# _extract_python_imports
# ===================================================================


class TestExtractPythonImports:
    def test_simple_import(self) -> None:
        assert _extract_python_imports("import os") == ["os"]

    def test_multi_import(self) -> None:
        result = _extract_python_imports("import os, sys")
        assert "os" in result
        assert "sys" in result

    def test_from_import(self) -> None:
        assert _extract_python_imports("from os.path import join") == ["os.path"]

    def test_relative_import_dot(self) -> None:
        assert _extract_python_imports("from . import sibling") == [".sibling"]

    def test_relative_import_double_dot(self) -> None:
        assert _extract_python_imports("from .. import parent") == ["..parent"]

    def test_relative_import_with_module(self) -> None:
        assert _extract_python_imports("from ..pkg import mod") == ["..pkg"]

    def test_relative_import_bare(self) -> None:
        # from . import something → ".something"
        result = _extract_python_imports("from . import foo")
        assert result == [".foo"]

    def test_conditional_import_try(self) -> None:
        source = "try:\n    import optional\nexcept ImportError:\n    pass\n"
        result = _extract_python_imports(source)
        assert "optional" in result

    def test_conditional_import_if(self) -> None:
        source = "if True:\n    from cond import thing\n"
        result = _extract_python_imports(source)
        assert "cond" in result

    def test_alias_import(self) -> None:
        result = _extract_python_imports("import os as operating_system")
        assert result == ["os"]

    def test_future_import(self) -> None:
        result = _extract_python_imports("from __future__ import annotations")
        assert result == ["__future__"]

    def test_syntax_error(self) -> None:
        result = _extract_python_imports("def foo(:\n  pass\n")
        assert result == []

    def test_empty_source(self) -> None:
        assert _extract_python_imports("") == []


# ===================================================================
# _extract_python_exports
# ===================================================================


class TestExtractPythonExports:
    def test_function(self) -> None:
        result = _extract_python_exports("def foo():\n    pass\n")
        assert "foo" in result

    def test_async_function(self) -> None:
        result = _extract_python_exports("async def bar():\n    pass\n")
        assert "bar" in result

    def test_class(self) -> None:
        result = _extract_python_exports("class MyClass:\n    pass\n")
        assert "MyClass" in result

    def test_assignment(self) -> None:
        result = _extract_python_exports("VERSION = '1.0'\n")
        assert "VERSION" in result

    def test_private_excluded(self) -> None:
        result = _extract_python_exports("_internal = True\n")
        assert "_internal" not in result

    def test_nested_function_excluded(self) -> None:
        source = "def outer():\n    def inner():\n        pass\n"
        result = _extract_python_exports(source)
        assert "outer" in result
        assert "inner" not in result

    def test_multiple_definitions(self) -> None:
        source = "def a():\n    pass\ndef b():\n    pass\nclass C:\n    pass\n"
        result = _extract_python_exports(source)
        assert set(result) == {"a", "b", "C"}

    def test_syntax_error(self) -> None:
        result = _extract_python_exports("def foo(:\n  pass\n")
        assert result == []


# ===================================================================
# FileIndex.build
# ===================================================================


class TestFileIndexBuild:
    def test_basic_build(self, idx: FileIndex) -> None:
        files = idx.all_files()
        assert "main.py" in files
        assert "utils.py" in files

    def test_non_python_indexed(self, idx: FileIndex) -> None:
        meta = idx.get_metadata("data.json")
        assert meta is not None
        assert meta.language == "json"
        assert meta.imports == ()

    def test_language_detection(self, idx: FileIndex) -> None:
        assert idx.get_metadata("main.py").language == "python"
        assert idx.get_metadata("readme.md").language == "markdown"

    def test_size_bytes(self, idx: FileIndex) -> None:
        meta = idx.get_metadata("main.py")
        assert meta.size_bytes > 0

    def test_file_count(self, idx: FileIndex, ws: Workspace) -> None:
        tree = ws.file_tree()
        file_count = sum(1 for e in tree if not e.is_dir)
        assert len(idx.all_files()) == file_count

    def test_syntax_error_no_crash(self, tmp_path: Path) -> None:
        (tmp_path / "bad.py").write_text("def foo(:\n  pass\n", encoding="utf-8")
        w = Workspace(tmp_path, cache_ttl=0)
        idx = FileIndex.build(w)
        meta = idx.get_metadata("bad.py")
        assert meta is not None
        assert meta.imports == ()
        assert meta.exports == ()

    def test_empty_workspace(self, tmp_path: Path) -> None:
        w = Workspace(tmp_path, cache_ttl=0)
        idx = FileIndex.build(w)
        assert idx.all_files() == []

    def test_directory_entries_excluded(self, idx: FileIndex) -> None:
        for f in idx.all_files():
            meta = idx.get_metadata(f)
            # All entries should be files, not dirs
            assert meta is not None

    def test_last_modified_populated(self, idx: FileIndex) -> None:
        meta = idx.get_metadata("main.py")
        assert meta.last_modified is not None

    def test_imports_populated(self, idx: FileIndex) -> None:
        meta = idx.get_metadata("main.py")
        assert "os" in meta.imports
        assert "sys" in meta.imports
        assert "pathlib" in meta.imports

    def test_exports_populated(self, idx: FileIndex) -> None:
        meta = idx.get_metadata("main.py")
        assert "main" in meta.exports
        assert "App" in meta.exports
        assert "VERSION" in meta.exports
        assert "_internal" not in meta.exports


# ===================================================================
# get_imports / get_importers
# ===================================================================


class TestImportGraph:
    def test_get_imports(self, idx: FileIndex) -> None:
        imports = idx.get_imports("main.py")
        assert "os" in imports
        assert "sys" in imports

    def test_get_imports_nonexistent(self, idx: FileIndex) -> None:
        assert idx.get_imports("nope.py") == []

    def test_get_importers(self, idx: FileIndex) -> None:
        # main.py imports "os", so main.py should be in importers of "os"
        importers = idx.get_importers("os")
        assert "main.py" in importers

    def test_get_importers_unknown(self, idx: FileIndex) -> None:
        assert idx.get_importers("unknown_module_xyz") == []

    def test_circular_imports(self, idx: FileIndex) -> None:
        a_imports = idx.get_imports("circular_a.py")
        b_imports = idx.get_imports("circular_b.py")
        assert "circular_b" in a_imports
        assert "circular_a" in b_imports

        a_importers = idx.get_importers("circular_a")
        b_importers = idx.get_importers("circular_b")
        assert "circular_b.py" in a_importers
        assert "circular_a.py" in b_importers

    def test_multiple_importers(self, idx: FileIndex) -> None:
        # Both main.py and circular_a/circular_b may import various things
        # Check that "os" importers includes main.py
        importers = idx.get_importers("os")
        assert "main.py" in importers

    def test_relative_imports(self, idx: FileIndex) -> None:
        imports = idx.get_imports("utils.py")
        assert ".main" in imports
        assert "..pkg" in imports

    def test_conditional_imports_captured(self, idx: FileIndex) -> None:
        imports = idx.get_imports("conditional.py")
        assert "optional_pkg" in imports
        assert "conditional_mod" in imports


# ===================================================================
# invalidate_file
# ===================================================================


class TestInvalidateFile:
    def test_invalidate_existing(self, idx: FileIndex, ws: Workspace) -> None:
        # Modify the file on disk
        (ws.root / "main.py").write_text(
            "import json\n\ndef new_func():\n    pass\n", encoding="utf-8"
        )
        idx.invalidate_file("main.py")
        meta = idx.get_metadata("main.py")
        assert meta is not None
        assert "json" in meta.imports
        assert "os" not in meta.imports
        assert "new_func" in meta.exports

    def test_invalidate_deleted(self, idx: FileIndex, ws: Workspace) -> None:
        (ws.root / "main.py").unlink()
        idx.invalidate_file("main.py")
        assert idx.get_metadata("main.py") is None
        assert "main.py" not in idx.all_files()

    def test_invalidate_nonexistent(self, idx: FileIndex) -> None:
        # Should not raise
        idx.invalidate_file("totally_fake.py")

    def test_reverse_graph_rebuilt(self, idx: FileIndex, ws: Workspace) -> None:
        # Initially main.py imports "os"
        assert "main.py" in idx.get_importers("os")

        # Change main.py to not import os
        (ws.root / "main.py").write_text("import json\n", encoding="utf-8")
        idx.invalidate_file("main.py")

        assert "main.py" not in idx.get_importers("os")
        assert "main.py" in idx.get_importers("json")

    def test_imports_updated(self, idx: FileIndex, ws: Workspace) -> None:
        (ws.root / "main.py").write_text(
            "from collections import OrderedDict\n", encoding="utf-8"
        )
        idx.invalidate_file("main.py")
        imports = idx.get_imports("main.py")
        assert "collections" in imports
        assert "os" not in imports


# ===================================================================
# Utility methods
# ===================================================================


class TestUtilityMethods:
    def test_all_files_sorted(self, idx: FileIndex) -> None:
        files = idx.all_files()
        assert files == sorted(files)

    def test_languages(self, idx: FileIndex) -> None:
        langs = idx.languages()
        assert "python" in langs
        assert langs["python"] >= 4  # main, utils, circular_a, circular_b, conditional

    def test_get_metadata_existing(self, idx: FileIndex) -> None:
        meta = idx.get_metadata("main.py")
        assert meta is not None
        assert meta.path == "main.py"

    def test_get_metadata_nonexistent(self, idx: FileIndex) -> None:
        assert idx.get_metadata("nope.py") is None

    def test_repr(self, idx: FileIndex) -> None:
        r = repr(idx)
        assert "FileIndex" in r
        assert "files=" in r


# ===================================================================
# Mixed file types
# ===================================================================


class TestMixedFileTypes:
    def test_json_no_imports(self, idx: FileIndex) -> None:
        meta = idx.get_metadata("data.json")
        assert meta.imports == ()
        assert meta.exports == ()

    def test_markdown_no_imports(self, idx: FileIndex) -> None:
        meta = idx.get_metadata("readme.md")
        assert meta.imports == ()
        assert meta.exports == ()

    def test_all_languages_correct(self, idx: FileIndex) -> None:
        langs = idx.languages()
        assert "json" in langs
        assert "markdown" in langs
