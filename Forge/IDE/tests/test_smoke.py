"""End-to-end smoke tests for the forge_ide package.

These tests exercise the full IDE stack with real filesystem I/O inside
temporary directories.  They verify that the major subsystems work
together, not just in isolation, and validate the kind of operations
the build loop performs during a real build.

Run with:
    python -m pytest Forge/IDE/tests/test_smoke.py -v

Each test creates a temporary workspace, performs a realistic sequence
of operations, and asserts that the results are structurally correct.
"""

from __future__ import annotations

import asyncio
import os
import textwrap
from pathlib import Path

import pytest

# ── Workspace + Reader + Searcher ────────────────────────────────────────


class TestWorkspaceReaderSearcher:
    """Smoke: workspace → read file → search → results are consistent."""

    @pytest.fixture()
    def workspace_dir(self, tmp_path: Path) -> Path:
        """Create a small toy project on disk."""
        (tmp_path / "src").mkdir()
        (tmp_path / "tests").mkdir()
        (tmp_path / "src" / "main.py").write_text(
            textwrap.dedent("""\
                import os
                import sys

                def greet(name: str) -> str:
                    return f"Hello, {name}!"

                class App:
                    def run(self) -> None:
                        print(greet("world"))
            """),
            encoding="utf-8",
        )
        (tmp_path / "src" / "utils.py").write_text(
            textwrap.dedent("""\
                def add(a: int, b: int) -> int:
                    return a + b

                def multiply(a: int, b: int) -> int:
                    return a * b
            """),
            encoding="utf-8",
        )
        (tmp_path / "tests" / "test_main.py").write_text(
            textwrap.dedent("""\
                from src.main import greet

                def test_greet():
                    assert greet("world") == "Hello, world!"
            """),
            encoding="utf-8",
        )
        (tmp_path / "README.md").write_text("# Test Project\n", encoding="utf-8")
        return tmp_path

    def test_workspace_file_tree(self, workspace_dir: Path) -> None:
        from forge_ide import Workspace

        ws = Workspace(str(workspace_dir))
        tree = ws.file_tree()
        paths = [e.path for e in tree]
        assert "src/main.py" in paths
        assert "src/utils.py" in paths
        assert "tests/test_main.py" in paths
        assert "README.md" in paths

    def test_workspace_summary(self, workspace_dir: Path) -> None:
        from forge_ide import Workspace

        ws = Workspace(str(workspace_dir))
        summary = ws.workspace_summary()
        assert summary.file_count >= 4
        assert summary.total_size_bytes > 0
        assert "python" in summary.languages

    def test_reader_full_file(self, workspace_dir: Path) -> None:
        from forge_ide import Workspace, ide_read_file

        ws = Workspace(str(workspace_dir))
        resp = ide_read_file(ws, "src/main.py")
        assert resp.success is True
        assert "def greet" in resp.data["content"]
        assert resp.data["language"] == "python"

    def test_reader_range(self, workspace_dir: Path) -> None:
        from forge_ide import Workspace, read_range

        ws = Workspace(str(workspace_dir))
        resp = read_range(ws, "src/main.py", start_line=4, end_line=5)
        assert resp.success is True
        assert "greet" in resp.data["content"]

    def test_reader_symbol(self, workspace_dir: Path) -> None:
        from forge_ide import Workspace, read_symbol

        ws = Workspace(str(workspace_dir))
        resp = read_symbol(ws, "src/main.py", "App")
        assert resp.success is True
        assert "class App" in resp.data["content"]

    def test_search_literal(self, workspace_dir: Path) -> None:
        from forge_ide import Workspace, search

        ws = Workspace(str(workspace_dir))
        resp = asyncio.get_event_loop().run_until_complete(
            search(ws, "greet")
        )
        assert resp.success is True
        assert resp.data["total_count"] >= 2  # defined + imported

    def test_search_regex(self, workspace_dir: Path) -> None:
        from forge_ide import Workspace, search

        ws = Workspace(str(workspace_dir))
        resp = asyncio.get_event_loop().run_until_complete(
            search(ws, r"def \w+\(", is_regex=True)
        )
        assert resp.success is True
        assert resp.data["total_count"] >= 3  # greet, add, multiply


