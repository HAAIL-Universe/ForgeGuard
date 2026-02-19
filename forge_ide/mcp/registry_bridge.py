"""Bridge Forge governance + MCP tools into the IDE Registry.

Three tool families are registered:

1. **Forge workspace tools** — read contracts from ``Forge/Contracts/`` in the
   build working directory (``forge_get_contract``, ``forge_get_phase_window``,
   ``forge_list_contracts``, ``forge_get_summary``, ``forge_scratchpad``).

2. **MCP project tools** — proxy to the ForgeGuard API for DB-stored contracts
   (``forge_get_project_context``, ``forge_list_project_contracts``,
   ``forge_get_project_contract``, ``forge_get_build_contracts``,
   ``forge_set_session``, ``forge_clear_session``).

3. **MCP artifact tools** — in-process artifact store for cross-agent data
   (``forge_store_artifact``, ``forge_get_artifact``, ``forge_list_artifacts``,
   ``forge_clear_artifacts``).

Usage::

    from forge_ide.registry import Registry
    from forge_ide.adapters import register_builtin_tools
    from forge_ide.mcp.registry_bridge import register_forge_tools

    registry = Registry()
    register_builtin_tools(registry)   # 7 core IDE tools
    register_forge_tools(registry)     # 15+ Forge/MCP tools

All registered handlers conform to the ``Registry.dispatch`` protocol:
``(request_model, working_dir) → ToolResponse``.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Optional

from pydantic import BaseModel, Field

from forge_ide.contracts import ToolResponse

if TYPE_CHECKING:
    from forge_ide.registry import Registry

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════
# Pydantic request models — Forge workspace tools
# ═══════════════════════════════════════════════════════════════════════════


class ForgeGetContractRequest(BaseModel):
    """Request to read a specific governance contract."""

    name: str = Field(
        ...,
        description=(
            "Contract name (e.g. 'blueprint', 'stack', 'schema', 'physics', "
            "'boundaries', 'manifesto', 'ui', 'builder_directive')."
        ),
    )


class ForgeGetPhaseWindowRequest(BaseModel):
    """Request to read a phase's deliverables."""

    phase_number: int = Field(
        ...,
        description="The phase number to retrieve (0-based).",
    )


class ForgeListContractsRequest(BaseModel):
    """Request to list all contracts (no params)."""


class ForgeGetSummaryRequest(BaseModel):
    """Request for governance framework overview (no params)."""


class ForgeScratchpadRequest(BaseModel):
    """Request to operate on the persistent scratchpad."""

    operation: str = Field(
        ...,
        description="Operation to perform: read | write | append | list",
    )
    key: Optional[str] = Field(
        None,
        description="Key name. Required for read/write/append.",
    )
    value: Optional[str] = Field(
        None,
        description="Value to write or append. Required for write/append.",
    )


# ═══════════════════════════════════════════════════════════════════════════
# Pydantic request models — MCP project tools
# ═══════════════════════════════════════════════════════════════════════════


class ForgeSetSessionRequest(BaseModel):
    """Set MCP session defaults."""

    project_id: str = Field(..., description="Project identifier (UUID)")
    build_id: Optional[str] = Field(None, description="Build identifier (UUID)")
    user_id: Optional[str] = Field(None, description="User identifier (UUID)")


class ForgeClearSessionRequest(BaseModel):
    """Clear MCP session (no params)."""


class ForgeGetProjectContextRequest(BaseModel):
    """Get project metadata manifest."""

    project_id: Optional[str] = Field(
        None,
        description="Project identifier. Optional if session is set.",
    )


class ForgeListProjectContractsRequest(BaseModel):
    """List project's generated contracts."""

    project_id: Optional[str] = Field(
        None,
        description="Project identifier. Optional if session is set.",
    )


class ForgeGetProjectContractRequest(BaseModel):
    """Fetch a single generated contract from DB."""

    contract_type: str = Field(..., description="Contract type (e.g. manifesto, stack, physics)")
    project_id: Optional[str] = Field(
        None,
        description="Project identifier. Optional if session is set.",
    )


class ForgeGetBuildContractsRequest(BaseModel):
    """Fetch a build's pinned contract snapshot."""

    build_id: Optional[str] = Field(
        None,
        description="Build identifier. Optional if session is set.",
    )


