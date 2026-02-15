"""Tests for app.services.tool_executor -- sandboxed tool execution."""

import os
import textwrap

import pytest

from app.services.tool_executor import (
    BUILDER_TOOLS,
    MAX_READ_FILE_BYTES,
    MAX_WRITE_FILE_BYTES,
    SKIP_DIRS,
    _resolve_sandboxed,
    execute_tool,
)


# ---------------------------------------------------------------------------
# _resolve_sandboxed
# ---------------------------------------------------------------------------

class TestResolveSandboxed:
    """Path sandboxing validation."""

    def test_valid_relative_path(self, tmp_path):
        target = _resolve_sandboxed("foo/bar.py", str(tmp_path))
        assert target is not None
        assert str(target).startswith(str(tmp_path.resolve()))

    def test_rejects_absolute_path(self, tmp_path):
        assert _resolve_sandboxed("/etc/passwd", str(tmp_path)) is None

    def test_rejects_windows_absolute_path(self, tmp_path):
        assert _resolve_sandboxed("C:\\Windows\\system32", str(tmp_path)) is None

    def test_rejects_dot_dot_traversal(self, tmp_path):
        assert _resolve_sandboxed("../../../etc/passwd", str(tmp_path)) is None

    def test_rejects_mid_path_dot_dot(self, tmp_path):
        assert _resolve_sandboxed("foo/../../etc/passwd", str(tmp_path)) is None

    def test_rejects_empty_path(self, tmp_path):
        assert _resolve_sandboxed("", str(tmp_path)) is None

    def test_rejects_empty_working_dir(self):
        assert _resolve_sandboxed("foo.py", "") is None

    def test_simple_filename(self, tmp_path):
        target = _resolve_sandboxed("README.md", str(tmp_path))
        assert target is not None
        assert target.name == "README.md"

    def test_nested_path(self, tmp_path):
        target = _resolve_sandboxed("src/app/main.py", str(tmp_path))
        assert target is not None
        assert target == (tmp_path / "src" / "app" / "main.py").resolve()


# ---------------------------------------------------------------------------
# read_file tool
# ---------------------------------------------------------------------------

class TestReadFile:
    """Tests for the read_file tool handler."""

    def test_read_existing_file(self, tmp_path):
        (tmp_path / "hello.txt").write_text("hello world", encoding="utf-8")
        result = execute_tool("read_file", {"path": "hello.txt"}, str(tmp_path))
        assert result == "hello world"

    def test_read_nested_file(self, tmp_path):
        sub = tmp_path / "src"
        sub.mkdir()
        (sub / "main.py").write_text("print('hi')", encoding="utf-8")
        result = execute_tool("read_file", {"path": "src/main.py"}, str(tmp_path))
        assert result == "print('hi')"

    def test_file_not_found(self, tmp_path):
        result = execute_tool("read_file", {"path": "missing.txt"}, str(tmp_path))
        assert "Error" in result
        assert "not found" in result

    def test_path_traversal_blocked(self, tmp_path):
        result = execute_tool("read_file", {"path": "../../etc/passwd"}, str(tmp_path))
        assert "Error" in result

    def test_truncation_at_limit(self, tmp_path):
        big_content = "x" * (MAX_READ_FILE_BYTES + 1000)
        (tmp_path / "big.txt").write_text(big_content, encoding="utf-8")
        result = execute_tool("read_file", {"path": "big.txt"}, str(tmp_path))
        assert "truncated" in result
        assert len(result) <= MAX_READ_FILE_BYTES + 200  # some overhead for msg

    def test_directory_not_a_file(self, tmp_path):
        (tmp_path / "subdir").mkdir()
        result = execute_tool("read_file", {"path": "subdir"}, str(tmp_path))
        assert "Error" in result
        assert "not a file" in result

    def test_empty_path(self, tmp_path):
        result = execute_tool("read_file", {"path": ""}, str(tmp_path))
        assert "Error" in result


# ---------------------------------------------------------------------------
# list_directory tool
# ---------------------------------------------------------------------------

