"""Tests for planner/plan_schema.py — ForgePlan schema and validate_plan().

The plan schema is the contract between Planner and Builder. These tests
verify that validation is strict, errors are surfaced clearly, and the
schema enforces its constraints (extra="forbid", required fields, enums).
"""

import sys
from pathlib import Path
import pytest

# planner/ uses bare imports — add it to sys.path
_PLANNER_DIR = Path(__file__).resolve().parent.parent / "planner"
if str(_PLANNER_DIR) not in sys.path:
    sys.path.insert(0, str(_PLANNER_DIR))

from plan_schema import (  # noqa: E402
    ForgePlan,
    ForgeMode,
    StackSpec,
    Phase,
    FileManifest,
    AcceptanceCriterion,
    ContractRef,
    ProjectSummary,
    validate_plan,
    SCHEMA_VERSION,
)


# ---------------------------------------------------------------------------
# Minimal valid fixtures
# ---------------------------------------------------------------------------

def _file_manifest(**overrides) -> dict:
    base = {
        "path": "app/main.py",
        "layer": "router",
        "action": "create",
        "description": "FastAPI entry point.",
    }
    return {**base, **overrides}


def _acceptance_criterion(**overrides) -> dict:
    base = {
        "id": "AC-0-1",
        "description": "App starts without errors.",
        "test_hint": "tests/test_health.py",
    }
    return {**base, **overrides}


def _phase(**overrides) -> dict:
    base = {
        "number": 0,
        "name": "Genesis",
        "purpose": "Bootstrap project structure.",
        "file_manifest": [_file_manifest()],
        "acceptance_criteria": [_acceptance_criterion()],
    }
    return {**base, **overrides}


def _stack(**overrides) -> dict:
    base = {
        "backend_language": "python",
        "backend_framework": "fastapi",
        "database": "postgresql",
        "test_framework": "pytest",
        "boot_script": True,
    }
    return {**base, **overrides}


def _summary(**overrides) -> dict:
    base = {
        "one_liner": "A FastAPI task API with JWT and PostgreSQL.",
        "mode": "greenfield",
        "stack_rationale": "FastAPI for performance. PostgreSQL for reliability.",
        "key_constraints": ["No direct DB access from routers"],
        "existing_contracts": ["builder_contract.md"],
        "missing_contracts": [],
        "boot_script_required": True,
    }
    return {**base, **overrides}


def _contract_ref(**overrides) -> dict:
    base = {
        "contract": "builder_contract.md",
        "section": "§1 Read gate",
        "note": "Builder must read all contracts before writing any file.",
    }
    return {**base, **overrides}


def _valid_plan(**overrides) -> dict:
    base = {
        "summary": _summary(),
        "stack": _stack(),
        "phases": [_phase()],
        "required_contracts": ["builder_contract.md"],
        "contract_refs": [_contract_ref()],
    }
    return {**base, **overrides}


# ---------------------------------------------------------------------------
# SCHEMA_VERSION
# ---------------------------------------------------------------------------

class TestSchemaVersion:
    def test_schema_version_is_string(self):
        assert isinstance(SCHEMA_VERSION, str)

    def test_schema_version_not_empty(self):
        assert SCHEMA_VERSION.strip() != ""


# ---------------------------------------------------------------------------
# ForgeMode
# ---------------------------------------------------------------------------

class TestForgeMode:
    def test_greenfield(self):
        assert ForgeMode("greenfield") == ForgeMode.greenfield

    def test_remediation(self):
        assert ForgeMode("remediation") == ForgeMode.remediation

    def test_invalid_mode_raises(self):
        with pytest.raises(ValueError):
            ForgeMode("invalid_mode")


# ---------------------------------------------------------------------------
# StackSpec
# ---------------------------------------------------------------------------

class TestStackSpec:
    def test_valid_minimal_stack(self):
        s = StackSpec(**_stack())
        assert s.backend_language == "python"
        assert s.frontend is None
        assert s.boot_script is True

    def test_optional_fields_accepted(self):
        s = StackSpec(**_stack(frontend="react", auth="jwt", llm_integration="anthropic"))
        assert s.frontend == "react"
        assert s.auth == "jwt"
        assert s.llm_integration == "anthropic"

    def test_missing_required_field_raises(self):
        data = _stack()
        del data["backend_language"]
        with pytest.raises(Exception):
            StackSpec(**data)


# ---------------------------------------------------------------------------
# Phase
# ---------------------------------------------------------------------------

class TestPhase:
    def test_valid_phase(self):
        p = Phase(**_phase())
        assert p.number == 0
        assert len(p.file_manifest) == 1
        assert p.schema_tables_claimed == []
        assert p.wires_from_phase is None
        assert p.exemptions == []

    def test_extra_field_forbidden(self):
        data = _phase()
        data["unknown_field"] = "bad"
        with pytest.raises(Exception):
            Phase(**data)

    def test_optional_wires_from_phase(self):
        p = Phase(**_phase(wires_from_phase=1))
        assert p.wires_from_phase == 1

    def test_schema_tables_claimed_list(self):
        p = Phase(**_phase(schema_tables_claimed=["users", "projects"]))
        assert p.schema_tables_claimed == ["users", "projects"]

    def test_missing_file_manifest_raises(self):
        data = _phase()
        del data["file_manifest"]
        with pytest.raises(Exception):
            Phase(**data)