# ═══════════════════════════════════════════════════════════════════════════
# Pydantic request models — MCP artifact tools
# ═══════════════════════════════════════════════════════════════════════════


class ForgeStoreArtifactRequest(BaseModel):
    """Store an artifact in the MCP artifact store."""

    project_id: str = Field(..., description="Project identifier")
    artifact_type: str = Field(
        ...,
        description="Category: contract | scout | renovation | directive | phase | seal | diff",
    )
    key: str = Field(..., description="Artifact key within its type")
    content: Any = Field(..., description="Artifact content (string or JSON)")
    ttl_hours: float = Field(24, description="Memory TTL in hours (default 24)")
    persist: bool = Field(True, description="Write to disk for durability")


class ForgeGetArtifactRequest(BaseModel):
    """Retrieve a stored artifact."""

    project_id: str = Field(..., description="Project identifier")
    artifact_type: str = Field(..., description="Artifact category")
    key: str = Field(..., description="Artifact key")


class ForgeListArtifactsRequest(BaseModel):
    """List stored artifacts for a project."""

    project_id: str = Field(..., description="Project identifier")
    artifact_type: Optional[str] = Field(
        None,
        description="Optional filter: contract | scout | renovation | directive | phase | seal | diff",
    )


class ForgeClearArtifactsRequest(BaseModel):
    """Clear stored artifacts for a project."""

    project_id: str = Field(..., description="Project identifier")
    artifact_type: Optional[str] = Field(
        None,
        description="Optional — clear only this type",
    )


# ═══════════════════════════════════════════════════════════════════════════
# Adapter functions — Forge workspace tools
# ═══════════════════════════════════════════════════════════════════════════


def _adapt_forge_get_contract(
    req: ForgeGetContractRequest, working_dir: str
) -> ToolResponse:
    from app.services.tool_executor import _exec_forge_get_contract

    raw = _exec_forge_get_contract({"name": req.name}, working_dir)
    if raw.startswith("Error:"):
        return ToolResponse.fail(raw)
    return ToolResponse.ok({"name": req.name, "content": raw})


def _adapt_forge_get_phase_window(
    req: ForgeGetPhaseWindowRequest, working_dir: str
) -> ToolResponse:
    from app.services.tool_executor import _exec_forge_get_phase_window

    raw = _exec_forge_get_phase_window(
        {"phase_number": req.phase_number}, working_dir
    )
    if raw.startswith("Error:"):
        return ToolResponse.fail(raw)
    return ToolResponse.ok({"phase_number": req.phase_number, "content": raw})


def _adapt_forge_list_contracts(
    req: ForgeListContractsRequest, working_dir: str
) -> ToolResponse:
    from app.services.tool_executor import _exec_forge_list_contracts

    raw = _exec_forge_list_contracts({}, working_dir)
    if raw.startswith("Error:"):
        return ToolResponse.fail(raw)
    return ToolResponse.ok({"contracts": raw})


def _adapt_forge_get_summary(
    req: ForgeGetSummaryRequest, working_dir: str
) -> ToolResponse:
    from app.services.tool_executor import _exec_forge_get_summary

    raw = _exec_forge_get_summary({}, working_dir)
    if raw.startswith("Error:"):
        return ToolResponse.fail(raw)
    return ToolResponse.ok({"summary": raw})


def _adapt_forge_scratchpad(
    req: ForgeScratchpadRequest, working_dir: str
) -> ToolResponse:
    from app.services.tool_executor import _exec_forge_scratchpad

    inp: dict[str, Any] = {"operation": req.operation}
    if req.key is not None:
        inp["key"] = req.key
    if req.value is not None:
        inp["value"] = req.value

    raw = _exec_forge_scratchpad(inp, working_dir)
    if raw.startswith("Error:"):
        return ToolResponse.fail(raw)
    return ToolResponse.ok({"result": raw})


# ═══════════════════════════════════════════════════════════════════════════
# Adapter functions — MCP project tools (async, proxied via ForgeGuard API)
# ═══════════════════════════════════════════════════════════════════════════


async def _adapt_forge_set_session(
    req: ForgeSetSessionRequest, working_dir: str
) -> ToolResponse:
    from forge_ide.mcp.tools import dispatch as mcp_dispatch

    args: dict[str, Any] = {"project_id": req.project_id}
    if req.build_id is not None:
        args["build_id"] = req.build_id
    if req.user_id is not None:
        args["user_id"] = req.user_id

    try:
        result = await mcp_dispatch("forge_set_session", args)
    except Exception as exc:
        return ToolResponse.fail(f"MCP error: {exc}")
    if "error" in result:
        return ToolResponse.fail(result["error"])
    return ToolResponse.ok(result)


