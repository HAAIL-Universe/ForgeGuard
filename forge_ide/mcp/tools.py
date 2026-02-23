"""MCP tool definitions and dispatch logic."""

from __future__ import annotations

import logging
import time
from typing import Any

logger = logging.getLogger(__name__)

from .artifact_store import (
    clear_artifacts,
    get_artifact,
    list_artifacts,
    store_artifact,
)
from .config import LOCAL_MODE
from .local import get_governance, get_invariants, get_summary, list_contracts, load_contract
from .project import (
    get_build_contracts,
    get_project_contract,
    get_project_context,
    list_project_contracts,
)
from .remote import api_get
from .session import clear_session, get_session, set_session

# Contract types that are generated per-project and live in the Neon DB.
# When a session is active, these should be fetched from the DB rather
# than from local disk.  Static governance templates (system_prompt,
# auditor_prompt, etc.) remain on disk.
_DB_CONTRACT_TYPES = frozenset({
    "manifesto", "blueprint", "stack", "schema", "physics",
    "boundaries", "ui", "phases", "builder_directive",
})

# ── Tool catalogue ────────────────────────────────────────────────────────

# Artifact store tools are always served in-process (not proxied to API)
_ARTIFACT_TOOLS = frozenset(
    {"forge_store_artifact", "forge_get_artifact", "forge_list_artifacts", "forge_clear_artifacts"}
)

# Planner tools always run in-process against the local planner package
_PLANNER_TOOLS = frozenset({"forge_run_planner"})

# Project-scoped tools always proxy to ForgeGuard API (DB contracts)
_PROJECT_TOOLS = frozenset(
    {
        "forge_set_session",
        "forge_clear_session",
        "forge_get_project_context",
        "forge_list_project_contracts",
        "forge_get_project_contract",
        "forge_get_build_contracts",
    }
)

# ── Annotation presets ────────────────────────────────────────────────────

_READ_ONLY = {
    "readOnlyHint": True,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": False,
}

_WRITE = {
    "readOnlyHint": False,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": False,
}

# ── Tool definitions ─────────────────────────────────────────────────────
# Single list for all modes.  16 tools (removed 4 redundant shortcuts,
# added forge_get_governance).  Descriptions optimised for Forge agents
# (Scout, Coder, Auditor, Planner, Fixer) as primary consumers.
#
# The 4 shortcut tools (forge_get_boundaries, forge_get_physics,
# forge_get_directive, forge_get_stack) are no longer advertised.
# Use forge_get_contract(name="boundaries") etc. instead.
# Dispatch still handles them for backward compat (returns deprecation hint).

