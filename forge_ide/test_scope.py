"""Test-scoping utilities — map changed source files to relevant tests.

The Forge IDE builder agent can use these helpers to run *targeted* tests
after each logical unit of work rather than the full suite every time.

Workflow
--------
1. Agent tracks which files it wrote / patched during a sub-task.
2. ``infer_test_candidates`` turns those paths into probable test paths.
3. ``filter_existing`` narrows candidates to files that actually exist.
4. ``format_scoped_command`` builds the CLI invocation.
5. Agent runs the scoped command via ``run_tests``.
6. Full suite is run **once** at end-of-phase via the project's test runner.

Naming conventions
------------------
Python:  ``src/auth.py``            → ``tests/test_auth.py``
         ``app/services/user.py``   → ``tests/test_user.py``
JS/TS:   ``src/Auth.tsx``           → ``src/Auth.test.tsx`` / ``__tests__/Auth.test.tsx``
         ``src/utils/parse.ts``     → ``src/utils/parse.test.ts``
"""

from __future__ import annotations

import os
import re
from pathlib import PurePosixPath
from typing import Sequence

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Common test-directory names (checked in order of likelihood).
PYTHON_TEST_DIRS: tuple[str, ...] = ("tests", "test", "spec")

#: File extensions recognised as Python source.
_PY_EXTS: frozenset[str] = frozenset({".py"})

#: File extensions recognised as JS / TS source.
_JS_EXTS: frozenset[str] = frozenset({
    ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs",
})

#: Regex that already looks like a test file name.
_ALREADY_TEST_RE = re.compile(
    r"(^test_|_test\.|\.test\.|\.spec\.|^test\.)", re.IGNORECASE,
)

#: Files / dirs we never scope — infra / config / fixtures.
_IGNORE_PATTERNS: frozenset[str] = frozenset({
    "conftest.py", "setup.py", "setup.cfg", "pyproject.toml",
    "package.json", "tsconfig.json", "jest.config",
    "__init__.py", "alembic",
})


# ---------------------------------------------------------------------------
# Core helpers
# ---------------------------------------------------------------------------

def _is_ignorable(path: str) -> bool:
    """Return *True* when *path* should not be scoped (config / fixture)."""
    name = PurePosixPath(path).name
    for pat in _IGNORE_PATTERNS:
        if pat in name:
            return True
    return False


def _stem(path: str) -> str:
    """Return the bare stem of *path* (no extension)."""
    return PurePosixPath(path).stem


def _ext(path: str) -> str:
    """Return the extension (including the dot) of *path*."""
    return PurePosixPath(path).suffix


def _is_test_file(path: str) -> bool:
    """Does *path* already look like a test file?"""
    return bool(_ALREADY_TEST_RE.search(PurePosixPath(path).name))


# ---------------------------------------------------------------------------
# Python candidates
# ---------------------------------------------------------------------------

def _python_candidates(path: str) -> list[str]:
    """Return candidate test-file paths for a Python source file.

    Given ``app/services/user.py`` produces::

        tests/test_user.py
        test/test_user.py

    Given ``src/utils/helpers.py`` produces::

        tests/test_helpers.py
        test/test_helpers.py
    """
    stem = _stem(path)
    candidates: list[str] = []

    # Standard: tests/test_<stem>.py
    for td in PYTHON_TEST_DIRS:
        candidates.append(f"{td}/test_{stem}.py")

    # Co-located: same dir test_<stem>.py
    parent = str(PurePosixPath(path).parent)
    if parent and parent != ".":
        candidates.append(f"{parent}/test_{stem}.py")

    return candidates


# ---------------------------------------------------------------------------
# JS / TS candidates
# ---------------------------------------------------------------------------

