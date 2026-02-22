"""Tests for planner/tools.py — sandboxed tool implementations.

Covers:
  - _is_allowed: sandbox enforcement
  - tool_read_file: success, not found, is dir, outside sandbox, truncation
  - tool_list_directory: success, not found, is file, outside sandbox
  - tool_get_contract: known/unknown contract names
  - tool_write_plan: missing args, schema failure, metadata stripping, success
  - dispatch_tool: routing, unknown tool, contract_fetcher callback
"""

import json
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock
import pytest

# planner/ uses bare imports — add it to sys.path
_PLANNER_DIR = Path(__file__).resolve().parent.parent / "planner"
if str(_PLANNER_DIR) not in sys.path:
    sys.path.insert(0, str(_PLANNER_DIR))

import tools as planner_tools  # noqa: E402
from tools import (  # noqa: E402
    _is_allowed,
    tool_read_file,
    tool_list_directory,
    tool_get_contract,
    tool_write_plan,
    dispatch_tool,
    ALLOWED_ROOT,
    LARGE_FILE_THRESHOLD,
    CONTRACT_REGISTRY,
)


# ---------------------------------------------------------------------------
# Minimal valid plan for write_plan tests
# ---------------------------------------------------------------------------

def _valid_plan_dict() -> dict:
    return {
        "summary": {
            "one_liner": "A FastAPI task API.",
            "mode": "greenfield",
            "stack_rationale": "FastAPI + PostgreSQL.",
            "key_constraints": ["No direct DB access from routers"],
            "existing_contracts": ["builder_contract.md"],
            "missing_contracts": [],
            "boot_script_required": True,
        },
        "stack": {
            "backend_language": "python",
            "backend_framework": "fastapi",
            "database": "postgresql",
            "test_framework": "pytest",
            "boot_script": True,
        },
        "phases": [
            {
                "number": 0,
                "name": "Genesis",
                "purpose": "Bootstrap project.",
                "file_manifest": [
                    {
                        "path": "app/main.py",
                        "layer": "router",
                        "action": "create",
                        "description": "FastAPI entry point.",
                    }
                ],
                "acceptance_criteria": [
                    {
                        "id": "AC-0-1",
                        "description": "App starts without errors.",
                        "test_hint": "tests/test_health.py",
                    }
                ],
            }
        ],
        "required_contracts": ["builder_contract.md"],
        "contract_refs": [
            {
                "contract": "builder_contract.md",
                "section": "§1 Read gate",
                "note": "Builder must read all contracts before writing any file.",
            }
        ],
    }


# ---------------------------------------------------------------------------
# _is_allowed
# ---------------------------------------------------------------------------

class TestIsAllowed:
    def test_path_inside_allowed_root(self, tmp_path):
        # Use a subpath of ALLOWED_ROOT
        inside = ALLOWED_ROOT / "some_subdir" / "file.txt"
        assert _is_allowed(inside) is True

    def test_path_is_allowed_root_itself(self):
        assert _is_allowed(ALLOWED_ROOT) is True

    def test_path_outside_allowed_root(self, tmp_path):
        # tmp_path is outside z:/ForgeCollection on this machine
        outside = Path("C:/Windows/System32/some_file.txt").resolve()
        # Only assert False if it's actually outside
        if not str(outside).startswith(str(ALLOWED_ROOT)):
            assert _is_allowed(outside) is False

    def test_traversal_attempt_blocked(self):
        # Attempt to escape via ../..
        escape = ALLOWED_ROOT / "subdir" / ".." / ".." / ".." / "etc" / "passwd"
        assert _is_allowed(escape) is False


# ---------------------------------------------------------------------------
# tool_read_file
# ---------------------------------------------------------------------------

