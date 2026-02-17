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

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Callable

from app.clients.llm_client import chat_anthropic
from forge_ide.contracts import ToolResponse
from forge_ide.redactor import redact
from forge_ide.registry import Registry

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DEFAULT_MAX_TURNS = 50
DEFAULT_MAX_TOKENS = 16384     # per-turn output budget
MAX_TOOL_RESULT_CHARS = 60_000  # truncate large tool outputs


@dataclass(frozen=True)
class AgentConfig:
    """Configuration for an agent loop invocation."""

    api_key: str
    model: str = "claude-sonnet-4-5-20250514"
    system_prompt: str = ""
    max_turns: int = DEFAULT_MAX_TURNS
    max_tokens: int = DEFAULT_MAX_TOKENS
    working_dir: str = "."
    redact_secrets: bool = True


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
        # ── Call Claude ───────────────────────────────────────────
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
                await _emit(on_event, ToolCallEvent(
                    turn=turn,
                    elapsed_ms=_elapsed_ms(loop_start),
                    tool_name=block["name"],
                    tool_input=block["input"],
                    tool_use_id=block["id"],
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
            await _emit(on_event, done)
            return done

        # ── Execute tool calls ────────────────────────────────────
        if stop_reason == "tool_use" and tool_uses:
            tool_results: list[dict[str, Any]] = []

            for tool_block in tool_uses:
                tool_name = tool_block["name"]
                tool_input = tool_block["input"]
                tool_id = tool_block["id"]
                usage.tool_calls += 1

                # Execute via registry
                try:
                    result = await registry.dispatch(
                        tool_name, tool_input, config.working_dir,
                    )
                except Exception as exc:
                    result = ToolResponse.fail(
                        f"Tool '{tool_name}' raised an exception: {exc}"
                    )

                await _emit(on_event, ToolResultEvent(
                    turn=turn,
                    elapsed_ms=_elapsed_ms(loop_start),
                    tool_name=tool_name,
                    tool_use_id=tool_id,
                    response=result,
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
    await _emit(on_event, done)
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
    model: str = "claude-sonnet-4-5-20250514",
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

    config = AgentConfig(
        api_key=api_key,
        model=model,
        system_prompt=system_prompt or _DEFAULT_SYSTEM_PROMPT,
        working_dir=working_dir,
        max_turns=max_turns,
    )

    return await run_agent(task, registry, config, on_event=on_event)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class AgentError(Exception):
    """Raised when the agent loop encounters an unrecoverable error."""


def _elapsed_ms(start: float) -> int:
    return int((time.perf_counter() - start) * 1000)


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
    import asyncio
    result = callback(event)
    if asyncio.iscoroutine(result):
        await result


# ---------------------------------------------------------------------------
# Default system prompt
# ---------------------------------------------------------------------------

_DEFAULT_SYSTEM_PROMPT = """You are an expert coding agent embedded in Forge IDE.
You have access to tools for reading files, writing files, searching code,
running tests, checking syntax, and executing commands within the project.

WORKFLOW:
1. Before editing, ALWAYS read the relevant files first. Never guess at
   file contents or code structure.
2. After writing or editing a file, use check_syntax to verify it parses.
3. Run tests after making changes to verify correctness.
4. If tests fail, read the failure output, fix the issue, and re-run.

EDITING RULES:
- Use write_file to create or overwrite files. Provide the COMPLETE file
  content — never use placeholder comments like "// rest of code here".
- Before modifying a file, read it first to understand the current state.
- Follow the existing code style and patterns in the project.

INVESTIGATION:
- Use search_code to find where things are defined or used.
- Use list_directory to understand project structure.
- Read imports to understand dependencies before modifying a file.

PLANNING:
- For complex tasks, think step by step. Explain your plan briefly before
  starting, then execute systematically.
- Complete one logical unit before starting the next.
- Always verify your work compiles and tests pass before finishing.

When you are done with the task, provide a brief summary of what you did.
Do NOT ask for permission to continue — just keep working until the task
is complete."""