# ── File Index + Relevance + Context Pack ────────────────────────────────


class TestFileIndexRelevanceContextPack:
    """Smoke: build index → score relevance → assemble context pack."""

    @pytest.fixture()
    def workspace_dir(self, tmp_path: Path) -> Path:
        (tmp_path / "app").mkdir()
        (tmp_path / "app" / "__init__.py").write_text("", encoding="utf-8")
        (tmp_path / "app" / "main.py").write_text(
            textwrap.dedent("""\
                from app.helpers import sanitise

                def handler():
                    return sanitise("data")
            """),
            encoding="utf-8",
        )
        (tmp_path / "app" / "helpers.py").write_text(
            textwrap.dedent("""\
                def sanitise(text: str) -> str:
                    return text.strip()
            """),
            encoding="utf-8",
        )
        (tmp_path / "tests").mkdir()
        (tmp_path / "tests" / "test_main.py").write_text(
            textwrap.dedent("""\
                from app.main import handler

                def test_handler():
                    assert handler() == "data"
            """),
            encoding="utf-8",
        )
        return tmp_path

    def test_file_index_builds(self, workspace_dir: Path) -> None:
        from forge_ide import FileIndex, Workspace

        ws = Workspace(str(workspace_dir))
        idx = FileIndex.build(ws)
        all_files = idx.all_files()
        assert "app/main.py" in all_files
        assert "app/helpers.py" in all_files

    def test_file_index_imports(self, workspace_dir: Path) -> None:
        from forge_ide import FileIndex, Workspace

        ws = Workspace(str(workspace_dir))
        idx = FileIndex.build(ws)
        imports = idx.get_imports("app/main.py")
        # Should contain the 'app.helpers' import
        assert any("helpers" in imp for imp in imports)

    def test_relevance_scoring(self, workspace_dir: Path) -> None:
        from forge_ide import FileIndex, Workspace, find_related

        ws = Workspace(str(workspace_dir))
        idx = FileIndex.build(ws)
        related = find_related(
            target_path="app/main.py",
            all_files=list(idx._index.values()),
            imports=idx._import_graph,
            importers=idx._reverse_graph,
        )
        # helpers.py should be highly relevant (imported directly)
        related_paths = [r.path for r in related]
        assert "app/helpers.py" in related_paths

    def test_context_pack_assembly(self, workspace_dir: Path) -> None:
        from forge_ide import (
            ContextPack,
            TargetFile,
            Workspace,
            assemble_pack,
            build_repo_summary,
            ide_read_file,
            pack_to_text,
        )

        ws = Workspace(str(workspace_dir))
        tree = ws.file_tree()
        ws_summary = ws.workspace_summary()
        paths = [e.path for e in tree]
        summary = build_repo_summary(
            file_count=ws_summary.file_count,
            languages=ws_summary.languages,
            files=paths,
        )
        assert summary.file_count >= 4

        resp = ide_read_file(ws, "app/main.py")
        target = TargetFile(
            path="app/main.py",
            content=resp.data["content"],
            language="python",
        )
        pack = assemble_pack(
            target_files=[target],
            repo_summary=summary,
            budget_tokens=4000,
        )
        assert isinstance(pack, ContextPack)
        assert len(pack.target_files) == 1
        assert pack.target_files[0].path == "app/main.py"

        text = pack_to_text(pack)
        assert "app/main.py" in text
        assert "handler" in text


# ── Patcher + Diff Generator ────────────────────────────────────────────


class TestPatcherDiffRoundTrip:
    """Smoke: generate diff → parse → apply patch → get expected result."""

    def test_diff_apply_roundtrip(self) -> None:
        from forge_ide import (
            apply_response,
            diff_to_text,
            generate_diff,
        )

        old = "line1\nline2\nline3\nline4\n"
        new = "line1\nline2_modified\nline3\nline4\nline5\n"

        diff = generate_diff(old, new, path="test.py")
        assert diff.insertions >= 1

        # Apply the diff text via apply_response (handles parse + patch)
        text = diff_to_text(diff)
        result = apply_response(old, text)
        assert result.method == "patch"
        assert result.content == new

    def test_multi_diff(self) -> None:
        from forge_ide import generate_multi_diff

        changes = [
            {"path": "a.py", "old": "x\n", "new": "y\n"},
            {"path": "b.py", "old": "1\n", "new": "2\n"},
        ]
        diffs = generate_multi_diff(changes)
        assert len(diffs) == 2
        assert diffs[0].path == "a.py"
        assert diffs[1].path == "b.py"

    def test_backslash_paths_normalised(self) -> None:
        from forge_ide import generate_diff

        diff = generate_diff("a\n", "b\n", path=r"src\utils\file.py")
        assert "\\" not in diff.path
        assert diff.path == "src/utils/file.py"


