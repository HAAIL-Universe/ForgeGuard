"""Tests for forge_ide.context_pack — context pack assembly and token estimation."""

from __future__ import annotations

import pytest

from forge_ide.contracts import Diagnostic, Snippet
from forge_ide.context_pack import (
    ContextPack,
    DependencySnippet,
    RepoSummary,
    TargetFile,
    assemble_pack,
    build_repo_summary,
    build_structure_tree,
    estimate_tokens,
    pack_to_text,
)
from forge_ide.lang import DiagnosticReport


# ===================================================================
# estimate_tokens
# ===================================================================


class TestEstimateTokens:
    def test_empty_string(self):
        assert estimate_tokens("") == 0

    def test_short_text(self):
        assert estimate_tokens("hi") == 1  # 2 chars // 4 = 0, max(1, 0) = 1

    def test_exact_four(self):
        assert estimate_tokens("abcd") == 1

    def test_long_text(self):
        text = "x" * 400
        assert estimate_tokens(text) == 100

    def test_consistent(self):
        """Same input always same output."""
        t = "hello world"
        assert estimate_tokens(t) == estimate_tokens(t)


# ===================================================================
# build_structure_tree
# ===================================================================


class TestBuildStructureTree:
    def test_empty(self):
        assert build_structure_tree([]) == ""

    def test_flat_files(self):
        result = build_structure_tree(["a.py", "b.py", "c.py"])
        assert "a.py" in result
        assert "b.py" in result

    def test_nested_dirs(self):
        result = build_structure_tree(["src/a.py", "src/sub/b.py"])
        assert "src/" in result

    def test_max_depth_zero(self):
        result = build_structure_tree(["src/sub/deep/a.py"], max_depth=0)
        # All paths flattened to root level
        assert result != ""

    def test_max_depth_truncation(self):
        files = [f"a/b/c/d/file{i}.py" for i in range(3)]
        result = build_structure_tree(files, max_depth=2)
        assert result != ""

    def test_many_files_truncated(self):
        """More than 5 files in one dir shows '…and N more'."""
        files = [f"src/file{i}.py" for i in range(10)]
        result = build_structure_tree(files)
        assert "more" in result

    def test_backslash_normalised(self):
        result = build_structure_tree(["src\\a.py", "src\\b.py"])
        assert "src/" in result


# ===================================================================
# build_repo_summary
# ===================================================================


class TestBuildRepoSummary:
    def test_normal(self):
        s = build_repo_summary(10, {"python": 8, "typescript": 2}, ["a.py", "b.ts"])
        assert s.file_count == 10
        assert s.languages["python"] == 8
        assert s.structure_tree != ""

    def test_empty(self):
        s = build_repo_summary(0, {}, [])
        assert s.file_count == 0
        assert s.structure_tree == ""


# ===================================================================
# Model freezing
# ===================================================================


class TestModelsFrozen:
    def test_target_file_frozen(self):
        tf = TargetFile(path="a.py", content="pass")
        with pytest.raises(Exception):
            tf.path = "b.py"  # type: ignore[misc]

    def test_dependency_snippet_frozen(self):
        ds = DependencySnippet(path="a.py", content="x", why="test")
        with pytest.raises(Exception):
            ds.path = "b.py"  # type: ignore[misc]

    def test_repo_summary_frozen(self):
        rs = RepoSummary(file_count=5)
        with pytest.raises(Exception):
            rs.file_count = 10  # type: ignore[misc]

    def test_context_pack_frozen(self):
        cp = ContextPack()
        with pytest.raises(Exception):
            cp.test_output = "x"  # type: ignore[misc]


# ===================================================================
# assemble_pack
# ===================================================================


def _diag(f: str, line: int, sev: str = "error", msg: str = "err") -> Diagnostic:
    return Diagnostic(file=f, line=line, column=0, message=msg, severity=sev)


def _snippet(path: str, content: str) -> Snippet:
    return Snippet(path=path, start_line=1, end_line=1, content=content)


