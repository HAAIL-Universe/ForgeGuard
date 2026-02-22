# Forge Auditor Agent — Full Implementation Plan

**Version:** 1.0  
**Date:** 2025-02-22  
**Status:** Design Phase (ready for implementation)

---

## Overview

This document outlines the complete implementation of the **Auditor Agent** subsystem, which separates external audit responsibilities from Planner and Builder agents. The auditor provides independent governance, prevents self-audit bias, and maintains persistent audit ledgers for compliance and debugging.

---

## Architecture

### High-Level Flow

```
Planner writes plan.json
    ↓
PlannerAuditor (agent mode: "plan")
    ├─ Fetch auditor_prompt, audit_reference, plan.json
    ├─ Run independent audit
    ├─ Save audit_ledger entry
    └─ Return: {passed, issues, recommendations}
        ↓ (if passed)
        Builder Phase 0
          ↓
        BuilderAuditor (agent mode: "phase", phase=0)
          ├─ Fetch phase outputs (files, tests)
          ├─ Check boundaries, coverage, quality
          ├─ Save audit_ledger entry
          └─ Return: {passed, violations}
          
          (repeat for phases 1-N)
              ↓
        Audit summaries exported to GitHub
```

### Agent Modes

The Auditor is **unified** but mode-aware:

```python
# Same agent, different contexts:
auditor_agent(mode="plan", contract_fetcher=...)      # for planner
auditor_agent(mode="phase", phase_num=2, files=...)   # for builder
```

---

## Database Schema

### 1. Alembic Migration: `audit_ledgers` Table

**File:** `db/alembic/versions/027_create_audit_ledgers_table.py`

```python
"""Create audit_ledgers table."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

def upgrade():
    op.create_table(
        'audit_ledgers',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('build_id', sa.UUID(), nullable=False),
        sa.Column('project_id', sa.UUID(), nullable=False),
        
        # Audit context
        sa.Column('agent_name', sa.String(50), nullable=False),
            # "planner_auditor" | "builder_auditor"
        sa.Column('phase_number', sa.Integer(), nullable=True),
            # NULL for planner; 0-N for builder phases
        
        # Results
        sa.Column('status', sa.String(20), nullable=False),
            # "passed" | "failed" | "warned"
        sa.Column('passed_checks', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('failed_checks', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('warned_checks', sa.Integer(), nullable=False, server_default='0'),
        
        # Audit details (JSONB for flexibility)
        sa.Column('issues', postgresql.JSONB(), nullable=True),
            # [{check_name, severity, message, remediation}]
        sa.Column('recommendations', postgresql.JSONB(), nullable=True),
            # [{priority, suggestion}]
        
        # Performance & cost tracking
        sa.Column('duration_seconds', sa.Float(), nullable=False),
        sa.Column('token_usage', postgresql.JSONB(), nullable=False),
            # {input_tokens, output_tokens, cache_read, cache_write}
        
        # Artifact tracking
        sa.Column('audit_report_path', sa.String(255), nullable=True),
            # GitHub path: builds/<build_id>/audit/phase_<N>_report.md
        
        # Timestamps
        sa.Column('created_at', sa.DateTime(timezone=True), 
                  nullable=False, server_default=sa.func.now()),
        
        sa.ForeignKeyConstraint(['build_id'], ['builds.id'], ),
        sa.ForeignKeyConstraint(['project_id'], ['projects.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.Index('idx_audit_build', 'build_id'),
        sa.Index('idx_audit_project', 'project_id'),
        sa.Index('idx_audit_agent', 'agent_name'),
    )

def downgrade():
    op.drop_table('audit_ledgers')
```

### 2. Pydantic Models

**File:** `app/models.py` (create if not exists, or add to existing models file)

