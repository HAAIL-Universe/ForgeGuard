"""Tests for forge_ide.runner — command execution with safety controls."""

from __future__ import annotations

import asyncio
import subprocess
from unittest.mock import MagicMock, patch

import pytest

from forge_ide.errors import SandboxViolation
from forge_ide.runner import (
    ALL_ALLOWED_PREFIXES,
    BLOCKED_COMMANDS,
    DEFAULT_TIMEOUT_S,
    INJECTION_CHARS,
    MAX_STDERR_BYTES,
    MAX_STDOUT_BYTES,
    RUN_COMMAND_PREFIXES,
    RUN_TESTS_PREFIXES,
    RunResult,
    _build_env,
    _truncate,
    run,
    validate_command,
)


# ═══════════════════════════════════════════════════════════════════════════
# RunResult model
# ═══════════════════════════════════════════════════════════════════════════


class TestRunResult:
    def test_minimal(self):
        r = RunResult(exit_code=0, command="echo hi")
        assert r.exit_code == 0
        assert r.stdout == ""
        assert r.stderr == ""
        assert r.duration_ms == 0
        assert r.truncated is False
        assert r.killed is False
        assert r.command == "echo hi"

    def test_full(self):
        r = RunResult(
            exit_code=1,
            stdout="out",
            stderr="err",
            duration_ms=500,
            truncated=True,
            killed=True,
            command="pytest",
        )
        assert r.exit_code == 1
        assert r.stdout == "out"
        assert r.stderr == "err"
        assert r.duration_ms == 500
        assert r.truncated is True
        assert r.killed is True

    def test_frozen(self):
        r = RunResult(exit_code=0, command="echo")
        with pytest.raises(Exception):
            r.exit_code = 99  # type: ignore[misc]

    def test_negative_exit_code(self):
        r = RunResult(exit_code=-1, command="bad")
        assert r.exit_code == -1


# ═══════════════════════════════════════════════════════════════════════════
# Constants sanity
# ═══════════════════════════════════════════════════════════════════════════


class TestConstants:
    def test_stdout_limit(self):
        assert MAX_STDOUT_BYTES == 50_000

    def test_stderr_limit(self):
        assert MAX_STDERR_BYTES == 10_000

    def test_default_timeout(self):
        assert DEFAULT_TIMEOUT_S == 120

    def test_injection_chars_present(self):
        for ch in (";", "|", "&", "`", "$", "(", ")", "{", "}"):
            assert ch in INJECTION_CHARS

    def test_all_prefixes_is_union(self):
        for p in RUN_TESTS_PREFIXES:
            assert p in ALL_ALLOWED_PREFIXES
        for p in RUN_COMMAND_PREFIXES:
            assert p in ALL_ALLOWED_PREFIXES


# ═══════════════════════════════════════════════════════════════════════════
# validate_command
# ═══════════════════════════════════════════════════════════════════════════


class TestValidateCommand:
    def test_empty_string(self):
        assert validate_command("") is not None
        assert "empty" in validate_command("").lower()

    def test_whitespace_only(self):
        assert validate_command("   ") is not None

    @pytest.mark.parametrize("char", list(INJECTION_CHARS))
    def test_injection_chars(self, char: str):
        result = validate_command(f"echo hello{char}world")
        assert result is not None
        assert "disallowed character" in result

    def test_blocked_command_rm(self):
        result = validate_command("rm -rf /")
        assert result is not None
        assert "not allowed" in result

    def test_blocked_command_curl(self):
        result = validate_command("curl http://evil.com")
        assert result is not None

    def test_blocked_command_git_push(self):
        result = validate_command("git push origin main")
        assert result is not None

    def test_allowed_pytest(self):
        assert validate_command("pytest tests/ -v") is None

    def test_allowed_python_m_pytest(self):
        assert validate_command("python -m pytest tests/") is None

    def test_allowed_npm_test(self):
        assert validate_command("npm test") is None

    def test_allowed_pip_install(self):
        assert validate_command("pip install requests") is None

    def test_allowed_npx(self):
        assert validate_command("npx vitest") is None

    def test_unknown_prefix_rejected(self):
        result = validate_command("my_custom_tool --arg")
        assert result is not None
        assert "allowlist" in result.lower()

    def test_custom_allowed_prefixes(self):
        assert validate_command("custom cmd", allowed_prefixes=("custom",)) is None

    def test_custom_prefixes_reject(self):
        result = validate_command("pytest", allowed_prefixes=("custom",))
        assert result is not None

    def test_case_insensitive(self):
        assert validate_command("PYTEST tests/") is None
        assert validate_command("Pytest tests/ -v") is None

    def test_leading_whitespace_stripped(self):
        assert validate_command("  pytest tests/") is None


