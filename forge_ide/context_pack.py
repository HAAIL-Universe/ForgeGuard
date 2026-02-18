"""Context pack assembly — build compact, token-budgeted context packs.

Provides models and functions to assemble a ``ContextPack`` that
contains everything an LLM builder needs: repository summary, target
file contents, dependency snippets, related code, diagnostics, test
output, and git diff summaries.

Also provides ``build_context_pack_for_file()`` which uses a
``WorkspaceSnapshot`` to select only the contracts and dependency
context relevant to a specific file being generated.

All functions are pure — they operate on in-memory data, never touch
the filesystem or spawn subprocesses.
"""

from __future__ import annotations

from pathlib import PurePosixPath

from pydantic import BaseModel, ConfigDict, Field

from forge_ide.contracts import Diagnostic, Snippet
from forge_ide.lang import DiagnosticReport

# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class TargetFile(BaseModel):
    """Full content of a file being created or modified."""

    model_config = ConfigDict(frozen=True)

    path: str
    content: str
    diagnostics: list[Diagnostic] = Field(default_factory=list)


class DependencySnippet(BaseModel):
    """A dependency file snippet included for context."""

    model_config = ConfigDict(frozen=True)

    path: str
    content: str
    why: str = Field(..., description="Reason this file is included")


class RepoSummary(BaseModel):
    """High-level repository summary."""

    model_config = ConfigDict(frozen=True)

    file_count: int = 0
    languages: dict[str, int] = Field(default_factory=dict)
    structure_tree: str = ""


class ContextPack(BaseModel):
    """A complete, token-budgeted context pack for LLM consumption."""

    model_config = ConfigDict(frozen=True)

    repo_summary: RepoSummary = Field(default_factory=RepoSummary)
    target_files: list[TargetFile] = Field(default_factory=list)
    dependency_snippets: list[DependencySnippet] = Field(default_factory=list)
    related_snippets: list[Snippet] = Field(default_factory=list)
    diagnostics_summary: DiagnosticReport | None = None
    test_output: str = ""
    git_diff_summary: str = ""
    token_estimate: int = 0


# ---------------------------------------------------------------------------
# Token estimation
# ---------------------------------------------------------------------------