def _js_candidates(path: str) -> list[str]:
    """Return candidate test-file paths for a JS/TS source file.

    Given ``src/components/Auth.tsx`` produces::

        src/components/Auth.test.tsx
        src/components/__tests__/Auth.test.tsx
    """
    p = PurePosixPath(path)
    stem = p.stem
    ext = p.suffix          # .tsx, .ts, .js, …
    parent = str(p.parent)

    candidates: list[str] = []

    # Co-located: <stem>.test.<ext>
    if parent and parent != ".":
        candidates.append(f"{parent}/{stem}.test{ext}")
        candidates.append(f"{parent}/{stem}.spec{ext}")
        # __tests__ subfolder
        candidates.append(f"{parent}/__tests__/{stem}.test{ext}")
        candidates.append(f"{parent}/__tests__/{stem}.spec{ext}")
    else:
        candidates.append(f"{stem}.test{ext}")
        candidates.append(f"{stem}.spec{ext}")

    return candidates


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def infer_test_candidates(
    changed_files: Sequence[str],
) -> list[str]:
    """Map *changed_files* to probable test-file paths (may not exist).

    Paths that are already test files are returned unchanged.
    Config / fixture files are silently skipped.

    Returns a de-duplicated list preserving insertion order.
    """
    seen: set[str] = set()
    candidates: list[str] = []

    for raw in changed_files:
        path = raw.replace("\\", "/").strip("/")
        if not path:
            continue
        if _is_ignorable(path):
            continue

        # Already a test file → include as-is.
        if _is_test_file(path):
            if path not in seen:
                seen.add(path)
                candidates.append(path)
            continue

        ext = _ext(path)
        if ext in _PY_EXTS:
            for c in _python_candidates(path):
                if c not in seen:
                    seen.add(c)
                    candidates.append(c)
        elif ext in _JS_EXTS:
            for c in _js_candidates(path):
                if c not in seen:
                    seen.add(c)
                    candidates.append(c)

    return candidates


def filter_existing(
    candidates: Sequence[str],
    existing_files: Sequence[str],
) -> list[str]:
    """Narrow *candidates* to those that appear in *existing_files*.

    Both inputs should use forward slashes.  Comparison is
    case-insensitive on Windows-style paths.
    """
    normalised: set[str] = {
        f.replace("\\", "/").strip("/").lower() for f in existing_files
    }
    return [c for c in candidates if c.lower() in normalised]


def format_scoped_command(
    test_paths: Sequence[str],
    *,
    runner: str = "pytest",
    extra_args: str = "-x -v",
    fallback_full: str | None = None,
) -> str:
    """Build a test command targeting only *test_paths*.

    Parameters
    ----------
    test_paths:
        Relative paths to test files.
    runner:
        The test runner executable (``pytest``, ``npx vitest``, etc.).
    extra_args:
        Additional arguments appended after paths.
    fallback_full:
        If *test_paths* is empty, return this command instead.
        ``None`` means return an empty string.

    Returns
    -------
    str
        The assembled command, e.g.
        ``pytest tests/test_auth.py tests/test_user.py -x -v``
    """
    if not test_paths:
        return fallback_full or ""

    paths_str = " ".join(test_paths)
    parts = [runner, paths_str]
    if extra_args:
        parts.append(extra_args)
    return " ".join(parts)


def scope_tests_for_changes(
    changed_files: Sequence[str],
    existing_files: Sequence[str],
    *,
    runner: str = "pytest",
    extra_args: str = "-x -v",
    fallback_full: str | None = None,
) -> tuple[list[str], str]:
    """One-shot helper: changed files → (matched_test_paths, command).

    Combines ``infer_test_candidates`` → ``filter_existing`` →
    ``format_scoped_command``.  Convenient for callers that just want the
    final command string.

    Returns
    -------
    tuple[list[str], str]
        ``(matched_test_paths, scoped_command)`` — either or both may be
        empty when no relevant tests are found.
    """
    candidates = infer_test_candidates(changed_files)
    matched = filter_existing(candidates, existing_files)
    cmd = format_scoped_command(
        matched,
        runner=runner,
        extra_args=extra_args,
        fallback_full=fallback_full,
    )
    return matched, cmd
