"""Tests for forge_ide.searcher — code search with ripgrep + Python fallback."""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from forge_ide.searcher import (
    DEFAULT_CONTEXT_LINES,
    DEFAULT_MAX_RESULTS,
    Match,
    _glob_to_regex,
    _is_gitignored,
    _parse_gitignore,
    _reset_rg_cache,
    _search_python,
    search,
)
from forge_ide.workspace import Workspace


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@pytest.fixture()
def ws(tmp_path: Path) -> Workspace:
    """Workspace with sample files for searching."""
    (tmp_path / "main.py").write_text(
        "import os\ndef hello():\n    print('Hello')\n\ndef world():\n    print('World')\n",
        encoding="utf-8",
    )
    (tmp_path / "utils.py").write_text(
        "def helper():\n    return True\n\ndef HELLO_UPPER():\n    pass\n",
        encoding="utf-8",
    )
    sub = tmp_path / "src"
    sub.mkdir()
    (sub / "app.ts").write_text(
        "function greet(name: string) {\n  console.log(`Hello ${name}`);\n}\n",
        encoding="utf-8",
    )
    (tmp_path / "data.json").write_text('{"hello": "world"}', encoding="utf-8")
    (tmp_path / "photo.png").write_bytes(b"\x89PNG\r\n\x1a\n\x00" * 10)
    return Workspace(tmp_path, cache_ttl=0)


@pytest.fixture(autouse=True)
def _reset_rg() -> None:
    """Reset ripgrep cache before each test."""
    _reset_rg_cache()