def estimate_tokens(text: str) -> int:
    """Approximate token count using ~4 chars per token heuristic.

    This is a fast, dependency-free estimate that closely matches
    the average token density of GPT-4 / Claude tokenisers for
    typical source code.  Returns 0 for empty strings.
    """
    if not text:
        return 0
    return max(1, len(text) // 4)


# ---------------------------------------------------------------------------
# Structure tree builder
# ---------------------------------------------------------------------------


def build_structure_tree(files: list[str], *, max_depth: int = 2) -> str:
    """Build a simple directory tree from sorted file paths.

    Only shows directories up to *max_depth* levels and leaf-file
    counts, keeping the tree compact for LLM context.

    Parameters
    ----------
    files:
        Workspace-relative file paths (forward-slash separated).
    max_depth:
        Maximum directory nesting to display.  0 means root-level only.

    Returns
    -------
    str
        Indented text tree.
    """
    if not files:
        return ""

    # Normalise to forward slashes and sort
    normalised = sorted(f.replace("\\", "/") for f in files)

    # Build a nested dict of {dir_name: {sub_dir: ..., _files: [names]}}
    tree: dict = {}
    for fp in normalised:
        parts = fp.split("/")
        node = tree
        for i, part in enumerate(parts[:-1]):
            if i >= max_depth:
                # Flatten remaining into current node
                node.setdefault("_files", [])
                node["_files"].append("/".join(parts[i:]))
                break
            node = node.setdefault(part, {})
        else:
            # Leaf file
            node.setdefault("_files", [])
            node["_files"].append(parts[-1])

    lines: list[str] = []
    _render_tree(tree, lines, indent=0)
    return "\n".join(lines)


def _render_tree(node: dict, lines: list[str], indent: int) -> None:
    """Recursively render a tree dict into indented text lines."""
    prefix = "  " * indent
    # Render subdirectories first (sorted)
    dirs = sorted(k for k in node if k != "_files")
    for d in dirs:
        child = node[d]
        file_count = _count_files(child)
        lines.append(f"{prefix}{d}/ ({file_count} files)")
        _render_tree(child, lines, indent + 1)

    # Render files (capped at a few, with "…and N more" if many)
    file_list: list[str] = node.get("_files", [])
    max_shown = 5
    for f in file_list[:max_shown]:
        lines.append(f"{prefix}{f}")
    if len(file_list) > max_shown:
        lines.append(f"{prefix}…and {len(file_list) - max_shown} more")


def _count_files(node: dict) -> int:
    """Count total files in a tree node recursively."""
    total = len(node.get("_files", []))
    for k, v in node.items():
        if k != "_files" and isinstance(v, dict):
            total += _count_files(v)
    return total


# ---------------------------------------------------------------------------
# Repo summary builder
# ---------------------------------------------------------------------------


def build_repo_summary(
    file_count: int,
    languages: dict[str, int],
    files: list[str],
    *,
    max_depth: int = 2,
) -> RepoSummary:
    """Create a ``RepoSummary`` with a structure tree."""
    return RepoSummary(
        file_count=file_count,
        languages=languages,
        structure_tree=build_structure_tree(files, max_depth=max_depth),
    )


# ---------------------------------------------------------------------------
# Pack assembly
# ---------------------------------------------------------------------------


def assemble_pack(
    *,
    target_files: list[TargetFile] | None = None,
    dependency_snippets: list[DependencySnippet] | None = None,
    related_snippets: list[Snippet] | None = None,
    repo_summary: RepoSummary | None = None,
    diagnostics_summary: DiagnosticReport | None = None,
    test_output: str = "",
    git_diff_summary: str = "",
    budget_tokens: int = 0,
) -> ContextPack:
    """Assemble a context pack with token budget enforcement.

    Priority order (items are never dropped):
    1. repo_summary
    2. target_files
    3. diagnostics_summary

    Budget-trimmed (lowest-relevance items dropped first):
    4. dependency_snippets
    5. related_snippets (assumed sorted by relevance descending)
    6. test_output, git_diff_summary

    Parameters
    ----------
    budget_tokens:
        Maximum token estimate for the pack.  0 or negative means
        unlimited (no trimming).
    """
    target_files = target_files or []
    dependency_snippets = dependency_snippets or []
    related_snippets = list(related_snippets or [])
    repo_summary = repo_summary or RepoSummary()

    # Always-included sections
    core_text = pack_to_text(
        ContextPack(
            repo_summary=repo_summary,
            target_files=target_files,
            diagnostics_summary=diagnostics_summary,
        )
    )
    core_tokens = estimate_tokens(core_text)

    # Start with unlimited budget if 0 or negative
    if budget_tokens <= 0:
        pack = ContextPack(
            repo_summary=repo_summary,
            target_files=target_files,
            dependency_snippets=dependency_snippets,
            related_snippets=related_snippets,
            diagnostics_summary=diagnostics_summary,
            test_output=test_output,
            git_diff_summary=git_diff_summary,
        )
        full_text = pack_to_text(pack)
        return pack.model_copy(update={"token_estimate": estimate_tokens(full_text)})

    # Budget-aware assembly
    remaining = budget_tokens - core_tokens

    # Add dependency snippets (high priority)
    kept_deps: list[DependencySnippet] = []
    for ds in dependency_snippets:
        dep_tokens = estimate_tokens(ds.content)
        if dep_tokens <= remaining:
            kept_deps.append(ds)
            remaining -= dep_tokens

    # Add related snippets (already sorted by relevance, trim from end)
    kept_related: list[Snippet] = []
    for rs in related_snippets:
        rs_tokens = estimate_tokens(rs.content)
        if rs_tokens <= remaining:
            kept_related.append(rs)
            remaining -= rs_tokens

    # Add test output
    kept_test = ""
    if test_output:
        test_tokens = estimate_tokens(test_output)
        if test_tokens <= remaining:
            kept_test = test_output
            remaining -= test_tokens

    # Add git diff summary
    kept_diff = ""
    if git_diff_summary:
        diff_tokens = estimate_tokens(git_diff_summary)
        if diff_tokens <= remaining:
            kept_diff = git_diff_summary
            remaining -= diff_tokens

    pack = ContextPack(
        repo_summary=repo_summary,
        target_files=target_files,
        dependency_snippets=kept_deps,
        related_snippets=kept_related,
        diagnostics_summary=diagnostics_summary,
        test_output=kept_test,
        git_diff_summary=kept_diff,
    )
    full_text = pack_to_text(pack)
    return pack.model_copy(update={"token_estimate": estimate_tokens(full_text)})


# ---------------------------------------------------------------------------
# Text serialisation
# ---------------------------------------------------------------------------


def pack_to_text(pack: ContextPack) -> str:
    """Serialise a ``ContextPack`` to a human/LLM-readable text block.

    The format is designed for prompt injection — clear section headers,
    fenced code blocks, and concise summaries.
    """
    sections: list[str] = []

    # Repo summary
    rs = pack.repo_summary
    if rs.file_count or rs.structure_tree:
        lines = [f"## Repository ({rs.file_count} files)"]
        if rs.languages:
            lang_str = ", ".join(
                f"{lang}: {count}" for lang, count in sorted(rs.languages.items())
            )
            lines.append(f"Languages: {lang_str}")
        if rs.structure_tree:
            lines.append("")
            lines.append(rs.structure_tree)
        sections.append("\n".join(lines))

    # Target files
    for tf in pack.target_files:
        header = f"## Target: {tf.path}"
        section_lines = [header, f"```\n{tf.content}\n```"]
        if tf.diagnostics:
            section_lines.append(f"Diagnostics ({len(tf.diagnostics)}):")
            for d in tf.diagnostics:
                section_lines.append(f"  L{d.line}: [{d.severity}] {d.message}")
        sections.append("\n".join(section_lines))

    # Dependency snippets
    if pack.dependency_snippets:
        dep_lines = ["## Dependencies"]
        for ds in pack.dependency_snippets:
            dep_lines.append(f"### {ds.path} ({ds.why})")
            dep_lines.append(f"```\n{ds.content}\n```")
        sections.append("\n".join(dep_lines))

    # Related snippets
    if pack.related_snippets:
        rel_lines = ["## Related"]
        for rs_item in pack.related_snippets:
            rel_lines.append(
                f"### {rs_item.path} (L{rs_item.start_line}-{rs_item.end_line})"
            )
            rel_lines.append(f"```\n{rs_item.content}\n```")
        sections.append("\n".join(rel_lines))

    # Diagnostics summary
    if pack.diagnostics_summary:
        ds_item = pack.diagnostics_summary
        diag_lines = [
            f"## Diagnostics (E:{ds_item.error_count} W:{ds_item.warning_count})"
        ]
        for fpath, diags in ds_item.files.items():
            for d in diags:
                diag_lines.append(f"  {fpath}:{d.line} [{d.severity}] {d.message}")
        sections.append("\n".join(diag_lines))

    # Test output
    if pack.test_output:
        sections.append(f"## Test Output\n```\n{pack.test_output}\n```")

    # Git diff
    if pack.git_diff_summary:
        sections.append(f"## Git Diff\n{pack.git_diff_summary}")

    return "\n\n".join(sections)


__all__ = [
    "ContextPack",
    "DependencySnippet",
    "RepoSummary",
    "TargetFile",
    "assemble_pack",
    "build_context_pack_for_file",
    "build_repo_summary",
    "build_structure_tree",
    "estimate_tokens",
    "pack_to_text",
]


# ---------------------------------------------------------------------------
# Contract relevance map for context packs
# ---------------------------------------------------------------------------

# Maps file category -> relevant contract types.
# Used by build_context_pack_for_file to select only the contracts
# that matter for each generated file.
_FILE_CONTRACT_MAP: dict[str, list[str]] = {
    "python_backend": ["blueprint", "schema", "stack", "boundaries"],
    "python_test": ["blueprint", "schema", "stack"],
    "frontend": ["ui", "blueprint", "stack"],
    "frontend_test": ["ui", "blueprint"],
    "migration": ["schema"],
    "config": ["stack", "boundaries"],
    "markdown": ["blueprint"],
}


def _classify_file(path: str) -> str:
    """Classify a file path into a category for contract selection."""
    p = path.lower().replace("\\", "/")
    ext = PurePosixPath(p).suffix

    is_test = (
        "test_" in p
        or "_test." in p
        or ".test." in p
        or ".spec." in p
        or "/__tests__/" in p
        or "/tests/" in p
    )

    if ext == ".py":
        if is_test:
            return "python_test"
        return "python_backend"
    elif ext in (".ts", ".tsx", ".js", ".jsx"):
        if is_test:
            return "frontend_test"
        return "frontend"
    elif ext == ".sql":
        return "migration"
    elif ext in (".md", ".markdown"):
        return "markdown"
    elif ext in (".toml", ".yaml", ".yml", ".json", ".cfg", ".ini", ".env"):
        return "config"
    else:
        return "python_backend"  # default


def build_context_pack_for_file(
    *,
    file_path: str,
    file_purpose: str,
    contracts: list[dict],
    context_file_contents: dict[str, str],
    snapshot_symbol_table: dict[str, str] | None = None,
    snapshot_dep_graph: dict[str, tuple[str, ...]] | None = None,
    budget_tokens: int = 30_000,
) -> ContextPack:
    """Build a token-budgeted context pack for a single file generation call.

    Selects only the contracts relevant to the file type, includes
    dependency file contents, and injects relevant symbols from the
    workspace snapshot.

    Parameters
    ----------
    file_path : str
        The relative path of the file to be generated.
    file_purpose : str
        Brief description of what this file does.
    contracts : list[dict]
        All project contracts (``[{"contract_type": str, "content": str}]``).
    context_file_contents : dict[str, str]
        ``{path: content}`` for files this one depends on
        (from the manifest ``context_files`` or ``depends_on``).
    snapshot_symbol_table : dict[str, str] | None
        ``{dotted_path: kind}`` symbol table from the workspace snapshot.
    snapshot_dep_graph : dict[str, tuple[str, ...]] | None
        ``{file: (modules, ...)}`` dependency graph from snapshot.
    budget_tokens : int
        Maximum total tokens for the pack (default 30K).

    Returns
    -------
    ContextPack
        A token-budgeted pack ready for ``pack_to_text()``.
    """
    category = _classify_file(file_path)
    relevant_types = _FILE_CONTRACT_MAP.get(category, ["blueprint", "stack"])

    # Build repo summary section with symbol highlights
    summary_parts: list[str] = [f"File: {file_path}", f"Purpose: {file_purpose}"]

    if snapshot_symbol_table:
        # Find symbols from modules this file is likely to import
        related_symbols: list[str] = []
        if snapshot_dep_graph:
            # Get modules imported by files this file depends on
            for dep_path in context_file_contents:
                dep_imports = snapshot_dep_graph.get(dep_path, ())
                for mod in dep_imports:
                    for sym, kind in snapshot_symbol_table.items():
                        if sym.startswith(mod.replace("/", ".").removesuffix(".py") + "."):
                            related_symbols.append(f"  {sym} ({kind})")
                            if len(related_symbols) >= 30:
                                break
                    if len(related_symbols) >= 30:
                        break

        if related_symbols:
            summary_parts.append("\nAvailable symbols:")
            summary_parts.extend(related_symbols[:30])

    repo_summary = RepoSummary(
        file_count=0,
        languages={},
        structure_tree="\n".join(summary_parts),
    )

    # Select relevant contracts as target files
    target_files: list[TargetFile] = []
    for c in contracts:
        ct = c.get("contract_type", "")
        if ct in relevant_types:
            target_files.append(TargetFile(
                path=f"contracts/{ct}",
                content=c.get("content", ""),
            ))

    # Build dependency snippets from context_file_contents
    dep_snippets: list[DependencySnippet] = []
    for dep_path, dep_content in context_file_contents.items():
        dep_snippets.append(DependencySnippet(
            path=dep_path,
            content=dep_content,
            why="depends_on" if dep_path != file_path else "self",
        ))

    return assemble_pack(
        target_files=target_files,
        dependency_snippets=dep_snippets,
        repo_summary=repo_summary,
        budget_tokens=budget_tokens,
    )
