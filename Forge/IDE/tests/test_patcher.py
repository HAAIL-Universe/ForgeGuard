"""Tests for forge_ide.patcher — unified diff parsing and application."""

from __future__ import annotations

import pytest

from forge_ide.errors import ParseError, PatchConflict
from forge_ide.patcher import (
    DEFAULT_FUZZ,
    Hunk,
    PatchResult,
    _match_hunk,
    apply_multi_patch,
    apply_patch,
    parse_unified_diff,
)


# ═══════════════════════════════════════════════════════════════════════════
# Fixtures — sample content & diffs
# ═══════════════════════════════════════════════════════════════════════════

SAMPLE_CONTENT = """\
line 1
line 2
line 3
line 4
line 5
line 6
line 7
line 8
line 9
line 10"""

SINGLE_HUNK_DIFF = """\
--- a/file.py
+++ b/file.py
@@ -3,3 +3,3 @@
 line 3
-line 4
+line 4 modified
 line 5"""

MULTI_HUNK_DIFF = """\
--- a/file.py
+++ b/file.py
@@ -2,3 +2,3 @@
 line 2
-line 3
+line 3 changed
 line 4
@@ -8,3 +8,3 @@
 line 8
-line 9
+line 9 changed
 line 10"""

INSERT_ONLY_DIFF = """\
--- a/file.py
+++ b/file.py
@@ -4,2 +4,4 @@
 line 4
+inserted A
+inserted B
 line 5"""

DELETE_ONLY_DIFF = """\
--- a/file.py
+++ b/file.py
@@ -4,3 +4,1 @@
 line 4
-line 5
-line 6
 line 7"""

HUNK_AT_START_DIFF = """\
--- a/file.py
+++ b/file.py
@@ -1,2 +1,2 @@
-line 1
+LINE ONE
 line 2"""

HUNK_AT_END_DIFF = """\
--- a/file.py
+++ b/file.py
@@ -9,2 +9,2 @@
 line 9
-line 10
+line 10 final"""


# ═══════════════════════════════════════════════════════════════════════════
# Models
# ═══════════════════════════════════════════════════════════════════════════


class TestHunkModel:
    def test_frozen(self):
        h = Hunk(old_start=1, old_count=1, new_start=1, new_count=1)
        with pytest.raises(Exception):
            h.old_start = 5  # type: ignore[misc]

    def test_defaults(self):
        h = Hunk(old_start=1, old_count=3, new_start=1, new_count=3)
        assert h.context_before == []
        assert h.removals == []
        assert h.additions == []
        assert h.context_after == []


class TestPatchResultModel:
    def test_frozen(self):
        r = PatchResult(success=True)
        with pytest.raises(Exception):
            r.success = False  # type: ignore[misc]

    def test_defaults(self):
        r = PatchResult(success=True)
        assert r.path == ""
        assert r.hunks_applied == 0
        assert r.insertions == 0
        assert r.deletions == 0


# ═══════════════════════════════════════════════════════════════════════════
# parse_unified_diff
# ═══════════════════════════════════════════════════════════════════════════


