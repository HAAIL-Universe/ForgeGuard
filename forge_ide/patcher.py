"""Patch engine — apply unified diffs and surgical edits to source content.

Provides ``apply_patch()`` for applying a unified diff string to file
content, with fuzzy hunk matching and conflict detection.

Also provides ``apply_edits()`` for applying :class:`Edit` objects
(anchor-based text replacement) with automatic retargeting:
exact match → anchor-based fuzzy search → difflib similarity matching.

All operations work on strings (not files) — the caller is responsible
for reading/writing the filesystem.
"""

from __future__ import annotations

import difflib
import re

from pydantic import BaseModel, ConfigDict, Field

from forge_ide.contracts import Edit, EditInstruction, EditResult
from forge_ide.errors import ParseError, PatchConflict

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_FUZZ: int = 3  # max ± line offset for fuzzy matching

_HUNK_HEADER_RE = re.compile(
    r"^@@\s+-(\d+)(?:,(\d+))?\s+\+(\d+)(?:,(\d+))?\s+@@",
)


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class Hunk(BaseModel):
    """A single hunk parsed from a unified diff."""

    model_config = ConfigDict(frozen=True)

    old_start: int = Field(..., ge=1, description="1-based start line in old file")
    old_count: int = Field(..., ge=0, description="Number of lines in old file")
    new_start: int = Field(..., ge=1, description="1-based start line in new file")
    new_count: int = Field(..., ge=0, description="Number of lines in new file")
    context_before: list[str] = Field(default_factory=list)
    removals: list[str] = Field(default_factory=list)
    additions: list[str] = Field(default_factory=list)
    context_after: list[str] = Field(default_factory=list)
    old_lines: list[str] = Field(default_factory=list, description="All old-side lines in source order")
    new_lines: list[str] = Field(default_factory=list, description="All new-side lines in source order")


class PatchResult(BaseModel):
    """Result of applying a patch to content."""

    model_config = ConfigDict(frozen=True)

    success: bool = Field(..., description="True if all hunks applied")
    path: str = Field(default="", description="File path for context")
    hunks_applied: int = Field(default=0, ge=0)
    hunks_failed: int = Field(default=0, ge=0)
    pre_content: str = Field(default="", description="Content before patching")
    post_content: str = Field(default="", description="Content after patching")
    insertions: int = Field(default=0, ge=0, description="Net lines added")
    deletions: int = Field(default=0, ge=0, description="Net lines removed")


# ---------------------------------------------------------------------------
# Diff parser
# ---------------------------------------------------------------------------


def parse_unified_diff(diff_text: str) -> list[Hunk]:
    """Parse a unified diff string into a list of ``Hunk`` objects.

    Expects standard ``---``/``+++``/``@@ -old,count +new,count @@`` format.
    Returns an empty list for an empty diff.
    Raises ``ParseError`` on malformed input.
    """
    if not diff_text or not diff_text.strip():
        return []

    hunks: list[Hunk] = []
    lines = diff_text.split("\n")

    i = 0
    # Skip --- / +++ file headers if present
    while i < len(lines):
        line = lines[i]
        if line.startswith("---") or line.startswith("+++"):
            i += 1
            continue
        if line.startswith("@@"):
            break
        # Skip any preamble lines (diff --git, index, etc.)
        i += 1

    while i < len(lines):
        line = lines[i]
        if not line.startswith("@@"):
            i += 1
            continue

        m = _HUNK_HEADER_RE.match(line)
        if not m:
            raise ParseError(
                "unified_diff",
                f"Malformed hunk header: {line!r}",
            )

        old_start = int(m.group(1))
        old_count = int(m.group(2)) if m.group(2) is not None else 1
        new_start = int(m.group(3))
        new_count = int(m.group(4)) if m.group(4) is not None else 1

        i += 1
        context_before: list[str] = []
        removals: list[str] = []
        additions: list[str] = []
        context_after: list[str] = []

        # State machine: BEFORE → CHANGES → AFTER
        # BEFORE: context lines before any - or + lines
        # CHANGES: - and + lines (and any interspersed context)
        # AFTER: context lines after all changes
        seen_change = False
        post_change_context: list[str] = []
        old_lines_seq: list[str] = []
        new_lines_seq: list[str] = []

        while i < len(lines):
            ln = lines[i]
            if ln.startswith("@@") or ln.startswith("---") or ln.startswith("+++"):
                break

            if ln.startswith("-"):
                seen_change = True
                if post_change_context:
                    context_after.extend(post_change_context)
                    post_change_context = []
                removals.append(ln[1:])
                old_lines_seq.append(ln[1:])
                i += 1
            elif ln.startswith("+"):
                seen_change = True
                if post_change_context:
                    context_after.extend(post_change_context)
                    post_change_context = []
                additions.append(ln[1:])
                new_lines_seq.append(ln[1:])
                i += 1
            elif ln.startswith(" ") or ln == "":
                content = ln[1:] if ln.startswith(" ") else ln
                if not seen_change:
                    context_before.append(content)
                else:
                    post_change_context.append(content)
                old_lines_seq.append(content)
                new_lines_seq.append(content)
                i += 1
            elif ln.startswith("\\"):
                # "\ No newline at end of file" — skip
                i += 1
            else:
                # Unknown line format — end of hunk
                break

        # Remaining post-change context is the true context_after
        context_after.extend(post_change_context)

        hunks.append(Hunk(
            old_start=old_start,
            old_count=old_count,
            new_start=new_start,
            new_count=new_count,
            context_before=context_before,
            removals=removals,
            additions=additions,
            context_after=context_after,
            old_lines=old_lines_seq,
            new_lines=new_lines_seq,
        ))

    return hunks