```python
"""Pydantic models for audit ledger and results."""

from datetime import datetime
from typing import Optional
from uuid import UUID
from pydantic import BaseModel, Field

class AuditCheck(BaseModel):
    """Single audit check result."""
    check_name: str  # e.g., "boundary_violation", "test_coverage"
    severity: str  # "info", "warning", "error"
    message: str
    remediation: Optional[str] = None

class AuditRecommendation(BaseModel):
    """Recommendation from auditor."""
    priority: str  # "low", "medium", "high"
    suggestion: str

class TokenUsage(BaseModel):
    """Token usage for an audit run."""
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0

class AuditLedgerEntry(BaseModel):
    """Record of a single audit."""
    id: UUID
    build_id: UUID
    project_id: UUID
    agent_name: str  # "planner_auditor" | "builder_auditor"
    phase_number: Optional[int] = None
    status: str  # "passed" | "failed" | "warned"
    passed_checks: int
    failed_checks: int
    warned_checks: int
    issues: Optional[list[AuditCheck]] = None
    recommendations: Optional[list[AuditRecommendation]] = None
    duration_seconds: float
    token_usage: TokenUsage
    audit_report_path: Optional[str] = None
    created_at: datetime

class AuditResult(BaseModel):
    """What the auditor returns to caller."""
    passed: bool
    status: str  # "passed" | "failed" | "warned"
    failed_checks: int
    warned_checks: int
    issues: Optional[list[AuditCheck]] = None
    recommendations: Optional[list[AuditRecommendation]] = None
    duration_seconds: float
    token_usage: TokenUsage
    ledger_id: UUID
```

---

## Repository Methods

### File: `app/repos/project_repo.py` (add these methods)

```python
async def insert_audit_ledger(
    build_id: UUID,
    project_id: UUID,
    agent_name: str,
    phase_number: Optional[int],
    status: str,
    passed_checks: int,
    failed_checks: int,
    warned_checks: int,
    issues: Optional[list[dict]],
    recommendations: Optional[list[dict]],
    duration_seconds: float,
    token_usage: dict,
    audit_report_path: Optional[str] = None,
) -> UUID:
    """
    Insert an audit ledger entry to the database.
    Returns the inserted ledger ID.
    """
    ledger_id = uuid4()
    async with get_db() as session:
        entry = AuditLedger(
            id=ledger_id,
            build_id=build_id,
            project_id=project_id,
            agent_name=agent_name,
            phase_number=phase_number,
            status=status,
            passed_checks=passed_checks,
            failed_checks=failed_checks,
            warned_checks=warned_checks,
            issues=issues,
            recommendations=recommendations,
            duration_seconds=duration_seconds,
            token_usage=token_usage,
            audit_report_path=audit_report_path,
        )
        session.add(entry)
        await session.commit()
    return ledger_id

async def get_prior_audits_for_project(
    project_id: UUID,
    agent_name: Optional[str] = None,
    limit: int = 10,
) -> list[AuditLedgerEntry]:
    """
    Fetch prior audit records for the project.
    Used by auditor to recall patterns (e.g., "have we seen this boundary violation?").
    """
    async with get_db() as session:
        query = session.query(AuditLedger).filter(
            AuditLedger.project_id == project_id
        )
        if agent_name:
            query = query.filter(AuditLedger.agent_name == agent_name)
        results = await query.order_by(
            AuditLedger.created_at.desc()
        ).limit(limit).all()
    return [AuditLedgerEntry.from_orm(r) for r in results]

async def get_audit_by_build(
    build_id: UUID,
    phase_number: Optional[int] = None,
) -> list[AuditLedgerEntry]:
    """Fetch all audits for a build (optionally filtered by phase)."""
    async with get_db() as session:
        query = session.query(AuditLedger).filter(
            AuditLedger.build_id == build_id
        )
        if phase_number is not None:
            query = query.filter(AuditLedger.phase_number == phase_number)
        results = await query.order_by(AuditLedger.created_at.asc()).all()
    return [AuditLedgerEntry.from_orm(r) for r in results]

async def export_audit_ledger_to_markdown(
    build_id: UUID,
    project_id: UUID,
) -> str:
    """
    Generate a markdown audit ledger summary for GitHub.
    Returns the markdown content.
    """
    audits = await get_audit_by_build(build_id)
    lines = [
        f"# Audit Ledger — Build {build_id}",
        "",
    ]
    
    for audit in audits:
        phase_str = f" (Phase {audit.phase_number})" if audit.phase_number else ""
        lines.append(f"## {audit.agent_name}{phase_str}")
        lines.append(f"**Status:** {'✅ Passed' if audit.passed == 'passed' else '❌ Failed'}")
        lines.append(f"**Duration:** {audit.duration_seconds:.1f}s")
        lines.append(f"**Checks:** {audit.passed_checks} passed, {audit.failed_checks} failed, {audit.warned_checks} warned")
        lines.append("")
        
        if audit.issues:
            lines.append("**Issues:**")
            for issue in audit.issues:
                lines.append(f"- [{issue['severity']}] {issue['check_name']}: {issue['message']}")
            lines.append("")
        
        if audit.recommendations:
            lines.append("**Recommendations:**")
            for rec in audit.recommendations:
                lines.append(f"- {rec['suggestion']}")
            lines.append("")
    
    return "\n".join(lines)
```