class TestToolReadFile:
    def test_reads_file_successfully(self, tmp_path, monkeypatch):
        # Redirect ALLOWED_ROOT to tmp_path
        monkeypatch.setattr(planner_tools, "ALLOWED_ROOT", tmp_path.resolve())
        test_file = tmp_path / "hello.txt"
        test_file.write_text("hello world", encoding="utf-8")

        result = tool_read_file(str(test_file))

        assert "error" not in result
        assert result["content"] == "hello world"
        assert result["size_chars"] == len("hello world")
        assert result["truncated"] is False
        assert result["truncated_at"] is None

    def test_file_not_found(self, tmp_path, monkeypatch):
        monkeypatch.setattr(planner_tools, "ALLOWED_ROOT", tmp_path.resolve())
        result = tool_read_file(str(tmp_path / "nonexistent.txt"))
        assert "error" in result
        assert "not found" in result["error"].lower()

    def test_directory_rejected(self, tmp_path, monkeypatch):
        monkeypatch.setattr(planner_tools, "ALLOWED_ROOT", tmp_path.resolve())
        subdir = tmp_path / "subdir"
        subdir.mkdir()
        result = tool_read_file(str(subdir))
        assert "error" in result
        assert "directory" in result["error"].lower()

    def test_outside_sandbox_rejected(self, tmp_path, monkeypatch):
        monkeypatch.setattr(planner_tools, "ALLOWED_ROOT", tmp_path.resolve())
        outside = Path("C:/Windows/win.ini")
        result = tool_read_file(str(outside))
        assert "error" in result
        assert "access denied" in result["error"].lower()

    def test_large_file_truncated(self, tmp_path, monkeypatch):
        monkeypatch.setattr(planner_tools, "ALLOWED_ROOT", tmp_path.resolve())
        big_file = tmp_path / "big.txt"
        content = "x" * (LARGE_FILE_THRESHOLD + 100)
        big_file.write_text(content, encoding="utf-8")

        result = tool_read_file(str(big_file))

        assert result["truncated"] is True
        assert len(result["content"]) == LARGE_FILE_THRESHOLD
        assert result["size_chars"] == len(content)
        assert result["truncated_at"] == LARGE_FILE_THRESHOLD

    def test_small_file_not_truncated(self, tmp_path, monkeypatch):
        monkeypatch.setattr(planner_tools, "ALLOWED_ROOT", tmp_path.resolve())
        small_file = tmp_path / "small.txt"
        content = "a" * (LARGE_FILE_THRESHOLD - 1)
        small_file.write_text(content, encoding="utf-8")

        result = tool_read_file(str(small_file))

        assert result["truncated"] is False
        assert result["content"] == content


# ---------------------------------------------------------------------------
# tool_list_directory
# ---------------------------------------------------------------------------

class TestToolListDirectory:
    def test_lists_directory_successfully(self, tmp_path, monkeypatch):
        monkeypatch.setattr(planner_tools, "ALLOWED_ROOT", tmp_path.resolve())
        (tmp_path / "file_a.txt").write_text("a")
        (tmp_path / "file_b.py").write_text("b")
        (tmp_path / "subdir").mkdir()

        result = tool_list_directory(str(tmp_path))

        assert "error" not in result
        assert "subdir" in result["directories"]
        assert "file_a.txt" in result["files"]
        assert "file_b.py" in result["files"]

    def test_directories_come_before_files(self, tmp_path, monkeypatch):
        monkeypatch.setattr(planner_tools, "ALLOWED_ROOT", tmp_path.resolve())
        (tmp_path / "z_file.txt").write_text("z")
        (tmp_path / "a_dir").mkdir()

        result = tool_list_directory(str(tmp_path))

        assert "a_dir" in result["directories"]
        assert "z_file.txt" in result["files"]

    def test_directory_not_found(self, tmp_path, monkeypatch):
        monkeypatch.setattr(planner_tools, "ALLOWED_ROOT", tmp_path.resolve())
        result = tool_list_directory(str(tmp_path / "nonexistent"))
        assert "error" in result
        assert "not found" in result["error"].lower()

    def test_file_rejected(self, tmp_path, monkeypatch):
        monkeypatch.setattr(planner_tools, "ALLOWED_ROOT", tmp_path.resolve())
        f = tmp_path / "file.txt"
        f.write_text("x")
        result = tool_list_directory(str(f))
        assert "error" in result
        assert "not a directory" in result["error"].lower()

    def test_outside_sandbox_rejected(self, tmp_path, monkeypatch):
        monkeypatch.setattr(planner_tools, "ALLOWED_ROOT", tmp_path.resolve())
        result = tool_list_directory("C:/Windows")
        assert "error" in result
        assert "access denied" in result["error"].lower()


