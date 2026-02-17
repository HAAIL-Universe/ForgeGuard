"""Diff generator — produce unified diffs from old/new content pairs.

Uses ``difflib.unified_diff`` to generate standards-compliant unified
diffs, and wraps the output in the existing ``UnifiedDiff`` contract
model from ``forge_ide.contracts``.

All operations work on strings — no filesystem access.
"""

from __future__ import annotations

import difflib

from forge_ide.contracts import UnifiedDiff


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _normalise_path(path: str) -> str:
    r"""Normalise path separators: backslash → forward slash."""
    return path.replace("\\", "/")


# ---------------------------------------------------------------------------
# Core functions
# ---------------------------------------------------------------------------


def generate_diff(
    old_content: str,
    new_content: str,
    *,
    path: str = "",
    context_lines: int = 3,
) -> UnifiedDiff:
    """Generate a unified diff between *old_content* and *new_content*.

    Line endings are normalised to LF before diffing.
    Path separators are normalised to forward slashes.
    Returns a ``UnifiedDiff`` model with hunk strings and counts.
    """
    # Normalise path separators
    norm_path = _normalise_path(path)

    # Normalise line endings
    old = old_content.replace("\r\n", "\n").replace("\r", "\n")
    new = new_content.replace("\r\n", "\n").replace("\r", "\n")

    old_lines = old.split("\n")
    new_lines = new.split("\n")

    diff_lines = list(difflib.unified_diff(
        old_lines,
        new_lines,
        fromfile=f"a/{norm_path}",
        tofile=f"b/{norm_path}",
        lineterm="",
        n=context_lines,
    ))

    if not diff_lines:
        return UnifiedDiff(path=norm_path, hunks=[], insertions=0, deletions=0)

    # Split into hunks (each starting with @@)
    hunks: list[str] = []
    current_hunk_lines: list[str] = []

    for line in diff_lines:
        if line.startswith("---") or line.startswith("+++"):
            continue  # skip file headers
        if line.startswith("@@"):
            if current_hunk_lines:
                hunks.append("\n".join(current_hunk_lines))
            current_hunk_lines = [line]
        else:
            current_hunk_lines.append(line)

    if current_hunk_lines:
        hunks.append("\n".join(current_hunk_lines))

    # Count insertions and deletions from the raw diff lines
    insertions = 0
    deletions = 0
    for line in diff_lines:
        if line.startswith("+") and not line.startswith("+++"):
            insertions += 1
        elif line.startswith("-") and not line.startswith("---"):
            deletions += 1

    return UnifiedDiff(
        path=norm_path,
        hunks=hunks,
        insertions=insertions,
        deletions=deletions,
    )


def generate_multi_diff(
    changes: list[dict[str, str]],
) -> list[UnifiedDiff]:
    """Generate diffs for multiple file changes.

    Each dict should have ``path``, ``old``, and ``new`` keys.
    Returns one ``UnifiedDiff`` per input change.
    """
    return [
        generate_diff(
            old_content=change["old"],
            new_content=change["new"],
            path=change.get("path", "file"),
        )
        for change in changes
    ]


def diff_to_text(diff: UnifiedDiff) -> str:
    """Render a ``UnifiedDiff`` model back to a standard unified diff string.

    Produces the ``---``/``+++``/hunk format suitable for
    ``parse_unified_diff`` or display.  Path separators are normalised
    to forward slashes and trailing whitespace is stripped from each line.
    """
    norm_path = _normalise_path(diff.path)
    parts: list[str] = [
        f"--- a/{norm_path}",
        f"+++ b/{norm_path}",
    ]
    for hunk in diff.hunks:
        # Strip trailing whitespace from each line within a hunk
        stripped_lines = [ln.rstrip() for ln in hunk.split("\n")]
        parts.append("\n".join(stripped_lines))

    return "\n".join(parts)
