"""Session Journal — persistent event log for multi-phase builds.

Captures every significant build event (task completion, test runs,
file writes, errors, phase transitions) so the builder can resume from
any point without re-discovering what it has already done.

The journal produces a *structured summary* optimised for LLM context
injection — replacing lossy conversation summarisation with a dense,
accurate state document.

Phase 43 of the ForgeIDE cognitive architecture.
"""

from __future__ import annotations

import hashlib
import json
import textwrap
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any


# ---------------------------------------------------------------------------
# Journal entry types
# ---------------------------------------------------------------------------

ENTRY_TYPES = frozenset({
    "phase_start",
    "phase_complete",
    "task_completed",
    "task_failed",
    "test_run",
    "file_written",
    "error",
    "recon",
    "invariant_set",
    "invariant_violated",
    "context_compacted",
    "checkpoint_saved",
    "custom",
})


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class JournalEntry:
    """A single recorded event in the session journal."""

    timestamp: str  # ISO-8601; str for easy JSON round-trip
    event_type: str
    phase: str
    task_id: str | None
    detail: str
    metadata: dict[str, Any] = field(default_factory=dict)

    # -- helpers ----------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> JournalEntry:
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


@dataclass
class InvariantResult:
    """Result of checking a tracked invariant against a current value."""

    key: str
    expected: Any
    actual: Any
    passed: bool


@dataclass
class JournalCheckpoint:
    """Serialisable snapshot for DB persistence / build resume."""

    build_id: str
    phase: str
    task_dag_state: dict[str, Any] = field(default_factory=dict)
    invariants: dict[str, Any] = field(default_factory=dict)
    files_written: list[str] = field(default_factory=list)
    snapshot_hash: str = ""
    compressed_history: str = ""
    created_at: str = ""

    def to_json(self) -> str:
        return json.dumps(asdict(self), default=str)

    @classmethod
    def from_json(cls, raw: str) -> JournalCheckpoint:
        data = json.loads(raw)
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


# ---------------------------------------------------------------------------
# Session Journal
# ---------------------------------------------------------------------------


