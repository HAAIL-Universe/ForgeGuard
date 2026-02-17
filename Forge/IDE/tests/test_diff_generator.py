"""Tests for forge_ide.diff_generator — diff creation and round-trip."""

from __future__ import annotations

import pytest

from forge_ide.contracts import UnifiedDiff
from forge_ide.diff_generator import diff_to_text, generate_diff, generate_multi_diff
from forge_ide.patcher import apply_patch, parse_unified_diff


# ═══════════════════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════════════════

OLD = """\
line 1
line 2
line 3
line 4
line 5"""

NEW_MODIFIED = """\
line 1
line 2
line 3 changed
line 4
line 5"""

NEW_INSERT = """\
line 1
line 2
line 3
HELLO
line 4
line 5"""

NEW_DELETE = """\
line 1
line 2
line 4
line 5"""


# ═══════════════════════════════════════════════════════════════════════════
# generate_diff
# ═══════════════════════════════════════════════════════════════════════════


class TestGenerateDiff:
    def test_identical_content(self):
        d = generate_diff(OLD, OLD, path="same.py")
        assert isinstance(d, UnifiedDiff)
        assert d.hunks == []
        assert d.insertions == 0
        assert d.deletions == 0
        assert d.path == "same.py"

    def test_single_line_change(self):
        d = generate_diff(OLD, NEW_MODIFIED, path="mod.py")
        assert d.insertions == 1
        assert d.deletions == 1
        assert len(d.hunks) >= 1

    def test_insertion(self):
        d = generate_diff(OLD, NEW_INSERT, path="ins.py")
        assert d.insertions == 1
        assert d.deletions == 0

    def test_deletion(self):
        d = generate_diff(OLD, NEW_DELETE, path="del.py")
        assert d.deletions == 1
        assert d.insertions == 0

    def test_multi_line_change(self):
        old = "a\nb\nc\nd\ne"
        new = "a\nB\nC\nd\ne"
        d = generate_diff(old, new, path="multi.py")
        assert d.insertions == 2
        assert d.deletions == 2

    def test_crlf_normalisation(self):
        old_crlf = "line 1\r\nline 2\r\nline 3\r\n"
        new_crlf = "line 1\r\nline 2 changed\r\nline 3\r\n"
        d = generate_diff(old_crlf, new_crlf, path="crlf.py")
        assert d.deletions == 1
        assert d.insertions == 1
        # hunks should NOT contain \r
        for h in d.hunks:
            assert "\r" not in h

    def test_both_empty(self):
        d = generate_diff("", "", path="empty.py")
        assert d.hunks == []
        assert d.insertions == 0
        assert d.deletions == 0

    def test_from_empty(self):
        d = generate_diff("", "hello\n", path="new.py")
        assert d.insertions >= 1
        assert d.deletions == 0

    def test_to_empty(self):
        d = generate_diff("hello\n", "", path="gone.py")
        assert d.deletions >= 1
        assert d.insertions == 0

    def test_context_lines_parameter(self):
        d3 = generate_diff(OLD, NEW_MODIFIED, path="f.py", context_lines=3)
        d0 = generate_diff(OLD, NEW_MODIFIED, path="f.py", context_lines=0)
        # Fewer context lines → fewer total lines in hunk text
        total_3 = sum(len(h.split("\n")) for h in d3.hunks)
        total_0 = sum(len(h.split("\n")) for h in d0.hunks)
        assert total_0 <= total_3

    def test_path_default(self):
        d = generate_diff("a", "b")
        assert d.path == ""


# ═══════════════════════════════════════════════════════════════════════════
# generate_multi_diff
# ═══════════════════════════════════════════════════════════════════════════


class TestGenerateMultiDiff:
    def test_multiple_files(self):
        changes = [
            {"path": "a.py", "old": OLD, "new": NEW_MODIFIED},
            {"path": "b.py", "old": OLD, "new": NEW_INSERT},
        ]
        results = generate_multi_diff(changes)
        assert len(results) == 2
        assert results[0].path == "a.py"
        assert results[1].path == "b.py"

    def test_empty_list(self):
        results = generate_multi_diff([])
        assert results == []

    def test_identical_file_in_list(self):
        changes = [
            {"path": "same.py", "old": OLD, "new": OLD},
        ]
        results = generate_multi_diff(changes)
        assert len(results) == 1
        assert results[0].hunks == []


# ═══════════════════════════════════════════════════════════════════════════
# diff_to_text
# ═══════════════════════════════════════════════════════════════════════════


class TestDiffToText:
    def test_renders_nonempty(self):
        d = generate_diff(OLD, NEW_MODIFIED, path="file.py")
        text = diff_to_text(d)
        assert "---" in text
        assert "+++" in text
        assert "@@" in text

    def test_empty_diff_renders_minimal(self):
        d = generate_diff(OLD, OLD, path="same.py")
        text = diff_to_text(d)
        # Empty diff → just headers or truly empty
        assert "@@" not in text

    def test_parseable_output(self):
        """diff_to_text output can be parsed by parse_unified_diff."""
        d = generate_diff(OLD, NEW_MODIFIED, path="file.py")
        text = diff_to_text(d)
        hunks = parse_unified_diff(text)
        assert len(hunks) >= 1


# ═══════════════════════════════════════════════════════════════════════════
# Round-trip: generate → text → apply = new
# ═══════════════════════════════════════════════════════════════════════════


class TestRoundTrip:
    def _round_trip(self, old: str, new: str, path: str = "rt.py") -> str:
        d = generate_diff(old, new, path=path)
        text = diff_to_text(d)
        result = apply_patch(old, text, path=path)
        return result.post_content

    def test_modification(self):
        out = self._round_trip(OLD, NEW_MODIFIED)
        assert out == NEW_MODIFIED

    def test_insertion(self):
        out = self._round_trip(OLD, NEW_INSERT)
        assert out == NEW_INSERT

    def test_deletion(self):
        out = self._round_trip(OLD, NEW_DELETE)
        assert out == NEW_DELETE

    def test_identical(self):
        out = self._round_trip(OLD, OLD)
        assert out == OLD

    def test_multi_change(self):
        old = "a\nb\nc\nd\ne\nf\ng"
        new = "a\nB\nc\nd\nE\nf\nG"
        out = self._round_trip(old, new)
        assert out == new

    def test_crlf_input(self):
        old_crlf = "line1\r\nline2\r\nline3\r\n"
        new_crlf = "line1\r\nLINE2\r\nline3\r\n"
        # After normalisation, result uses LF
        d = generate_diff(old_crlf, new_crlf, path="crlf.py")
        text = diff_to_text(d)
        old_lf = old_crlf.replace("\r\n", "\n")
        result = apply_patch(old_lf, text, path="crlf.py")
        assert "LINE2" in result.post_content

    def test_from_empty_round_trip(self):
        """Creating content from nothing."""
        new = "hello\nworld\n"
        d = generate_diff("", new, path="new.py")
        text = diff_to_text(d)
        result = apply_patch("", text, path="new.py")
        assert result.post_content == new

    def test_to_empty_round_trip(self):
        """Deleting all content."""
        old = "goodbye\nworld\n"
        d = generate_diff(old, "", path="del.py")
        text = diff_to_text(d)
        result = apply_patch(old, text, path="del.py")
        assert result.post_content == ""