class TestAssemblePack:
    def test_unlimited_budget(self):
        """Budget 0 means no trimming."""
        pack = assemble_pack(
            target_files=[TargetFile(path="a.py", content="x" * 100)],
            dependency_snippets=[DependencySnippet(path="b.py", content="y" * 100, why="dep")],
            related_snippets=[_snippet("c.py", "z" * 100)],
            test_output="output",
            git_diff_summary="diff",
            budget_tokens=0,
        )
        assert len(pack.dependency_snippets) == 1
        assert len(pack.related_snippets) == 1
        assert pack.test_output == "output"
        assert pack.git_diff_summary == "diff"
        assert pack.token_estimate > 0

    def test_budget_trims_related(self):
        """Tight budget drops related snippets."""
        big_snippet = _snippet("big.py", "x" * 10000)
        pack = assemble_pack(
            target_files=[TargetFile(path="a.py", content="small")],
            related_snippets=[big_snippet],
            budget_tokens=50,  # very tight
        )
        # Big snippet should be dropped
        assert len(pack.related_snippets) == 0

    def test_budget_trims_test_output(self):
        """Test output dropped if budget is tight."""
        pack = assemble_pack(
            target_files=[TargetFile(path="a.py", content="sm")],
            test_output="x" * 10000,
            budget_tokens=50,
        )
        assert pack.test_output == ""

    def test_budget_trims_git_diff(self):
        """Git diff dropped if budget is tight."""
        pack = assemble_pack(
            target_files=[TargetFile(path="a.py", content="sm")],
            git_diff_summary="x" * 10000,
            budget_tokens=50,
        )
        assert pack.git_diff_summary == ""

    def test_all_sections_present(self):
        """All sections included when budget allows."""
        report = DiagnosticReport(
            files={"a.py": [_diag("a.py", 1)]}, error_count=1
        )
        pack = assemble_pack(
            target_files=[TargetFile(path="a.py", content="hi")],
            dependency_snippets=[DependencySnippet(path="b.py", content="dep", why="used")],
            related_snippets=[_snippet("c.py", "rel")],
            repo_summary=RepoSummary(file_count=5),
            diagnostics_summary=report,
            test_output="PASSED",
            git_diff_summary="+line",
            budget_tokens=0,
        )
        assert pack.repo_summary.file_count == 5
        assert len(pack.target_files) == 1
        assert len(pack.dependency_snippets) == 1
        assert len(pack.related_snippets) == 1
        assert pack.diagnostics_summary is not None
        assert pack.test_output == "PASSED"
        assert pack.git_diff_summary == "+line"

    def test_empty_pack(self):
        pack = assemble_pack()
        assert pack.token_estimate >= 0
        assert pack.target_files == []

    def test_token_estimate_set(self):
        pack = assemble_pack(
            target_files=[TargetFile(path="a.py", content="x" * 400)],
            budget_tokens=0,
        )
        assert pack.token_estimate > 0

    def test_negative_budget_treated_as_unlimited(self):
        pack = assemble_pack(
            target_files=[TargetFile(path="a.py", content="x" * 100)],
            related_snippets=[_snippet("b.py", "y" * 100)],
            budget_tokens=-1,
        )
        assert len(pack.related_snippets) == 1

    def test_deps_kept_before_related(self):
        """Dependencies have higher priority than related snippets."""
        pack = assemble_pack(
            target_files=[TargetFile(path="a.py", content="sm")],
            dependency_snippets=[DependencySnippet(path="dep.py", content="d" * 400, why="core")],
            related_snippets=[_snippet("rel.py", "r" * 400)],
            budget_tokens=150,  # tight — core ~10 + dep ~100 fills budget; no room for related
        )
        assert len(pack.dependency_snippets) == 1
        assert len(pack.related_snippets) == 0


# ===================================================================
# pack_to_text
# ===================================================================


class TestPackToText:
    def test_empty_pack(self):
        text = pack_to_text(ContextPack())
        assert isinstance(text, str)

    def test_includes_repo_summary(self):
        pack = ContextPack(repo_summary=RepoSummary(file_count=42, languages={"python": 30}))
        text = pack_to_text(pack)
        assert "42 files" in text
        assert "python" in text

    def test_includes_target(self):
        pack = ContextPack(
            target_files=[TargetFile(path="main.py", content="print('hi')")]
        )
        text = pack_to_text(pack)
        assert "main.py" in text
        assert "print('hi')" in text

    def test_includes_diagnostics_in_target(self):
        pack = ContextPack(
            target_files=[
                TargetFile(
                    path="a.py",
                    content="x",
                    diagnostics=[_diag("a.py", 5, "warning", "unused var")],
                )
            ]
        )
        text = pack_to_text(pack)
        assert "unused var" in text

    def test_includes_deps(self):
        pack = ContextPack(
            dependency_snippets=[DependencySnippet(path="util.py", content="fn()", why="helper")]
        )
        text = pack_to_text(pack)
        assert "util.py" in text
        assert "helper" in text

    def test_includes_related(self):
        pack = ContextPack(
            related_snippets=[_snippet("related.py", "code")]
        )
        text = pack_to_text(pack)
        assert "related.py" in text

    def test_includes_diag_summary(self):
        report = DiagnosticReport(
            files={"x.py": [_diag("x.py", 10, "error", "syntax error")]},
            error_count=1,
        )
        pack = ContextPack(diagnostics_summary=report)
        text = pack_to_text(pack)
        assert "syntax error" in text

    def test_includes_test_output(self):
        pack = ContextPack(test_output="3 passed, 1 failed")
        text = pack_to_text(pack)
        assert "3 passed, 1 failed" in text

    def test_includes_git_diff(self):
        pack = ContextPack(git_diff_summary="+new_line\n-old_line")
        text = pack_to_text(pack)
        assert "+new_line" in text

    def test_full_pack_renders(self):
        """A fully populated pack renders without errors."""
        report = DiagnosticReport(
            files={"a.py": [_diag("a.py", 1)]}, error_count=1
        )
        pack = ContextPack(
            repo_summary=RepoSummary(file_count=10, languages={"python": 10}),
            target_files=[TargetFile(path="a.py", content="code")],
            dependency_snippets=[DependencySnippet(path="b.py", content="dep", why="ref")],
            related_snippets=[_snippet("c.py", "rel")],
            diagnostics_summary=report,
            test_output="ok",
            git_diff_summary="diff",
            token_estimate=500,
        )
        text = pack_to_text(pack)
        assert len(text) > 0
        assert "Repository" in text
        assert "Target" in text
