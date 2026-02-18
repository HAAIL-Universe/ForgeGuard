"""Tests for Phase 44 — Invariant Gates & Test Baseline Tracking.

Covers:
- Constraint enum
- GateResult dataclass
- InvariantRegistry.register() / check() — all 4 constraint types
- MONOTONIC_UP — increase passes, decrease fails
- MONOTONIC_DOWN — decrease passes, increase fails
- EQUAL — exact match passes, any change fails
- NON_ZERO — positive passes, zero fails
- check_all() — mixed results
- update() — only updates if constraint satisfied
- register_builtins() — all 7 built-in invariants
- Serialisation: to_dict() / from_dict() round-trip
- Invariant persistence via journal checkpoint
- Edge cases
"""

from __future__ import annotations

import json

import pytest

from forge_ide.invariants import (
    BUILTIN_DEFAULTS,
    BUILTIN_INVARIANTS,
    Constraint,
    GateResult,
    InvariantRegistry,
)


# ---------------------------------------------------------------------------
# Constraint enum
# ---------------------------------------------------------------------------


class TestConstraint:
    """Test Constraint enum values."""

    def test_values(self):
        assert Constraint.MONOTONIC_UP.value == "monotonic_up"
        assert Constraint.MONOTONIC_DOWN.value == "monotonic_down"
        assert Constraint.EQUAL.value == "equal"
        assert Constraint.NON_ZERO.value == "non_zero"

    def test_from_value(self):
        assert Constraint("monotonic_up") == Constraint.MONOTONIC_UP
        assert Constraint("equal") == Constraint.EQUAL


# ---------------------------------------------------------------------------
# GateResult
# ---------------------------------------------------------------------------


class TestGateResult:
    """Test GateResult dataclass."""

    def test_create(self):
        r = GateResult(
            name="test_count",
            passed=True,
            expected=42,
            actual=45,
            constraint=Constraint.MONOTONIC_UP,
            message="test_count: 42 → 45 (+3) ✓",
        )
        assert r.passed is True
        assert r.constraint == Constraint.MONOTONIC_UP

    def test_failed(self):
        r = GateResult(
            name="x", passed=False, expected=10, actual=5,
            constraint=Constraint.MONOTONIC_UP, message="VIOLATION",
        )
        assert r.passed is False


# ---------------------------------------------------------------------------
# InvariantRegistry — registration
# ---------------------------------------------------------------------------


class TestRegistryRegister:
    """Test register() and basic introspection."""

    def test_register_single(self):
        reg = InvariantRegistry()
        reg.register("test_count", 100, Constraint.MONOTONIC_UP)
        assert "test_count" in reg
        assert len(reg) == 1

    def test_register_multiple(self):
        reg = InvariantRegistry()
        reg.register("a", 1, Constraint.EQUAL)
        reg.register("b", 2, Constraint.NON_ZERO)
        assert len(reg) == 2
        assert reg.names == ["a", "b"]

    def test_register_overwrites(self):
        reg = InvariantRegistry()
        reg.register("x", 10, Constraint.EQUAL)
        reg.register("x", 20, Constraint.MONOTONIC_UP)
        assert reg.get_value("x") == 20
        assert reg.get_constraint("x") == Constraint.MONOTONIC_UP

    def test_get_value_missing_raises(self):
        reg = InvariantRegistry()
        with pytest.raises(KeyError):
            reg.get_value("nonexistent")

    def test_get_constraint_missing_raises(self):
        reg = InvariantRegistry()
        with pytest.raises(KeyError):
            reg.get_constraint("nonexistent")


# ---------------------------------------------------------------------------
# MONOTONIC_UP
# ---------------------------------------------------------------------------


