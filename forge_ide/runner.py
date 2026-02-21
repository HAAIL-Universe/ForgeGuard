"""Command runner — structured subprocess execution with safety controls.

Provides ``run()`` for executing sandboxed shell commands and returning
structured ``RunResult`` models.  Command validation, environment isolation,
timeout management and output truncation are all handled transparently.

No LLM involvement — this is a pure systems layer.
"""

from __future__ import annotations

import asyncio
import os
import time
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field

from forge_ide.errors import SandboxViolation

if TYPE_CHECKING:
    pass  # reserved for future type-only imports

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MAX_STDOUT_BYTES: int = 50_000  # 50 KB
MAX_STDERR_BYTES: int = 10_000  # 10 KB
DEFAULT_TIMEOUT_S: int = 120

INJECTION_CHARS: frozenset[str] = frozenset({
    ";", "|", "&", "`", "$", "(", ")", "{", "}",
})

BLOCKED_COMMANDS: frozenset[str] = frozenset({
    "rm", "del", "rmdir", "curl", "wget", "ssh", "scp",
    "git push", "git remote", "shutdown", "reboot",
    "format", "mkfs", "dd ", "chmod", "chown",
})

RUN_TESTS_PREFIXES: tuple[str, ...] = (
    "pytest", "python -m pytest", "python3 -m pytest",
    "npm test", "npm run test", "npx vitest", "npx jest",
)

RUN_COMMAND_PREFIXES: tuple[str, ...] = (
    "pip install", "pip3 install",
    "npm install", "npx ",
    "python -m ", "python3 -m ",
    "python forge/scripts/", "python3 forge/scripts/",  # Forge governance scripts
    "cat ", "head ", "tail ", "wc ", "find ", "ls ",
    "dir ",   # Windows
    "type ",  # Windows cat equivalent
)

ALL_ALLOWED_PREFIXES: tuple[str, ...] = RUN_TESTS_PREFIXES + RUN_COMMAND_PREFIXES

# Env vars safe to propagate (no secrets).
_SAFE_ENV_KEYS: tuple[str, ...] = (
    "PATH", "SYSTEMROOT", "TEMP", "TMP",
    "HOME", "USERPROFILE", "VIRTUAL_ENV",
)


# ---------------------------------------------------------------------------
# Model
# ---------------------------------------------------------------------------


class RunResult(BaseModel):
    """Structured result of a subprocess invocation."""

    model_config = ConfigDict(frozen=True)

    exit_code: int = Field(..., description="Process exit code (-1 if crashed)")
    stdout: str = Field(default="", description="Captured stdout (may be truncated)")
    stderr: str = Field(default="", description="Captured stderr (may be truncated)")
    duration_ms: int = Field(default=0, ge=0, description="Wall-clock duration in ms")
    truncated: bool = Field(
        default=False,
        description="True if stdout or stderr was truncated",
    )
    killed: bool = Field(
        default=False,
        description="True if the process was killed due to timeout",
    )
    command: str = Field(..., description="The command that was executed")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def validate_command(
    command: str,
    allowed_prefixes: tuple[str, ...] | None = None,
) -> str | None:
    """Check *command* against safety rules.

    Returns ``None`` when the command is acceptable, or an error-message
    string explaining why it was rejected.
    """
    if not command or not command.strip():
        return "Error: Command is empty"

    cmd = command.strip()

    # Injection character check
    for ch in cmd:
        if ch in INJECTION_CHARS:
            return f"Error: Command contains disallowed character '{ch}'"

    # Blocked-command check
    cmd_lower = cmd.lower()
    for blocked in BLOCKED_COMMANDS:
        if cmd_lower.startswith(blocked):
            return f"Error: Command '{blocked}' is not allowed"

    # Allowlist check
    prefixes = allowed_prefixes if allowed_prefixes is not None else ALL_ALLOWED_PREFIXES
    if not any(cmd_lower.startswith(p) for p in prefixes):
        return (
            f"Error: Command not in allowlist. "
            f"Allowed prefixes: {', '.join(prefixes)}"
        )

    return None


