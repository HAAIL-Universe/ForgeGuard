# MCP-Driven Builder Architecture — Detailed Implementation Plan

> **Goal**: Replace the 30-60K token contract dump in the first user message with
> on-demand MCP tool calls. The builder fetches exactly what it needs, when it
> needs it, governed by a lean system prompt that encodes the AEM workflow.

---

## 0. Current Architecture (What We're Replacing)

### The Problem

| Component | Tokens | Where | Persists? |
|-----------|-------:|-------|-----------|
| `builder_contract.md` (universal governance) | ~10K | First user msg | ✅ All turns |
| `blueprint.md` | ~1.1K | First user msg | ✅ All turns |
| `manifesto.md` | ~0.8K | First user msg | ✅ All turns |
| `stack.md` | ~0.6K | First user msg | ✅ All turns |
| `schema.md` | ~2.5K | First user msg | ✅ All turns |
| `physics.yaml` | ~3.6K | First user msg | ✅ All turns |
| `boundaries.json` | ~1.1K | First user msg | ✅ All turns |
| `ui.md` | ~1.9K | First user msg | ✅ All turns |
| `builder_directive.md` | ~1.5K | First user msg | ✅ All turns |
| Phase window (2 phases) | ~2K | First user msg | ✅ All turns |
| System prompt | ~1.5K | System block | ✅ All turns |
| **Total** | **~27K** | | |

These ~27K tokens sit in the context window for *every single turn* of
potentially 50+ turns. They're Anthropic-cached (cheaper on reads), but still
consume the 200K context window. After compaction, `messages[0]` (the contract
dump) is **always preserved** — it's the one message that never gets trimmed.

### The Flow Today

```
1. _build_directive(contracts)     → concatenate all contracts except phases
2. _extract_phase_window(0)        → extract Phase 0+1 from phases.md
3. first_message = directive + workspace_info + phase_window
4. messages = [{ role: "user", content: first_message }]
5. system_prompt = 70-line behavioral prompt (tools, workflow, rules)
6. while True: stream_agent(system_prompt, messages, tools=BUILDER_TOOLS)
```

---

## 1. New Architecture Overview

```
┌──────────────────────────────────────────────────────────────┐
│  SYSTEM PROMPT  (~4-5K tokens)                               │
│  ┌─────────────────────────────────────────────────────────┐ │
│  │ Identity + AEM 7-step workflow                          │ │
│  │ Critical governance rules (extracted from builder_ctr)  │ │
│  │ Tool catalogue (existing 8 + new 5 forge tools)         │ │
│  │ Phase workflow (PLAN → code → test → SIGN-OFF)          │ │
│  │ "START by calling forge_get_phase_window(0)"            │ │
│  └─────────────────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────┐
│  FIRST USER MESSAGE  (~1-2K tokens)                          │
│  ┌─────────────────────────────────────────────────────────┐ │
│  │ Project name + description (from blueprint title)       │ │
│  │ Workspace file listing                                  │ │
│  │ "Begin Phase 0. Use forge tools to fetch contracts."    │ │
│  └─────────────────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────┐
│  BUILDER TOOLS  (13 total)                                   │
│  ┌──────────────────┐  ┌──────────────────────────────────┐ │
│  │ Existing 8:       │  │ New Forge 5:                     │ │
│  │ read_file         │  │ forge_get_contract(name)         │ │
│  │ list_directory    │  │ forge_get_phase_window(phase_num)│ │
│  │ search_code       │  │ forge_list_contracts()           │ │
│  │ write_file        │  │ forge_get_summary()              │ │
│  │ edit_file         │  │ forge_scratchpad(op, key, value) │ │
│  │ run_tests         │  │                                  │ │
│  │ check_syntax      │  │                                  │ │
│  │ run_command        │  │                                  │ │
│  └──────────────────┘  └──────────────────────────────────┘ │
└──────────────────────────────────────────────────────────────┘
```

**Token budget comparison**:

| | Current | New (Turn 1) | New (Turns 2+) |
|---|---:|---:|---:|
| System prompt | 1.5K | 4.5K | 4.5K (cached) |
| First message (contracts) | 27K | 0 | 0 |
| First message (workspace) | 1.5K | 1.5K | 1.5K (cached) |
| Tool results (contracts fetched) | 0 | ~8-12K | ~2-4K |
| Tool definitions | ~2K | ~3.5K | ~3.5K (cached) |
| **Total fixed context** | **~32K** | **~6K** | **~6K** |

