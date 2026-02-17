"""Tests for forge_ide.contracts — Pydantic models for tool requests/responses."""

import pytest
from pydantic import ValidationError

from forge_ide.contracts import (
    CheckSyntaxRequest,
    Diagnostic,
    LineRange,
    ListDirectoryRequest,
    ReadFileRequest,
    RunCommandRequest,
    RunTestsRequest,
    SearchCodeRequest,
    Snippet,
    ToolRequest,
    ToolResponse,
    UnifiedDiff,
    WriteFileRequest,
)


# ---------------------------------------------------------------------------
# Shared sub-models
# ---------------------------------------------------------------------------


class TestLineRange:
    def test_valid(self):
        lr = LineRange(start=1, end=10)
        assert lr.start == 1
        assert lr.end == 10

    def test_single_line(self):
        lr = LineRange(start=5, end=5)
        assert lr.start == lr.end == 5

    def test_zero_start_rejected(self):
        with pytest.raises(ValidationError):
            LineRange(start=0, end=10)

    def test_negative_rejected(self):
        with pytest.raises(ValidationError):
            LineRange(start=-1, end=10)

    def test_frozen(self):
        lr = LineRange(start=1, end=10)
        with pytest.raises(ValidationError):
            lr.start = 5


class TestSnippet:
    def test_valid(self):
        s = Snippet(path="app/main.py", start_line=1, end_line=3, content="abc")
        assert s.path == "app/main.py"
        assert s.content == "abc"

    def test_empty_content(self):
        s = Snippet(path="f.py", start_line=1, end_line=1, content="")
        assert s.content == ""

    def test_zero_line_rejected(self):
        with pytest.raises(ValidationError):
            Snippet(path="f.py", start_line=0, end_line=1, content="x")

    def test_frozen(self):
        s = Snippet(path="f.py", start_line=1, end_line=1, content="x")
        with pytest.raises(ValidationError):
            s.path = "other.py"


class TestDiagnostic:
    def test_valid_error(self):
        d = Diagnostic(
            file="app/main.py",
            line=10,
            column=5,
            message="undefined name 'foo'",
            severity="error",
        )
        assert d.severity == "error"
        assert d.code is None

    def test_with_code(self):
        d = Diagnostic(
            file="a.py", line=1, column=0, message="m", severity="warning", code="W001"
        )
        assert d.code == "W001"

    def test_all_severities(self):
        for sev in ("error", "warning", "info", "hint"):
            d = Diagnostic(file="f", line=1, column=0, message="m", severity=sev)
            assert d.severity == sev

    def test_invalid_severity(self):
        with pytest.raises(ValidationError):
            Diagnostic(
                file="f", line=1, column=0, message="m", severity="critical"
            )

    def test_frozen(self):
        d = Diagnostic(file="f", line=1, column=0, message="m", severity="error")
        with pytest.raises(ValidationError):
            d.message = "changed"


class TestUnifiedDiff:
    def test_valid(self):
        ud = UnifiedDiff(
            path="app/main.py", hunks=["@@ -1,3 +1,4 @@"], insertions=1, deletions=0
        )
        assert ud.path == "app/main.py"
        assert len(ud.hunks) == 1

    def test_zero_changes(self):
        ud = UnifiedDiff(path="f.py", hunks=[], insertions=0, deletions=0)
        assert ud.insertions == 0
        assert ud.deletions == 0

    def test_negative_insertions_rejected(self):
        with pytest.raises(ValidationError):
            UnifiedDiff(path="f", hunks=[], insertions=-1, deletions=0)

    def test_frozen(self):
        ud = UnifiedDiff(path="f", hunks=[], insertions=0, deletions=0)
        with pytest.raises(ValidationError):
            ud.path = "other"


# ---------------------------------------------------------------------------
# Core request / response
# ---------------------------------------------------------------------------


class TestToolRequest:
    def test_valid(self):
        tr = ToolRequest(name="read_file", params={"path": "foo.py"})
        assert tr.name == "read_file"
        assert tr.params == {"path": "foo.py"}

    def test_default_params(self):
        tr = ToolRequest(name="list_directory")
        assert tr.params == {}

    def test_missing_name(self):
        with pytest.raises(ValidationError):
            ToolRequest(params={"x": 1})

    def test_frozen(self):
        tr = ToolRequest(name="x")
        with pytest.raises(ValidationError):
            tr.name = "y"