# ---------------------------------------------------------------------------
# tool_get_contract
# ---------------------------------------------------------------------------

class TestToolGetContract:
    def test_unknown_contract_returns_error(self):
        result = tool_get_contract("totally_unknown_contract")
        assert "error" in result
        assert "unknown contract" in result["error"].lower()

    def test_error_lists_available_contracts(self):
        result = tool_get_contract("not_real")
        assert "error" in result
        # Should list available names
        for known in ("builder_contract", "manifesto", "blueprint"):
            assert known in result["error"]

    def test_known_contract_name_exists_in_registry(self):
        # Every key in CONTRACT_REGISTRY should be a valid name
        for name in CONTRACT_REGISTRY:
            assert isinstance(name, str)
            assert len(name) > 0

    def test_known_contract_delegates_to_read_file(self, monkeypatch, tmp_path):
        # Patch tool_read_file to avoid filesystem dependency
        mock_read = MagicMock(return_value={"content": "contract content", "size_chars": 16})
        monkeypatch.setattr(planner_tools, "tool_read_file", mock_read)
        monkeypatch.setattr(planner_tools, "CONTRACTS_DIR", tmp_path)

        result = tool_get_contract("builder_contract")

        mock_read.assert_called_once()
        assert result["content"] == "contract content"


# ---------------------------------------------------------------------------
# tool_write_plan
# ---------------------------------------------------------------------------

class TestToolWritePlan:
    def test_missing_plan_json_returns_error(self):
        result = tool_write_plan(plan_json=None, project_name="test")
        assert result["success"] is False
        assert "plan_json" in result["errors"][0]

    def test_empty_plan_json_returns_error(self):
        result = tool_write_plan(plan_json={}, project_name="test")
        assert result["success"] is False

    def test_missing_project_name_returns_error(self):
        result = tool_write_plan(plan_json=_valid_plan_dict(), project_name=None)
        assert result["success"] is False
        assert "project_name" in result["errors"][0]

    def test_empty_project_name_returns_error(self):
        result = tool_write_plan(plan_json=_valid_plan_dict(), project_name="")
        assert result["success"] is False

    def test_schema_validation_failure_returns_errors(self):
        bad_plan = {"summary": "not a dict", "stack": {}, "phases": []}
        result = tool_write_plan(plan_json=bad_plan, project_name="test-proj")
        assert result["success"] is False
        assert len(result["errors"]) > 0
        assert "Fix all errors" in result["message"]

    def test_metadata_stripped_before_validation(self, tmp_path, monkeypatch):
        monkeypatch.setattr(planner_tools, "PLAN_OUTPUT_DIR", tmp_path)
        plan = _valid_plan_dict()
        plan["metadata"] = {"schema_version": "old", "injected": True}

        result = tool_write_plan(plan_json=plan, project_name="test-proj")

        # metadata key should be stripped then re-stamped by tool_write_plan
        assert result["success"] is True
        written = json.loads(Path(result["path"]).read_text(encoding="utf-8"))
        assert written["metadata"]["schema_version"] == planner_tools.SCHEMA_VERSION

    def test_success_writes_file_and_returns_path(self, tmp_path, monkeypatch):
        monkeypatch.setattr(planner_tools, "PLAN_OUTPUT_DIR", tmp_path)
        plan = _valid_plan_dict()

        result = tool_write_plan(plan_json=plan, project_name="my-project")

        assert result["success"] is True
        assert "path" in result
        output_file = Path(result["path"])
        assert output_file.exists()

    def test_success_stamps_metadata(self, tmp_path, monkeypatch):
        monkeypatch.setattr(planner_tools, "PLAN_OUTPUT_DIR", tmp_path)
        plan = _valid_plan_dict()

        result = tool_write_plan(plan_json=plan, project_name="meta-test")

        written = json.loads(Path(result["path"]).read_text(encoding="utf-8"))
        assert "metadata" in written
        assert written["metadata"]["schema_version"] == planner_tools.SCHEMA_VERSION
        assert written["metadata"]["project_name"] == "meta-test"
        assert "generated_at" in written["metadata"]

    def test_filename_slugified(self, tmp_path, monkeypatch):
        monkeypatch.setattr(planner_tools, "PLAN_OUTPUT_DIR", tmp_path)
        plan = _valid_plan_dict()

        result = tool_write_plan(plan_json=plan, project_name="My Project")

        assert "my-project" in Path(result["path"]).name

    def test_extra_kwargs_ignored(self, tmp_path, monkeypatch):
        monkeypatch.setattr(planner_tools, "PLAN_OUTPUT_DIR", tmp_path)
        plan = _valid_plan_dict()

        # Should not raise — **_ignored absorbs unknown keys
        result = tool_write_plan(plan_json=plan, project_name="test", unknown_arg="ignored")
        assert result["success"] is True


