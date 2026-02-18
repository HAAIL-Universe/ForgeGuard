"""Invariant Gates — hard constraint enforcement for multi-phase builds.

Tracks named invariants with monotonic, equality, or positivity constraints.
Used by the build loop to ensure the test count never decreases, syntax
errors remain at zero, and other key metrics are enforced automatically.

Phase 44 of the ForgeIDE cognitive architecture.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Any


# ---------------------------------------------------------------------------
# Constraint types
# ---------------------------------------------------------------------------


class Constraint(enum.Enum):
    """Constraint type for an invariant."""

    MONOTONIC_UP = "monotonic_up"      # value must be >= previous
    MONOTONIC_DOWN = "monotonic_down"  # value must be <= previous
    EQUAL = "equal"                    # value must match exactly
    NON_ZERO = "non_zero"             # value must be > 0


# ---------------------------------------------------------------------------
# Result
# ---------------------------------------------------------------------------


@dataclass
class GateResult:
    """Result of checking a single invariant against its constraint.

    Distinct from ``journal.InvariantResult`` which is a simpler
    equality check.  ``GateResult`` carries the *constraint kind*
    and a human-readable *message* suitable for violation reports.
    """

    name: str
    passed: bool
    expected: Any
    actual: Any
    constraint: Constraint
    message: str


# ---------------------------------------------------------------------------
# Built-in invariant definitions
# ---------------------------------------------------------------------------


BUILTIN_INVARIANTS: list[tuple[str, Constraint]] = [
    ("backend_test_count", Constraint.MONOTONIC_UP),
    ("frontend_test_count", Constraint.MONOTONIC_UP),
    ("backend_test_failures", Constraint.EQUAL),
    ("frontend_test_failures", Constraint.EQUAL),
    ("total_files", Constraint.MONOTONIC_UP),
    ("migration_count", Constraint.MONOTONIC_UP),
    ("syntax_errors", Constraint.EQUAL),
]

#: Default baseline values for built-in invariants.
BUILTIN_DEFAULTS: dict[str, Any] = {
    "backend_test_failures": 0,
    "frontend_test_failures": 0,
    "syntax_errors": 0,
}


# ---------------------------------------------------------------------------
# Invariant record (internal)
# ---------------------------------------------------------------------------


@dataclass
class _Invariant:
    """Single tracked invariant."""

    name: str
    value: Any
    constraint: Constraint


# ---------------------------------------------------------------------------
# InvariantRegistry
# ---------------------------------------------------------------------------


class InvariantRegistry:
    """Track and enforce named invariants with typed constraints.

    Usage::

        reg = InvariantRegistry()
        reg.register("backend_test_count", 929, Constraint.MONOTONIC_UP)
        result = reg.check("backend_test_count", 930)   # passes (930 >= 929)
        result = reg.check("backend_test_count", 928)   # FAILS  (928 < 929)
        reg.update("backend_test_count", 930)            # updates baseline to 930
    """

    def __init__(self) -> None:
        self._invariants: dict[str, _Invariant] = {}

    # -- registration -----------------------------------------------------

    def register(self, name: str, value: Any, constraint: Constraint) -> None:
        """Register (or re-register) an invariant with its baseline value."""
        self._invariants[name] = _Invariant(name=name, value=value, constraint=constraint)

    def register_builtins(self, initial_values: dict[str, Any] | None = None) -> None:
        """Register all built-in invariants with optional initial values.

        Any invariant not present in *initial_values* gets its default
        from ``BUILTIN_DEFAULTS`` (or ``0``).
        """
        vals = dict(BUILTIN_DEFAULTS)
        if initial_values:
            vals.update(initial_values)
        for name, constraint in BUILTIN_INVARIANTS:
            self.register(name, vals.get(name, 0), constraint)

    # -- checking ---------------------------------------------------------

    def check(self, name: str, current_value: Any) -> GateResult:
        """Check *current_value* against the registered invariant.

        Returns a ``GateResult`` regardless of pass/fail.
        Raises ``KeyError`` if *name* is not registered.
        """
        inv = self._invariants.get(name)
        if inv is None:
            raise KeyError(f"Invariant {name!r} not registered")

        passed, message = self._evaluate(inv, current_value)
        return GateResult(
            name=name,
            passed=passed,
            expected=inv.value,
            actual=current_value,
            constraint=inv.constraint,
            message=message,
        )

    def check_all(self, current_values: dict[str, Any]) -> list[GateResult]:
        """Check multiple invariants at once.

        Only checks invariants that appear in *current_values*.
        Returns a list of results (one per checked invariant).
        """
        results: list[GateResult] = []
        for name, value in current_values.items():
            if name in self._invariants:
                results.append(self.check(name, value))
        return results

    # -- update -----------------------------------------------------------

    def update(self, name: str, new_value: Any) -> GateResult:
        """Update the invariant baseline, but *only* if the new value
        satisfies the constraint.

        Returns the ``GateResult`` of the check.  If the check fails,
        the baseline is **not** updated.
        """
        result = self.check(name, new_value)
        if result.passed:
            self._invariants[name].value = new_value
        return result

    # -- serialisation (for checkpoint persistence) -----------------------

    def to_dict(self) -> dict[str, Any]:
        """Serialise all invariants to a dict for checkpoint storage."""
        return {
            name: {
                "value": inv.value,
                "constraint": inv.constraint.value,
            }
            for name, inv in self._invariants.items()
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> InvariantRegistry:
        """Restore registry from a serialised dict."""
        reg = cls()
        for name, inv_data in data.items():
            constraint = Constraint(inv_data["constraint"])
            reg.register(name, inv_data["value"], constraint)
        return reg

    # -- introspection ----------------------------------------------------

    @property
    def names(self) -> list[str]:
        """Return list of registered invariant names."""
        return list(self._invariants.keys())

    def get_value(self, name: str) -> Any:
        """Return the current baseline value for an invariant."""
        inv = self._invariants.get(name)
        if inv is None:
            raise KeyError(f"Invariant {name!r} not registered")
        return inv.value

    def get_constraint(self, name: str) -> Constraint:
        """Return the constraint type for an invariant."""
        inv = self._invariants.get(name)
        if inv is None:
            raise KeyError(f"Invariant {name!r} not registered")
        return inv.constraint

    def __len__(self) -> int:
        return len(self._invariants)

    def __contains__(self, name: str) -> bool:
        return name in self._invariants

    def __repr__(self) -> str:
        return f"InvariantRegistry({len(self._invariants)} invariants)"

    # -- private ----------------------------------------------------------

    @staticmethod
    def _evaluate(inv: _Invariant, current: Any) -> tuple[bool, str]:
        """Evaluate a constraint and return (passed, message)."""
        c = inv.constraint
        baseline = inv.value

        if c == Constraint.MONOTONIC_UP:
            passed = current >= baseline
            if passed:
                delta = current - baseline
                msg = (
                    f"{inv.name}: {current} >= {baseline} ✓"
                    if delta == 0
                    else f"{inv.name}: {baseline} → {current} (+{delta}) ✓"
                )
            else:
                msg = (
                    f"INVARIANT VIOLATION: {inv.name}\n"
                    f"  Expected: >= {baseline}\n"
                    f"  Actual: {current}\n"
                    f"  Constraint: MONOTONIC_UP"
                )
            return passed, msg

        if c == Constraint.MONOTONIC_DOWN:
            passed = current <= baseline
            if passed:
                delta = baseline - current
                msg = (
                    f"{inv.name}: {current} <= {baseline} ✓"
                    if delta == 0
                    else f"{inv.name}: {baseline} → {current} (-{delta}) ✓"
                )
            else:
                msg = (
                    f"INVARIANT VIOLATION: {inv.name}\n"
                    f"  Expected: <= {baseline}\n"
                    f"  Actual: {current}\n"
                    f"  Constraint: MONOTONIC_DOWN"
                )
            return passed, msg

        if c == Constraint.EQUAL:
            passed = current == baseline
            if passed:
                msg = f"{inv.name}: {current} == {baseline} ✓"
            else:
                msg = (
                    f"INVARIANT VIOLATION: {inv.name}\n"
                    f"  Expected: {baseline}\n"
                    f"  Actual: {current}\n"
                    f"  Constraint: EQUAL"
                )
            return passed, msg

        if c == Constraint.NON_ZERO:
            passed = current > 0
            if passed:
                msg = f"{inv.name}: {current} > 0 ✓"
            else:
                msg = (
                    f"INVARIANT VIOLATION: {inv.name}\n"
                    f"  Expected: > 0\n"
                    f"  Actual: {current}\n"
                    f"  Constraint: NON_ZERO"
                )
            return passed, msg

        # Fallback — shouldn't happen
        return True, f"{inv.name}: unknown constraint {c}"
