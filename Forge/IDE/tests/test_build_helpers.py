"""Tests for forge_ide.build_helpers — apply_response, run_and_summarise, models."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from forge_ide.build_helpers import (
    ApplyResult,
    VerificationResult,
    apply_response,
    run_and_summarise,
)
from forge_ide.lang import DiagnosticReport
from forge_ide.log_parser import GenericSummary, PytestSummary
from forge_ide.runner import RunResult


# ===================================================================
# Fixtures
# ===================================================================

ORIGINAL_CONTENT = """\
import os

def main():
    print("hello")

if __name__ == "__main__":
    main()
"""

VALID_DIFF = """\
--- a/main.py
+++ b/main.py
@@ -1,4 +1,5 @@
 import os
+import sys
 
 def main():
     print("hello")
"""

CONFLICTING_DIFF = """\
--- a/main.py
+++ b/main.py
@@ -1,4 +1,5 @@
 import nonexistent_module
+import sys
 
 def totally_wrong():
     pass
"""

FULL_RESPONSE = """\
import os
import sys

def main():
    print("hello world")

if __name__ == "__main__":
    main()
"""

FENCED_DIFF = """\
```diff
--- a/main.py
+++ b/main.py
@@ -1,4 +1,5 @@
 import os
+import sys
 
 def main():
     print("hello")
```
"""


# ===================================================================
# apply_response — full content
# ===================================================================


class TestApplyResponseFullContent:
    def test_full_content_basic(self):
        result = apply_response(ORIGINAL_CONTENT, FULL_RESPONSE)
        assert result.method == "full"
        assert "hello world" in result.content
        assert result.hunks_applied == 0
        assert result.had_conflict is False

    def test_full_content_ensures_newline(self):
        result = apply_response("old", "new content")
        assert result.content.endswith("\n")

    def test_empty_response(self):
        result = apply_response(ORIGINAL_CONTENT, "")
        assert result.method == "full"


# ===================================================================
# apply_response — diff (patch)
# ===================================================================


class TestApplyResponsePatch:
    def test_valid_diff_applies(self):
        result = apply_response(ORIGINAL_CONTENT, VALID_DIFF)
        assert result.method == "patch"
        assert "import sys" in result.content
        assert result.hunks_applied >= 1
        assert result.had_conflict is False

    def test_fenced_diff_applies(self):
        """Fences stripped before patch application."""
        result = apply_response(ORIGINAL_CONTENT, FENCED_DIFF)
        assert result.method == "patch"
        assert "import sys" in result.content

    def test_conflicting_diff_fallback(self):
        """Patch conflict → fallback to full content with had_conflict=True."""
        result = apply_response(ORIGINAL_CONTENT, CONFLICTING_DIFF, path="main.py")
        assert result.had_conflict is True
        assert result.method == "full"


# ===================================================================
# apply_response — edge cases
# ===================================================================


class TestApplyResponseEdgeCases:
    def test_path_passed_through(self):
        """Path parameter is used for error context."""
        result = apply_response(ORIGINAL_CONTENT, FULL_RESPONSE, path="src/main.py")
        assert result.method == "full"

    def test_diff_preserves_original_on_success(self):
        """Original content parts are preserved after patching."""
        result = apply_response(ORIGINAL_CONTENT, VALID_DIFF)
        assert "def main():" in result.content
        assert 'print("hello")' in result.content


# ===================================================================
# run_and_summarise
# ===================================================================


class TestRunAndSummarise:
    @pytest.mark.asyncio
    async def test_echo_command(self):
        """Mocked run returns a RunResult; auto_summarise produces GenericSummary."""
        mock_result = RunResult(
            command="echo hello world",
            exit_code=0,
            stdout="hello world\n",
            stderr="",
            timed_out=False,
            duration_ms=10,
            truncated=False,
        )
        with patch("forge_ide.build_helpers.ide_run", new_callable=AsyncMock, return_value=mock_result):
            run_result, summary = await run_and_summarise("echo hello world", timeout_s=10)
        assert run_result.exit_code == 0
        assert "hello" in run_result.stdout
        assert isinstance(summary, GenericSummary)

    @pytest.mark.asyncio
    async def test_pytest_command(self):
        """Mocked pytest output is parsed as PytestSummary."""
        pytest_output = "===== 3 passed in 0.5s =====\n"
        mock_result = RunResult(
            command="python -m pytest tests/ -q",
            exit_code=0,
            stdout=pytest_output,
            stderr="",
            timed_out=False,
            duration_ms=500,
            truncated=False,
        )
        with patch("forge_ide.build_helpers.ide_run", new_callable=AsyncMock, return_value=mock_result):
            run_result, summary = await run_and_summarise("python -m pytest tests/ -q", timeout_s=30)
        assert run_result.exit_code == 0
        assert isinstance(summary, PytestSummary)
        assert summary.passed == 3

    @pytest.mark.asyncio
    async def test_failing_command(self):
        """Non-zero exit code still produces a summary."""
        mock_result = RunResult(
            command="python -m pytest tests/",
            exit_code=1,
            stdout="FAILED tests/test_x.py::test_a\n===== 1 failed in 0.2s =====\n",
            stderr="",
            timed_out=False,
            duration_ms=200,
            truncated=False,
        )
        with patch("forge_ide.build_helpers.ide_run", new_callable=AsyncMock, return_value=mock_result):
            run_result, summary = await run_and_summarise("python -m pytest tests/", timeout_s=10)
        assert run_result.exit_code == 1
        assert isinstance(summary, PytestSummary)


# ===================================================================
# Model tests
# ===================================================================


class TestModels:
    def test_apply_result_frozen(self):
        ar = ApplyResult(content="x", method="full")
        with pytest.raises(Exception):
            ar.content = "y"  # type: ignore[misc]

    def test_verification_result_frozen(self):
        vr = VerificationResult()
        with pytest.raises(Exception):
            vr.fixes_applied = 5  # type: ignore[misc]

    def test_verification_result_defaults(self):
        vr = VerificationResult()
        assert vr.diagnostics is None
        assert vr.test_summary is None
        assert vr.fixes_applied == 0

    def test_verification_result_with_diagnostics(self):
        dr = DiagnosticReport(error_count=2)
        vr = VerificationResult(diagnostics=dr, fixes_applied=1)
        assert vr.diagnostics.error_count == 2
        assert vr.fixes_applied == 1

    def test_apply_result_patch_fields(self):
        ar = ApplyResult(
            content="patched",
            method="patch",
            hunks_applied=3,
            had_conflict=False,
        )
        assert ar.hunks_applied == 3
        assert ar.method == "patch"
