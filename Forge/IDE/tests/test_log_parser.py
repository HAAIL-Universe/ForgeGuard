"""Tests for forge_ide.log_parser — deterministic log parsers."""

from __future__ import annotations

import pytest

from forge_ide.log_parser import (
    BuildIssue,
    BuildSummary,
    GenericSummary,
    NpmTestSummary,
    PytestSummary,
    TestFailure,
    auto_summarise,
    detect_parser,
    summarise_build,
    summarise_generic,
    summarise_npm_test,
    summarise_pytest,
)
from forge_ide.runner import RunResult


# ═══════════════════════════════════════════════════════════════════════════
# Models
# ═══════════════════════════════════════════════════════════════════════════


class TestTestFailure:
    def test_minimal(self):
        f = TestFailure(test_name="test_foo")
        assert f.test_name == "test_foo"
        assert f.file == ""
        assert f.line == 0
        assert f.message == ""

    def test_full(self):
        f = TestFailure(test_name="test_it", file="t.py", line=10, message="nope")
        assert f.file == "t.py"
        assert f.line == 10

    def test_frozen(self):
        f = TestFailure(test_name="t")
        with pytest.raises(Exception):
            f.test_name = "x"  # type: ignore[misc]


class TestPytestSummaryModel:
    def test_defaults(self):
        s = PytestSummary()
        assert s.total == 0
        assert s.failures == []
        assert s.collection_errors == []

    def test_frozen(self):
        s = PytestSummary()
        with pytest.raises(Exception):
            s.total = 5  # type: ignore[misc]


class TestBuildIssue:
    def test_error(self):
        i = BuildIssue(file="a.py", line=5, message="bad", severity="error")
        assert i.severity == "error"

    def test_warning(self):
        i = BuildIssue(message="warn", severity="warning")
        assert i.file == ""


class TestGenericSummaryModel:
    def test_defaults(self):
        s = GenericSummary()
        assert s.line_count == 0
        assert s.truncated is False


# ═══════════════════════════════════════════════════════════════════════════
# summarise_pytest
# ═══════════════════════════════════════════════════════════════════════════


