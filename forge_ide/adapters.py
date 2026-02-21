"""Backward-compatibility adapters wrapping existing tool_executor handlers.

Each adapter:
 1. Accepts a validated Pydantic request model.
 2. Calls the corresponding ``tool_executor._exec_*`` handler.
 3. Parses the raw string result into structured ``ToolResponse.data``.
 4. Returns a ``ToolResponse``.

Call ``register_builtin_tools(registry)`` to wire all 7 into a
``Registry`` instance.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from forge_ide.contracts import (
    CheckSyntaxRequest,
    ListDirectoryRequest,
    ReadFileRequest,
    RunCommandRequest,
    RunTestsRequest,
    SearchCodeRequest,
    ToolResponse,
    WriteFileRequest,
)
from app.services.tool_executor import (
    _exec_check_syntax,
    _exec_list_directory,
    _exec_read_file,
    _exec_run_command,
    _exec_run_tests,
    _exec_search_code,
    _exec_write_file,
)

if TYPE_CHECKING:
    from forge_ide.registry import Registry


# ---------------------------------------------------------------------------
# Individual adapters
# ---------------------------------------------------------------------------

_WRITE_OK_RE = re.compile(r"^OK: Wrote (\d+) bytes to (.+)$")
_WRITE_SKIP_RE = re.compile(
    r"^SKIP: (.+?) already exists with identical content \((\d+) bytes\)"
)
_SEARCH_LINE_RE = re.compile(r"^(.+?):(\d+): (.*)$")
_EXIT_CODE_RE = re.compile(r"^Exit code: (-?\d+)", re.MULTILINE)
_STDOUT_RE = re.compile(r"--- stdout ---\n(.*?)(?=\n--- stderr ---|$)", re.DOTALL)
_STDERR_RE = re.compile(r"--- stderr ---\n(.*)", re.DOTALL)
_SYNTAX_ERR_RE = re.compile(
    r"^Syntax error in (.+?):(\d+): (.+)$"
)
_SYNTAX_ERR_LINE_COL_RE = re.compile(
    r"^Syntax error in (.+?):(\d+)(?::(\d+))?: (.+)$"
)


def _adapt_read_file(req: ReadFileRequest, working_dir: str) -> ToolResponse:
    raw = _exec_read_file({"path": req.path}, working_dir)

    if raw.startswith("Error:"):
        return ToolResponse.fail(raw)

    truncated = "[... truncated at" in raw
    content = raw
    size_bytes = len(raw.encode("utf-8"))

    return ToolResponse.ok(
        {
            "path": req.path,
            "content": content,
            "size_bytes": size_bytes,
            "truncated": truncated,
        }
    )


def _adapt_list_directory(
    req: ListDirectoryRequest, working_dir: str
) -> ToolResponse:
    raw = _exec_list_directory({"path": req.path}, working_dir)

    if raw.startswith("Error:"):
        return ToolResponse.fail(raw)

    if raw == "(empty directory)":
        return ToolResponse.ok({"path": req.path, "entries": []})

    entries = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.endswith("/"):
            entries.append({"name": line.rstrip("/"), "is_dir": True})
        else:
            entries.append({"name": line, "is_dir": False})

    return ToolResponse.ok({"path": req.path, "entries": entries})


def _adapt_search_code(req: SearchCodeRequest, working_dir: str) -> ToolResponse:
    inp: dict = {"pattern": req.pattern}
    if req.glob is not None:
        inp["glob"] = req.glob

    raw = _exec_search_code(inp, working_dir)

    if raw.startswith("No matches found"):
        return ToolResponse.ok(
            {
                "pattern": req.pattern,
                "matches": [],
                "total_count": 0,
                "truncated": False,
            }
        )

    matches = []
    truncated = False
    for line in raw.splitlines():
        if line.startswith("... (truncated"):
            truncated = True
            continue
        m = _SEARCH_LINE_RE.match(line)
        if m:
            matches.append(
                {"path": m.group(1), "line": int(m.group(2)), "snippet": m.group(3)}
            )

    return ToolResponse.ok(
        {
            "pattern": req.pattern,
            "matches": matches,
            "total_count": len(matches),
            "truncated": truncated,
        }
    )


def _adapt_write_file(req: WriteFileRequest, working_dir: str) -> ToolResponse:
    raw = _exec_write_file({"path": req.path, "content": req.content}, working_dir)

    m_ok = _WRITE_OK_RE.match(raw)
    if m_ok:
        return ToolResponse.ok(
            {
                "path": m_ok.group(2),
                "bytes_written": int(m_ok.group(1)),
                "created": True,
                "skipped": False,
            }
        )

    m_skip = _WRITE_SKIP_RE.match(raw)
    if m_skip:
        return ToolResponse.ok(
            {
                "path": m_skip.group(1),
                "bytes_written": 0,
                "created": False,
                "skipped": True,
            }
        )

    return ToolResponse.fail(raw)


async def _adapt_run_tests(req: RunTestsRequest, working_dir: str) -> ToolResponse:
    raw = await _exec_run_tests(
        {"command": req.command, "timeout": req.timeout}, working_dir
    )

    return _parse_command_output(raw, req.command)


async def _adapt_check_syntax(
    req: CheckSyntaxRequest, working_dir: str
) -> ToolResponse:
    raw = await _exec_check_syntax({"file_path": req.file_path}, working_dir)

    if raw.startswith("Error:"):
        return ToolResponse.fail(raw)

    if raw.startswith("No syntax errors") or raw.startswith("No errors"):
        return ToolResponse.ok(
            {
                "file_path": req.file_path,
                "valid": True,
                "error_message": None,
                "line": None,
                "column": None,
            }
        )

    # Try to extract line/column from syntax error messages
    m = _SYNTAX_ERR_LINE_COL_RE.match(raw)
    if m:
        return ToolResponse.ok(
            {
                "file_path": m.group(1),
                "valid": False,
                "error_message": m.group(4),
                "line": int(m.group(2)),
                "column": int(m.group(3)) if m.group(3) else None,
            }
        )

    m2 = _SYNTAX_ERR_RE.match(raw)
    if m2:
        return ToolResponse.ok(
            {
                "file_path": m2.group(1),
                "valid": False,
                "error_message": m2.group(3),
                "line": int(m2.group(2)),
                "column": None,
            }
        )

    # TS/JS errors or other output
    return ToolResponse.ok(
        {
            "file_path": req.file_path,
            "valid": False,
            "error_message": raw,
            "line": None,
            "column": None,
        }
    )


async def _adapt_run_command(
    req: RunCommandRequest, working_dir: str
) -> ToolResponse:
    raw = await _exec_run_command(
        {"command": req.command, "timeout": req.timeout}, working_dir
    )

    return _parse_command_output(raw, req.command)


# ---------------------------------------------------------------------------
# Shared parser for run_tests / run_command output
# ---------------------------------------------------------------------------


def _parse_command_output(raw: str, command: str) -> ToolResponse:
    """Parse the ``Exit code: N / stdout / stderr`` format returned by
    ``_exec_run_tests`` and ``_exec_run_command``.
    """
    if raw.startswith("Error:"):
        return ToolResponse.fail(raw)

    exit_code = 0
    m_exit = _EXIT_CODE_RE.search(raw)
    if m_exit:
        exit_code = int(m_exit.group(1))

    stdout = ""
    m_stdout = _STDOUT_RE.search(raw)
    if m_stdout:
        stdout = m_stdout.group(1).strip()

    stderr = ""
    m_stderr = _STDERR_RE.search(raw)
    if m_stderr:
        stderr = m_stderr.group(1).strip()

    truncated = "[... truncated at" in raw

    return ToolResponse.ok(
        {
            "command": command,
            "exit_code": exit_code,
            "stdout": stdout,
            "stderr": stderr,
            "duration_ms": 0,  # actual duration stamped by Registry.dispatch
            "truncated": truncated,
        }
    )


# ---------------------------------------------------------------------------
# Registration helper
# ---------------------------------------------------------------------------

# Tool descriptions — match the existing BUILDER_TOOLS descriptions from
# tool_executor.py so that list_tools() output is backward-compatible.
_TOOL_DESCRIPTIONS: dict[str, str] = {
    "read_file": (
        "Read a file from the project working directory. "
        "Returns the file content (truncated at 50KB). "
        "Use this to check existing code, understand the current state, "
        "and verify your work."
    ),
    "list_directory": (
        "List files and folders in a directory within the working directory. "
        "Returns names with '/' suffix for directories. "
        "Use this to understand the project structure before making changes."
    ),
    "search_code": (
        "Search for a pattern across files in the working directory. "
        "Returns matching file paths and line snippets (max 50 results). "
        "Use this to find existing implementations, imports, or patterns."
    ),
    "write_file": (
        "Write or overwrite a file at a relative path in the working directory. "
        "Creates parent directories automatically. "
        "Preferred over the === FILE: ... === block format for creating files."
    ),
    "run_tests": (
        "Run the project test suite or a subset of tests in the working directory. "
        "Returns exit code, stdout, and stderr. Use this after writing code to verify "
        "it works before signing off a phase. Only sign off when tests pass."
    ),
    "check_syntax": (
        "Check a file for syntax errors. For Python files, uses ast.parse. "
        "For TypeScript/JavaScript, uses tsc/node --check. "
        "Use this after writing files to catch syntax errors immediately."
    ),
    "run_command": (
        "Run a sandboxed shell command in the working directory. "
        "Allowed commands include: pip install, npm install, python -m, npx, "
        "cat, head, tail, wc, find, ls. Destructive commands are blocked."
    ),
}


def register_forge_tools(registry: "Registry", working_dir: str | None = None) -> None:
    """Register 5 Forge governance tools as in-process handlers.

    These are thin disk-reading wrappers that give the agent structured
    access to Forge contracts, phase windows, and scratchpad state —
    without requiring the MCP stdio server to be running.

    Registers: forge_get_contract, forge_list_contracts,
    forge_get_phase_window, forge_get_summary, forge_scratchpad.
    """
    import json as _json
    import re as _re
    from pathlib import Path as _Path
    from pydantic import BaseModel as _BM, Field as _F

    # --- forge_get_contract ---

    class _GetContractRequest(_BM):
        name: str = _F(..., description=(
            "Contract name (e.g. 'builder_contract', 'phases', 'boundaries', "
            "'blueprint', 'stack', 'physics', 'schema')"
        ))

    def _handle_get_contract(req: _GetContractRequest, wd: str) -> ToolResponse:
        contracts_dir = _Path(wd) / "Forge" / "Contracts"
        if not contracts_dir.exists():
            return ToolResponse.fail(f"Forge/Contracts/ not found in working dir: {wd}")
        # Match by base name (case-insensitive)
        name_lower = req.name.lower().replace(" ", "_")
        candidates = [
            f for f in contracts_dir.iterdir()
            if f.is_file() and f.stem.lower().replace(" ", "_") == name_lower
        ]
        if not candidates:
            # Fallback: partial match
            candidates = [f for f in contracts_dir.iterdir() if name_lower in f.stem.lower()]
        if not candidates:
            available = [f.stem for f in contracts_dir.iterdir() if f.is_file()]
            return ToolResponse.fail(
                f"Contract '{req.name}' not found. Available: {', '.join(sorted(available))}"
            )
        target = candidates[0]
        try:
            content = target.read_text(encoding="utf-8")
            return ToolResponse.ok({"name": req.name, "filename": target.name, "content": content})
        except Exception as exc:
            return ToolResponse.fail(f"Cannot read '{target.name}': {exc}")

    registry.register(
        "forge_get_contract",
        _handle_get_contract,
        _GetContractRequest,
        "Read a Forge governance contract from Forge/Contracts/. "
        "Use names like 'builder_contract', 'phases', 'boundaries', 'blueprint', "
        "'stack', 'physics', 'schema', 'manifesto'. "
        "Returns the full contract text for reading governance rules.",
    )

    # --- forge_list_contracts ---

    class _ListContractsRequest(_BM):
        pass  # no params

    def _handle_list_contracts(req: _ListContractsRequest, wd: str) -> ToolResponse:
        contracts_dir = _Path(wd) / "Forge" / "Contracts"
        if not contracts_dir.exists():
            return ToolResponse.fail(f"Forge/Contracts/ not found in {wd}")
        files = sorted(f.name for f in contracts_dir.iterdir() if f.is_file())
        return ToolResponse.ok({"contracts": files, "total": len(files)})

    registry.register(
        "forge_list_contracts",
        _handle_list_contracts,
        _ListContractsRequest,
        "List all available Forge governance contracts in Forge/Contracts/. "
        "Returns filenames and total count. Call this first to discover "
        "what contracts are available before calling forge_get_contract.",
    )

    # --- forge_get_phase_window ---

    class _GetPhaseWindowRequest(_BM):
        phase: int = _F(..., ge=0, description="Current phase number (0-based)")

    def _handle_get_phase_window(req: _GetPhaseWindowRequest, wd: str) -> ToolResponse:
        phases_path = _Path(wd) / "Forge" / "Contracts" / "phases.md"
        if not phases_path.exists():
            return ToolResponse.fail("Forge/Contracts/phases.md not found")
        try:
            content = phases_path.read_text(encoding="utf-8")
            pattern = _re.compile(
                r"(?m)^#{1,3}\s+Phase\s+(\d+)\b(.+?)(?=^#{1,3}\s+Phase\s+\d+|\Z)",
                _re.DOTALL,
            )
            phases = {int(m.group(1)): m.group(0).strip() for m in pattern.finditer(content)}
            current = phases.get(req.phase, "")
            nxt = phases.get(req.phase + 1, "")
            if not current and not nxt:
                # Return raw content if structure not matched
                return ToolResponse.ok({"phase": req.phase, "content": content[:3000]})
            return ToolResponse.ok({
                "phase": req.phase,
                "current": current[:3000],
                "next": nxt[:2000],
                "total_phases": len(phases),
            })
        except Exception as exc:
            return ToolResponse.fail(f"Error reading phases.md: {exc}")

    registry.register(
        "forge_get_phase_window",
        _handle_get_phase_window,
        _GetPhaseWindowRequest,
        "Get the current and next phase deliverables from Forge/Contracts/phases.md. "
        "Provide the current phase number (0-based). Returns phase window context "
        "for understanding what must be built and what comes next.",
    )

    # --- forge_get_summary ---

    class _GetSummaryRequest(_BM):
        pass  # no params

    def _handle_get_summary(req: _GetSummaryRequest, wd: str) -> ToolResponse:
        contracts_dir = _Path(wd) / "Forge" / "Contracts"
        try:
            contracts = (
                sorted(f.name for f in contracts_dir.iterdir() if f.is_file())
                if contracts_dir.exists() else []
            )
            bc_path = contracts_dir / "builder_contract.md"
            bc_preview = ""
            if bc_path.exists():
                text = bc_path.read_text(encoding="utf-8")
                bc_preview = text[:1500] + "..." if len(text) > 1500 else text

            evidence_dir = _Path(wd) / "Forge" / "evidence"
            latest_status = ""
            latest_path = evidence_dir / "test_runs_latest.md"
            if latest_path.exists():
                first_line = latest_path.read_text(encoding="utf-8").splitlines()
                latest_status = first_line[0] if first_line else ""

            return ToolResponse.ok({
                "framework": "Forge",
                "working_dir": wd,
                "contracts": contracts,
                "builder_contract_preview": bc_preview,
                "last_test_status": latest_status,
            })
        except Exception as exc:
            return ToolResponse.fail(f"Error building summary: {exc}")

    registry.register(
        "forge_get_summary",
        _handle_get_summary,
        _GetSummaryRequest,
        "Get a Forge governance overview: list of contracts, builder contract preview, "
        "and last test run status. Use this at the start of a build session to "
        "understand the governance structure without reading every contract.",
    )

    # --- forge_scratchpad ---

    class _ScratchpadRequest(_BM):
        action: str = _F(..., description="Action: 'read' | 'write' | 'append'")
        key: str = _F(default="", description=(
            "Key to read/write. Leave empty to list all keys (read action only)."
        ))
        value: str = _F(default="", description="Value to write or append (write/append only)")

    def _handle_scratchpad(req: _ScratchpadRequest, wd: str) -> ToolResponse:
        pad_path = _Path(wd) / "Forge" / "evidence" / "scratchpad.json"
        pad_path.parent.mkdir(parents=True, exist_ok=True)

        data: dict[str, str] = {}
        if pad_path.exists():
            try:
                data = _json.loads(pad_path.read_text(encoding="utf-8"))
            except Exception:
                data = {}

        if req.action == "read":
            if not req.key:
                return ToolResponse.ok({"keys": list(data.keys()), "total": len(data), "data": data})
            val = data.get(req.key)
            return ToolResponse.ok({"key": req.key, "value": val, "found": val is not None})

        elif req.action == "write":
            data[req.key] = req.value
            pad_path.write_text(_json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
            return ToolResponse.ok({"key": req.key, "action": "written"})

        elif req.action == "append":
            existing = data.get(req.key, "")
            data[req.key] = (existing + "\n" + req.value).strip() if existing else req.value
            pad_path.write_text(_json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
            return ToolResponse.ok({"key": req.key, "action": "appended"})

        else:
            return ToolResponse.fail(
                f"Unknown action '{req.action}'. Use 'read', 'write', or 'append'."
            )

    registry.register(
        "forge_scratchpad",
        _handle_scratchpad,
        _ScratchpadRequest,
        "Read, write, or append key-value notes to Forge/evidence/scratchpad.json. "
        "Persists across context compaction — the only storage that survives a "
        "compaction event. Use 'read' (key='' for all), 'write', or 'append'. "
        "Store: current phase, audit results, key decisions, invariant values.",
    )


def register_builtin_tools(registry: Registry) -> None:
    """Register all 7 existing tool_executor tools with a ``Registry``."""
    registry.register(
        "read_file", _adapt_read_file, ReadFileRequest, _TOOL_DESCRIPTIONS["read_file"]
    )
    registry.register(
        "list_directory",
        _adapt_list_directory,
        ListDirectoryRequest,
        _TOOL_DESCRIPTIONS["list_directory"],
    )
    registry.register(
        "search_code",
        _adapt_search_code,
        SearchCodeRequest,
        _TOOL_DESCRIPTIONS["search_code"],
    )
    registry.register(
        "write_file",
        _adapt_write_file,
        WriteFileRequest,
        _TOOL_DESCRIPTIONS["write_file"],
    )
    registry.register(
        "run_tests",
        _adapt_run_tests,
        RunTestsRequest,
        _TOOL_DESCRIPTIONS["run_tests"],
    )
    registry.register(
        "check_syntax",
        _adapt_check_syntax,
        CheckSyntaxRequest,
        _TOOL_DESCRIPTIONS["check_syntax"],
    )
    registry.register(
        "run_command",
        _adapt_run_command,
        RunCommandRequest,
        _TOOL_DESCRIPTIONS["run_command"],
    )