class TestListDirectory:
    """Tests for the list_directory tool handler."""

    def test_list_root(self, tmp_path):
        (tmp_path / "file.py").write_text("x")
        (tmp_path / "subdir").mkdir()
        result = execute_tool("list_directory", {"path": "."}, str(tmp_path))
        assert "file.py" in result
        assert "subdir/" in result

    def test_list_subdirectory(self, tmp_path):
        sub = tmp_path / "src"
        sub.mkdir()
        (sub / "main.py").write_text("x")
        (sub / "utils.py").write_text("x")
        result = execute_tool("list_directory", {"path": "src"}, str(tmp_path))
        assert "main.py" in result
        assert "utils.py" in result

    def test_skips_hidden_dirs(self, tmp_path):
        (tmp_path / ".git").mkdir()
        (tmp_path / "__pycache__").mkdir()
        (tmp_path / "node_modules").mkdir()
        (tmp_path / "app.py").write_text("x")
        result = execute_tool("list_directory", {"path": "."}, str(tmp_path))
        assert ".git" not in result
        assert "__pycache__" not in result
        assert "node_modules" not in result
        assert "app.py" in result

    def test_empty_directory(self, tmp_path):
        (tmp_path / "empty").mkdir()
        result = execute_tool("list_directory", {"path": "empty"}, str(tmp_path))
        assert "empty directory" in result

    def test_not_a_directory(self, tmp_path):
        (tmp_path / "file.txt").write_text("x")
        result = execute_tool("list_directory", {"path": "file.txt"}, str(tmp_path))
        assert "Error" in result
        assert "not a directory" in result

    def test_nonexistent_directory(self, tmp_path):
        result = execute_tool("list_directory", {"path": "nope"}, str(tmp_path))
        assert "Error" in result

    def test_default_path_is_root(self, tmp_path):
        (tmp_path / "root_file.py").write_text("x")
        result = execute_tool("list_directory", {}, str(tmp_path))
        assert "root_file.py" in result


# ---------------------------------------------------------------------------
# search_code tool
# ---------------------------------------------------------------------------

class TestSearchCode:
    """Tests for the search_code tool handler."""

    def test_literal_search(self, tmp_path):
        (tmp_path / "main.py").write_text("def hello_world():\n    pass\n")
        result = execute_tool(
            "search_code", {"pattern": "hello_world"}, str(tmp_path)
        )
        assert "main.py:1:" in result
        assert "hello_world" in result

    def test_regex_search(self, tmp_path):
        (tmp_path / "app.py").write_text("class MyFoo:\n    x = 42\n")
        result = execute_tool(
            "search_code", {"pattern": r"class\s+\w+:"}, str(tmp_path)
        )
        assert "app.py:1:" in result

    def test_glob_filter(self, tmp_path):
        (tmp_path / "main.py").write_text("target_value = 1\n")
        (tmp_path / "main.js").write_text("target_value = 1\n")
        result = execute_tool(
            "search_code", {"pattern": "target_value", "glob": "*.py"}, str(tmp_path)
        )
        assert "main.py" in result
        assert "main.js" not in result

    def test_no_results(self, tmp_path):
        (tmp_path / "empty.py").write_text("pass\n")
        result = execute_tool(
            "search_code", {"pattern": "zzz_not_here"}, str(tmp_path)
        )
        assert "No matches" in result

    def test_empty_pattern_error(self, tmp_path):
        result = execute_tool("search_code", {"pattern": ""}, str(tmp_path))
        assert "Error" in result

    def test_skips_excluded_dirs(self, tmp_path):
        cache = tmp_path / "__pycache__"
        cache.mkdir()
        (cache / "cached.py").write_text("target_find_me\n")
        (tmp_path / "normal.py").write_text("target_find_me\n")
        result = execute_tool(
            "search_code", {"pattern": "target_find_me"}, str(tmp_path)
        )
        assert "normal.py" in result
        assert "__pycache__" not in result

    def test_invalid_regex_falls_back_to_literal(self, tmp_path):
        (tmp_path / "test.py").write_text("foo[bar\n")
        result = execute_tool(
            "search_code", {"pattern": "foo[bar"}, str(tmp_path)
        )
        assert "test.py:1:" in result

    def test_case_insensitive(self, tmp_path):
        (tmp_path / "test.py").write_text("MyVariable = 42\n")
        result = execute_tool(
            "search_code", {"pattern": "myvariable"}, str(tmp_path)
        )
        assert "test.py:1:" in result