class TestMonotonicUp:
    """Test MONOTONIC_UP constraint."""

    def test_increase_passes(self):
        reg = InvariantRegistry()
        reg.register("tests", 100, Constraint.MONOTONIC_UP)
        result = reg.check("tests", 105)
        assert result.passed is True
        assert "✓" in result.message

    def test_equal_passes(self):
        reg = InvariantRegistry()
        reg.register("tests", 100, Constraint.MONOTONIC_UP)
        result = reg.check("tests", 100)
        assert result.passed is True

    def test_decrease_fails(self):
        reg = InvariantRegistry()
        reg.register("tests", 100, Constraint.MONOTONIC_UP)
        result = reg.check("tests", 98)
        assert result.passed is False
        assert "INVARIANT VIOLATION" in result.message
        assert "MONOTONIC_UP" in result.message

    def test_message_shows_delta(self):
        reg = InvariantRegistry()
        reg.register("tests", 100, Constraint.MONOTONIC_UP)
        result = reg.check("tests", 110)
        assert "+10" in result.message


# ---------------------------------------------------------------------------
# MONOTONIC_DOWN
# ---------------------------------------------------------------------------


class TestMonotonicDown:
    """Test MONOTONIC_DOWN constraint."""

    def test_decrease_passes(self):
        reg = InvariantRegistry()
        reg.register("errors", 10, Constraint.MONOTONIC_DOWN)
        result = reg.check("errors", 5)
        assert result.passed is True

    def test_equal_passes(self):
        reg = InvariantRegistry()
        reg.register("errors", 10, Constraint.MONOTONIC_DOWN)
        result = reg.check("errors", 10)
        assert result.passed is True

    def test_increase_fails(self):
        reg = InvariantRegistry()
        reg.register("errors", 10, Constraint.MONOTONIC_DOWN)
        result = reg.check("errors", 15)
        assert result.passed is False
        assert "INVARIANT VIOLATION" in result.message
        assert "MONOTONIC_DOWN" in result.message


# ---------------------------------------------------------------------------
# EQUAL
# ---------------------------------------------------------------------------


class TestEqual:
    """Test EQUAL constraint."""

    def test_match_passes(self):
        reg = InvariantRegistry()
        reg.register("failures", 0, Constraint.EQUAL)
        result = reg.check("failures", 0)
        assert result.passed is True

    def test_mismatch_fails(self):
        reg = InvariantRegistry()
        reg.register("failures", 0, Constraint.EQUAL)
        result = reg.check("failures", 3)
        assert result.passed is False
        assert "EQUAL" in result.message

    def test_string_equal(self):
        reg = InvariantRegistry()
        reg.register("hash", "abc123", Constraint.EQUAL)
        assert reg.check("hash", "abc123").passed is True
        assert reg.check("hash", "xyz").passed is False


# ---------------------------------------------------------------------------
# NON_ZERO
# ---------------------------------------------------------------------------


class TestNonZero:
    """Test NON_ZERO constraint."""

    def test_positive_passes(self):
        reg = InvariantRegistry()
        reg.register("tests", 1, Constraint.NON_ZERO)
        result = reg.check("tests", 42)
        assert result.passed is True

    def test_zero_fails(self):
        reg = InvariantRegistry()
        reg.register("tests", 1, Constraint.NON_ZERO)
        result = reg.check("tests", 0)
        assert result.passed is False
        assert "NON_ZERO" in result.message

    def test_negative_fails(self):
        reg = InvariantRegistry()
        reg.register("tests", 1, Constraint.NON_ZERO)
        result = reg.check("tests", -1)
        assert result.passed is False


# ---------------------------------------------------------------------------
# check_all()
# ---------------------------------------------------------------------------


class TestCheckAll:
    """Test check_all() with multiple invariants."""

    def test_all_pass(self):
        reg = InvariantRegistry()
        reg.register("a", 10, Constraint.MONOTONIC_UP)
        reg.register("b", 0, Constraint.EQUAL)
        results = reg.check_all({"a": 15, "b": 0})
        assert len(results) == 2
        assert all(r.passed for r in results)

    def test_mixed_results(self):
        reg = InvariantRegistry()
        reg.register("a", 10, Constraint.MONOTONIC_UP)
        reg.register("b", 0, Constraint.EQUAL)
        results = reg.check_all({"a": 5, "b": 0})
        assert len(results) == 2
        assert not results[0].passed  # a: 5 < 10
        assert results[1].passed      # b: 0 == 0

    def test_only_checks_registered(self):
        reg = InvariantRegistry()
        reg.register("a", 10, Constraint.MONOTONIC_UP)
        results = reg.check_all({"a": 15, "unknown": 99})
        assert len(results) == 1  # "unknown" skipped

    def test_empty_values(self):
        reg = InvariantRegistry()
        reg.register("a", 10, Constraint.MONOTONIC_UP)
        results = reg.check_all({})
        assert len(results) == 0

    def test_check_unregistered_raises(self):
        reg = InvariantRegistry()
        with pytest.raises(KeyError):
            reg.check("nonexistent", 42)


