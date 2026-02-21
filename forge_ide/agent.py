"""Agentic tool-use loop for Forge IDE.

Orchestrates a multi-turn conversation between Claude and local IDE tools.
The LLM decides which tools to call and when to stop.  The loop handles
tool execution, error recovery, context management, and usage tracking.

Architecture
------------
1. User sends a task (e.g. "add pagination to the /repos endpoint").
2. Loop sends the task + tool definitions to Claude.
3. Claude responds with text and/or tool_use blocks.
4. Loop executes requested tools via the Registry, returns results.
5. Claude sees tool results, decides next action.  → repeat from 3.
6. When Claude responds with stop_reason="end_turn", the loop yields
   the final text response.

The loop is a pure async function — no global state, no singletons.
Everything is injected: API key, model, registry, working directory.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import AsyncIterator, Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from app.clients.llm_client import chat_anthropic
from forge_ide.context_pack import estimate_tokens
from forge_ide.contracts import ToolResponse
from forge_ide.journal import SessionJournal
from forge_ide.redactor import redact
from forge_ide.registry import Registry

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DEFAULT_MAX_TURNS = 50
DEFAULT_MAX_TOKENS = 16384     # per-turn output budget
MAX_TOOL_RESULT_CHARS = 60_000  # truncate large tool outputs
CONTEXT_WINDOW_LIMIT = 180_000  # ~180K tokens — soft limit for compaction
COMPACTION_TARGET = 120_000     # ~120K tokens — target after compaction
TOOL_RETRY_MAX = 2              # max retries for transient tool failures
TOOL_RETRY_DELAY = 1.0          # seconds between tool retries


@dataclass(frozen=True)
class AgentConfig:
    """Configuration for an agent loop invocation."""

    api_key: str
    model: str = "claude-sonnet-4-6"
    system_prompt: str = ""
    max_turns: int = DEFAULT_MAX_TURNS
    max_tokens: int = DEFAULT_MAX_TOKENS
    working_dir: str = "."
    redact_secrets: bool = True
    context_window_limit: int = CONTEXT_WINDOW_LIMIT
    compaction_target: int = COMPACTION_TARGET
    tool_retry_max: int = TOOL_RETRY_MAX
    tool_retry_delay: float = TOOL_RETRY_DELAY
    # Forge-aware state tracking — journal survives context compaction
    journal: SessionJournal | None = field(default=None, hash=False, compare=False)
    # JSONL trace log path — one JSON line per turn/tool/event for debugging
    trace_log_path: str = ""


# ---------------------------------------------------------------------------
# Event types — for streaming progress to the caller
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class AgentEvent:
    """Base class for events emitted by the agent loop."""
    turn: int
    elapsed_ms: int


@dataclass(frozen=True)
class ThinkingEvent(AgentEvent):
    """The LLM produced reasoning text."""
    text: str


@dataclass(frozen=True)
class ToolCallEvent(AgentEvent):
    """The LLM requested a tool call."""
    tool_name: str
    tool_input: dict[str, Any]
    tool_use_id: str


@dataclass(frozen=True)
class ToolResultEvent(AgentEvent):
    """A tool call completed."""
    tool_name: str
    tool_use_id: str
    response: ToolResponse


@dataclass(frozen=True)
class TextEvent(AgentEvent):
    """The LLM emitted text output (may be intermediate or final)."""
    text: str


@dataclass(frozen=True)
class DoneEvent(AgentEvent):
    """The loop completed."""
    final_text: str
    total_input_tokens: int
    total_output_tokens: int
    tool_calls_made: int


@dataclass(frozen=True)
class ErrorEvent(AgentEvent):
    """The loop encountered an unrecoverable error."""
    error: str


@dataclass(frozen=True)
class ContextCompactionEvent(AgentEvent):
    """The conversation history was compacted to fit the context window."""
    messages_before: int
    messages_after: int
    tokens_before: int
    tokens_after: int


# ---------------------------------------------------------------------------
# Structured turn trace — JSONL log for debugging
# ---------------------------------------------------------------------------

@dataclass
class TurnTrace:
    """One event in the per-build JSONL trace log.

    Written to ``{trace_log_path}`` (one JSON line per event).
    Open the JSONL file after a failed build to replay exactly:
    turn N → model said X → called tool Y → got result Z → did what?
    """
    turn: int
    timestamp: str
    event_type: str   # "llm_call" | "tool_call" | "tool_result" | "compaction" | "done" | "error"
    model: str
    data: dict        # event-specific payload
    elapsed_ms: int
    tokens_in: int = 0
    tokens_out: int = 0


def _write_trace(trace_log_path: str, trace: TurnTrace) -> None:
    """Append one TurnTrace JSON line to the trace log file (best-effort)."""
    if not trace_log_path:
        return
    try:
        path = Path(trace_log_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(asdict(trace), default=str) + "\n")
    except Exception:
        pass  # Trace failures must never crash the agent loop


# ---------------------------------------------------------------------------
# Usage tracking
# ---------------------------------------------------------------------------

@dataclass
class _UsageAccumulator:
    input_tokens: int = 0
    output_tokens: int = 0
    tool_calls: int = 0

    def add(self, usage: dict) -> None:
        self.input_tokens += usage.get("input_tokens", 0)
        self.output_tokens += usage.get("output_tokens", 0)


# ---------------------------------------------------------------------------
# Core agent loop
# ---------------------------------------------------------------------------

async def run_agent(
    task: str,
    registry: Registry,
    config: AgentConfig,
    *,
    on_event: Callable[[AgentEvent], Any] | None = None,
) -> DoneEvent:
    """Execute the agentic tool-use loop.

    Parameters
    ----------
    task : str
        The user's request / instruction.
    registry : Registry
        Pre-configured tool registry with handlers registered.
    config : AgentConfig
        API key, model, system prompt, limits, etc.
    on_event : callable, optional
        Callback invoked with each ``AgentEvent`` for streaming progress.
        Can be sync or async.

    Returns
    -------
    DoneEvent
        Final result with the LLM's text response and usage stats.
    """
    loop_start = time.perf_counter()
    usage = _UsageAccumulator()
    tools = registry.list_tools()

    # Build initial messages
    messages: list[dict[str, Any]] = [
        {"role": "user", "content": task},
    ]

    final_text = ""

    for turn in range(1, config.max_turns + 1):
        # ── Context window management ─────────────────────────────
        # Check if conversation is approaching the context limit and
        # compact if needed — preserves the first message (task) and
        # a summary of older turns, keeping recent tool results intact.
        messages = await _maybe_compact(
            messages, config, turn, loop_start, on_event,
        )

        # ── Call Claude ───────────────────────────────────────────
        llm_start = time.perf_counter()
        try:
            response = await chat_anthropic(
                api_key=config.api_key,
                model=config.model,
                system_prompt=config.system_prompt,
                messages=messages,
                max_tokens=config.max_tokens,
                tools=tools,
            )
        except Exception as exc:
            logger.error("[agent:llm] turn=%d model=%s  LLM API error: %s", turn, config.model, exc)
            event = ErrorEvent(
                turn=turn,
                elapsed_ms=_elapsed_ms(loop_start),
                error=f"LLM API error: {exc}",
            )
            await _emit(on_event, event)
            raise AgentError(str(exc)) from exc

        # Track usage
        usage.add(response.get("usage", {}))
        stop_reason = response.get("stop_reason", "end_turn")
        content_blocks = response.get("content", [])
        llm_ms = int((time.perf_counter() - llm_start) * 1000)
        resp_usage = response.get("usage", {})
        tokens_in = resp_usage.get("input_tokens", 0)
        tokens_out = resp_usage.get("output_tokens", 0)
        logger.info(
            "[agent:llm] turn=%d model=%s  stop=%s  in=%d out=%d (%dms)",
            turn, config.model, stop_reason, tokens_in, tokens_out, llm_ms,
        )

        # Trace: LLM call
        _write_trace(config.trace_log_path, TurnTrace(
            turn=turn,
            timestamp=_now_iso(),
            event_type="llm_call",
            model=config.model,
            data={"stop_reason": stop_reason},
            elapsed_ms=llm_ms,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
        ))

        # ── Append assistant message to conversation ──────────────
        messages.append({"role": "assistant", "content": content_blocks})

        # ── Process content blocks ────────────────────────────────
        text_parts: list[str] = []
        tool_uses: list[dict[str, Any]] = []

        for block in content_blocks:
            if block.get("type") == "text":
                text = block["text"]
                text_parts.append(text)
                await _emit(on_event, TextEvent(
                    turn=turn,
                    elapsed_ms=_elapsed_ms(loop_start),
                    text=text,
                ))
            elif block.get("type") == "tool_use":
                tool_uses.append(block)
                logger.info(
                    "[agent:tool_call] turn=%d  %s  input=%s",
                    turn, block["name"],
                    json.dumps(block["input"], default=str)[:200],
                )
                await _emit(on_event, ToolCallEvent(
                    turn=turn,
                    elapsed_ms=_elapsed_ms(loop_start),
                    tool_name=block["name"],
                    tool_input=block["input"],
                    tool_use_id=block["id"],
                ))
                # Trace: tool call
                _write_trace(config.trace_log_path, TurnTrace(
                    turn=turn,
                    timestamp=_now_iso(),
                    event_type="tool_call",
                    model=config.model,
                    data={"tool": block["name"], "input": block["input"]},
                    elapsed_ms=_elapsed_ms(loop_start),
                ))

        # Capture text for potential final response
        if text_parts:
            final_text = "\n".join(text_parts)

        # ── If end_turn → we're done ─────────────────────────────
        if stop_reason == "end_turn":
            done = DoneEvent(
                turn=turn,
                elapsed_ms=_elapsed_ms(loop_start),
                final_text=final_text,
                total_input_tokens=usage.input_tokens,
                total_output_tokens=usage.output_tokens,
                tool_calls_made=usage.tool_calls,
            )
            logger.info(
                "[agent:done] turns=%d  tools=%d  in=%d out=%d (%dms)  model=%s",
                turn, usage.tool_calls, usage.input_tokens,
                usage.output_tokens, done.elapsed_ms, config.model,
            )
            await _emit(on_event, done)
            # Trace: done
            _write_trace(config.trace_log_path, TurnTrace(
                turn=turn,
                timestamp=_now_iso(),
                event_type="done",
                model=config.model,
                data={
                    "turns": turn,
                    "tool_calls": usage.tool_calls,
                    "stop": "end_turn",
                },
                elapsed_ms=done.elapsed_ms,
                tokens_in=usage.input_tokens,
                tokens_out=usage.output_tokens,
            ))
            return done

        # ── Execute tool calls with error recovery ────────────────
        if stop_reason == "tool_use" and tool_uses:
            tool_results: list[dict[str, Any]] = []

            for tool_block in tool_uses:
                tool_name = tool_block["name"]
                tool_input = tool_block["input"]
                tool_id = tool_block["id"]
                usage.tool_calls += 1

                result = await _execute_with_retry(
                    registry, tool_name, tool_input,
                    config.working_dir, config.tool_retry_max,
                    config.tool_retry_delay,
                )

                await _emit(on_event, ToolResultEvent(
                    turn=turn,
                    elapsed_ms=_elapsed_ms(loop_start),
                    tool_name=tool_name,
                    tool_use_id=tool_id,
                    response=result,
                ))
                status = "OK" if result.success else "FAIL"
                logger.info(
                    "[agent:tool_result] turn=%d  %s  %s (%dms)",
                    turn, tool_name, status, result.duration_ms,
                )

                # Journal: record tool completion / errors / file writes
                if config.journal is not None:
                    if result.success:
                        # Record a file_written entry so the journal tracks the
                        # files_written list and the compaction summary is accurate.
                        if tool_name in ("write_file", "apply_patch"):
                            file_path = (result.data or {}).get("path", "")
                            if file_path:
                                config.journal.record(
                                    "file_written",
                                    f"Wrote {file_path}",
                                    metadata={"file_path": file_path, "tool": tool_name, "turn": turn},
                                )
                        else:
                            config.journal.record(
                                "task_completed",
                                f"Tool {tool_name} succeeded",
                                metadata={"tool": tool_name, "turn": turn},
                            )
                    else:
                        config.journal.record(
                            "error",
                            f"Tool {tool_name} failed: {(result.error or '')[:200]}",
                            metadata={"tool": tool_name, "turn": turn, "error": result.error},
                        )

                # Trace: tool result
                _write_trace(config.trace_log_path, TurnTrace(
                    turn=turn,
                    timestamp=_now_iso(),
                    event_type="tool_result",
                    model=config.model,
                    data={
                        "tool": tool_name,
                        "success": result.success,
                        "duration_ms": result.duration_ms,
                        "error": result.error,
                    },
                    elapsed_ms=_elapsed_ms(loop_start),
                ))

                # Format result for the API
                result_text = _format_tool_result(result, config.redact_secrets)
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": tool_id,
                    "content": result_text,
                })

            # Append all tool results as the next user message
            messages.append({"role": "user", "content": tool_results})

    # ── Max turns exhausted ───────────────────────────────────────
    done = DoneEvent(
        turn=config.max_turns,
        elapsed_ms=_elapsed_ms(loop_start),
        final_text=final_text or "(Agent reached max turns without completing)",
        total_input_tokens=usage.input_tokens,
        total_output_tokens=usage.output_tokens,
        tool_calls_made=usage.tool_calls,
    )
    logger.warning(
        "[agent:done] MAX TURNS EXHAUSTED  turns=%d  tools=%d  in=%d out=%d (%dms)  model=%s",
        config.max_turns, usage.tool_calls, usage.input_tokens,
        usage.output_tokens, done.elapsed_ms, config.model,
    )
    await _emit(on_event, done)
    # Trace: max-turns exhausted
    _write_trace(config.trace_log_path, TurnTrace(
        turn=config.max_turns,
        timestamp=_now_iso(),
        event_type="done",
        model=config.model,
        data={
            "turns": config.max_turns,
            "tool_calls": usage.tool_calls,
            "stop": "max_turns_exhausted",
        },
        elapsed_ms=done.elapsed_ms,
        tokens_in=usage.input_tokens,
        tokens_out=usage.output_tokens,
    ))
    return done


# ---------------------------------------------------------------------------
# Streaming variant — yields events instead of using a callback
# ---------------------------------------------------------------------------

async def stream_agent(
    task: str,
    registry: Registry,
    config: AgentConfig,
) -> AsyncIterator[AgentEvent]:
    """Run the agent loop, yielding events as an async iterator.

    This is a convenience wrapper around ``run_agent`` for callers that
    prefer ``async for`` over callbacks.

    Usage::

        async for event in stream_agent(task, registry, config):
            if isinstance(event, ToolCallEvent):
                print(f"Calling {event.tool_name}...")
            elif isinstance(event, DoneEvent):
                print(f"Done: {event.final_text}")
    """
    import asyncio

    queue: asyncio.Queue[AgentEvent | None] = asyncio.Queue()

    async def _collector(event: AgentEvent) -> None:
        await queue.put(event)

    async def _run() -> None:
        try:
            await run_agent(task, registry, config, on_event=_collector)
        except Exception as exc:
            await queue.put(ErrorEvent(
                turn=0,
                elapsed_ms=0,
                error=str(exc),
            ))
        finally:
            await queue.put(None)  # Sentinel

    loop_task = asyncio.create_task(_run())

    try:
        while True:
            event = await queue.get()
            if event is None:
                break
            yield event
    finally:
        if not loop_task.done():
            loop_task.cancel()


# ---------------------------------------------------------------------------
# Convenience: one-shot run with auto-registry
# ---------------------------------------------------------------------------

async def run_task(
    task: str,
    *,
    api_key: str,
    working_dir: str,
    model: str = "claude-sonnet-4-6",
    system_prompt: str = "",
    max_turns: int = DEFAULT_MAX_TURNS,
    on_event: Callable[[AgentEvent], Any] | None = None,
) -> DoneEvent:
    """High-level entry point: create a registry, register tools, run agent.

    Usage::

        result = await run_task(
            "Add pagination to the /repos endpoint",
            api_key=settings.ANTHROPIC_API_KEY,
            working_dir="/path/to/repo",
        )
        print(result.final_text)
    """
    from forge_ide.adapters import register_builtin_tools

    registry = Registry()
    register_builtin_tools(registry)
    _register_apply_patch_tool(registry)

    config = AgentConfig(
        api_key=api_key,
        model=model,
        system_prompt=system_prompt or _DEFAULT_SYSTEM_PROMPT,
        working_dir=working_dir,
        max_turns=max_turns,
    )

    return await run_agent(task, registry, config, on_event=on_event)


# ---------------------------------------------------------------------------
# WebSocket activity feed bridge
# ---------------------------------------------------------------------------

def make_ws_event_bridge(
    user_id: str,
    build_id: str,
) -> Callable[[AgentEvent], Any]:
    """Create an ``on_event`` callback that broadcasts agent events to the
    WebSocket activity feed.

    This bridges the agent loop's event system into the existing build
    progress WebSocket protocol that the frontend already renders.

    Usage::

        bridge = make_ws_event_bridge(str(user_id), str(build_id))
        await run_agent(task, registry, config, on_event=bridge)

    The frontend's ``BuildProgress.tsx`` and ``DevConsole.tsx`` already
    handle these event types: ``tool_use``, ``build_log``,
    ``context_compaction``.
    """
    from app.ws_manager import manager

    async def _bridge(event: AgentEvent) -> None:
        if isinstance(event, ToolCallEvent):
            await manager.send_to_user(user_id, {
                "type": "tool_use",
                "payload": {
                    "build_id": build_id,
                    "tool_name": event.tool_name,
                    "input_summary": json.dumps(event.tool_input, default=str)[:200],
                    "turn": event.turn,
                    "elapsed_ms": event.elapsed_ms,
                },
            })
        elif isinstance(event, ToolResultEvent):
            status = "ok" if event.response.success else "error"
            summary = (
                json.dumps(event.response.data, default=str)[:200]
                if event.response.success
                else (event.response.error or "")[:200]
            )
            await manager.send_to_user(user_id, {
                "type": "build_log",
                "payload": {
                    "build_id": build_id,
                    "message": f"Tool {event.tool_name}: {status} — {summary}",
                    "source": "tool",
                    "level": "info" if event.response.success else "warning",
                    "duration_ms": event.response.duration_ms,
                },
            })
        elif isinstance(event, TextEvent):
            await manager.send_to_user(user_id, {
                "type": "build_log",
                "payload": {
                    "build_id": build_id,
                    "message": event.text[:500],
                    "source": "agent",
                    "level": "info",
                },
            })
        elif isinstance(event, ContextCompactionEvent):
            await manager.send_to_user(user_id, {
                "type": "context_compaction",
                "payload": {
                    "build_id": build_id,
                    "turn": event.turn,
                    "messages_before": event.messages_before,
                    "messages_after": event.messages_after,
                    "tokens_before": event.tokens_before,
                    "tokens_after": event.tokens_after,
                },
            })
        elif isinstance(event, DoneEvent):
            await manager.send_to_user(user_id, {
                "type": "build_log",
                "payload": {
                    "build_id": build_id,
                    "message": (
                        f"Agent completed in {event.turn} turns, "
                        f"{event.tool_calls_made} tool calls, "
                        f"{event.total_input_tokens}in + "
                        f"{event.total_output_tokens}out tokens "
                        f"({event.elapsed_ms}ms)"
                    ),
                    "source": "system",
                    "level": "info",
                    "total_input_tokens": event.total_input_tokens,
                    "total_output_tokens": event.total_output_tokens,
                    "tool_calls_made": event.tool_calls_made,
                    "elapsed_ms": event.elapsed_ms,
                },
            })
        elif isinstance(event, ErrorEvent):
            await manager.send_to_user(user_id, {
                "type": "build_log",
                "payload": {
                    "build_id": build_id,
                    "message": f"Agent error: {event.error[:300]}",
                    "source": "system",
                    "level": "error",
                },
            })

    return _bridge


# ---------------------------------------------------------------------------
# 8th tool — apply_patch
# ---------------------------------------------------------------------------

def _register_apply_patch_tool(registry: Registry) -> None:
    """Register the ``apply_patch`` tool for surgical file edits via diffs.

    Unlike ``write_file`` (which overwrites the entire file), this tool
    applies a unified diff patch to an existing file — preserving content
    the LLM didn't touch and reducing token cost.
    """
    from pydantic import BaseModel as _BaseModel, Field as _Field

    class ApplyPatchRequest(_BaseModel):
        path: str = _Field(..., min_length=1, description="Relative path to the file to patch")
        diff: str = _Field(..., min_length=1, description=(
            "Unified diff to apply. Must start with @@ hunk headers. "
            "Use standard unified diff format: lines starting with '-' are "
            "removed, '+' are added, ' ' (space) are context."
        ))

    def _handle_apply_patch(req: ApplyPatchRequest, working_dir: str) -> ToolResponse:
        import os
        from forge_ide.patcher import apply_patch
        from forge_ide.errors import PatchConflict, ParseError as PError

        full_path = os.path.normpath(os.path.join(working_dir, req.path))
        # Sandbox check
        if not full_path.startswith(os.path.normpath(working_dir)):
            return ToolResponse.fail(f"Path escapes working directory: {req.path}")

        if not os.path.isfile(full_path):
            return ToolResponse.fail(f"File not found: {req.path}")

        try:
            content = open(full_path, "r", encoding="utf-8").read()
        except Exception as exc:
            return ToolResponse.fail(f"Cannot read {req.path}: {exc}")

        try:
            result = apply_patch(content, req.diff, path=req.path)
        except PatchConflict as exc:
            return ToolResponse.fail(
                f"Patch conflict in {req.path}: {exc}. "
                "Read the file first to check current content, then retry."
            )
        except PError as exc:
            return ToolResponse.fail(f"Malformed diff: {exc}")

        try:
            with open(full_path, "w", encoding="utf-8") as f:
                f.write(result.post_content)
        except Exception as exc:
            return ToolResponse.fail(f"Cannot write {req.path}: {exc}")

        return ToolResponse.ok({
            "path": req.path,
            "hunks_applied": result.hunks_applied,
            "insertions": result.insertions,
            "deletions": result.deletions,
        })

    registry.register(
        "apply_patch",
        _handle_apply_patch,
        ApplyPatchRequest,
        "Apply a unified diff patch to an existing file. More efficient than "
        "write_file for surgical edits — only send the changed lines instead "
        "of the entire file content. The file must already exist. Use standard "
        "unified diff format with @@ hunk headers.",
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class AgentError(Exception):
    """Raised when the agent loop encounters an unrecoverable error."""


def _elapsed_ms(start: float) -> int:
    return int((time.perf_counter() - start) * 1000)


def _now_iso() -> str:
    """Return current UTC time as ISO-8601 string."""
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _format_tool_result(result: ToolResponse, redact_secrets: bool) -> str:
    """Serialize a ToolResponse into a string for the API.

    Large outputs are truncated.  Secrets are redacted if configured.
    """
    if result.success:
        text = json.dumps(result.data, indent=2, default=str)
    else:
        text = f"ERROR: {result.error}"

    # Truncate
    if len(text) > MAX_TOOL_RESULT_CHARS:
        text = text[:MAX_TOOL_RESULT_CHARS] + (
            f"\n\n... [truncated at {MAX_TOOL_RESULT_CHARS:,} chars — "
            f"original was {len(text):,} chars]"
        )

    # Redact secrets
    if redact_secrets:
        text = redact(text)

    return text


async def _emit(
    callback: Callable[[AgentEvent], Any] | None,
    event: AgentEvent,
) -> None:
    """Invoke the event callback, handling both sync and async."""
    if callback is None:
        return
    result = callback(event)
    if asyncio.iscoroutine(result):
        await result


# ---------------------------------------------------------------------------
# Error recovery — tool execution with retry
# ---------------------------------------------------------------------------

_TRANSIENT_ERRORS = frozenset({
    "timeout", "timed out", "connection", "temporary",
    "unavailable", "EAGAIN", "ECONNRESET",
})


def _is_transient(error_msg: str) -> bool:
    """Heuristic: does this error look like a transient/retryable failure?"""
    lower = error_msg.lower()
    return any(kw in lower for kw in _TRANSIENT_ERRORS)


async def _execute_with_retry(
    registry: Registry,
    tool_name: str,
    tool_input: dict,
    working_dir: str,
    max_retries: int,
    retry_delay: float,
) -> ToolResponse:
    """Execute a tool call with retry logic for transient failures.

    Non-transient errors (validation, sandbox violations, logic errors)
    are returned immediately without retry.  Transient errors (timeouts,
    connection issues) are retried up to *max_retries* times with
    exponential backoff.
    """
    last_result: ToolResponse | None = None

    for attempt in range(max_retries + 1):
        try:
            result = await registry.dispatch(tool_name, tool_input, working_dir)
        except Exception as exc:
            result = ToolResponse.fail(
                f"Tool '{tool_name}' raised an exception: {exc}"
            )

        # Success → return immediately
        if result.success:
            return result

        last_result = result

        # Non-transient error → don't retry
        if not _is_transient(result.error or ""):
            return result

        # Transient error → retry with backoff
        if attempt < max_retries:
            wait = retry_delay * (2 ** attempt)
            logger.warning(
                "Tool '%s' transient failure (attempt %d/%d), retrying in %.1fs: %s",
                tool_name, attempt + 1, max_retries + 1, wait, result.error,
            )
            await asyncio.sleep(wait)

    return last_result  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Context window management — conversation compaction
# ---------------------------------------------------------------------------


def _estimate_messages_tokens(messages: list[dict[str, Any]]) -> int:
    """Estimate total tokens across all messages."""
    total = 0
    for msg in messages:
        content = msg.get("content", "")
        if isinstance(content, str):
            total += estimate_tokens(content)
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict):
                    # text blocks, tool_use input, tool_result content
                    for val in block.values():
                        if isinstance(val, str):
                            total += estimate_tokens(val)
                        elif isinstance(val, dict):
                            total += estimate_tokens(json.dumps(val, default=str))
                elif isinstance(block, str):
                    total += estimate_tokens(block)
    return total


def _compact_messages(
    messages: list[dict[str, Any]],
    target_tokens: int,
    journal: SessionJournal | None = None,
) -> list[dict[str, Any]]:
    """Compact conversation history to fit within *target_tokens*.

    Strategy:
    1. Always keep the first message (the original task).
    2. Always keep the last 6 messages (recent context the LLM needs).
    3. Summarize everything in between into a single condensed message.

    If a ``journal`` is provided, the compaction header is replaced with
    the journal's structured summary (current phase, files written, last
    audit result). This ensures the agent retains Forge framework state
    across compaction events.

    This preserves the task context and recent working memory while
    discarding verbose intermediate tool results.
    """
    if len(messages) <= 8:
        # Too few messages to compact meaningfully
        return messages

    first = messages[0]        # Original task
    recent = messages[-6:]     # Last 6 messages = 3 turns of context
    middle = messages[1:-6]    # Everything to summarize

    # Build a compressed summary of the middle section
    summary_parts: list[str] = []
    if journal is not None:
        # Forge-aware compaction header: journal summary replaces generic placeholder
        journal.record("context_compacted", "Context compacted — journal summary injected")
        summary_parts.append(journal.get_summary(max_tokens=1000))
    else:
        summary_parts.append("[CONTEXT COMPACTION — the following summarizes earlier conversation turns]")

    tool_calls_seen: list[str] = []
    files_modified: set[str] = set()
    key_decisions: list[str] = []

    for msg in middle:
        role = msg.get("role", "")
        content = msg.get("content", "")

        if role == "assistant" and isinstance(content, list):
            for block in content:
                if isinstance(block, dict):
                    if block.get("type") == "tool_use":
                        name = block.get("name", "?")
                        inp = block.get("input", {})
                        # Track file modifications
                        if name in ("write_file", "apply_patch") and "path" in inp:
                            files_modified.add(inp["path"])
                        tool_calls_seen.append(f"{name}({json.dumps(inp, default=str)[:80]})")
                    elif block.get("type") == "text":
                        text = block.get("text", "")
                        # Keep short text fragments — they're often plans/decisions
                        if len(text) < 200:
                            key_decisions.append(text.strip())

        elif role == "assistant" and isinstance(content, str):
            if len(content) < 200:
                key_decisions.append(content.strip())

    if tool_calls_seen:
        summary_parts.append(f"Tool calls made: {len(tool_calls_seen)}")
        # Show first and last few calls
        shown = tool_calls_seen[:5]
        if len(tool_calls_seen) > 10:
            shown.append(f"... ({len(tool_calls_seen) - 10} more) ...")
            shown.extend(tool_calls_seen[-5:])
        elif len(tool_calls_seen) > 5:
            shown.extend(tool_calls_seen[5:])
        for tc in shown:
            summary_parts.append(f"  - {tc}")

    if files_modified:
        summary_parts.append(f"Files modified: {', '.join(sorted(files_modified))}")

    if key_decisions:
        summary_parts.append("Key decisions/observations:")
        for d in key_decisions[:10]:
            summary_parts.append(f"  - {d[:150]}")

    summary_parts.append("[END COMPACTION — recent context follows]")
    summary_text = "\n".join(summary_parts)

    # Build compacted messages — ensure valid alternation
    compacted = [first]

    # The summary goes as a user message (to maintain user/assistant alternation)
    compacted.append({"role": "user", "content": summary_text})

    # Need an assistant acknowledgment to maintain alternation before recent messages
    if recent and recent[0].get("role") == "user":
        compacted.append({"role": "assistant", "content": [{"type": "text", "text": "Understood. Continuing from the compacted context."}]})

    compacted.extend(recent)

    # Verify the compacted version is actually smaller
    compacted_tokens = _estimate_messages_tokens(compacted)
    if compacted_tokens >= target_tokens:
        # If still too big, be more aggressive — keep only first + last 4
        compacted = [first]
        compacted.append({"role": "user", "content": "[Previous conversation compacted due to length. Continuing with recent context.]"})
        if messages[-4:][0].get("role") != "assistant":
            compacted.append({"role": "assistant", "content": [{"type": "text", "text": "Understood."}]})
        compacted.extend(messages[-4:])

    return compacted


async def _maybe_compact(
    messages: list[dict[str, Any]],
    config: AgentConfig,
    turn: int,
    loop_start: float,
    on_event: Callable[[AgentEvent], Any] | None,
) -> list[dict[str, Any]]:
    """Check if messages exceed the context window limit and compact if needed."""
    tokens = _estimate_messages_tokens(messages)
    if tokens <= config.context_window_limit:
        return messages

    logger.info(
        "Context window at ~%d tokens (limit %d), compacting...",
        tokens, config.context_window_limit,
    )

    messages_before = len(messages)
    tokens_before = tokens

    compacted = _compact_messages(messages, config.compaction_target, journal=config.journal)

    tokens_after = _estimate_messages_tokens(compacted)
    logger.info(
        "Compacted: %d→%d messages, ~%d→~%d tokens",
        messages_before, len(compacted), tokens_before, tokens_after,
    )

    await _emit(on_event, ContextCompactionEvent(
        turn=turn,
        elapsed_ms=_elapsed_ms(loop_start),
        messages_before=messages_before,
        messages_after=len(compacted),
        tokens_before=tokens_before,
        tokens_after=tokens_after,
    ))
    # Trace: compaction event
    _write_trace(config.trace_log_path, TurnTrace(
        turn=turn,
        timestamp=_now_iso(),
        event_type="compaction",
        model=config.model,
        data={
            "messages_before": messages_before,
            "messages_after": len(compacted),
            "tokens_before": tokens_before,
            "tokens_after": tokens_after,
            "journal_injected": config.journal is not None,
        },
        elapsed_ms=_elapsed_ms(loop_start),
    ))

    return compacted


# ---------------------------------------------------------------------------
# Default system prompt — engineered for autonomous coding agent
# ---------------------------------------------------------------------------

_DEFAULT_SYSTEM_PROMPT = """You are an expert autonomous coding agent embedded in Forge IDE.
You have access to 8 tools for investigating, modifying, and verifying code within
the project working directory. You operate in a loop: read → reason → act → verify.

