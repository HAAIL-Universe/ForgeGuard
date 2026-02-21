"""Unified IDE+MCP build agent launcher.

Every build flow — Mini Build, Full Build, Renovation — uses the same
IDE+MCP agent under the hood.  The difference between flows is only in
the *preparation* that precedes the launch:

- **System prompt** — full vs mini vs custom
- **Task/directive** — phase deliverables, contract references, workspace info
- **Model** — Opus for code generation, Sonnet for scouting/auditing
- **Turn limit** — mini gets fewer turns
- **Tool surface** — read-only for scouts, full for builders

The launch itself is always the same:  ``launch_build_agent(task, ...)``
which creates a Registry, sets the MCP session, wires the WS event bridge,
and runs ``forge_ide.agent.run_agent()``.

Usage::

    from app.services.build.ide_launcher import (
        launch_build_agent,
        create_build_registry,
        BuildAgentResult,
    )

    # Mini build — Phase 0
    result = await launch_build_agent(
        task="Build Phase 0 (Backend Scaffold). Call forge_get_phase_window(0)...",
        api_key=api_key,
        working_dir="/tmp/build-abc/workspace",
        model="claude-opus-4-20250514",
        system_prompt=MCP_MINI_SYSTEM_PROMPT,
        max_turns=50,
        project_id=str(project_id),
        build_id=str(build_id),
        user_id=str(user_id),
    )

    # Plan-execute — Phase 2
    result = await launch_build_agent(
        task="Build Phase 2: API Endpoints. ...",
        api_key=api_key,
        working_dir="/tmp/build-abc/workspace",
        model="claude-opus-4-20250514",
        system_prompt=PLAN_EXECUTE_SYSTEM_PROMPT,
        max_turns=40,
        project_id=str(project_id),
        build_id=str(build_id),
        user_id=str(user_id),
    )
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from forge_ide.agent import (
    AgentConfig,
    AgentEvent,
    DoneEvent,
    ErrorEvent,
    TextEvent,
    ToolCallEvent,
    ToolResultEvent,
    ContextCompactionEvent,
    run_agent,
    make_ws_event_bridge,
)
from forge_ide.journal import SessionJournal
from forge_ide.registry import Registry

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════
# Result type
# ═══════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class BuildAgentResult:
    """Structured result from a build agent invocation."""

    final_text: str
    total_input_tokens: int
    total_output_tokens: int
    tool_calls_made: int
    turns: int
    elapsed_ms: int
    files_written: tuple[str, ...] = ()
    error: str | None = None


# ═══════════════════════════════════════════════════════════════════════════
# Registry factory
# ═══════════════════════════════════════════════════════════════════════════


def create_build_registry(
    *,
    include_write_tools: bool = True,
    include_forge_workspace: bool = True,
    include_mcp_project: bool = True,
    include_mcp_artifacts: bool = True,
) -> Registry:
    """Create a Registry with the IDE + Forge/MCP tool surface.

    Parameters
    ----------
    include_write_tools : bool
        Include file-writing tools (write_file, apply_patch). Set False
        for read-only scouts/auditors.
    include_forge_workspace : bool
        Include Forge workspace tools (contract reading, phase window,
        scratchpad) — reads from Forge/Contracts/ in working directory.
    include_mcp_project : bool
        Include MCP project tools (DB-stored contracts via ForgeGuard API).
    include_mcp_artifacts : bool
        Include MCP artifact tools (cross-agent data storage).

    Returns
    -------
    Registry
        Fully-loaded registry ready for ``run_agent()``.
    """
    from forge_ide.adapters import register_builtin_tools

    registry = Registry()

    if include_write_tools:
        # Register all 7 core tools + apply_patch
        register_builtin_tools(registry)
        _register_apply_patch(registry)
    else:
        # Read-only subset
        _register_readonly_tools(registry)

    if include_forge_workspace:
        from forge_ide.mcp.registry_bridge import register_forge_workspace_tools
        register_forge_workspace_tools(registry)

    if include_mcp_project:
        from forge_ide.mcp.registry_bridge import register_mcp_project_tools
        register_mcp_project_tools(registry)

    if include_mcp_artifacts:
        from forge_ide.mcp.registry_bridge import register_mcp_artifact_tools
        register_mcp_artifact_tools(registry)

    logger.info(
        "[ide_launcher] Registry created with %d tools: %s",
        len(registry.tool_names()),
        ", ".join(registry.tool_names()),
    )
    return registry


def _register_apply_patch(registry: Registry) -> None:
    """Register the apply_patch tool (delegate to agent.py's implementation)."""
    from forge_ide.agent import _register_apply_patch_tool
    _register_apply_patch_tool(registry)


def _register_readonly_tools(registry: Registry) -> None:
    """Register only the read-only IDE tools (no write_file, apply_patch)."""
    from forge_ide.adapters import (
        _adapt_read_file,
        _adapt_list_directory,
        _adapt_search_code,
        _adapt_check_syntax,
        _adapt_run_command,
        _adapt_run_tests,
        _TOOL_DESCRIPTIONS,
    )
    from forge_ide.contracts import (
        ReadFileRequest,
        ListDirectoryRequest,
        SearchCodeRequest,
        CheckSyntaxRequest,
        RunCommandRequest,
        RunTestsRequest,
    )

    registry.register(
        "read_file", _adapt_read_file, ReadFileRequest,
        _TOOL_DESCRIPTIONS["read_file"],
    )
    registry.register(
        "list_directory", _adapt_list_directory, ListDirectoryRequest,
        _TOOL_DESCRIPTIONS["list_directory"],
    )
    registry.register(
        "search_code", _adapt_search_code, SearchCodeRequest,
        _TOOL_DESCRIPTIONS["search_code"],
    )
    registry.register(
        "check_syntax", _adapt_check_syntax, CheckSyntaxRequest,
        _TOOL_DESCRIPTIONS["check_syntax"],
    )
    registry.register(
        "run_tests", _adapt_run_tests, RunTestsRequest,
        _TOOL_DESCRIPTIONS["run_tests"],
    )
    registry.register(
        "run_command", _adapt_run_command, RunCommandRequest,
        _TOOL_DESCRIPTIONS["run_command"],
    )


# ═══════════════════════════════════════════════════════════════════════════
# MCP session setup
# ═══════════════════════════════════════════════════════════════════════════


def _setup_mcp_session(
    project_id: str | None,
    build_id: str | None,
    user_id: str | None,
) -> None:
    """Set the MCP session so project tools auto-resolve IDs."""
    if project_id is None:
        return
    from forge_ide.mcp.session import set_session
    set_session(
        project_id=project_id,
        build_id=build_id,
        user_id=user_id,
    )
    logger.info(
        "[ide_launcher] MCP session set: project=%s build=%s user=%s",
        project_id, build_id, user_id,
    )


# ═══════════════════════════════════════════════════════════════════════════
# Event handler composition
# ═══════════════════════════════════════════════════════════════════════════


@dataclass
class _BuildEventTracker:
    """Tracks files written and other build side-effects during the agent loop."""

    files_written: list[str] = field(default_factory=list)
    phase_signoffs: list[str] = field(default_factory=list)
    last_error: str | None = None


def _make_composite_handler(
    *,
    ws_handler: Callable[[AgentEvent], Any] | None = None,
    tracker: _BuildEventTracker,
    extra_handler: Callable[[AgentEvent], Any] | None = None,
) -> Callable[[AgentEvent], Any]:
    """Create a composite event handler that:

    1. Broadcasts to WS (for frontend)
    2. Tracks files written (for git operations)
    3. Detects phase sign-offs
    4. Delegates to any extra callback
    """

    async def _handler(event: AgentEvent) -> None:
        import asyncio

        # ── WS broadcast ──
        if ws_handler is not None:
            result = ws_handler(event)
            if asyncio.iscoroutine(result):
                await result

        # ── File tracking ──
        if isinstance(event, ToolResultEvent):
            if event.tool_name in ("write_file", "apply_patch") and event.response.success:
                path = event.response.data.get("path", "")
                if path and path not in tracker.files_written:
                    tracker.files_written.append(path)

        # ── Phase sign-off detection ──
        if isinstance(event, TextEvent):
            if "=== PHASE SIGN-OFF:" in event.text:
                tracker.phase_signoffs.append(event.text)

        # ── Error tracking ──
        if isinstance(event, ErrorEvent):
            tracker.last_error = event.error

        # ── Extra callback ──
        if extra_handler is not None:
            result = extra_handler(event)
            if asyncio.iscoroutine(result):
                await result

    return _handler


# ═══════════════════════════════════════════════════════════════════════════
# Unified launcher
# ═══════════════════════════════════════════════════════════════════════════


async def launch_build_agent(
    task: str,
    *,
    api_key: str,
    working_dir: str,
    model: str = "claude-sonnet-4-6",
    system_prompt: str = "",
    max_turns: int = 50,
    max_tokens: int = 16384,
    context_window_limit: int = 180_000,
    compaction_target: int = 120_000,
    # Registry — pass a pre-built one, or let the launcher create it
    registry: Registry | None = None,
    # MCP session — auto-configures project tool routing
    project_id: str | None = None,
    build_id: str | None = None,
    user_id: str | None = None,
    # Event handling
    on_event: Callable[[AgentEvent], Any] | None = None,
    broadcast_ws: bool = True,
    # Observability — journal survives compaction; trace writes a JSONL debug log
    journal: SessionJournal | None = None,
    trace_log_path: str | None = None,
) -> BuildAgentResult:
    """Launch the IDE+MCP agent for a build phase.

    This is the **single entry point** that all build flows use.  The
    caller is responsible for preparing the ``task`` (phase directive),
    ``system_prompt``, ``model``, and ``max_turns`` — that's the
    "flow-specific preparation".  Everything after that is identical:
    same Registry, same agent loop, same tool surface, same event
    handling.

    Parameters
    ----------
    task : str
        The user instruction / phase directive.  This is the first
        message the agent sees.  It should tell the agent what to build,
        what phase it's in, and how to find the contracts (via tools).
    api_key : str
        Anthropic API key.
    working_dir : str
        Absolute path to the build workspace.
    model : str
        Model identifier (e.g. ``claude-opus-4-20250514``).
    system_prompt : str
        System prompt — defines the agent's behavior and tool surface docs.
    max_turns : int
        Maximum agent turns before forced stop.
    max_tokens : int
        Per-turn output token budget.
    context_window_limit : int
        Token threshold for automatic context compaction.
    compaction_target : int
        Target token count after compaction.
    registry : Registry | None
        Pre-built Registry.  If None, a full-surface Registry is created
        (8 IDE tools + 15 Forge/MCP tools = 23 tools).
    project_id, build_id, user_id : str | None
        MCP session identifiers — set before tools are used so that
        project-scoped tools auto-resolve IDs.
    on_event : callable | None
        Extra event callback invoked for every ``AgentEvent``.
    broadcast_ws : bool
        If True (and user_id + build_id are set), broadcasts agent events
        to the build progress WebSocket for the frontend.
    journal : SessionJournal | None
        Pre-created journal to track build events across context compaction.
        If None and ``build_id`` is set, a new journal is created automatically.
        The journal summary replaces the generic ``[CONTEXT COMPACTION]``
        header so the agent retains Forge framework state after compaction.
    trace_log_path : str | None
        Path for the JSONL per-turn trace log (one line per llm_call /
        tool_call / tool_result / compaction / done event).  If None and
        ``build_id`` is set, defaults to
        ``{working_dir}/../logs/build_{build_id}_trace.jsonl``.
        Pass ``""`` to disable tracing explicitly.

    Returns
    -------
    BuildAgentResult
        Final result with text, usage stats, and tracked side-effects.
    """
    from pathlib import Path as _Path

    # ── Setup MCP session ──
    _setup_mcp_session(project_id, build_id, user_id)

    # ── Create registry if not provided ──
    if registry is None:
        registry = create_build_registry()

    # ── Journal — create if not supplied and we have a build_id ──
    effective_journal: SessionJournal | None = journal
    if effective_journal is None and build_id:
        effective_journal = SessionJournal(build_id, phase="Build Start")

    # ── Trace log path — derive from working_dir if not supplied ──
    effective_trace: str
    if trace_log_path is not None:
        effective_trace = trace_log_path
    elif build_id:
        effective_trace = str(
            _Path(working_dir).parent / "logs" / f"build_{build_id}_trace.jsonl"
        )
    else:
        effective_trace = ""

    # ── Event handler composition ──
    tracker = _BuildEventTracker()

    ws_handler = None
    if broadcast_ws and user_id and build_id:
        ws_handler = make_ws_event_bridge(str(user_id), str(build_id))

    composite_handler = _make_composite_handler(
        ws_handler=ws_handler,
        tracker=tracker,
        extra_handler=on_event,
    )

    # ── Agent config ──
    config = AgentConfig(
        api_key=api_key,
        model=model,
        system_prompt=system_prompt,
        max_turns=max_turns,
        max_tokens=max_tokens,
        working_dir=working_dir,
        context_window_limit=context_window_limit,
        compaction_target=compaction_target,
        journal=effective_journal,
        trace_log_path=effective_trace,
    )

    logger.info(
        "[ide_launcher] Launching agent: model=%s  turns=%d  tools=%d  "
        "journal=%s  trace=%s  working_dir=%s",
        model, max_turns, len(registry.tool_names()),
        "yes" if effective_journal else "no",
        effective_trace or "disabled",
        working_dir,
    )

    # ── Run the agent ──
    try:
        done: DoneEvent = await run_agent(
            task=task,
            registry=registry,
            config=config,
            on_event=composite_handler,
        )
    except Exception as exc:
        logger.error("[ide_launcher] Agent error: %s", exc)
        return BuildAgentResult(
            final_text="",
            total_input_tokens=0,
            total_output_tokens=0,
            tool_calls_made=0,
            turns=0,
            elapsed_ms=0,
            files_written=tuple(tracker.files_written),
            error=str(exc),
        )

    return BuildAgentResult(
        final_text=done.final_text,
        total_input_tokens=done.total_input_tokens,
        total_output_tokens=done.total_output_tokens,
        tool_calls_made=done.tool_calls_made,
        turns=done.turn,
        elapsed_ms=done.elapsed_ms,
        files_written=tuple(tracker.files_written),
        error=tracker.last_error,
    )


# ═══════════════════════════════════════════════════════════════════════════
# Convenience: role-specific registry presets
# ═══════════════════════════════════════════════════════════════════════════


def create_scout_registry() -> Registry:
    """Create a read-only Registry for reconnaissance agents."""
    return create_build_registry(
        include_write_tools=False,
        include_forge_workspace=True,
        include_mcp_project=True,
        include_mcp_artifacts=True,
    )


def create_coder_registry() -> Registry:
    """Create a full-access Registry for code generation agents."""
    return create_build_registry(
        include_write_tools=True,
        include_forge_workspace=True,
        include_mcp_project=True,
        include_mcp_artifacts=True,
    )


def create_auditor_registry() -> Registry:
    """Create a read-only Registry for audit/review agents."""
    return create_build_registry(
        include_write_tools=False,
        include_forge_workspace=True,
        include_mcp_project=True,
        include_mcp_artifacts=False,
    )
