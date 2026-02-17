"""Tests for forge_ide.diagnostics — merge and language detection utilities."""

from __future__ import annotations

from forge_ide.contracts import Diagnostic
from forge_ide.diagnostics import detect_language, merge_diagnostics
from forge_ide.lang import DiagnosticReport


# ═══════════════════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════════════════

def _diag(file: str, severity: str, msg: str = "x", line: int = 1) -> Diagnostic:
    return Diagnostic(file=file, line=line, column=0, message=msg, severity=severity)


# ═══════════════════════════════════════════════════════════════════════════
# merge_diagnostics
# ═══════════════════════════════════════════════════════════════════════════


class TestMergeDiagnostics:
    def test_empty(self):
        report = merge_diagnostics()
        assert isinstance(report, DiagnosticReport)
        assert report.files == {}
        assert report.error_count == 0

    def test_single_list(self):
        diags = [_diag("a.py", "error"), _diag("a.py", "warning")]
        report = merge_diagnostics(diags)
        assert report.error_count == 1
        assert report.warning_count == 1
        assert len(report.files["a.py"]) == 2

    def test_multiple_lists(self):
        list1 = [_diag("a.py", "error")]
        list2 = [_diag("b.py", "warning")]
        report = merge_diagnostics(list1, list2)
        assert report.error_count == 1
        assert report.warning_count == 1
        assert "a.py" in report.files
        assert "b.py" in report.files

    def test_mixed_severities(self):
        diags = [
            _diag("f.py", "error"),
            _diag("f.py", "warning"),
            _diag("f.py", "info"),
            _diag("f.py", "hint"),
        ]
        report = merge_diagnostics(diags)
        assert report.error_count == 1
        assert report.warning_count == 1
        assert report.info_count == 1
        assert report.hint_count == 1

    def test_multiple_files(self):
        diags = [
            _diag("a.py", "error"),
            _diag("b.py", "error"),
            _diag("a.py", "warning"),
        ]
        report = merge_diagnostics(diags)
        assert len(report.files) == 2
        assert len(report.files["a.py"]) == 2
        assert len(report.files["b.py"]) == 1

    def test_empty_list(self):
        report = merge_diagnostics([])
        assert report.error_count == 0
        assert report.files == {}

    def test_counts_across_lists(self):
        list1 = [_diag("a.py", "error"), _diag("a.py", "error")]
        list2 = [_diag("a.py", "error")]
        report = merge_diagnostics(list1, list2)
        assert report.error_count == 3

    def test_frozen(self):
        report = merge_diagnostics()
        import pytest
        with pytest.raises(Exception):
            report.error_count = 99  # type: ignore[misc]


# ═══════════════════════════════════════════════════════════════════════════
# detect_language
# ═══════════════════════════════════════════════════════════════════════════


class TestDetectLanguage:
    def test_python(self):
        assert detect_language("app/main.py") == "python"

    def test_python_pyi(self):
        assert detect_language("stubs/types.pyi") == "python"

    def test_typescript(self):
        assert detect_language("src/app.ts") == "typescript"

    def test_tsx(self):
        assert detect_language("component.tsx") == "typescriptreact"

    def test_javascript(self):
        assert detect_language("index.js") == "javascript"

    def test_jsx(self):
        assert detect_language("App.jsx") == "javascriptreact"

    def test_json(self):
        assert detect_language("package.json") == "json"

    def test_markdown(self):
        assert detect_language("README.md") == "markdown"

    def test_unknown(self):
        assert detect_language("file.xyz123") == "unknown"

    def test_no_extension(self):
        assert detect_language("Makefile") == "unknown"

    def test_sql(self):
        assert detect_language("migrations/001.sql") == "sql"

    def test_yaml(self):
        assert detect_language("config.yaml") == "yaml"
        assert detect_language("config.yml") == "yaml"

    def test_case_insensitive(self):
        assert detect_language("README.MD") == "markdown"
        assert detect_language("App.PY") == "python"

    def test_nested_path(self):
        assert detect_language("a/b/c/deep.ts") == "typescript"
