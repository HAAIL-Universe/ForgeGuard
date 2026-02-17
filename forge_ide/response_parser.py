"""LLM response parser — classify and clean builder responses.

Detects whether an LLM builder response is a unified diff or full file
content, strips markdown code fences, and normalises trailing newlines.

All functions are pure string processors — no I/O, no side effects.
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Match a unified-diff file header: ``--- a/path`` or ``--- path``
_DIFF_OLD_RE = re.compile(r"^---\s+\S", re.MULTILINE)
_DIFF_NEW_RE = re.compile(r"^\+\+\+\s+\S", re.MULTILINE)

# Match a hunk header: ``@@ -1,3 +1,4 @@``
_HUNK_HEADER_RE = re.compile(r"^@@\s+-\d+", re.MULTILINE)

# Match outermost fenced code block: ``` optionally followed by a lang tag
_FENCE_OPEN_RE = re.compile(r"^```[a-zA-Z0-9_]*\s*$", re.MULTILINE)
_FENCE_CLOSE_RE = re.compile(r"^```\s*$", re.MULTILINE)


# ---------------------------------------------------------------------------
# Model
# ---------------------------------------------------------------------------


class ParsedResponse(BaseModel):
    """Result of parsing a raw LLM builder response."""

    model_config = ConfigDict(frozen=True)

    kind: Literal["diff", "full_content"]
    raw: str = Field(..., description="Original LLM output")
    cleaned: str = Field(..., description="After fence-stripping and trimming")


# ---------------------------------------------------------------------------
# Public functions
# ---------------------------------------------------------------------------


def classify_response(text: str) -> Literal["diff", "full_content"]:
    """Classify *text* as a unified diff or full file content.

    Returns ``"diff"`` only when all three markers are present:

    1. A ``--- <path>`` line (not just ``---`` alone — that's a
       markdown horizontal rule).
    2. A ``+++ <path>`` line.
    3. At least one ``@@ -`` hunk header.

    Otherwise returns ``"full_content"``.
    """
    if not text:
        return "full_content"

    has_old = bool(_DIFF_OLD_RE.search(text))
    has_new = bool(_DIFF_NEW_RE.search(text))
    has_hunk = bool(_HUNK_HEADER_RE.search(text))

    if has_old and has_new and has_hunk:
        return "diff"
    return "full_content"


def strip_fences(text: str) -> str:
    """Remove the outermost markdown code fences from *text*.

    Handles fences with or without a language tag (e.g. ````` ```python `````).
    Only the first opening fence and its matching closing fence are removed.
    Inner fences (nested) are preserved.

    Returns *text* unchanged if no fences are found.
    """
    if not text:
        return text

    lines = text.split("\n")

    # Find first opening fence
    open_idx: int | None = None
    for i, line in enumerate(lines):
        if _FENCE_OPEN_RE.match(line.strip()):
            open_idx = i
            break

    if open_idx is None:
        return text

    # Find matching closing fence (search from end)
    close_idx: int | None = None
    for i in range(len(lines) - 1, open_idx, -1):
        if _FENCE_CLOSE_RE.match(lines[i].strip()):
            close_idx = i
            break

    if close_idx is None:
        return text

    # Extract content between fences
    inner = lines[open_idx + 1 : close_idx]
    # Preserve lines before the opening fence and after the closing fence
    before = lines[:open_idx]
    after = lines[close_idx + 1 :]

    result_lines = before + inner + after
    return "\n".join(result_lines)


def ensure_trailing_newline(text: str) -> str:
    """Append a trailing newline if *text* doesn't already end with one.

    Returns empty string unchanged.
    """
    if not text:
        return text
    if text.endswith("\n"):
        return text
    return text + "\n"


def parse_response(raw: str) -> ParsedResponse:
    """Parse a raw LLM builder response into a ``ParsedResponse``.

    Pipeline:
    1. Strip outermost markdown code fences.
    2. Classify as diff or full content.
    3. For full content: ensure trailing newline.
    4. Build ``ParsedResponse``.
    """
    stripped = strip_fences(raw)
    kind = classify_response(stripped)

    if kind == "full_content":
        cleaned = ensure_trailing_newline(stripped)
    else:
        # Diffs are kept as-is (trailing newline not forced)
        cleaned = stripped

    return ParsedResponse(kind=kind, raw=raw, cleaned=cleaned)


__all__ = [
    "ParsedResponse",
    "classify_response",
    "ensure_trailing_newline",
    "parse_response",
    "strip_fences",
]
