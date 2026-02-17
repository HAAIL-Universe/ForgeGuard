"""Tests for forge_ide.sanitiser — sorting, noise filtering, path normalisation."""

from __future__ import annotations

import pytest

from forge_ide.contracts import Diagnostic
from forge_ide.lang import Symbol
from forge_ide.searcher import Match
from forge_ide.sanitiser import (
    normalise_path,
    normalise_paths,
    sanitise_output,
    sort_diagnostics,
    sort_file_list,
    sort_matches,
    sort_symbols,
    strip_pids,
    strip_timestamps,
    strip_tmpdir,
)


# ── normalise_path ──────────────────────────────────────────────────────

class TestNormalisePath:
    """Backslash → forward-slash normalisation."""

    def test_backslash_to_forward(self) -> None:
        assert normalise_path(r"src\utils\helper.py") == "src/utils/helper.py"

    def test_forward_slash_unchanged(self) -> None:
        assert normalise_path("src/utils/helper.py") == "src/utils/helper.py"

    def test_empty_string(self) -> None:
        assert normalise_path("") == ""

    def test_mixed_slashes(self) -> None:
        assert normalise_path(r"src/utils\helper.py") == "src/utils/helper.py"


# ── sort_file_list ──────────────────────────────────────────────────────

class TestSortFileList:
    """Alphabetical, case-insensitive file sorting."""

    def test_alphabetical(self) -> None:
        paths = ["zoo.py", "alpha.py", "mid.py"]
        assert sort_file_list(paths) == ["alpha.py", "mid.py", "zoo.py"]

    def test_case_insensitive(self) -> None:
        paths = ["Zebra.py", "apple.py", "Banana.py"]
        result = sort_file_list(paths)
        assert result == ["apple.py", "Banana.py", "Zebra.py"]

    def test_backslash_normalised(self) -> None:
        paths = [r"b\file.py", "a/file.py"]
        result = sort_file_list(paths)
        assert result[0] == "a/file.py"
        assert result[1] == r"b\file.py"

    def test_empty_list(self) -> None:
        assert sort_file_list([]) == []

    def test_single_item(self) -> None:
        assert sort_file_list(["only.py"]) == ["only.py"]


# ── sort_matches ────────────────────────────────────────────────────────

class TestSortMatches:
    """Search match sorting by (path, line, column)."""

    def _match(self, path: str, line: int, column: int) -> Match:
        return Match(path=path, line=line, column=column, snippet="x")

    def test_sort_by_path_then_line_then_column(self) -> None:
        matches = [
            self._match("b.py", 10, 0),
            self._match("a.py", 5, 3),
            self._match("a.py", 5, 1),
            self._match("a.py", 1, 0),
        ]
        result = sort_matches(matches)
        assert [m.path for m in result] == ["a.py", "a.py", "a.py", "b.py"]
        assert [m.line for m in result] == [1, 5, 5, 10]
        assert result[1].column == 1
        assert result[2].column == 3

    def test_empty_list(self) -> None:
        assert sort_matches([]) == []

    def test_same_file_ordering(self) -> None:
        matches = [
            self._match("f.py", 20, 0),
            self._match("f.py", 3, 0),
        ]
        result = sort_matches(matches)
        assert result[0].line == 3
        assert result[1].line == 20


# ── sort_diagnostics ────────────────────────────────────────────────────

