# Builder Agent — Implementation Plan

## Context

ForgeGuard can plan but cannot build. `app/services/build/builder_agent.py` is a stub
that raises `NotImplementedError`. Everything downstream (file generation, workspace
commits, build status) is blocked until this is wired up.

The `planner/` module is the architectural template: a standalone, CLI-runnable agent
with its own loop, tools, and context loader. The builder mirrors this pattern exactly,
then wires into the existing `build_service.py` integration point.

**Critical discovery:** The sub-agent infrastructure (`subagent.py`) is already fully
built — SCOUT, CODER (single-shot), AUDITOR, and FIXER roles all exist with tool
allowlists, handoff/result dataclasses, and `run_sub_agent()`. The builder orchestrator
just needs to wire them into a pipeline.

---

## Target Structure

```
builder/                           ← new standalone module (mirrors planner/)
├── builder_agent.py               ← main orchestrator loop
├── run_builder.py                 ← CLI entry point
├── context_loader.py              ← system prompt builder
├── tools.py                       ← minimal tool shims (delegates to BUILDER_TOOLS)
└── phases.md                      ← this plan (placed here after approval)

app/services/build/builder_agent.py  ← integration shim (replace NotImplementedError)
```

---

## Critical Files

| File | Role |
|---|---|
| `planner/planner_agent.py` | Template — mirror the loop pattern exactly |
| `app/services/build/subagent.py` | Reuse `run_sub_agent()`, `SubAgentHandoff`, `SubAgentResult`, `SubAgentRole` |
| `app/services/build/builder_agent.py` | Integration shim — replace `NotImplementedError` |
| `app/services/build/build_service.py` | Integration point — `_run_build_plan_execute()`, marker `"BUILDER AGENT INTEGRATION POINT"` |
| `app/services/build/context.py` | Reuse `BuildContext` for shared state |
| `app/clients/agent_client.py` | Reuse `stream_agent()`, `ApiKeyPool` |
| `tests/test_builder_agent_stub.py` | Existing test — must remain green (not break) |

---

## Phase 1 — Standalone builder/ scaffold ✅ COMPLETE

**Files created:**
- `builder/context_loader.py` — `build_system_prompt(file_entry, contracts)`
- `builder/tools.py` — `TOOL_DEFINITIONS = []`, `dispatch_tool()` stub
- `builder/run_builder.py` — CLI entry point
- `builder/phases.md` — this document

---

## Phase 2 — Builder orchestrator (`builder/builder_agent.py`) ✅ COMPLETE

Core function: `run_builder(file_entry, contracts, context, phase_deliverables,
working_dir, build_id, user_id, api_key, ...) -> BuilderResult`

**Pipeline (Scout → Coder → Auditor → Fixer):**

1. SCOUT  → gather context for the target file
2. CODER  → generate file content (single-shot mode)
3. AUDITOR → verify structural quality against contracts
4. FIXER  → apply surgical fixes if AUDITOR returns FAIL (max 2 retries)

**BuilderResult dataclass:**
```python
@dataclass
class BuilderResult:
    file_path: str
    content: str
    status: str   # "completed" | "failed"
    error: str = ""
    token_usage: dict = field(default_factory=dict)
    sub_agent_results: list = field(default_factory=list)
    iterations: int = 0
```

---

## Phase 3 — Integration shim ✅ COMPLETE

**File:** `app/services/build/builder_agent.py`

Replaced `NotImplementedError` with a real call to `run_builder()`.
`BuilderError` is re-exported from `builder.builder_agent` for callers.

---

## Phase 4 — Tests ✅ COMPLETE

- `tests/test_builder_agent.py` — 5 new tests (pipeline, stop_event, fixer, tokens)
- `tests/test_builder_agent_stub.py` — updated: `NotImplementedError` test → mocked success

---

## Verification

```bash
# 1. Run builder-specific tests
pytest tests/test_builder_agent.py -v

# 2. Confirm no regression in build suite
pytest tests/test_build_service.py tests/test_plan_execute.py -v

# 3. CLI smoke test (requires ANTHROPIC_API_KEY + a plan.json from planner)
cd builder && python run_builder.py ../Forge/Plans/<latest_plan>.json --phase 1 --file <first_file>

# 4. Full test suite baseline
pytest tests/ -x -q
```