class TestSummarisePytest:
    def test_all_pass(self):
        output = """\
============================= test session starts =============================
collected 10 items

tests/test_foo.py ..........                                             [100%]

============================== 10 passed in 2.50s ==============================
"""
        s = summarise_pytest(output)
        assert s.passed == 10
        assert s.failed == 0
        assert s.errors == 0
        assert s.total == 10
        assert s.duration_s == pytest.approx(2.5)
        assert s.failures == []

    def test_mixed_results(self):
        output = """\
============================= test session starts =============================
collected 12 items

tests/test_a.py ........                                                 [ 66%]
tests/test_b.py ..FF                                                     [100%]

=========================== short test summary info ===========================
FAILED tests/test_b.py::test_one - AssertionError: 1 != 2
FAILED tests/test_b.py::test_two - ValueError: bad
========================= 2 failed, 10 passed in 5.30s =========================
"""
        s = summarise_pytest(output)
        assert s.passed == 10
        assert s.failed == 2
        assert s.total == 12
        assert s.duration_s == pytest.approx(5.3)
        assert len(s.failures) == 2
        assert s.failures[0].test_name == "test_one"
        assert s.failures[0].file == "tests/test_b.py"
        assert "1 != 2" in s.failures[0].message
        assert s.failures[1].test_name == "test_two"

    def test_all_fail(self):
        output = """\
FAILED tests/test_x.py::test_a - assert False
FAILED tests/test_x.py::test_b - RuntimeError
====================== 2 failed in 0.10s ======================
"""
        s = summarise_pytest(output)
        assert s.failed == 2
        assert s.passed == 0
        assert len(s.failures) == 2

    def test_collection_errors(self):
        output = """\
============================= test session starts =============================
ERROR collecting tests/test_broken.py
E   SyntaxError: invalid syntax
ERROR collecting tests/test_bad.py
E   ModuleNotFoundError: No module named 'foo'
============================== 2 errors in 0.50s ==============================
"""
        s = summarise_pytest(output)
        assert s.errors == 2
        assert len(s.collection_errors) == 2
        assert "test_broken.py" in s.collection_errors[0]

    def test_no_tests_ran(self):
        output = """\
============================= test session starts =============================
collected 0 items

========================= no tests ran in 0.01s =========================
"""
        s = summarise_pytest(output)
        assert s.total == 0
        assert s.passed == 0

    def test_parametrized(self):
        output = """\
FAILED tests/test_math.py::test_add[1-2-4] - AssertionError: 3 != 4
FAILED tests/test_math.py::test_add[0-0-1] - AssertionError: 0 != 1
========================= 2 failed, 8 passed in 1.20s =========================
"""
        s = summarise_pytest(output)
        assert s.failed == 2
        assert s.passed == 8
        assert len(s.failures) == 2
        assert "[1-2-4]" in s.failures[0].test_name

    def test_warnings_counted(self):
        output = """\
============================== 5 passed, 3 warnings in 1.00s ==============================
"""
        s = summarise_pytest(output)
        assert s.passed == 5
        assert s.warnings == 3

    def test_skipped(self):
        output = """\
====================== 3 passed, 2 skipped in 0.50s ======================
"""
        s = summarise_pytest(output)
        assert s.passed == 3
        assert s.skipped == 2
        assert s.total == 5

    def test_verbose_output(self):
        output = """\
tests/test_a.py::test_one PASSED
tests/test_a.py::test_two PASSED
tests/test_b.py::test_three FAILED

FAILED tests/test_b.py::test_three - AssertionError
====================== 1 failed, 2 passed in 0.30s ======================
"""
        s = summarise_pytest(output)
        assert s.passed == 2
        assert s.failed == 1

    def test_empty_output(self):
        s = summarise_pytest("")
        assert s.total == 0
        assert s.failures == []


# ═══════════════════════════════════════════════════════════════════════════
# summarise_npm_test
# ═══════════════════════════════════════════════════════════════════════════


class TestSummariseNpmTest:
    def test_vitest_all_pass(self):
        output = """\
 ✓ src/utils.test.ts (5 tests) 42ms
 ✓ src/App.test.tsx (3 tests) 120ms

 Test Files  2 passed (2)
      Tests  8 passed (8)
   Start at  10:00:00
   Duration  1.50s
"""
        s = summarise_npm_test(output)
        assert s.passed == 8
        assert s.failed == 0
        assert s.total == 8
        assert s.suite == "vitest"

    def test_vitest_failures(self):
        output = """\
 ✓ src/utils.test.ts (3 tests) 42ms
 × src/App.test.tsx (1 test | 1 failed) 120ms

 FAIL src/App.test.tsx > renders correctly
   AssertionError: expected 1 to be 2

 Test Files  1 failed | 1 passed (2)
      Tests  1 failed | 3 passed (4)
   Duration  1.50s
"""
        s = summarise_npm_test(output)
        assert s.failed == 1
        assert s.total == 4
        assert s.passed == 3

    def test_jest_all_pass(self):
        output = """\
PASS src/utils.test.ts
PASS src/App.test.tsx

Test Suites: 2 passed, 2 total
Tests:       8 passed, 8 total
Snapshots:   0 total
Time:        2.5 s
"""
        s = summarise_npm_test(output)
        assert s.passed == 8
        assert s.failed == 0
        assert s.total == 8
        assert s.suite == "jest"

    def test_jest_failures(self):
        output = """\
FAIL src/App.test.tsx
  ● renders correctly
    expect(received).toBe(expected)

Test Suites: 1 failed, 1 passed, 2 total
Tests:       1 failed, 7 passed, 8 total
"""
        s = summarise_npm_test(output)
        assert s.passed == 7
        assert s.failed == 1
        assert s.total == 8
        assert s.suite == "jest"
        assert len(s.failures) == 1
        assert "renders correctly" in s.failures[0].test_name

    def test_empty_output(self):
        s = summarise_npm_test("")
        assert s.total == 0
        assert s.passed == 0
        assert s.failures == []

    def test_vitest_fail_lines(self):
        output = """\
 × src/math.test.ts > add > handles negatives
 × src/math.test.ts > multiply > overflow

 Tests  2 failed | 5 passed (7)
"""
        s = summarise_npm_test(output)
        assert s.failed == 2
        assert s.total == 7
        assert len(s.failures) >= 2