# ---------------------------------------------------------------------------
# update()
# ---------------------------------------------------------------------------


class TestUpdate:
    """Test update() — only updates if constraint is satisfied."""

    def test_update_with_valid_value(self):
        reg = InvariantRegistry()
        reg.register("tests", 100, Constraint.MONOTONIC_UP)
        result = reg.update("tests", 110)
        assert result.passed is True
        assert reg.get_value("tests") == 110

    def test_update_with_invalid_value(self):
        reg = InvariantRegistry()
        reg.register("tests", 100, Constraint.MONOTONIC_UP)
        result = reg.update("tests", 90)
        assert result.passed is False
        assert reg.get_value("tests") == 100  # NOT updated

    def test_update_equal_constraint(self):
        reg = InvariantRegistry()
        reg.register("fails", 0, Constraint.EQUAL)
        result = reg.update("fails", 0)
        assert result.passed is True
        # Updating to non-zero should fail
        result = reg.update("fails", 5)
        assert result.passed is False
        assert reg.get_value("fails") == 0


# ---------------------------------------------------------------------------
# register_builtins()
# ---------------------------------------------------------------------------


class TestBuiltinInvariants:
    """Test built-in invariant registration."""

    def test_register_builtins_count(self):
        reg = InvariantRegistry()
        reg.register_builtins()
        assert len(reg) == len(BUILTIN_INVARIANTS)

    def test_register_builtins_names(self):
        reg = InvariantRegistry()
        reg.register_builtins()
        expected_names = {name for name, _ in BUILTIN_INVARIANTS}
        assert set(reg.names) == expected_names

    def test_register_builtins_defaults(self):
        reg = InvariantRegistry()
        reg.register_builtins()
        # Defaults from BUILTIN_DEFAULTS
        assert reg.get_value("backend_test_failures") == 0
        assert reg.get_value("syntax_errors") == 0

    def test_register_builtins_custom_values(self):
        reg = InvariantRegistry()
        reg.register_builtins({"backend_test_count": 929, "total_files": 50})
        assert reg.get_value("backend_test_count") == 929
        assert reg.get_value("total_files") == 50

    def test_builtin_defaults_constant(self):
        assert "backend_test_failures" in BUILTIN_DEFAULTS
        assert BUILTIN_DEFAULTS["backend_test_failures"] == 0


# ---------------------------------------------------------------------------
# Serialisation: to_dict / from_dict round-trip
# ---------------------------------------------------------------------------


class TestSerialisation:
    """Test to_dict() / from_dict() round-trip."""

    def test_roundtrip_empty(self):
        reg = InvariantRegistry()
        data = reg.to_dict()
        assert data == {}
        restored = InvariantRegistry.from_dict(data)
        assert len(restored) == 0

    def test_roundtrip_with_invariants(self):
        reg = InvariantRegistry()
        reg.register("tests", 100, Constraint.MONOTONIC_UP)
        reg.register("errors", 0, Constraint.EQUAL)
        reg.register("files", 50, Constraint.MONOTONIC_UP)

        data = reg.to_dict()
        restored = InvariantRegistry.from_dict(data)

        assert len(restored) == 3
        assert restored.get_value("tests") == 100
        assert restored.get_constraint("tests") == Constraint.MONOTONIC_UP
        assert restored.get_value("errors") == 0
        assert restored.get_constraint("errors") == Constraint.EQUAL

    def test_roundtrip_json(self):
        reg = InvariantRegistry()
        reg.register_builtins({"backend_test_count": 500})

        raw = json.dumps(reg.to_dict())
        data = json.loads(raw)
        restored = InvariantRegistry.from_dict(data)
        assert restored.get_value("backend_test_count") == 500

    def test_roundtrip_preserves_constraints(self):
        reg = InvariantRegistry()
        reg.register_builtins()
        data = reg.to_dict()
        restored = InvariantRegistry.from_dict(data)
        for name, constraint in BUILTIN_INVARIANTS:
            assert restored.get_constraint(name) == constraint