async def _adapt_forge_clear_session(
    req: ForgeClearSessionRequest, working_dir: str
) -> ToolResponse:
    from forge_ide.mcp.tools import dispatch as mcp_dispatch

    try:
        result = await mcp_dispatch("forge_clear_session", {})
    except Exception as exc:
        return ToolResponse.fail(f"MCP error: {exc}")
    if "error" in result:
        return ToolResponse.fail(result["error"])
    return ToolResponse.ok(result)


async def _adapt_forge_get_project_context(
    req: ForgeGetProjectContextRequest, working_dir: str
) -> ToolResponse:
    from forge_ide.mcp.tools import dispatch as mcp_dispatch

    args: dict[str, Any] = {}
    if req.project_id is not None:
        args["project_id"] = req.project_id

    try:
        result = await mcp_dispatch("forge_get_project_context", args)
    except Exception as exc:
        return ToolResponse.fail(f"MCP error: {exc}")
    if "error" in result:
        return ToolResponse.fail(result["error"])
    return ToolResponse.ok(result)


async def _adapt_forge_list_project_contracts(
    req: ForgeListProjectContractsRequest, working_dir: str
) -> ToolResponse:
    from forge_ide.mcp.tools import dispatch as mcp_dispatch

    args: dict[str, Any] = {}
    if req.project_id is not None:
        args["project_id"] = req.project_id

    try:
        result = await mcp_dispatch("forge_list_project_contracts", args)
    except Exception as exc:
        return ToolResponse.fail(f"MCP error: {exc}")
    if "error" in result:
        return ToolResponse.fail(result["error"])
    return ToolResponse.ok(result)


async def _adapt_forge_get_project_contract(
    req: ForgeGetProjectContractRequest, working_dir: str
) -> ToolResponse:
    from forge_ide.mcp.tools import dispatch as mcp_dispatch

    args: dict[str, Any] = {"contract_type": req.contract_type}
    if req.project_id is not None:
        args["project_id"] = req.project_id

    try:
        result = await mcp_dispatch("forge_get_project_contract", args)
    except Exception as exc:
        return ToolResponse.fail(f"MCP error: {exc}")
    if "error" in result:
        return ToolResponse.fail(result["error"])
    return ToolResponse.ok(result)


async def _adapt_forge_get_build_contracts(
    req: ForgeGetBuildContractsRequest, working_dir: str
) -> ToolResponse:
    from forge_ide.mcp.tools import dispatch as mcp_dispatch

    args: dict[str, Any] = {}
    if req.build_id is not None:
        args["build_id"] = req.build_id

    try:
        result = await mcp_dispatch("forge_get_build_contracts", args)
    except Exception as exc:
        return ToolResponse.fail(f"MCP error: {exc}")
    if "error" in result:
        return ToolResponse.fail(result["error"])
    return ToolResponse.ok(result)


# ═══════════════════════════════════════════════════════════════════════════
# Adapter functions — MCP artifact tools
# ═══════════════════════════════════════════════════════════════════════════


async def _adapt_forge_store_artifact(
    req: ForgeStoreArtifactRequest, working_dir: str
) -> ToolResponse:
    from forge_ide.mcp.tools import dispatch as mcp_dispatch

    try:
        result = await mcp_dispatch("forge_store_artifact", {
            "project_id": req.project_id,
            "artifact_type": req.artifact_type,
            "key": req.key,
            "content": req.content,
            "ttl_hours": req.ttl_hours,
            "persist": req.persist,
        })
    except Exception as exc:
        return ToolResponse.fail(f"MCP error: {exc}")
    if "error" in result:
        return ToolResponse.fail(result["error"])
    return ToolResponse.ok(result)


async def _adapt_forge_get_artifact(
    req: ForgeGetArtifactRequest, working_dir: str
) -> ToolResponse:
    from forge_ide.mcp.tools import dispatch as mcp_dispatch

    try:
        result = await mcp_dispatch("forge_get_artifact", {
            "project_id": req.project_id,
            "artifact_type": req.artifact_type,
            "key": req.key,
        })
    except Exception as exc:
        return ToolResponse.fail(f"MCP error: {exc}")
    if "error" in result:
        return ToolResponse.fail(result["error"])
    return ToolResponse.ok(result)


