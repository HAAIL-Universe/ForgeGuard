# Diff Log (overwrite each cycle)

## Cycle Metadata
- Timestamp: 2026-02-15T22:41:58+00:00
- Branch: master
- HEAD: 284a46d72a95b699f6256e9713b03a673d1335a9
- BASE_HEAD: e9bdaf4351f9c160e26357bdc4dbc660f6d83357
- Diff basis: staged

## Cycle Status
- Status: IN_PROCESS

## Summary
- TODO: 1-5 bullets (what changed, why, scope).

## Files Changed (staged)
- Forge/Contracts/physics.yaml
- app/api/routers/projects.py
- app/clients/agent_client.py
- app/services/build_service.py
- app/services/project_service.py
- app/services/tool_executor.py
- tests/test_agent_client.py
- tests/test_build_integration.py
- tests/test_build_service.py
- tests/test_project_service.py
- tests/test_tool_executor.py
- web/src/components/ContractProgress.tsx
- web/src/pages/BuildProgress.tsx

## git status -sb
    ## master...origin/master [ahead 11]
    M  Forge/Contracts/physics.yaml
    M  app/api/routers/projects.py
    M  app/clients/agent_client.py
    M  app/services/build_service.py
    M  app/services/project_service.py
    A  app/services/tool_executor.py
    M  tests/test_agent_client.py
    M  tests/test_build_integration.py
    M  tests/test_build_service.py
    M  tests/test_project_service.py
    A  tests/test_tool_executor.py
    M  web/src/components/ContractProgress.tsx
    M  web/src/pages/BuildProgress.tsx

## Verification
- TODO: verification evidence (static -> runtime -> behavior -> contract).

## Notes (optional)
- TODO: blockers, risks, constraints.

## Next Steps
- TODO: next actions (small, specific).

