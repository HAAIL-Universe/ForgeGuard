"""Context construction — file-block parsing, conversation compaction, directives."""

import logging
import re
from pathlib import Path

from ._state import (
    FORGE_CONTRACTS_DIR,
    FILE_START_PATTERN,
    FILE_END_PATTERN,
    logger,
)

# Re-export the patterns for backward compat
__all__ = [
    "_parse_file_blocks",
    "_strip_code_fence",
    "_compact_conversation",
    "_build_directive",
    "write_forge_config_to_workdir",
    "_extract_phase_window",
    "inject_forge_gitignore",
]


def _parse_file_blocks(text: str) -> list[dict]:
    """Parse file blocks from builder output.

    Expected format:
        === FILE: path/to/file.py ===
        <file contents>
        === END FILE ===

    Returns list of {path, content} dicts.
    """
    blocks: list[dict] = []
    pos = 0
    while pos < len(text):
        start_match = FILE_START_PATTERN.search(text, pos)
        if not start_match:
            break
        file_path = start_match.group(1).strip()
        content_start = start_match.end()

        end_match = FILE_END_PATTERN.search(text, content_start)
        if not end_match:
            logger.warning(
                "Malformed file block (no END delimiter) for: %s", file_path
            )
            pos = content_start
            break

        raw_content = text[content_start:end_match.start()]
        content = _strip_code_fence(raw_content)

        if not file_path:
            logger.warning("Malformed file block: empty path, skipping")
            pos = end_match.end()
            continue

        blocks.append({"path": file_path, "content": content})
        pos = end_match.end()

    return blocks


def _strip_code_fence(text: str) -> str:
    """Strip optional markdown code fence wrapper from file content."""
    stripped = text.strip()
    if stripped.startswith("```"):
        first_nl = stripped.find("\n")
        if first_nl >= 0:
            stripped = stripped[first_nl + 1:]
    if stripped.rstrip().endswith("```"):
        stripped = stripped.rstrip()[:-3]
    return stripped.rstrip("\n") + "\n" if stripped.strip() else ""


def _compact_conversation(
    messages: list[dict],
    files_written: list[dict] | None = None,
    current_phase: str = "",
    journal_summary: str = "",
    use_mcp_contracts: bool = False,
) -> list[dict]:
    """Compact a conversation by summarizing older turns.

    Keeps the first message (directive) and last 2 assistant/user pairs
    intact.  Middle turns are replaced with a progress summary.

    If *journal_summary* is provided (from a SessionJournal), it replaces
    the lossy turn-by-turn summary with a dense, structured state document.

    When *use_mcp_contracts* is True, the summary extracts which contracts
    were fetched (from forge_get_contract tool calls) and adds a re-fetch
    hint so the builder knows to call forge tools again.
    """
    if len(messages) <= 5:
        return list(messages)

    directive = messages[0]
    tail = messages[-4:]

    # In MCP mode, track which contracts were fetched in compacted turns
    fetched_contracts: set[str] = set()
    if use_mcp_contracts:
        for msg in messages[1:-4]:
            content = msg.get("content", "")
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "tool_use":
                        name = block.get("name", "")
                        inp = block.get("input", {})
                        if name == "forge_get_contract":
                            fetched_contracts.add(inp.get("name", "?"))
                        elif name == "forge_get_phase_window":
                            fetched_contracts.add(f"phase_window({inp.get('phase_number', '?')})")

    if journal_summary:
        # Use journal-based summary — dense and accurate
        summary_parts = [
            "[Context compacted — journal-based summary]\n",
            journal_summary,
            "\n",
        ]
        if files_written:
            summary_parts.append(
                "\nThese files are ALREADY written to disk. "
                "Do NOT re-write them. Continue with the NEXT unwritten file.\n"
            )
    else:
        summary_parts = [
            "[Context compacted — progress summary]\n",
            f"Current phase: {current_phase}\n",
        ]

        if files_written:
            summary_parts.append(f"\nFiles written so far ({len(files_written)}):\n")
            for f in files_written:
                summary_parts.append(f"  - {f['path']} ({f['size_bytes']} bytes)\n")
            summary_parts.append(
                "\nThese files are ALREADY written to disk. "
                "Do NOT re-write them. Continue with the NEXT unwritten file.\n"
            )
        else:
            summary_parts.append("\nNo files written yet.\n")

        for msg in messages[1:-4]:
            role = msg.get("role", "unknown")
            content = msg.get("content", "")
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "tool_use":
                        summary_parts.append(
                            f"[tool_use]: {block.get('name', '?')}\n"
                        )
            elif isinstance(content, str) and len(content) > 200:
                content = content[:200] + "..."
                summary_parts.append(f"[{role}]: {content}\n")

    # MCP-aware: note fetched contracts and re-fetch hint
    if use_mcp_contracts and fetched_contracts:
        summary_parts.append(
            f"\nContracts previously fetched: {', '.join(sorted(fetched_contracts))}\n"
            "Contract data has been compacted away — re-fetch any contract you need "
            "using `forge_get_contract(name)` or `forge_get_phase_window(N)`.\n"
            "Use `forge_scratchpad(\"read\", key)` to retrieve your saved notes.\n"
        )
    elif use_mcp_contracts:
        summary_parts.append(
            "\nContract data has been compacted. Re-fetch as needed via forge tools.\n"
        )

    summary_msg = {
        "role": "user",
        "content": "\n".join(summary_parts),
    }

    return [directive, summary_msg] + tail