async def _adapt_forge_list_artifacts(
    req: ForgeListArtifactsRequest, working_dir: str
) -> ToolResponse:
    from forge_ide.mcp.tools import dispatch as mcp_dispatch

    args: dict[str, Any] = {"project_id": req.project_id}
    if req.artifact_type is not None:
        args["artifact_type"] = req.artifact_type

    try:
        result = await mcp_dispatch("forge_list_artifacts", args)
    except Exception as exc:
        return ToolResponse.fail(f"MCP error: {exc}")
    if "error" in result:
        return ToolResponse.fail(result["error"])
    return ToolResponse.ok(result)


async def _adapt_forge_clear_artifacts(
    req: ForgeClearArtifactsRequest, working_dir: str
) -> ToolResponse:
    from forge_ide.mcp.tools import dispatch as mcp_dispatch

    args: dict[str, Any] = {"project_id": req.project_id}
    if req.artifact_type is not None:
        args["artifact_type"] = req.artifact_type

    try:
        result = await mcp_dispatch("forge_clear_artifacts", args)
    except Exception as exc:
        return ToolResponse.fail(f"MCP error: {exc}")
    if "error" in result:
        return ToolResponse.fail(result["error"])
    return ToolResponse.ok(result)


# ═══════════════════════════════════════════════════════════════════════════
# Tool descriptions
# ═══════════════════════════════════════════════════════════════════════════


_FORGE_WS_DESCRIPTIONS: dict[str, str] = {
    "forge_get_contract": (
        "Read a specific governance contract from the Forge/Contracts/ directory. "
        "Use this to fetch project specifications (blueprint, stack, schema, physics, "
        "boundaries, manifesto, ui, builder_directive, builder_contract). "
        "Note: 'phases' is blocked — use forge_get_phase_window instead."
    ),
    "forge_get_phase_window": (
        "Get the deliverables for a specific build phase (current + next phase). "
        "Call this at the START of each phase to understand what to build. "
        "Returns ~1-3K tokens instead of the full phases contract."
    ),
    "forge_list_contracts": (
        "List all available governance contracts in the Forge/Contracts/ directory. "
        "Returns names, filenames, and sizes."
    ),
    "forge_get_summary": (
        "Get a compact overview of the Forge governance framework. "
        "Returns available contracts, architecture layers, key rules, and tool descriptions. "
        "Call this FIRST to orient yourself before fetching specific contracts."
    ),
    "forge_scratchpad": (
        "Persistent scratchpad for storing reasoning, decisions, and notes that "
        "survive context compaction. Operations: read, write, append, list."
    ),
}

_MCP_PROJECT_DESCRIPTIONS: dict[str, str] = {
    "forge_set_session": (
        "Set session-level defaults for the MCP server instance. "
        "Called once by the build orchestrator before spawning sub-agents."
    ),
    "forge_clear_session": (
        "Reset the MCP session to blank."
    ),
    "forge_get_project_context": (
        "Get a combined manifest for a project — project info, available contracts, "
        "build count. Returns METADATA only, not full contract content."
    ),
    "forge_list_project_contracts": (
        "List all generated contracts for the current project — types, versions, timestamps."
    ),
    "forge_get_project_contract": (
        "Fetch a single generated contract from the database. Returns full content, "
        "version, and source. Common types: manifesto, stack, physics, boundaries, "
        "blueprint, builder_directive, schema, ui."
    ),
    "forge_get_build_contracts": (
        "Fetch the pinned contract snapshot for a specific build. "
        "These are immutable — mid-build edits don't affect them."
    ),
}

_MCP_ARTIFACT_DESCRIPTIONS: dict[str, str] = {
    "forge_store_artifact": (
        "Store a generated artifact (contract, scout dossier, renovation plan, "
        "builder directive, phase output, etc.) in the MCP artifact store for "
        "on-demand retrieval. Use instead of embedding large content in prompts."
    ),
    "forge_get_artifact": (
        "Retrieve a previously stored artifact by project ID, type, and key. "
        "Checks in-memory store first, then disk."
    ),
    "forge_list_artifacts": (
        "List all stored artifacts for a project, optionally filtered by type."
    ),
    "forge_clear_artifacts": (
        "Remove all stored artifacts for a project (memory + disk)."
    ),
}


