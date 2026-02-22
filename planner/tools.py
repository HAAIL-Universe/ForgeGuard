"""
tools.py — Tool definitions and implementations for the Planner Agent.

DESIGN: Two separate concerns kept explicit:
  - TOOL_DEFINITIONS: JSON schema the Anthropic API shows to the model
  - Implementations: Python functions that execute when the model calls a tool

Principle of least privilege:
  - read_file: read-only, sandboxed to ALLOWED_ROOT
  - list_directory: read-only, sandboxed to ALLOWED_ROOT
  - write_plan: validates schema BEFORE writing — loop continues on failure

Sandboxing:
  Every tool resolves the path and verifies it starts with ALLOWED_ROOT.
  This prevents the model from accidentally (or otherwise) reading files
  outside your ForgeCollection workspace.
"""

from __future__ import annotations
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from plan_schema import validate_plan, SCHEMA_VERSION

ALLOWED_ROOT = Path("z:/ForgeCollection").resolve()
CONTRACTS_DIR = ALLOWED_ROOT / "Forge" / "Contracts"
PLAN_OUTPUT_DIR = ALLOWED_ROOT / "ForgeBuilds"
LARGE_FILE_THRESHOLD = 8_000  # characters — truncate beyond this

# Named governance contracts available via get_contract()
# These are NOT pre-loaded into the system prompt — fetch on demand.
CONTRACT_REGISTRY: dict[str, str] = {
    "system_prompt":     "system_prompt.md",
    "auditor_prompt":    "auditor_prompt.md",
    "audit_reference":   "audit_reference.md",
    "builder_contract":  "builder_contract.md",
    "builder_directive": "builder_directive.md",
    "manifesto":         "templates/manifesto_template.md",
    "blueprint":         "templates/blueprint_template.md",
    "stack":             "templates/stack_template.md",
    "schema":            "templates/schema_template.md",
    "physics":           "templates/physics_template.yaml",
    "boundaries":        "templates/boundaries_template.json",
    "phases":            "templates/phases_template.md",
    "ui":                "templates/ui_template.md",
}


# ─── Tool Definitions (what the Anthropic API shows to the model) ────────────
#
# Production note: read_file, list_directory, and get_contract are intentionally
# NOT exposed to the model.  All user data (contracts, intake briefs) is passed
# in the initial user turn via project_request, already fetched from the Neon DB
# by planner_service.  Exposing filesystem tools would let the model read server
# internals — wrong and unsafe.
#
# The implementations below are kept for local dev/test scripts only.
# If you need the model to access external data in future (e.g. GitHub files),
# add purpose-built tools with proper auth, not raw filesystem access.

TOOL_DEFINITIONS: list[dict] = [
    {
        "name": "forge_get_project_contract",
        "description": (
            "Fetch the content of a project contract by type. "
            "Call this for every contract listed in the manifest — "
            "make ALL calls in parallel in your first turn."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "contract_type": {
                    "type": "string",
                    "description": (
                        "Contract type to fetch. One of: "
                        "blueprint, stack, schema, manifesto, physics, "
                        "boundaries, ui, phases, builder_directive"
                    ),
                },
            },
            "required": ["contract_type"],
        },
    },
    {
        "name": "write_plan",
        "description": (
            "Write the completed build plan as a JSON artifact to disk. "
            "ONLY call this when your plan is fully complete and validated. "
            "The plan is validated against the Forge plan schema before writing. "
            "If validation fails, errors are returned so you can fix and retry — "
            "the session does NOT end on validation failure. "
            "On success, the session ends and the plan is saved."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "plan_json": {
                    "type": "object",
                    "description": (
                        "The complete build plan. Must match the ForgePlan schema: "
                        "summary, stack, phases[], required_contracts[], contract_refs[]. "
                        "Do NOT include a 'metadata' key — it is stamped automatically."
                    ),
                },
                "project_name": {
                    "type": "string",
                    "description": (
                        "Short slug for the project (used in the output filename). "
                        "Lowercase, hyphens only. Example: 'task-api' or 'auth-service'."
                    ),
                },
            },
            "required": ["plan_json", "project_name"],
        },
    },
]


# ─── Tool Implementations ────────────────────────────────────────────────────

def _is_allowed(path: Path) -> bool:
    """Return True if the resolved path is under ALLOWED_ROOT."""
    try:
        path.resolve().relative_to(ALLOWED_ROOT)
        return True
    except ValueError:
        return False


def tool_read_file(path: str) -> dict:
    """
    Sandboxed file reader with automatic truncation for large files.

    Returns:
      On success: {"content": str, "size_chars": int, "truncated": bool, "path": str}
      On error:   {"error": str}
    """
    resolved = Path(path).resolve()

    if not _is_allowed(resolved):
        return {"error": f"Access denied: path is outside z:\\ForgeCollection\\"}

    if not resolved.exists():
        return {"error": f"File not found: {path}"}

    if resolved.is_dir():
        return {"error": f"{path} is a directory — use list_directory instead."}

    try:
        content = resolved.read_text(encoding="utf-8")
    except Exception as exc:
        return {"error": f"Could not read file: {exc}"}

    total_chars = len(content)
    truncated = total_chars > LARGE_FILE_THRESHOLD

    return {
        "content": content[:LARGE_FILE_THRESHOLD] if truncated else content,
        "size_chars": total_chars,
        "truncated": truncated,
        "truncated_at": LARGE_FILE_THRESHOLD if truncated else None,
        "path": str(resolved),
    }


