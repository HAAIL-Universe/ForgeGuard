"""Tests for forge_ide.reader — structured file reading."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from forge_ide.reader import (
    DEFAULT_MAX_FILE_BYTES,
    _BINARY_EXTENSIONS,
    _find_block_end,
    detect_encoding,
    is_binary,
    read_file,
    read_range,
    read_symbol,
)
from forge_ide.workspace import Workspace


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@pytest.fixture()
def ws(tmp_path: Path) -> Workspace:
    """Create a minimal workspace with sample files."""
    (tmp_path / "hello.py").write_text(
        textwrap.dedent("""\
            import os

            def greet(name: str) -> str:
                return f"Hello, {name}!"

            async def async_greet(name: str) -> str:
                return f"Hi, {name}!"

            class Greeter:
                def __init__(self, name: str):
                    self.name = name

                def say(self) -> str:
                    return f"Hello, {self.name}!"
        """),
        encoding="utf-8",
    )
    (tmp_path / "empty.py").write_text("", encoding="utf-8")
    (tmp_path / "data.json").write_text('{"key": "value"}', encoding="utf-8")
    (tmp_path / "photo.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)
    (tmp_path / "notes.txt").write_text("Line 1\nLine 2\nLine 3\n", encoding="utf-8")
    (tmp_path / "multiline.txt").write_text(
        "A\nB\nC\nD\nE\nF\nG\nH\nI\nJ\n", encoding="utf-8"
    )
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "nested.py").write_text("x = 1\n", encoding="utf-8")
    return Workspace(tmp_path, cache_ttl=0)


@pytest.fixture()
def ts_ws(tmp_path: Path) -> Workspace:
    """Workspace with TypeScript/JavaScript files."""
    (tmp_path / "app.ts").write_text(
        textwrap.dedent("""\
            export function greet(name: string): string {
                return `Hello, ${name}!`;
            }

            export class MyClass {
                constructor(private name: string) {}
                say(): string {
                    return this.name;
                }
            }

            export const MY_CONST = 42;

            export async function fetchData(url: string): Promise<void> {
                await fetch(url);
            }
        """),
        encoding="utf-8",
    )
    (tmp_path / "index.js").write_text(
        textwrap.dedent("""\
            function helper() {
                return true;
            }

            const config = {
                debug: false,
            };
        """),
        encoding="utf-8",
    )
    return Workspace(tmp_path, cache_ttl=0)


# ===================================================================
# detect_encoding
# ===================================================================


class TestDetectEncoding:
    def test_utf8(self) -> None:
        assert detect_encoding(b"Hello world") == "utf-8"

    def test_utf8_bom(self) -> None:
        assert detect_encoding(b"\xef\xbb\xbfHello") == "utf-8-sig"

    def test_latin1(self) -> None:
        # Byte 0xe9 is valid latin-1 but invalid UTF-8
        assert detect_encoding(b"caf\xe9") == "latin-1"

    def test_empty(self) -> None:
        assert detect_encoding(b"") == "utf-8"


# ===================================================================
# is_binary
# ===================================================================


class TestIsBinary:
    def test_binary_extension(self, tmp_path: Path) -> None:
        f = tmp_path / "image.png"
        f.write_bytes(b"not really png")
        assert is_binary(f) is True

    def test_null_byte_content(self, tmp_path: Path) -> None:
        f = tmp_path / "data.bin"
        f.write_bytes(b"some\x00binary")
        assert is_binary(f) is True

    def test_normal_file(self, tmp_path: Path) -> None:
        f = tmp_path / "readme.md"
        f.write_text("# Hello", encoding="utf-8")
        assert is_binary(f) is False

    def test_nonexistent_extension(self, tmp_path: Path) -> None:
        f = tmp_path / "data.xyz"
        f.write_text("normal text", encoding="utf-8")
        assert is_binary(f) is False


# ===================================================================
# read_file
# ===================================================================


class TestReadFile:
    def test_basic_read(self, ws: Workspace) -> None:
        r = read_file(ws, "hello.py")
        assert r.success is True
        assert r.data["path"] == "hello.py"
        assert "def greet" in r.data["content"]
        assert r.data["line_count"] > 0
        assert r.data["size_bytes"] > 0
        assert r.data["language"] == "python"
        assert r.data["encoding"] == "utf-8"

    def test_nonexistent(self, ws: Workspace) -> None:
        r = read_file(ws, "nope.py")
        assert r.success is False
        assert "not found" in r.error.lower()

    def test_directory(self, ws: Workspace) -> None:
        r = read_file(ws, "sub")
        assert r.success is False
        assert "not a file" in r.error.lower()

    def test_binary_file(self, ws: Workspace) -> None:
        r = read_file(ws, "photo.png")
        assert r.success is False
        assert "binary" in r.error.lower()

    def test_empty_file(self, ws: Workspace) -> None:
        r = read_file(ws, "empty.py")
        assert r.success is True
        assert r.data["content"] == ""
        assert r.data["line_count"] == 0

    def test_json_language(self, ws: Workspace) -> None:
        r = read_file(ws, "data.json")
        assert r.success is True
        assert r.data["language"] == "json"

    def test_size_limit_exceeded(self, ws: Workspace) -> None:
        r = read_file(ws, "hello.py", max_bytes=10)
        assert r.success is False
        assert "size limit" in r.error.lower()

    def test_sandbox_violation(self, ws: Workspace) -> None:
        r = read_file(ws, "../outside.py")
        assert r.success is False
        assert "sandbox" in r.error.lower() or "traversal" in r.error.lower()

    def test_nested_file(self, ws: Workspace) -> None:
        r = read_file(ws, "sub/nested.py")
        assert r.success is True
        assert r.data["content"].strip() == "x = 1"

    def test_utf8_bom(self, tmp_path: Path) -> None:
        f = tmp_path / "bom.py"
        f.write_bytes(b"\xef\xbb\xbfprint('hello')\n")
        w = Workspace(tmp_path, cache_ttl=0)
        r = read_file(w, "bom.py")
        assert r.success is True
        assert r.data["encoding"] == "utf-8-sig"

    def test_latin1_encoding(self, tmp_path: Path) -> None:
        f = tmp_path / "latin.txt"
        f.write_bytes(b"caf\xe9\n")
        w = Workspace(tmp_path, cache_ttl=0)
        r = read_file(w, "latin.txt")
        assert r.success is True
        assert r.data["encoding"] == "latin-1"

    def test_path_with_spaces(self, tmp_path: Path) -> None:
        f = tmp_path / "my file.txt"
        f.write_text("content", encoding="utf-8")
        w = Workspace(tmp_path, cache_ttl=0)
        r = read_file(w, "my file.txt")
        assert r.success is True


# ===================================================================
# read_range
# ===================================================================


class TestReadRange:
    def test_basic_range(self, ws: Workspace) -> None:
        r = read_range(ws, "multiline.txt", 2, 4)
        assert r.success is True
        assert r.data["start_line"] == 2
        assert r.data["end_line"] == 4
        assert r.data["lines"] == ["B", "C", "D"]
        assert r.data["content"] == "B\nC\nD"

    def test_first_line(self, ws: Workspace) -> None:
        r = read_range(ws, "multiline.txt", 1, 1)
        assert r.success is True
        assert r.data["lines"] == ["A"]

    def test_last_line(self, ws: Workspace) -> None:
        r = read_range(ws, "multiline.txt", 10, 10)
        assert r.success is True
        assert r.data["lines"] == ["J"]

    def test_beyond_eof(self, ws: Workspace) -> None:
        r = read_range(ws, "multiline.txt", 8, 100)
        assert r.success is True
        assert r.data["end_line"] == 10
        assert len(r.data["lines"]) == 3  # H, I, J

    def test_single_line_middle(self, ws: Workspace) -> None:
        r = read_range(ws, "multiline.txt", 5, 5)
        assert r.success is True
        assert r.data["lines"] == ["E"]

    def test_empty_file(self, ws: Workspace) -> None:
        r = read_range(ws, "empty.py", 1, 1)
        assert r.success is True
        assert r.data["lines"] == []

    def test_start_line_less_than_1(self, ws: Workspace) -> None:
        r = read_range(ws, "multiline.txt", 0, 5)
        assert r.success is False
        assert "start_line" in r.error.lower()

    def test_end_less_than_start(self, ws: Workspace) -> None:
        r = read_range(ws, "multiline.txt", 5, 3)
        assert r.success is False
        assert "end_line" in r.error.lower()

    def test_entire_file(self, ws: Workspace) -> None:
        r = read_range(ws, "multiline.txt", 1, 9999)
        assert r.success is True
        assert len(r.data["lines"]) == 10

    def test_binary_file(self, ws: Workspace) -> None:
        r = read_range(ws, "photo.png", 1, 5)
        assert r.success is False
        assert "binary" in r.error.lower()

    def test_sandbox_violation(self, ws: Workspace) -> None:
        r = read_range(ws, "../secret.txt", 1, 5)
        assert r.success is False

    def test_nonexistent(self, ws: Workspace) -> None:
        r = read_range(ws, "nope.txt", 1, 5)
        assert r.success is False
        assert "not found" in r.error.lower()


# ===================================================================
# read_symbol — Python
# ===================================================================


class TestReadSymbolPython:
    def test_function(self, ws: Workspace) -> None:
        r = read_symbol(ws, "hello.py", "greet")
        assert r.success is True
        assert r.data["symbol"] == "greet"
        assert r.data["kind"] == "function"
        assert "def greet" in r.data["content"]
        assert r.data["start_line"] >= 1
        assert r.data["end_line"] >= r.data["start_line"]

    def test_async_function(self, ws: Workspace) -> None:
        r = read_symbol(ws, "hello.py", "async_greet")
        assert r.success is True
        assert r.data["kind"] == "async_function"

    def test_class(self, ws: Workspace) -> None:
        r = read_symbol(ws, "hello.py", "Greeter")
        assert r.success is True
        assert r.data["kind"] == "class"
        assert "class Greeter" in r.data["content"]
        # Class body should include methods
        assert "__init__" in r.data["content"]
        assert "say" in r.data["content"]

    def test_symbol_not_found(self, ws: Workspace) -> None:
        r = read_symbol(ws, "hello.py", "nonexistent")
        assert r.success is False
        assert "not found" in r.error.lower()

    def test_binary_file(self, ws: Workspace) -> None:
        r = read_symbol(ws, "photo.png", "greet")
        assert r.success is False
        assert "binary" in r.error.lower()

    def test_empty_file(self, ws: Workspace) -> None:
        r = read_symbol(ws, "empty.py", "greet")
        assert r.success is False
        assert "empty" in r.error.lower()

    def test_syntax_error(self, tmp_path: Path) -> None:
        (tmp_path / "bad.py").write_text("def foo(:\n  pass\n", encoding="utf-8")
        w = Workspace(tmp_path, cache_ttl=0)
        r = read_symbol(w, "bad.py", "foo")
        assert r.success is False
        assert "syntax" in r.error.lower()


# ===================================================================
# read_symbol — TS/JS
# ===================================================================


class TestReadSymbolTsJs:
    def test_ts_function(self, ts_ws: Workspace) -> None:
        r = read_symbol(ts_ws, "app.ts", "greet")
        assert r.success is True
        assert r.data["kind"] == "function"
        assert "function greet" in r.data["content"]

    def test_ts_class(self, ts_ws: Workspace) -> None:
        r = read_symbol(ts_ws, "app.ts", "MyClass")
        assert r.success is True
        assert r.data["kind"] == "class"

    def test_ts_const(self, ts_ws: Workspace) -> None:
        r = read_symbol(ts_ws, "app.ts", "MY_CONST")
        assert r.success is True
        assert r.data["kind"] == "variable"

    def test_ts_async_function(self, ts_ws: Workspace) -> None:
        r = read_symbol(ts_ws, "app.ts", "fetchData")
        assert r.success is True
        assert r.data["kind"] == "async_function"

    def test_js_function(self, ts_ws: Workspace) -> None:
        r = read_symbol(ts_ws, "index.js", "helper")
        assert r.success is True
        assert r.data["kind"] == "function"

    def test_js_const(self, ts_ws: Workspace) -> None:
        r = read_symbol(ts_ws, "index.js", "config")
        assert r.success is True
        assert r.data["kind"] == "variable"

    def test_symbol_not_found(self, ts_ws: Workspace) -> None:
        r = read_symbol(ts_ws, "app.ts", "nonexistent")
        assert r.success is False
        assert "not found" in r.error.lower()

    def test_unsupported_language(self, ws: Workspace) -> None:
        r = read_symbol(ws, "data.json", "key")
        assert r.success is False
        assert "unsupported" in r.error.lower()


# ===================================================================
# _find_block_end
# ===================================================================


class TestFindBlockEnd:
    def test_simple_braces(self) -> None:
        lines = ["function foo() {", "  return 1;", "}"]
        assert _find_block_end(lines, 0) == 3

    def test_no_braces_semicolon(self) -> None:
        lines = ["const x = 42;"]
        assert _find_block_end(lines, 0) == 1

    def test_nested_braces(self) -> None:
        lines = [
            "class Foo {",
            "  method() {",
            "    return {};",
            "  }",
            "}",
        ]
        assert _find_block_end(lines, 0) == 5

    def test_no_closing(self) -> None:
        lines = ["function foo() {", "  return 1;"]
        # Should return last line
        assert _find_block_end(lines, 0) == 2


# ===================================================================
# Edge cases
# ===================================================================


class TestEdgeCases:
    def test_unicode_content(self, tmp_path: Path) -> None:
        f = tmp_path / "unicode.py"
        f.write_text('msg = "こんにちは"\n', encoding="utf-8")
        w = Workspace(tmp_path, cache_ttl=0)
        r = read_file(w, "unicode.py")
        assert r.success is True
        assert "こんにちは" in r.data["content"]

    def test_mixed_line_endings(self, tmp_path: Path) -> None:
        f = tmp_path / "mixed.txt"
        f.write_bytes(b"line1\r\nline2\nline3\r\n")
        w = Workspace(tmp_path, cache_ttl=0)
        r = read_range(w, "mixed.txt", 1, 3)
        assert r.success is True
        assert len(r.data["lines"]) == 3

    def test_no_trailing_newline(self, tmp_path: Path) -> None:
        f = tmp_path / "no_nl.txt"
        f.write_text("line1\nline2", encoding="utf-8")
        w = Workspace(tmp_path, cache_ttl=0)
        r = read_file(w, "no_nl.txt")
        assert r.success is True
        assert r.data["line_count"] == 2

    def test_read_range_single_line_file(self, tmp_path: Path) -> None:
        f = tmp_path / "one.txt"
        f.write_text("only line", encoding="utf-8")
        w = Workspace(tmp_path, cache_ttl=0)
        r = read_range(w, "one.txt", 1, 1)
        assert r.success is True
        assert r.data["lines"] == ["only line"]

    def test_gitignore_readable(self, tmp_path: Path) -> None:
        f = tmp_path / ".gitignore"
        f.write_text("*.pyc\n", encoding="utf-8")
        w = Workspace(tmp_path, cache_ttl=0)
        r = read_file(w, ".gitignore")
        assert r.success is True
        assert "*.pyc" in r.data["content"]

    def test_binary_null_byte_detection(self, tmp_path: Path) -> None:
        f = tmp_path / "sneaky.txt"
        f.write_bytes(b"looks normal\x00but has null")
        w = Workspace(tmp_path, cache_ttl=0)
        r = read_file(w, "sneaky.txt")
        assert r.success is False
        assert "binary" in r.error.lower()

    def test_custom_max_bytes(self, ws: Workspace) -> None:
        r = read_file(ws, "notes.txt", max_bytes=1_000_000)
        assert r.success is True