---

## Auditor Agent Design

### File Structure

```
auditor/
  __init__.py
  auditor_agent.py          # Main agent loop
  tools.py                  # Tool definitions & implementations
  audit_schema.py           # Pydantic schemas for audit results
  context_loader.py         # Load contracts and context
  run_auditor.py            # Entry point for external calls
  PLAN.md                   # This file
```

### 1. Audit Schema (`audit_schema.py`)

```python
"""Audit schema and validation."""

from pydantic import BaseModel
from typing import Optional

class AuditResult(BaseModel):
    """What auditor returns."""
    passed: bool
    status: str  # "passed" | "failed" | "warned"
    issues: list[dict] = []
    recommendations: list[dict] = []
    token_usage: dict
    duration_seconds: float
    
    def validate(self) -> bool:
        """Ensure result is well-formed."""
        return isinstance(self.passed, bool) and self.status in ["passed", "failed", "warned"]

def validate_audit_result(result: AuditResult) -> tuple[bool, Optional[str]]:
    """
    Validate auditor result is complete and consistent.
    Returns: (is_valid, error_message)
    """
    if not result.validate():
        return False, "Audit result schema invalid"
    
    if result.status == "passed" and result.failed_checks > 0:
        return False, "Status is 'passed' but failed_checks > 0"
    
    if result.status == "failed" and not result.issues:
        return False, "Status is 'failed' but no issues reported"
    
    return True, None
```

### 2. Auditor Agent (`auditor_agent.py`)

