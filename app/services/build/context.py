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
    "_write_contracts_to_workdir",
    "_extract_phase_window",
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
) -> list[dict]:
    """Compact a conversation by summarizing older turns.

    Keeps the first message (directive) and last 2 assistant/user pairs
    intact.  Middle turns are replaced with a progress summary.

    If *journal_summary* is provided (from a SessionJournal), it replaces
    the lossy turn-by-turn summary with a dense, structured state document.
    """
    if len(messages) <= 5:
        return list(messages)

    directive = messages[0]
    tail = messages[-4:]

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

    summary_msg = {
        "role": "user",
        "content": "\n".join(summary_parts),
    }

    return [directive, summary_msg] + tail


def _build_directive(contracts: list[dict]) -> str:
    """Assemble the builder directive from project contracts.

    Includes the universal builder_contract.md and all per-project
    contracts **except** ``phases`` (which uses a sliding window).
    """
    parts = ["# Forge Governance & Project Contracts\n"]

    builder_contract_path = FORGE_CONTRACTS_DIR / "builder_contract.md"
    if builder_contract_path.exists():
        parts.append("\n---\n## builder_contract (universal governance)\n")
        parts.append(builder_contract_path.read_text(encoding="utf-8"))
        parts.append("\n")
    else:
        logger.warning("builder_contract.md not found at %s", builder_contract_path)

    parts.append("\n---\n# Per-Project Contracts\n")
    type_order = [
        "blueprint", "manifesto", "stack", "schema", "physics",
        "boundaries", "ui", "builder_directive",
    ]
    sorted_contracts = sorted(
        contracts,
        key=lambda c: (
            type_order.index(c["contract_type"])
            if c["contract_type"] in type_order
            else len(type_order)
        ),
    )
    for contract in sorted_contracts:
        if contract["contract_type"] == "phases":
            continue
        parts.append(f"\n---\n## {contract['contract_type']}\n")
        parts.append(contract["content"])
        parts.append("\n")
    return "\n".join(parts)


def _write_contracts_to_workdir(
    working_dir: str, contracts: list[dict],
) -> list[str]:
    """Write per-project contract files into ``Forge/Contracts/`` in the repo."""
    contracts_dir = Path(working_dir) / "Forge" / "Contracts"
    contracts_dir.mkdir(parents=True, exist_ok=True)
    written: list[str] = []
    for contract in contracts:
        ctype = contract["contract_type"]
        content = contract["content"]
        ext = ".md"
        if ctype == "boundaries":
            ext = ".json"
        elif ctype == "physics":
            ext = ".yaml"
        path = contracts_dir / f"{ctype}{ext}"
        path.write_text(content, encoding="utf-8")
        written.append(f"Forge/Contracts/{ctype}{ext}")
    return written


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