# ═══════════════════════════════════════════════════════════════════════════
# summarise_build
# ═══════════════════════════════════════════════════════════════════════════


class TestSummariseBuild:
    def test_clean_build(self):
        output = "Build completed successfully.\n"
        s = summarise_build(output)
        assert s.success is True
        assert s.errors == []
        assert s.warnings == []

    def test_python_syntax_error(self):
        output = """\
app/main.py:25: error: Name 'foo' is not defined
app/main.py:30: error: Incompatible types
"""
        s = summarise_build(output)
        assert s.success is False
        assert len(s.errors) == 2
        assert s.errors[0].file == "app/main.py"
        assert s.errors[0].line == 25
        assert s.errors[0].severity == "error"

    def test_typescript_errors(self):
        output = """\
src/App.tsx:10:5: error TS2322: Type 'string' is not assignable to type 'number'.
src/utils.ts:20:1: error TS2304: Cannot find name 'foo'.
"""
        s = summarise_build(output)
        assert s.success is False
        assert len(s.errors) == 2
        assert s.errors[0].file == "src/App.tsx"
        assert s.errors[0].line == 10

    def test_warnings_only(self):
        output = """\
src/utils.ts:5:1: warning: Unused variable 'x'
src/utils.ts:10:1: warning: Missing return type
"""
        s = summarise_build(output)
        assert s.success is True
        assert len(s.warnings) == 2
        assert s.warnings[0].severity == "warning"

    def test_mixed_errors_warnings(self):
        output = """\
app/foo.py:10: error: bad code
app/foo.py:15: warning: unused import
"""
        s = summarise_build(output)
        assert s.success is False
        assert len(s.errors) == 1
        assert len(s.warnings) == 1

    def test_generic_error_line(self):
        output = "ERROR: Module not found\n"
        s = summarise_build(output)
        assert s.success is False
        assert len(s.errors) == 1
        assert "Module not found" in s.errors[0].message

    def test_generic_warning_line(self):
        output = "WARNING: Deprecation notice\n"
        s = summarise_build(output)
        assert s.success is True  # warnings don't make it fail
        assert len(s.warnings) == 1

    def test_stderr_included(self):
        s = summarise_build("", stderr="error: link failed\n")
        # This is a generic error line — captured by the generic pattern
        assert s.success is False

    def test_empty_output(self):
        s = summarise_build("")
        assert s.success is True
        assert s.errors == []


# ═══════════════════════════════════════════════════════════════════════════
# summarise_generic
# ═══════════════════════════════════════════════════════════════════════════