def _build_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    """Build a restricted environment dict for subprocess execution.

    Only propagates a small set of non-secret variables from the host
    environment, plus any caller-supplied *extra* vars.
    """
    env: dict[str, str] = {}
    for key in _SAFE_ENV_KEYS:
        val = os.environ.get(key)
        if val:
            env[key] = val
    if extra:
        env.update(extra)
    return env


def _truncate(text: str, max_bytes: int) -> tuple[str, bool]:
    """Truncate *text* to at most *max_bytes* characters.

    Returns ``(text, False)`` when no truncation occurred, or
    ``(truncated_text, True)`` with an appended notice otherwise.
    """
    if len(text) <= max_bytes:
        return text, False
    return (
        text[:max_bytes] + f"\n\n[... truncated at {max_bytes} bytes ...]",
        True,
    )


# ---------------------------------------------------------------------------
# Core runner
# ---------------------------------------------------------------------------


async def run(
    command: str,
    *,
    timeout_s: int = DEFAULT_TIMEOUT_S,
    cwd: str | None = None,
    env: dict[str, str] | None = None,
    allowed_prefixes: tuple[str, ...] | None = None,
) -> RunResult:
    """Execute *command* in a sandboxed subprocess and return a ``RunResult``.

    Parameters
    ----------
    command:
        Shell command string.  Must pass ``validate_command``.
    timeout_s:
        Maximum wall-clock seconds before the process is killed.
    cwd:
        Working directory for the subprocess.  ``None`` → inherit.
    env:
        Extra environment variables merged on top of the safe base set.
    allowed_prefixes:
        Override the default ``ALL_ALLOWED_PREFIXES`` for validation.

    Raises
    ------
    SandboxViolation
        When the command fails validation (injection, blocked, not in
        allowlist).
    """
    error = validate_command(command, allowed_prefixes)
    if error:
        raise SandboxViolation(error)

    import subprocess as _sp

    merged_env = _build_env(env)
    start = time.perf_counter()

    def _sync() -> tuple[int, str, str, bool]:
        """Run in a thread so the event loop stays free."""
        try:
            result = _sp.run(
                command,
                capture_output=True,
                text=True,
                cwd=cwd,
                env=merged_env,
                shell=True,
                timeout=timeout_s,
            )
            return result.returncode, result.stdout or "", result.stderr or "", False
        except _sp.TimeoutExpired as exc:
            partial_out = (exc.stdout or b"") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
            partial_err = (exc.stderr or b"") if isinstance(exc.stderr, bytes) else (exc.stderr or "")
            if isinstance(partial_out, bytes):
                partial_out = partial_out.decode("utf-8", errors="replace")
            if isinstance(partial_err, bytes):
                partial_err = partial_err.decode("utf-8", errors="replace")
            return -1, partial_out, partial_err, True

    loop = asyncio.get_event_loop()
    try:
        exit_code, raw_out, raw_err, was_killed = await loop.run_in_executor(
            None, _sync,
        )
    except Exception as exc:
        elapsed = int((time.perf_counter() - start) * 1000)
        return RunResult(
            exit_code=-1,
            stdout="",
            stderr=f"Error: {exc}",
            duration_ms=elapsed,
            truncated=False,
            killed=False,
            command=command,
        )

    elapsed = int((time.perf_counter() - start) * 1000)

    stdout, trunc_out = _truncate(raw_out, MAX_STDOUT_BYTES)
    stderr, trunc_err = _truncate(raw_err, MAX_STDERR_BYTES)

    return RunResult(
        exit_code=exit_code,
        stdout=stdout,
        stderr=stderr,
        duration_ms=elapsed,
        truncated=trunc_out or trunc_err,
        killed=was_killed,
        command=command,
    )