```python
"""
auditor_agent.py — External audit agent for Planner and Builder.

Modes:
  - "plan": audit a generated plan from Planner
  - "phase": audit a builder phase output

This agent is independent of Planner/Builder and uses external governance contracts.
"""

from __future__ import annotations
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Callable
from uuid import UUID, uuid4
import anthropic

from audit_schema import AuditResult, validate_audit_result
from tools import TOOL_DEFINITIONS, dispatch_tool
from context_loader import build_audit_system_prompt

MODEL = os.environ.get("LLM_AUDITOR_MODEL", "claude-opus-4-1")
MAX_TOKENS = 16_000  # Audits are typically shorter than planning
MAX_ITERATIONS = 5   # Safety valve

INITIAL_USER_TURN_PLAN = """\
You are an external auditor. Your job is to audit the plan below for consistency,
completeness, and alignment with Forge contracts.

PLAN TO AUDIT:
{plan_json}

GOVERNANCE CONTRACTS:
- auditor_prompt.md: your audit mandate
- audit_reference.md: reference standards
- builder_contract.md: builder expectations

Audit this plan and call audit_complete with your result.
"""

INITIAL_USER_TURN_PHASE = """\
You are an external auditor. Your job is to audit Phase {phase_number} output
for code quality, boundary compliance, and test coverage.

PHASE OUTPUT:
- Files: {phase_files}
- Test coverage: {test_coverage}%
- Violations detected: {violations_list}

GOVERNANCE CONTRACTS:
- boundaries.json: layer rules
- builder_contract.md: builder standards

Audit this phase and call audit_complete with your result.
"""

class AuditorError(Exception):
    pass

async def run_auditor(
    mode: str,  # "plan" | "phase"
    build_id: UUID,
    project_id: UUID,
    plan_json: Optional[dict] = None,       # for mode="plan"
    phase_number: Optional[int] = None,     # for mode="phase"
    phase_files: Optional[list[str]] = None,  # for mode="phase"
    test_coverage: Optional[float] = None,   # for mode="phase"
    violations: Optional[list[dict]] = None,  # for mode="phase"
    contract_fetcher: Callable = None,
    verbose: bool = True,
) -> AuditResult:
    """
    Run auditor in specified mode.
    
    Args:
        mode: "plan" for planner audits, "phase" for builder audits
        build_id: unique build identifier
        project_id: project id
        plan_json: plan to audit (mode="plan")
        phase_number: phase number (mode="phase")
        phase_files: files generated in phase (mode="phase")
        test_coverage: test coverage % (mode="phase")
        violations: pre-detected violations (mode="phase")
        contract_fetcher: function to fetch contracts
        verbose: print debug output
    
    Returns:
        AuditResult object
    """
    agent_name = "planner_auditor" if mode == "plan" else "builder_auditor"
    
    if verbose:
        print(f"[AUDITOR] Starting {agent_name} (build={build_id})")
    
    client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
    system_blocks = build_audit_system_prompt(mode, contract_fetcher=contract_fetcher)
    
    # Build initial user turn
    if mode == "plan":
        user_turn = INITIAL_USER_TURN_PLAN.format(
            plan_json=json.dumps(plan_json, indent=2)[:8000]  # truncate for context
        )
    elif mode == "phase":
        violations_str = "\n".join([f"  - {v}" for v in (violations or [])])
        user_turn = INITIAL_USER_TURN_PHASE.format(
            phase_number=phase_number,
            phase_files=", ".join(phase_files or [])[:500],
            test_coverage=test_coverage or 0,
            violations_list=violations_str if violations else "(none detected)"
        )
    else:
        raise AuditorError(f"Unknown mode: {mode}")
    
    messages = [{"role": "user", "content": user_turn}]
    total_usage = {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_tokens": 0,
        "cache_write_tokens": 0,
    }
    
    start_time = time.time()
    iteration = 0
    audit_result: Optional[AuditResult] = None
    
    # ─── Agentic Loop ────────────────────────────────────────────────────
    while iteration < MAX_ITERATIONS:
        iteration += 1
        
        if verbose:
            print(f"[AUDITOR] Turn {iteration}/{MAX_ITERATIONS}")
        
        response = client.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            system=system_blocks,
            tools=TOOL_DEFINITIONS,
            messages=messages,
        )
        
        # Track tokens
        u = response.usage
        total_usage["input_tokens"] += u.input_tokens
        total_usage["output_tokens"] += u.output_tokens
        total_usage["cache_read_tokens"] += getattr(u, "cache_read_input_tokens", 0)
        total_usage["cache_write_tokens"] += getattr(u, "cache_creation_input_tokens", 0)
        
        if verbose:
            print(f"[AUDITOR] API: {u.output_tokens} output tokens")
        
        # Append assistant response
        messages.append({"role": "assistant", "content": [
            {"type": "text", "text": block.text} if block.type == "text"
            else {"type": "tool_use", "id": block.id, "name": block.name, "input": block.input}
            for block in response.content
        ]})
        
        # Handle end_turn
        if response.stop_reason == "end_turn":
            raise AuditorError("Auditor ended turn without calling audit_complete")
        
        # Dispatch tools
        tool_results = []
        for block in response.content:
            if block.type != "tool_use":
                continue
            
            if verbose:
                print(f"[AUDITOR] Tool: {block.name}")
            
            result = dispatch_tool(block.name, block.input)
            
            if block.name == "audit_complete":
                audit_result = AuditResult(**result.get("audit_result", {}))
                duration = time.time() - start_time
                audit_result.token_usage = total_usage
                audit_result.duration_seconds = duration
                
                # Validate result
                is_valid, err_msg = validate_audit_result(audit_result)
                if not is_valid:
                    raise AuditorError(f"Audit result invalid: {err_msg}")
                
                if verbose:
                    print(f"[AUDITOR] Complete: {audit_result.status} ({len(audit_result.issues)} issues)")
                
                return audit_result
            
            # Continue loop with tool result
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": json.dumps(result),
            })
        
        if tool_results:
            messages.append({"role": "user", "content": tool_results})
        else:
            raise AuditorError("No tool calls in response")
    
    raise AuditorError(f"Auditor hit MAX_ITERATIONS ({MAX_ITERATIONS})")
```

### 3. Auditor Tools (`tools.py`)