# ---------------------------------------------------------------------------
# write_file tool
# ---------------------------------------------------------------------------

class TestWriteFile:
    """Tests for the write_file tool handler."""

    def test_write_new_file(self, tmp_path):
        result = execute_tool(
            "write_file",
            {"path": "output.py", "content": "print('hello')"},
            str(tmp_path),
        )
        assert result.startswith("OK:")
        assert (tmp_path / "output.py").read_text() == "print('hello')"

    def test_write_creates_parent_dirs(self, tmp_path):
        result = execute_tool(
            "write_file",
            {"path": "src/deep/nested/file.py", "content": "x = 1"},
            str(tmp_path),
        )
        assert result.startswith("OK:")
        assert (tmp_path / "src" / "deep" / "nested" / "file.py").exists()

    def test_overwrite_existing_file(self, tmp_path):
        (tmp_path / "exist.txt").write_text("old content")
        result = execute_tool(
            "write_file",
            {"path": "exist.txt", "content": "new content"},
            str(tmp_path),
        )
        assert result.startswith("OK:")
        assert (tmp_path / "exist.txt").read_text() == "new content"

    def test_rejects_path_traversal(self, tmp_path):
        result = execute_tool(
            "write_file",
            {"path": "../../evil.py", "content": "hack"},
            str(tmp_path),
        )
        assert "Error" in result

    def test_rejects_oversized_content(self, tmp_path):
        big_content = "x" * (MAX_WRITE_FILE_BYTES + 1)
        result = execute_tool(
            "write_file",
            {"path": "big.py", "content": big_content},
            str(tmp_path),
        )
        assert "Error" in result
        assert "limit" in result
        assert not (tmp_path / "big.py").exists()

    def test_reports_byte_count(self, tmp_path):
        content = "hello world"
        result = execute_tool(
            "write_file",
            {"path": "out.txt", "content": content},
            str(tmp_path),
        )
        assert str(len(content)) in result

    def test_empty_content_writes_empty_file(self, tmp_path):
        result = execute_tool(
            "write_file",
            {"path": "empty.txt", "content": ""},
            str(tmp_path),
        )
        assert result.startswith("OK:")
        assert (tmp_path / "empty.txt").read_text() == ""


# ---------------------------------------------------------------------------
# execute_tool dispatcher
# ---------------------------------------------------------------------------

class TestExecuteTool:
    """Tests for the execute_tool dispatcher."""

    def test_unknown_tool(self, tmp_path):
        result = execute_tool("nonexistent_tool", {}, str(tmp_path))
        assert "Error" in result
        assert "Unknown tool" in result

    def test_exception_in_handler_returns_error(self, tmp_path, monkeypatch):
        """If a handler raises, dispatcher catches and returns error string."""
        def boom(_inp, _wd):
            raise RuntimeError("kaboom")

        monkeypatch.setattr(
            "app.services.tool_executor._exec_read_file", boom
        )
        result = execute_tool("read_file", {"path": "x"}, str(tmp_path))
        assert "Error" in result
        assert "kaboom" in result


# ---------------------------------------------------------------------------
# BUILDER_TOOLS spec
# ---------------------------------------------------------------------------

class TestBuilderToolsSpec:
    """Verify the BUILDER_TOOLS constant is well-formed."""

    def test_has_four_tools(self):
        assert len(BUILDER_TOOLS) == 4

    def test_tool_names(self):
        names = {t["name"] for t in BUILDER_TOOLS}
        assert names == {"read_file", "list_directory", "search_code", "write_file"}

    def test_each_tool_has_required_fields(self):
        for tool in BUILDER_TOOLS:
            assert "name" in tool
            assert "description" in tool
            assert "input_schema" in tool
            schema = tool["input_schema"]
            assert schema.get("type") == "object"
            assert "properties" in schema
            assert "required" in schema


# ---------------------------------------------------------------------------
# SKIP_DIRS constant
# ---------------------------------------------------------------------------

class TestSkipDirs:
    """Verify SKIP_DIRS contains expected entries."""

    def test_contains_common_dirs(self):
        for d in [".git", "__pycache__", "node_modules", ".venv"]:
            assert d in SKIP_DIRS