═══════════════════════════════════════════════════════════════════
AVAILABLE TOOLS (8)
═══════════════════════════════════════════════════════════════════

Investigation:
  • read_file(path)           — Read a file's contents (truncated at 50KB)
  • list_directory(path)      — List files/folders in a directory
  • search_code(pattern,glob) — Search for patterns across the codebase

Modification:
  • write_file(path,content)  — Create or overwrite a file (full content required)
  • apply_patch(path,diff)    — Apply a unified diff to an existing file (surgical edit)

Verification:
  • check_syntax(file_path)   — Check a file for syntax errors
  • run_tests(command,timeout) — Run test suite or specific tests
  • run_command(command,timeout) — Run a sandboxed shell command

═══════════════════════════════════════════════════════════════════
CORE WORKFLOW — Read → Reason → Act → Verify
═══════════════════════════════════════════════════════════════════

1. INVESTIGATE FIRST
   - ALWAYS read relevant files before modifying them. Never guess.
   - Use list_directory to understand project structure.
   - Use search_code to find definitions, usages, and patterns.
   - Read imports and dependencies before modifying a module.

2. PLAN
   - For complex tasks, think step by step.
   - Break work into logical units: one concern at a time.
   - Identify which files need changes and in what order.

3. IMPLEMENT
   - For new files or full rewrites: use write_file with COMPLETE content.
     Never use placeholder comments like "// rest of code here".
   - For surgical edits to existing files: prefer apply_patch — it's more
     token-efficient and preserves unchanged code.
   - Follow the existing code style, naming conventions, and patterns.