def tool_list_directory(path: str) -> dict:
    """
    Sandboxed directory lister.

    Returns:
      On success: {"path": str, "directories": list[str], "files": list[str]}
      On error:   {"error": str}
    """
    resolved = Path(path).resolve()

    if not _is_allowed(resolved):
        return {"error": f"Access denied: path is outside z:\\ForgeCollection\\"}

    if not resolved.exists():
        return {"error": f"Directory not found: {path}"}

    if not resolved.is_dir():
        return {"error": f"{path} is not a directory — use read_file instead."}

    try:
        entries = sorted(resolved.iterdir(), key=lambda e: (e.is_file(), e.name))
        return {
            "path": str(resolved),
            "directories": [e.name for e in entries if e.is_dir()],
            "files": [e.name for e in entries if e.is_file()],
        }
    except Exception as exc:
        return {"error": f"Could not list directory: {exc}"}


def tool_get_contract(name: str) -> dict:
    """
    Fetch a named governance contract.

    Returns the same shape as tool_read_file so callers get consistent results.
    """
    filename = CONTRACT_REGISTRY.get(name)
    if filename is None:
        available = ", ".join(sorted(CONTRACT_REGISTRY))
        return {"error": f"Unknown contract '{name}'. Available: {available}"}

    path = CONTRACTS_DIR / filename
    return tool_read_file(str(path))


def tool_write_plan(plan_json: dict | None = None, project_name: str | None = None, **_ignored) -> dict:
    """
    Validate and persist the plan artifact.

    This is the TERMINAL tool — when the model calls this successfully,
    the agentic loop exits. If schema validation fails, error strings are
    returned to the model so it can correct and retry. The loop does NOT
    exit on validation failure.

    Validation order:
      1. Required-arg guard (returns error dict rather than raising TypeError)
      2. Pydantic schema check (ForgePlan)
      3. On success: stamp metadata and write to disk

    Returns:
      On success: {"success": True, "path": str, "message": str}
      On failure: {"success": False, "errors": list[str], "message": str}
    """
    # Guard: model called write_plan without required args — return a
    # recoverable error so the loop can continue rather than crashing.
    if not plan_json or not isinstance(plan_json, dict):
        return {
            "success": False,
            "errors": ["'plan_json' is required and must be a JSON object containing the complete plan."],
            "message": (
                "Call write_plan again with both 'plan_json' (the complete plan object) "
                "and 'project_name' (lowercase slug, e.g. 'my-project')."
            ),
        }
    if not project_name or not isinstance(project_name, str):
        return {
            "success": False,
            "errors": ["'project_name' is required and must be a non-empty string (e.g. 'my-project')."],
            "message": (
                "Call write_plan again including 'project_name' "
                "(lowercase slug, hyphens only, e.g. 'task-api')."
            ),
        }

    # Remove metadata if the model accidentally included it
    # (it belongs to the envelope, not the model's output)
    plan_json.pop("metadata", None)

    errors = validate_plan(plan_json)
    if errors:
        return {
            "success": False,
            "errors": errors,
            "message": (
                f"Plan schema validation failed with {len(errors)} error(s). "
                "Fix all errors listed and call write_plan again."
            ),
        }

    # Stamp the metadata envelope — information the builder uses to validate
    # the artifact it received
    plan_json["metadata"] = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "project_name": project_name,
        # planner_model is injected by run_planner() after writing
        # (the model itself doesn't know its own model ID)
    }

    PLAN_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    slug = project_name.lower().replace(" ", "-")
    filename = f"plan_{slug}_{timestamp}.json"
    output_path = PLAN_OUTPUT_DIR / filename

    output_path.write_text(json.dumps(plan_json, indent=2), encoding="utf-8")

    return {
        "success": True,
        "path": str(output_path),
        "message": f"Plan successfully written to {output_path}",
    }


# ─── Dispatcher ─────────────────────────────────────────────────────────────

def dispatch_tool(name: str, inputs: dict, contract_fetcher=None) -> Any:
    """Route a tool call from the agent loop to its implementation."""
    if name == "forge_get_project_contract":
        ct = inputs.get("contract_type", "")
        if contract_fetcher is not None:
            content = contract_fetcher(ct)
            if content is None:
                return {"error": f"Contract '{ct}' not found in project."}
            return {"contract_type": ct, "content": content}
        # Fallback: read from CONTRACT_REGISTRY (local dev / standalone use)
        return tool_get_contract(ct)
    elif name == "read_file":
        return tool_read_file(**inputs)
    elif name == "list_directory":
        return tool_list_directory(**inputs)
    elif name == "get_contract":
        return tool_get_contract(**inputs)
    elif name == "write_plan":
        return tool_write_plan(**inputs)
    else:
        return {"error": f"Unknown tool: '{name}'"}
