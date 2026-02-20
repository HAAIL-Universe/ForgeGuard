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
from .local import get_invariants, get_summary, list_contracts, load_contract
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

TOOL_DEFINITIONS = [
    {
        "name": "forge_summary",
        "description": (
            "Get a compact overview of the entire ForgeGuard governance "
            "framework — all contracts, invariants, layer boundaries, and "
            "available endpoints. Call this FIRST to understand the framework."
        ),
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "forge_list_contracts",
        "description": (
            "List all available governance contracts with their names, "
            "filenames, and formats (json/yaml/markdown)."
        ),
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "forge_get_contract",
        "description": (
            "Read the full content of a specific governance contract. "
            "Use forge_list_contracts first to see available names. "
            "Key contracts: boundaries (architecture rules), physics "
            "(API spec), builder_directive (build rules), stack (tech stack)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": (
                        "Contract name — one of: boundaries, physics, "
                        "blueprint, builder_contract, builder_directive, "
                        "manifesto, phases, schema, stack, system_prompt, "
                        "ui, auditor_prompt, recovery_planner_prompt, "
                        "remediation, desktop_distribution_plan"
                    ),
                },
            },
            "required": ["name"],
        },
    },
    {
        "name": "forge_get_invariants",
        "description": (
            "Get all invariant gate definitions — hard constraints enforced "
            "during builds. Includes constraint types (MONOTONIC_UP, EQUAL, "
            "etc.) and default values."
        ),
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "forge_get_boundaries",
        "description": (
            "Get architectural layer boundary rules — which imports and "
            "patterns are forbidden in each layer (routers, repos, clients, "
            "audit_engine, services). Violation of these rules fails the build."
        ),
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "forge_get_physics",
        "description": (
            "Get the canonical API specification (physics.yaml) — the "
            "single source of truth for every endpoint, auth type, request "
            "and response schemas."
        ),
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "forge_get_directive",
        "description": (
            "Get the builder directive — the system prompt and rules that "
            "govern how the AI builder operates during code generation."
        ),
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "forge_get_stack",
        "description": (
            "Get the technology stack contract — required languages, "
            "frameworks, versions, and dependencies."
        ),
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
    # ── Artifact store (MCP temp pattern) ─────────────────────────────────
    {
        "name": "forge_store_artifact",
        "description": (
            "Store a generated artifact (contract, scout dossier, renovation "
            "plan, builder directive, phase output, etc.) in the MCP artifact "
            "store for on-demand retrieval by other sub-agents.  Use this "
            "instead of embedding large content in agent system prompts."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_id": {
                    "type": "string",
                    "description": "Project identifier",
                },
                "artifact_type": {
                    "type": "string",
                    "description": (
                        "Category: contract | scout | renovation | "
                        "directive | phase | seal | diff"
                    ),
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
                    "description": "Memory TTL in hours (default: 24).  Disk copies are permanent.",
                },
                "persist": {
                    "type": "boolean",
                    "description": "Write to disk for cross-session durability (default: true)",
                },
            },
            "required": ["project_id", "artifact_type", "key", "content"],
        },
    },
    {
        "name": "forge_get_artifact",
        "description": (
            "Retrieve a previously stored artifact by project ID, type, and "
            "key.  Checks in-memory store first, then disk.  Use this to "
            "lazy-load contracts or other large context on demand."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_id": {"type": "string"},
                "artifact_type": {
                    "type": "string",
                    "description": "contract | scout | renovation | directive | phase | seal | diff",
                },
                "key": {
                    "type": "string",
                    "description": "Artifact key, e.g. 'manifesto', 'dossier'",
                },
            },
            "required": ["project_id", "artifact_type", "key"],
        },
    },
    {
        "name": "forge_list_artifacts",
        "description": (
            "List all stored artifacts for a project, optionally filtered "
            "by type.  Shows key, source (memory/disk), age, and size."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_id": {"type": "string"},
                "artifact_type": {
                    "type": "string",
                    "description": "Optional filter: contract | scout | renovation | directive | phase | seal | diff",
                },
            },
            "required": ["project_id"],
        },
    },
    {
        "name": "forge_clear_artifacts",
        "description": (
            "Remove all stored artifacts for a project (memory + disk), "
            "optionally scoped to a single artifact type."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_id": {"type": "string"},
                "artifact_type": {
                    "type": "string",
                    "description": "Optional — clear only this type",
                },
            },
            "required": ["project_id"],
        },
    },
    # ── Project-scoped contract tools (Phase E) ──────────────────────────
    {
        "name": "forge_set_session",
        "description": (
            "Set session-level defaults for the MCP server instance. "
            "Called once by the build orchestrator before spawning sub-agents. "
            "Project-scoped tools auto-resolve project_id and build_id from "
            "the session when the caller omits them."
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
    },
    {
        "name": "forge_clear_session",
        "description": (
            "Reset the MCP session to blank. Clears project_id, build_id, "
            "and user_id defaults."
        ),
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "forge_get_project_context",
        "description": (
            "Get a combined manifest for a project — project info, list of "
            "available generated contracts (types, versions, sizes), build "
            "count, and latest snapshot batch. Returns METADATA only, not "
            "full contract content. Call this first to see what's available, "
            "then fetch specific contracts with forge_get_project_contract."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_id": {
                    "type": "string",
                    "description": (
                        "Project identifier. Optional if session is set "
                        "via forge_set_session."
                    ),
                },
            },
            "required": [],
        },
    },
    {
        "name": "forge_list_project_contracts",
        "description": (
            "List all generated contracts for the current project — contract "
            "types, versions, and last updated timestamps. Lighter than "
            "forge_get_project_context when you only need the contract list."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_id": {
                    "type": "string",
                    "description": (
                        "Project identifier. Optional if session is set."
                    ),
                },
            },
            "required": [],
        },
    },
    {
        "name": "forge_get_project_contract",
        "description": (
            "Fetch a single generated contract for the current project from "
            "the database. Returns the full content, version, and source. "
            "Use forge_list_project_contracts to see available types first. "
            "Common types: manifesto, stack, physics, boundaries, blueprint, "
            "builder_directive, schema, ui."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_id": {
                    "type": "string",
                    "description": (
                        "Project identifier. Optional if session is set."
                    ),
                },
                "contract_type": {
                    "type": "string",
                    "description": (
                        "Contract type — e.g. manifesto, stack, physics, "
                        "boundaries, blueprint, builder_directive, schema, "
                        "ui, phases, system_prompt, remediation"
                    ),
                },
            },
            "required": ["contract_type"],
        },
    },
    {
        "name": "forge_get_build_contracts",
        "description": (
            "Fetch the pinned contract snapshot for a specific build. "
            "Returns all contracts that were frozen when the build started. "
            "These are immutable — mid-build edits don't affect them. "
            "Used by the Fixer role to reference the exact contracts the "
            "build was executed against."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "build_id": {
                    "type": "string",
                    "description": (
                        "Build identifier. Optional if session is set "
                        "via forge_set_session."
                    ),
                },
            },
            "required": [],
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


# Sentinel returned by _dispatch_local when an async DB fetch is needed
_NEED_ASYNC = {"__redirect_to_db__": True}


async def _resolve_contract_from_db(
    name: str, arguments: dict[str, Any],
) -> dict[str, Any]:
    """Fetch a generated contract from the Neon DB via project-scoped API.

    Maps the generic tool names (forge_get_contract, forge_get_boundaries,
    etc.) to the project-scoped handler that reads from PostgreSQL.
    """
    # Determine contract type from tool name or arguments
    contract_type: str | None = None
    match name:
        case "forge_get_contract":
            contract_type = arguments.get("name")
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
