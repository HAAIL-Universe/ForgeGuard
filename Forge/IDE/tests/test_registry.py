"""Tests for forge_ide.registry and adapter integration.

Registry tests use mock handlers.  Adapter integration tests use real
file I/O via tmp directories and the actual tool_executor handlers.
"""

import os
import textwrap

import pytest

from forge_ide.adapters import register_builtin_tools
from forge_ide.contracts import (
    ReadFileRequest,
    ToolResponse,
    WriteFileRequest,
)
from forge_ide.errors import ToolNotFound
from forge_ide.registry import Registry


# ===================================================================
# Registry unit tests
# ===================================================================


class TestRegistryRegister:
    def test_register_tool(self):
        reg = Registry()
        reg.register("my_tool", lambda req, wd: {}, ReadFileRequest, "A tool")
        assert reg.has_tool("my_tool")
        assert "my_tool" in reg.tool_names()

    def test_duplicate_rejected(self):
        reg = Registry()
        reg.register("t", lambda r, w: {}, ReadFileRequest, "desc")
        with pytest.raises(ValueError, match="already registered"):
            reg.register("t", lambda r, w: {}, ReadFileRequest, "desc")

    def test_register_multiple(self):
        reg = Registry()
        reg.register("a", lambda r, w: {}, ReadFileRequest, "A")
        reg.register("b", lambda r, w: {}, ReadFileRequest, "B")
        assert reg.tool_names() == ["a", "b"]


class TestRegistryHasTool:
    def test_exists(self):
        reg = Registry()
        reg.register("x", lambda r, w: {}, ReadFileRequest, "X")
        assert reg.has_tool("x") is True

    def test_not_exists(self):
        reg = Registry()
        assert reg.has_tool("nope") is False


class TestRegistryListTools:
    def test_anthropic_shape(self):
        reg = Registry()
        reg.register("read_file", lambda r, w: {}, ReadFileRequest, "Read a file")

        tools = reg.list_tools()
        assert len(tools) == 1

        t = tools[0]
        assert t["name"] == "read_file"
        assert t["description"] == "Read a file"
        assert "input_schema" in t
        schema = t["input_schema"]
        assert schema["type"] == "object"
        assert "properties" in schema
        assert "path" in schema["properties"]
        assert "required" in schema

    def test_all_registered_present(self):
        reg = Registry()
        reg.register("a", lambda r, w: {}, ReadFileRequest, "A")
        reg.register("b", lambda r, w: {}, ReadFileRequest, "B")
        names = {t["name"] for t in reg.list_tools()}
        assert names == {"a", "b"}


@pytest.mark.asyncio
class TestRegistryDispatch:
    async def test_happy_path(self):
        def handler(req: ReadFileRequest, wd: str) -> dict:
            return {"path": req.path, "content": "hello", "size_bytes": 5, "truncated": False}

        reg = Registry()
        reg.register("read_file", handler, ReadFileRequest, "Read")
        result = await reg.dispatch("read_file", {"path": "foo.py"}, "/work")

        assert result.success is True
        assert result.data["path"] == "foo.py"
        assert result.data["content"] == "hello"
        assert result.duration_ms >= 0

    async def test_unknown_tool(self):
        reg = Registry()
        with pytest.raises(ToolNotFound):
            await reg.dispatch("nonexistent", {}, "/work")

    async def test_invalid_params(self):
        reg = Registry()
        reg.register("read_file", lambda r, w: {}, ReadFileRequest, "Read")

        result = await reg.dispatch("read_file", {"path": ""}, "/work")
        assert result.success is False
        assert "Invalid params" in result.error

    async def test_missing_required_params(self):
        reg = Registry()
        reg.register("read_file", lambda r, w: {}, ReadFileRequest, "Read")

        result = await reg.dispatch("read_file", {}, "/work")
        assert result.success is False

    async def test_handler_exception(self):
        def handler(req, wd):
            raise RuntimeError("kaboom")

        reg = Registry()
        reg.register("boom", handler, ReadFileRequest, "Boom")
        result = await reg.dispatch("boom", {"path": "x"}, "/work")

        assert result.success is False
        assert "kaboom" in result.error
        assert result.duration_ms >= 0

    async def test_handler_returns_tool_response(self):
        def handler(req, wd):
            return ToolResponse.ok({"custom": True})

        reg = Registry()
        reg.register("custom", handler, ReadFileRequest, "Custom")
        result = await reg.dispatch("custom", {"path": "x"}, "/work")

        assert result.success is True
        assert result.data["custom"] is True
        assert result.duration_ms >= 0

    async def test_async_handler(self):
        async def handler(req: ReadFileRequest, wd: str) -> dict:
            return {"path": req.path, "content": "async!", "size_bytes": 6, "truncated": False}

        reg = Registry()
        reg.register("async_tool", handler, ReadFileRequest, "Async")
        result = await reg.dispatch("async_tool", {"path": "a.py"}, "/work")

        assert result.success is True
        assert result.data["content"] == "async!"