# ═══════════════════════════════════════════════════════════════════════════
# Registration functions
# ═══════════════════════════════════════════════════════════════════════════


def register_forge_workspace_tools(registry: Registry) -> None:
    """Register 5 Forge workspace tools for reading contracts and phase info."""
    registry.register(
        "forge_get_contract",
        _adapt_forge_get_contract,
        ForgeGetContractRequest,
        _FORGE_WS_DESCRIPTIONS["forge_get_contract"],
    )
    registry.register(
        "forge_get_phase_window",
        _adapt_forge_get_phase_window,
        ForgeGetPhaseWindowRequest,
        _FORGE_WS_DESCRIPTIONS["forge_get_phase_window"],
    )
    registry.register(
        "forge_list_contracts",
        _adapt_forge_list_contracts,
        ForgeListContractsRequest,
        _FORGE_WS_DESCRIPTIONS["forge_list_contracts"],
    )
    registry.register(
        "forge_get_summary",
        _adapt_forge_get_summary,
        ForgeGetSummaryRequest,
        _FORGE_WS_DESCRIPTIONS["forge_get_summary"],
    )
    registry.register(
        "forge_scratchpad",
        _adapt_forge_scratchpad,
        ForgeScratchpadRequest,
        _FORGE_WS_DESCRIPTIONS["forge_scratchpad"],
    )


def register_mcp_project_tools(registry: Registry) -> None:
    """Register 6 MCP project tools for DB-stored contract access."""
    registry.register(
        "forge_set_session",
        _adapt_forge_set_session,
        ForgeSetSessionRequest,
        _MCP_PROJECT_DESCRIPTIONS["forge_set_session"],
    )
    registry.register(
        "forge_clear_session",
        _adapt_forge_clear_session,
        ForgeClearSessionRequest,
        _MCP_PROJECT_DESCRIPTIONS["forge_clear_session"],
    )
    registry.register(
        "forge_get_project_context",
        _adapt_forge_get_project_context,
        ForgeGetProjectContextRequest,
        _MCP_PROJECT_DESCRIPTIONS["forge_get_project_context"],
    )
    registry.register(
        "forge_list_project_contracts",
        _adapt_forge_list_project_contracts,
        ForgeListProjectContractsRequest,
        _MCP_PROJECT_DESCRIPTIONS["forge_list_project_contracts"],
    )
    registry.register(
        "forge_get_project_contract",
        _adapt_forge_get_project_contract,
        ForgeGetProjectContractRequest,
        _MCP_PROJECT_DESCRIPTIONS["forge_get_project_contract"],
    )
    registry.register(
        "forge_get_build_contracts",
        _adapt_forge_get_build_contracts,
        ForgeGetBuildContractsRequest,
        _MCP_PROJECT_DESCRIPTIONS["forge_get_build_contracts"],
    )


def register_mcp_artifact_tools(registry: Registry) -> None:
    """Register 4 MCP artifact tools for cross-agent data storage."""
    registry.register(
        "forge_store_artifact",
        _adapt_forge_store_artifact,
        ForgeStoreArtifactRequest,
        _MCP_ARTIFACT_DESCRIPTIONS["forge_store_artifact"],
    )
    registry.register(
        "forge_get_artifact",
        _adapt_forge_get_artifact,
        ForgeGetArtifactRequest,
        _MCP_ARTIFACT_DESCRIPTIONS["forge_get_artifact"],
    )
    registry.register(
        "forge_list_artifacts",
        _adapt_forge_list_artifacts,
        ForgeListArtifactsRequest,
        _MCP_ARTIFACT_DESCRIPTIONS["forge_list_artifacts"],
    )
    registry.register(
        "forge_clear_artifacts",
        _adapt_forge_clear_artifacts,
        ForgeClearArtifactsRequest,
        _MCP_ARTIFACT_DESCRIPTIONS["forge_clear_artifacts"],
    )


def register_forge_tools(registry: Registry) -> None:
    """Register ALL Forge/MCP tools (workspace + project + artifact).

    This is the convenience function for build flows that need the full
    tool surface.  After calling this, the registry will have 15 Forge
    tools in addition to whatever core IDE tools were already registered.
    """
    register_forge_workspace_tools(registry)
    register_mcp_project_tools(registry)
    register_mcp_artifact_tools(registry)
