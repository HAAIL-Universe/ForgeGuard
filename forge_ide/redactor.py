"""Secret redaction — detect and replace sensitive values in text.

Pure functions for scanning strings for common secret patterns (API keys,
tokens, connection strings, passwords) and replacing them with
``[REDACTED]``.  All operations are pure — no I/O or side effects.
"""

from __future__ import annotations

import re
from typing import Sequence

from pydantic import BaseModel, ConfigDict


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class SecretMatch(BaseModel):
    """A single secret occurrence found in text."""

    model_config = ConfigDict(frozen=True)

    pattern_name: str
    start: int
    end: int


# ---------------------------------------------------------------------------
# Default patterns
# ---------------------------------------------------------------------------

DEFAULT_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("api_key_sk", re.compile(r"sk-[A-Za-z0-9]{20,}")),
    ("api_key_key", re.compile(r"key-[A-Za-z0-9]{20,}")),
    ("github_pat", re.compile(r"gh[po]_[A-Za-z0-9]{36,}")),
    ("aws_key", re.compile(r"AKIA[A-Z0-9]{16}")),
    (
        "jwt",
        re.compile(
            r"eyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}"
        ),
    ),
    ("pg_conn", re.compile(r"postgresql://\S+")),
    ("mysql_conn", re.compile(r"mysql://\S+")),
    ("password_at", re.compile(r":[^:@\s]{4,}@")),
    (
        "password_eq",
        re.compile(r"(?i)(?:password|passwd|pwd)\s*=\s*\S+"),
    ),
    ("secret_eq", re.compile(r"(?i)secret\s*=\s*\S+")),
    (
        "bearer_token",
        re.compile(r"Bearer\s+[A-Za-z0-9._~+/=-]{20,}"),
    ),
    ("pem_block", re.compile(r"-----BEGIN\s[\w\s]+-----")),
)

REDACTED: str = "[REDACTED]"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _all_patterns(
    extra: Sequence[tuple[str, re.Pattern[str]]] | None,
) -> Sequence[tuple[str, re.Pattern[str]]]:
    """Combine default patterns with optional extras."""
    if extra:
        return list(DEFAULT_PATTERNS) + list(extra)
    return DEFAULT_PATTERNS


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def find_secrets(
    text: str,
    *,
    extra_patterns: list[tuple[str, re.Pattern[str]]] | None = None,
) -> list[SecretMatch]:
    """Return all secret matches found in *text*.

    Matches are returned in order of appearance (by ``start`` offset).
    Overlapping matches from different patterns are all returned.
    """
    patterns = _all_patterns(extra_patterns)
    matches: list[SecretMatch] = []

    for name, pattern in patterns:
        for m in pattern.finditer(text):
            matches.append(
                SecretMatch(pattern_name=name, start=m.start(), end=m.end())
            )

    matches.sort(key=lambda s: s.start)
    return matches


def has_secrets(
    text: str,
    *,
    extra_patterns: list[tuple[str, re.Pattern[str]]] | None = None,
) -> bool:
    """Return ``True`` if *text* contains any secret patterns."""
    patterns = _all_patterns(extra_patterns)
    for _name, pattern in patterns:
        if pattern.search(text):
            return True
    return False


def redact(
    text: str,
    *,
    extra_patterns: list[tuple[str, re.Pattern[str]]] | None = None,
) -> str:
    """Replace all secret matches in *text* with ``[REDACTED]``.

    Works right-to-left to preserve character offsets during replacement.
    Returns the text unchanged if no secrets are found.
    """
    matches = find_secrets(text, extra_patterns=extra_patterns)
    if not matches:
        return text

    # Deduplicate overlapping spans — keep the wider match
    deduped: list[SecretMatch] = []
    for m in matches:
        if deduped and m.start < deduped[-1].end:
            # Overlapping — keep the wider one
            if m.end > deduped[-1].end:
                deduped[-1] = SecretMatch(
                    pattern_name=m.pattern_name,
                    start=deduped[-1].start,
                    end=m.end,
                )
            continue
        deduped.append(m)

    # Replace right-to-left to preserve earlier offsets
    result = text
    for m in reversed(deduped):
        result = result[: m.start] + REDACTED + result[m.end :]

    return result


__all__ = [
    "DEFAULT_PATTERNS",
    "REDACTED",
    "SecretMatch",
    "find_secrets",
    "has_secrets",
    "redact",
]