# ── Response Parser + Build Helpers ──────────────────────────────────────


class TestResponseParserBuildHelpers:
    """Smoke: parse LLM response → apply to source → verify result."""

    def test_full_content_response(self) -> None:
        from forge_ide import apply_response

        original = "def old():\n    pass\n"
        llm_output = "def new_function():\n    return 42\n"

        result = apply_response(original, llm_output)
        assert result.method == "full"
        assert "new_function" in result.content

    def test_diff_response(self) -> None:
        from forge_ide import apply_response

        original = "line1\nline2\nline3\n"
        diff_text = textwrap.dedent("""\
            --- a/file.py
            +++ b/file.py
            @@ -1,3 +1,3 @@
             line1
            -line2
            +line2_changed
             line3
        """)

        result = apply_response(original, diff_text)
        assert result.method == "patch"
        assert "line2_changed" in result.content

    def test_fenced_response(self) -> None:
        from forge_ide import apply_response

        original = "old\n"
        llm_output = "```python\nnew_code\n```"

        result = apply_response(original, llm_output)
        assert "new_code" in result.content


# ── Log Parsers ──────────────────────────────────────────────────────────


class TestLogParsers:
    """Smoke: auto_summarise picks the right parser."""

    def test_pytest_output(self) -> None:
        from forge_ide import auto_summarise
        from forge_ide.runner import RunResult

        stdout = (
            "============================= test session starts "
            "=============================\n"
            "collected 10 items\n\n"
            "tests/test_a.py ..........  [100%]\n\n"
            "============================== 10 passed in 1.23s "
            "==============================\n"
        )
        result = RunResult(
            exit_code=0,
            stdout=stdout,
            stderr="",
            command="pytest tests/ -v",
            killed=False,
            truncated=False,
        )
        summary = auto_summarise(result)
        assert summary.passed == 10
        assert summary.failed == 0

    def test_npm_vitest_output(self) -> None:
        from forge_ide import auto_summarise
        from forge_ide.runner import RunResult

        stdout = (
            " Tests  2 failed | 8 passed (10)\n"
            "  Time  3.45s\n"
        )
        result = RunResult(
            exit_code=1,
            stdout=stdout,
            stderr="",
            command="npm test",
            killed=False,
            truncated=False,
        )
        summary = auto_summarise(result)
        assert summary.passed == 8
        assert summary.failed == 2


# ── Language Intelligence ────────────────────────────────────────────────


