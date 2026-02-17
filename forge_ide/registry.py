"""IDE tool registry — maps tool names to handlers with schema validation.

The ``Registry`` is a plain class (not a singleton) so tests can create
fresh instances.  The build service creates one instance per build and
registers the tools it needs via ``register_builtin_tools``.
"""

from __future__ import annotations

import asyncio
import inspect
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from pydantic import BaseModel, ValidationError

from forge_ide.contracts import ToolResponse
from forge_ide.errors import ToolNotFound


@dataclass
class _ToolEntry:
    """Internal record for a registered tool."""

    name: str
    handler: Callable
    request_model: type[BaseModel]
    description: str
    definition: dict[str, Any] = field(default_factory=dict)


class Registry:
    """Tool registry with schema-validated dispatch.

    Usage::

        reg = Registry()
        reg.register("read_file", handler_fn, ReadFileRequest, "Read a file ...")
        result = await reg.dispatch("read_file", {"path": "foo.py"}, "/work/dir")
    """

    def __init__(self) -> None:
        self._tools: dict[str, _ToolEntry] = {}

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register(
        self,
        name: str,
        handler: Callable,
        request_model: type[BaseModel],
        description: str,
    ) -> None:
        """Register a tool with its handler and request schema.

        Raises ``ValueError`` if a tool with the same name is already
        registered.
        """
        if name in self._tools:
            raise ValueError(f"Tool '{name}' is already registered")

        definition = _build_tool_definition(name, description, request_model)
        self._tools[name] = _ToolEntry(
            name=name,
            handler=handler,
            request_model=request_model,
            description=description,
            definition=definition,
        )

    # ------------------------------------------------------------------
    # Dispatch
    # ------------------------------------------------------------------

    async def dispatch(
        self, name: str, params: dict[str, Any], working_dir: str
    ) -> ToolResponse:
        """Validate *params*, call the tool handler, and return a
        ``ToolResponse`` with measured duration.

        * Unknown tool name → ``ToolResponse.fail`` (also raises nothing)
        * Invalid params → ``ToolResponse.fail`` with validation details
        * Handler exception → ``ToolResponse.fail`` with exception message
        """
        start = time.perf_counter()

        entry = self._tools.get(name)
        if entry is None:
            elapsed = _elapsed_ms(start)
            raise ToolNotFound(name, list(self._tools.keys()))

        # Validate input ------------------------------------------------
        try:
            validated = entry.request_model.model_validate(params)
        except ValidationError as exc:
            return ToolResponse.fail(
                f"Invalid params for '{name}': {exc}",
                duration_ms=_elapsed_ms(start),
            )

        # Call handler ---------------------------------------------------
        try:
            if inspect.iscoroutinefunction(entry.handler):
                result = await entry.handler(validated, working_dir)
            else:
                result = entry.handler(validated, working_dir)
        except Exception as exc:
            return ToolResponse.fail(str(exc), duration_ms=_elapsed_ms(start))

        # Wrap result ----------------------------------------------------
        elapsed = _elapsed_ms(start)
        if isinstance(result, ToolResponse):
            # Handler already returned a ToolResponse — just stamp duration
            return ToolResponse(
                success=result.success,
                data=result.data,
                error=result.error,
                duration_ms=elapsed,
            )

        # Handler returned a plain dict — wrap as success
        if isinstance(result, dict):
            return ToolResponse.ok(result, duration_ms=elapsed)

        # Fallback: stringify
        return ToolResponse.ok({"result": str(result)}, duration_ms=elapsed)

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    def list_tools(self) -> list[dict[str, Any]]:
        """Return Anthropic-compatible tool definitions for all
        registered tools.
        """
        return [entry.definition for entry in self._tools.values()]

    def has_tool(self, name: str) -> bool:
        return name in self._tools

    def tool_names(self) -> list[str]:
        return list(self._tools.keys())


# -----------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------


def _elapsed_ms(start: float) -> int:
    return int((time.perf_counter() - start) * 1000)


def _build_tool_definition(
    name: str, description: str, request_model: type[BaseModel]
) -> dict[str, Any]:
    """Build an Anthropic-compatible tool definition from a Pydantic model."""
    schema = request_model.model_json_schema()

    # Pydantic v2 puts properties + required at the top level of the
    # JSON schema.  We need to emit the exact shape Anthropic expects.
    properties = schema.get("properties", {})
    required = schema.get("required", [])

    # Strip Pydantic metadata keys that Anthropic doesn't understand
    clean_props: dict[str, Any] = {}
    for prop_name, prop_schema in properties.items():
        cleaned = {
            k: v
            for k, v in prop_schema.items()
            if k in ("type", "description", "default", "enum", "items")
        }
        # Ensure every property has a type
        if "type" not in cleaned:
            cleaned["type"] = "string"
        clean_props[prop_name] = cleaned

    return {
        "name": name,
        "description": description,
        "input_schema": {
            "type": "object",
            "properties": clean_props,
            "required": required,
        },
    }