## Minimal Diff Hunks
    diff --git a/Forge/Contracts/physics.yaml b/Forge/Contracts/physics.yaml
    index 91ecd5c..0902857 100644
    --- a/Forge/Contracts/physics.yaml
    +++ b/Forge/Contracts/physics.yaml
    @@ -181,6 +181,8 @@ paths:
             payload: BuildInterjectionEvent
             type: "recovery_plan"
             payload: RecoveryPlanEvent
    +        type: "tool_use"
    +        payload: ToolUseEvent
     
       # -- Projects (Phase 8) -------------------------------------------
     
    @@ -563,3 +565,8 @@ schemas:
       RecoveryPlanEvent:
         phase: string
         plan_text: string
    +
    +  ToolUseEvent:
    +    tool_name: string
    +    input_summary: string
    +    result_summary: string
    diff --git a/app/api/routers/projects.py b/app/api/routers/projects.py
    index ca1c800..0bc76b7 100644
    --- a/app/api/routers/projects.py
    +++ b/app/api/routers/projects.py
    @@ -8,6 +8,7 @@ from pydantic import BaseModel, Field
     
     from app.api.deps import get_current_user
     from app.services.project_service import (
    +    ContractCancelled,
         cancel_contract_generation,
         create_new_project,
         delete_user_project,
    @@ -206,6 +207,9 @@ async def gen_contracts(
         """Generate all contract files from completed questionnaire answers."""
         try:
             contracts = await generate_contracts(current_user["id"], project_id)
    +    except ContractCancelled:
    +        # User cancelled ÔÇö return 200 with cancelled flag (not an error)
    +        return {"cancelled": True, "contracts": []}
         except ValueError as exc:
             detail = str(exc)
             code = (
    diff --git a/app/clients/agent_client.py b/app/clients/agent_client.py
    index b8b5e22..434f351 100644
    --- a/app/clients/agent_client.py
    +++ b/app/clients/agent_client.py
    @@ -11,6 +11,7 @@ No database access, no business logic, no HTTP framework imports.
     import json
     from collections.abc import AsyncIterator
     from dataclasses import dataclass, field
    +from typing import Union
     
     import httpx
     
    @@ -26,6 +27,14 @@ class StreamUsage:
         model: str = ""
     
     
    +@dataclass
    +class ToolCall:
    +    """Represents a tool_use block from the streaming response."""
    +    id: str
    +    name: str
    +    input: dict
    +
    +
     def _headers(api_key: str) -> dict:
         """Build request headers for the Anthropic API."""
         return {
    @@ -42,8 +51,9 @@ async def stream_agent(
         messages: list[dict],
         max_tokens: int = 16384,
         usage_out: StreamUsage | None = None,
    -) -> AsyncIterator[str]:
    -    """Stream a builder agent session, yielding text chunks.
    +    tools: list[dict] | None = None,
    +) -> AsyncIterator[Union[str, ToolCall]]:
    +    """Stream a builder agent session, yielding text chunks or tool calls.
     
         Args:
             api_key: Anthropic API key.
    @@ -52,21 +62,31 @@ async def stream_agent(
             messages: Conversation history in Anthropic messages format.
             max_tokens: Maximum tokens for the response.
             usage_out: Optional StreamUsage to accumulate token counts.
    +        tools: Optional list of tool definitions in Anthropic format.
     
         Yields:
    -        Incremental text chunks from the builder agent.
    +        str: Incremental text chunks from the builder agent.
    +        ToolCall: A tool_use block that the caller should execute.
     
         Raises:
             httpx.HTTPStatusError: On API errors.
             ValueError: On unexpected stream format.
         """
    -    payload = {
    +    payload: dict = {
             "model": model,
             "max_tokens": max_tokens,
             "system": system_prompt,
             "messages": messages,
             "stream": True,
         }
    +    if tools:
    +        payload["tools"] = tools
    +
    +    # Track tool_use blocks being accumulated
    +    current_tool_id: str = ""
    +    current_tool_name: str = ""
    +    current_tool_json: str = ""
    +    in_tool_block: bool = False
     
         async with httpx.AsyncClient(timeout=300.0) as client:
             async with client.stream(
    @@ -82,7 +102,6 @@ async def stream_agent(
                     data = line[6:]  # strip "data: " prefix
                     if data == "[DONE]":
                         break
    -                # Parse SSE data for content and usage events
                     try:
                         event = json.loads(data)
                         etype = event.get("type", "")
    @@ -99,11 +118,44 @@ async def stream_agent(
                             usage = event.get("usage", {})
                             usage_out.output_tokens += usage.get("output_tokens", 0)
     
    +                    # Content block start ÔÇö detect tool_use blocks
    +                    if etype == "content_block_start":
    +                        block = event.get("content_block", {})
    +                        if block.get("type") == "tool_use":
    +                            in_tool_block = True
    +                            current_tool_id = block.get("id", "")
    +                            current_tool_name = block.get("name", "")
    +                            current_tool_json = ""
    +
    +                    # Content block delta ÔÇö text or tool input JSON
                         if etype == "content_block_delta":
                             delta = event.get("delta", {})
    -                        text = delta.get("text", "")
    -                        if text:
    -                            yield text
    +                        if in_tool_block:
    +                            # Accumulate tool input JSON
    +                            json_chunk = delta.get("partial_json", "")
    +                            if json_chunk:
    +                                current_tool_json += json_chunk
    +                        else:
    +                            text = delta.get("text", "")
    +                            if text:
    +                                yield text
    +
    +                    # Content block stop ÔÇö finalize tool call
    +                    if etype == "content_block_stop" and in_tool_block:
    +                        in_tool_block = False
    +                        try:
    +                            tool_input = json.loads(current_tool_json) if current_tool_json else {}
    +                        except json.JSONDecodeError:
    +                            tool_input = {"_raw": current_tool_json}
    +                        yield ToolCall(
    +                            id=current_tool_id,
    +                            name=current_tool_name,
    +                            input=tool_input,
    +                        )
    +                        current_tool_id = ""
    +                        current_tool_name = ""
    +                        current_tool_json = ""
    +
                     except (ValueError, KeyError):
                         # Skip malformed events
                         continue
    diff --git a/app/services/build_service.py b/app/services/build_service.py
    index 70e7483..d3a4925 100644
    --- a/app/services/build_service.py
    +++ b/app/services/build_service.py
    @@ -19,13 +19,14 @@ from datetime import datetime, timezone
     from pathlib import Path
     from uuid import UUID
     
    -from app.clients.agent_client import StreamUsage, stream_agent
    +from app.clients.agent_client import StreamUsage, ToolCall, stream_agent
     from app.clients import git_client
     from app.clients import github_client
     from app.config import settings
     from app.repos import build_repo
     from app.repos import project_repo
     from app.repos.user_repo import get_user_by_id
    +from app.services.tool_executor import BUILDER_TOOLS, execute_tool
     from app.ws_manager import manager
     
     logger = logging.getLogger(__name__)
    @@ -685,7 +686,19 @@ async def _run_build(
                 "=== END PLAN ===\n\n"
                 "Do NOT plan ahead to future phases. Each phase gets its own fresh plan.\n\n"
                 "As you complete each task, emit: === TASK DONE: N ===\n"
    -            "where N is the task number from your current phase plan.\n"
    +            "where N is the task number from your current phase plan.\n\n"
    +            "## Tools\n"
    +            "You have access to the following tools for interacting with the project:\n"
    +            "- **read_file**: Read a file to check existing code or verify your work.\n"
    +            "- **list_directory**: List files/folders to understand project structure before making changes.\n"
    +            "- **search_code**: Search for patterns across files to find implementations or imports.\n"
    +            "- **write_file**: Write or overwrite a file. Preferred over === FILE: ... === blocks.\n\n"
    +            "Guidelines for tool use:\n"
    +            "1. Use list_directory at the start of each phase to understand the current state.\n"
    +            "2. Use read_file to examine existing code before modifying it.\n"
    +            "3. Prefer write_file tool over === FILE: path === blocks for creating/updating files.\n"
    +            "4. Use search_code to find existing patterns, imports, or implementations.\n"
    +            "5. After writing files, use read_file to verify the content was written correctly.\n"
             )
     
             # Emit build overview (high-level phase list) at build start
    @@ -768,13 +781,68 @@ async def _run_build(
     
                 # Stream agent output for this turn
                 turn_text = ""
    -            async for chunk in stream_agent(
    +            tool_calls_this_turn: list[dict] = []
    +            pending_tool_results: list[dict] = []
    +
    +            async for item in stream_agent(
                     api_key=api_key,
                     model=settings.LLM_BUILDER_MODEL,
                     system_prompt=system_prompt,
                     messages=messages,
                     usage_out=usage,
    +                tools=BUILDER_TOOLS if working_dir else None,
                 ):
    +                if isinstance(item, ToolCall):
    +                    # --- Tool call detected ---
    +                    tool_result = execute_tool(item.name, item.input, working_dir or "")
    +
    +                    # Log the tool call
    +                    input_summary = json.dumps(item.input)[:200]
    +                    result_summary = tool_result[:300]
    +                    await build_repo.append_build_log(
    +                        build_id,
    +                        f"Tool: {item.name}({input_summary}) ÔåÆ {result_summary}",
    +                        source="tool", level="info",
    +                    )
    +
    +                    # Broadcast tool_use WS event
    +                    await _broadcast_build_event(
    +                        user_id, build_id, "tool_use", {
    +                            "tool_name": item.name,
    +                            "input_summary": input_summary,
    +                            "result_summary": result_summary,
    +                        }
    +                    )
    +
    +                    # Track write_file calls as files_written
    +                    if item.name == "write_file" and tool_result.startswith("OK:"):
    +                        rel_path = item.input.get("path", "")
    +                        content = item.input.get("content", "")
    +                        lang = _detect_language(rel_path)
    +                        if rel_path and not any(f["path"] == rel_path for f in files_written):
    +                            files_written.append({
    +                                "path": rel_path,
    +                                "size_bytes": len(content),
    +                                "language": lang,
    +                            })
    +                        # Emit file_created event
    +                        await _broadcast_build_event(
    +                            user_id, build_id, "file_created", {
    +                                "path": rel_path,
    +                                "size_bytes": len(content),
    +                                "language": lang,
    +                            }
    +                        )
    +
    +                    tool_calls_this_turn.append({
    +                        "id": item.id,
    +                        "name": item.name,
    +                        "result": tool_result,
    +                    })
    +                    continue
    +
    +                # --- Text chunk ---
    +                chunk = item
                     accumulated_text += chunk
                     turn_text += chunk
     
    @@ -832,7 +900,37 @@ async def _run_build(
                                     }
                                 )
     
    -            # Turn complete ÔÇö add assistant response to conversation history
    +            # Turn complete ÔÇö handle tool calls if any
    +            if tool_calls_this_turn:
    +                # Build the assistant message with tool_use content blocks
    +                assistant_content: list[dict] = []
    +                if turn_text:
    +                    assistant_content.append({"type": "text", "text": turn_text})
    +                for tc in tool_calls_this_turn:
    +                    assistant_content.append({
    +                        "type": "tool_use",
    +                        "id": tc["id"],
    +                        "name": tc["name"],
    +                        "input": {},  # original input not needed in history
    +                    })
    +                messages.append({"role": "assistant", "content": assistant_content})
    +
    +                # Add tool results as a user message
    +                tool_results_content: list[dict] = [
    +                    {
    +                        "type": "tool_result",
    +                        "tool_use_id": tc["id"],
    +                        "content": tc["result"][:10_000],  # cap result size
    +                    }
    +                    for tc in tool_calls_this_turn
    +                ]
    +                messages.append({"role": "user", "content": tool_results_content})
    +
    +                # Continue to next iteration ÔÇö the agent will respond to tool results
    +                total_tokens_all_turns += usage.input_tokens + usage.output_tokens
    +                continue
    +
    +            # Add assistant response to conversation history
                 messages.append({"role": "assistant", "content": turn_text})
     
                 # Check for user interjections between turns
    diff --git a/app/services/project_service.py b/app/services/project_service.py
    index 16b823a..f3ffa26 100644
    --- a/app/services/project_service.py
    +++ b/app/services/project_service.py
    @@ -1,5 +1,6 @@
     """Project service -- orchestrates project CRUD, questionnaire chat, and contract generation."""
     
    +import asyncio
     import json
     import logging
     from pathlib import Path
    @@ -23,8 +24,13 @@ from app.repos.project_repo import (
     
     logger = logging.getLogger(__name__)
     
    -# Active contract generation tasks ÔÇö checked between contracts for cancellation
    -_active_generations: set[str] = set()
    +
    +class ContractCancelled(Exception):
    +    """Raised when contract generation is cancelled by the user."""
    +
    +
    +# Active contract generation tasks ÔÇö maps project-id ÔåÆ cancel Event
    +_active_generations: dict[str, asyncio.Event] = {}
     
     # ---------------------------------------------------------------------------
     # Questionnaire definitions
    @@ -434,14 +440,15 @@ async def generate_contracts(
             llm_model = settings.LLM_QUESTIONNAIRE_MODEL
     
         pid = str(project_id)
    -    _active_generations.add(pid)
    +    cancel_event = asyncio.Event()
    +    _active_generations[pid] = cancel_event
     
         generated = []
         total = len(CONTRACT_TYPES)
         try:
             for idx, contract_type in enumerate(CONTRACT_TYPES):
                 # Check cancellation between contracts
    -            if pid not in _active_generations:
    +            if cancel_event.is_set():
                     logger.info("Contract generation cancelled for project %s", pid)
                     await manager.send_to_user(str(user_id), {
                         "type": "contract_progress",
    @@ -453,7 +460,7 @@ async def generate_contracts(
                             "total": total,
                         },
                     })
    -                raise ValueError("Contract generation cancelled")
    +                raise ContractCancelled("Contract generation cancelled")
     
                 # Notify client that generation of this contract has started
                 await manager.send_to_user(str(user_id), {
    @@ -467,9 +474,40 @@ async def generate_contracts(
                     },
                 })
     
    -            content, usage = await _generate_contract_content(
    -                contract_type, project, answers_text, llm_api_key, llm_model, provider
    +            # Race the LLM call against the cancel event so cancellation
    +            # takes effect immediately, even mid-generation.
    +            llm_task = asyncio.ensure_future(
    +                _generate_contract_content(
    +                    contract_type, project, answers_text, llm_api_key, llm_model, provider
    +                )
    +            )
    +            cancel_task = asyncio.ensure_future(cancel_event.wait())
    +
    +            done, pending = await asyncio.wait(
    +                [llm_task, cancel_task],
    +                return_when=asyncio.FIRST_COMPLETED,
                 )
    +
    +            if cancel_task in done:
    +                # Cancel fired while LLM was running ÔÇö abort immediately
    +                llm_task.cancel()
    +                logger.info("Contract generation cancelled mid-LLM for project %s", pid)
    +                await manager.send_to_user(str(user_id), {
    +                    "type": "contract_progress",
    +                    "payload": {
    +                        "project_id": pid,
    +                        "contract_type": contract_type,
    +                        "status": "cancelled",
    +                        "index": idx,
    +                        "total": total,
    +                    },
    +                })
    +                raise ContractCancelled("Contract generation cancelled")
    +
    +            # LLM finished first ÔÇö clean up the cancel waiter
    +            cancel_task.cancel()
    +            content, usage = llm_task.result()
    +
                 row = await upsert_contract(project_id, contract_type, content)
                 generated.append({
                     "id": str(row["id"]),
    @@ -494,7 +532,7 @@ async def generate_contracts(
                     },
                 })
         finally:
    -        _active_generations.discard(pid)
    +        _active_generations.pop(pid, None)
     
         await update_project_status(project_id, "contracts_ready")
         return generated
    @@ -506,11 +544,12 @@ async def cancel_contract_generation(
     ) -> dict:
         """Cancel an in-progress contract generation.
     
    -    Removes the project from the active set so the generation loop
    -    stops at the next contract boundary.
    +    Sets the cancel event so the generation loop stops immediately,
    +    even if an LLM call is currently in flight.
         """
         pid = str(project_id)
    -    if pid not in _active_generations:
    +    cancel_event = _active_generations.get(pid)
    +    if cancel_event is None:
             raise ValueError("No active contract generation for this project")
     
         # Verify ownership
    @@ -518,7 +557,7 @@ async def cancel_contract_generation(
         if not project or str(project["user_id"]) != str(user_id):
             raise ValueError("Project not found")
     
    -    _active_generations.discard(pid)
    +    cancel_event.set()
         logger.info("Contract generation cancel requested for project %s", pid)
         return {"status": "cancelling"}
     
    diff --git a/app/services/tool_executor.py b/app/services/tool_executor.py
    new file mode 100644
    index 0000000..1f3f523
    --- /dev/null
    +++ b/app/services/tool_executor.py
    @@ -0,0 +1,310 @@
    +"""Tool executor -- sandboxed tool handlers for builder agent tool use.
    +
    +Each tool handler enforces path sandboxing (no traversal outside working_dir),
    +size limits, and returns string results (required by Anthropic tool API).
    +All handlers catch exceptions and return error strings rather than raising.
    +"""
    +
    +import fnmatch
    +import os
    +import re
    +from pathlib import Path
    +
    +# ---------------------------------------------------------------------------
    +# Constants
    +# ---------------------------------------------------------------------------
    +
    +MAX_READ_FILE_BYTES = 50_000  # 50KB max for read_file
    +MAX_SEARCH_RESULTS = 50
    +MAX_WRITE_FILE_BYTES = 500_000  # 500KB max for write_file
    +SKIP_DIRS = frozenset({
    +    ".git", "__pycache__", "node_modules", ".venv", "venv",
    +    ".tox", "dist", "build", ".mypy_cache", ".pytest_cache",
    +})
    +
    +
    +# ---------------------------------------------------------------------------
    +# Path sandboxing
    +# ---------------------------------------------------------------------------
    +
    +
    +def _resolve_sandboxed(rel_path: str, working_dir: str) -> Path | None:
    +    """Resolve a relative path within working_dir, enforcing sandbox.
    +
    +    Returns the resolved absolute Path if safe, or None if the path
    +    would escape the sandbox (e.g. via `..` traversal or absolute paths).
    +    """
    +    if not rel_path or not working_dir:
    +        return None
    +
    +    # Reject absolute paths
    ... (1222 lines truncated, 1722 total)