class TestLanguageIntelligence:
    """Smoke: extract symbols and resolve imports from real code."""

    def test_extract_python_symbols(self) -> None:
        from forge_ide import extract_python_symbols

        code = textwrap.dedent("""\
            import os

            MAX_SIZE = 100

            def process(data: str) -> str:
                return data.strip()

            class Processor:
                def run(self) -> None:
                    pass
        """)
        symbols = extract_python_symbols(code)
        names = [s.name for s in symbols]
        assert "MAX_SIZE" in names
        assert "process" in names
        assert "Processor" in names
        assert "run" in names

    def test_resolve_python_imports(self, tmp_path: Path) -> None:
        from forge_ide import Workspace, resolve_python_imports

        (tmp_path / "mymod").mkdir()
        (tmp_path / "mymod" / "__init__.py").write_text("", encoding="utf-8")
        (tmp_path / "mymod" / "core.py").write_text("x = 1\n", encoding="utf-8")

        code = "from mymod.core import x\nimport os\n"
        ws = Workspace(str(tmp_path))
        ws_files = [e.path for e in ws.file_tree()]
        imports = resolve_python_imports(code, "main.py", ws_files)

        stdlib_imports = [i for i in imports if i.is_stdlib]
        workspace_imports = [i for i in imports if i.resolved_path]
        assert len(stdlib_imports) >= 1  # os
        assert len(workspace_imports) >= 1  # mymod.core

    def test_parse_ruff_json(self) -> None:
        import json
        from forge_ide import parse_ruff_json

        ruff_output = json.dumps([
            {
                "code": "F401",
                "message": "os imported but unused",
                "filename": "src/main.py",
                "location": {"row": 1, "column": 1},
                "end_location": {"row": 1, "column": 10},
            },
        ])
        diagnostics = parse_ruff_json(ruff_output)
        assert len(diagnostics) == 1
        assert diagnostics[0].code == "F401"

    def test_diagnostics_merge(self) -> None:
        from forge_ide import Diagnostic, merge_diagnostics

        d1 = [Diagnostic(file="a.py", line=1, column=0,
                         message="err", severity="error")]
        d2 = [Diagnostic(file="b.py", line=5, column=0,
                         message="warn", severity="warning")]
        report = merge_diagnostics(d1, d2)
        assert report.error_count == 1
        assert report.warning_count == 1
        assert "a.py" in report.files
        assert "b.py" in report.files


# ── Redactor + Sanitiser ────────────────────────────────────────────────


class TestRedactorSanitiser:
    """Smoke: secrets are caught, output is deterministic."""

    def test_redact_api_key(self) -> None:
        from forge_ide import REDACTED, redact

        text = f"key=sk-{'A' * 30}"
        result = redact(text)
        assert REDACTED in result
        assert "sk-" not in result

    def test_sanitise_full_pipeline(self) -> None:
        from forge_ide import sanitise_output

        text = (
            "2025-01-15T10:00:00Z pid=1234 "
            "wrote /tmp/pytest-xyz/out "
            "at /project/src/main.py"
        )
        result = sanitise_output(text, workspace_root="/project")
        assert "[timestamp]" in result
        assert "[pid]" in result
        assert "[tmpdir]" in result
        assert result.endswith("src/main.py")

    def test_sort_determinism(self) -> None:
        from forge_ide import sort_file_list

        paths = ["z.py", "a.py", "M.py"]
        result1 = sort_file_list(paths)
        result2 = sort_file_list(list(reversed(paths)))
        assert result1 == result2  # same order regardless of input order


# ── Backoff + Concurrency ───────────────────────────────────────────────


class TestBackoffConcurrency:
    """Smoke: backoff iterator and concurrency limiter work."""

    def test_exponential_backoff_sequence(self) -> None:
        from forge_ide import ExponentialBackoff

        bo = ExponentialBackoff(initial_s=1.0, max_s=10.0, jitter=False)
        delays = [next(bo) for _ in range(4)]
        assert delays == [1.0, 2.0, 4.0, 8.0]

    def test_concurrency_limiter(self) -> None:
        from forge_ide import ConcurrencyLimiter

        limiter = ConcurrencyLimiter(max_concurrent=2)
        assert limiter.max_concurrent == 2
        assert limiter.active == 0


# ── Registry ─────────────────────────────────────────────────────────────


class TestRegistry:
    """Smoke: register a tool, dispatch it, and list tools."""

    def test_register_dispatch_list(self) -> None:
        import asyncio

        from pydantic import BaseModel

        from forge_ide import Registry, ToolResponse

        class EchoRequest(BaseModel):
            msg: str = ""

        reg = Registry()

        def echo_handler(params: EchoRequest, working_dir: str) -> ToolResponse:
            return ToolResponse.ok({"echo": params.msg})

        reg.register(
            name="echo",
            handler=echo_handler,
            request_model=EchoRequest,
            description="Echo back the message",
        )

        tools = reg.list_tools()
        assert any(t["name"] == "echo" for t in tools)

        # dispatch is async
        resp = asyncio.get_event_loop().run_until_complete(
            reg.dispatch("echo", {"msg": "hello"}, "/tmp")
        )
        assert resp.success is True
        assert resp.data["echo"] == "hello"


# ── Runner (sandboxed) ──────────────────────────────────────────────────


