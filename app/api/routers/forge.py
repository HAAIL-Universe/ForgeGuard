"""Read-only Forge contract endpoints.

Exposes governance contracts, invariants, and project metadata so that
external tools (MCP, CLI, third-party integrations) can read the
framework rules without write access.

All endpoints require a valid Forge API key *or* JWT.
"""

import json
import logging
from pathlib import Path
from typing import Literal

import yaml
from fastapi import APIRouter, Depends, HTTPException

from app.api.forge_auth import get_forge_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/forge", tags=["forge"])

# ── Contract file registry ────────────────────────────────────────────────

_CONTRACTS_DIR = Path(__file__).resolve().parent.parent.parent.parent / "Forge" / "Contracts"

# Map of logical name → (filename, format)
_CONTRACT_MAP: dict[str, tuple[str, str]] = {
    "boundaries": ("boundaries.json", "json"),
    "physics": ("physics.yaml", "yaml"),
    "blueprint": ("blueprint.md", "markdown"),
    "builder_contract": ("builder_contract.md", "markdown"),
    "builder_directive": ("builder_directive.md", "markdown"),
    "manifesto": ("manifesto.md", "markdown"),
    "phases": ("phases.md", "markdown"),
    "schema": ("schema.md", "markdown"),
    "stack": ("stack.md", "markdown"),
    "system_prompt": ("system_prompt.md", "markdown"),
    "ui": ("ui.md", "markdown"),
    "auditor_prompt": ("auditor_prompt.md", "markdown"),
    "recovery_planner_prompt": ("recovery_planner_prompt.md", "markdown"),
    "remediation": ("Remediation.md", "markdown"),
    "desktop_distribution_plan": ("Desktop_Distribution_Plan.md", "markdown"),
}


def _load_contract(name: str) -> dict:
    """Load a single contract file and return structured content."""
    if name not in _CONTRACT_MAP:
        raise HTTPException(status_code=404, detail=f"Unknown contract: {name}")

    filename, fmt = _CONTRACT_MAP[name]
    path = _CONTRACTS_DIR / filename

    if not path.exists():
        raise HTTPException(status_code=404, detail=f"Contract file not found: {filename}")

    raw = path.read_text(encoding="utf-8")

    if fmt == "json":
        content = json.loads(raw)
    elif fmt == "yaml":
        try:
            content = yaml.safe_load(raw)
        except yaml.YAMLError:
            # Some YAML files may contain non-standard syntax; serve as raw text
            content = raw
    else:
        content = raw  # markdown — return as string

    return {
        "name": name,
        "filename": filename,
        "format": fmt,
        "content": content,
    }


# ── Endpoints ─────────────────────────────────────────────────────────────


@router.get("/contracts")
async def list_contracts(_user: dict = Depends(get_forge_user)) -> dict:
    """List all available contract types with their filenames and formats."""
    items = []
    for name, (filename, fmt) in _CONTRACT_MAP.items():
        exists = (_CONTRACTS_DIR / filename).exists()
        items.append({
            "name": name,
            "filename": filename,
            "format": fmt,
            "available": exists,
        })
    return {"contracts": items, "total": len(items)}


@router.get("/contracts/{contract_name}")
async def get_contract(
    contract_name: str,
    _user: dict = Depends(get_forge_user),
) -> dict:
    """Return the full content of a single contract."""
    return _load_contract(contract_name)


@router.get("/invariants")
async def get_invariants(_user: dict = Depends(get_forge_user)) -> dict:
    """Return all invariant definitions and constraint types."""
    from forge_ide.invariants import BUILTIN_DEFAULTS, BUILTIN_INVARIANTS, Constraint

    invariants = []
    for name, constraint in BUILTIN_INVARIANTS:
        invariants.append({
            "name": name,
            "constraint": constraint.value,
            "default_value": BUILTIN_DEFAULTS.get(name, 0),
            "description": _INVARIANT_DESCRIPTIONS.get(name, ""),
        })

    constraints = [{"name": c.name, "value": c.value} for c in Constraint]

    return {
        "invariants": invariants,
        "constraint_types": constraints,
        "total": len(invariants),
    }


@router.get("/boundaries")
async def get_boundaries(_user: dict = Depends(get_forge_user)) -> dict:
    """Return the architectural layer boundary rules."""
    return _load_contract("boundaries")


@router.get("/physics")
async def get_physics(_user: dict = Depends(get_forge_user)) -> dict:
    """Return the canonical API specification (physics.yaml)."""
    return _load_contract("physics")


@router.get("/directive")
async def get_directive(_user: dict = Depends(get_forge_user)) -> dict:
    """Return the builder directive / system prompt."""
    return _load_contract("builder_directive")


@router.get("/stack")
async def get_stack(_user: dict = Depends(get_forge_user)) -> dict:
    """Return the technology stack contract."""
    return _load_contract("stack")


@router.get("/summary")
async def get_forge_summary(_user: dict = Depends(get_forge_user)) -> dict:
    """Return a compact summary of the entire governance framework.

    Useful for LLMs to get a single-call overview of all rules,
    invariants, and layer boundaries without fetching each contract.
    """
    from forge_ide.invariants import BUILTIN_INVARIANTS

    boundaries_data = _load_contract("boundaries")
    layers = []
    if isinstance(boundaries_data["content"], dict):
        for layer in boundaries_data["content"].get("layers", []):
            layers.append({
                "name": layer["name"],
                "description": layer.get("description", ""),
                "forbidden_count": len(layer.get("forbidden", [])),
            })

    invariant_names = [name for name, _ in BUILTIN_INVARIANTS]

    contract_names = list(_CONTRACT_MAP.keys())

    return {
        "framework": "ForgeGuard",
        "description": (
            "Governance-as-code framework for AI-driven software builds. "
            "Enforces architectural boundaries, invariant gates, and "
            "phased delivery with audit trails."
        ),
        "contracts": contract_names,
        "architectural_layers": layers,
        "invariants": invariant_names,
        "endpoints": {
            "list_contracts": "GET /forge/contracts",
            "get_contract": "GET /forge/contracts/{name}",
            "get_invariants": "GET /forge/invariants",
            "get_boundaries": "GET /forge/boundaries",
            "get_physics": "GET /forge/physics",
            "get_directive": "GET /forge/directive",
            "get_stack": "GET /forge/stack",
        },
    }


# ── Invariant human-readable descriptions ────────────────────────────────

_INVARIANT_DESCRIPTIONS: dict[str, str] = {
    "backend_test_count": "Backend test count must never decrease between phases",
    "frontend_test_count": "Frontend test count must never decrease between phases",
    "backend_test_failures": "Backend test failures must remain at zero",
    "frontend_test_failures": "Frontend test failures must remain at zero",
    "total_files": "Total tracked files must never decrease (no accidental deletions)",
    "migration_count": "Database migration count must never decrease",
    "syntax_errors": "Syntax errors must remain at zero",
}
