"""Headless IDE runtime — structured tool contracts, registry, and dispatch.

Public API
----------
Agent loop::

    AgentConfig, AgentError, AgentEvent,
    ContextCompactionEvent,
    ThinkingEvent, ToolCallEvent, ToolResultEvent,
    TextEvent, DoneEvent, ErrorEvent,
    run_agent, stream_agent, run_task,

Contracts (Pydantic models)::

    ToolRequest, ToolResponse,
    LineRange, Snippet, Diagnostic, UnifiedDiff,
    ReadFileRequest, ListDirectoryRequest, SearchCodeRequest,
    WriteFileRequest, RunTestsRequest, CheckSyntaxRequest,
    RunCommandRequest,

Registry::

    Registry  — register / dispatch / list_tools

Errors::

    IDEError, SandboxViolation, ToolTimeout,
    ParseError, PatchConflict, ToolNotFound,

Workspace::

    Workspace, FileEntry, WorkspaceSummary

Git operations::

    git_ops  — structured git wrappers returning ToolResponse

Reader::

    ide_read_file, read_range, read_symbol

Searcher::

    Match, search

File index::

    FileIndex, FileMetadata

Runner::

    ide_run, RunResult, validate_command

Log parsers::

    summarise_pytest, summarise_npm_test, summarise_build,
    summarise_generic, auto_summarise, detect_parser,
    PytestSummary, NpmTestSummary, BuildSummary, BuildIssue,
    GenericSummary, TestFailure,

Patch engine::

    Hunk, PatchResult, apply_patch, apply_multi_patch, parse_unified_diff,
    generate_diff, generate_multi_diff, diff_to_text,

Language intelligence::

    Symbol, ImportInfo, DiagnosticReport,
    parse_ruff_json, parse_pyright_json, parse_python_ast_errors,
    extract_python_symbols, resolve_python_imports,
    parse_tsc_output, parse_eslint_json, extract_ts_symbols,
    merge_diagnostics, detect_language,

Relevance scoring::

    RelatedFile, find_related,
    score_import_graph, score_directory_proximity,
    score_name_similarity, score_recency,

Context packs::

    ContextPack, TargetFile, DependencySnippet, RepoSummary,
    assemble_pack, build_repo_summary, build_structure_tree,
    estimate_tokens, pack_to_text,

Response parser::

    ParsedResponse, classify_response, strip_fences,
    ensure_trailing_newline, parse_response,

Build helpers::

    ApplyResult, VerificationResult,
    apply_response, run_and_summarise,

Backoff & concurrency::

    ExponentialBackoff, ConcurrencyLimiter,

Secret redaction::

    SecretMatch, DEFAULT_PATTERNS, REDACTED,
    redact, has_secrets, find_secrets,

Output sanitisation::

    normalise_path, sort_file_list, sort_matches,
    sort_diagnostics, sort_symbols,
    strip_timestamps, strip_pids, strip_tmpdir,
    normalise_paths, sanitise_output,

Adapters::

    register_builtin_tools  — wire existing tool_executor handlers
"""

from forge_ide.agent import (
    AgentConfig,
    AgentError,
    AgentEvent,
    ContextCompactionEvent,
    DoneEvent,
    ErrorEvent,
    TextEvent,
    ThinkingEvent,
    ToolCallEvent,
    ToolResultEvent,
    make_ws_event_bridge,
    run_agent,
    run_task,
    stream_agent,
)
from forge_ide import git_ops
from forge_ide.adapters import register_builtin_tools
from forge_ide.backoff import ConcurrencyLimiter, ExponentialBackoff
from forge_ide.build_helpers import (
    ApplyResult,
    VerificationResult,
    apply_response,
    run_and_summarise,
)
from forge_ide.context_pack import (
    ContextPack,
    DependencySnippet,
    RepoSummary,
    TargetFile,
    assemble_pack,
    build_context_pack_for_file,
    build_repo_summary,
    build_structure_tree,
    estimate_tokens,
    pack_to_text,
)
from forge_ide.contracts import (
    CheckSyntaxRequest,
    Diagnostic,
    LineRange,
    ListDirectoryRequest,
    ReadFileRequest,
    RunCommandRequest,
    RunTestsRequest,
    SearchCodeRequest,
    Snippet,
    ToolRequest,
    ToolResponse,
    UnifiedDiff,
    WriteFileRequest,
)
from forge_ide.errors import (
    IDEError,
    ParseError,
    PatchConflict,
    SandboxViolation,
    ToolNotFound,
    ToolTimeout,
)
from forge_ide.diagnostics import detect_language, merge_diagnostics
from forge_ide.diff_generator import diff_to_text, generate_diff, generate_multi_diff
from forge_ide.file_index import FileIndex, FileMetadata
from forge_ide.lang import DiagnosticReport, ImportInfo, Symbol
from forge_ide.lang.python_intel import (
    extract_symbols as extract_python_symbols,
    parse_pyright_json,
    parse_python_ast_errors,
    parse_ruff_json,
    resolve_imports as resolve_python_imports,
)
from forge_ide.lang.ts_intel import (
    extract_symbols as extract_ts_symbols,
    parse_eslint_json,
    parse_tsc_output,
)
from forge_ide.log_parser import (
    BuildIssue,
    BuildSummary,
    GenericSummary,
    NpmTestSummary,
    PytestSummary,
    TestFailure,
    auto_summarise,
    detect_parser,
    summarise_build,
    summarise_generic,
    summarise_npm_test,
    summarise_pytest,
)
from forge_ide.patcher import (
    Hunk,
    PatchResult,
    apply_multi_patch,
    apply_patch,
    parse_unified_diff,
)
from forge_ide.reader import read_file as ide_read_file
from forge_ide.reader import read_range, read_symbol
from forge_ide.redactor import (
    DEFAULT_PATTERNS,
    REDACTED,
    SecretMatch,
    find_secrets,
    has_secrets,
    redact,
)
from forge_ide.registry import Registry
from forge_ide.relevance import (
    RelatedFile,
    find_related,
    score_directory_proximity,
    score_import_graph,
    score_name_similarity,
    score_recency,
)
from forge_ide.response_parser import (
    ParsedResponse,
    classify_response,
    ensure_trailing_newline,
    parse_response,
    strip_fences,
)
from forge_ide.runner import RunResult
from forge_ide.runner import run as ide_run
from forge_ide.runner import validate_command
from forge_ide.sanitiser import (
    normalise_path,
    normalise_paths,
    sanitise_output,
    sort_diagnostics,
    sort_file_list,
    sort_matches,
    sort_symbols,
    strip_pids,
    strip_timestamps,
    strip_tmpdir,
)
from forge_ide.searcher import Match, search
from forge_ide.workspace import (
    FileEntry,
    SchemaInventory,
    TestInventory,
    Workspace,
    WorkspaceSnapshot,
    WorkspaceSummary,
    capture_snapshot,
    snapshot_to_workspace_info,
    update_snapshot,
)