class TestParseUnifiedDiff:
    def test_empty_string(self):
        assert parse_unified_diff("") == []

    def test_whitespace_only(self):
        assert parse_unified_diff("   \n  ") == []

    def test_single_hunk(self):
        hunks = parse_unified_diff(SINGLE_HUNK_DIFF)
        assert len(hunks) == 1
        h = hunks[0]
        assert h.old_start == 3
        assert h.old_count == 3
        assert h.new_start == 3
        assert h.new_count == 3
        assert h.context_before == ["line 3"]
        assert h.removals == ["line 4"]
        assert h.additions == ["line 4 modified"]
        assert h.context_after == ["line 5"]

    def test_multi_hunk(self):
        hunks = parse_unified_diff(MULTI_HUNK_DIFF)
        assert len(hunks) == 2
        assert hunks[0].removals == ["line 3"]
        assert hunks[0].additions == ["line 3 changed"]
        assert hunks[1].removals == ["line 9"]
        assert hunks[1].additions == ["line 9 changed"]

    def test_insert_only(self):
        hunks = parse_unified_diff(INSERT_ONLY_DIFF)
        assert len(hunks) == 1
        assert hunks[0].removals == []
        assert hunks[0].additions == ["inserted A", "inserted B"]

    def test_delete_only(self):
        hunks = parse_unified_diff(DELETE_ONLY_DIFF)
        assert len(hunks) == 1
        assert hunks[0].removals == ["line 5", "line 6"]
        assert hunks[0].additions == []

    def test_hunk_at_start(self):
        hunks = parse_unified_diff(HUNK_AT_START_DIFF)
        assert len(hunks) == 1
        assert hunks[0].old_start == 1
        assert hunks[0].removals == ["line 1"]
        assert hunks[0].additions == ["LINE ONE"]

    def test_hunk_at_end(self):
        hunks = parse_unified_diff(HUNK_AT_END_DIFF)
        assert len(hunks) == 1
        assert hunks[0].removals == ["line 10"]
        assert hunks[0].additions == ["line 10 final"]

    def test_malformed_header(self):
        bad = "@@ this is not valid @@\n-old\n+new"
        with pytest.raises(ParseError):
            parse_unified_diff(bad)

    def test_no_hunk_headers(self):
        """Just file headers, no hunks."""
        result = parse_unified_diff("--- a/file\n+++ b/file\n")
        assert result == []

    def test_count_defaults_to_one(self):
        """@@ -5 +5 @@ without counts defaults to 1."""
        diff = "@@ -5 +5 @@\n-old\n+new"
        hunks = parse_unified_diff(diff)
        assert len(hunks) == 1
        assert hunks[0].old_count == 1
        assert hunks[0].new_count == 1

    def test_no_newline_marker(self):
        diff = """\
@@ -1,2 +1,2 @@
-old
+new
 ctx
\\ No newline at end of file"""
        hunks = parse_unified_diff(diff)
        assert len(hunks) == 1


# ═══════════════════════════════════════════════════════════════════════════
# _match_hunk
# ═══════════════════════════════════════════════════════════════════════════