# ---------------------------------------------------------------------------
# Hunk matching
# ---------------------------------------------------------------------------


def _match_hunk(
    lines: list[str],
    hunk: Hunk,
    fuzz: int = DEFAULT_FUZZ,
) -> int | None:
    """Find the 0-based line index where *hunk* matches in *lines*.

    Tries exact position first (``hunk.old_start - 1``), then scans
    ±*fuzz* lines around it.  Returns ``None`` if no match found.
    """
    # Build the expected "old" pattern from sequential old_lines when available,
    # falling back to concatenated fields for manually-constructed Hunks.
    pattern = hunk.old_lines if hunk.old_lines else (
        hunk.context_before + hunk.removals + hunk.context_after
    )

    if not pattern:
        # Pure insertion (no old lines to match) — use the target position
        target = max(0, hunk.old_start - 1)
        return min(target, len(lines))

    def _matches_at(pos: int) -> bool:
        if pos < 0 or pos + len(pattern) > len(lines):
            return False
        for j, expected in enumerate(pattern):
            if lines[pos + j] != expected:
                return False
        return True

    # Try exact position first
    exact = hunk.old_start - 1
    if _matches_at(exact):
        return exact

    # Scan ± fuzz
    for offset in range(1, fuzz + 1):
        if _matches_at(exact - offset):
            return exact - offset
        if _matches_at(exact + offset):
            return exact + offset

    return None


# ---------------------------------------------------------------------------
# Patch application
# ---------------------------------------------------------------------------