__all__ = [
    # Agent loop
    "AgentConfig",
    "AgentError",
    "AgentEvent",
    "ContextCompactionEvent",
    "DoneEvent",
    "ErrorEvent",
    "TextEvent",
    "ThinkingEvent",
    "ToolCallEvent",
    "ToolResultEvent",
    "run_agent",
    "run_task",
    "stream_agent",
    "make_ws_event_bridge",
    # Contracts
    "ToolRequest",
    "ToolResponse",
    "LineRange",
    "Snippet",
    "Diagnostic",
    "UnifiedDiff",
    "ReadFileRequest",
    "ListDirectoryRequest",
    "SearchCodeRequest",
    "WriteFileRequest",
    "RunTestsRequest",
    "CheckSyntaxRequest",
    "RunCommandRequest",
    # Registry
    "Registry",
    # Errors
    "IDEError",
    "SandboxViolation",
    "ToolTimeout",
    "ParseError",
    "PatchConflict",
    "ToolNotFound",
    # Workspace
    "Workspace",
    "FileEntry",
    "WorkspaceSummary",
    "WorkspaceSnapshot",
    "TestInventory",
    "SchemaInventory",
    "capture_snapshot",
    "update_snapshot",
    "snapshot_to_workspace_info",
    # Git operations
    "git_ops",
    # Reader
    "ide_read_file",
    "read_range",
    "read_symbol",
    # Searcher
    "Match",
    "search",
    # File index
    "FileIndex",
    "FileMetadata",
    # Runner
    "ide_run",
    "RunResult",
    "validate_command",
    # Log parsers
    "summarise_pytest",
    "summarise_npm_test",
    "summarise_build",
    "summarise_generic",
    "auto_summarise",
    "detect_parser",
    "PytestSummary",
    "NpmTestSummary",
    "BuildSummary",
    "BuildIssue",
    "GenericSummary",
    "TestFailure",
    # Patch engine
    "Hunk",
    "PatchResult",
    "apply_patch",
    "apply_multi_patch",
    "parse_unified_diff",
    "generate_diff",
    "generate_multi_diff",
    "diff_to_text",
    # Language intelligence
    "Symbol",
    "ImportInfo",
    "DiagnosticReport",
    "parse_ruff_json",
    "parse_pyright_json",
    "parse_python_ast_errors",
    "extract_python_symbols",
    "resolve_python_imports",
    "parse_tsc_output",
    "parse_eslint_json",
    "extract_ts_symbols",
    "merge_diagnostics",
    "detect_language",
    # Relevance scoring
    "RelatedFile",
    "find_related",
    "score_import_graph",
    "score_directory_proximity",
    "score_name_similarity",
    "score_recency",
    # Context packs
    "ContextPack",
    "TargetFile",
    "DependencySnippet",
    "RepoSummary",
    "assemble_pack",
    "build_context_pack_for_file",
    "build_repo_summary",
    "build_structure_tree",
    "estimate_tokens",
    "pack_to_text",
    # Response parser
    "ParsedResponse",
    "classify_response",
    "strip_fences",
    "ensure_trailing_newline",
    "parse_response",
    # Build helpers
    "ApplyResult",
    "VerificationResult",
    "apply_response",
    "run_and_summarise",
    # Backoff & concurrency
    "ExponentialBackoff",
    "ConcurrencyLimiter",
    # Secret redaction
    "SecretMatch",
    "DEFAULT_PATTERNS",
    "REDACTED",
    "redact",
    "has_secrets",
    "find_secrets",
    # Output sanitisation
    "normalise_path",
    "sort_file_list",
    "sort_matches",
    "sort_diagnostics",
    "sort_symbols",
    "strip_timestamps",
    "strip_pids",
    "strip_tmpdir",
    "normalise_paths",
    "sanitise_output",
    # Adapters
    "register_builtin_tools",
]