class TestSortDiagnostics:
    """Diagnostic sorting by (file, line, severity, message)."""

    def _diag(
        self,
        file: str = "f.py",
        line: int = 1,
        severity: str = "error",
        message: str = "msg",
    ) -> Diagnostic:
        return Diagnostic(
            file=file, line=line, column=0, message=message, severity=severity
        )

    def test_sort_by_file_then_line(self) -> None:
        diags = [
            self._diag("b.py", 5),
            self._diag("a.py", 10),
            self._diag("a.py", 1),
        ]
        result = sort_diagnostics(diags)
        assert [(d.file, d.line) for d in result] == [
            ("a.py", 1), ("a.py", 10), ("b.py", 5)
        ]

    def test_error_before_warning(self) -> None:
        diags = [
            self._diag(severity="warning", message="w"),
            self._diag(severity="error", message="e"),
        ]
        result = sort_diagnostics(diags)
        assert result[0].severity == "error"
        assert result[1].severity == "warning"

    def test_severity_full_order(self) -> None:
        diags = [
            self._diag(severity="hint"),
            self._diag(severity="error"),
            self._diag(severity="info"),
            self._diag(severity="warning"),
        ]
        result = sort_diagnostics(diags)
        assert [d.severity for d in result] == [
            "error", "warning", "info", "hint"
        ]

    def test_empty_list(self) -> None:
        assert sort_diagnostics([]) == []

    def test_same_severity_by_message(self) -> None:
        diags = [
            self._diag(message="z error"),
            self._diag(message="a error"),
        ]
        result = sort_diagnostics(diags)
        assert result[0].message == "a error"


# ── sort_symbols ────────────────────────────────────────────────────────

class TestSortSymbols:
    """Symbol sorting by start_line."""

    def _sym(self, start: int) -> Symbol:
        return Symbol(
            name=f"sym_{start}", kind="function",
            start_line=start, end_line=start + 5,
        )

    def test_sort_by_start_line(self) -> None:
        syms = [self._sym(30), self._sym(1), self._sym(15)]
        result = sort_symbols(syms)
        assert [s.start_line for s in result] == [1, 15, 30]

    def test_empty_list(self) -> None:
        assert sort_symbols([]) == []

    def test_same_line_stability(self) -> None:
        s1 = Symbol(name="a", kind="function", start_line=5, end_line=10)
        s2 = Symbol(name="b", kind="function", start_line=5, end_line=10)
        result = sort_symbols([s1, s2])
        # Stable sort — original order preserved for ties
        assert result[0].name == "a"
        assert result[1].name == "b"


# ── strip_timestamps ────────────────────────────────────────────────────

class TestStripTimestamps:
    """ISO-8601 and common log timestamp replacement."""

    def test_iso_date_time(self) -> None:
        text = "logged at 2025-01-15T12:00:00Z done"
        result = strip_timestamps(text)
        assert "[timestamp]" in result
        assert "2025-01-15" not in result

    def test_time_with_ms(self) -> None:
        text = "at 2025-06-10T08:30:45.123Z end"
        result = strip_timestamps(text)
        assert "[timestamp]" in result

    def test_time_with_offset(self) -> None:
        text = "at 2025-06-10T08:30:45+05:30 end"
        result = strip_timestamps(text)
        assert "[timestamp]" in result

    def test_space_separator(self) -> None:
        text = "logged 2025-01-15 12:00:00 done"
        result = strip_timestamps(text)
        assert "[timestamp]" in result

    def test_no_timestamps_unchanged(self) -> None:
        text = "nothing special here"
        assert strip_timestamps(text) == text


# ── strip_pids ──────────────────────────────────────────────────────────

class TestStripPids:
    """Process/thread ID replacement."""

    def test_pid_equals(self) -> None:
        text = "worker pid=12345 started"
        result = strip_pids(text)
        assert "[pid]" in result
        assert "12345" not in result

    def test_pid_colon(self) -> None:
        text = "PID: 9999"
        result = strip_pids(text)
        assert "[pid]" in result
        assert "9999" not in result

    def test_process_equals(self) -> None:
        text = "process=42 running"
        result = strip_pids(text)
        assert "[pid]" in result
        assert "42" not in result

    def test_no_pids_unchanged(self) -> None:
        text = "no process info"
        assert strip_pids(text) == text


# ── strip_tmpdir ────────────────────────────────────────────────────────

