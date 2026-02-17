"""TypeScript / JavaScript language intelligence — parse tool outputs and extract symbols.

All functions are **pure** (string in → model out).  No subprocess execution,
no filesystem access.

Parsers
-------
- ``parse_tsc_output``   — tsc ``--noEmit --pretty false`` output
- ``parse_eslint_json``  — eslint ``--format json`` output

Extractors
----------
- ``extract_symbols``    — regex-based export/class/interface/type/enum outline
"""

from __future__ import annotations

import json
import re

from forge_ide.contracts import Diagnostic
from forge_ide.errors import ParseError
from forge_ide.lang import Symbol

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# TSC error line format:  file.ts(line,col): error TS1234: message
_TSC_LINE_RE = re.compile(
    r"^(.+?)\((\d+),(\d+)\):\s+(error|warning|info)\s+(TS\d+):\s+(.+)$",
)

# Symbol extraction regexes for TS/JS
_TS_EXPORT_RE = re.compile(
    r"^(?:export\s+)?(?:default\s+)?"
    r"(?:declare\s+)?"
    r"(function\*?|class|interface|type|enum|const|let|var|abstract\s+class)"
    r"\s+([A-Za-z_$][A-Za-z0-9_$]*)",
    re.MULTILINE,
)

_TS_KIND_MAP: dict[str, str] = {
    "function": "function",
    "function*": "function",
    "class": "class",
    "abstract class": "class",
    "interface": "interface",
    "type": "type_alias",
    "enum": "enum",
    "const": "constant",
    "let": "variable",
    "var": "variable",
}


# ---------------------------------------------------------------------------
# TSC output parser
# ---------------------------------------------------------------------------


def parse_tsc_output(raw: str) -> list[Diagnostic]:
    """Parse ``tsc --noEmit --pretty false`` output into diagnostics.

    Line format::

        file.ts(line,col): error TS2304: Cannot find name 'foo'.

    Returns an empty list for empty input.
    """
    if not raw or not raw.strip():
        return []

    diagnostics: list[Diagnostic] = []

    for line in raw.split("\n"):
        line = line.rstrip()
        if not line:
            continue

        m = _TSC_LINE_RE.match(line)
        if not m:
            continue

        file_path = m.group(1).strip()
        line_no = int(m.group(2))
        col = int(m.group(3))
        severity = m.group(4).lower()
        code = m.group(5)
        message = m.group(6).strip()

        diagnostics.append(Diagnostic(
            file=file_path,
            line=line_no,
            column=col,
            message=message,
            severity=_map_tsc_severity(severity),
            code=code,
        ))

    return diagnostics


def _map_tsc_severity(raw: str) -> str:
    """Map tsc severity to standard levels."""
    mapping = {"error": "error", "warning": "warning", "info": "info"}
    return mapping.get(raw, "error")


# ---------------------------------------------------------------------------
# ESLint JSON parser
# ---------------------------------------------------------------------------


def parse_eslint_json(raw: str) -> list[Diagnostic]:
    """Parse ``eslint --format json`` output into diagnostics.

    Expected shape (array of file results)::

        [{"filePath": "...", "messages": [
            {"ruleId": "no-unused-vars", "severity": 2,
             "message": "...", "line": 1, "column": 5}
        ]}]

    ESLint severities: ``1`` = warning, ``2`` = error.

    Returns an empty list for empty input.
    Raises ``ParseError`` on invalid JSON.
    """
    if not raw or not raw.strip():
        return []

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ParseError(raw, "eslint_json") from exc

    if not isinstance(data, list):
        raise ParseError(raw, "eslint_json")

    diagnostics: list[Diagnostic] = []

    for file_entry in data:
        file_path = file_entry.get("filePath", "")
        messages = file_entry.get("messages", [])
        if not isinstance(messages, list):
            continue

        for msg in messages:
            sev_num = msg.get("severity", 1)
            diagnostics.append(Diagnostic(
                file=file_path,
                line=msg.get("line", 1),
                column=msg.get("column", 0),
                message=msg.get("message", ""),
                severity="error" if sev_num >= 2 else "warning",
                code=msg.get("ruleId"),
            ))

    return diagnostics


# ---------------------------------------------------------------------------
# Symbol extraction
# ---------------------------------------------------------------------------


def extract_symbols(source: str) -> list[Symbol]:
    """Extract top-level symbols from TypeScript / JavaScript source.

    Uses regex to identify:
    - ``export function``, ``function``
    - ``export class``, ``class``, ``abstract class``
    - ``export interface``, ``interface``
    - ``export type ... =``, ``type ... =``
    - ``export enum``, ``enum``
    - ``export const``, ``const``, ``let``, ``var``

    Does NOT extract nested members (methods, properties).
    Returns an empty list for empty source.
    """
    if not source or not source.strip():
        return []

    symbols: list[Symbol] = []
    lines = source.split("\n")

    for match in _TS_EXPORT_RE.finditer(source):
        kind_raw = match.group(1).strip()
        name = match.group(2)

        kind = _TS_KIND_MAP.get(kind_raw, "variable")

        # Calculate line number
        start_pos = match.start()
        start_line = source[:start_pos].count("\n") + 1

        # Estimate end line — find the closing brace for blocks
        if kind in ("class", "interface", "enum", "function"):
            end_line = _find_block_end(lines, start_line - 1)
        else:
            end_line = start_line  # single-line declarations

        symbols.append(Symbol(
            name=name,
            kind=kind,
            start_line=start_line,
            end_line=end_line,
        ))

    return symbols


def _find_block_end(lines: list[str], start_idx: int) -> int:
    """Find the closing brace of a block starting at *start_idx*.

    Falls back to *start_idx + 1* if no opening brace found.
    Returns 1-based line number.
    """
    depth = 0
    found_open = False

    for i in range(start_idx, len(lines)):
        for ch in lines[i]:
            if ch == "{":
                depth += 1
                found_open = True
            elif ch == "}":
                depth -= 1
                if found_open and depth == 0:
                    return i + 1  # 1-based

    # No closing brace found — return start
    return start_idx + 1


__all__ = [
    "extract_symbols",
    "parse_eslint_json",
    "parse_tsc_output",
]
