"""
plan_schema.py — The contract between Planner and Builder.

This Pydantic schema is the handshake. The planner validates against
it before writing. The builder validates against it before building.
If the schema version changes, everything fails loudly.

Design principles:
  - COMPACT: the builder reads plan.json in one API call (~2,150 tokens)
  - SELF-CONTAINED: builder needs no other context to start Phase 0
  - CONTRACT-TRACED: decisions link back to specific contract sections
  - STRICT: extra = "forbid" means any unknown field is a validation error
"""

from __future__ import annotations
from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field


SCHEMA_VERSION = "1.0"


class ForgeMode(str, Enum):
    greenfield = "greenfield"
    remediation = "remediation"


class StackSpec(BaseModel):
    backend_language: str            # "python"
    backend_framework: str           # "fastapi"
    database: str                    # "postgresql"
    frontend: Optional[str] = None   # "react" | "vanilla-ts" | null
    auth: Optional[str] = None       # "jwt" | "apikey" | null
    llm_integration: Optional[str] = None  # "openai" | "anthropic" | null
    test_framework: str              # "pytest"
    boot_script: bool = False        # opt-in setup script (default: no)


class ContractRef(BaseModel):
    """Compact pointer to a contract section. Avoids duplicating full text."""
    contract: str   # filename, e.g. "builder_contract.md"
    section: str    # e.g. "§4 File boundary enforcement"
    note: str       # 1-sentence why it is relevant to this specific plan


class FileManifest(BaseModel):
    """One file the builder will create or modify."""
    path: str           # relative to project root, e.g. "app/routers/auth.py"
    layer: str          # "router" | "service" | "repo" | "llm" | "config" | "test"
    action: str         # "create" | "modify" | "delete"
    description: str    # what this file does (1 sentence)
    depends_on: list[str] = Field(
        default_factory=list,
        description="File paths this file imports from or depends on",
    )
    exports: list[str] = Field(
        default_factory=list,
        description=(
            "Public interface signatures this file MUST export. "
            "One-line strings: 'class Timer(BaseModel): id, name, state' or "
            "'async def create_timer(name: str, duration: int) -> Timer' or "
            "'router prefix: /timers — POST / GET /{id}'"
        ),
    )


class AcceptanceCriterion(BaseModel):
    id: str              # e.g. "AC-1-2"
    description: str     # end-to-end, observable criterion
    test_hint: str       # which test file or manual step covers this


class Phase(BaseModel):
    number: int
    name: str                           # e.g. "Genesis" | "Auth" | "Core Data"
    purpose: str                        # 1-2 sentences for the TL;DR header
    file_manifest: list[FileManifest]
    acceptance_criteria: list[AcceptanceCriterion]
    schema_tables_claimed: list[str] = Field(default_factory=list)
    wires_from_phase: Optional[int] = None  # builder_contract.md §9.5
    exemptions: list[str] = Field(default_factory=list)  # phase-level build exemptions

    class Config:
        extra = "forbid"


class ProjectSummary(BaseModel):
    """
    TL;DR section — read this first.

    Under 500 tokens. Replaces re-reading all contracts for the builder.
    The builder uses key_constraints as the fast path to non-negotiable rules.
    """
    one_liner: str                      # "A FastAPI task API with JWT and PostgreSQL"
    mode: ForgeMode
    stack_rationale: str                # why this stack (2-3 sentences)
    key_constraints: list[str]          # top 5 contract rules affecting this build
    existing_contracts: list[str]       # filled contract files already on disk
    missing_contracts: list[str]        # templates still needing to be filled
    boot_script_required: bool = False

    class Config:
        extra = "forbid"


class ForgePlan(BaseModel):
    """
    Top-level plan artifact.

    Written by the Planner Agent to ForgeBuilds/plan_{name}_{ts}.json.
    Read and validated by the Builder Agent before any build work begins.

    Token budget:
      summary           ~300 tokens
      stack             ~100 tokens
      phases × 4        ~1,500 tokens
      contract_refs     ~200 tokens
      metadata          ~50 tokens
      Total             ~2,150 tokens — fits in one builder API call
    """
    summary: ProjectSummary
    stack: StackSpec
    phases: list[Phase]
    required_contracts: list[str]       # builder_contract.md §1 read gate
    contract_refs: list[ContractRef]    # non-obvious rules for this specific build
    metadata: Optional[dict] = None     # stamped by write_plan(), not by the model

    class Config:
        extra = "forbid"


def validate_plan(plan_dict: dict) -> list[str]:
    """
    Validate a plan dict against ForgePlan.
    Returns a list of error strings (empty list = valid).

    Used by:
      - tools.tool_write_plan() — prevents the planner from writing a bad artifact
      - builder startup — prevents the builder from consuming a bad artifact
    """
    try:
        ForgePlan(**plan_dict)
        return []
    except Exception as exc:
        errors = []
        for err in exc.errors():
            loc = " -> ".join(str(part) for part in err["loc"])
            errors.append(f"{loc}: {err['msg']}")
        return errors
