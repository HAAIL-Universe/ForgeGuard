"""IDE runtime contracts — Pydantic models for tool requests and responses.

Every tool in the IDE runtime communicates through these models.
Responses are always structured JSON (never raw strings).
All models are frozen (immutable after creation).

Also contains the Task DAG models used for dependency-aware build
execution (Phase 41).
"""

from __future__ import annotations

import enum
from datetime import datetime, timezone
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
# Task DAG — dependency-aware build execution (Phase 41)
# ---------------------------------------------------------------------------


class TaskStatus(str, enum.Enum):
    """Lifecycle states for a build task."""

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    BLOCKED = "blocked"
    SKIPPED = "skipped"


class TaskNode:
    """A single task in the build DAG.

    Mutable — status transitions happen during the build loop.
    """

    __slots__ = (
        "id", "title", "phase", "status", "depends_on", "blocks",
        "file_path", "estimated_tokens", "actual_tokens",
        "started_at", "completed_at", "error", "retry_count",
    )

    def __init__(
        self,
        *,
        id: str,
        title: str,
        phase: str = "",
        status: TaskStatus = TaskStatus.PENDING,
        depends_on: list[str] | None = None,
        blocks: list[str] | None = None,
        file_path: str | None = None,
        estimated_tokens: int = 0,
        actual_tokens: int = 0,
        started_at: datetime | None = None,
        completed_at: datetime | None = None,
        error: str | None = None,
        retry_count: int = 0,
    ) -> None:
        self.id = id
        self.title = title
        self.phase = phase
        self.status = status
        self.depends_on: list[str] = depends_on or []
        self.blocks: list[str] = blocks or []
        self.file_path = file_path
        self.estimated_tokens = estimated_tokens
        self.actual_tokens = actual_tokens
        self.started_at = started_at
        self.completed_at = completed_at
        self.error = error
        self.retry_count = retry_count

    # -- serialisation -------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Serialise for WS events / persistence."""
        return {
            "id": self.id,
            "title": self.title,
            "phase": self.phase,
            "status": self.status.value,
            "depends_on": self.depends_on,
            "blocks": self.blocks,
            "file_path": self.file_path,
            "estimated_tokens": self.estimated_tokens,
            "actual_tokens": self.actual_tokens,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "error": self.error,
            "retry_count": self.retry_count,
        }

    def __repr__(self) -> str:
        return f"TaskNode(id={self.id!r}, status={self.status.value!r}, file={self.file_path!r})"


class DAGProgress(BaseModel):
    """Aggregate progress snapshot for the task DAG."""

    model_config = ConfigDict(frozen=True)

    total: int = 0
    completed: int = 0
    failed: int = 0
    blocked: int = 0
    in_progress: int = 0
    pending: int = 0
    skipped: int = 0
    percentage: float = 0.0
    estimated_remaining_tokens: int = 0
    estimated_remaining_cost_usd: float = 0.0


class CyclicDependencyError(ValueError):
    """Raised when the task DAG contains a cycle."""