```python
"""Tools available to auditor agent."""

import json
from audit_schema import AuditResult

TOOL_DEFINITIONS = [
    {
        "name": "audit_complete",
        "description": "Report audit completion with results.",
        "input_schema": {
            "type": "object",
            "properties": {
                "passed": {
                    "type": "boolean",
                    "description": "Whether audit passed (no critical issues)"
                },
                "status": {
                    "type": "string",
                    "enum": ["passed", "failed", "warned"],
                },
                "issues": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "check_name": {"type": "string"},
                            "severity": {"type": "string", "enum": ["info", "warning", "error"]},
                            "message": {"type": "string"},
                            "remediation": {"type": "string"},
                        },
                        "required": ["check_name", "severity", "message"],
                    },
                },
                "recommendations": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "priority": {"type": "string", "enum": ["low", "medium", "high"]},
                            "suggestion": {"type": "string"},
                        },
                        "required": ["priority", "suggestion"],
                    },
                },
            },
            "required": ["passed", "status"],
        },
    }
]

def dispatch_tool(tool_name: str, tool_input: dict) -> dict:
    """Execute a tool call."""
    if tool_name == "audit_complete":
        return {
            "success": True,
            "audit_result": {
                "passed": tool_input["passed"],
                "status": tool_input["status"],
                "issues": tool_input.get("issues", []),
                "recommendations": tool_input.get("recommendations", []),
            }
        }
    
    return {"error": f"Unknown tool: {tool_name}"}
```

### 4. Context Loader (`context_loader.py`)

```python
"""Load audit context and system prompts."""

from pathlib import Path

def build_audit_system_prompt(mode: str, contract_fetcher=None) -> list[dict]:
    """
    Build the system prompt for auditor in specified mode.
    
    Returns list of content blocks (for prompt caching).
    """
    blocks = []
    
    # Mode-specific mandate
    if mode == "plan":
        mandate = """\
You are the PLANNER AUDITOR — an independent agent that audits the plan output
from the Planner before it goes to the Builder.

Your job:
1. Audit the plan for consistency (contracts agree with each other)
2. Audit for completeness (nothing missing)
3. Audit for feasibility (is this buildable?)

You do NOT build. You only verify the plan is sound."""
    elif mode == "phase":
        mandate = """\
You are the BUILDER AUDITOR — an independent agent that audits phase outputs
from the Builder.

Your job:
1. Check boundary violations (routers shouldn't import repos, etc.)
2. Check test coverage (meets threshold?)
3. Check code quality (naming, documentation, patterns)
4. Check against builder_contract standards

You do NOT build. You only verify the code is sound."""
    else:
        raise ValueError(f"Unknown mode: {mode}")
    
    blocks.append({"type": "text", "text": mandate})
    
    # Load governance contracts (if fetcher provided)
    if contract_fetcher and mode == "plan":
        auditor_prompt = contract_fetcher("auditor_prompt")
        audit_reference = contract_fetcher("audit_reference")
        blocks.append({"type": "text", "text": f"AUDITOR MANDATE:\n{auditor_prompt}"})
        blocks.append({"type": "text", "text": f"AUDIT REFERENCE:\n{audit_reference}"})
    elif contract_fetcher and mode == "phase":
        builder_contract = contract_fetcher("builder_contract")
        boundaries = contract_fetcher("boundaries")
        blocks.append({"type": "text", "text": f"BUILDER CONTRACT:\n{builder_contract}"})
        blocks.append({"type": "text", "text": f"BOUNDARIES:\n{boundaries}"})
    
    return blocks
```

### 5. Run Auditor (`run_auditor.py`)

```python
"""Entry point for running auditor as standalone service."""

import asyncio
import sys
from argparse import ArgumentParser
from uuid import UUID

from auditor_agent import run_auditor

async def main():
    parser = ArgumentParser(description="Run Forge auditor")
    parser.add_argument("--mode", choices=["plan", "phase"], required=True)
    parser.add_argument("--build-id", type=UUID, required=True)
    parser.add_argument("--project-id", type=UUID, required=True)
    parser.add_argument("--plan-json", type=str, help="JSON plan (mode=plan)")
    parser.add_argument("--phase", type=int, help="Phase number (mode=phase)")
    parser.add_argument("--verbose", action="store_true")
    
    args = parser.parse_args()
    
    # TODO: Wire in contract_fetcher (from ForgeGuard app)
    result = await run_auditor(
        mode=args.mode,
        build_id=args.build_id,
        project_id=args.project_id,
        plan_json=json.loads(args.plan_json) if args.plan_json else None,
        phase_number=args.phase,
        verbose=args.verbose,
    )
    
    print(f"Result: {result.json(indent=2)}")

if __name__ == "__main__":
    asyncio.run(main())
```