# ---------------------------------------------------------------------------
# Invariant persistence via journal checkpoint
# ---------------------------------------------------------------------------


class TestInvariantPersistence:
    """Test invariant registry state survives checkpoint round-trip."""

    def test_persist_in_journal(self):
        from forge_ide.journal import SessionJournal, JournalCheckpoint

        # Setup
        journal = SessionJournal("b1", phase="Phase 2")
        reg = InvariantRegistry()
        reg.register("backend_test_count", 100, Constraint.MONOTONIC_UP)
        reg.register("syntax_errors", 0, Constraint.EQUAL)

        # Store registry in journal invariants (like build_service does)
        journal.invariants["_inv_registry"] = reg.to_dict()

        # Checkpoint round-trip
        ckpt = journal.get_checkpoint()
        raw = ckpt.to_json()
        restored_ckpt = JournalCheckpoint.from_json(raw)

        # Restore
        journal2 = SessionJournal.restore_from_checkpoint(restored_ckpt)
        inv_data = journal2.invariants.pop("_inv_registry", None)
        assert inv_data is not None

        reg2 = InvariantRegistry.from_dict(inv_data)
        assert reg2.get_value("backend_test_count") == 100
        assert reg2.get_constraint("backend_test_count") == Constraint.MONOTONIC_UP
        assert reg2.get_value("syntax_errors") == 0

    def test_persist_after_update(self):
        from forge_ide.journal import SessionJournal

        reg = InvariantRegistry()
        reg.register("tests", 100, Constraint.MONOTONIC_UP)
        reg.update("tests", 120)

        journal = SessionJournal("b1")
        journal.invariants["_inv_registry"] = reg.to_dict()

        ckpt = journal.get_checkpoint()
        raw = ckpt.to_json()

        from forge_ide.journal import JournalCheckpoint
        restored = JournalCheckpoint.from_json(raw)
        j2 = SessionJournal.restore_from_checkpoint(restored)
        inv_data = j2.invariants.pop("_inv_registry", {})
        reg2 = InvariantRegistry.from_dict(inv_data)
        assert reg2.get_value("tests") == 120


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    """Edge cases and misc."""

    def test_repr(self):
        reg = InvariantRegistry()
        reg.register("x", 1, Constraint.EQUAL)
        assert "1 invariants" in repr(reg)

    def test_contains(self):
        reg = InvariantRegistry()
        reg.register("x", 1, Constraint.EQUAL)
        assert "x" in reg
        assert "y" not in reg

    def test_len_empty(self):
        assert len(InvariantRegistry()) == 0

    def test_monotonic_up_zero_baseline(self):
        """Test starting from 0 — any positive value should pass."""
        reg = InvariantRegistry()
        reg.register("tests", 0, Constraint.MONOTONIC_UP)
        assert reg.check("tests", 0).passed is True
        assert reg.check("tests", 1).passed is True

    def test_equal_with_none(self):
        reg = InvariantRegistry()
        reg.register("x", None, Constraint.EQUAL)
        assert reg.check("x", None).passed is True
        assert reg.check("x", 0).passed is False

    def test_update_chain(self):
        """Successive updates increase baseline monotonically."""
        reg = InvariantRegistry()
        reg.register("tests", 10, Constraint.MONOTONIC_UP)
        reg.update("tests", 20)
        reg.update("tests", 30)
        assert reg.get_value("tests") == 30
        # Can't go back to 20
        result = reg.check("tests", 20)
        assert result.passed is False
