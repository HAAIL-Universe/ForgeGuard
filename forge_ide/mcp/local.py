"""Local-mode readers — serve contracts, invariants, and summaries from disk."""

from __future__ import annotations

import json
import sys
from typing import Any

from .cache import cache_get, cache_set
from .config import (
    CONTRACT_MAP,
    CONTRACTS_DIR,
    FORGEGUARD_ROOT,
    INVARIANT_DESCRIPTIONS,
)


def load_contract(name: str) -> dict[str, Any]:
    """Read a single contract from disk (cached)."""
    cached = cache_get(f"contract:{name}")
    if cached is not None:
        return cached

    if name not in CONTRACT_MAP:
        return {"error": f"Unknown contract: {name}"}

    filename, fmt = CONTRACT_MAP[name]
    path = CONTRACTS_DIR / filename

    if not path.exists():
        return {"error": f"Contract file not found: {filename}"}

    raw = path.read_text(encoding="utf-8")

    if fmt == "json":
        content = json.loads(raw)
    elif fmt == "yaml":
        try:
            import yaml

            content = yaml.safe_load(raw)
        except Exception:
            content = raw
    else:
        content = raw

    result = {"name": name, "filename": filename, "format": fmt, "content": content}
    return cache_set(f"contract:{name}", result)


def list_contracts() -> dict[str, Any]:
    """List all contracts from disk (cached)."""
    cached = cache_get("list_contracts")
    if cached is not None:
        return cached

    items = []
    for name, (filename, fmt) in CONTRACT_MAP.items():
        items.append(
            {
                "name": name,
                "filename": filename,
                "format": fmt,
                "available": (CONTRACTS_DIR / filename).exists(),
            }
        )
    result = {"contracts": items, "total": len(items)}
    return cache_set("list_contracts", result)


def get_invariants() -> dict[str, Any]:
    """Read invariant definitions from forge_ide (cached)."""
    cached = cache_get("invariants")
    if cached is not None:
        return cached

    sys.path.insert(0, str(FORGEGUARD_ROOT))
    try:
        from forge_ide.invariants import BUILTIN_DEFAULTS, BUILTIN_INVARIANTS, Constraint
    except ImportError:
        return {
            "error": "Cannot import forge_ide.invariants — ensure FORGEGUARD_ROOT is correct",
            "root": str(FORGEGUARD_ROOT),
        }

    invariants = []
    for name, constraint in BUILTIN_INVARIANTS:
        invariants.append(
            {
                "name": name,
                "constraint": constraint.value,
                "default_value": BUILTIN_DEFAULTS.get(name, 0),
                "description": INVARIANT_DESCRIPTIONS.get(name, ""),
            }
        )

    constraints = [{"name": c.name, "value": c.value} for c in Constraint]
    result = {
        "invariants": invariants,
        "constraint_types": constraints,
        "total": len(invariants),
    }
    return cache_set("invariants", result)


def get_summary() -> dict[str, Any]:
    """Build a governance summary from disk (cached)."""
    cached = cache_get("summary")
    if cached is not None:
        return cached

    boundaries_data = load_contract("boundaries")
    layers = []
    content = boundaries_data.get("content")
    if isinstance(content, dict):
        for layer in content.get("layers", []):
            layers.append(
                {
                    "name": layer["name"],
                    "description": layer.get("description", ""),
                    "forbidden_count": len(layer.get("forbidden", [])),
                }
            )

    sys.path.insert(0, str(FORGEGUARD_ROOT))
    try:
        from forge_ide.invariants import BUILTIN_INVARIANTS

        invariant_names = [name for name, _ in BUILTIN_INVARIANTS]
    except ImportError:
        invariant_names = list(INVARIANT_DESCRIPTIONS.keys())

    result = {
        "framework": "ForgeGuard",
        "description": (
            "Governance-as-code framework for AI-driven software builds. "
            "Enforces architectural boundaries, invariant gates, and "
            "phased delivery with audit trails."
        ),
        "contracts": list(CONTRACT_MAP.keys()),
        "architectural_layers": layers,
        "invariants": invariant_names,
        "endpoints": {
            "list_contracts": "forge_list_contracts (MCP tool)",
            "get_contract": "forge_get_contract(name) (MCP tool)",
            "get_invariants": "forge_get_invariants (MCP tool)",
            "get_boundaries": "forge_get_boundaries (MCP tool)",
            "get_physics": "forge_get_physics (MCP tool)",
            "get_directive": "forge_get_directive (MCP tool)",
            "get_stack": "forge_get_stack (MCP tool)",
        },
    }
    return cache_set("summary", result)