**Savings**: ~26K tokens freed from permanent context. With a 200K window,
that's 13% more room for actual build conversation. After compaction, the
savings are even larger — tool results from earlier turns get compacted away,
and the builder can re-fetch via tools if needed.

---

## 2. New MCP Tools for the Builder

### 2a. `forge_get_contract` (EXISTING — reuse as-is)

Already in `forge_ide/mcp/tools.py`. Takes `{name: string}`, returns full
contract content. Handles all 15 contract types including `builder_contract`,
`blueprint`, `manifesto`, `stack`, `schema`, `physics`, `boundaries`, `ui`,
`builder_directive`, `phases`, etc.

**For the builder**: We expose this directly. The builder calls it when it
needs a specific contract. E.g., before coding Phase 3, it might call
`forge_get_contract("physics")` to check the API spec.

**Key choice**: We do NOT expose the raw 230KB `phases` contract. Instead,
we provide `forge_get_phase_window` (below). If the builder calls
`forge_get_contract("phases")`, we should return a helpful error:
*"Use forge_get_phase_window(phase_number) instead — the full phases contract
is too large for context."*

### 2b. `forge_get_phase_window` (NEW)

```json
{
  "name": "forge_get_phase_window",
  "description": "Get the deliverables for a specific build phase (current + next phase). Call this at the START of each phase to understand what to build.",
  "input_schema": {
    "type": "object",
    "properties": {
      "phase_number": {
        "type": "integer",
        "description": "The phase number to retrieve (0-based). Returns this phase + the next phase for context."
      }
    },
    "required": ["phase_number"]
  }
}
```

**Implementation**: Reuses `_extract_phase_window()` logic from `context.py`.
Reads `phases.md` from `Forge/Contracts/` in the working directory, splits
by `## Phase`, returns the window. ~1-3K tokens per call vs 230K for full file.

### 2c. `forge_list_contracts` (EXISTING — reuse as-is)

Already exists. Returns names + filenames + formats of all available contracts.
The builder calls this if it needs to discover what contracts exist.

### 2d. `forge_get_summary` (EXISTING — reuse as-is)

Returns a compact overview of the governance framework. Good for the builder's
first call to orient itself without fetching every contract.

### 2e. `forge_scratchpad` (NEW)

```json
{
  "name": "forge_scratchpad",
  "description": "Persistent scratchpad for storing reasoning, decisions, and notes that survive context compaction. Use this to record architectural decisions, known issues, and progress notes that you'll need in later phases.",
  "input_schema": {
    "type": "object",
    "properties": {
      "operation": {
        "type": "string",
        "enum": ["read", "write", "append", "list"],
        "description": "Operation: read (get value), write (set value), append (add to value), list (show all keys)"
      },
      "key": {
        "type": "string",
        "description": "Key name (e.g. 'architecture_decisions', 'phase_2_issues'). Required for read/write/append."
      },
      "value": {
        "type": "string",
        "description": "Value to write or append. Required for write/append."
      }
    },
    "required": ["operation"]
  }
}
```

**Implementation**: In-memory dict per build (keyed by `build_id`), persisted
to the build's working directory as `Forge/.scratchpad.json` for durability.
Entries survive context compaction because they're stored outside the conversation.

**Use cases**:
- Builder records architectural decisions in Phase 0, references them in Phase 5
- Builder notes known test issues so it doesn't retry the same fix
- Builder tracks which contracts it already read (avoids redundant fetches)
- Recovery planner stores remediation hints

---

## 3. New System Prompt Design

The system prompt becomes the single governance document. It replaces both the
current 70-line system prompt AND the 27K contract dump. Target: ~4-5K tokens.

### Structure