# ---------------------------------------------------------------------------
# dispatch_tool
# ---------------------------------------------------------------------------

class TestDispatchTool:
    def test_unknown_tool_returns_error(self):
        result = dispatch_tool("nonexistent_tool", {})
        assert "error" in result
        assert "unknown tool" in result["error"].lower()

    def test_routes_write_plan(self, tmp_path, monkeypatch):
        monkeypatch.setattr(planner_tools, "PLAN_OUTPUT_DIR", tmp_path)
        result = dispatch_tool("write_plan", {
            "plan_json": _valid_plan_dict(),
            "project_name": "dispatch-test",
        })
        assert result["success"] is True

    def test_routes_read_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr(planner_tools, "ALLOWED_ROOT", tmp_path.resolve())
        f = tmp_path / "test.txt"
        f.write_text("hello")
        result = dispatch_tool("read_file", {"path": str(f)})
        assert result["content"] == "hello"

    def test_routes_list_directory(self, tmp_path, monkeypatch):
        monkeypatch.setattr(planner_tools, "ALLOWED_ROOT", tmp_path.resolve())
        result = dispatch_tool("list_directory", {"path": str(tmp_path)})
        assert "files" in result

    def test_forge_get_project_contract_uses_fetcher(self):
        fetcher = MagicMock(return_value="# Blueprint content")
        result = dispatch_tool(
            "forge_get_project_contract",
            {"contract_type": "blueprint"},
            contract_fetcher=fetcher,
        )
        fetcher.assert_called_once_with("blueprint")
        assert result["content"] == "# Blueprint content"
        assert result["contract_type"] == "blueprint"

    def test_forge_get_project_contract_missing_returns_error(self):
        fetcher = MagicMock(return_value=None)
        result = dispatch_tool(
            "forge_get_project_contract",
            {"contract_type": "blueprint"},
            contract_fetcher=fetcher,
        )
        assert "error" in result
        assert "not found" in result["error"]

    def test_forge_get_project_contract_without_fetcher_falls_back(self, monkeypatch):
        mock_get = MagicMock(return_value={"content": "fallback"})
        monkeypatch.setattr(planner_tools, "tool_get_contract", mock_get)
        result = dispatch_tool(
            "forge_get_project_contract",
            {"contract_type": "builder_contract"},
        )
        mock_get.assert_called_once_with("builder_contract")