class TestMatchHunk:
    def _lines(self) -> list[str]:
        return SAMPLE_CONTENT.split("\n")

    def test_exact_match(self):
        h = Hunk(
            old_start=3, old_count=3, new_start=3, new_count=3,
            context_before=["line 3"], removals=["line 4"], context_after=["line 5"],
        )
        pos = _match_hunk(self._lines(), h, fuzz=3)
        assert pos == 2  # 0-based

    def test_offset_plus_1(self):
        """Hunk says line 2, but content is at line 3 (offset +1)."""
        h = Hunk(
            old_start=2, old_count=3, new_start=2, new_count=3,
            context_before=["line 3"], removals=["line 4"], context_after=["line 5"],
        )
        pos = _match_hunk(self._lines(), h, fuzz=3)
        assert pos == 2

    def test_offset_minus_1(self):
        h = Hunk(
            old_start=4, old_count=3, new_start=4, new_count=3,
            context_before=["line 3"], removals=["line 4"], context_after=["line 5"],
        )
        pos = _match_hunk(self._lines(), h, fuzz=3)
        assert pos == 2

    def test_offset_plus_3(self):
        h = Hunk(
            old_start=1, old_count=3, new_start=1, new_count=3,
            context_before=["line 3"], removals=["line 4"], context_after=["line 5"],
        )
        pos = _match_hunk(self._lines(), h, fuzz=3)
        # old_start=1, expected at index 0. Actual at index 2. Offset = +2.
        assert pos == 2

    def test_no_match(self):
        h = Hunk(
            old_start=3, old_count=3, new_start=3, new_count=3,
            context_before=["NONEXISTENT"], removals=["ALSO FAKE"],
            context_after=["NOPE"],
        )
        pos = _match_hunk(self._lines(), h, fuzz=3)
        assert pos is None

    def test_strict_fuzz_zero(self):
        """With fuzz=0, only exact position matches."""
        h = Hunk(
            old_start=3, old_count=3, new_start=3, new_count=3,
            context_before=["line 3"], removals=["line 4"], context_after=["line 5"],
        )
        # Exact position: old_start=3, 0-based=2. Content IS at 2 → match
        pos = _match_hunk(self._lines(), h, fuzz=0)
        assert pos == 2

    def test_strict_fuzz_zero_miss(self):
        """Fuzz=0 and wrong position → no match."""
        h = Hunk(
            old_start=5, old_count=3, new_start=5, new_count=3,
            context_before=["line 3"], removals=["line 4"], context_after=["line 5"],
        )
        pos = _match_hunk(self._lines(), h, fuzz=0)
        assert pos is None

    def test_pure_insertion(self):
        """Pure insertion (no old lines) uses target position."""
        h = Hunk(
            old_start=5, old_count=0, new_start=5, new_count=2,
            context_before=[], removals=[], additions=["new1", "new2"],
            context_after=[],
        )
        pos = _match_hunk(self._lines(), h, fuzz=3)
        assert pos == 4  # 0-based for line 5

    def test_beyond_file_end(self):
        """Offset takes search past end of file."""
        h = Hunk(
            old_start=100, old_count=1, new_start=100, new_count=1,
            context_before=[], removals=["line 3"], context_after=[],
        )
        pos = _match_hunk(self._lines(), h, fuzz=3)
        assert pos is None


# ═══════════════════════════════════════════════════════════════════════════
# apply_patch
# ═══════════════════════════════════════════════════════════════════════════