class TestToolResponse:
    def test_ok_factory(self):
        r = ToolResponse.ok({"key": "val"}, duration_ms=42)
        assert r.success is True
        assert r.data == {"key": "val"}
        assert r.error is None
        assert r.duration_ms == 42

    def test_fail_factory(self):
        r = ToolResponse.fail("something broke", duration_ms=7)
        assert r.success is False
        assert r.data == {}
        assert r.error == "something broke"
        assert r.duration_ms == 7

    def test_ok_default_duration(self):
        r = ToolResponse.ok({"a": 1})
        assert r.duration_ms == 0

    def test_fail_default_duration(self):
        r = ToolResponse.fail("err")
        assert r.duration_ms == 0

    def test_frozen(self):
        r = ToolResponse.ok({"x": 1})
        with pytest.raises(ValidationError):
            r.success = False

    def test_ok_empty_data(self):
        r = ToolResponse.ok({})
        assert r.success is True
        assert r.data == {}

    def test_serialise_roundtrip(self):
        r = ToolResponse.ok({"path": "f.py", "content": "hello"}, duration_ms=10)
        d = r.model_dump()
        r2 = ToolResponse.model_validate(d)
        assert r2 == r


# ---------------------------------------------------------------------------
# Per-tool request models
# ---------------------------------------------------------------------------


class TestReadFileRequest:
    def test_valid(self):
        r = ReadFileRequest(path="app/main.py")
        assert r.path == "app/main.py"

    def test_empty_path_rejected(self):
        with pytest.raises(ValidationError):
            ReadFileRequest(path="")

    def test_missing_path(self):
        with pytest.raises(ValidationError):
            ReadFileRequest()


class TestListDirectoryRequest:
    def test_valid(self):
        r = ListDirectoryRequest(path="app/")
        assert r.path == "app/"

    def test_default_dot(self):
        r = ListDirectoryRequest()
        assert r.path == "."

    def test_empty_rejected(self):
        with pytest.raises(ValidationError):
            ListDirectoryRequest(path="")


class TestSearchCodeRequest:
    def test_valid(self):
        r = SearchCodeRequest(pattern="import os")
        assert r.pattern == "import os"
        assert r.glob is None

    def test_with_glob(self):
        r = SearchCodeRequest(pattern="def ", glob="*.py")
        assert r.glob == "*.py"

    def test_empty_pattern_rejected(self):
        with pytest.raises(ValidationError):
            SearchCodeRequest(pattern="")


class TestWriteFileRequest:
    def test_valid(self):
        r = WriteFileRequest(path="app/new.py", content="print('hello')")
        assert r.path == "app/new.py"
        assert r.content == "print('hello')"

    def test_empty_path_rejected(self):
        with pytest.raises(ValidationError):
            WriteFileRequest(path="", content="x")

    def test_empty_content_allowed(self):
        # Empty files are valid (e.g. __init__.py)
        r = WriteFileRequest(path="pkg/__init__.py", content="")
        assert r.content == ""


class TestRunTestsRequest:
    def test_valid(self):
        r = RunTestsRequest(command="pytest tests/ -v")
        assert r.command == "pytest tests/ -v"
        assert r.timeout == 120

    def test_custom_timeout(self):
        r = RunTestsRequest(command="pytest", timeout=60)
        assert r.timeout == 60

    def test_empty_command_rejected(self):
        with pytest.raises(ValidationError):
            RunTestsRequest(command="")

    def test_timeout_too_high_rejected(self):
        with pytest.raises(ValidationError):
            RunTestsRequest(command="pytest", timeout=9999)

    def test_timeout_zero_rejected(self):
        with pytest.raises(ValidationError):
            RunTestsRequest(command="pytest", timeout=0)


class TestCheckSyntaxRequest:
    def test_valid(self):
        r = CheckSyntaxRequest(file_path="app/main.py")
        assert r.file_path == "app/main.py"

    def test_empty_rejected(self):
        with pytest.raises(ValidationError):
            CheckSyntaxRequest(file_path="")


class TestRunCommandRequest:
    def test_valid(self):
        r = RunCommandRequest(command="pip install -r requirements.txt")
        assert r.timeout == 60

    def test_custom_timeout(self):
        r = RunCommandRequest(command="pip install foo", timeout=30)
        assert r.timeout == 30

    def test_empty_command_rejected(self):
        with pytest.raises(ValidationError):
            RunCommandRequest(command="")

    def test_timeout_boundary(self):
        r = RunCommandRequest(command="ls", timeout=300)
        assert r.timeout == 300
        with pytest.raises(ValidationError):
            RunCommandRequest(command="ls", timeout=301)
