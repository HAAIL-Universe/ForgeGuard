"""Tests for forge_ide.errors — the IDE error hierarchy."""

import pytest

from forge_ide.errors import (
    IDEError,
    PatchConflict,
    ParseError,
    SandboxViolation,
    ToolNotFound,
    ToolTimeout,
)


# ---------------------------------------------------------------------------
# IDEError (base)
# ---------------------------------------------------------------------------


class TestIDEError:
    def test_basic_construction(self):
        err = IDEError("something broke")
        assert str(err) == "something broke"
        assert err.message == "something broke"
        assert err.detail == {}

    def test_with_detail(self):
        err = IDEError("bad", detail={"key": "val"})
        assert err.detail == {"key": "val"}

    def test_to_dict(self):
        err = IDEError("fail", detail={"x": 1})
        d = err.to_dict()
        assert d["error"] == "IDEError"
        assert d["message"] == "fail"
        assert d["x"] == 1

    def test_is_exception(self):
        with pytest.raises(IDEError):
            raise IDEError("boom")


# ---------------------------------------------------------------------------
# SandboxViolation
# ---------------------------------------------------------------------------


class TestSandboxViolation:
    def test_construction(self):
        err = SandboxViolation("../etc/passwd", "/workspace/../etc/passwd")
        assert err.path == "../etc/passwd"
        assert err.attempted_path == "/workspace/../etc/passwd"
        assert "Sandbox violation" in str(err)

    def test_to_dict(self):
        err = SandboxViolation("bad/path", "/resolved")
        d = err.to_dict()
        assert d["error"] == "SandboxViolation"
        assert d["path"] == "bad/path"
        assert d["attempted_path"] == "/resolved"

    def test_is_ide_error(self):
        assert issubclass(SandboxViolation, IDEError)


# ---------------------------------------------------------------------------
# ToolTimeout
# ---------------------------------------------------------------------------


class TestToolTimeout:
    def test_construction(self):
        err = ToolTimeout("run_tests", 30000)
        assert err.tool_name == "run_tests"
        assert err.timeout_ms == 30000
        assert "timed out" in str(err)
        assert "30000ms" in str(err)

    def test_to_dict(self):
        err = ToolTimeout("check_syntax", 5000)
        d = err.to_dict()
        assert d["error"] == "ToolTimeout"
        assert d["tool_name"] == "check_syntax"
        assert d["timeout_ms"] == 5000

    def test_is_ide_error(self):
        assert issubclass(ToolTimeout, IDEError)


# ---------------------------------------------------------------------------
# ParseError
# ---------------------------------------------------------------------------


class TestParseError:
    def test_construction(self):
        err = ParseError("raw junk output", "pytest_parser")
        assert err.raw_output == "raw junk output"
        assert err.parser_name == "pytest_parser"
        assert "pytest_parser" in str(err)
        assert "15 chars" in str(err)

    def test_to_dict(self):
        err = ParseError("abc", "json_parser")
        d = err.to_dict()
        assert d["error"] == "ParseError"
        assert d["parser_name"] == "json_parser"
        assert d["raw_output_length"] == 3

    def test_is_ide_error(self):
        assert issubclass(ParseError, IDEError)


# ---------------------------------------------------------------------------
# PatchConflict
# ---------------------------------------------------------------------------


class TestPatchConflict:
    def test_construction(self):
        err = PatchConflict("app/main.py", 2, "expected line", "actual line")
        assert err.file_path == "app/main.py"
        assert err.hunk_index == 2
        assert err.expected == "expected line"
        assert err.actual == "actual line"
        assert "hunk 2" in str(err)

    def test_to_dict(self):
        err = PatchConflict("foo.py", 0, "e", "a")
        d = err.to_dict()
        assert d["error"] == "PatchConflict"
        assert d["file_path"] == "foo.py"
        assert d["hunk_index"] == 0
        assert d["expected"] == "e"
        assert d["actual"] == "a"

    def test_is_ide_error(self):
        assert issubclass(PatchConflict, IDEError)


# ---------------------------------------------------------------------------
# ToolNotFound
# ---------------------------------------------------------------------------


class TestToolNotFound:
    def test_construction(self):
        err = ToolNotFound("magic_tool", ["read_file", "write_file"])
        assert err.tool_name == "magic_tool"
        assert err.available_tools == ["read_file", "write_file"]
        assert "magic_tool" in str(err)
        assert "read_file" in str(err)

    def test_to_dict(self):
        err = ToolNotFound("x", ["a", "b"])
        d = err.to_dict()
        assert d["error"] == "ToolNotFound"
        assert d["tool_name"] == "x"
        assert d["available_tools"] == ["a", "b"]

    def test_is_ide_error(self):
        assert issubclass(ToolNotFound, IDEError)

    def test_empty_available(self):
        err = ToolNotFound("x", [])
        assert err.available_tools == []
        assert "Available:" in str(err)
