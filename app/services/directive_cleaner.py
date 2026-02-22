"""directive_cleaner.py — Strip contaminating metadata from builder_directives.

Old builder_directives (generated before 2026-02-22) contain three pieces of
metadata that were removed from the system_prompt.md template:

  AEM: enabled.            — triggers Auto-Execute Mode in the legacy CLI
  Auto-authorize: enabled. — skips per-phase user confirmation
  boot_script: true/false  — opt-in boot.ps1 creation gate

These entries cause the Forge Planner to emit contradictions (e.g. "No boot.ps1 in
file manifest — wait, directive says boot_script: true so I will include it").

This module provides clean_builder_directive() which strips these entries and their
inline references from any directive string.  Safe to run on already-clean
directives — idempotent.
"""

from __future__ import annotations
import re


# ── Standalone-line patterns ──────────────────────────────────────────────────
# These lines appear at the END of the directive as a metadata block.

_STANDALONE_PATTERNS = [
    # AEM: enabled.   or   - AEM: enabled
    re.compile(r"^[ \t]*-?[ \t]*AEM:\s*enabled\.?\s*$", re.MULTILINE | re.IGNORECASE),
    # Auto-authorize: enabled.
    re.compile(r"^[ \t]*-?[ \t]*Auto-authorize:\s*enabled\.?\s*$", re.MULTILINE | re.IGNORECASE),
    # boot_script: true / false / [true|false]
    re.compile(
        r"^[ \t]*-?[ \t]*boot_script:\s*(?:true|false|\[true\|false\])\.?\s*$",
        re.MULTILINE | re.IGNORECASE,
    ),
]


# ── Inline-reference patterns ─────────────────────────────────────────────────
# These appear as sub-bullets inside builder steps.

_INLINE_SUBSTITUTIONS: list[tuple[re.Pattern, str]] = [
    # "All PASS" audit result bullet that includes "Because `Auto-authorize: enabled`, ..."
    (
        re.compile(
            r"Because\s+`Auto-authorize:\s*enabled`,\s*commit and proceed directly"
            r" to the next phase without halting\.",
            re.IGNORECASE,
        ),
        "Commit and proceed directly to the next phase.",
    ),
    # Boot-script true branch: whole bullet (may span to end of line)
    (
        re.compile(
            r"^[ \t]*-[ \t]*If\s+`boot_script:\s*true`:.*$",
            re.MULTILINE | re.IGNORECASE,
        ),
        "",
    ),
    # Boot-script false branch: whole bullet (replaces with clean HALT instruction)
    (
        re.compile(
            r"^[ \t]*-[ \t]*If\s+`boot_script:\s*false`:.*$",
            re.MULTILINE | re.IGNORECASE,
        ),
        '7. HALT and report: "All phases complete."',
    ),
    # "**Auto-authorize** means: ..." explanation bullet
    (
        re.compile(
            r"^[ \t]*-[ \t]*\*\*Auto-authorize\*\*\s*means:.*$",
            re.MULTILINE | re.IGNORECASE,
        ),
        "",
    ),
    # Inline backtick variant: `Auto-authorize: enabled`
    (
        re.compile(r"`Auto-authorize:\s*enabled`", re.IGNORECASE),
        "auto-advance",
    ),
]


def clean_builder_directive(content: str) -> tuple[str, list[str]]:
    """Strip AEM/auto-authorize/boot_script contamination from a directive.

    Args:
        content: Raw builder_directive string from the DB.

    Returns:
        (cleaned_content, changes) where `changes` is a list of human-readable
        descriptions of what was removed/replaced — empty if nothing changed.
    """
    original = content
    changes: list[str] = []

    # 1. Strip standalone metadata lines
    for pattern in _STANDALONE_PATTERNS:
        result, n = pattern.subn("", content)
        if n:
            changes.append(f"Removed {n} standalone line(s) matching /{pattern.pattern}/")
            content = result

    # 2. Apply inline substitutions
    for pattern, replacement in _INLINE_SUBSTITUTIONS:
        result, n = pattern.subn(replacement, content)
        if n:
            label = "Removed" if not replacement else "Rewrote"
            changes.append(f"{label} {n} inline reference(s) matching /{pattern.pattern[:40]}…/")
            content = result

    # 3. Collapse runs of 3+ blank lines left by removals
    content = re.sub(r"\n{3,}", "\n\n", content)
    content = content.strip()

    if content != original.strip():
        if not changes:
            changes.append("Whitespace/blank-line cleanup only")
    else:
        changes = []  # nothing actually changed

    return content, changes