def _run(coro):
    """Run an async function synchronously."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ===================================================================
# Match model
# ===================================================================


class TestMatch:
    def test_creation(self) -> None:
        m = Match(
            path="foo.py", line=1, column=0, snippet="hello",
            context_before=["before"], context_after=["after"],
        )
        assert m.path == "foo.py"
        assert m.line == 1
        assert m.column == 0
        assert m.snippet == "hello"
        assert m.context_before == ["before"]
        assert m.context_after == ["after"]

    def test_frozen(self) -> None:
        m = Match(path="a.py", line=1, column=0, snippet="x")
        with pytest.raises(Exception):
            m.path = "other"  # type: ignore[misc]

    def test_defaults(self) -> None:
        m = Match(path="a.py", line=1, column=0, snippet="x")
        assert m.context_before == []
        assert m.context_after == []


# ===================================================================
# search — Python fallback (force no ripgrep)
# ===================================================================


class TestSearchPythonFallback:
    """Tests using the Python fallback path (ripgrep unavailable)."""

    @patch("forge_ide.searcher._ripgrep_available", return_value=False)
    def test_literal_match(self, _mock, ws: Workspace) -> None:
        r = _run(search(ws, "hello"))
        assert r.success is True
        assert r.data["total_count"] >= 1
        assert len(r.data["matches"]) >= 1

    @patch("forge_ide.searcher._ripgrep_available", return_value=False)
    def test_regex_match(self, _mock, ws: Workspace) -> None:
        r = _run(search(ws, r"def \w+\(", is_regex=True))
        assert r.success is True
        assert r.data["total_count"] >= 2

    @patch("forge_ide.searcher._ripgrep_available", return_value=False)
    def test_no_matches(self, _mock, ws: Workspace) -> None:
        r = _run(search(ws, "zzz_nonexistent_pattern_zzz"))
        assert r.success is True
        assert r.data["total_count"] == 0
        assert r.data["matches"] == []
        assert r.data["truncated"] is False

    @patch("forge_ide.searcher._ripgrep_available", return_value=False)
    def test_case_insensitive_default(self, _mock, ws: Workspace) -> None:
        r = _run(search(ws, "HELLO"))
        assert r.success is True
        # Should match 'Hello', 'hello', 'HELLO' etc
        assert r.data["total_count"] >= 2

    @patch("forge_ide.searcher._ripgrep_available", return_value=False)
    def test_case_sensitive(self, _mock, ws: Workspace) -> None:
        r = _run(search(ws, "Hello", case_sensitive=True))
        assert r.success is True
        # "Hello" appears but "hello" does not match
        paths_lines = [(m["path"], m["line"]) for m in r.data["matches"]]
        # All snippets must contain 'Hello' exactly
        for m in r.data["matches"]:
            assert "Hello" in m["snippet"] or "HELLO" in m["snippet"]

    @patch("forge_ide.searcher._ripgrep_available", return_value=False)
    def test_glob_filter(self, _mock, ws: Workspace) -> None:
        r = _run(search(ws, "hello", glob="*.py"))
        assert r.success is True
        for m in r.data["matches"]:
            assert m["path"].endswith(".py")

    @patch("forge_ide.searcher._ripgrep_available", return_value=False)
    def test_max_results(self, _mock, ws: Workspace) -> None:
        r = _run(search(ws, "def", max_results=1))
        assert r.success is True
        assert len(r.data["matches"]) == 1
        assert r.data["truncated"] is True

    @patch("forge_ide.searcher._ripgrep_available", return_value=False)
    def test_context_lines(self, _mock, ws: Workspace) -> None:
        r = _run(search(ws, "def hello", context_lines=1))
        assert r.success is True
        assert len(r.data["matches"]) >= 1
        m = r.data["matches"][0]
        # context_before/after should be lists
        assert isinstance(m["context_before"], list)
        assert isinstance(m["context_after"], list)

    @patch("forge_ide.searcher._ripgrep_available", return_value=False)
    def test_empty_pattern(self, _mock, ws: Workspace) -> None:
        r = _run(search(ws, ""))
        assert r.success is False
        assert "empty" in r.error.lower()

    @patch("forge_ide.searcher._ripgrep_available", return_value=False)
    def test_binary_files_skipped(self, _mock, ws: Workspace) -> None:
        r = _run(search(ws, "PNG"))
        assert r.success is True
        for m in r.data["matches"]:
            assert not m["path"].endswith(".png")

    @patch("forge_ide.searcher._ripgrep_available", return_value=False)
    def test_nested_directory(self, _mock, ws: Workspace) -> None:
        r = _run(search(ws, "greet", glob="*.ts"))
        assert r.success is True
        assert r.data["total_count"] >= 1
        assert any("src/" in m["path"] for m in r.data["matches"])

    @patch("forge_ide.searcher._ripgrep_available", return_value=False)
    def test_column_value(self, _mock, ws: Workspace) -> None:
        r = _run(search(ws, "import os"))
        assert r.success is True
        m = r.data["matches"][0]
        assert m["column"] == 0  # "import os" starts at col 0


# ===================================================================
# .gitignore
# ===================================================================


class TestGitignore:
    def test_basic_pattern(self, tmp_path: Path) -> None:
        (tmp_path / ".gitignore").write_text("*.pyc\n", encoding="utf-8")
        patterns = _parse_gitignore(tmp_path)
        assert len(patterns) >= 1
        assert _is_gitignored("foo.pyc", patterns) is True
        assert _is_gitignored("foo.py", patterns) is False

    def test_directory_pattern(self, tmp_path: Path) -> None:
        (tmp_path / ".gitignore").write_text("node_modules/\n", encoding="utf-8")
        patterns = _parse_gitignore(tmp_path)
        assert _is_gitignored("node_modules/foo.js", patterns) is True

    def test_negation_skipped(self, tmp_path: Path) -> None:
        (tmp_path / ".gitignore").write_text("*.pyc\n!important.pyc\n", encoding="utf-8")
        patterns = _parse_gitignore(tmp_path)
        # Basic parser doesn't support negation — important.pyc still matched
        assert _is_gitignored("foo.pyc", patterns) is True

    def test_no_gitignore(self, tmp_path: Path) -> None:
        patterns = _parse_gitignore(tmp_path)
        assert patterns == []

    def test_comments_ignored(self, tmp_path: Path) -> None:
        (tmp_path / ".gitignore").write_text("# comment\n*.log\n", encoding="utf-8")
        patterns = _parse_gitignore(tmp_path)
        assert len(patterns) == 1
        assert _is_gitignored("app.log", patterns) is True

    def test_blank_lines_ignored(self, tmp_path: Path) -> None:
        (tmp_path / ".gitignore").write_text("\n\n*.tmp\n\n", encoding="utf-8")
        patterns = _parse_gitignore(tmp_path)
        assert len(patterns) == 1

    @patch("forge_ide.searcher._ripgrep_available", return_value=False)
    def test_gitignore_respected_in_search(self, _mock, tmp_path: Path) -> None:
        (tmp_path / ".gitignore").write_text("*.log\n", encoding="utf-8")
        (tmp_path / "app.log").write_text("error: something\n", encoding="utf-8")
        (tmp_path / "app.py").write_text("error: handled\n", encoding="utf-8")
        w = Workspace(tmp_path, cache_ttl=0)
        r = _run(search(w, "error"))
        assert r.success is True
        paths = [m["path"] for m in r.data["matches"]]
        assert "app.log" not in paths
        assert "app.py" in paths


# ===================================================================
# _glob_to_regex
# ===================================================================


class TestGlobToRegex:
    def test_star(self) -> None:
        pat = re.compile(_glob_to_regex("*.py"))
        assert pat.search("foo.py") is not None
        assert pat.search("foo.txt") is None

    def test_double_star(self) -> None:
        pat = re.compile(_glob_to_regex("**/test_*.py"))
        assert pat.search("tests/test_foo.py") is not None
        assert pat.search("deep/nested/test_bar.py") is not None

    def test_question_mark(self) -> None:
        pat = re.compile(_glob_to_regex("?.py"))
        assert pat.search("a.py") is not None
        assert pat.search("ab.py") is None

    def test_directory_slash(self) -> None:
        pat = re.compile(_glob_to_regex("build/"))
        assert pat.search("build/") is not None
        assert pat.search("build") is not None


# ===================================================================
# Ripgrep path (mocked subprocess)
# ===================================================================


class TestSearchRipgrep:
    def _make_rg_output(self, matches_data: list[dict]) -> bytes:
        """Build fake ripgrep JSON output."""
        lines: list[str] = []
        for md in matches_data:
            entry = {
                "type": "match",
                "data": {
                    "path": {"text": md["path"]},
                    "lines": {"text": md["text"] + "\n"},
                    "line_number": md["line"],
                    "submatches": [{"match": {"text": md.get("match_text", "")}, "start": md.get("col", 0), "end": md.get("col", 0) + 1}],
                },
            }
            lines.append(json.dumps(entry))
        lines.append(json.dumps({"type": "end", "data": {}}))
        return "\n".join(lines).encode()

    @patch("forge_ide.searcher._ripgrep_available", return_value=True)
    @patch("asyncio.create_subprocess_exec")
    def test_basic_ripgrep_search(self, mock_exec, _mock_rg, ws: Workspace) -> None:
        rg_out = self._make_rg_output([
            {"path": "main.py", "line": 1, "text": "import os", "col": 0},
        ])
        proc = AsyncMock()
        proc.communicate.return_value = (rg_out, b"")
        mock_exec.return_value = proc

        r = _run(search(ws, "import"))
        assert r.success is True
        assert len(r.data["matches"]) == 1
        assert r.data["matches"][0]["path"] == "main.py"

    @patch("forge_ide.searcher._ripgrep_available", return_value=True)
    @patch("asyncio.create_subprocess_exec")
    def test_ripgrep_column(self, mock_exec, _mock_rg, ws: Workspace) -> None:
        rg_out = self._make_rg_output([
            {"path": "main.py", "line": 1, "text": "import os", "col": 7},
        ])
        proc = AsyncMock()
        proc.communicate.return_value = (rg_out, b"")
        mock_exec.return_value = proc

        r = _run(search(ws, "os"))
        assert r.success is True
        assert r.data["matches"][0]["column"] == 7

    @patch("forge_ide.searcher._ripgrep_available", return_value=True)
    @patch("asyncio.create_subprocess_exec")
    def test_ripgrep_truncated(self, mock_exec, _mock_rg, ws: Workspace) -> None:
        entries = [
            {"path": f"f{i}.py", "line": 1, "text": f"match {i}", "col": 0}
            for i in range(5)
        ]
        rg_out = self._make_rg_output(entries)
        proc = AsyncMock()
        proc.communicate.return_value = (rg_out, b"")
        mock_exec.return_value = proc

        r = _run(search(ws, "match", max_results=3))
        assert r.success is True
        assert len(r.data["matches"]) == 3
        assert r.data["truncated"] is True

    @patch("forge_ide.searcher._ripgrep_available", return_value=True)
    @patch("asyncio.create_subprocess_exec")
    def test_ripgrep_empty_results(self, mock_exec, _mock_rg, ws: Workspace) -> None:
        proc = AsyncMock()
        proc.communicate.return_value = (b"", b"")
        mock_exec.return_value = proc

        r = _run(search(ws, "zzz_nothing"))
        assert r.success is True
        assert r.data["total_count"] == 0
        assert r.data["matches"] == []

    @patch("forge_ide.searcher._ripgrep_available", return_value=True)
    @patch("asyncio.create_subprocess_exec")
    def test_ripgrep_error_falls_back(self, mock_exec, _mock_rg, ws: Workspace) -> None:
        mock_exec.side_effect = Exception("rg crashed")
        # Should fall back to Python search
        r = _run(search(ws, "hello"))
        assert r.success is True


# ===================================================================
# _ripgrep_available caching
# ===================================================================


class TestRipgrepCache:
    def test_cache_reset(self) -> None:
        _reset_rg_cache()
        # After reset, next call should re-check
        import forge_ide.searcher as mod
        assert mod._rg_available is None

    @patch("shutil.which", return_value=None)
    def test_not_available(self, _mock) -> None:
        _reset_rg_cache()
        from forge_ide.searcher import _ripgrep_available
        assert _ripgrep_available() is False

    @patch("shutil.which", return_value="/usr/bin/rg")
    def test_available(self, _mock) -> None:
        _reset_rg_cache()
        from forge_ide.searcher import _ripgrep_available
        assert _ripgrep_available() is True


# ===================================================================
# Integration-style tests
# ===================================================================


class TestSearchIntegration:
    @patch("forge_ide.searcher._ripgrep_available", return_value=False)
    def test_search_multiple_files(self, _mock, ws: Workspace) -> None:
        r = _run(search(ws, "def"))
        assert r.success is True
        paths = {m["path"] for m in r.data["matches"]}
        assert "main.py" in paths
        assert "utils.py" in paths

    @patch("forge_ide.searcher._ripgrep_available", return_value=False)
    def test_search_unicode(self, _mock, tmp_path: Path) -> None:
        (tmp_path / "uni.py").write_text('msg = "こんにちは"\n', encoding="utf-8")
        w = Workspace(tmp_path, cache_ttl=0)
        r = _run(search(w, "こんにちは"))
        assert r.success is True
        assert r.data["total_count"] >= 1

    @patch("forge_ide.searcher._ripgrep_available", return_value=False)
    def test_empty_workspace(self, _mock, tmp_path: Path) -> None:
        w = Workspace(tmp_path, cache_ttl=0)
        r = _run(search(w, "anything"))
        assert r.success is True
        assert r.data["total_count"] == 0

    @patch("forge_ide.searcher._ripgrep_available", return_value=False)
    def test_special_regex_chars_literal(self, _mock, tmp_path: Path) -> None:
        (tmp_path / "code.py").write_text("if (x + y) == 0:\n", encoding="utf-8")
        w = Workspace(tmp_path, cache_ttl=0)
        r = _run(search(w, "(x + y)"))
        assert r.success is True
        assert r.data["total_count"] >= 1

    @patch("forge_ide.searcher._ripgrep_available", return_value=False)
    def test_context_zero(self, _mock, ws: Workspace) -> None:
        r = _run(search(ws, "import os", context_lines=0))
        assert r.success is True
        for m in r.data["matches"]:
            assert m["context_before"] == []
            assert m["context_after"] == []

    @patch("forge_ide.searcher._ripgrep_available", return_value=False)
    def test_skip_dirs_respected(self, _mock, tmp_path: Path) -> None:
        cache_dir = tmp_path / "__pycache__"
        cache_dir.mkdir()
        (cache_dir / "cached.py").write_text("findme = True\n", encoding="utf-8")
        (tmp_path / "main.py").write_text("findme = True\n", encoding="utf-8")
        w = Workspace(tmp_path, cache_ttl=0)
        r = _run(search(w, "findme"))
        assert r.success is True
        paths = [m["path"] for m in r.data["matches"]]
        assert "main.py" in paths
        assert not any("__pycache__" in p for p in paths)