TOOL_DEFINITIONS: list[dict] = [
    # ── Governance (read contracts from disk or DB) ───────────────────────
    {
        "name": "forge_get_governance",
        "description": (
            "Get all governance rules in one call — builder contract "
            "(master rules), architecture layer boundaries (forbidden "
            "imports), tech stack constraints, and invariant gates. "
            "Call this first before any code generation or modification."
        ),
        "inputSchema": {"type": "object", "properties": {}, "required": []},
        "annotations": _READ_ONLY,
    },
    {
        "name": "forge_summary",
        "description": (
            "Get a compact overview of the ForgeGuard framework — contract "
            "names, architectural layers, invariant names, and available "
            "tool endpoints. Lighter than forge_get_governance when you "
            "only need metadata."
        ),
        "inputSchema": {"type": "object", "properties": {}, "required": []},
        "annotations": _READ_ONLY,
    },
    {
        "name": "forge_get_contract",
        "description": (
            "Read a specific governance contract by name. Returns full "
            "content (parsed JSON/YAML or raw markdown). Key contracts: "
            "boundaries (layer rules), physics (API spec), stack (tech "
            "requirements), builder_directive (AI build rules), "
            "builder_contract (master governance)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Contract name to fetch.",
                    "enum": [
                        "boundaries", "physics", "blueprint",
                        "builder_contract", "builder_directive",
                        "manifesto", "phases", "schema", "stack",
                        "system_prompt", "ui", "auditor_prompt",
                        "recovery_planner_prompt", "remediation",
                        "desktop_distribution_plan",
                    ],
                },
            },
            "required": ["name"],
        },
        "annotations": _READ_ONLY,
    },
    {
        "name": "forge_list_contracts",
        "description": (
            "List all available governance contracts with names, filenames, "
            "and formats. Use before forge_get_contract to discover what "
            "contracts exist."
        ),
        "inputSchema": {"type": "object", "properties": {}, "required": []},
        "annotations": _READ_ONLY,
    },
    {
        "name": "forge_get_invariants",
        "description": (
            "Get invariant gate definitions — hard constraints enforced "
            "between build phases. Includes constraint types (MONOTONIC_UP, "
            "EQUAL, etc.), default values, and descriptions."
        ),
        "inputSchema": {"type": "object", "properties": {}, "required": []},
        "annotations": _READ_ONLY,
    },
    # ── Artifact store ────────────────────────────────────────────────────
    {
        "name": "forge_store_artifact",
        "description": (
            "Store a generated artifact (contract, scout dossier, plan, "
            "phase output, diff, seal) for retrieval by other agents. "
            "Persists to memory (TTL-aware) and optionally to disk."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_id": {
                    "type": "string",
                    "description": "Project identifier (UUID)",
                },
                "artifact_type": {
                    "type": "string",
                    "description": "Category of artifact.",
                    "enum": [
                        "contract", "scout", "renovation",
                        "directive", "phase", "seal", "diff",
                    ],
                },
                "key": {
                    "type": "string",
                    "description": (
                        "Artifact key within its type, e.g. 'manifesto', "
                        "'dossier', 'stack_profile', 'executive_brief'"
                    ),
                },
                "content": {
                    "description": "Artifact content (string or JSON-serialisable value)",
                },
                "ttl_hours": {
                    "type": "number",
                    "description": "Memory TTL in hours (default 24). Disk copies are permanent.",
                },
                "persist": {
                    "type": "boolean",
                    "description": "Also write to disk for cross-session durability (default true).",
                },
            },
            "required": ["project_id", "artifact_type", "key", "content"],
        },
        "annotations": _WRITE,
    },
    {
        "name": "forge_get_artifact",
        "description": (
            "Retrieve a stored artifact by project, type, and key. "
            "Checks in-memory store first (TTL-aware), then disk."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_id": {
                    "type": "string",
                    "description": "Project identifier (UUID)",
                },
                "artifact_type": {
                    "type": "string",
                    "description": "Category of artifact.",
                    "enum": [
                        "contract", "scout", "renovation",
                        "directive", "phase", "seal", "diff",
                    ],
                },
                "key": {
                    "type": "string",
                    "description": "Artifact key, e.g. 'manifesto', 'dossier'",
                },
            },
            "required": ["project_id", "artifact_type", "key"],
        },
        "annotations": _READ_ONLY,
    },
    {
        "name": "forge_list_artifacts",
        "description": (
            "List all stored artifacts for a project. Optionally filter "
            "by type. Shows key, source (memory/disk), age, and size."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_id": {
                    "type": "string",
                    "description": "Project identifier (UUID)",
                },
                "artifact_type": {
                    "type": "string",
                    "description": "Optional filter by artifact category.",
                    "enum": [
                        "contract", "scout", "renovation",
                        "directive", "phase", "seal", "diff",
                    ],
                },
            },
            "required": ["project_id"],
        },
        "annotations": _READ_ONLY,
    },
    {
        "name": "forge_clear_artifacts",
        "description": (
            "Delete all stored artifacts for a project (memory + disk). "
            "Optionally scope to a single artifact type."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_id": {
                    "type": "string",
                    "description": "Project identifier (UUID)",
                },
                "artifact_type": {
                    "type": "string",
                    "description": "Optional — delete only this type.",
                    "enum": [
                        "contract", "scout", "renovation",
                        "directive", "phase", "seal", "diff",
                    ],
                },
            },
            "required": ["project_id"],
        },
        "annotations": {
            "readOnlyHint": False,
            "destructiveHint": True,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    },
    # ── Session management ────────────────────────────────────────────────
    {
        "name": "forge_set_session",
        "description": (
            "Set session-level defaults (project_id, build_id, user_id). "
            "Call once at build start. Project-scoped tools then auto-resolve "
            "IDs from the session without repeating them."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_id": {
                    "type": "string",
                    "description": "Project identifier (UUID)",
                },
                "build_id": {
                    "type": "string",
                    "description": "Build identifier (UUID) — optional",
                },
                "user_id": {
                    "type": "string",
                    "description": "User identifier (UUID) — optional",
                },
            },
            "required": ["project_id"],
        },
        "annotations": _WRITE,
    },
    {
        "name": "forge_clear_session",
        "description": (
            "Reset the MCP session to blank. Clears project_id, build_id, "
            "and user_id defaults."
        ),
        "inputSchema": {"type": "object", "properties": {}, "required": []},
        "annotations": _WRITE,
    },
    # ── Project-scoped contract tools (reads from Neon DB) ────────────────
    {
        "name": "forge_get_project_context",
        "description": (
            "Get project manifest — project info, list of generated "
            "contracts (types, versions, sizes), build count. Returns "
            "metadata only, not full contract content. Call this first, "
            "then use forge_get_project_contract for specific contracts."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_id": {
                    "type": "string",
                    "description": "Project identifier (UUID). Optional if session is set.",
                },
            },
            "required": [],
        },
        "annotations": _READ_ONLY,
    },
    {
        "name": "forge_list_project_contracts",
        "description": (
            "List all generated contracts for the project — types, versions, "
            "last-updated timestamps. Lighter than forge_get_project_context."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_id": {
                    "type": "string",
                    "description": "Project identifier (UUID). Optional if session is set.",
                },
            },
            "required": [],
        },
        "annotations": _READ_ONLY,
    },
    {
        "name": "forge_get_project_contract",
        "description": (
            "Fetch a single generated contract for the project from the "
            "database. Returns full content, version, and source. "
            "Common types: manifesto, blueprint, stack, schema, physics, "
            "boundaries, ui, phases, builder_directive."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_id": {
                    "type": "string",
                    "description": "Project identifier (UUID). Optional if session is set.",
                },
                "contract_type": {
                    "type": "string",
                    "description": "Contract type to fetch.",
                    "enum": [
                        "manifesto", "blueprint", "stack", "schema",
                        "physics", "boundaries", "ui", "phases",
                        "builder_directive",
                    ],
                },
            },
            "required": ["contract_type"],
        },
        "annotations": _READ_ONLY,
    },
    {
        "name": "forge_get_build_contracts",
        "description": (
            "Fetch the pinned contract snapshot for a build. Returns all "
            "contracts frozen at build start (immutable). Use this to "
            "reference the exact rules the build was executed against."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "build_id": {
                    "type": "string",
                    "description": "Build identifier (UUID). Optional if session is set.",
                },
            },
            "required": [],
        },
        "annotations": _READ_ONLY,
    },
    # ── Planner ───────────────────────────────────────────────────────────
    {
        "name": "forge_run_planner",
        "description": (
            "Run the Forge Planner Agent. Analyses the project request and "
            "generates a complete build plan covering all phases. Returns "
            "plan path, phase list, token usage, and turn-by-turn trace."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_request": {
                    "type": "string",
                    "description": (
                        "Natural-language description of what to build — "
                        "stack, features, constraints."
                    ),
                },
            },
            "required": ["project_request"],
        },
        "annotations": {
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": True,
        },
    },
]


