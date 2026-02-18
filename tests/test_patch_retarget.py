"""Tests for Phase 42 — Patch Retargeting & Self-Healing Edits.

Covers:
  42.1  Edit / EditInstruction / EditResult models
  42.2  apply_edits with exact, anchor, and difflib retargeting
  42.3  edit_file tool in tool_executor
  42.x  apply_edit_instruction, edge cases
"""

import textwrap
from pathlib import Path

import pytest

from forge_ide.contracts import Edit, EditInstruction, EditResult
from forge_ide.patcher import (
    apply_edit_instruction,
    apply_edits,
    _find_exact,
    _find_by_anchor,
    _find_by_difflib,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


_SAMPLE_FILE = textwrap.dedent("""\
    import os
    import sys

    from app.config import Settings

    DATABASE_URL = os.getenv("DATABASE_URL", "")


    class UserRepo:
        def __init__(self, db):
            self.db = db

        def get_user(self, user_id):
            return self.db.fetch_one("SELECT * FROM users WHERE id = $1", user_id)

        def create_user(self, email):
            return self.db.execute("INSERT INTO users (email) VALUES ($1)", email)
""")


# ===========================================================================
# 42.1  Model basics
# ===========================================================================


class TestEditModels:
    """Tests for Edit / EditInstruction / EditResult Pydantic models."""

    def test_edit_creation(self) -> None:
        e = Edit(old_text="foo", new_text="bar")
        assert e.old_text == "foo"
        assert e.new_text == "bar"
        assert e.anchor == ""
        assert e.explanation == ""

    def test_edit_with_anchor(self) -> None:
        e = Edit(
            old_text="foo",
            new_text="bar",
            anchor="line above",
            explanation="rename variable",
        )
        assert e.anchor == "line above"
        assert e.explanation == "rename variable"

    def test_edit_instruction(self) -> None:
        ei = EditInstruction(
            file_path="app/main.py",
            edits=[Edit(old_text="a", new_text="b")],
        )
        assert ei.file_path == "app/main.py"
        assert len(ei.edits) == 1
        assert ei.full_rewrite is False

    def test_edit_result(self) -> None:
        er = EditResult(
            success=True,
            file_path="app/main.py",
            applied=[Edit(old_text="a", new_text="b")],
            failed=[],
            final_content="b",
            retargeted=0,
        )
        assert er.success
        assert len(er.applied) == 1
        assert er.retargeted == 0


# ===========================================================================
# 42.2  Exact match
# ===========================================================================


class TestExactMatch:
    """Tests for exact-match edit application."""

    def test_simple_replacement(self) -> None:
        edits = [Edit(old_text="DATABASE_URL", new_text="DB_URL")]
        result = apply_edits(_SAMPLE_FILE, edits)

        assert result.success
        assert len(result.applied) == 1
        assert "DB_URL" in result.final_content
        assert result.retargeted == 0

    def test_multiline_replacement(self) -> None:
        old = '    def get_user(self, user_id):\n            return self.db.fetch_one("SELECT * FROM users WHERE id = $1", user_id)'
        new = '    def get_user(self, user_id: str):\n            row = self.db.fetch_one("SELECT * FROM users WHERE id = $1", user_id)\n            return row'
        edits = [Edit(old_text=old, new_text=new)]
        result = apply_edits(_SAMPLE_FILE, edits)

        assert result.success
        assert "user_id: str" in result.final_content
        assert "row = self.db.fetch_one" in result.final_content

    def test_multiple_edits(self) -> None:
        edits = [
            Edit(old_text="import os", new_text="import os\nimport logging"),
            Edit(old_text="class UserRepo:", new_text="class UserRepo(BaseRepo):"),
        ]
        result = apply_edits(_SAMPLE_FILE, edits)

        assert result.success
        assert len(result.applied) == 2
        assert "import logging" in result.final_content
        assert "UserRepo(BaseRepo)" in result.final_content

    def test_no_match_fails(self) -> None:
        edits = [Edit(old_text="this text does not exist anywhere", new_text="replacement")]
        result = apply_edits(_SAMPLE_FILE, edits)

        assert not result.success
        assert len(result.failed) == 1
        assert len(result.applied) == 0


# ===========================================================================
# 42.2  Anchor-based retargeting
# ===========================================================================


class TestAnchorRetargeting:
    """Tests for anchor-based fuzzy matching."""

    def test_anchor_finds_shifted_text(self) -> None:
        # Simulate file where lines were inserted above the target
        shifted = (
            "import os\nimport sys\nimport json\nimport logging\n\n"
            "# Added some new comments\n# More comments\n\n"
            + _SAMPLE_FILE.split("from app.config")[1]
        )
        shifted = "from app.config" + shifted

        edits = [
            Edit(
                old_text="class UserRepo:",
                new_text="class UserRepo(BaseRepo):",
                anchor="DATABASE_URL = os.getenv",
            )
        ]
        result = apply_edits(shifted, edits)
        assert result.success
        assert "UserRepo(BaseRepo)" in result.final_content

    def test_anchor_with_no_exact_match_but_anchor_locates(self) -> None:
        # old_text has slightly different indentation (won't exact match),
        # but anchor still locates the region
        content = "def foo():\n    x = 1\n    y = 2\n    return x + y\n"
        edits = [
            Edit(
                old_text="    y = 2\n    return x + y",
                new_text="    y = 2\n    z = 3\n    return x + y + z",
                anchor="def foo():\n    x = 1",
            )
        ]
        result = apply_edits(content, edits)
        assert result.success
        assert "z = 3" in result.final_content


# ===========================================================================
# 42.2  Difflib retargeting
# ===========================================================================


class TestDifflibRetargeting:
    """Tests for difflib similarity-based matching."""

    def test_whitespace_difference(self) -> None:
        # old_text has tabs, actual file has spaces — difflib should match
        content = "def greet():\n    name = 'World'\n    print(f'Hello {name}')\n    return name\n"
        edits = [
            Edit(
                old_text="def greet():\n\tname = 'World'\n\tprint(f'Hello {name}')",
                new_text="def greet(who: str = 'World'):\n    name = who\n    print(f'Hello {name}')",
            )
        ]
        result = apply_edits(content, edits)
        assert result.success
        assert result.retargeted == 1
        assert "who: str" in result.final_content

    def test_minor_text_change(self) -> None:
        content = "class Config:\n    DEBUG = True\n    PORT = 8000\n    HOST = 'localhost'\n"
        # old_text is slightly different (missing trailing space, different value)
        edits = [
            Edit(
                old_text="class Config:\n    DEBUG = False\n    PORT = 8000",
                new_text="class Config:\n    DEBUG = False\n    PORT = 9000",
            )
        ]
        result = apply_edits(content, edits)
        # Should retarget via difflib (ratio > 0.80)
        assert result.success
        assert result.retargeted == 1

    def test_completely_different_text_fails(self) -> None:
        content = "def foo():\n    return 1\n"
        edits = [
            Edit(
                old_text="class BarService:\n    def __init__(self):\n        self.x = 42\n        self.y = 99",
                new_text="class BarService:\n    pass",
            )
        ]
        result = apply_edits(content, edits)
        assert not result.success
        assert len(result.failed) == 1


# ===========================================================================
# 42.x  apply_edit_instruction
# ===========================================================================


class TestApplyEditInstruction:
    """Tests for the EditInstruction wrapper."""

    def test_normal_edits(self) -> None:
        instr = EditInstruction(
            file_path="app/config.py",
            edits=[Edit(old_text="import os", new_text="import os\nimport sys")],
        )
        result = apply_edit_instruction(_SAMPLE_FILE, instr)
        assert result.success
        assert result.file_path == "app/config.py"
        assert "import sys" in result.final_content

    def test_full_rewrite(self) -> None:
        new_content = "# completely new file\nprint('hello')\n"
        instr = EditInstruction(
            file_path="app/config.py",
            edits=[Edit(old_text="", new_text=new_content)],
            full_rewrite=True,
        )
        result = apply_edit_instruction(_SAMPLE_FILE, instr)
        assert result.success
        assert result.final_content == new_content

    def test_empty_edits(self) -> None:
        instr = EditInstruction(file_path="x.py", edits=[])
        result = apply_edit_instruction(_SAMPLE_FILE, instr)
        assert result.success
        assert result.final_content == _SAMPLE_FILE


# ===========================================================================
# 42.2  Internal helpers
# ===========================================================================


class TestInternalHelpers:
    """Tests for internal matching functions."""

    def test_find_exact_present(self) -> None:
        idx = _find_exact("hello world", "world")
        assert idx == 6

    def test_find_exact_absent(self) -> None:
        idx = _find_exact("hello world", "xyz")
        assert idx is None

    def test_find_by_anchor_locates(self) -> None:
        content = "line1\nline2\nTARGET\nline4\n"
        idx = _find_by_anchor(content, "line1\nline2", "TARGET")
        assert idx is not None
        assert content[idx : idx + 6] == "TARGET"

    def test_find_by_anchor_no_anchor(self) -> None:
        idx = _find_by_anchor("some content", "", "TARGET")
        assert idx is None

    def test_find_by_difflib_close_match(self) -> None:
        content = "def foo():\n    x = 1\n    y = 2\n    return x + y\n"
        old_text = "def foo():\n    x = 1\n    y = 3\n    return x + y"
        result = _find_by_difflib(content, old_text)
        assert result is not None
        start, end = result
        assert start >= 0

    def test_find_by_difflib_no_match(self) -> None:
        content = "completely different\ncontent here\n"
        old_text = "def very_specific_function():\n    some_logic()\n    more_logic()\n    final_line()"
        result = _find_by_difflib(content, old_text)
        assert result is None


# ===========================================================================
# 42.3  edit_file tool
# ===========================================================================


class TestEditFileTool:
    """Tests for the edit_file tool in tool_executor."""

    def test_edit_file_basic(self, tmp_path: Path) -> None:
        from app.services.tool_executor import _exec_edit_file

        target = tmp_path / "test.py"
        target.write_text("def hello():\n    return 'world'\n", encoding="utf-8")

        result = _exec_edit_file(
            {
                "path": "test.py",
                "edits": [
                    {
                        "old_text": "return 'world'",
                        "new_text": "return 'hello world'",
                    }
                ],
            },
            str(tmp_path),
        )

        assert "OK" in result
        assert "1/1" in result
        assert target.read_text(encoding="utf-8") == "def hello():\n    return 'hello world'\n"

    def test_edit_file_with_retarget(self, tmp_path: Path) -> None:
        from app.services.tool_executor import _exec_edit_file

        target = tmp_path / "test.py"
        target.write_text("class Foo:\n    x = 1\n    y = 2\n", encoding="utf-8")

        result = _exec_edit_file(
            {
                "path": "test.py",
                "edits": [
                    {
                        "old_text": "class Foo:\n\tx = 1",
                        "new_text": "class Foo:\n    x = 10",
                        "anchor": "",
                    }
                ],
            },
            str(tmp_path),
        )

        assert "OK" in result or "retargeted" in result

    def test_edit_file_nonexistent(self, tmp_path: Path) -> None:
        from app.services.tool_executor import _exec_edit_file

        result = _exec_edit_file(
            {
                "path": "nonexistent.py",
                "edits": [{"old_text": "a", "new_text": "b"}],
            },
            str(tmp_path),
        )

        assert "Error" in result
        assert "does not exist" in result

    def test_edit_file_no_edits(self, tmp_path: Path) -> None:
        from app.services.tool_executor import _exec_edit_file

        target = tmp_path / "test.py"
        target.write_text("content\n", encoding="utf-8")

        result = _exec_edit_file(
            {"path": "test.py", "edits": []},
            str(tmp_path),
        )

        assert "Error" in result
        assert "No edits" in result

    def test_edit_file_partial_failure(self, tmp_path: Path) -> None:
        from app.services.tool_executor import _exec_edit_file

        target = tmp_path / "test.py"
        target.write_text("line1\nline2\nline3\n", encoding="utf-8")

        result = _exec_edit_file(
            {
                "path": "test.py",
                "edits": [
                    {"old_text": "line1", "new_text": "LINE1"},
                    {"old_text": "this does not exist at all in the file", "new_text": "X"},
                ],
            },
            str(tmp_path),
        )

        assert "PARTIAL" in result or "FAILED" in result

    def test_edit_file_sandbox_violation(self, tmp_path: Path) -> None:
        from app.services.tool_executor import _exec_edit_file

        result = _exec_edit_file(
            {
                "path": "../../../etc/passwd",
                "edits": [{"old_text": "a", "new_text": "b"}],
            },
            str(tmp_path),
        )

        assert "Error" in result


# ===========================================================================
# Edge cases
# ===========================================================================


class TestEdgeCases:
    """Edge case tests for the edit engine."""

    def test_empty_content(self) -> None:
        edits = [Edit(old_text="", new_text="new content")]
        result = apply_edits("", edits)
        # Empty old_text matches at position 0
        assert result.success
        assert result.final_content == "new content"

    def test_edit_preserves_surrounding(self) -> None:
        content = "before\ntarget line\nafter\n"
        edits = [Edit(old_text="target line", new_text="modified line")]
        result = apply_edits(content, edits)
        assert result.success
        assert result.final_content == "before\nmodified line\nafter\n"

    def test_sequential_edits_compose(self) -> None:
        content = "a = 1\nb = 2\nc = 3\n"
        edits = [
            Edit(old_text="a = 1", new_text="a = 10"),
            Edit(old_text="b = 2", new_text="b = 20"),
            Edit(old_text="c = 3", new_text="c = 30"),
        ]
        result = apply_edits(content, edits)
        assert result.success
        assert "a = 10" in result.final_content
        assert "b = 20" in result.final_content
        assert "c = 30" in result.final_content

    def test_delete_text(self) -> None:
        content = "line1\ndelete_me\nline3\n"
        edits = [Edit(old_text="delete_me\n", new_text="")]
        result = apply_edits(content, edits)
        assert result.success
        assert "delete_me" not in result.final_content
        assert "line1\nline3\n" == result.final_content

    def test_insert_text(self) -> None:
        content = "line1\nline2\n"
        edits = [Edit(old_text="line1\n", new_text="line1\nnew_line\n")]
        result = apply_edits(content, edits)
        assert result.success
        assert "new_line" in result.final_content

    def test_file_path_in_result(self) -> None:
        edits = [Edit(old_text="x", new_text="y")]
        result = apply_edits("x = 1", edits, file_path="app/main.py")
        assert result.file_path == "app/main.py"