class TaskDAG:
    """Directed acyclic graph of build tasks.

    The DAG is the build loop's "program counter" — it tracks what
    has been done, what's in progress, and what's blocked.

    Nodes are mutable :class:`TaskNode` instances; the DAG enforces
    transition rules and dependency ordering.
    """

    __slots__ = ("nodes",)

    def __init__(self) -> None:
        self.nodes: dict[str, TaskNode] = {}

    # -- construction --------------------------------------------------------

    def add_task(self, node: TaskNode) -> None:
        """Add a task to the DAG."""
        self.nodes[node.id] = node

    def wire_blocks(self) -> None:
        """Compute the reverse ``blocks`` edges from ``depends_on``.

        Call once after all tasks have been added.
        """
        # Reset blocks
        for n in self.nodes.values():
            n.blocks = []
        for n in self.nodes.values():
            for dep_id in n.depends_on:
                dep = self.nodes.get(dep_id)
                if dep is not None and n.id not in dep.blocks:
                    dep.blocks.append(n.id)

    @classmethod
    def from_manifest(
        cls,
        manifest: list[dict[str, Any]],
        *,
        phase_label: str = "",
    ) -> "TaskDAG":
        """Factory: build a DAG from a planner file manifest.

        Each entry in *manifest* is a dict with at least ``path``
        and optionally ``depends_on``, ``purpose``, ``estimated_lines``.
        """
        dag = cls()
        for i, entry in enumerate(manifest):
            path = entry["path"]
            task_id = f"p{phase_label}_t{i}" if phase_label else f"t{i}"
            est_lines = entry.get("estimated_lines", 100)
            # Rough token estimate: ~4 tokens per line of output
            est_tokens = est_lines * 4

            # Map depends_on file paths → task IDs
            dep_paths = entry.get("depends_on", [])
            dep_ids: list[str] = []
            for dp in dep_paths:
                for j, other in enumerate(manifest):
                    if other["path"] == dp:
                        other_id = f"p{phase_label}_t{j}" if phase_label else f"t{j}"
                        dep_ids.append(other_id)
                        break

            node = TaskNode(
                id=task_id,
                title=entry.get("purpose", f"Generate {path}"),
                phase=phase_label,
                file_path=path,
                depends_on=dep_ids,
                estimated_tokens=est_tokens,
            )
            dag.add_task(node)

        dag.wire_blocks()
        dag._detect_cycles()
        return dag

    # -- queries -------------------------------------------------------------

    def get_ready_tasks(self) -> list[TaskNode]:
        """Return tasks whose dependencies are all completed/skipped."""
        ready: list[TaskNode] = []
        for n in self.nodes.values():
            if n.status != TaskStatus.PENDING:
                continue
            if all(
                self.nodes[d].status in (TaskStatus.COMPLETED, TaskStatus.SKIPPED)
                for d in n.depends_on
                if d in self.nodes
            ):
                ready.append(n)
        return ready

    def get_blocked_by(self, task_id: str) -> list[TaskNode]:
        """Return all tasks transitively blocked by *task_id*."""
        result: list[TaskNode] = []
        visited: set[str] = set()
        stack = list(self.nodes[task_id].blocks)
        while stack:
            tid = stack.pop()
            if tid in visited:
                continue
            visited.add(tid)
            node = self.nodes.get(tid)
            if node is not None:
                result.append(node)
                stack.extend(node.blocks)
        return result

    def topological_order(self) -> list[TaskNode]:
        """Return nodes in dependency-first order.

        Raises :class:`CyclicDependencyError` if the graph has a cycle.
        """
        visited: set[str] = set()
        temp: set[str] = set()
        order: list[TaskNode] = []

        def _visit(nid: str) -> None:
            if nid in temp:
                raise CyclicDependencyError(
                    f"Cycle detected involving task {nid!r}"
                )
            if nid in visited:
                return
            temp.add(nid)
            node = self.nodes[nid]
            for dep in node.depends_on:
                if dep in self.nodes:
                    _visit(dep)
            temp.discard(nid)
            visited.add(nid)
            order.append(node)

        for nid in self.nodes:
            if nid not in visited:
                _visit(nid)
        return order

    # -- state transitions ---------------------------------------------------

    def mark_in_progress(self, task_id: str) -> None:
        """Transition a task to in-progress."""
        node = self.nodes[task_id]
        node.status = TaskStatus.IN_PROGRESS
        node.started_at = datetime.now(timezone.utc)

    def mark_completed(self, task_id: str, actual_tokens: int = 0) -> None:
        """Transition a task to completed."""
        node = self.nodes[task_id]
        node.status = TaskStatus.COMPLETED
        node.actual_tokens = actual_tokens
        node.completed_at = datetime.now(timezone.utc)

    def mark_failed(self, task_id: str, error: str) -> None:
        """Transition a task to failed and cascade to dependents."""
        node = self.nodes[task_id]
        node.status = TaskStatus.FAILED
        node.error = error
        node.completed_at = datetime.now(timezone.utc)
        # Cascade: block all downstream tasks
        for blocked in self.get_blocked_by(task_id):
            if blocked.status == TaskStatus.PENDING:
                blocked.status = TaskStatus.BLOCKED
                blocked.error = f"Blocked by failed task {task_id}"

    def mark_skipped(self, task_id: str) -> None:
        """Transition a task to skipped (e.g., file already exists)."""
        node = self.nodes[task_id]
        node.status = TaskStatus.SKIPPED
        node.completed_at = datetime.now(timezone.utc)

    def unblock_downstream(self, task_id: str) -> list[TaskNode]:
        """After a failed task is recovered, unblock its dependents.

        Returns the list of tasks that were unblocked.
        """
        unblocked: list[TaskNode] = []
        for blocked in self.get_blocked_by(task_id):
            if blocked.status != TaskStatus.BLOCKED:
                continue
            # Check if ALL deps are now completed/skipped
            all_met = all(
                self.nodes[d].status in (TaskStatus.COMPLETED, TaskStatus.SKIPPED)
                for d in blocked.depends_on
                if d in self.nodes
            )
            if all_met:
                blocked.status = TaskStatus.PENDING
                blocked.error = None
                unblocked.append(blocked)
        return unblocked

    # -- progress ------------------------------------------------------------

    def get_progress(self) -> DAGProgress:
        """Compute aggregate progress."""
        total = len(self.nodes)
        counts: dict[TaskStatus, int] = {s: 0 for s in TaskStatus}
        est_remaining = 0
        for n in self.nodes.values():
            counts[n.status] = counts.get(n.status, 0) + 1
            if n.status in (TaskStatus.PENDING, TaskStatus.IN_PROGRESS, TaskStatus.BLOCKED):
                est_remaining += n.estimated_tokens

        done = counts[TaskStatus.COMPLETED] + counts[TaskStatus.SKIPPED]
        pct = (done / total * 100) if total > 0 else 0.0
        # Rough cost estimate: $3 per 1M output tokens (Sonnet)
        est_cost = est_remaining * 3.0 / 1_000_000

        return DAGProgress(
            total=total,
            completed=counts[TaskStatus.COMPLETED],
            failed=counts[TaskStatus.FAILED],
            blocked=counts[TaskStatus.BLOCKED],
            in_progress=counts[TaskStatus.IN_PROGRESS],
            pending=counts[TaskStatus.PENDING],
            skipped=counts[TaskStatus.SKIPPED],
            percentage=round(pct, 1),
            estimated_remaining_tokens=est_remaining,
            estimated_remaining_cost_usd=round(est_cost, 4),
        )

    # -- serialisation -------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Serialise the full DAG for WS events / persistence."""
        return {
            "nodes": {nid: n.to_dict() for nid, n in self.nodes.items()},
            "progress": self.get_progress().model_dump(),
        }

    # -- internals -----------------------------------------------------------

    def _detect_cycles(self) -> None:
        """Raise :class:`CyclicDependencyError` if the graph has a cycle."""
        try:
            self.topological_order()
        except CyclicDependencyError:
            raise

    def __repr__(self) -> str:
        p = self.get_progress()
        return (
            f"TaskDAG(tasks={p.total}, done={p.completed}, "
            f"failed={p.failed}, blocked={p.blocked})"
        )


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