# ── Dispatch ──────────────────────────────────────────────────────────────


async def dispatch(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Route tool calls — artifact store, project-scoped, then local/remote."""
    start = time.perf_counter()
    logger.info("[mcp:call]   %s  args=%s", name, _summarise(arguments))
    # Artifact store tools are always served in-process regardless of LOCAL_MODE
    if name in _ARTIFACT_TOOLS:
        result = _dispatch_artifact(name, arguments)
        _log_result(name, result, start)
        return result
    # Project-scoped tools always proxy to ForgeGuard API
    if name in _PROJECT_TOOLS:
        result = await _dispatch_project(name, arguments)
        _log_result(name, result, start)
        return result
    # Planner tools always run in-process (local planner package)
    if name in _PLANNER_TOOLS:
        result = await _dispatch_planner(name, arguments)
        _log_result(name, result, start)
        return result
    if LOCAL_MODE:
        result = _dispatch_local(name, arguments)
        if isinstance(result, dict) and result.get("__redirect_to_db__"):
            # Local dispatch determined this contract lives in the DB
            result = await _resolve_contract_from_db(name, arguments)
        _log_result(name, result, start)
        return result
    result = await _dispatch_remote(name, arguments)
    _log_result(name, result, start)
    return result


def _summarise(args: dict[str, Any], max_len: int = 200) -> str:
    """One-line summary of MCP tool arguments."""
    try:
        raw = ", ".join(f"{k}={v!r}" for k, v in args.items())
    except Exception:
        raw = str(args)
    return raw[:max_len] + ("…" if len(raw) > max_len else "")


def _log_result(name: str, result: dict[str, Any], start: float) -> None:
    elapsed = int((time.perf_counter() - start) * 1000)
    if "error" in result:
        logger.warning("[mcp:result] %s  ERROR (%dms): %s", name, elapsed, result["error"])
    else:
        logger.info("[mcp:result] %s  OK (%dms)", name, elapsed)


def _dispatch_artifact(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Serve artifact store tool calls from in-process store."""
    project_id = arguments.get("project_id")
    artifact_type = arguments.get("artifact_type")
    key = arguments.get("key")

    match name:
        case "forge_store_artifact":
            if not all([project_id, artifact_type, key]):
                return {"error": "Missing required parameters: project_id, artifact_type, key"}
            content = arguments.get("content")
            ttl_hours = float(arguments.get("ttl_hours", 24))
            persist = bool(arguments.get("persist", True))
            return store_artifact(project_id, artifact_type, key, content, ttl_hours, persist)

        case "forge_get_artifact":
            if not all([project_id, artifact_type, key]):
                return {"error": "Missing required parameters: project_id, artifact_type, key"}
            return get_artifact(project_id, artifact_type, key)

        case "forge_list_artifacts":
            if not project_id:
                return {"error": "Missing required parameter: project_id"}
            return list_artifacts(project_id, artifact_type)

        case "forge_clear_artifacts":
            if not project_id:
                return {"error": "Missing required parameter: project_id"}
            return clear_artifacts(project_id, artifact_type)

        case _:
            return {"error": f"Unknown artifact tool: {name}"}


async def _dispatch_project(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Serve project-scoped tool calls via ForgeGuard API."""
    match name:
        case "forge_set_session":
            pid = arguments.get("project_id")
            if not pid:
                return {"error": "Missing required parameter: project_id"}
            return set_session(
                project_id=pid,
                build_id=arguments.get("build_id"),
                user_id=arguments.get("user_id"),
            )
        case "forge_clear_session":
            return clear_session()
        case "forge_get_project_context":
            return await get_project_context(arguments)
        case "forge_list_project_contracts":
            return await list_project_contracts(arguments)
        case "forge_get_project_contract":
            return await get_project_contract(arguments)
        case "forge_get_build_contracts":
            return await get_build_contracts(arguments)
        case _:
            return {"error": f"Unknown project tool: {name}"}


def _dispatch_local(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Serve from local disk — but redirect DB contracts when session active."""
    match name:
        case "forge_get_governance":
            return get_governance()
        case "forge_summary":
            return get_summary()
        case "forge_list_contracts":
            return list_contracts()
        case "forge_get_contract":
            contract_name = arguments.get("name")
            if not contract_name:
                return {"error": "Missing required parameter: name"}
            # DB-backed contracts redirect to project handler when session set
            session = get_session()
            if contract_name in _DB_CONTRACT_TYPES and session.project_id:
                logger.info(
                    "[mcp:local] redirecting %s to DB (project=%s)",
                    contract_name, session.project_id,
                )
                return _NEED_ASYNC  # handled in dispatch()
            return load_contract(contract_name)
        case "forge_get_invariants":
            return get_invariants()
        # Deprecated shortcuts — not advertised but still handled for
        # backward compatibility with older agent prompts.
        case "forge_get_boundaries":
            session = get_session()
            if session.project_id:
                return _NEED_ASYNC
            return load_contract("boundaries")
        case "forge_get_physics":
            session = get_session()
            if session.project_id:
                return _NEED_ASYNC
            return load_contract("physics")
        case "forge_get_directive":
            session = get_session()
            if session.project_id:
                return _NEED_ASYNC
            return load_contract("builder_directive")
        case "forge_get_stack":
            session = get_session()
            if session.project_id:
                return _NEED_ASYNC
            return load_contract("stack")
        case _:
            return {"error": f"Unknown tool: {name}"}


# ── Planner dispatch ──────────────────────────────────────────────────────


async def _dispatch_planner(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    match name:
        case "forge_run_planner":
            return await _run_planner(arguments)
        case _:
            return {"error": f"Unknown planner tool: {name}"}


async def _run_planner(arguments: dict[str, Any]) -> dict[str, Any]:
    """Run the standalone planner agent and return a turn-by-turn trace."""
    import asyncio as _asyncio
    import sys

    from .config import FORGEGUARD_ROOT

    project_request = arguments.get("project_request", "").strip()
    if not project_request:
        return {"error": "Missing required parameter: project_request"}

    planner_dir = FORGEGUARD_ROOT / "planner"
    if not planner_dir.exists():
        return {"error": f"Planner directory not found: {planner_dir}"}

    planner_str = str(planner_dir)
    if planner_str not in sys.path:
        sys.path.insert(0, planner_str)

    try:
        from planner_agent import PlannerError, run_planner  # type: ignore[import]
    except ImportError as exc:
        return {"error": f"Cannot import planner_agent: {exc}"}

    import threading as _threading

    # Collect per-turn data from the planner loop via callback.
    # The callback fires in the executor thread — list.append is GIL-safe.
    turns: list[dict] = []
    _stop_event = _threading.Event()

    def _turn_callback(turn_data: dict) -> None:
        turns.append(turn_data)

    loop = _asyncio.get_event_loop()
    try:
        result = await loop.run_in_executor(
            None,
            lambda: run_planner(
                project_request=project_request,
                verbose=False,
                turn_callback=_turn_callback,
                stop_event=_stop_event,
            ),
        )
    except _asyncio.CancelledError:
        _stop_event.set()
        raise
    except PlannerError as exc:
        return {
            "error": str(exc),
            "turns_completed": len(turns),
            "turns": turns,
        }
    except Exception as exc:
        return {"error": f"Planner crashed: {exc}", "turns": turns}

    u = result["token_usage"]
    savings_pct = u["cache_read_input_tokens"] / max(u["input_tokens"], 1) * 100

    return {
        "plan_path": result["plan_path"],
        "iterations": result["iterations"],
        "phases": [
            {"number": p["number"], "name": p["name"]}
            for p in result["plan"].get("phases", [])
        ],
        "token_usage": {**u, "cache_savings_pct": round(savings_pct, 1)},
        "turns": turns,
    }


# Sentinel returned by _dispatch_local when an async DB fetch is needed
_NEED_ASYNC = {"__redirect_to_db__": True}


async def _resolve_contract_from_db(
    name: str, arguments: dict[str, Any],
) -> dict[str, Any]:
    """Fetch a generated contract from the Neon DB via project-scoped API.

    Maps tool names to the project-scoped handler that reads from PostgreSQL.
    Handles both forge_get_contract and deprecated shortcut tool names.
    """
    contract_type: str | None = None
    match name:
        case "forge_get_contract":
            contract_type = arguments.get("name")
        # Deprecated shortcuts — backward compat
        case "forge_get_boundaries":
            contract_type = "boundaries"
        case "forge_get_physics":
            contract_type = "physics"
        case "forge_get_directive":
            contract_type = "builder_directive"
        case "forge_get_stack":
            contract_type = "stack"

    if not contract_type:
        return {"error": f"Cannot resolve contract type for tool: {name}"}

    return await get_project_contract({"contract_type": contract_type})


async def _dispatch_remote(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Proxy to ForgeGuard API."""
    # When a session is active and the tool requests a DB-backed contract,
    # fetch from the project-scoped API (Neon) instead of the generic
    # /forge/contracts/ routes (which serve static local files).
    session = get_session()
    if session.project_id:
        contract_name: str | None = None
        match name:
            case "forge_get_contract":
                contract_name = arguments.get("name")
            case "forge_get_boundaries":
                contract_name = "boundaries"
            case "forge_get_physics":
                contract_name = "physics"
            case "forge_get_directive":
                contract_name = "builder_directive"
            case "forge_get_stack":
                contract_name = "stack"
        if contract_name and contract_name in _DB_CONTRACT_TYPES:
            logger.info(
                "[mcp:remote] redirecting %s to DB (project=%s)",
                contract_name, session.project_id,
            )
            return await get_project_contract({"contract_type": contract_name})

    match name:
        case "forge_get_governance":
            # In remote mode, fall back to local governance reader
            return get_governance()
        case "forge_summary":
            return await api_get("/forge/summary")
        case "forge_list_contracts":
            return await api_get("/forge/contracts")
        case "forge_get_contract":
            contract_name = arguments.get("name")
            if not contract_name:
                return {"error": "Missing required parameter: name"}
            return await api_get(f"/forge/contracts/{contract_name}")
        case "forge_get_invariants":
            return await api_get("/forge/invariants")
        case "forge_get_boundaries":
            return await api_get("/forge/boundaries")
        case "forge_get_physics":
            return await api_get("/forge/physics")
        case "forge_get_directive":
            return await api_get("/forge/directive")
        case "forge_get_stack":
            return await api_get("/forge/stack")
        case _:
            return {"error": f"Unknown tool: {name}"}
