"""IDE runtime contracts — Pydantic models for tool requests and responses.

Every tool in the IDE runtime communicates through these models.
Responses are always structured JSON (never raw strings).
All models are frozen (immutable after creation).
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# Shared sub-models (used across many tools in later phases)
# ---------------------------------------------------------------------------


class LineRange(BaseModel):
    """Inclusive line range within a file."""

    model_config = ConfigDict(frozen=True)

    start: int = Field(..., ge=1, description="Start line (1-based, inclusive)")
    end: int = Field(..., ge=1, description="End line (1-based, inclusive)")


class Snippet(BaseModel):
    """A code snippet extracted from a file."""

    model_config = ConfigDict(frozen=True)

    path: str
    start_line: int = Field(..., ge=1)
    end_line: int = Field(..., ge=1)
    content: str


class Diagnostic(BaseModel):
    """A single diagnostic (error/warning/info/hint) from a language tool."""

    model_config = ConfigDict(frozen=True)

    file: str
    line: int = Field(..., ge=1)
    column: int = Field(..., ge=0)
    message: str
    severity: Literal["error", "warning", "info", "hint"]
    code: str | None = None


class UnifiedDiff(BaseModel):
    """A unified diff for a single file."""

    model_config = ConfigDict(frozen=True)

    path: str
    hunks: list[str]
    insertions: int = Field(..., ge=0)
    deletions: int = Field(..., ge=0)


# ---------------------------------------------------------------------------
# Core request / response
# ---------------------------------------------------------------------------


class ToolRequest(BaseModel):
    """Generic tool invocation request."""

    model_config = ConfigDict(frozen=True)

    name: str
    params: dict[str, Any] = Field(default_factory=dict)


class ToolResponse(BaseModel):
    """Structured result from any IDE tool invocation.

    Use the ``ok`` / ``fail`` factory class methods for clean construction.
    """

    model_config = ConfigDict(frozen=True)

    success: bool
    data: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
    duration_ms: int = 0

    @classmethod
    def ok(cls, data: dict[str, Any], *, duration_ms: int = 0) -> ToolResponse:
        """Create a successful response."""
        return cls(success=True, data=data, duration_ms=duration_ms)

    @classmethod
    def fail(cls, error: str, *, duration_ms: int = 0) -> ToolResponse:
        """Create a failure response."""
        return cls(success=False, error=error, duration_ms=duration_ms)


# ---------------------------------------------------------------------------
# Per-tool request models
# ---------------------------------------------------------------------------


class ReadFileRequest(BaseModel):
    """Request schema for the ``read_file`` tool."""

    model_config = ConfigDict(frozen=True)

    path: str = Field(..., min_length=1, description="Relative path to the file")


class ListDirectoryRequest(BaseModel):
    """Request schema for the ``list_directory`` tool."""

    model_config = ConfigDict(frozen=True)

    path: str = Field(
        default=".", min_length=1, description="Relative path to the directory"
    )


class SearchCodeRequest(BaseModel):
    """Request schema for the ``search_code`` tool."""

    model_config = ConfigDict(frozen=True)

    pattern: str = Field(..., min_length=1, description="Search string or regex")
    glob: str | None = Field(
        default=None, description="Optional file glob filter (e.g. '*.py')"
    )


class WriteFileRequest(BaseModel):
    """Request schema for the ``write_file`` tool."""

    model_config = ConfigDict(frozen=True)

    path: str = Field(..., min_length=1, description="Relative path for the file")
    content: str = Field(..., description="Full content to write")


class RunTestsRequest(BaseModel):
    """Request schema for the ``run_tests`` tool."""

    model_config = ConfigDict(frozen=True)

    command: str = Field(..., min_length=1, description="Test command to run")
    timeout: int = Field(default=120, ge=1, le=300, description="Timeout in seconds")


class CheckSyntaxRequest(BaseModel):
    """Request schema for the ``check_syntax`` tool."""

    model_config = ConfigDict(frozen=True)

    file_path: str = Field(
        ..., min_length=1, description="Relative path to the file to check"
    )


class RunCommandRequest(BaseModel):
    """Request schema for the ``run_command`` tool."""

    model_config = ConfigDict(frozen=True)

    command: str = Field(..., min_length=1, description="Shell command to run")
    timeout: int = Field(default=60, ge=1, le=300, description="Timeout in seconds")
