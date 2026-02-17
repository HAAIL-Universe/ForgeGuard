"""Language intelligence models — shared types for all language parsers.

Provides ``Symbol``, ``ImportInfo``, and ``DiagnosticReport`` models
used by language-specific modules (python_intel, ts_intel) and the
diagnostics aggregator.

All models are frozen (immutable after creation).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from forge_ide.contracts import Diagnostic

# ---------------------------------------------------------------------------
# Symbol model
# ---------------------------------------------------------------------------


class Symbol(BaseModel):
    """A named symbol extracted from source code."""

    model_config = ConfigDict(frozen=True)

    name: str
    kind: Literal[
        "class",
        "function",
        "method",
        "variable",
        "interface",
        "type_alias",
        "enum",
        "constant",
    ]
    start_line: int = Field(..., ge=1, description="1-based start line")
    end_line: int = Field(..., ge=1, description="1-based end line (inclusive)")
    parent: str | None = Field(
        default=None, description="Enclosing class/function name"
    )


# ---------------------------------------------------------------------------
# Import info model
# ---------------------------------------------------------------------------


class ImportInfo(BaseModel):
    """A resolved import statement."""

    model_config = ConfigDict(frozen=True)

    module: str = Field(..., description="Module path, e.g. 'os.path' or './utils'")
    names: list[str] = Field(
        default_factory=list, description="Imported names ([] for bare import)"
    )
    resolved_path: str | None = Field(
        default=None, description="Workspace-relative path if resolved"
    )
    is_stdlib: bool = Field(default=False, description="True if standard library")


# ---------------------------------------------------------------------------
# Diagnostic report model
# ---------------------------------------------------------------------------


class DiagnosticReport(BaseModel):
    """Aggregated diagnostics across one or more files."""

    model_config = ConfigDict(frozen=True)

    files: dict[str, list[Diagnostic]] = Field(
        default_factory=dict, description="Path → diagnostics"
    )
    error_count: int = Field(default=0, ge=0)
    warning_count: int = Field(default=0, ge=0)
    info_count: int = Field(default=0, ge=0)
    hint_count: int = Field(default=0, ge=0)


__all__ = [
    "DiagnosticReport",
    "ImportInfo",
    "Symbol",
]