class TestApplyPatch:
    def test_single_hunk(self):
        r = apply_patch(SAMPLE_CONTENT, SINGLE_HUNK_DIFF, path="file.py")
        assert r.success is True
        assert r.hunks_applied == 1
        assert "line 4 modified" in r.post_content
        assert "line 4\n" not in r.post_content.replace("line 4 modified", "")
        assert r.insertions == 1
        assert r.deletions == 1

    def test_multi_hunk(self):
        r = apply_patch(SAMPLE_CONTENT, MULTI_HUNK_DIFF, path="file.py")
        assert r.success is True
        assert r.hunks_applied == 2
        assert "line 3 changed" in r.post_content
        assert "line 9 changed" in r.post_content

    def test_insert_only(self):
        r = apply_patch(SAMPLE_CONTENT, INSERT_ONLY_DIFF)
        assert r.success is True
        assert "inserted A" in r.post_content
        assert "inserted B" in r.post_content
        assert r.insertions == 2
        assert r.deletions == 0

    def test_delete_only(self):
        r = apply_patch(SAMPLE_CONTENT, DELETE_ONLY_DIFF)
        assert r.success is True
        assert "line 5" not in r.post_content
        assert "line 6" not in r.post_content
        assert r.deletions == 2
        assert r.insertions == 0

    def test_hunk_at_start(self):
        r = apply_patch(SAMPLE_CONTENT, HUNK_AT_START_DIFF)
        assert r.success is True
        lines = r.post_content.split("\n")
        assert lines[0] == "LINE ONE"

    def test_hunk_at_end(self):
        r = apply_patch(SAMPLE_CONTENT, HUNK_AT_END_DIFF)
        assert r.success is True
        lines = r.post_content.split("\n")
        assert lines[-1] == "line 10 final"

    def test_empty_diff(self):
        r = apply_patch(SAMPLE_CONTENT, "")
        assert r.success is True
        assert r.hunks_applied == 0
        assert r.post_content == SAMPLE_CONTENT
        assert r.insertions == 0
        assert r.deletions == 0

    def test_preserves_pre_content(self):
        r = apply_patch(SAMPLE_CONTENT, SINGLE_HUNK_DIFF)
        assert r.pre_content == SAMPLE_CONTENT

    def test_path_propagated(self):
        r = apply_patch(SAMPLE_CONTENT, SINGLE_HUNK_DIFF, path="my/file.py")
        assert r.path == "my/file.py"

    def test_conflict_raises(self):
        modified = SAMPLE_CONTENT.replace("line 4", "DIFFERENT")
        with pytest.raises(PatchConflict) as exc_info:
            apply_patch(modified, SINGLE_HUNK_DIFF, path="file.py")
        assert exc_info.value.hunk_index == 0
        assert exc_info.value.file_path == "file.py"

    def test_conflict_deleted_lines(self):
        """Target has fewer lines than expected."""
        short_content = "line 1\nline 2"
        diff = """\
@@ -3,3 +3,3 @@
 line 3
-line 4
+line 4 mod
 line 5"""
        with pytest.raises(PatchConflict):
            apply_patch(short_content, diff)

    def test_fuzzy_offset_2(self):
        """Target content shifted by 2 lines (inserted 2 lines at top)."""
        shifted = "extra 1\nextra 2\n" + SAMPLE_CONTENT
        diff = """\
@@ -3,3 +3,3 @@
 line 3
-line 4
+line 4 fuzzed
 line 5"""
        r = apply_patch(shifted, diff, fuzz=3)
        assert r.success is True
        assert "line 4 fuzzed" in r.post_content

    def test_fuzzy_beyond_fuzz_raises(self):
        """Offset 4 with fuzz=3 → conflict."""
        shifted = "a\nb\nc\nd\n" + SAMPLE_CONTENT
        diff = """\
@@ -3,3 +3,3 @@
 line 3
-line 4
+line 4 mod
 line 5"""
        with pytest.raises(PatchConflict):
            apply_patch(shifted, diff, fuzz=3)

    def test_multi_hunk_offset_adjustment(self):
        """First hunk adds lines, second hunk's position must adjust."""
        content = "a\nb\nc\nd\ne\nf"
        diff = """\
--- a/test
+++ b/test
@@ -2,1 +2,2 @@
 b
+inserted
@@ -5,1 +6,1 @@
-e
+E"""
        r = apply_patch(content, diff)
        assert r.success is True
        lines = r.post_content.split("\n")
        assert "inserted" in lines
        assert "E" in lines
        assert "e" not in lines


# ═══════════════════════════════════════════════════════════════════════════
# apply_multi_patch
# ═══════════════════════════════════════════════════════════════════════════


class TestApplyMultiPatch:
    def test_all_succeed(self):
        patches = [
            {"path": "a.py", "content": SAMPLE_CONTENT, "diff": SINGLE_HUNK_DIFF},
            {"path": "b.py", "content": SAMPLE_CONTENT, "diff": HUNK_AT_START_DIFF},
        ]
        results = apply_multi_patch(patches)
        assert len(results) == 2
        assert all(r.success for r in results)

    def test_second_fails(self):
        bad_content = "totally different"
        patches = [
            {"path": "a.py", "content": SAMPLE_CONTENT, "diff": SINGLE_HUNK_DIFF},
            {"path": "b.py", "content": bad_content, "diff": SINGLE_HUNK_DIFF},
        ]
        with pytest.raises(PatchConflict):
            apply_multi_patch(patches)

    def test_empty_list(self):
        results = apply_multi_patch([])
        assert results == []

    def test_empty_diff_in_list(self):
        patches = [
            {"path": "a.py", "content": SAMPLE_CONTENT, "diff": ""},
        ]
        results = apply_multi_patch(patches)
        assert len(results) == 1
        assert results[0].success is True
        assert results[0].post_content == SAMPLE_CONTENT