# ---------------------------------------------------------------------------
# FileManifest
# ---------------------------------------------------------------------------

class TestFileManifest:
    def test_valid_manifest_entry(self):
        fm = FileManifest(**_file_manifest())
        assert fm.action == "create"
        assert fm.layer == "router"

    def test_all_required_fields(self):
        for field in ("path", "layer", "action", "description"):
            data = _file_manifest()
            del data[field]
            with pytest.raises(Exception):
                FileManifest(**data)


# ---------------------------------------------------------------------------
# AcceptanceCriterion
# ---------------------------------------------------------------------------

class TestAcceptanceCriterion:
    def test_valid(self):
        ac = AcceptanceCriterion(**_acceptance_criterion())
        assert ac.id == "AC-0-1"

    def test_all_required_fields(self):
        for field in ("id", "description", "test_hint"):
            data = _acceptance_criterion()
            del data[field]
            with pytest.raises(Exception):
                AcceptanceCriterion(**data)


# ---------------------------------------------------------------------------
# ProjectSummary
# ---------------------------------------------------------------------------

class TestProjectSummary:
    def test_valid_summary(self):
        s = ProjectSummary(**_summary())
        assert s.mode == ForgeMode.greenfield
        assert s.boot_script_required is True

    def test_extra_field_forbidden(self):
        data = _summary()
        data["extra"] = "nope"
        with pytest.raises(Exception):
            ProjectSummary(**data)

    def test_missing_required_field_raises(self):
        data = _summary()
        del data["one_liner"]
        with pytest.raises(Exception):
            ProjectSummary(**data)


# ---------------------------------------------------------------------------
# ContractRef
# ---------------------------------------------------------------------------

class TestContractRef:
    def test_valid(self):
        ref = ContractRef(**_contract_ref())
        assert ref.contract == "builder_contract.md"

    def test_all_required_fields(self):
        for field in ("contract", "section", "note"):
            data = _contract_ref()
            del data[field]
            with pytest.raises(Exception):
                ContractRef(**data)


# ---------------------------------------------------------------------------
# ForgePlan
# ---------------------------------------------------------------------------

class TestForgePlan:
    def test_valid_plan(self):
        plan = ForgePlan(**_valid_plan())
        assert plan.stack.backend_framework == "fastapi"
        assert len(plan.phases) == 1
        assert plan.metadata is None

    def test_metadata_is_optional(self):
        data = _valid_plan()
        data["metadata"] = {"schema_version": "1.0"}
        plan = ForgePlan(**data)
        assert plan.metadata == {"schema_version": "1.0"}

    def test_extra_field_forbidden(self):
        data = _valid_plan()
        data["surprise_field"] = "oops"
        with pytest.raises(Exception):
            ForgePlan(**data)

    def test_missing_summary_raises(self):
        data = _valid_plan()
        del data["summary"]
        with pytest.raises(Exception):
            ForgePlan(**data)

    def test_missing_phases_raises(self):
        data = _valid_plan()
        del data["phases"]
        with pytest.raises(Exception):
            ForgePlan(**data)

    def test_multiple_phases(self):
        phases = [_phase(number=i, name=f"Phase {i}") for i in range(3)]
        plan = ForgePlan(**_valid_plan(phases=phases))
        assert len(plan.phases) == 3


# ---------------------------------------------------------------------------
# validate_plan()
# ---------------------------------------------------------------------------

class TestValidatePlan:
    def test_valid_plan_returns_empty_list(self):
        errors = validate_plan(_valid_plan())
        assert errors == []

    def test_missing_required_field_returns_errors(self):
        data = _valid_plan()
        del data["summary"]
        errors = validate_plan(data)
        assert len(errors) > 0
        assert any("summary" in e for e in errors)

    def test_extra_forbidden_field_returns_errors(self):
        data = _valid_plan()
        data["unexpected"] = "value"
        errors = validate_plan(data)
        assert len(errors) > 0

    def test_invalid_mode_returns_errors(self):
        data = _valid_plan()
        data["summary"] = _summary(mode="not_a_mode")
        errors = validate_plan(data)
        assert len(errors) > 0

    def test_wrong_type_returns_errors(self):
        data = _valid_plan()
        data["phases"] = "not_a_list"
        errors = validate_plan(data)
        assert len(errors) > 0

    def test_empty_dict_returns_errors(self):
        errors = validate_plan({})
        assert len(errors) > 0

    def test_errors_are_strings(self):
        errors = validate_plan({})
        assert all(isinstance(e, str) for e in errors)

    def test_nested_error_surfaces_field_path(self):
        data = _valid_plan()
        # Break a nested required field
        data["stack"] = _stack(backend_language=None)
        errors = validate_plan(data)
        assert len(errors) > 0