# ---------------------------------------------------------------------------
# .gitignore injection — Phase 46
# ---------------------------------------------------------------------------

_FORGE_GITIGNORE_RULES = [
    "# Forge contracts (server-side only — do not push)",
    "Forge/",
    "forge.json",
    "*.forge-contract",
    ".forge/",
    "",
    "# Environment & build artifacts",
    ".venv/",
    ".env",
    ".env.local",
    "node_modules/",
    "__pycache__/",
    "*.pyc",
    "*.pyo",
    "dist/",
    "build/",
    ".pytest_cache/",
    ".mypy_cache/",
]

_FORGE_GITIGNORE_MARKER = "# Forge contracts (server-side only — do not push)"


def inject_forge_gitignore(working_dir: str | Path) -> bool:
    """Ensure the target repo's ``.gitignore`` excludes Forge contract files.

    * If ``.gitignore`` exists and already contains the marker comment,
      this is a no-op (idempotent).
    * If ``.gitignore`` exists without the rules, the rules are appended.
    * If ``.gitignore`` does not exist, it is created with the rules.

    Returns ``True`` if the file was modified/created, ``False`` if already
    correct.
    """
    gi_path = Path(working_dir) / ".gitignore"

    if gi_path.exists():
        existing = gi_path.read_text(encoding="utf-8")
        if _FORGE_GITIGNORE_MARKER in existing:
            return False  # already injected
        # Append rules — ensure blank line separator
        separator = "" if existing.endswith("\n") else "\n"
        gi_path.write_text(
            existing + separator + "\n".join(_FORGE_GITIGNORE_RULES) + "\n",
            encoding="utf-8",
        )
        logger.info("Appended Forge exclusion rules to .gitignore in %s", working_dir)
        return True

    # No .gitignore — create one
    gi_path.write_text("\n".join(_FORGE_GITIGNORE_RULES) + "\n", encoding="utf-8")
    logger.info("Created .gitignore with Forge exclusion rules in %s", working_dir)
    return True


def write_forge_config_to_workdir(
    working_dir: str | Path,
    project: dict,
) -> bool:
    """Write forge.json to the project root from the stored forge_config.

    The forge_config is populated during contract generation (in _template_stack)
    and stored inside questionnaire_state.forge_config.  It contains the
    machine-readable operational config the builder reads at boot:
    test commands, venv path, entry module, frontend dir, etc.

    Returns True if written, False if no config was found (e.g. contracts
    were generated before this feature was added).
    """
    import json as _json
    qs = project.get("questionnaire_state") or {}
    forge_config = qs.get("forge_config")
    if not forge_config:
        return False
    path = Path(working_dir) / "forge.json"
    path.write_text(_json.dumps(forge_config, indent=2), encoding="utf-8")
    logger.info("forge.json written to %s", path)
    return True


def _extract_phase_window(
    contracts: list[dict], current_phase_num: int,
) -> str:
    """Extract text for current phase + next phase from the phases contract."""
    phases_content = ""
    for c in contracts:
        if c["contract_type"] == "phases":
            phases_content = c["content"]
            break
    if not phases_content:
        return ""

    phase_blocks = re.split(r"(?=^## Phase )", phases_content, flags=re.MULTILINE)
    target_nums = {current_phase_num, current_phase_num + 1}
    selected: list[str] = []
    for block in phase_blocks:
        header = re.match(
            r"^## Phase\s+(\d+)\s*[-—–]+\s*(.+)", block, re.MULTILINE,
        )
        if header and int(header.group(1)) in target_nums:
            selected.append(block.strip())

    if not selected:
        return ""

    return (
        "## Phase Window (current + next)\n\n"
        + "\n\n---\n\n".join(selected)
    )
