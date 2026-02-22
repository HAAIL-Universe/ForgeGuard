# Builder Agent Plan
_Created: 2026-02-22 | Status: PENDING APPROVAL_

---

## Context

The current build service (`build_service.py`) contains two distinct modes:

| Mode | Function | Lines | Status |
|------|----------|-------|--------|
| `conversation` | `_run_build_conversation()` | 2,695–4,033 (1,339 lines) | **LEGACY — remove** |
| `plan_execute` | `_run_build_plan_execute()` | 4,727–7,387 (2,661 lines) | Active — but builder phase needs stubbing |

The `plan_execute` function has two distinct halves:
- **Planner half** (workspace setup → IDE gate → planner agent → plan review gate → `commit_plan_to_git`) — **KEEP**
- **Builder half** (file generation loop → verification → incremental commits) — **STUB OUT NOW, replace later**

The builder half is what causes the race condition with `/push`: after plan approval it starts making incremental commits every 3 files, which conflicts with the user manually pushing.

---

## Phase 1 — This Iteration (Immediate)

### Goal
Remove the legacy conversation loop, stub out the builder half of plan_execute, and create a clean plug-in point for the future builder agent. No race conditions. No dead code.

### Changes

#### 1. `app/services/build_service.py`

**a) Remove `_run_build_conversation()` entirely**
- Lines 2,695–4,033 (1,339 lines of legacy single-conversation builder)
- Only called from `build_task()` when `mode != "plan_execute"`
- Zero other references in the codebase

**b) Simplify `build_task()` dispatcher (line ~2,632)**
- Remove the `BUILD_MODE` branch — always call `_run_build_plan_execute()`
- Before:
  ```python
  if mode == "plan_execute" and working_dir:
      await _run_build_plan_execute(...)
  else:
      await _run_build_conversation(...)
  ```
- After:
  ```python
  await _run_build_plan_execute(...)
  ```

**c) In `_run_build_plan_execute()` — stub out the builder phase**

The plan review gate at line ~5,168 is the dividing line.

After `commit_plan_to_git()` completes and the plan is saved to git, instead of entering the file generation loop:
- Broadcast a `build_log` event: `"✅ Plan saved to git. Builder agent not yet integrated — build paused at planning stage."`
- Set build status to `"planned"` in DB
- Broadcast `build_paused` event with `reason="awaiting_builder_agent"`
- Return cleanly (do NOT enter the file generation loop)

This means the build lifecycle becomes:
```
start_build → workspace setup → IDE gate → planner agent → plan review gate
    → commit_plan_to_git → ✅ STOP (status: "planned")
```

**d) Add `"planned"` as a valid build status**
- Check if `"planned"` is already in the DB schema enum; add it if not
- The build can be resumed later when the builder agent is integrated

#### 2. `app/config.py`
- Remove `BUILD_MODE` setting (no longer needed — plan_execute is the only mode)

#### 3. `app/services/build/builder_agent.py` (NEW FILE)

Create a clean interface stub:

```python
"""
Builder Agent Interface
=======================
This module is the plug-in point for the ForgeGuard builder agent.

When the builder agent is implemented, replace `generate_file()` with
a real agent loop that:
  - Accepts the file entry + contracts + context
  - Runs tool-using agent turns (read → write → test → iterate)
  - Returns the completed file content as a string

Integration point in build_service.py:
  - _run_build_plan_execute() → file generation loop → generate_file()
"""

from pathlib import Path
from typing import Any


async def generate_file(
    build_id: str,
    user_id: str,
    api_key: str,
    file_entry: dict[str, Any],
    contracts: list[str],
    context: list[str],
    phase_deliverables: list[str],
    working_dir: str | Path,
    phase_plan_context: str = "",
) -> str:
    """Generate a single file's content.

    Returns the complete file content as a string (no markdown fences).

    Raises:
        NotImplementedError: Builder agent not yet implemented.
    """
    raise NotImplementedError(
        "Builder agent not yet implemented. "
        "Implement this function in Phase 2 (builder agent integration)."
    )
```