```
# Forge Autonomous Builder

You are an autonomous software builder operating under the Forge governance
framework. You build projects phase-by-phase using the tools below.

## Your Tools

### Project Tools
- **read_file**(path): Read a file from the project.
- **list_directory**(path): List files/folders in a directory.
- **search_code**(pattern, glob?): Search for patterns across files.
- **write_file**(path, content): Write or overwrite a file.
- **edit_file**(path, edits): Apply surgical edits to an existing file.
- **run_tests**(command, timeout?): Run the test suite.
- **check_syntax**(file_path): Check a file for syntax errors.
- **run_command**(command, timeout?): Run safe shell commands.

### Forge Contract Tools
- **forge_get_summary**(): Overview of governance framework. Call FIRST.
- **forge_list_contracts**(): List all available contracts.
- **forge_get_contract**(name): Read a specific governance contract.
- **forge_get_phase_window**(phase_number): Get current + next phase deliverables.
- **forge_scratchpad**(operation, key?, value?): Persistent notes across phases.

## AEM (Autonomous Execution Mode) — The Build Loop

For EACH phase, follow these steps exactly:

### Step 1: Fetch Phase Context
Call `forge_get_phase_window(N)` where N is the current phase number.
Read any contracts relevant to this phase's deliverables:
- Always read: `blueprint` (what to build), `stack` (tech requirements)
- If writing APIs: `physics` (endpoint spec), `boundaries` (layer rules)
- If writing UI: `ui` (design contract)
- If first phase: `manifesto` (project ethos), `schema` (data model)

### Step 2: Plan
Emit a structured plan for THIS phase only:
=== PLAN ===
1. First task for this phase
2. Second task for this phase
...
=== END PLAN ===
Do NOT plan future phases. Use `forge_scratchpad(write, ...)` to record
any decisions you'll need later.

### Step 3: Build
Write code using `write_file` and `edit_file`. After each file:
- Call `check_syntax` to catch errors immediately.
- Do NOT explore the filesystem unnecessarily — the workspace listing
  is in the first message, and you can `list_directory` if needed.

### Step 4: Mark Progress
After completing each plan task, emit: === TASK DONE: N ===

### Step 5: Verify
Run `run_tests` with the project's test command. If tests fail:
- Read the error output carefully
- Fix with `edit_file` or `write_file`
- Re-run tests
- Repeat until tests pass (max 3 attempts per issue)

### Step 6: Phase Sign-Off
When ALL tasks are done and tests pass, emit:
=== PHASE SIGN-OFF: PASS ===
Phase: {phase_name}
Deliverables: {comma-separated list}
Tests: PASS
=== END PHASE SIGN-OFF ===

### Step 7: Next Phase
After sign-off, a new message will arrive with the next phase context.
Start again from Step 1.

## Critical Rules

1. **Minimal Diff**: Only change what the phase requires. No renames,
   no cleanup, no unrelated refactors.
2. **Boundary Enforcement**: Routers → Services → Repos. No skipping layers.
   Read `boundaries` contract if unsure.
3. **Contract Exclusion**: NEVER include Forge contract content, references,
   or metadata in committed source files, READMEs, or code comments.
   The `Forge/` directory is server-side only.
4. **Evidence**: Every change must be traceable to a phase deliverable.
5. **STOP Codes**: If you encounter an unresolvable issue, emit one of:
   EVIDENCE_MISSING, AMBIGUOUS_INTENT, CONTRACT_CONFLICT,
   RISK_EXCEEDS_SCOPE, NON_DETERMINISTIC_BEHAVIOR, ENVIRONMENT_LIMITATION
6. **README**: Before the final phase, write a comprehensive README.md.

## First Turn

On your VERY FIRST response:
1. Call `forge_get_phase_window(0)` to get Phase 0 deliverables
2. Call `forge_get_contract("blueprint")` and `forge_get_contract("stack")`
3. Emit === PLAN === for Phase 0
4. Start writing code immediately
```

### Why This Works

- **~4.5K tokens** vs ~27K in the old contract dump
- Critical rules are inline (always in context)
- Detail rules (physics YAML, boundaries JSON, schema) are fetched on-demand
- The 38KB `builder_contract.md` is no longer injected — its key rules are
  distilled into the "Critical Rules" section, with the full document available
  via `forge_get_contract("builder_contract")` if the builder needs specifics
- Phase deliverables are fetched per-phase, not pre-loaded

---

## 4. First Message Redesign

### Current (27K+ tokens)

```python
first_message = (
    "## ⚠ IMPORTANT — DO NOT EXPLORE\n"
    "Everything you need is in this message...\n\n"
    + directive            # ← 25K tokens of concatenated contracts
    + workspace_info       # ← 1K file listing
    + phase_window         # ← 2K Phase 0+1
)
```