# ═══════════════════════════════════════════════════════════════════════════
# _build_env
# ═══════════════════════════════════════════════════════════════════════════


class TestBuildEnv:
    @patch.dict("os.environ", {"PATH": "/usr/bin", "SECRET_KEY": "s3cr3t"}, clear=True)
    def test_includes_path(self):
        env = _build_env()
        assert "PATH" in env
        assert env["PATH"] == "/usr/bin"

    @patch.dict("os.environ", {"PATH": "/usr/bin", "SECRET_KEY": "s3cr3t"}, clear=True)
    def test_excludes_secrets(self):
        env = _build_env()
        assert "SECRET_KEY" not in env

    @patch.dict("os.environ", {"PATH": "/usr/bin", "VIRTUAL_ENV": "/venv"}, clear=True)
    def test_includes_virtual_env(self):
        env = _build_env()
        assert env.get("VIRTUAL_ENV") == "/venv"

    @patch.dict("os.environ", {"PATH": "/usr/bin"}, clear=True)
    def test_extra_vars_merged(self):
        env = _build_env({"MY_VAR": "hello"})
        assert env["MY_VAR"] == "hello"
        assert "PATH" in env

    @patch.dict("os.environ", {"PATH": "/usr/bin"}, clear=True)
    def test_extra_overrides(self):
        env = _build_env({"PATH": "/custom/bin"})
        assert env["PATH"] == "/custom/bin"


# ═══════════════════════════════════════════════════════════════════════════
# _truncate
# ═══════════════════════════════════════════════════════════════════════════


class TestTruncate:
    def test_under_limit(self):
        text, flag = _truncate("hello", 100)
        assert text == "hello"
        assert flag is False

    def test_exact_limit(self):
        text, flag = _truncate("abcde", 5)
        assert text == "abcde"
        assert flag is False

    def test_over_limit(self):
        text, flag = _truncate("abcdefghij", 5)
        assert flag is True
        assert text.startswith("abcde")
        assert "truncated" in text

    def test_empty_input(self):
        text, flag = _truncate("", 100)
        assert text == ""
        assert flag is False


# ═══════════════════════════════════════════════════════════════════════════
# run() — async subprocess execution
# ═══════════════════════════════════════════════════════════════════════════