def apply_patch(
    content: str,
    diff_text: str,
    *,
    path: str = "",
    fuzz: int = DEFAULT_FUZZ,
) -> PatchResult:
    """Apply a unified diff to *content* and return a ``PatchResult``.

    Parameters
    ----------
    content:
        The original file content as a string.
    diff_text:
        A unified diff string to apply.
    path:
        File path for context in error messages and result.
    fuzz:
        Maximum line offset for fuzzy hunk matching.

    Raises
    ------
    PatchConflict
        When a hunk cannot be matched at or near its expected position.
    """
    pre_content = content
    hunks = parse_unified_diff(diff_text)

    if not hunks:
        return PatchResult(
            success=True,
            path=path,
            hunks_applied=0,
            hunks_failed=0,
            pre_content=pre_content,
            post_content=content,
            insertions=0,
            deletions=0,
        )

    lines = content.split("\n")
    # Track how many lines have been shifted by prior hunk applications
    offset = 0
    total_insertions = 0
    total_deletions = 0
    hunks_applied = 0

    for idx, hunk in enumerate(hunks):
        # Adjust hunk position by accumulated offset
        adjusted_hunk = Hunk(
            old_start=hunk.old_start + offset,
            old_count=hunk.old_count,
            new_start=hunk.new_start,
            new_count=hunk.new_count,
            context_before=hunk.context_before,
            removals=hunk.removals,
            additions=hunk.additions,
            context_after=hunk.context_after,
            old_lines=hunk.old_lines,
            new_lines=hunk.new_lines,
        )

        match_pos = _match_hunk(lines, adjusted_hunk, fuzz)
        if match_pos is None:
            # Build expected vs actual for error context
            pattern = hunk.context_before + hunk.removals + hunk.context_after
            expected_start = max(0, hunk.old_start - 1 + offset)
            actual_end = min(len(lines), expected_start + len(pattern))
            actual = lines[expected_start:actual_end]
            raise PatchConflict(
                file_path=path,
                hunk_index=idx,
                expected="\n".join(pattern),
                actual="\n".join(actual),
            )

        # Build old/new sequences — prefer old_lines/new_lines (source order)
        old_seq = hunk.old_lines if hunk.old_lines else (
            hunk.context_before + hunk.removals + hunk.context_after
        )
        new_seq = hunk.new_lines if hunk.new_lines else (
            hunk.context_before + hunk.additions + hunk.context_after
        )
        remove_start = match_pos
        remove_count = len(old_seq)
        new_lines = new_seq

        lines[remove_start:remove_start + remove_count] = new_lines

        # Update offset: difference between new and old line counts
        delta = len(new_lines) - remove_count
        offset += delta

        total_insertions += len(hunk.additions)
        total_deletions += len(hunk.removals)
        hunks_applied += 1

    post_content = "\n".join(lines)

    return PatchResult(
        success=True,
        path=path,
        hunks_applied=hunks_applied,
        hunks_failed=0,
        pre_content=pre_content,
        post_content=post_content,
        insertions=total_insertions,
        deletions=total_deletions,
    )


def apply_multi_patch(
    patches: list[dict[str, str]],
    *,
    fuzz: int = DEFAULT_FUZZ,
) -> list[PatchResult]:
    """Apply multiple patches.  Each dict has ``path``, ``content``, ``diff``.

    Processes all patches in order.  If any raises ``PatchConflict``,
    it propagates immediately — results for successfully-applied patches
    up to that point are not returned (caller manages rollback at the
    filesystem level).

    Returns a list of ``PatchResult`` — one per input patch.
    """
    results: list[PatchResult] = []
    for patch in patches:
        result = apply_patch(
            content=patch["content"],
            diff_text=patch["diff"],
            path=patch.get("path", ""),
            fuzz=fuzz,
        )
        results.append(result)
    return results


# ---------------------------------------------------------------------------
# Surgical edit application with retargeting (Phase 42)
# ---------------------------------------------------------------------------

# Similarity threshold for difflib retargeting
_DIFFLIB_THRESHOLD: float = 0.80

# Maximum line offset when searching for anchor context
_ANCHOR_SEARCH_RANGE: int = 40


def _find_exact(content: str, old_text: str) -> int | None:
    """Find *old_text* in *content*. Return index if unique, else None."""
    idx = content.find(old_text)
    if idx == -1:
        return None
    # Ensure unique — if a second occurrence exists, the match is ambiguous.
    # We still return the first match, as the anchor can disambiguate later.
    return idx


def _find_by_anchor(
    content: str,
    anchor: str,
    old_text: str,
    search_range: int = _ANCHOR_SEARCH_RANGE,
) -> int | None:
    """Locate *old_text* near the *anchor* context in *content*.

    1. Find the anchor text in the content.
    2. Search within ± *search_range* lines of the anchor for *old_text*.
    """
    if not anchor or not anchor.strip():
        return None

    anchor_idx = content.find(anchor.rstrip())
    if anchor_idx == -1:
        # Try matching just the last line of the anchor (more lenient)
        anchor_lines = anchor.rstrip().split("\n")
        last_anchor_line = anchor_lines[-1].strip()
        if not last_anchor_line:
            return None
        anchor_idx = content.find(last_anchor_line)
        if anchor_idx == -1:
            return None

    # Convert anchor position to line number
    anchor_line = content[:anchor_idx].count("\n")
    lines = content.split("\n")

    # Search window around anchor
    lo = max(0, anchor_line - search_range)
    hi = min(len(lines), anchor_line + search_range)
    window = "\n".join(lines[lo:hi])

    idx = window.find(old_text)
    if idx == -1:
        return None

    # Convert back to absolute position
    prefix = "\n".join(lines[:lo])
    abs_offset = len(prefix) + (1 if prefix else 0) + idx
    return abs_offset