class TestSummariseGeneric:
    def test_short_output(self):
        output = "line1\nline2\nline3"
        s = summarise_generic(output)
        assert s.line_count == 3
        assert s.truncated is False
        assert len(s.head) == 3

    def test_empty_output(self):
        s = summarise_generic("")
        assert s.line_count == 0
        assert s.truncated is False
        assert s.head == []
        assert s.tail == []

    def test_long_output_truncated(self):
        lines = [f"line {i}" for i in range(200)]
        output = "\n".join(lines)
        s = summarise_generic(output, max_lines=10)
        assert s.line_count == 200
        assert s.truncated is True
        assert len(s.head) == 10
        assert len(s.tail) == 10
        assert s.head[0] == "line 0"
        assert s.tail[-1] == "line 199"

    def test_error_lines_preserved(self):
        lines = [f"line {i}" for i in range(200)]
        lines[100] = "FATAL ERROR: something broke"
        lines[150] = "exception raised in module X"
        output = "\n".join(lines)
        s = summarise_generic(output, max_lines=10)
        assert s.truncated is True
        assert any("FATAL ERROR" in ln for ln in s.error_lines)
        assert any("exception" in ln for ln in s.error_lines)

    def test_fail_keyword(self):
        output = "test passed\ntest FAILED\ntest passed\n"
        s = summarise_generic(output)
        assert any("FAILED" in ln for ln in s.error_lines)

    def test_traceback_keyword(self):
        output = "Traceback (most recent call last):\n  File ...\n"
        s = summarise_generic(output)
        assert len(s.error_lines) >= 1

    def test_exact_boundary(self):
        """Exactly 2*max_lines → no truncation."""
        lines = [f"line {i}" for i in range(20)]
        output = "\n".join(lines)
        s = summarise_generic(output, max_lines=10)
        assert s.truncated is False
        assert len(s.head) == 20

    def test_stderr_combined(self):
        s = summarise_generic("stdout line", stderr="stderr error line")
        assert s.line_count > 0
        assert any("error" in ln for ln in s.error_lines)


# ═══════════════════════════════════════════════════════════════════════════
# detect_parser
# ═══════════════════════════════════════════════════════════════════════════


class TestDetectParser:
    def test_pytest(self):
        assert detect_parser("pytest tests/ -v") == "pytest"

    def test_python_m_pytest(self):
        assert detect_parser("python -m pytest tests/") == "pytest"

    def test_npm_test(self):
        assert detect_parser("npm test") == "npm"

    def test_npx_vitest(self):
        assert detect_parser("npx vitest") == "npm"

    def test_npx_jest(self):
        assert detect_parser("npx jest") == "npm"

    def test_npm_run_test(self):
        assert detect_parser("npm run test") == "npm"

    def test_pip_install(self):
        assert detect_parser("pip install requests") == "build"

    def test_npm_install(self):
        assert detect_parser("npm install") == "build"

    def test_npx_tsc(self):
        assert detect_parser("npx tsc --noEmit") == "build"

    def test_unknown(self):
        assert detect_parser("unknown_tool --flag") == "generic"

    def test_case_insensitive(self):
        assert detect_parser("PYTEST tests/") == "pytest"

    def test_leading_whitespace(self):
        assert detect_parser("  pytest tests/") == "pytest"


# ═══════════════════════════════════════════════════════════════════════════
# auto_summarise
# ═══════════════════════════════════════════════════════════════════════════


class TestAutoSummarise:
    def _result(self, command: str, stdout: str = "", stderr: str = "") -> RunResult:
        return RunResult(
            exit_code=0, stdout=stdout, stderr=stderr,
            command=command, duration_ms=100,
        )

    def test_pytest_dispatch(self):
        r = self._result(
            "pytest tests/",
            stdout="====================== 5 passed in 1.00s ======================",
        )
        s = auto_summarise(r)
        assert isinstance(s, PytestSummary)
        assert s.passed == 5

    def test_npm_dispatch(self):
        r = self._result(
            "npm test",
            stdout="Tests  3 passed (3)\n",
        )
        s = auto_summarise(r)
        assert isinstance(s, NpmTestSummary)
        assert s.passed == 3

    def test_build_dispatch(self):
        r = self._result(
            "pip install requests",
            stdout="Successfully installed requests-2.28.0\n",
        )
        s = auto_summarise(r)
        assert isinstance(s, BuildSummary)
        assert s.success is True

    def test_generic_dispatch(self):
        r = self._result(
            "unknown_tool",
            stdout="some output\n",
        )
        s = auto_summarise(r)
        assert isinstance(s, GenericSummary)

    def test_with_plain_object(self):
        """auto_summarise works with any object having command/stdout/stderr."""

        class FakeResult:
            command = "pytest tests/"
            stdout = "====== 3 passed in 0.10s ======"
            stderr = ""

        s = auto_summarise(FakeResult())
        assert isinstance(s, PytestSummary)
        assert s.passed == 3
