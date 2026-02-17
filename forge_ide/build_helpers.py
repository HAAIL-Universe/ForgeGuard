"""Build helpers — higher-level primitives for the builder loop.

Composes existing ``forge_ide`` modules to provide:

- ``apply_response`` — try patch, fallback to full content.
- ``run_and_summarise`` — run a command and auto-summarise output.
- ``ApplyResult`` / ``VerificationResult`` — structured result models.

``apply_response`` is synchronous (string processing only).
``run_and_summarise`` is async (delegates to ``runner.run``).
"""

from __future__ import annotations

from typing import Literal, Union

from pydantic import BaseModel, ConfigDict, Field

from forge_ide.errors import PatchConflict
from forge_ide.lang import DiagnosticReport
from forge_ide.log_parser import (
    BuildSummary,
    GenericSummary,
    NpmTestSummary,
    PytestSummary,
    auto_summarise,
)
from forge_ide.patcher import apply_patch
from forge_ide.response_parser import parse_response
from forge_ide.runner import RunResult
from forge_ide.runner import run as ide_run

# Type alias for any summary type
Summary = Union[PytestSummary, NpmTestSummary, BuildSummary, GenericSummary]

# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class ApplyResult(BaseModel):
    """Result of applying an LLM response to existing file content."""

    model_config = ConfigDict(frozen=True)

    content: str = Field(..., description="Resulting file content")
    method: Literal["patch", "full"] = Field(
        ..., description="How the response was applied"
    )
    hunks_applied: int = Field(default=0, ge=0)
    had_conflict: bool = Field(
        default=False,
        description="True if patch failed and fell back to full content",
    )


class VerificationResult(BaseModel):
    """Structured output from phase verification."""

    model_config = ConfigDict(frozen=True)

    diagnostics: DiagnosticReport | None = None
    test_summary: PytestSummary | None = None
    fixes_applied: int = Field(default=0, ge=0)


# ---------------------------------------------------------------------------
# apply_response
# ---------------------------------------------------------------------------


def apply_response(
    original: str,
    llm_response: str,
    *,
    path: str = "",
) -> ApplyResult:
    """Apply an LLM builder response to existing file content.

    1. Parse the response (strip fences, classify).
    2. If classified as ``"full_content"`` → return as-is.
    3. If classified as ``"diff"`` → try ``apply_patch``.
    4. On ``PatchConflict`` → fallback: return the cleaned response
       as full content and set ``had_conflict=True``.

    Parameters
    ----------
    original:
        Current file content (before modification).
    llm_response:
        Raw LLM builder output (may be fenced code, diff, or raw).
    path:
        File path for patch-engine error context.

    Returns
    -------
    ApplyResult
        The resulting content and metadata about the application method.
    """
    parsed = parse_response(llm_response)

    if parsed.kind == "full_content":
        return ApplyResult(content=parsed.cleaned, method="full")

    # Attempt to apply as a unified diff
    try:
        result = apply_patch(original, parsed.cleaned, path=path)
        return ApplyResult(
            content=result.post_content,
            method="patch",
            hunks_applied=result.hunks_applied,
        )
    except PatchConflict:
        # Fallback: the cleaned diff text is NOT valid file content,
        # so we return the cleaned text and signal the conflict.
        # The caller should re-request as full file content.
        return ApplyResult(
            content=parsed.cleaned,
            method="full",
            had_conflict=True,
        )


# ---------------------------------------------------------------------------
# run_and_summarise
# ---------------------------------------------------------------------------


async def run_and_summarise(
    command: str,
    *,
    cwd: str | None = None,
    timeout_s: int = 60,
) -> tuple[RunResult, Summary]:
    """Run a command and auto-summarise the output.

    Combines ``ide_run`` (subprocess execution) with ``auto_summarise``
    (log parsing) into a single async call.

    Parameters
    ----------
    command:
        Shell command to execute.
    cwd:
        Working directory (optional, defaults to runner's default).
    timeout_s:
        Timeout in seconds for the subprocess.

    Returns
    -------
    tuple[RunResult, Summary]
        The run result and the parsed summary (PytestSummary,
        NpmTestSummary, BuildSummary, or GenericSummary).
    """
    result = await ide_run(command, cwd=cwd, timeout_s=timeout_s)
    summary = auto_summarise(result)
    return result, summary


__all__ = [
    "ApplyResult",
    "Summary",
    "VerificationResult",
    "apply_response",
    "run_and_summarise",
]