#### 4. `Forge/Plans/builder_agent_plan.md` (THIS FILE)
Already created. Updated after Phase 1 ships.

#### 5. `tests/test_builder_agent_stub.py` (NEW)
- Test that `generate_file()` raises `NotImplementedError`
- Test that a completed build with `commit_plan_to_git()` resolves to `"planned"` status
- Test that the build does NOT enter file generation after plan approval

---

## Phase 2 — Builder Agent (Future)

### Architecture (to be designed when ready)

The builder agent replaces `_generate_single_file()` / `generate_file()` with a proper agentic loop:

```
For each file in phase manifest:
  ├─ Read-sibling agent: reads relevant existing files for context
  ├─ Write-sibling agent: generates file content via tool-using loop
  │    ├─ read_file tool (via workspace)
  │    ├─ write_file tool (via workspace)
  │    ├─ run_tests tool (via workspace)
  │    └─ iterate until tests pass or max_turns
  └─ Returns: final file content string
```

**Key decisions deferred to Phase 2:**
- Tool set for the builder agent (file read/write, test runner, linter)
- Read-sibling vs write-sibling separation
- GitHub Git Trees API for write-via-API (eliminates local git coordination)
- Max turns / token budget per file
- Error recovery (retry vs skip vs abort phase)

### Integration Point
Single function call in `_run_build_plan_execute()`:
```python
# Currently: await _generate_single_file(...)
# Future:    await builder_agent.generate_file(...)
```
Same signature. Same return type (`str`). Drop-in replacement.

---

## Phase 3 — Git Write API (Further Future)

When the builder agent ships:
- Evaluate GitHub Git Trees API for atomic multi-file commits
- Eliminates need for local workspace git operations
- Each phase becomes: plan → generate all files → single atomic tree commit → push
- Deferred until builder agent architecture is finalized

---

## Invariants (Must Not Break)

1. Planner half of plan_execute must remain fully functional
2. `commit_plan_to_git()` must still be called on plan approval
3. All slash commands (`/stop`, `/reset`, `/push`, `/plan`, `/start`) must still work
4. WebSocket events must still fire (workspace_ready, forge_ide_ready, build_overview, etc.)
5. IDE gate and plan review gate must still work correctly
6. Build watchdog must still run (kills stalled builds after 15 min)
7. No race condition between build lifecycle and `/push`

---

## What Changes vs What Stays

| Component | Change |
|-----------|--------|
| Workspace setup | **KEEP** — git clone, branch, .forge/ dir |
| IDE ready gate | **KEEP** — waits for /start or /prep |
| Planner agent (Sonnet) | **KEEP** — generates phase manifest |
| Plan review gate | **KEEP** — waits for user approval |
| `commit_plan_to_git()` | **KEEP** — saves plan to git |
| File generation loop | **STUB** → NotImplementedError stub |
| Incremental commits (every 3 files) | **REMOVED** with file generation loop |
| Verification (`_verify_phase_output`) | **KEEP** — needed when builder ships |
| Audit/fix background tasks | **KEEP** — needed when builder ships |
| All slash commands | **KEEP** — all 13 still functional |
| WebSocket events | **KEEP** — all still emitted |
| `_run_build_conversation()` | **REMOVE** — 1,339 lines of legacy dead code |
| `BUILD_MODE` config setting | **REMOVE** — always plan_execute now |

---

## Files Touched in Phase 1

| File | Change type | Estimated diff |
|------|-------------|----------------|
| `app/services/build_service.py` | Edit (delete + stub) | -1,400 / +15 lines |
| `app/services/build/builder_agent.py` | New | +40 lines |
| `app/config.py` | Edit (remove setting) | -3 lines |
| `tests/test_builder_agent_stub.py` | New | +60 lines |

_Total: net reduction of ~1,350 lines. Zero new dependencies._
