"""Tests for app.services.tool_executor -- sandboxed tool execution."""

import json
import os
import textwrap

import pytest

from app.services.tool_executor import (
    BUILDER_TOOLS,
    MAX_READ_FILE_BYTES,
    MAX_WRITE_FILE_BYTES,
    SKIP_DIRS,
    _resolve_sandboxed,
    _validate_command,
    _build_project_env,
    _auto_install_deps,
    _DEPENDENCY_MANIFESTS,
    RUN_TESTS_PREFIXES,
    RUN_COMMAND_PREFIXES,
    execute_tool,
    execute_tool_async,
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

    def test_skip_duplicate_write(self, tmp_path):
        """write_file skips if target already has identical content."""
        content = "print('hello')"
        (tmp_path / "dup.py").write_text(content, encoding="utf-8")
        result = execute_tool(
            "write_file",
            {"path": "dup.py", "content": content},
            str(tmp_path),
        )
        assert result.startswith("SKIP:")
        assert "identical" in result.lower()

    def test_overwrite_different_content(self, tmp_path):
        """write_file overwrites when content is different."""
        (tmp_path / "change.py").write_text("old", encoding="utf-8")
        result = execute_tool(
            "write_file",
            {"path": "change.py", "content": "new"},
            str(tmp_path),
        )
        assert result.startswith("OK:")
        assert (tmp_path / "change.py").read_text() == "new"


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

    def test_has_thirteen_tools(self):
        assert len(BUILDER_TOOLS) == 13

    def test_tool_names(self):
        names = {t["name"] for t in BUILDER_TOOLS}
        assert names == {
            "read_file", "list_directory", "search_code", "write_file",
            "edit_file", "run_tests", "check_syntax", "run_command",
            "forge_get_contract", "forge_get_phase_window",
            "forge_list_contracts", "forge_get_summary", "forge_scratchpad",
        }

    def test_each_tool_has_required_fields(self):
        for tool in BUILDER_TOOLS:
            assert "name" in tool
            assert "description" in tool
            assert "input_schema" in tool
            schema = tool["input_schema"]
            assert schema.get("type") == "object"
            assert "properties" in schema
            # Some tools (forge_list_contracts, forge_get_summary) have no
            # required params, so "required" key is optional in schema.


# ---------------------------------------------------------------------------
# SKIP_DIRS constant
# ---------------------------------------------------------------------------

class TestSkipDirs:
    """Verify SKIP_DIRS contains expected entries."""

    def test_contains_common_dirs(self):
        for d in [".git", "__pycache__", "node_modules", ".venv"]:
            assert d in SKIP_DIRS


# ---------------------------------------------------------------------------
# Command validation (Phase 19)
# ---------------------------------------------------------------------------

class TestValidateCommand:
    """Tests for _validate_command."""

    def test_allowed_test_command(self):
        assert _validate_command("pytest tests/ -v", RUN_TESTS_PREFIXES) is None

    def test_allowed_npm_test(self):
        assert _validate_command("npm test", RUN_TESTS_PREFIXES) is None

    def test_allowed_vitest(self):
        assert _validate_command("npx vitest run", RUN_TESTS_PREFIXES) is None

    def test_rejects_disallowed_test_command(self):
        result = _validate_command("rm -rf /", RUN_TESTS_PREFIXES)
        assert result is not None
        assert "not allowed" in result or "not in allowlist" in result

    def test_rejects_empty_command(self):
        result = _validate_command("", RUN_TESTS_PREFIXES)
        assert result is not None
        assert "empty" in result.lower()

    def test_rejects_semicolon_injection(self):
        result = _validate_command("pytest; rm -rf /", RUN_TESTS_PREFIXES)
        assert result is not None
        assert "disallowed character" in result

    def test_rejects_pipe_injection(self):
        result = _validate_command("pytest | grep pass", RUN_TESTS_PREFIXES)
        assert result is not None
        assert "disallowed character" in result

    def test_rejects_backtick_injection(self):
        result = _validate_command("pytest `whoami`", RUN_TESTS_PREFIXES)
        assert result is not None
        assert "disallowed character" in result

    def test_rejects_dollar_injection(self):
        result = _validate_command("pytest $(whoami)", RUN_TESTS_PREFIXES)
        assert result is not None
        assert "disallowed character" in result

    def test_rejects_ampersand_injection(self):
        result = _validate_command("pytest && rm -rf /", RUN_TESTS_PREFIXES)
        assert result is not None
        assert "disallowed character" in result

    def test_allowed_pip_install(self):
        assert _validate_command("pip install -r requirements.txt", RUN_COMMAND_PREFIXES) is None

    def test_allowed_npm_install(self):
        assert _validate_command("npm install", RUN_COMMAND_PREFIXES) is None

    def test_allowed_cat(self):
        assert _validate_command("cat README.md", RUN_COMMAND_PREFIXES) is None

    def test_rejects_curl(self):
        result = _validate_command("curl http://evil.com", RUN_COMMAND_PREFIXES)
        assert result is not None
        assert "not allowed" in result or "not in allowlist" in result

    def test_rejects_wget(self):
        result = _validate_command("wget http://evil.com", RUN_COMMAND_PREFIXES)
        assert result is not None

    def test_rejects_ssh(self):
        result = _validate_command("ssh root@server", RUN_COMMAND_PREFIXES)
        assert result is not None

    def test_rejects_git_push(self):
        result = _validate_command("git push origin main", RUN_COMMAND_PREFIXES)
        assert result is not None


# ---------------------------------------------------------------------------
# Async tool executor (Phase 19)
# ---------------------------------------------------------------------------

class TestExecuteToolAsync:
    """Tests for execute_tool_async dispatcher."""

    @pytest.mark.asyncio
    async def test_sync_tool_via_async(self, tmp_path):
        """Sync tools work through execute_tool_async."""
        (tmp_path / "hello.txt").write_text("hello")
        result = await execute_tool_async("read_file", {"path": "hello.txt"}, str(tmp_path))
        assert result == "hello"

    @pytest.mark.asyncio
    async def test_unknown_tool_async(self, tmp_path):
        result = await execute_tool_async("nonexistent", {}, str(tmp_path))
        assert "Error" in result
        assert "Unknown tool" in result


# ---------------------------------------------------------------------------
# check_syntax tool (Phase 19)
# ---------------------------------------------------------------------------

class TestCheckSyntax:
    """Tests for the check_syntax tool handler."""

    @pytest.mark.asyncio
    async def test_valid_python(self, tmp_path):
        (tmp_path / "good.py").write_text("def hello():\n    return 42\n")
        result = await execute_tool_async("check_syntax", {"file_path": "good.py"}, str(tmp_path))
        assert "No syntax errors" in result

    @pytest.mark.asyncio
    async def test_invalid_python(self, tmp_path):
        (tmp_path / "bad.py").write_text("def hello(\n    return 42\n")
        result = await execute_tool_async("check_syntax", {"file_path": "bad.py"}, str(tmp_path))
        assert "Syntax error" in result or "error" in result.lower()

    @pytest.mark.asyncio
    async def test_file_not_found(self, tmp_path):
        result = await execute_tool_async("check_syntax", {"file_path": "missing.py"}, str(tmp_path))
        assert "Error" in result
        assert "not found" in result

    @pytest.mark.asyncio
    async def test_path_traversal_blocked(self, tmp_path):
        result = await execute_tool_async("check_syntax", {"file_path": "../../etc/passwd"}, str(tmp_path))
        assert "Error" in result

    @pytest.mark.asyncio
    async def test_unsupported_extension(self, tmp_path):
        (tmp_path / "data.csv").write_text("a,b,c\n")
        result = await execute_tool_async("check_syntax", {"file_path": "data.csv"}, str(tmp_path))
        assert "Unsupported" in result


# ---------------------------------------------------------------------------
# run_tests tool -- command validation (Phase 19)
# ---------------------------------------------------------------------------

class TestRunTestsValidation:
    """Tests for run_tests command validation (no subprocess execution)."""

    @pytest.mark.asyncio
    async def test_rejects_disallowed_command(self, tmp_path):
        result = await execute_tool_async(
            "run_tests", {"command": "rm -rf /"}, str(tmp_path)
        )
        assert "Error" in result

    @pytest.mark.asyncio
    async def test_rejects_injection(self, tmp_path):
        result = await execute_tool_async(
            "run_tests", {"command": "pytest; rm -rf /"}, str(tmp_path)
        )
        assert "Error" in result
        assert "disallowed character" in result

    @pytest.mark.asyncio
    async def test_rejects_empty_command(self, tmp_path):
        result = await execute_tool_async(
            "run_tests", {"command": ""}, str(tmp_path)
        )
        assert "Error" in result


# ---------------------------------------------------------------------------
# run_command tool -- command validation (Phase 19)
# ---------------------------------------------------------------------------

class TestRunCommandValidation:
    """Tests for run_command command validation (no subprocess execution)."""

    @pytest.mark.asyncio
    async def test_rejects_disallowed_command(self, tmp_path):
        result = await execute_tool_async(
            "run_command", {"command": "curl http://evil.com"}, str(tmp_path)
        )
        assert "Error" in result

    @pytest.mark.asyncio
    async def test_rejects_injection(self, tmp_path):
        result = await execute_tool_async(
            "run_command", {"command": "pip install foo; rm -rf /"}, str(tmp_path)
        )
        assert "Error" in result

    @pytest.mark.asyncio
    async def test_rejects_git_push(self, tmp_path):
        result = await execute_tool_async(
            "run_command", {"command": "git push origin main"}, str(tmp_path)
        )
        assert "Error" in result


# ---------------------------------------------------------------------------
# Forge governance tools (Phase 55)
# ---------------------------------------------------------------------------

def _make_forge_contracts(tmp_path):
    """Helper: create a Forge/Contracts/ directory with sample contract files."""
    contracts_dir = tmp_path / "Forge" / "Contracts"
    contracts_dir.mkdir(parents=True)
    (contracts_dir / "blueprint.md").write_text("# Blueprint\nIntent: Build a task app", encoding="utf-8")
    (contracts_dir / "stack.md").write_text("# Stack\nBackend: Python / FastAPI", encoding="utf-8")
    (contracts_dir / "schema.md").write_text("# Schema\nCREATE TABLE users (...)", encoding="utf-8")
    (contracts_dir / "manifesto.md").write_text("# Manifesto\nPrinciple 1: User-first", encoding="utf-8")
    (contracts_dir / "physics.yaml").write_text("routes:\n  - path: /health\n    method: GET", encoding="utf-8")
    (contracts_dir / "boundaries.json").write_text(
        '{"layers": [{"name": "routers"}, {"name": "services"}]}', encoding="utf-8"
    )
    (contracts_dir / "ui.md").write_text("# UI\nApp shell: sidebar + main", encoding="utf-8")
    (contracts_dir / "builder_directive.md").write_text("# Builder Directive\nAEM: active", encoding="utf-8")
    (contracts_dir / "phases.md").write_text(
        "# Phases\n\n"
        "## Phase 0 — Genesis\n\n"
        "**Objective:** Skeleton project\n\n"
        "**Deliverables:**\n- /health endpoint\n- boot.ps1\n\n"
        "**Exit criteria:**\n- pytest passes\n\n---\n\n"
        "## Phase 1 — Auth\n\n"
        "**Objective:** Add authentication\n\n"
        "**Deliverables:**\n- JWT auth\n- Login endpoint\n\n"
        "**Exit criteria:**\n- Auth tests pass\n\n---\n\n"
        "## Phase 2 — Core\n\n"
        "**Objective:** Core features\n\n"
        "**Deliverables:**\n- CRUD endpoints\n\n"
        "**Exit criteria:**\n- All tests pass\n",
        encoding="utf-8",
    )
    return contracts_dir


class TestForgeGetContract:
    """Tests for forge_get_contract tool."""

    def test_reads_markdown_contract(self, tmp_path):
        _make_forge_contracts(tmp_path)
        result = execute_tool("forge_get_contract", {"name": "blueprint"}, str(tmp_path))
        assert "# Blueprint" in result
        assert "task app" in result

    def test_reads_yaml_contract(self, tmp_path):
        _make_forge_contracts(tmp_path)
        result = execute_tool("forge_get_contract", {"name": "physics"}, str(tmp_path))
        assert "/health" in result

    def test_reads_json_contract(self, tmp_path):
        _make_forge_contracts(tmp_path)
        result = execute_tool("forge_get_contract", {"name": "boundaries"}, str(tmp_path))
        assert "routers" in result

    def test_blocks_phases(self, tmp_path):
        _make_forge_contracts(tmp_path)
        result = execute_tool("forge_get_contract", {"name": "phases"}, str(tmp_path))
        assert "Error" in result
        assert "forge_get_phase_window" in result

    def test_missing_contract(self, tmp_path):
        _make_forge_contracts(tmp_path)
        result = execute_tool("forge_get_contract", {"name": "nonexistent"}, str(tmp_path))
        assert "Error" in result
        assert "not found" in result.lower()

    def test_missing_name(self, tmp_path):
        _make_forge_contracts(tmp_path)
        result = execute_tool("forge_get_contract", {}, str(tmp_path))
        assert "Error" in result

    def test_no_contracts_dir(self, tmp_path):
        result = execute_tool("forge_get_contract", {"name": "blueprint"}, str(tmp_path))
        assert "Error" in result


class TestForgeGetPhaseWindow:
    """Tests for forge_get_phase_window tool."""

    def test_phase_zero_returns_current_and_next(self, tmp_path):
        _make_forge_contracts(tmp_path)
        result = execute_tool("forge_get_phase_window", {"phase_number": 0}, str(tmp_path))
        assert "Phase 0" in result
        assert "Genesis" in result
        assert "Phase 1" in result
        assert "Auth" in result

    def test_middle_phase(self, tmp_path):
        _make_forge_contracts(tmp_path)
        result = execute_tool("forge_get_phase_window", {"phase_number": 1}, str(tmp_path))
        assert "Phase 1" in result
        assert "Phase 2" in result

    def test_last_phase(self, tmp_path):
        _make_forge_contracts(tmp_path)
        result = execute_tool("forge_get_phase_window", {"phase_number": 2}, str(tmp_path))
        assert "Phase 2" in result
        assert "final phase" in result.lower() or "Core" in result

    def test_out_of_range(self, tmp_path):
        _make_forge_contracts(tmp_path)
        result = execute_tool("forge_get_phase_window", {"phase_number": 99}, str(tmp_path))
        assert "Error" in result

    def test_missing_phases_file(self, tmp_path):
        # Create contracts dir without phases.md
        (tmp_path / "Forge" / "Contracts").mkdir(parents=True)
        result = execute_tool("forge_get_phase_window", {"phase_number": 0}, str(tmp_path))
        assert "Error" in result

    def test_defaults_to_phase_zero(self, tmp_path):
        _make_forge_contracts(tmp_path)
        result = execute_tool("forge_get_phase_window", {}, str(tmp_path))
        assert "Phase 0" in result


class TestForgeListContracts:
    """Tests for forge_list_contracts tool."""

    def test_lists_all_contracts(self, tmp_path):
        _make_forge_contracts(tmp_path)
        result = execute_tool("forge_list_contracts", {}, str(tmp_path))
        data = json.loads(result)
        assert data["total"] >= 9
        names = [c["name"] for c in data["contracts"]]
        assert "blueprint" in names
        assert "phases" in names

    def test_includes_size(self, tmp_path):
        _make_forge_contracts(tmp_path)
        result = execute_tool("forge_list_contracts", {}, str(tmp_path))
        data = json.loads(result)
        for contract in data["contracts"]:
            assert "size_kb" in contract

    def test_no_contracts_dir(self, tmp_path):
        result = execute_tool("forge_list_contracts", {}, str(tmp_path))
        data = json.loads(result)
        assert data["contracts"] == []
        assert "error" in data


class TestForgeGetSummary:
    """Tests for forge_get_summary tool."""

    def test_returns_summary(self, tmp_path):
        _make_forge_contracts(tmp_path)
        result = execute_tool("forge_get_summary", {}, str(tmp_path))
        data = json.loads(result)
        assert data["framework"] == "Forge Governance"
        assert "blueprint" in data["available_contracts"]
        assert len(data["critical_rules"]) > 0

    def test_extracts_layers_from_boundaries(self, tmp_path):
        _make_forge_contracts(tmp_path)
        result = execute_tool("forge_get_summary", {}, str(tmp_path))
        data = json.loads(result)
        assert "routers" in data["architectural_layers"]
        assert "services" in data["architectural_layers"]

    def test_no_contracts_dir(self, tmp_path):
        result = execute_tool("forge_get_summary", {}, str(tmp_path))
        data = json.loads(result)
        assert data["available_contracts"] == []


class TestForgeScratchpad:
    """Tests for forge_scratchpad tool."""

    def test_write_and_read(self, tmp_path):
        from app.services.tool_executor import _scratchpads
        _scratchpads.pop(str(tmp_path), None)  # clean slate

        execute_tool("forge_scratchpad", {
            "operation": "write", "key": "arch_decisions", "value": "Use FastAPI"
        }, str(tmp_path))

        result = execute_tool("forge_scratchpad", {
            "operation": "read", "key": "arch_decisions"
        }, str(tmp_path))
        assert result == "Use FastAPI"

    def test_append(self, tmp_path):
        from app.services.tool_executor import _scratchpads
        _scratchpads.pop(str(tmp_path), None)

        execute_tool("forge_scratchpad", {
            "operation": "write", "key": "notes", "value": "Phase 0 done. "
        }, str(tmp_path))
        execute_tool("forge_scratchpad", {
            "operation": "append", "key": "notes", "value": "Phase 1 started."
        }, str(tmp_path))

        result = execute_tool("forge_scratchpad", {
            "operation": "read", "key": "notes"
        }, str(tmp_path))
        assert result == "Phase 0 done. Phase 1 started."

    def test_list(self, tmp_path):
        from app.services.tool_executor import _scratchpads
        _scratchpads.pop(str(tmp_path), None)

        execute_tool("forge_scratchpad", {
            "operation": "write", "key": "alpha", "value": "A"
        }, str(tmp_path))
        execute_tool("forge_scratchpad", {
            "operation": "write", "key": "beta", "value": "B"
        }, str(tmp_path))

        result = execute_tool("forge_scratchpad", {"operation": "list"}, str(tmp_path))
        data = json.loads(result)
        assert "alpha" in data["keys"]
        assert "beta" in data["keys"]
        assert data["count"] == 2

    def test_read_missing_key(self, tmp_path):
        from app.services.tool_executor import _scratchpads
        _scratchpads.pop(str(tmp_path), None)

        result = execute_tool("forge_scratchpad", {
            "operation": "read", "key": "nonexistent"
        }, str(tmp_path))
        assert "Error" in result

    def test_persists_to_disk(self, tmp_path):
        from app.services.tool_executor import _scratchpads
        _scratchpads.pop(str(tmp_path), None)

        # Create Forge/ dir (scratchpad persists there)
        (tmp_path / "Forge").mkdir(parents=True, exist_ok=True)
        execute_tool("forge_scratchpad", {
            "operation": "write", "key": "persist_test", "value": "hello"
        }, str(tmp_path))

        # Verify file exists on disk
        scratchpad_file = tmp_path / "Forge" / ".scratchpad.json"
        assert scratchpad_file.exists()
        data = json.loads(scratchpad_file.read_text(encoding="utf-8"))
        assert data["persist_test"] == "hello"

    def test_loads_from_disk_on_first_access(self, tmp_path):
        from app.services.tool_executor import _scratchpads
        _scratchpads.pop(str(tmp_path), None)

        # Pre-populate disk file
        forge_dir = tmp_path / "Forge"
        forge_dir.mkdir(parents=True, exist_ok=True)
        (forge_dir / ".scratchpad.json").write_text(
            '{"preloaded": "from_disk"}', encoding="utf-8"
        )

        result = execute_tool("forge_scratchpad", {
            "operation": "read", "key": "preloaded"
        }, str(tmp_path))
        assert result == "from_disk"

    def test_key_required_for_read_write_append(self, tmp_path):
        from app.services.tool_executor import _scratchpads
        _scratchpads.pop(str(tmp_path), None)

        for op in ["read", "write", "append"]:
            result = execute_tool("forge_scratchpad", {"operation": op}, str(tmp_path))
            assert "Error" in result


class TestForgeToolsViaAsyncDispatch:
    """Verify forge tools are accessible through execute_tool_async."""

    @pytest.mark.asyncio
    async def test_forge_get_contract_async(self, tmp_path):
        _make_forge_contracts(tmp_path)
        result = await execute_tool_async("forge_get_contract", {"name": "stack"}, str(tmp_path))
        assert "FastAPI" in result

    @pytest.mark.asyncio
    async def test_forge_scratchpad_async(self, tmp_path):
        from app.services.tool_executor import _scratchpads
        _scratchpads.pop(str(tmp_path), None)

        result = await execute_tool_async("forge_scratchpad", {
            "operation": "write", "key": "async_test", "value": "works"
        }, str(tmp_path))
        assert "OK" in result


class TestBuilderToolsIncludesForge:
    """Verify BUILDER_TOOLS contains all forge tool definitions."""

    def test_forge_tools_in_builder_tools(self):
        tool_names = {t["name"] for t in BUILDER_TOOLS}
        assert "forge_get_contract" in tool_names
        assert "forge_get_phase_window" in tool_names
        assert "forge_list_contracts" in tool_names
        assert "forge_get_summary" in tool_names
        assert "forge_scratchpad" in tool_names

    def test_total_tool_count(self):
        # 8 existing + 5 forge = 13
        assert len(BUILDER_TOOLS) == 13


# ---------------------------------------------------------------------------
# _build_project_env — venv activation + .env loading
# ---------------------------------------------------------------------------


class TestBuildProjectEnv:
    """Tests for the _build_project_env helper that builds subprocess env dicts."""

    def test_basic_path_propagated(self, tmp_path):
        env = _build_project_env(str(tmp_path))
        assert "PATH" in env

    def test_activates_venv_when_present(self, tmp_path):
        venv_dir = tmp_path / ".venv"
        if os.name == "nt":
            (venv_dir / "Scripts").mkdir(parents=True)
        else:
            (venv_dir / "bin").mkdir(parents=True)

        env = _build_project_env(str(tmp_path))
        assert env.get("VIRTUAL_ENV") == str(venv_dir)
        assert str(venv_dir) in env["PATH"]

    def test_loads_dotenv_variables(self, tmp_path):
        dotenv = tmp_path / ".env"
        dotenv.write_text(
            "DATABASE_URL=sqlite:///test.db\n"
            "SECRET_KEY=mysecret\n"
            "# Comment line\n"
            "\n"
            'QUOTED_VAR="hello world"\n',
            encoding="utf-8",
        )
        env = _build_project_env(str(tmp_path))
        assert env["DATABASE_URL"] == "sqlite:///test.db"
        assert env["SECRET_KEY"] == "mysecret"
        assert env["QUOTED_VAR"] == "hello world"

    def test_no_dotenv_no_error(self, tmp_path):
        """No .env file → no crash, just base env."""
        env = _build_project_env(str(tmp_path))
        assert "PATH" in env

    def test_no_venv_falls_back(self, tmp_path):
        """No .venv dir → falls back to host VIRTUAL_ENV if set."""
        env = _build_project_env(str(tmp_path))
        # Should not have VIRTUAL_ENV set to this project's path
        venv_val = env.get("VIRTUAL_ENV", "")
        assert str(tmp_path / ".venv") not in venv_val


# ---------------------------------------------------------------------------
# _auto_install_deps — post-write dependency auto-install
# ---------------------------------------------------------------------------


class TestAutoInstallDeps:
    """Tests for the auto-install hook triggered by write_file."""

    @pytest.mark.asyncio
    async def test_non_manifest_file_no_install(self, tmp_path):
        """Writing a regular file does not trigger auto-install."""
        result = await _auto_install_deps("app/main.py", str(tmp_path))
        assert result == ""

    @pytest.mark.asyncio
    async def test_requirements_txt_no_venv_skips(self, tmp_path):
        """requirements.txt without .venv → skip message."""
        (tmp_path / "requirements.txt").write_text("flask\n", encoding="utf-8")
        result = await _auto_install_deps("requirements.txt", str(tmp_path))
        assert "skipped" in result.lower()

    @pytest.mark.asyncio
    async def test_requirements_txt_with_venv_runs(self, tmp_path):
        """requirements.txt with .venv → runs pip install."""
        venv_dir = tmp_path / ".venv"
        if os.name == "nt":
            scripts = venv_dir / "Scripts"
            scripts.mkdir(parents=True)
            # Create a dummy pip that succeeds
            (scripts / "pip.exe").write_text("", encoding="utf-8")
        else:
            bin_dir = venv_dir / "bin"
            bin_dir.mkdir(parents=True)
            (bin_dir / "pip").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")

        (tmp_path / "requirements.txt").write_text("flask\n", encoding="utf-8")
        result = await _auto_install_deps("requirements.txt", str(tmp_path))
        # Should attempt the install (may fail since pip is a dummy, but it tried)
        assert result != ""

    @pytest.mark.asyncio
    async def test_package_json_triggers_npm(self, tmp_path):
        """package.json triggers npm install attempt."""
        (tmp_path / "package.json").write_text(
            '{"name":"test","version":"1.0.0"}', encoding="utf-8"
        )
        result = await _auto_install_deps("package.json", str(tmp_path))
        # npm may not be installed, but the hook should still return a message
        assert result != ""

    @pytest.mark.asyncio
    async def test_nested_path_uses_filename(self, tmp_path):
        """A nested path like 'backend/requirements.txt' matches on filename."""
        result = await _auto_install_deps("backend/requirements.txt", str(tmp_path))
        # No .venv → skip
        assert "skipped" in result.lower()

    @pytest.mark.asyncio
    async def test_disabled_via_config(self, tmp_path, monkeypatch):
        """AUTO_INSTALL_DEPS=False disables the hook."""
        from app.config import settings
        monkeypatch.setattr(settings, "AUTO_INSTALL_DEPS", False)
        result = await _auto_install_deps("requirements.txt", str(tmp_path))
        assert result == ""

    def test_dependency_manifests_dict(self):
        """Ensure all expected manifests are registered."""
        assert "requirements.txt" in _DEPENDENCY_MANIFESTS
        assert "package.json" in _DEPENDENCY_MANIFESTS
        assert "pyproject.toml" in _DEPENDENCY_MANIFESTS


# ---------------------------------------------------------------------------
# execute_tool_async — post-write hook integration
# ---------------------------------------------------------------------------


class TestWriteFileAutoInstallIntegration:
    """Verify that write_file → auto-install hook fires via execute_tool_async."""

    @pytest.mark.asyncio
    async def test_write_regular_file_no_install_msg(self, tmp_path):
        """Writing a normal file doesn't append install messages."""
        result = await execute_tool_async(
            "write_file",
            {"path": "app/main.py", "content": "print('hi')"},
            str(tmp_path),
        )
        assert result.startswith("OK:")
        assert "Auto-install" not in result

    @pytest.mark.asyncio
    async def test_write_requirements_txt_triggers_hook(self, tmp_path):
        """Writing requirements.txt appends an install status message."""
        result = await execute_tool_async(
            "write_file",
            {"path": "requirements.txt", "content": "flask\nrequests\n"},
            str(tmp_path),
        )
        assert result.startswith("OK:")
        # Should have the auto-install message appended (skip since no .venv)
        assert "skipped" in result.lower() or "auto-install" in result.lower()


# ---------------------------------------------------------------------------
# Expanded .gitignore rules
# ---------------------------------------------------------------------------


class TestExpandedGitignoreRules:
    """Verify the expanded gitignore rules include env/build artifact entries."""

    def test_venv_in_rules(self):
        from app.services.build.context import _FORGE_GITIGNORE_RULES
        joined = "\n".join(_FORGE_GITIGNORE_RULES)
        assert ".venv/" in joined

    def test_env_in_rules(self):
        from app.services.build.context import _FORGE_GITIGNORE_RULES
        joined = "\n".join(_FORGE_GITIGNORE_RULES)
        assert ".env" in joined

    def test_node_modules_in_rules(self):
        from app.services.build.context import _FORGE_GITIGNORE_RULES
        joined = "\n".join(_FORGE_GITIGNORE_RULES)
        assert "node_modules/" in joined

    def test_pycache_in_rules(self):
        from app.services.build.context import _FORGE_GITIGNORE_RULES
        joined = "\n".join(_FORGE_GITIGNORE_RULES)
        assert "__pycache__/" in joined
