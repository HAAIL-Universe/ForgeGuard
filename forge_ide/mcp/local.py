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


def get_governance() -> dict[str, Any]:
    """Consolidated governance read gate — one call for all rules (cached).

    Returns the builder contract content, architecture boundary details,
    tech stack, and invariant definitions.  Designed for the IDE use case
    where Claude Code needs governance context before modifying code.
    """
    cached = cache_get("governance")
    if cached is not None:
        return cached

    sections: dict[str, Any] = {}

    # 1. Builder contract (master governance document)
    bc = load_contract("builder_contract")
    if "error" not in bc:
        sections["builder_contract"] = bc["content"]
    else:
        sections["builder_contract_error"] = bc["error"]

    # 2. Architecture boundaries (layer names + forbidden patterns)
    bd = load_contract("boundaries")
    if "error" not in bd:
        content = bd["content"]
        if isinstance(content, dict):
            layers_summary = []
            for layer in content.get("layers", []):
                layers_summary.append({
                    "name": layer["name"],
                    "glob": layer.get("glob", ""),
                    "description": layer.get("description", ""),
                    "forbidden": [
                        f["pattern"] + " — " + f["reason"]
                        for f in layer.get("forbidden", [])
                    ],
                })
            sections["boundaries"] = {
                "layers": layers_summary,
                "known_violations": content.get("known_violations", []),
            }
        else:
            sections["boundaries"] = content
    else:
        sections["boundaries_error"] = bd["error"]

    # 3. Technology stack
    st = load_contract("stack")
    if "error" not in st:
        sections["stack"] = st["content"]
    else:
        sections["stack_error"] = st["error"]

    # 4. Invariant gates
    inv = get_invariants()
    if "error" not in inv:
        sections["invariants"] = inv["invariants"]
    else:
        sections["invariants_error"] = inv.get("error", "Unknown error")

    result = {
        "governance_version": "1.0",
        "description": (
            "ForgeGuard governance rules. The builder_contract is the "
            "master document. Boundaries define forbidden imports per "
            "architectural layer. Stack defines required technologies. "
            "Invariants are hard constraints enforced during builds."
        ),
        "sections": sections,
        "available_contracts": list(CONTRACT_MAP.keys()),
        "hint": (
            "Use forge_get_contract(name) to read any individual "
            "contract in full. Key contracts: boundaries, physics, "
            "builder_directive, stack, builder_contract."
        ),
    }
    return cache_set("governance", result)


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
            "governance": "forge_get_governance (MCP tool) — consolidated read gate",
            "list_contracts": "forge_list_contracts (MCP tool)",
            "get_contract": "forge_get_contract(name) (MCP tool)",
            "get_invariants": "forge_get_invariants (MCP tool)",
        },
    }
    return cache_set("summary", result)