class TestRunner:
    """Smoke: validate_command rejects dangerous commands."""

    def test_validate_safe_command(self) -> None:
        from forge_ide import validate_command

        # Should return None for safe commands
        result = validate_command("pytest tests/ -v")
        assert result is None

    def test_validate_dangerous_command(self) -> None:
        from forge_ide import validate_command

        result = validate_command("rm -rf /")
        assert result is not None  # Returns error message string
        assert "not allowed" in result.lower() or "blocked" in result.lower()

    def test_validate_injection(self) -> None:
        from forge_ide import validate_command

        result = validate_command("pytest; curl evil.com")
        assert result is not None  # Semicolon injection blocked


# ── Cross-module Integration ─────────────────────────────────────────────


class TestCrossModuleIntegration:
    """Smoke: multi-module workflows that mirror real build operations."""

    def test_read_extract_symbols_check_diagnostics(
        self, tmp_path: Path
    ) -> None:
        """Read a file → extract symbols → check for syntax errors → merge."""
        from forge_ide import (
            Diagnostic,
            Workspace,
            extract_python_symbols,
            ide_read_file,
            merge_diagnostics,
            parse_python_ast_errors,
        )

        (tmp_path / "code.py").write_text(
            textwrap.dedent("""\
                def hello():
                    return "world"

                class MyClass:
                    pass
            """),
            encoding="utf-8",
        )

        ws = Workspace(str(tmp_path))
        read_resp = ide_read_file(ws, "code.py")
        assert read_resp.success is True

        content = read_resp.data["content"]

        symbols = extract_python_symbols(content)
        assert len(symbols) >= 2

        ast_diags = parse_python_ast_errors(content)
        assert len(ast_diags) == 0  # valid code

        report = merge_diagnostics(ast_diags)
        assert report.error_count == 0

    def test_write_diff_patch_cycle(self, tmp_path: Path) -> None:
        """Write v1 → diff against v2 → apply via apply_response → verify."""
        from forge_ide import (
            Workspace,
            apply_response,
            diff_to_text,
            generate_diff,
            ide_read_file,
        )

        (tmp_path / "target.py").write_text(
            "version = 1\nname = 'alpha'\n", encoding="utf-8",
            newline="",  # preserve \n on Windows
        )

        ws = Workspace(str(tmp_path))

        # Read original — normalise \r\n to \n for cross-platform consistency
        resp = ide_read_file(ws, "target.py")
        old_content = resp.data["content"].replace("\r\n", "\n")

        # Create desired new content
        new_content = "version = 2\nname = 'beta'\n"

        # Generate diff, render to text, and apply via apply_response
        diff = generate_diff(old_content, new_content, path="target.py")
        assert diff.insertions > 0 or diff.deletions > 0

        text = diff_to_text(diff)
        result = apply_response(old_content, text)
        assert result.method == "patch"
        assert result.content == new_content

    def test_context_pack_with_relevance(self, tmp_path: Path) -> None:
        """Build full context pack using relevance scoring."""
        from forge_ide import (
            FileIndex,
            TargetFile,
            Workspace,
            assemble_pack,
            build_repo_summary,
            estimate_tokens,
            ide_read_file,
            pack_to_text,
        )

        # Create a small project
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "api.py").write_text(
            "from src.db import query\n\ndef get_users():\n    return query('users')\n",
            encoding="utf-8",
        )
        (tmp_path / "src" / "db.py").write_text(
            "def query(table: str):\n    return []\n",
            encoding="utf-8",
        )
        (tmp_path / "src" / "__init__.py").write_text("", encoding="utf-8")

        ws = Workspace(str(tmp_path))
        ws_summary = ws.workspace_summary()
        paths = [e.path for e in ws.file_tree()]
        summary = build_repo_summary(
            file_count=ws_summary.file_count,
            languages=ws_summary.languages,
            files=paths,
        )

        resp = ide_read_file(ws, "src/api.py")
        target = TargetFile(
            path="src/api.py",
            content=resp.data["content"],
            language="python",
        )
        pack = assemble_pack(
            target_files=[target],
            repo_summary=summary,
            budget_tokens=2000,
        )

        text = pack_to_text(pack)
        tokens = estimate_tokens(text)
        assert tokens <= 2000
        assert "api.py" in text