---

## Integration Points

### 1. Planner Integration

**File:** `planner/planner_agent.py` (modify the loop)

**Current (broken):**
```python
if block.name == "write_plan":
    ok = result.get("success", False)
    if not ok:
        # self-audit failed, retry
        messages.append(...)
```

**New flow:**
```python
if block.name == "write_plan":
    ok = result.get("success", False)
    if ok:
        # Plan written successfully
        # Now invoke external auditor
        from auditor.auditor_agent import run_auditor
        audit_result = await run_auditor(
            mode="plan",
            build_id=build_id,
            project_id=project_id,
            plan_json=result["plan_json"],
            contract_fetcher=contract_fetcher,
            verbose=verbose,
        )
        
        # Save ledger
        ledger_id = await save_audit_ledger(
            build_id=build_id,
            project_id=project_id,
            agent_name="planner_auditor",
            status=audit_result.status,
            passed_checks=...,
            failed_checks=...,
            issued=audit_result.issues,
            token_usage=audit_result.token_usage,
            duration_seconds=audit_result.duration_seconds,
        )
        
        if audit_result.passed:
            # Proceed to builder
            should_exit = True
        else:
            # Audit failed: inject issues back to planner for re-write
            messages.append({"role": "user", "content": f"""Your plan was rejected by the auditor.
Issues:
{json.dumps(audit_result.issues, indent=2)}

Please revise the plan to address these issues."""})
            continue  # Planner retries
```

### 2. Builder Integration

**File:** `builder/builder_agent.py` (after each phase)

**Pseudocode:**
```python
for phase_num in range(num_phases):
    # Execute phase
    phase_output = await execute_phase(phase_num)
    
    # Invoke auditor
    from auditor.auditor_agent import run_auditor
    audit_result = await run_auditor(
        mode="phase",
        build_id=build_id,
        project_id=project_id,
        phase_number=phase_num,
        phase_files=phase_output["files"],
        test_coverage=phase_output.get("coverage", 0),
        violations=phase_output.get("violations", []),
    )
    
    # Save ledger
    await save_audit_ledger(...)
    
    # Decide on remediation
    if audit_result.status == "failed":
        # Critical issues: halt
        logger.error(f"Phase {phase_num} audit failed: {audit_result.issues}")
        raise BuilderError("Audit halt triggered")
    elif audit_result.status == "warned":
        # Minor issues: auto-fix or skip
        for issue in audit_result.issues:
            if issue["severity"] == "info":
                continue  # Ignore info
            elif issue["severity"] == "warning" and auto_fixable(issue):
                await auto_fix(issue)
            else:
                logger.warning(f"Phase {phase_num}: {issue['message']}")
```

### 3. watch_audit.py (Forge Internal Tool)

**File:** `scripts/watch_audit.py` or `ForgeGuard/watch_audit.py`

```python
#!/usr/bin/env python3
"""
watch_audit.py — Watch a build and run audits after each phase.

For Forge internals use only. Tails Builder output and invokes BuilderAuditor.

Usage:
  python watch_audit.py --build-id=<uuid> --project-id=<uuid> [--config=config.json]
"""

import asyncio
import logging
from uuid import UUID

# Configuration: where to subscribe to build events
BUILD_EVENT_STREAM = "ws://localhost:8000/ws/build/{build_id}"

async def main(build_id: UUID, project_id: UUID):
    """Watch build and audit each phase."""
    logging.info(f"Watching build {build_id}")
    
    # Subscribe to builder event stream
    async with subscribe_to_build(build_id) as stream:
        async for event in stream:
            if event["type"] == "phase_complete":
                phase_num = event["phase_number"]
                logging.info(f"Phase {phase_num} complete — auditing")
                
                # Invoke builder auditor
                from auditor.auditor_agent import run_auditor
                result = await run_auditor(
                    mode="phase",
                    build_id=build_id,
                    project_id=project_id,
                    phase_number=phase_num,
                    phase_files=event["generated_files"],
                    test_coverage=event.get("test_coverage"),
                    violations=event.get("violations"),
                )
                
                # Log result
                await log_audit_result(result)
                
                # Notify builder of result (halt if critical)
                should_continue = await notify_builder(build_id, result)
                if not should_continue:
                    logging.error("Builder halted by audit")
                    break

if __name__ == "__main__":
    import sys
    build_id = UUID(sys.argv[1])
    project_id = UUID(sys.argv[2])
    asyncio.run(main(build_id, project_id))
```