def _find_by_difflib(
    content: str,
    old_text: str,
    threshold: float = _DIFFLIB_THRESHOLD,
) -> tuple[int, int] | None:
    """Find the closest match for *old_text* using difflib.

    Returns ``(start_index, end_index)`` of the best match in
    *content*, or ``None`` if no match exceeds *threshold*.
    """
    old_lines = old_text.split("\n")
    content_lines = content.split("\n")
    n = len(old_lines)

    if n == 0 or len(content_lines) == 0:
        return None

    best_ratio = 0.0
    best_start = -1

    # Slide a window of size n over content_lines
    for i in range(max(1, len(content_lines) - n + 1)):
        candidate = content_lines[i : i + n]
        # Quick length-ratio pre-check
        len_ratio = min(len(candidate), n) / max(len(candidate), n, 1)
        if len_ratio < threshold * 0.8:
            continue
        sm = difflib.SequenceMatcher(
            None,
            "\n".join(old_lines),
            "\n".join(candidate),
        )
        ratio = sm.ratio()
        if ratio > best_ratio:
            best_ratio = ratio
            best_start = i

    if best_ratio < threshold or best_start < 0:
        return None

    # Convert line-based window back to character positions
    start_chars = sum(len(content_lines[j]) + 1 for j in range(best_start))
    # The matched region spans n lines
    end_chars = start_chars + sum(
        len(content_lines[best_start + j]) + 1 for j in range(min(n, len(content_lines) - best_start))
    )
    # Remove trailing newline from end
    if end_chars > 0:
        end_chars -= 1

    return (start_chars, end_chars)


def apply_edits(
    content: str,
    edits: list[Edit],
    *,
    file_path: str = "",
) -> EditResult:
    """Apply a list of surgical :class:`Edit` objects to *content*.

    For each edit, tries three strategies in order:

    1. **Exact match** — ``content.find(old_text)``
    2. **Anchor-based fuzzy** — locate ``anchor`` text, then search
       within ±40 lines for ``old_text``
    3. **Difflib similarity** — find the closest match above a 0.80
       similarity threshold

    Returns an :class:`EditResult` with applied/failed edits and the
    final content.
    """
    working = content
    applied: list[Edit] = []
    failed: list[tuple[str, str]] = []
    retargeted = 0

    for edit in edits:
        old = edit.old_text
        new = edit.new_text

        # Strategy 1: exact match
        idx = _find_exact(working, old)
        if idx is not None:
            working = working[:idx] + new + working[idx + len(old):]
            applied.append(edit)
            continue

        # Strategy 2: anchor-based fuzzy
        anchor_idx = _find_by_anchor(working, edit.anchor, old)
        if anchor_idx is not None:
            working = working[:anchor_idx] + new + working[anchor_idx + len(old):]
            applied.append(edit)
            retargeted += 1
            continue

        # Strategy 3: difflib similarity
        match = _find_by_difflib(working, old)
        if match is not None:
            start, end = match
            working = working[:start] + new + working[end:]
            applied.append(edit)
            retargeted += 1
            continue

        # All strategies failed
        preview = old[:80] + ("..." if len(old) > 80 else "")
        failed.append((preview, "No match found (exact, anchor, difflib all failed)"))

    return EditResult(
        success=len(failed) == 0,
        file_path=file_path,
        applied=applied,
        failed=failed,
        final_content=working,
        retargeted=retargeted,
    )


def apply_edit_instruction(
    content: str,
    instruction: EditInstruction,
) -> EditResult:
    """Apply an :class:`EditInstruction` to *content*.

    If ``instruction.full_rewrite`` is True, the content is replaced
    entirely with the first edit's ``new_text``.
    """
    if instruction.full_rewrite and instruction.edits:
        return EditResult(
            success=True,
            file_path=instruction.file_path,
            applied=list(instruction.edits[:1]),
            failed=[],
            final_content=instruction.edits[0].new_text,
            retargeted=0,
        )

    return apply_edits(
        content,
        instruction.edits,
        file_path=instruction.file_path,
    )
