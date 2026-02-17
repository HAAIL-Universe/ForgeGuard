"""IDE runtime error hierarchy.

Every error carries typed fields (not just a message string),
supports ``to_dict()`` for serialisation into ``ToolResponse.error``,
and has a readable ``__str__`` for logging.
"""

from __future__ import annotations


class IDEError(Exception):
    """Base error for all IDE runtime failures."""

    def __init__(self, message: str, *, detail: dict | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.detail = detail or {}

    def to_dict(self) -> dict:
        return {"error": type(self).__name__, "message": self.message, **self.detail}

    def __str__(self) -> str:
        return self.message


class SandboxViolation(IDEError):
    """Path resolved outside the workspace root."""

    def __init__(
        self,
        path: str,
        attempted_path: str | None = None,
        *,
        root: str | None = None,
        reason: str | None = None,
    ) -> None:
        self.path = path
        self.attempted_path = attempted_path or ""
        self.root = root or ""
        self.reason = reason or ""

        if reason:
            msg = f"Sandbox violation: {reason} (path={path!r}, root={root!r})"
        else:
            msg = (
                f"Sandbox violation: '{path}' resolved to "
                f"'{self.attempted_path}' which is outside the workspace"
            )

        detail: dict = {"path": path}
        if self.attempted_path:
            detail["attempted_path"] = self.attempted_path
        if root:
            detail["root"] = root
        if reason:
            detail["reason"] = reason

        super().__init__(msg, detail=detail)


class ToolTimeout(IDEError):
    """A tool exceeded its allowed execution time."""

    def __init__(self, tool_name: str, timeout_ms: int) -> None:
        self.tool_name = tool_name
        self.timeout_ms = timeout_ms
        super().__init__(
            f"Tool '{tool_name}' timed out after {timeout_ms}ms",
            detail={"tool_name": tool_name, "timeout_ms": timeout_ms},
        )


class ParseError(IDEError):
    """Failed to parse tool output into a structured result."""

    def __init__(self, raw_output: str, parser_name: str) -> None:
        self.raw_output = raw_output
        self.parser_name = parser_name
        super().__init__(
            f"Parser '{parser_name}' failed to parse output ({len(raw_output)} chars)",
            detail={"parser_name": parser_name, "raw_output_length": len(raw_output)},
        )


class PatchConflict(IDEError):
    """A diff hunk does not match the target file content."""

    def __init__(
        self, file_path: str, hunk_index: int, expected: str, actual: str
    ) -> None:
        self.file_path = file_path
        self.hunk_index = hunk_index
        self.expected = expected
        self.actual = actual
        super().__init__(
            f"Patch conflict in '{file_path}' at hunk {hunk_index}",
            detail={
                "file_path": file_path,
                "hunk_index": hunk_index,
                "expected": expected,
                "actual": actual,
            },
        )


class ToolNotFound(IDEError):
    """Requested tool name is not registered."""

    def __init__(self, tool_name: str, available_tools: list[str]) -> None:
        self.tool_name = tool_name
        self.available_tools = available_tools
        super().__init__(
            f"Tool '{tool_name}' not found. Available: {', '.join(available_tools)}",
            detail={"tool_name": tool_name, "available_tools": available_tools},
        )