---

## Implementation Phases

### Phase 1: Database & Models (Week 1)
- [ ] Create Alembic migration (`027_create_audit_ledgers_table.py`)
- [ ] Add Pydantic models (`app/models.py`)
- [ ] Add repository methods (`app/repos/project_repo.py`)
- [ ] Run migration locally and in staging

### Phase 2: Auditor Agent (Week 2)
- [ ] Create `auditor/` folder with all files
- [ ] Implement `auditor_agent.py` (core loop)
- [ ] Implement `audit_schema.py` (validation)
- [ ] Implement `tools.py` (audit_complete tool)
- [ ] Implement `context_loader.py` (prompt building)
- [ ] Test auditor in isolation (unit tests)

### Phase 3: Planner Integration (Week 3)
- [ ] Modify `planner/planner_agent.py` to call external auditor
- [ ] Update planner loop to handle audit failures + retries
- [ ] Update `planner/tools.py` if needed
- [ ] Test: planner → auditor → ledger flow
- [ ] Remove self-audit code from planner

### Phase 4: Builder Integration (Week 4)
- [ ] Modify `builder/builder_agent.py` to call auditor per-phase
- [ ] Implement severity-based remediation (auto-fix vs halt)
- [ ] Test: builder phase → auditor → ledger flow
- [ ] Add phase-level remediation logic

### Phase 5: watch_audit.py (Week 5)
- [ ] Create `watch_audit.py` entry point
- [ ] Wire subscription to builder events
- [ ] Test: watch_audit monitoring live build

### Phase 6: Boot & Startup Scripts (Week 6)
- [ ] Update contracts to include startup script generation
- [ ] Update `builder_directive` template
- [ ] Create `boot.py` and `run_audit.py` generators
- [ ] Test: generated scripts work on Linux/macOS/Windows

### Phase 7: Ledger Export & GitHub (Week 7)
- [ ] Implement ledger export to markdown
- [ ] Wire GitHub commit after build
- [ ] Verify ledger appears in `builds/<build_id>/audit/ledger.md`

---

## Files to Create

```
auditor/
  __init__.py                      (empty)
  PLAN.md                          (this file)
  auditor_agent.py                 (main loop)
  audit_schema.py                  (pydantic models)
  tools.py                         (tool definitions)
  context_loader.py                (prompt building)
  run_auditor.py                   (entry point)
  tests/
    test_auditor_agent.py
    test_audit_schema.py

db/alembic/versions/
  027_create_audit_ledgers_table.py (migration)
```

## Files to Modify

```
app/models.py                      (ADD audit models)
app/repos/project_repo.py          (ADD audit methods)
planner/planner_agent.py           (MODIFY loop, add auditor call)
builder/builder_agent.py           (MODIFY loop, add auditor call)
scripts/watch_audit.py             (CREATE OR UPDATE)
```

---

## Success Criteria

- [ ] Auditor runs independently, not self-auditing
- [ ] Planner audit failure triggers planner re-write (up to 3 times)
- [ ] Builder audit failure halts build with specific violations
- [ ] Audit ledger recorded in DB for every audit
- [ ] Audit ledger exported to GitHub after build
- [ ] watch_audit.py monitors during build and reports results
- [ ] Boot.py generated and works on Linux/macOS/Windows
- [ ] Zero self-audit bias (auditor is completely separate agent)

---

## Risks & Mitigations

| Risk | Impact | Mitigation |
|------|--------|-----------|
| Auditor gets into loop (100+ iterations) | Token waste | MAX_ITERATIONS=5, safety valve |
| Audit takes longer than builder | Slow builds | Parallel auditing (auditor runs while builder builds next phase) |
| Ledger grows too large | DB bloat | Prune old audits after 30 days (configurable) |
| Auditor rejects plans incorrectly | Blocks legitimate builds | Use audit_reference.md to define clear standards, test with examples |

---

## Questions for Review

1. Should auditor query prior audits to improve recommendations?
2. Should audit failures feed back to planner or just block with error message?
3. Should watch_audit.py run as a separate service or inline with builder?
4. Should we add confidence scores to audit results ("80% confident no boundary violations")?

---

**Ready for implementation. Questions?**