class SessionJournal:
    """Persistent event log for a single build session.

    Records all significant build events and produces structured
    summaries optimised for LLM context injection.
    """

    def __init__(self, build_id: str, *, phase: str = "") -> None:
        self.build_id = build_id
        self.current_phase = phase
        self.entries: list[JournalEntry] = []
        self.invariants: dict[str, Any] = {}
        self.files_written: list[str] = []
        self._phase_stats: dict[str, dict[str, Any]] = {}
        # Track tokens per phase
        self._phase_tokens: dict[str, int] = {}

    # -- recording --------------------------------------------------------

    def record(
        self,
        event_type: str,
        detail: str,
        *,
        metadata: dict[str, Any] | None = None,
        phase: str | None = None,
        task_id: str | None = None,
    ) -> JournalEntry:
        """Append a new event to the journal.

        Returns the created entry.
        """
        effective_phase = phase if phase is not None else self.current_phase
        entry = JournalEntry(
            timestamp=datetime.now(timezone.utc).isoformat(),
            event_type=event_type,
            phase=effective_phase,
            task_id=task_id,
            detail=detail,
            metadata=metadata or {},
        )
        self.entries.append(entry)

        # Auto-track file writes
        if event_type == "file_written":
            path = (metadata or {}).get("file_path", "") or (metadata or {}).get("path", "")
            if path and path not in self.files_written:
                self.files_written.append(path)

        # Auto-track phase stats
        if event_type == "phase_start":
            self._phase_stats[effective_phase] = {
                "started_at": entry.timestamp,
                "tasks_total": (metadata or {}).get("task_count", 0),
                "tasks_completed": 0,
                "tokens": 0,
            }
        elif event_type == "task_completed" and effective_phase in self._phase_stats:
            self._phase_stats[effective_phase]["tasks_completed"] += 1
            tokens = (metadata or {}).get("tokens_in", 0) + (metadata or {}).get("tokens_out", 0)
            self._phase_stats[effective_phase]["tokens"] += tokens
            self._phase_tokens[effective_phase] = self._phase_stats[effective_phase]["tokens"]
        elif event_type == "phase_complete" and effective_phase in self._phase_stats:
            self._phase_stats[effective_phase]["completed_at"] = entry.timestamp
            if metadata is not None and metadata.get("tokens_total"):
                self._phase_stats[effective_phase]["tokens"] = metadata["tokens_total"]
                self._phase_tokens[effective_phase] = metadata["tokens_total"]

        return entry

    def set_phase(self, phase: str) -> None:
        """Update the current phase label."""
        self.current_phase = phase

    # -- invariant tracking -----------------------------------------------

    def set_invariant(self, key: str, value: Any) -> None:
        """Set or update a tracked invariant."""
        self.invariants[key] = value
        self.record(
            "invariant_set",
            f"{key}: {value}",
            metadata={"key": key, "value": value},
        )

    def check_invariant(self, key: str, current: Any) -> InvariantResult:
        """Compare *current* against the tracked invariant value.

        If the key is not tracked, the check passes vacuously.
        """
        expected = self.invariants.get(key)
        if expected is None:
            return InvariantResult(key=key, expected=None, actual=current, passed=True)

        passed = current == expected
        if not passed:
            self.record(
                "invariant_violated",
                f"{key}: expected {expected}, got {current}",
                metadata={"key": key, "expected": expected, "actual": current},
            )
        return InvariantResult(key=key, expected=expected, actual=current, passed=passed)

    # -- summary / context injection --------------------------------------

    def get_summary(self, max_tokens: int = 4000) -> str:
        """Produce a structured summary optimised for LLM context injection.

        The summary stays within *max_tokens* (approximated at 4 chars/token)
        by trimming older entries while preserving the most recent events,
        completed phases, invariants, and files-written manifest.
        """
        max_chars = max_tokens * 4  # rough estimate

        parts: list[str] = []
        parts.append(f"=== SESSION JOURNAL (Build {self.build_id}, Phase {self.current_phase}) ===\n")

        # -- Completed phases --
        completed_phases = self._get_completed_phases()
        if completed_phases:
            parts.append("## Completed Phases")
            for ph_name, stats in completed_phases:
                tc = stats.get("tasks_completed", "?")
                tt = stats.get("tasks_total", "?")
                tokens = stats.get("tokens", 0)
                parts.append(f"- {ph_name}: {tc}/{tt} tasks ✓ — {tokens:,} tokens")
            parts.append("")

        # -- Current phase --
        in_progress = self._get_current_phase_summary()
        if in_progress:
            parts.append(in_progress)
            parts.append("")

        # -- Invariants --
        if self.invariants:
            parts.append("## Invariants")
            for key, value in self.invariants.items():
                parts.append(f"- {key}: {value}")
            parts.append("")

        # -- Files written (this phase) --
        current_phase_files = [
            e.metadata.get("file_path", "") or e.metadata.get("path", "")
            for e in self.entries
            if e.event_type == "file_written" and e.phase == self.current_phase
        ]
        if current_phase_files:
            parts.append("## Files Written This Phase")
            for fp in current_phase_files:
                parts.append(f"- {fp}")
            parts.append("")
        elif self.files_written:
            parts.append(f"## Files Written (total: {len(self.files_written)})")
            # Show last 20
            for fp in self.files_written[-20:]:
                parts.append(f"- {fp}")
            if len(self.files_written) > 20:
                parts.append(f"- ... ({len(self.files_written) - 20} more)")
            parts.append("")

        # -- Recent events --
        recent = self.entries[-10:] if len(self.entries) > 10 else self.entries
        if recent:
            parts.append("## Recent Events (last %d)" % len(recent))
            for e in reversed(recent):
                ts = e.timestamp
                # Show only HH:MM:SS from ISO timestamp
                if "T" in ts:
                    ts = ts.split("T")[1][:8]
                parts.append(f"- [{ts}] {e.detail}")
            parts.append("")

        parts.append("=== END JOURNAL ===")

        text = "\n".join(parts)

        # Trim if over budget — keep header, invariants, and trim from middle
        if len(text) > max_chars:
            text = self._trim_summary(text, max_chars)

        return text

    def to_context_block(self) -> str:
        """Format journal as a context block for API prompt injection.

        Alias for ``get_summary()`` with default budget.
        """
        return self.get_summary()

    # -- checkpoint / persistence -----------------------------------------

    def get_checkpoint(
        self,
        *,
        dag_state: dict[str, Any] | None = None,
        snapshot_hash: str = "",
    ) -> JournalCheckpoint:
        """Create a serialisable checkpoint of the current journal state."""
        return JournalCheckpoint(
            build_id=self.build_id,
            phase=self.current_phase,
            task_dag_state=dag_state or {},
            invariants=dict(self.invariants),
            files_written=list(self.files_written),
            snapshot_hash=snapshot_hash,
            compressed_history=self.get_summary(max_tokens=4000),
            created_at=datetime.now(timezone.utc).isoformat(),
        )

    @classmethod
    def restore_from_checkpoint(cls, checkpoint: JournalCheckpoint) -> SessionJournal:
        """Restore a journal from a previously saved checkpoint.

        Note: individual entries are *not* preserved — only the compressed
        summary, invariants, and file manifest survive the checkpoint.
        This is intentional: checkpoints are lossy-by-design, trading
        entry-level detail for bounded size.
        """
        journal = cls(checkpoint.build_id, phase=checkpoint.phase)
        journal.invariants = dict(checkpoint.invariants)
        journal.files_written = list(checkpoint.files_written)

        # Record a synthetic "resumed" entry so the journal knows it was restored
        journal.record(
            "custom",
            f"Resumed from checkpoint (phase {checkpoint.phase})",
            metadata={
                "checkpoint_created_at": checkpoint.created_at,
                "snapshot_hash": checkpoint.snapshot_hash,
                "files_restored": len(checkpoint.files_written),
            },
        )
        return journal

    # -- helpers ----------------------------------------------------------

    def _get_completed_phases(self) -> list[tuple[str, dict[str, Any]]]:
        """Return list of (phase_name, stats) for all completed phases."""
        completed = []
        for ph, stats in self._phase_stats.items():
            if "completed_at" in stats:
                completed.append((ph, stats))
        return completed

    def _get_current_phase_summary(self) -> str:
        """Build a summary line for the in-progress phase."""
        stats = self._phase_stats.get(self.current_phase)
        if not stats:
            return f"## Current Phase: {self.current_phase}"

        completed = stats.get("tasks_completed", 0)
        total = stats.get("tasks_total", 0)
        pending = max(0, total - completed)

        lines = [f"## Current Phase: {self.current_phase}"]
        lines.append(f"- Tasks: {completed}/{total} completed, {pending} pending")
        return "\n".join(lines)

    def _trim_summary(self, text: str, max_chars: int) -> str:
        """Trim summary to fit within *max_chars*, keeping structure."""
        lines = text.split("\n")
        # Always keep first 3 and last 3 lines (header + footer)
        head = lines[:3]
        tail = lines[-3:]
        middle = lines[3:-3]

        budget = max_chars - sum(len(l) + 1 for l in head + tail) - 40
        kept: list[str] = []
        used = 0
        for line in middle:
            if used + len(line) + 1 > budget:
                kept.append(f"... ({len(middle) - len(kept)} lines trimmed) ...")
                break
            kept.append(line)
            used += len(line) + 1

        return "\n".join(head + kept + tail)

    def __len__(self) -> int:
        return len(self.entries)

    def __repr__(self) -> str:
        return (
            f"SessionJournal(build_id={self.build_id!r}, "
            f"entries={len(self.entries)}, phase={self.current_phase!r})"
        )


# ---------------------------------------------------------------------------
# Utility: compute a snapshot hash for drift detection
# ---------------------------------------------------------------------------


def compute_snapshot_hash(file_paths: list[str]) -> str:
    """Compute a deterministic hash of a list of file paths.

    Used for detecting workspace drift between checkpoints.
    """
    content = "\n".join(sorted(file_paths))
    return hashlib.sha256(content.encode()).hexdigest()[:16]