class TestStripTmpdir:
    """Temp directory path replacement."""

    def test_linux_tmp(self) -> None:
        text = "wrote to /tmp/pytest-abc/file.txt"
        result = strip_tmpdir(text)
        assert "[tmpdir]" in result
        assert "/tmp/" not in result

    def test_macos_var_folders(self) -> None:
        text = "cache at /var/folders/xy/abc/T/stuff"
        result = strip_tmpdir(text)
        assert "[tmpdir]" in result
        assert "/var/folders/" not in result

    def test_windows_temp(self) -> None:
        text = r"temp at C:\Users\dev\AppData\Local\Temp\pytest123"
        result = strip_tmpdir(text)
        assert "[tmpdir]" in result

    def test_no_tmpdir_unchanged(self) -> None:
        text = "normal path /home/user/project"
        assert strip_tmpdir(text) == text


# ── normalise_paths ─────────────────────────────────────────────────────

class TestNormalisePaths:
    """Absolute→relative path conversion and slash normalisation."""

    def test_strips_workspace_root(self) -> None:
        text = "file at /home/user/project/src/main.py"
        result = normalise_paths(text, "/home/user/project")
        assert result == "file at src/main.py"

    def test_backslash_root(self) -> None:
        text = r"file at C:\code\project\src\main.py"
        result = normalise_paths(text, r"C:\code\project")
        assert "src" in result
        assert r"C:\code\project" not in result

    def test_empty_workspace_root(self) -> None:
        text = "unchanged text"
        assert normalise_paths(text, "") == text

    def test_root_with_trailing_slash(self) -> None:
        text = "file at /workspace/src/app.py"
        result = normalise_paths(text, "/workspace/")
        assert result == "file at src/app.py"


# ── sanitise_output (pipeline) ──────────────────────────────────────────

class TestSanitiseOutput:
    """Full pipeline composes all strippers."""

    def test_full_pipeline(self) -> None:
        text = (
            "2025-01-15T10:00:00Z pid=999 "
            "wrote /tmp/pytest-abc/result.txt "
            "at /workspace/src/main.py"
        )
        result = sanitise_output(text, workspace_root="/workspace")
        assert "[timestamp]" in result
        assert "[pid]" in result
        assert "[tmpdir]" in result
        assert result.endswith("src/main.py")

    def test_clean_text_unchanged(self) -> None:
        text = "nothing to strip"
        assert sanitise_output(text) == text


# ── Diff generator path normalisation ───────────────────────────────────

class TestDiffGeneratorPathNorm:
    """Diff generator normalises backslash paths."""

    def test_generate_diff_normalises_backslashes(self) -> None:
        from forge_ide.diff_generator import generate_diff

        diff = generate_diff("a\n", "b\n", path=r"src\utils\file.py")
        assert diff.path == "src/utils/file.py"

    def test_diff_to_text_normalises_backslashes(self) -> None:
        from forge_ide.contracts import UnifiedDiff
        from forge_ide.diff_generator import diff_to_text

        d = UnifiedDiff(
            path=r"src\main.py", hunks=["@@ -1 +1 @@\n-old\n+new"],
            insertions=1, deletions=1,
        )
        text = diff_to_text(d)
        assert r"a/src/main.py" in text
        assert r"b/src/main.py" in text
        assert "\\" not in text

    def test_diff_to_text_strips_trailing_whitespace(self) -> None:
        from forge_ide.contracts import UnifiedDiff
        from forge_ide.diff_generator import diff_to_text

        d = UnifiedDiff(
            path="f.py",
            hunks=["@@ -1 +1 @@\n-old   \n+new  "],
            insertions=1, deletions=1,
        )
        text = diff_to_text(d)
        for line in text.split("\n"):
            assert line == line.rstrip(), f"Trailing whitespace in: {line!r}"

    def test_generate_diff_forward_slashes_in_headers(self) -> None:
        from forge_ide.diff_generator import diff_to_text, generate_diff

        diff = generate_diff("x\n", "y\n", path="clean/path.py")
        text = diff_to_text(diff)
        assert "a/clean/path.py" in text
        assert "b/clean/path.py" in text

    def test_generate_multi_diff_normalises(self) -> None:
        from forge_ide.diff_generator import generate_multi_diff

        changes = [
            {"path": r"a\b.py", "old": "x\n", "new": "y\n"},
        ]
        results = generate_multi_diff(changes)
        assert results[0].path == "a/b.py"