def _run_sync(coro):
    """Helper to run an async function synchronously in tests."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class TestRun:
    def test_blocked_command_raises(self):
        with pytest.raises(SandboxViolation):
            _run_sync(run("rm -rf /"))

    def test_injection_raises(self):
        with pytest.raises(SandboxViolation):
            _run_sync(run("echo hello; echo world"))

    def test_unknown_prefix_raises(self):
        with pytest.raises(SandboxViolation):
            _run_sync(run("unknown_binary --flag"))

    @patch("forge_ide.runner.asyncio")
    def test_happy_path(self, mock_asyncio):
        """Mocked subprocess returning success."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "hello world"
        mock_result.stderr = ""

        # Make run_in_executor call the sync function directly
        async def fake_run_in_executor(executor, fn):
            return fn()

        mock_loop = MagicMock()
        mock_loop.run_in_executor = MagicMock(side_effect=fake_run_in_executor)
        mock_asyncio.get_event_loop.return_value = mock_loop

        with patch("subprocess.run", return_value=mock_result):
            result = _run_sync(run("pytest tests/"))

        assert result.exit_code == 0
        assert result.stdout == "hello world"
        assert result.killed is False
        assert result.command == "pytest tests/"

    @patch("forge_ide.runner.asyncio")
    def test_nonzero_exit(self, mock_asyncio):
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = "FAILED"
        mock_result.stderr = "error details"

        async def fake_run_in_executor(executor, fn):
            return fn()

        mock_loop = MagicMock()
        mock_loop.run_in_executor = MagicMock(side_effect=fake_run_in_executor)
        mock_asyncio.get_event_loop.return_value = mock_loop

        with patch("subprocess.run", return_value=mock_result):
            result = _run_sync(run("pytest tests/ -x"))

        assert result.exit_code == 1
        assert result.stdout == "FAILED"
        assert result.stderr == "error details"

    @patch("forge_ide.runner.asyncio")
    def test_timeout_killed(self, mock_asyncio):
        exc = subprocess.TimeoutExpired("pytest", 5)
        exc.stdout = b"partial output"
        exc.stderr = b""

        async def fake_run_in_executor(executor, fn):
            return fn()

        mock_loop = MagicMock()
        mock_loop.run_in_executor = MagicMock(side_effect=fake_run_in_executor)
        mock_asyncio.get_event_loop.return_value = mock_loop

        with patch("subprocess.run", side_effect=exc):
            result = _run_sync(run("pytest tests/", timeout_s=5))

        assert result.killed is True
        assert result.exit_code == -1
        assert result.stdout == "partial output"

    @patch("forge_ide.runner.asyncio")
    def test_timeout_killed_str_output(self, mock_asyncio):
        """TimeoutExpired with str (not bytes) partial output."""
        exc = subprocess.TimeoutExpired("pytest", 5)
        exc.stdout = "partial str"
        exc.stderr = "err str"

        async def fake_run_in_executor(executor, fn):
            return fn()

        mock_loop = MagicMock()
        mock_loop.run_in_executor = MagicMock(side_effect=fake_run_in_executor)
        mock_asyncio.get_event_loop.return_value = mock_loop

        with patch("subprocess.run", side_effect=exc):
            result = _run_sync(run("pytest tests/", timeout_s=5))

        assert result.killed is True
        assert result.stdout == "partial str"
        assert result.stderr == "err str"

    @patch("forge_ide.runner.asyncio")
    def test_output_truncation(self, mock_asyncio):
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "x" * (MAX_STDOUT_BYTES + 1000)
        mock_result.stderr = ""

        async def fake_run_in_executor(executor, fn):
            return fn()

        mock_loop = MagicMock()
        mock_loop.run_in_executor = MagicMock(side_effect=fake_run_in_executor)
        mock_asyncio.get_event_loop.return_value = mock_loop

        with patch("subprocess.run", return_value=mock_result):
            result = _run_sync(run("pytest tests/"))

        assert result.truncated is True
        assert len(result.stdout) < MAX_STDOUT_BYTES + 200  # truncated + notice

    @patch("forge_ide.runner.asyncio")
    def test_stderr_truncation(self, mock_asyncio):
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = ""
        mock_result.stderr = "e" * (MAX_STDERR_BYTES + 500)

        async def fake_run_in_executor(executor, fn):
            return fn()

        mock_loop = MagicMock()
        mock_loop.run_in_executor = MagicMock(side_effect=fake_run_in_executor)
        mock_asyncio.get_event_loop.return_value = mock_loop

        with patch("subprocess.run", return_value=mock_result):
            result = _run_sync(run("pytest tests/"))

        assert result.truncated is True

    @patch("forge_ide.runner.asyncio")
    def test_custom_cwd(self, mock_asyncio):
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = ""
        mock_result.stderr = ""

        captured_kwargs = {}

        def capture_run(*args, **kwargs):
            captured_kwargs.update(kwargs)
            return mock_result

        async def fake_run_in_executor(executor, fn):
            return fn()

        mock_loop = MagicMock()
        mock_loop.run_in_executor = MagicMock(side_effect=fake_run_in_executor)
        mock_asyncio.get_event_loop.return_value = mock_loop

        with patch("subprocess.run", side_effect=capture_run):
            _run_sync(run("pytest tests/", cwd="/my/project"))

        assert captured_kwargs.get("cwd") == "/my/project"

    @patch("forge_ide.runner.asyncio")
    def test_custom_env(self, mock_asyncio):
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = ""
        mock_result.stderr = ""

        captured_kwargs = {}

        def capture_run(*args, **kwargs):
            captured_kwargs.update(kwargs)
            return mock_result

        async def fake_run_in_executor(executor, fn):
            return fn()

        mock_loop = MagicMock()
        mock_loop.run_in_executor = MagicMock(side_effect=fake_run_in_executor)
        mock_asyncio.get_event_loop.return_value = mock_loop

        with patch("subprocess.run", side_effect=capture_run):
            _run_sync(run("pytest tests/", env={"MY_VAR": "val"}))

        env = captured_kwargs.get("env", {})
        assert env.get("MY_VAR") == "val"

    @patch("forge_ide.runner.asyncio")
    def test_duration_tracked(self, mock_asyncio):
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = ""
        mock_result.stderr = ""

        async def fake_run_in_executor(executor, fn):
            return fn()

        mock_loop = MagicMock()
        mock_loop.run_in_executor = MagicMock(side_effect=fake_run_in_executor)
        mock_asyncio.get_event_loop.return_value = mock_loop

        with patch("subprocess.run", return_value=mock_result):
            result = _run_sync(run("pytest tests/"))

        assert result.duration_ms >= 0

    @patch("forge_ide.runner.asyncio")
    def test_executor_exception(self, mock_asyncio):
        """When run_in_executor raises an unexpected exception."""

        async def raise_error(executor, fn):
            raise RuntimeError("executor boom")

        mock_loop = MagicMock()
        mock_loop.run_in_executor = MagicMock(side_effect=raise_error)
        mock_asyncio.get_event_loop.return_value = mock_loop

        result = _run_sync(run("pytest tests/"))
        assert result.exit_code == -1
        assert "executor boom" in result.stderr

    def test_custom_allowed_prefixes(self):
        """Custom prefix allows normally-blocked command type."""
        with pytest.raises(SandboxViolation):
            _run_sync(run("my-tool run"))

        # Should not raise with custom prefix
        # (will still fail at subprocess level since we're not mocking,
        #  but the validation should pass)
        # We test validation separately to confirm
        assert validate_command("my-tool run", allowed_prefixes=("my-tool",)) is None

    @patch("forge_ide.runner.asyncio")
    def test_none_stdout_stderr(self, mock_asyncio):
        """subprocess.run returning None for stdout/stderr."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = None
        mock_result.stderr = None

        async def fake_run_in_executor(executor, fn):
            return fn()

        mock_loop = MagicMock()
        mock_loop.run_in_executor = MagicMock(side_effect=fake_run_in_executor)
        mock_asyncio.get_event_loop.return_value = mock_loop

        with patch("subprocess.run", return_value=mock_result):
            result = _run_sync(run("pytest tests/"))

        assert result.stdout == ""
        assert result.stderr == ""

    @patch("forge_ide.runner.asyncio")
    def test_timeout_none_partial(self, mock_asyncio):
        """TimeoutExpired with None partial output."""
        exc = subprocess.TimeoutExpired("pytest", 5)
        exc.stdout = None
        exc.stderr = None

        async def fake_run_in_executor(executor, fn):
            return fn()

        mock_loop = MagicMock()
        mock_loop.run_in_executor = MagicMock(side_effect=fake_run_in_executor)
        mock_asyncio.get_event_loop.return_value = mock_loop

        with patch("subprocess.run", side_effect=exc):
            result = _run_sync(run("pytest tests/", timeout_s=5))

        assert result.killed is True
        assert result.stdout == ""
        assert result.stderr == ""
