"""Deterministic log parsers — structured summaries from raw command output.

Every parser is a pure function: no LLM, no network, no side effects.
Input is raw stdout/stderr text; output is a frozen Pydantic model.

Supported parsers:

- ``summarise_pytest``  — pytest output → ``PytestSummary``
- ``summarise_npm_test`` — vitest / jest output → ``NpmTestSummary``
- ``summarise_build``   — compiler / build tool output → ``BuildSummary``
- ``summarise_generic`` — any output → ``GenericSummary`` (head + tail + error lines)
- ``auto_summarise``    — detect parser from command, then dispatch
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class TestFailure(BaseModel):
    """A single failing test case."""

    __test__ = False  # Prevent pytest from collecting this class

    model_config = ConfigDict(frozen=True)

    test_name: str = Field(..., description="Fully-qualified test name")
    file: str = Field(default="", description="Source file path (if known)")
    line: int = Field(default=0, ge=0, description="Line number (0 = unknown)")
    message: str = Field(default="", description="Failure message / assertion text")


class PytestSummary(BaseModel):
    """Structured summary of a pytest run."""

    model_config = ConfigDict(frozen=True)

    total: int = Field(default=0, ge=0)
    passed: int = Field(default=0, ge=0)
    failed: int = Field(default=0, ge=0)
    errors: int = Field(default=0, ge=0)
    skipped: int = Field(default=0, ge=0)
    warnings: int = Field(default=0, ge=0)
    duration_s: float = Field(default=0.0, ge=0.0)
    failures: list[TestFailure] = Field(default_factory=list)
    collection_errors: list[str] = Field(default_factory=list)


class NpmTestSummary(BaseModel):
    """Structured summary of a vitest / jest run."""

    model_config = ConfigDict(frozen=True)

    total: int = Field(default=0, ge=0)
    passed: int = Field(default=0, ge=0)
    failed: int = Field(default=0, ge=0)
    failures: list[TestFailure] = Field(default_factory=list)
    suite: str = Field(default="", description="Test suite / file name")


class BuildIssue(BaseModel):
    """A single build error or warning."""

    model_config = ConfigDict(frozen=True)

    file: str = Field(default="", description="Source file")
    line: int = Field(default=0, ge=0, description="Line number (0 = unknown)")
    message: str = Field(..., description="Error or warning message")
    severity: Literal["error", "warning"] = Field(
        ..., description="Issue severity",
    )


class BuildSummary(BaseModel):
    """Structured summary of a build / compile run."""

    model_config = ConfigDict(frozen=True)

    success: bool = Field(..., description="True when zero errors detected")
    errors: list[BuildIssue] = Field(default_factory=list)
    warnings: list[BuildIssue] = Field(default_factory=list)


class GenericSummary(BaseModel):
    """Truncated summary of arbitrary command output."""

    model_config = ConfigDict(frozen=True)

    line_count: int = Field(default=0, ge=0, description="Total lines in original output")
    head: list[str] = Field(default_factory=list, description="First N lines")
    tail: list[str] = Field(default_factory=list, description="Last N lines")
    error_lines: list[str] = Field(
        default_factory=list,
        description="Lines containing error/fail/exception keywords",
    )
    truncated: bool = Field(default=False, description="True if output was truncated")


# ---------------------------------------------------------------------------
# Regex patterns
# ---------------------------------------------------------------------------

# Pytest
_PYTEST_SUMMARY_RE = re.compile(
    r"=+\s+(.+?)\s+in\s+([\d.]+)s?\s+=+",
)
_PYTEST_COUNTS_RE = re.compile(
    r"(\d+)\s+(passed|failed|error|errors|skipped|warnings?|deselected)",
)
_PYTEST_FAILED_RE = re.compile(
    r"FAILED\s+(.+?)::(.+?)(?:\s+-\s+(.*))?$", re.MULTILINE,
)
_PYTEST_FAILED_SIMPLE_RE = re.compile(
    r"FAILED\s+(.+?)\s+-\s+(.*)", re.MULTILINE,
)
_PYTEST_COLLECTION_ERROR_RE = re.compile(
    r"ERROR collecting (.+?)$", re.MULTILINE,
)

# Vitest
_VITEST_PASSED_RE = re.compile(r"(\d+)\s+passed", re.IGNORECASE)
_VITEST_FAILED_RE = re.compile(r"(\d+)\s+failed", re.IGNORECASE)
_VITEST_TOTAL_RE = re.compile(r"\((\d+)\)\s*$", re.MULTILINE)
_VITEST_SUITE_RE = re.compile(
    r"Test Files?\s+.*?(\d+)\s+(?:passed|failed)", re.IGNORECASE,
)
_VITEST_FAIL_BLOCK_RE = re.compile(
    r"(?:FAIL|×|✕)\s+(.+?)$", re.MULTILINE,
)

# Jest
_JEST_SUMMARY_RE = re.compile(
    r"Tests:\s+(?:(\d+)\s+failed,?\s*)?(?:(\d+)\s+passed,?\s*)?(\d+)\s+total",
    re.IGNORECASE,
)
_JEST_FAIL_BLOCK_RE = re.compile(
    r"●\s+(.+?)$", re.MULTILINE,
)

# Build errors / warnings
_BUILD_ERROR_RE = re.compile(
    r"^(.+?):(\d+)(?::\d+)?:\s*(?:error|Error)\b[:\s]*(.*)$", re.MULTILINE,
)
_BUILD_ERROR_GENERIC_RE = re.compile(
    r"^(?:ERROR|error)\b[:\s]+(.*)$", re.MULTILINE,
)
_BUILD_WARNING_RE = re.compile(
    r"^(.+?):(\d+)(?::\d+)?:\s*(?:warning|Warning)\b[:\s]*(.*)$", re.MULTILINE,
)
_BUILD_WARNING_GENERIC_RE = re.compile(
    r"^(?:WARNING|warning|Warning)\b[:\s]+(.*)$", re.MULTILINE,
)

# Generic error-line detection
_ERROR_LINE_RE = re.compile(
    r"error|fail|exception|traceback", re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Parsers
# ---------------------------------------------------------------------------


def summarise_pytest(stdout: str) -> PytestSummary:
    """Parse pytest stdout into a ``PytestSummary``."""
    passed = failed = errors = skipped = warnings_count = 0
    duration = 0.0
    failures: list[TestFailure] = []
    collection_errors: list[str] = []

    # Extract counts from summary line
    for m in _PYTEST_COUNTS_RE.finditer(stdout):
        count = int(m.group(1))
        kind = m.group(2).lower()
        if kind == "passed":
            passed = count
        elif kind == "failed":
            failed = count
        elif kind in ("error", "errors"):
            errors = count
        elif kind == "skipped":
            skipped = count
        elif kind.startswith("warning"):
            warnings_count = count
        # deselected is intentionally ignored

    # Duration
    dur_match = _PYTEST_SUMMARY_RE.search(stdout)
    if dur_match:
        try:
            duration = float(dur_match.group(2))
        except (ValueError, IndexError):
            pass

    # Failed test details
    for m in _PYTEST_FAILED_RE.finditer(stdout):
        file_part = m.group(1).strip()
        test_name = m.group(2).strip()
        message = (m.group(3) or "").strip()
        failures.append(TestFailure(
            test_name=test_name,
            file=file_part,
            message=message,
        ))

    # If no structured FAILED lines found, try simpler pattern
    if not failures:
        for m in _PYTEST_FAILED_SIMPLE_RE.finditer(stdout):
            test_id = m.group(1).strip()
            message = m.group(2).strip()
            failures.append(TestFailure(
                test_name=test_id,
                message=message,
            ))

    # Collection errors
    for m in _PYTEST_COLLECTION_ERROR_RE.finditer(stdout):
        collection_errors.append(m.group(1).strip())

    total = passed + failed + errors + skipped

    return PytestSummary(
        total=total,
        passed=passed,
        failed=failed,
        errors=errors,
        skipped=skipped,
        warnings=warnings_count,
        duration_s=duration,
        failures=failures,
        collection_errors=collection_errors,
    )


def summarise_npm_test(stdout: str) -> NpmTestSummary:
    """Parse vitest / jest stdout into an ``NpmTestSummary``."""
    passed = failed = total = 0
    suite = ""
    failures: list[TestFailure] = []

    # Try vitest format: look for lines like "Tests  1 failed | 3 passed (4)"
    # or "Tests  8 passed (8)".  Jest uses "Tests:" (with colon) — skip those.
    tests_line = ""
    for line in stdout.split("\n"):
        stripped = line.strip()
        lower = stripped.lower()
        if lower.startswith("tests") and ("passed" in lower or "failed" in lower):
            # Ignore "Test Files", "Test Suites", and jest-style "Tests:" lines
            if (lower.startswith("test files") or lower.startswith("test suites")
                    or lower.startswith("tests:")):
                continue
            tests_line = stripped
            break

    if tests_line:
        pm = _VITEST_PASSED_RE.search(tests_line)
        fm = _VITEST_FAILED_RE.search(tests_line)
        tm = _VITEST_TOTAL_RE.search(tests_line)
        if pm:
            passed = int(pm.group(1))
        if fm:
            failed = int(fm.group(1))
        if tm:
            total = int(tm.group(1))
        else:
            total = passed + failed

    sm = _VITEST_SUITE_RE.search(stdout)
    if sm:
        suite = "vitest"

    # Try jest format if vitest didn't match
    if not tests_line:
        jm = _JEST_SUMMARY_RE.search(stdout)
        if jm:
            failed = int(jm.group(1) or 0)
            passed = int(jm.group(2) or 0)
            total = int(jm.group(3) or 0)
            suite = "jest"

    # Extract failure names — vitest style
    if suite == "vitest" or (not suite and failed > 0 and suite != "jest"):
        for m in _VITEST_FAIL_BLOCK_RE.finditer(stdout):
            name = m.group(1).strip()
            if name and not name.startswith("Test Files"):
                failures.append(TestFailure(test_name=name))

    # Extract failure names — jest style
    if suite == "jest":
        for m in _JEST_FAIL_BLOCK_RE.finditer(stdout):
            name = m.group(1).strip()
            if name:
                failures.append(TestFailure(test_name=name))

    return NpmTestSummary(
        total=total,
        passed=passed,
        failed=failed,
        failures=failures,
        suite=suite,
    )


def summarise_build(stdout: str, stderr: str = "") -> BuildSummary:
    """Parse build / compile output into a ``BuildSummary``."""
    combined = (stdout + "\n" + stderr).strip()
    build_errors: list[BuildIssue] = []
    build_warnings: list[BuildIssue] = []

    # Structured errors (file:line: error: msg)
    for m in _BUILD_ERROR_RE.finditer(combined):
        build_errors.append(BuildIssue(
            file=m.group(1).strip(),
            line=int(m.group(2)),
            message=m.group(3).strip(),
            severity="error",
        ))

    # Generic ERROR lines
    for m in _BUILD_ERROR_GENERIC_RE.finditer(combined):
        msg = m.group(1).strip()
        # Avoid duplicates if already captured by structured pattern
        if not any(e.message == msg for e in build_errors):
            build_errors.append(BuildIssue(
                message=msg,
                severity="error",
            ))

    # Structured warnings
    for m in _BUILD_WARNING_RE.finditer(combined):
        build_warnings.append(BuildIssue(
            file=m.group(1).strip(),
            line=int(m.group(2)),
            message=m.group(3).strip(),
            severity="warning",
        ))

    # Generic WARNING lines
    for m in _BUILD_WARNING_GENERIC_RE.finditer(combined):
        msg = m.group(1).strip()
        if not any(w.message == msg for w in build_warnings):
            build_warnings.append(BuildIssue(
                message=msg,
                severity="warning",
            ))

    return BuildSummary(
        success=len(build_errors) == 0,
        errors=build_errors,
        warnings=build_warnings,
    )


def summarise_generic(
    stdout: str,
    stderr: str = "",
    *,
    max_lines: int = 50,
) -> GenericSummary:
    """Produce a truncated summary of arbitrary command output.

    Strategy: first *max_lines* + last *max_lines* + every line that
    contains ``error``, ``fail``, ``exception``, or ``traceback``
    (case-insensitive).
    """
    combined = (stdout + "\n" + stderr).rstrip()
    if not combined.strip():
        return GenericSummary(line_count=0, truncated=False)

    lines = combined.split("\n")
    total = len(lines)

    error_lines = [
        ln for ln in lines if _ERROR_LINE_RE.search(ln)
    ]

    if total <= max_lines * 2:
        return GenericSummary(
            line_count=total,
            head=lines,
            tail=[],
            error_lines=error_lines,
            truncated=False,
        )

    head = lines[:max_lines]
    tail = lines[-max_lines:]
    return GenericSummary(
        line_count=total,
        head=head,
        tail=tail,
        error_lines=error_lines,
        truncated=True,
    )


# ---------------------------------------------------------------------------
# Auto-detection + dispatch
# ---------------------------------------------------------------------------

_PARSER_MAP: list[tuple[tuple[str, ...], str]] = [
    (("pytest", "python -m pytest", "python3 -m pytest"), "pytest"),
    (("npm test", "npm run test", "npx vitest", "npx jest"), "npm"),
    (("pip install", "pip3 install", "npm install", "npx tsc", "tsc"), "build"),
]


def detect_parser(
    command: str,
) -> Literal["pytest", "npm", "build", "generic"]:
    """Map a command string to the most appropriate parser name."""
    cmd = command.strip().lower()
    for prefixes, parser_name in _PARSER_MAP:
        if any(cmd.startswith(p) for p in prefixes):
            return parser_name  # type: ignore[return-value]
    return "generic"


def auto_summarise(
    result: object,
) -> PytestSummary | NpmTestSummary | BuildSummary | GenericSummary:
    """Detect the right parser from *result.command* and apply it.

    *result* is expected to be a ``RunResult`` (or any object with
    ``.command``, ``.stdout``, and ``.stderr`` attributes).
    """
    command: str = getattr(result, "command", "")
    stdout: str = getattr(result, "stdout", "")
    stderr: str = getattr(result, "stderr", "")

    parser = detect_parser(command)
    if parser == "pytest":
        return summarise_pytest(stdout)
    if parser == "npm":
        return summarise_npm_test(stdout)
    if parser == "build":
        return summarise_build(stdout, stderr)
    return summarise_generic(stdout, stderr)