### New (~1.5K tokens)

```python
first_message = (
    f"# Project: {project_name}\n\n"
    f"{project_description}\n\n"       # ← from blueprint first line
    + workspace_info                    # ← file listing (same as before)
    + "\n\nBegin Phase 0. Use your forge tools to fetch the contracts "
    "you need, then emit your === PLAN === and start building.\n"
)
```

**No contracts. No phase window. No governance dump.** The builder fetches
everything via tool calls on its first turn.

---

## 5. Implementation Steps (Ordered)

### Step 1: Add Forge Tools to `tool_executor.py`

**File**: `app/services/tool_executor.py`

Add 5 new tool handlers + tool definitions to `BUILDER_TOOLS`:

```python
# New forge tool handlers
def _exec_forge_get_summary(tool_input: dict, working_dir: str) -> str:
    """Return governance framework overview."""
    from forge_ide.mcp.local import get_summary
    return json.dumps(get_summary(), indent=2)

def _exec_forge_list_contracts(tool_input: dict, working_dir: str) -> str:
    """List available contracts."""
    from forge_ide.mcp.local import list_contracts
    return json.dumps(list_contracts(), indent=2)

def _exec_forge_get_contract(tool_input: dict, working_dir: str) -> str:
    """Read a specific contract from working_dir/Forge/Contracts/."""
    name = tool_input.get("name", "")
    if name == "phases":
        return "Error: Use forge_get_phase_window(phase_number) instead — the full phases contract is too large."
    from forge_ide.mcp.local import load_contract
    result = load_contract(name)
    if "error" in result:
        return f"Error: {result['error']}"
    content = result.get("content", "")
    if isinstance(content, (dict, list)):
        return json.dumps(content, indent=2)
    return str(content)

def _exec_forge_get_phase_window(tool_input: dict, working_dir: str) -> str:
    """Extract current + next phase from phases contract."""
    phase_num = tool_input.get("phase_number", 0)
    # Read phases from working_dir/Forge/Contracts/phases.md
    phases_path = Path(working_dir) / "Forge" / "Contracts" / "phases.md"
    if not phases_path.exists():
        return "Error: phases.md not found in Forge/Contracts/"
    content = phases_path.read_text(encoding="utf-8")
    # Reuse _extract_phase_window logic ...
    return windowed_text

# Scratchpad: per-build in-memory store
_scratchpads: dict[str, dict[str, str]] = {}

def _exec_forge_scratchpad(tool_input: dict, working_dir: str) -> str:
    """Persistent key-value scratchpad scoped to the working directory."""
    op = tool_input.get("operation", "list")
    pad = _scratchpads.setdefault(working_dir, {})
    if op == "list":
        return json.dumps({"keys": list(pad.keys())})
    # ... read/write/append handlers
```

**Effort**: Medium. ~150 lines of new code + 5 tool definitions appended to
`BUILDER_TOOLS`.

### Step 2: Write New System Prompt

**File**: `app/services/build_service.py` (or extract to `app/services/build/prompts.py`)

Replace the current `system_prompt = (...)` block with the new prompt from
Section 3 above. Consider extracting to a separate file or template for
maintainability.

**Effort**: Small. ~100 lines of prompt text replacing ~60 lines.

### Step 3: Slim First Message Assembly

**File**: `app/services/build_service.py`

Replace lines 1860-1893 (the `first_message` + `messages` assembly):

```python
# OLD
directive = _build_directive(contracts)
phase_window = _extract_phase_window(contracts, 0)
first_message = directive + workspace_info + phase_window

# NEW
project_brief = _get_project_brief(contracts)  # title + 1-line desc from blueprint
first_message = (
    f"# Project: {project_brief}\n\n"
    + workspace_info
    + "\n\nBegin Phase 0. Use your forge tools to fetch the contracts "
    "you need, then emit your === PLAN === and start building.\n"
)
```

**Effort**: Small. Remove `_build_directive()` call, simplify assembly.

### Step 4: Update Phase Advancement

**File**: `app/services/build_service.py` (lines 2460-2530)

Currently, phase advancement injects the next phase window as a user message.
With MCP tools, we keep the advancement message but remove the phase window
injection — the builder will fetch it via `forge_get_phase_window`:

```python
# OLD
next_window = _extract_phase_window(contracts, current_phase_num)
advance_parts.append(f"\n{next_window}\n")

# NEW — no window injection
advance_parts.append(
    f"\nCall forge_get_phase_window({current_phase_num}) to get "
    "this phase's deliverables, then emit === PLAN ===.\n"
)
```

**Effort**: Small. Remove 2 lines, adjust 1 line.

### Step 5: Update Context Compaction

**File**: `app/services/build/context.py`

Current compaction preserves `messages[0]` (the contract dump) and last 4
messages. With the new architecture:

1. `messages[0]` is tiny (~1.5K tokens) — preserving it costs nothing
2. Tool results containing contract data naturally compact with the rest
3. After compaction, the builder can re-fetch any contract via MCP tools

**Changes needed**:
- The compaction summary should note *which* contracts the builder has read
  (extractable from tool_use blocks in the compacted turns)
- Add a hint in the summary: "Contracts can be re-fetched via forge tools"
- Consider storing contract-read history in the scratchpad automatically

**Effort**: Small. ~10-15 lines changed.

### Step 6: Update `_write_contracts_to_workdir()`

**File**: `app/services/build/context.py`

This function already writes contracts to `Forge/Contracts/` in the working
directory. It must continue to work so `forge_get_contract` can read from disk.
**No changes needed** — it's already correct.

### Step 7: Tests

**New tests needed**:
- `test_forge_tools_in_builder.py`: Verify all 5 forge tools execute correctly
  via `execute_tool_async`
- Update `test_build_service.py`: Verify the new system prompt, slim first
  message, and phase advancement without contract injection
- `test_scratchpad.py`: Read/write/append/list operations, working_dir scoping
- `test_phase_window_tool.py`: Phase extraction via tool, edge cases (no phases,
  last phase, invalid phase number)

**Effort**: Medium. ~200 lines of test code.

---

## 6. Migration Path

### Phase A: Add Tools (Non-Breaking)

Add forge tools to `BUILDER_TOOLS` and `execute_tool_async`. The builder gains
new tools but the current contract-dump flow is unchanged. Tests pass.
This is a safe, deployable change.

### Phase B: New System Prompt + Slim First Message

Replace the system prompt and first message assembly. This is the big switch.
The builder stops receiving contract dumps and must use tools instead.

**Risk mitigation**: Add a feature flag `USE_MCP_CONTRACTS` (default False)
in settings. When True, use the new system prompt + slim first message.
When False, use the old flow. This allows A/B testing and safe rollback.

```python
if settings.USE_MCP_CONTRACTS:
    system_prompt = NEW_SYSTEM_PROMPT
    first_message = slim_first_message
else:
    directive = _build_directive(contracts)
    system_prompt = OLD_SYSTEM_PROMPT
    first_message = directive + workspace_info + phase_window
```

### Phase C: Update Phase Advancement

Remove phase window injection from advancement messages. Builder fetches
via tool. Deploy behind same feature flag.

### Phase D: Update Compaction & Remove Flag

Once the MCP flow is validated on real builds, update compaction strategy
and remove the feature flag. Delete `_build_directive()` function.

---

## 7. Risks & Mitigations

| Risk | Impact | Mitigation |
|------|--------|-----------|
| Builder doesn't fetch contracts | Builds without governance | System prompt EXPLICITLY lists what to fetch in Step 1. First-turn check: if no forge_get tool calls in turn 1, inject "You must call forge_get_phase_window(0) before coding." |
| Too many tool calls → slow turns | 2-3s per tool round-trip | Local disk reads are <1ms. Bundle discovery: builder calls forge_get_summary once, then targeted contract reads. Expect 3-5 tool calls in turn 1, 1-2 per phase thereafter. |
| Re-fetching after compaction | Extra tokens in later turns | Tool results are ~2-4K each, vs 27K permanent. Net positive even with 5+ refetches. Scratchpad stores key decisions so contracts needn't be re-read in full. |
| System prompt too large | Eats context overhead | Target 4.5K tokens. Current system + contracts = 28.5K. Still a massive reduction. |
| Phases contract too large for tool | 230K phases.md via forge_get_contract | Block `phases` from `forge_get_contract`. Builder MUST use `forge_get_phase_window`. |

---

## 8. Builder Behaviour — Expected Flow (Full Build)

### Turn 1 (Phase 0 Start)