4. VERIFY EVERY CHANGE
   - After writing or patching a file, ALWAYS run check_syntax.
   - After each logical unit of changes, run TARGETED tests — not the
     full suite.  Scope tests to files that cover your recent changes:
       Python:  pytest tests/test_<module>.py -x -v
       JS/TS:   npx vitest src/<Module>.test.tsx --run
     If you are unsure which test file exists, use list_directory or
     search_code to discover them first.
   - Run the FULL test suite only ONCE at the end of the phase (final
     verification before sign-off).
   - If tests fail: read the failure output, diagnose, fix, and re-run
     only the failing tests — not the entire suite.
   - Do NOT sign off until syntax passes and full-suite tests pass.

═══════════════════════════════════════════════════════════════════
ERROR RECOVERY
═══════════════════════════════════════════════════════════════════

When something fails, follow this escalation:

1. SYNTAX ERROR after write → Read the file, find the error, fix it.
2. TEST FAILURE → Read the test output, understand the assertion,
   read the relevant source, fix the root cause, re-run ONLY the
   failing test file — not the entire suite.
3. PATCH CONFLICT → The file has changed since you last read it.
   Read the file again to see current content, then retry the patch.
4. TOOL ERROR → Read the error message. If it's a transient issue,
   the system will auto-retry. If persistent, try an alternative approach.
5. STUCK after 3 attempts on the same error → Step back, re-read the
   relevant files, reconsider your approach, and try a different strategy.

═══════════════════════════════════════════════════════════════════
CONTEXT WINDOW MANAGEMENT
═══════════════════════════════════════════════════════════════════

- Your conversation history may be automatically compacted during long tasks.
- If you notice compacted context, re-read any files you need — don't assume
  your memory of file contents is accurate after compaction.
- Prefer apply_patch over write_file for edits — it uses fewer tokens.
- When reading files, request only the parts you need when possible.

═══════════════════════════════════════════════════════════════════
COMPLETION
═══════════════════════════════════════════════════════════════════

- Do NOT ask for permission to continue — keep working until done.
- Complete one logical unit before starting the next.
- When finished, provide a concise summary of what you did.
- If you cannot complete the task, explain what you tried and why it failed."""
