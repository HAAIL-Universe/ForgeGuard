"""Relevance scoring — rank workspace files by relation to a target.

Provides four independent scoring factors (import graph distance,
directory proximity, filename similarity, and recency) plus a
``find_related`` orchestrator that aggregates scores and returns
ranked results.

All functions are pure — they operate on in-memory data, never touch
the filesystem or spawn subprocesses.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import PurePosixPath

from pydantic import BaseModel, ConfigDict, Field

from forge_ide.file_index import FileMetadata

# ---------------------------------------------------------------------------
# Model
# ---------------------------------------------------------------------------


class RelatedFile(BaseModel):
    """A workspace file with an aggregate relevance score."""

    model_config = ConfigDict(frozen=True)

    path: str
    score: float = Field(ge=0.0)
    reasons: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Individual scoring functions
# ---------------------------------------------------------------------------


def score_import_graph(
    target: str,
    candidate: str,
    imports: dict[str, list[str]],
    importers: dict[str, list[str]],
) -> float:
    """Score *candidate* by import-graph proximity to *target*.

    - Direct import (target imports candidate): 1.0
    - Reverse import (candidate imports target): 0.8
    - Two-hop transitive import: 0.5
    - No relation: 0.0
    """
    if target == candidate:
        return 0.0

    target_imports = set(imports.get(target, []))
    candidate_imports = set(imports.get(candidate, []))

    # Direct import — target directly uses candidate
    if candidate in target_imports:
        return 1.0

    # Reverse import — candidate directly uses target
    if target in candidate_imports:
        return 0.8

    # Transitive (2-hop): target imports X, X imports candidate
    for mid in target_imports:
        mid_imports = set(imports.get(mid, []))
        if candidate in mid_imports:
            return 0.5

    return 0.0


def score_directory_proximity(target: str, candidate: str) -> float:
    """Score *candidate* by directory proximity to *target*.

    - Same directory: 0.3
    - Parent / child (one level): 0.2
    - Grandparent / grandchild (two levels): 0.1
    - Further away: 0.0

    Paths are treated as POSIX-style relative paths (forward slashes).
    """
    t_parts = PurePosixPath(target.replace("\\", "/")).parent.parts
    c_parts = PurePosixPath(candidate.replace("\\", "/")).parent.parts

    if t_parts == c_parts:
        return 0.3

    # Measure directory distance.
    # Find length of the common prefix, then compute the deviation.
    common = 0
    for a, b in zip(t_parts, c_parts):
        if a == b:
            common += 1
        else:
            break

    distance = (len(t_parts) - common) + (len(c_parts) - common)

    if distance == 1:
        return 0.2
    if distance == 2:
        return 0.1
    return 0.0


def score_name_similarity(target: str, candidate: str) -> float:
    """Score *candidate* by filename similarity to *target*.

    - Test ↔ implementation mirror (``test_foo.py`` ↔ ``foo.py``): 0.4
    - Shared stem prefix (first 4+ chars match): 0.2
    - Otherwise: 0.0
    """
    t_stem = PurePosixPath(target.replace("\\", "/")).stem
    c_stem = PurePosixPath(candidate.replace("\\", "/")).stem

    # Test/impl mirror detection
    if _is_test_impl_pair(t_stem, c_stem):
        return 0.4

    # Shared prefix (at least 4 characters)
    prefix_len = 0
    for a, b in zip(t_stem, c_stem):
        if a == b:
            prefix_len += 1
        else:
            break

    if prefix_len >= 4 and t_stem != c_stem:
        return 0.2

    return 0.0


def score_recency(
    target_mtime: datetime | None,
    candidate_mtime: datetime | None,
    *,
    window_hours: float = 24.0,
) -> float:
    """Score *candidate* by recency relative to *target*.

    If both have modification times within *window_hours* of each other,
    return up to 0.3 scaled linearly (closer = higher).  Otherwise 0.0.
    """
    if target_mtime is None or candidate_mtime is None:
        return 0.0
    if window_hours <= 0:
        return 0.0

    # Normalise to UTC-aware
    t = target_mtime if target_mtime.tzinfo else target_mtime.replace(tzinfo=timezone.utc)
    c = candidate_mtime if candidate_mtime.tzinfo else candidate_mtime.replace(tzinfo=timezone.utc)

    delta_secs = abs((t - c).total_seconds())
    window_secs = window_hours * 3600.0

    if delta_secs >= window_secs:
        return 0.0

    return round(0.3 * (1.0 - delta_secs / window_secs), 4)


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


def find_related(
    target_path: str,
    all_files: list[FileMetadata],
    imports: dict[str, list[str]],
    importers: dict[str, list[str]],
    *,
    max_results: int = 15,
) -> list[RelatedFile]:
    """Aggregate all scoring factors and return ranked related files.

    Parameters
    ----------
    target_path:
        Workspace-relative path of the target file.
    all_files:
        Every indexed file in the workspace.
    imports:
        Forward import graph: ``{file: [imported_file, ...]}``.
    importers:
        Reverse import graph: ``{file: [files_that_import_it, ...]}``.
    max_results:
        Maximum number of results to return.

    Returns
    -------
    list[RelatedFile]
        Sorted descending by score, trimmed to *max_results*.
        Files with score 0 are excluded.
    """
    target_meta: FileMetadata | None = None
    for fm in all_files:
        if fm.path == target_path:
            target_meta = fm
            break

    results: list[RelatedFile] = []

    for fm in all_files:
        if fm.path == target_path:
            continue

        reasons: list[str] = []
        total = 0.0

        ig = score_import_graph(target_path, fm.path, imports, importers)
        if ig > 0:
            total += ig
            if ig >= 1.0:
                reasons.append("direct import")
            elif ig >= 0.8:
                reasons.append("reverse import")
            else:
                reasons.append("transitive import")

        dp = score_directory_proximity(target_path, fm.path)
        if dp > 0:
            total += dp
            reasons.append("directory proximity")

        ns = score_name_similarity(target_path, fm.path)
        if ns > 0:
            total += ns
            reasons.append("name similarity")

        target_mtime = target_meta.last_modified if target_meta else None
        rc = score_recency(target_mtime, fm.last_modified)
        if rc > 0:
            total += rc
            reasons.append("recent modification")

        if total > 0:
            results.append(
                RelatedFile(path=fm.path, score=round(total, 4), reasons=reasons)
            )

    results.sort(key=lambda r: r.score, reverse=True)
    return results[:max_results]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _is_test_impl_pair(stem_a: str, stem_b: str) -> bool:
    """Return True if *stem_a* and *stem_b* are a test↔impl pair.

    Recognises ``test_foo`` ↔ ``foo`` and ``foo_test`` ↔ ``foo``.
    """
    for a, b in [(stem_a, stem_b), (stem_b, stem_a)]:
        if a.startswith("test_") and a[5:] == b:
            return True
        if a.endswith("_test") and a[:-5] == b:
            return True
    return False


__all__ = [
    "RelatedFile",
    "find_related",
    "score_directory_proximity",
    "score_import_graph",
    "score_name_similarity",
    "score_recency",
]