```
Builder receives: workspace listing + "Begin Phase 0"

Builder calls: forge_get_phase_window(0)
  → Gets: Phase 0 (Genesis) + Phase 1 deliverables (~2K tokens)

Builder calls: forge_get_contract("blueprint")
  → Gets: What to build (~1K tokens)

Builder calls: forge_get_contract("stack")
  → Gets: Tech stack requirements (~0.6K tokens)

Builder emits:
=== PLAN ===
1. Create project scaffold (package.json, tsconfig, etc.)
2. Set up authentication module
3. Create database schema
...
=== END PLAN ===

Builder calls: write_file("package.json", ...)  → starts coding
```

### Turn N (Mid-Phase)

```
Builder calls: write_file("src/routes/auth.ts", ...)
Builder calls: check_syntax("src/routes/auth.ts") → PASS
Builder emits: === TASK DONE: 2 ===

Builder calls: forge_get_contract("physics")  ← needs API spec for next task
  → Gets: physics.yaml content (~3.6K tokens)

Builder calls: write_file("src/routes/api.ts", ...)
```

### Phase Transition

```
[System injects: "## Phase 2 — START" + diff summary + workspace listing]
[Message says: "Call forge_get_phase_window(2) to get deliverables"]

Builder calls: forge_get_phase_window(2)
  → Gets: Phase 2 + Phase 3 deliverables

Builder calls: forge_scratchpad("read", "architecture_decisions")
  → Gets: Notes from Phase 0 about chosen patterns

Builder calls: forge_get_contract("boundaries")  ← needs layer rules
  → Gets: boundaries.json content

Builder emits: === PLAN === for Phase 2 ...
```

### After Context Compaction

```
[System compacts middle turns. Contract data from tool results is lost.]

Builder needs physics spec but it was in a compacted turn.
Builder calls: forge_get_contract("physics")  ← re-fetches, ~3.6K tokens
  → Gets: physics.yaml content (fresh copy)

Builder calls: forge_scratchpad("read", "known_issues")
  → Gets: Issues noted before compaction (scratchpad survives!)
```

---

## 9. File Change Summary

| File | Change | Lines |
|------|--------|------:|
| `app/services/tool_executor.py` | Add 5 forge tool handlers + BUILDER_TOOLS entries | +200 |
| `app/services/build_service.py` | New system prompt, slim first message, phase advance | ~±100 |
| `app/services/build/context.py` | Update compaction for tool-aware summaries | ~±20 |
| `forge_ide/mcp/tools.py` | Add `forge_get_phase_window` to TOOL_DEFINITIONS | +20 |
| `forge_ide/mcp/local.py` | Add `get_phase_window(phase_num, phases_content)` | +30 |
| `tests/test_forge_builder_tools.py` | New test file for forge tools in builder | +200 |
| `tests/test_build_service.py` | Update existing tests for new flow | ~±50 |
| `app/config.py` | Add `USE_MCP_CONTRACTS` feature flag | +3 |
| **Total** | | **~+600** |

---

## 10. Open Questions

1. **Should `forge_get_summary()` include a mini-brief of each contract?**
   Currently it returns layer names and invariant names. Adding 1-line
   descriptions per contract would help the builder decide what to fetch
   without calling `forge_list_contracts` too.

2. **Should the scratchpad auto-persist which contracts the builder has read?**
   Would help the compaction summary ("Builder has read: blueprint, stack,
   physics") so the builder knows what it already knows after compaction.

3. **Should we enforce a "must-read" gate in the build loop?**
   E.g., if turn 1 completes without any `forge_get_*` calls, inject a
   user message: "You MUST fetch phase deliverables before coding. Call
   forge_get_phase_window(0) now." This prevents the builder ignoring
   contracts entirely.

4. **Mini builds**: Should mini builds use the same MCP flow? Mini builds
   have only 2 phases and simpler contracts. The overhead of 3-5 tool calls
   might be acceptable. Alternatively, mini builds could get a single
   combined contract dump (they're small enough to fit).

5. **`builder_contract.md` distillation**: The current 38KB document has
   detailed sections (§0-§12). The new system prompt extracts ~6 key rules.
   Should we create a `builder_contract_slim.md` (~3KB) with the essential
   rules, available via `forge_get_contract("builder_contract")`? Or keep
   the full 38KB version as a reference the builder can optionally fetch?
