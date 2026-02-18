"""Tests for Phase 43 — Session Journal & Context Continuity.

Covers:
- JournalEntry / JournalCheckpoint data classes (creation, serialisation)
- SessionJournal.record() — entries stored with correct timestamps and types
- get_summary() — structured text within token budget
- get_checkpoint() + restore_from_checkpoint() — round-trip preserves state
- Context rotation — conversation compacted with journal summary
- Checkpoint persistence — serialisable, retrievable
- Build resume — invariants and files_written restored
- Journal with 500+ entries — summary within token budget
- compute_snapshot_hash() — deterministic
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from forge_ide.journal import (
    ENTRY_TYPES,
    InvariantResult,
    JournalCheckpoint,
    JournalEntry,
    SessionJournal,
    compute_snapshot_hash,
)


# ---------------------------------------------------------------------------
# JournalEntry
# ---------------------------------------------------------------------------


class TestJournalEntry:
    """Test JournalEntry dataclass."""

    def test_create(self):
        e = JournalEntry(
            timestamp="2026-01-01T00:00:00+00:00",
            event_type="task_completed",
            phase="Phase 0",
            task_id="t1",
            detail="Generated app/main.py",
            metadata={"file_path": "app/main.py"},
        )
        assert e.event_type == "task_completed"
        assert e.phase == "Phase 0"
        assert e.task_id == "t1"
        assert e.metadata["file_path"] == "app/main.py"

    def test_to_dict(self):
        e = JournalEntry(
            timestamp="2026-01-01T00:00:00+00:00",
            event_type="phase_start",
            phase="Phase 1",
            task_id=None,
            detail="Starting Phase 1",
        )
        d = e.to_dict()
        assert d["event_type"] == "phase_start"
        assert d["task_id"] is None
        assert d["metadata"] == {}

    def test_from_dict(self):
        d = {
            "timestamp": "2026-01-01T00:00:00+00:00",
            "event_type": "error",
            "phase": "Phase 2",
            "task_id": "t5",
            "detail": "Failed to generate",
            "metadata": {"error": "timeout"},
        }
        e = JournalEntry.from_dict(d)
        assert e.event_type == "error"
        assert e.metadata["error"] == "timeout"

    def test_from_dict_ignores_extra_keys(self):
        d = {
            "timestamp": "2026-01-01T00:00:00+00:00",
            "event_type": "custom",
            "phase": "Phase 0",
            "task_id": None,
            "detail": "test",
            "metadata": {},
            "extra_field": "ignored",
        }
        e = JournalEntry.from_dict(d)
        assert e.detail == "test"
        assert not hasattr(e, "extra_field")

    def test_default_metadata(self):
        e = JournalEntry(
            timestamp="2026-01-01T00:00:00+00:00",
            event_type="custom",
            phase="",
            task_id=None,
            detail="no meta",
        )
        assert e.metadata == {}


# ---------------------------------------------------------------------------
# JournalCheckpoint
# ---------------------------------------------------------------------------


class TestJournalCheckpoint:
    """Test JournalCheckpoint serialisation."""

    def test_to_json_roundtrip(self):
        ckpt = JournalCheckpoint(
            build_id="b-123",
            phase="Phase 3",
            invariants={"test_count": 42},
            files_written=["a.py", "b.py"],
            snapshot_hash="abc123",
            compressed_history="summary text",
            created_at="2026-01-01T00:00:00+00:00",
        )
        raw = ckpt.to_json()
        restored = JournalCheckpoint.from_json(raw)
        assert restored.build_id == "b-123"
        assert restored.phase == "Phase 3"
        assert restored.invariants == {"test_count": 42}
        assert restored.files_written == ["a.py", "b.py"]
        assert restored.snapshot_hash == "abc123"
        assert restored.compressed_history == "summary text"

    def test_from_json_ignores_extra(self):
        raw = json.dumps({
            "build_id": "x",
            "phase": "Phase 0",
            "invariants": {},
            "files_written": [],
            "snapshot_hash": "",
            "compressed_history": "",
            "created_at": "",
            "task_dag_state": {},
            "bonus": True,
        })
        ckpt = JournalCheckpoint.from_json(raw)
        assert ckpt.build_id == "x"

    def test_empty_defaults(self):
        ckpt = JournalCheckpoint(build_id="b", phase="P")
        assert ckpt.task_dag_state == {}
        assert ckpt.files_written == []
        assert ckpt.snapshot_hash == ""


# ---------------------------------------------------------------------------
# SessionJournal — recording
# ---------------------------------------------------------------------------


class TestSessionJournalRecord:
    """Test SessionJournal.record() and related methods."""

    def test_record_appends_entry(self):
        j = SessionJournal("build-1", phase="Phase 0")
        entry = j.record("phase_start", "Starting Phase 0")
        assert len(j) == 1
        assert entry.event_type == "phase_start"
        assert entry.phase == "Phase 0"

    def test_record_uses_current_phase(self):
        j = SessionJournal("build-1", phase="Phase 1")
        entry = j.record("task_completed", "Done")
        assert entry.phase == "Phase 1"

    def test_record_override_phase(self):
        j = SessionJournal("build-1", phase="Phase 1")
        entry = j.record("error", "Oops", phase="Phase 0")
        assert entry.phase == "Phase 0"

    def test_record_with_task_id(self):
        j = SessionJournal("build-1")
        entry = j.record("task_completed", "Done", task_id="t-42")
        assert entry.task_id == "t-42"

    def test_record_with_metadata(self):
        j = SessionJournal("build-1")
        entry = j.record("file_written", "Wrote a.py", metadata={"file_path": "a.py"})
        assert entry.metadata["file_path"] == "a.py"

    def test_record_timestamp_is_iso(self):
        j = SessionJournal("build-1")
        entry = j.record("custom", "test")
        # Should parse as valid ISO datetime
        dt = datetime.fromisoformat(entry.timestamp)
        assert dt.tzinfo is not None

    def test_file_written_auto_tracks(self):
        j = SessionJournal("build-1")
        j.record("file_written", "Wrote x.py", metadata={"file_path": "x.py"})
        j.record("file_written", "Wrote y.py", metadata={"path": "y.py"})
        assert "x.py" in j.files_written
        assert "y.py" in j.files_written

    def test_file_written_no_duplicates(self):
        j = SessionJournal("build-1")
        j.record("file_written", "Wrote x.py", metadata={"file_path": "x.py"})
        j.record("file_written", "Wrote x.py again", metadata={"file_path": "x.py"})
        assert j.files_written.count("x.py") == 1

    def test_set_phase(self):
        j = SessionJournal("build-1")
        j.set_phase("Phase 5")
        assert j.current_phase == "Phase 5"
        entry = j.record("custom", "test")
        assert entry.phase == "Phase 5"

    def test_phase_stats_tracking(self):
        j = SessionJournal("build-1")
        j.record("phase_start", "Begin", phase="Phase 0", metadata={"task_count": 3})
        j.record("task_completed", "Done 1", phase="Phase 0", metadata={"tokens_in": 100, "tokens_out": 200})
        j.record("task_completed", "Done 2", phase="Phase 0", metadata={"tokens_in": 50, "tokens_out": 50})
        j.record("phase_complete", "Done", phase="Phase 0")
        completed = j._get_completed_phases()
        assert len(completed) == 1
        assert completed[0][0] == "Phase 0"
        assert completed[0][1]["tasks_completed"] == 2
        assert completed[0][1]["tokens"] == 400  # 100+200+50+50


# ---------------------------------------------------------------------------
# SessionJournal — invariants
# ---------------------------------------------------------------------------


class TestSessionJournalInvariants:
    """Test invariant tracking."""

    def test_set_invariant(self):
        j = SessionJournal("build-1")
        j.set_invariant("test_count", 42)
        assert j.invariants["test_count"] == 42
        # Also records an entry
        assert any(e.event_type == "invariant_set" for e in j.entries)

    def test_check_invariant_passes(self):
        j = SessionJournal("build-1")
        j.set_invariant("test_count", 42)
        result = j.check_invariant("test_count", 42)
        assert result.passed is True
        assert result.expected == 42
        assert result.actual == 42

    def test_check_invariant_fails(self):
        j = SessionJournal("build-1")
        j.set_invariant("test_count", 42)
        result = j.check_invariant("test_count", 40)
        assert result.passed is False
        assert result.expected == 42
        assert result.actual == 40
        # Records violation
        assert any(e.event_type == "invariant_violated" for e in j.entries)

    def test_check_untracked_invariant(self):
        j = SessionJournal("build-1")
        result = j.check_invariant("nonexistent", 99)
        assert result.passed is True
        assert result.expected is None

    def test_invariant_result_dataclass(self):
        r = InvariantResult(key="k", expected=1, actual=2, passed=False)
        assert r.key == "k"


# ---------------------------------------------------------------------------
# SessionJournal — get_summary()
# ---------------------------------------------------------------------------


class TestSessionJournalSummary:
    """Test summary generation."""

    def test_summary_contains_header(self):
        j = SessionJournal("build-42", phase="Phase 2")
        summary = j.get_summary()
        assert "SESSION JOURNAL" in summary
        assert "build-42" in summary
        assert "Phase 2" in summary

    def test_summary_contains_invariants(self):
        j = SessionJournal("b1")
        j.set_invariant("test_count", 100)
        summary = j.get_summary()
        assert "test_count" in summary
        assert "100" in summary

    def test_summary_contains_completed_phases(self):
        j = SessionJournal("b1")
        j.record("phase_start", "Begin", phase="Phase 0", metadata={"task_count": 2})
        j.record("task_completed", "Done 1", phase="Phase 0")
        j.record("task_completed", "Done 2", phase="Phase 0")
        j.record("phase_complete", "Done", phase="Phase 0")
        summary = j.get_summary()
        assert "Completed Phases" in summary
        assert "Phase 0" in summary

    def test_summary_contains_recent_events(self):
        j = SessionJournal("b1")
        j.record("custom", "First event")
        j.record("custom", "Second event")
        summary = j.get_summary()
        assert "Recent Events" in summary
        assert "First event" in summary
        assert "Second event" in summary

    def test_summary_contains_files_written(self):
        j = SessionJournal("b1", phase="Phase 1")
        j.record("file_written", "Wrote a.py", metadata={"file_path": "a.py"})
        summary = j.get_summary()
        assert "a.py" in summary

    def test_summary_ends_with_footer(self):
        j = SessionJournal("b1")
        summary = j.get_summary()
        assert summary.strip().endswith("=== END JOURNAL ===")

    def test_summary_token_budget(self):
        j = SessionJournal("b1")
        # Add lots of entries
        for i in range(100):
            j.record("custom", f"Event {i}: " + "x" * 50)
        summary = j.get_summary(max_tokens=500)
        # 500 tokens × 4 chars ≈ 2000 chars max
        assert len(summary) <= 2200  # small tolerance for structure

    def test_to_context_block_alias(self):
        j = SessionJournal("b1")
        j.record("custom", "test")
        assert j.to_context_block() == j.get_summary()


# ---------------------------------------------------------------------------
# SessionJournal — checkpoint round-trip
# ---------------------------------------------------------------------------


class TestSessionJournalCheckpoint:
    """Test checkpoint creation and restoration."""

    def test_get_checkpoint(self):
        j = SessionJournal("build-1", phase="Phase 2")
        j.set_invariant("test_count", 50)
        j.record("file_written", "Wrote x.py", metadata={"file_path": "x.py"})
        ckpt = j.get_checkpoint()
        assert ckpt.build_id == "build-1"
        assert ckpt.phase == "Phase 2"
        assert ckpt.invariants == {"test_count": 50}
        assert "x.py" in ckpt.files_written
        assert ckpt.compressed_history  # non-empty

    def test_get_checkpoint_with_dag_state(self):
        j = SessionJournal("b1")
        dag = {"nodes": {"t1": {"status": "completed"}}}
        ckpt = j.get_checkpoint(dag_state=dag)
        assert ckpt.task_dag_state == dag

    def test_get_checkpoint_with_snapshot_hash(self):
        j = SessionJournal("b1")
        ckpt = j.get_checkpoint(snapshot_hash="abc123")
        assert ckpt.snapshot_hash == "abc123"

    def test_roundtrip_checkpoint(self):
        j = SessionJournal("build-1", phase="Phase 5")
        j.set_invariant("test_count", 200)
        j.set_invariant("file_count", 30)
        j.record("file_written", "Wrote a.py", metadata={"file_path": "a.py"})
        j.record("file_written", "Wrote b.py", metadata={"file_path": "b.py"})
        j.record("task_completed", "Generated c.py", task_id="t1")

        ckpt = j.get_checkpoint(
            dag_state={"nodes": {}},
            snapshot_hash="hash123",
        )

        # Serialise to JSON and back
        raw = ckpt.to_json()
        restored_ckpt = JournalCheckpoint.from_json(raw)

        # Restore journal from checkpoint
        j2 = SessionJournal.restore_from_checkpoint(restored_ckpt)
        assert j2.build_id == "build-1"
        assert j2.current_phase == "Phase 5"
        assert j2.invariants == {"test_count": 200, "file_count": 30}
        assert "a.py" in j2.files_written
        assert "b.py" in j2.files_written
        # Restored journal has the synthetic "resumed" entry
        assert len(j2) == 1
        assert j2.entries[0].event_type == "custom"
        assert "Resumed" in j2.entries[0].detail

    def test_restore_preserves_invariants(self):
        j = SessionJournal("b1")
        j.set_invariant("x", 10)
        ckpt = j.get_checkpoint()
        j2 = SessionJournal.restore_from_checkpoint(ckpt)
        result = j2.check_invariant("x", 10)
        assert result.passed is True

    def test_checkpoint_serialisation_json(self):
        ckpt = JournalCheckpoint(
            build_id="b-1",
            phase="Phase 0",
            invariants={"a": 1},
        )
        raw = ckpt.to_json()
        parsed = json.loads(raw)
        assert parsed["build_id"] == "b-1"
        assert parsed["invariants"]["a"] == 1


# ---------------------------------------------------------------------------
# Context rotation (enhanced _compact_conversation)
# ---------------------------------------------------------------------------


class TestContextRotationWithJournal:
    """Test that _compact_conversation uses journal summary when provided."""

    def test_compact_without_journal(self):
        from app.services.build.context import _compact_conversation

        msgs = [
            {"role": "user", "content": "directive"},
            {"role": "assistant", "content": "resp1"},
            {"role": "user", "content": "turn2"},
            {"role": "assistant", "content": "resp2"},
            {"role": "user", "content": "turn3"},
            {"role": "assistant", "content": "resp3"},
            {"role": "user", "content": "turn4"},
            {"role": "assistant", "content": "resp4"},
        ]
        result = _compact_conversation(msgs, current_phase="Phase 1")
        # Should keep directive + summary + last 4
        assert len(result) == 6
        assert "progress summary" in result[1]["content"]

    def test_compact_with_journal_summary(self):
        from app.services.build.context import _compact_conversation

        msgs = [
            {"role": "user", "content": "directive"},
            {"role": "assistant", "content": "resp1"},
            {"role": "user", "content": "turn2"},
            {"role": "assistant", "content": "resp2"},
            {"role": "user", "content": "turn3"},
            {"role": "assistant", "content": "resp3"},
            {"role": "user", "content": "turn4"},
            {"role": "assistant", "content": "resp4"},
        ]

        journal_summary = "=== SESSION JOURNAL ===\n## Phase 1 done\n=== END ==="
        result = _compact_conversation(
            msgs, current_phase="Phase 1", journal_summary=journal_summary,
        )
        assert len(result) == 6
        assert "journal-based summary" in result[1]["content"]
        assert "SESSION JOURNAL" in result[1]["content"]

    def test_compact_short_messages_unchanged(self):
        from app.services.build.context import _compact_conversation

        msgs = [
            {"role": "user", "content": "a"},
            {"role": "assistant", "content": "b"},
        ]
        result = _compact_conversation(msgs, journal_summary="journal")
        assert len(result) == 2  # too short, no compaction


# ---------------------------------------------------------------------------
# Large journal — 500+ entries within budget
# ---------------------------------------------------------------------------


class TestLargeJournal:
    """Test journal with many entries stays within token budget."""

    def test_500_entries_summary_within_budget(self):
        j = SessionJournal("big-build", phase="Phase 10")

        # Record 5 completed phases
        for p in range(5):
            j.record("phase_start", f"Begin Phase {p}", phase=f"Phase {p}", metadata={"task_count": 100})
            for t in range(100):
                j.record(
                    "task_completed",
                    f"Generated file_{p}_{t}.py",
                    phase=f"Phase {p}",
                    task_id=f"t-{p}-{t}",
                    metadata={"tokens_in": 100, "tokens_out": 200, "file_path": f"file_{p}_{t}.py"},
                )
                j.record(
                    "file_written",
                    f"Wrote file_{p}_{t}.py",
                    phase=f"Phase {p}",
                    metadata={"file_path": f"file_{p}_{t}.py"},
                )
            j.record("phase_complete", f"Phase {p} done", phase=f"Phase {p}")

        assert len(j) > 500  # way more than 500

        summary = j.get_summary(max_tokens=4000)
        # 4000 tokens × 4 chars ≈ 16000 chars
        assert len(summary) <= 17000
        assert "SESSION JOURNAL" in summary
        assert "END JOURNAL" in summary

    def test_large_journal_contains_recent(self):
        j = SessionJournal("b1", phase="Phase 3")
        for i in range(200):
            j.record("custom", f"Event-{i}")

        summary = j.get_summary()
        # Recent events should be present
        assert "Event-199" in summary

    def test_large_journal_checkpoint_roundtrip(self):
        j = SessionJournal("b1", phase="Phase 3")
        j.set_invariant("test_count", 999)
        for i in range(100):
            j.record("file_written", f"Wrote f{i}.py", metadata={"file_path": f"f{i}.py"})

        ckpt = j.get_checkpoint()
        raw = ckpt.to_json()
        restored = JournalCheckpoint.from_json(raw)
        j2 = SessionJournal.restore_from_checkpoint(restored)

        assert j2.invariants["test_count"] == 999
        assert len(j2.files_written) == 100


# ---------------------------------------------------------------------------
# compute_snapshot_hash
# ---------------------------------------------------------------------------


class TestSnapshotHash:
    """Test snapshot hash utility."""

    def test_deterministic(self):
        h1 = compute_snapshot_hash(["a.py", "b.py"])
        h2 = compute_snapshot_hash(["a.py", "b.py"])
        assert h1 == h2

    def test_order_independent(self):
        h1 = compute_snapshot_hash(["b.py", "a.py"])
        h2 = compute_snapshot_hash(["a.py", "b.py"])
        assert h1 == h2  # sorted internally

    def test_different_inputs_different_hash(self):
        h1 = compute_snapshot_hash(["a.py"])
        h2 = compute_snapshot_hash(["b.py"])
        assert h1 != h2

    def test_returns_16_char_hex(self):
        h = compute_snapshot_hash(["x.py"])
        assert len(h) == 16
        assert all(c in "0123456789abcdef" for c in h)

    def test_empty_list(self):
        h = compute_snapshot_hash([])
        assert len(h) == 16


# ---------------------------------------------------------------------------
# ENTRY_TYPES constant
# ---------------------------------------------------------------------------


class TestEntryTypes:
    """Test the ENTRY_TYPES constant."""

    def test_contains_required_types(self):
        required = {
            "phase_start", "phase_complete", "task_completed", "task_failed",
            "test_run", "file_written", "error", "recon",
            "invariant_set", "invariant_violated", "checkpoint_saved", "custom",
        }
        assert required.issubset(ENTRY_TYPES)

    def test_is_frozenset(self):
        assert isinstance(ENTRY_TYPES, frozenset)


# ---------------------------------------------------------------------------
# Repr and len
# ---------------------------------------------------------------------------


class TestJournalMisc:
    """Test __repr__ and __len__."""

    def test_repr(self):
        j = SessionJournal("build-1", phase="Phase 2")
        r = repr(j)
        assert "build-1" in r
        assert "Phase 2" in r

    def test_len(self):
        j = SessionJournal("b1")
        assert len(j) == 0
        j.record("custom", "a")
        assert len(j) == 1
        j.record("custom", "b")
        assert len(j) == 2

    def test_current_phase_summary_no_stats(self):
        j = SessionJournal("b1", phase="Phase X")
        summary = j._get_current_phase_summary()
        assert "Phase X" in summary

    def test_current_phase_summary_with_stats(self):
        j = SessionJournal("b1", phase="Phase 1")
        j.record("phase_start", "Begin", metadata={"task_count": 5})
        j.record("task_completed", "Done 1")
        summary = j._get_current_phase_summary()
        assert "1/5" in summary