# ===================================================================
# Adapter integration tests — real file I/O via tmp dirs
# ===================================================================


@pytest.mark.asyncio
class TestAdapterIntegration:
    """Test all 7 builtin adapters through the Registry using real files."""

    @pytest.fixture
    def registry(self):
        reg = Registry()
        register_builtin_tools(reg)
        return reg

    @pytest.fixture
    def workspace(self, tmp_path):
        """Create a minimal workspace with some files."""
        # Python file
        py_file = tmp_path / "app" / "main.py"
        py_file.parent.mkdir(parents=True)
        py_file.write_text(
            textwrap.dedent("""\
                import os
                
                def hello():
                    return "world"
            """),
            encoding="utf-8",
        )

        # Another file for search
        util_file = tmp_path / "app" / "utils.py"
        util_file.write_text(
            textwrap.dedent("""\
                import os
                import sys
                
                def helper():
                    return os.getcwd()
            """),
            encoding="utf-8",
        )

        # Bad syntax file
        bad_file = tmp_path / "bad.py"
        bad_file.write_text("def broken(\n", encoding="utf-8")

        return str(tmp_path)

    async def test_builtin_tools_registered(self, registry):
        expected = {
            "read_file", "list_directory", "search_code", "write_file",
            "run_tests", "check_syntax", "run_command",
        }
        assert set(registry.tool_names()) == expected

    async def test_list_tools_shape(self, registry):
        tools = registry.list_tools()
        assert len(tools) == 7
        for t in tools:
            assert "name" in t
            assert "description" in t
            assert "input_schema" in t
            assert t["input_schema"]["type"] == "object"

    # -- read_file --

    async def test_read_file(self, registry, workspace):
        result = await registry.dispatch(
            "read_file", {"path": "app/main.py"}, workspace
        )
        assert result.success is True
        assert "def hello" in result.data["content"]
        assert result.data["path"] == "app/main.py"
        assert result.data["truncated"] is False
        assert result.data["size_bytes"] > 0

    async def test_read_file_not_found(self, registry, workspace):
        result = await registry.dispatch(
            "read_file", {"path": "nonexistent.py"}, workspace
        )
        assert result.success is False
        assert "not found" in result.error.lower() or "Error" in result.error

    async def test_read_file_traversal(self, registry, workspace):
        result = await registry.dispatch(
            "read_file", {"path": "../etc/passwd"}, workspace
        )
        assert result.success is False

    # -- list_directory --

    async def test_list_directory(self, registry, workspace):
        result = await registry.dispatch(
            "list_directory", {"path": "."}, workspace
        )
        assert result.success is True
        names = {e["name"] for e in result.data["entries"]}
        assert "app" in names
        assert "bad.py" in names

    async def test_list_directory_subdir(self, registry, workspace):
        result = await registry.dispatch(
            "list_directory", {"path": "app"}, workspace
        )
        assert result.success is True
        names = {e["name"] for e in result.data["entries"]}
        assert "main.py" in names
        assert "utils.py" in names
        # Check is_dir flags
        for entry in result.data["entries"]:
            assert "is_dir" in entry

    async def test_list_directory_not_found(self, registry, workspace):
        result = await registry.dispatch(
            "list_directory", {"path": "nope/"}, workspace
        )
        assert result.success is False

    # -- search_code --

    async def test_search_code(self, registry, workspace):
        result = await registry.dispatch(
            "search_code", {"pattern": "import os"}, workspace
        )
        assert result.success is True
        assert result.data["total_count"] >= 2
        for m in result.data["matches"]:
            assert "path" in m
            assert "line" in m
            assert "snippet" in m

    async def test_search_code_with_glob(self, registry, workspace):
        result = await registry.dispatch(
            "search_code", {"pattern": "def hello", "glob": "*.py"}, workspace
        )
        assert result.success is True
        assert result.data["total_count"] >= 1

    async def test_search_code_no_match(self, registry, workspace):
        result = await registry.dispatch(
            "search_code",
            {"pattern": "XYZZY_NEVER_FOUND_12345"},
            workspace,
        )
        assert result.success is True
        assert result.data["total_count"] == 0
        assert result.data["matches"] == []

    # -- write_file --

    async def test_write_file(self, registry, workspace):
        result = await registry.dispatch(
            "write_file",
            {"path": "new_file.txt", "content": "hello world"},
            workspace,
        )
        assert result.success is True
        assert result.data["bytes_written"] == 11
        assert result.data["created"] is True
        assert result.data["skipped"] is False

        # Verify file exists
        assert os.path.isfile(os.path.join(workspace, "new_file.txt"))

    async def test_write_file_duplicate_skip(self, registry, workspace):
        content = "exact content"
        # First write
        await registry.dispatch(
            "write_file", {"path": "dup.txt", "content": content}, workspace
        )
        # Second write — should skip
        result = await registry.dispatch(
            "write_file", {"path": "dup.txt", "content": content}, workspace
        )
        assert result.success is True
        assert result.data["skipped"] is True
        assert result.data["bytes_written"] == 0

    async def test_write_file_nested_dirs(self, registry, workspace):
        result = await registry.dispatch(
            "write_file",
            {"path": "deep/nested/dir/file.py", "content": "# deep"},
            workspace,
        )
        assert result.success is True
        assert result.data["created"] is True

    async def test_write_file_traversal(self, registry, workspace):
        result = await registry.dispatch(
            "write_file",
            {"path": "../escape.txt", "content": "bad"},
            workspace,
        )
        assert result.success is False

    # -- check_syntax --

    async def test_check_syntax_valid(self, registry, workspace):
        result = await registry.dispatch(
            "check_syntax", {"file_path": "app/main.py"}, workspace
        )
        assert result.success is True
        assert result.data["valid"] is True
        assert result.data["error_message"] is None

    async def test_check_syntax_invalid(self, registry, workspace):
        result = await registry.dispatch(
            "check_syntax", {"file_path": "bad.py"}, workspace
        )
        assert result.success is True  # The tool succeeded, the file has errors
        assert result.data["valid"] is False
        assert result.data["error_message"] is not None
        assert result.data["line"] is not None

    async def test_check_syntax_not_found(self, registry, workspace):
        result = await registry.dispatch(
            "check_syntax", {"file_path": "doesnt_exist.py"}, workspace
        )
        assert result.success is False

    # -- run_tests / run_command --
    # These are validation-only tests (no subprocess execution in CI)

    async def test_run_tests_blocked_command(self, registry, workspace):
        result = await registry.dispatch(
            "run_tests", {"command": "rm -rf /"}, workspace
        )
        assert result.success is False
        assert "not in allowlist" in result.error.lower() or "Error" in result.error

    async def test_run_command_blocked(self, registry, workspace):
        result = await registry.dispatch(
            "run_command", {"command": "curl http://evil.com"}, workspace
        )
        assert result.success is False

    async def test_run_command_injection(self, registry, workspace):
        result = await registry.dispatch(
            "run_command", {"command": "ls; rm -rf /"}, workspace
        )
        assert result.success is False
        assert "disallowed character" in result.error.lower() or "Error" in result.error
