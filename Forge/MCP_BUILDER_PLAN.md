# MCP-Driven Builder Architecture — Detailed Implementation Plan

> **Goal**: Two fundamental changes to the builder:
> 1. **Decouple ForgeGuard's own docs from user project generation** — replace
>    ForgeGuard contract examples with generic structural templates
> 2. **Replace the 30-60K token contract dump with MCP tool calls** — the builder
>    fetches exactly what it needs, when it needs it, via on-demand tools

---

## 0. Current Architecture (What We're Replacing)

### Problem 1: ForgeGuard Contamination

`_load_forge_example()` in `contract_generator.py` reads ForgeGuard's actual
contract files from `Forge/Contracts/` and injects them as "STRUCTURAL REFERENCE"
when generating contracts for **user projects**. The LLM sees ForgeGuard's
blueprint (GitHub OAuth, webhooks, audit engine), schema (users, repos tables),
physics (ForgeGuard API endpoints), etc. — and inevitably absorbs ForgeGuard's
architecture into user projects.

**What leaks**: Every contract type. ForgeGuard's product features, database
schema, API endpoints, and architecture patterns bleed into new projects.

**Root cause**: ForgeGuard's own governance docs serve double-duty as both
(a) ForgeGuard's living documentation and (b) templates for the contract generator.

### Problem 2: Contract Context Dump

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

~27K tokens persist across all 50+ turns, never compacted.

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

## 1. Contract Generation — Decouple from ForgeGuard

### 1a. New Generation Order

**Current**: blueprint → manifesto → stack → schema → physics → boundaries →
phases → ui → builder_directive

**New**: manifesto FIRST (values/philosophy drive everything), phases SECOND-TO-LAST
(needs full project picture to plan timeline), builder_directive LAST (AEM activation):

| # | Contract | Reads (all prior) | Rationale |
|---|----------|-------------------|-----------|
| 1 | **manifesto** | — | Values & philosophy first — everything else flows from this |
| 2 | **blueprint** | manifesto | What to build, informed by project values |
| 3 | **stack** | manifesto, blueprint | Tech choices driven by what we're building |
| 4 | **schema** | manifesto, blueprint, stack | Data model needs product + tech context |
| 5 | **physics** | all above | API spec needs schema + stack + product knowledge |
| 6 | **boundaries** | all above | Architecture rules need the full tech picture |
| 7 | **ui** | all above | Design needs schema, physics (APIs), and stack |
| 8 | **phases** | ALL above | Timeline/execution plan — needs EVERYTHING to plan properly |
| 9 | **builder_directive** | ALL above | AEM activation — the document that turns Claude into the autonomous builder |

**Key change**: Each contract reads ALL previously generated contracts (snowball
chain), not just a selective subset. The current `_CHAIN_CONTEXT` map cherry-picks
2-4 deps per contract — the new approach gives every contract full project context.

### 1b. Replace ForgeGuard Examples with Generic Templates

**Current**: `_load_forge_example()` reads ForgeGuard's actual files from
`Forge/Contracts/` — leaking ForgeGuard's architecture into user projects.

**New**: Generic structural templates in `app/templates/builder_examples/` —
same format and depth, but using a neutral fictional project ("TaskFlow" — a
simple task management app). Each template is ~1-2KB, showing ONLY the structure
and section format the LLM should follow.

| Template | Content | Size |
|----------|---------|-----:|
| `manifesto_example.md` | Generic principles: user-first, test-driven, simplicity | ~0.8KB |
| `blueprint_example.md` | Generic SaaS: project name, intent, features, architecture | ~1.2KB |
| `stack_example.md` | Placeholder sections: backend, frontend, infra, dev tools | ~0.8KB |
| `schema_example.md` | 2-3 simple entities (users, tasks, comments) with format | ~1.0KB |
| `physics_example.yaml` | 4 CRUD endpoints showing YAML structure | ~1.2KB |
| `boundaries_example.json` | Generic layered arch: routers, services, repos, clients | ~0.8KB |
| `ui_example.md` | Generic dashboard: design system, pages, components | ~1.0KB |
| `phases_example.md` | 4-phase generic plan showing format (objective, deliverables, criteria) | ~1.5KB |
| `builder_directive_example.md` | AEM config, phase order, settings | ~0.6KB |

**Mini-build**: Same templates, but phases example is 2-phase variant (~0.8KB).
The `_mini` instruction variants already say "EXACTLY 2 phases" — now the
structural reference also shows 2 phases instead of ForgeGuard's 7+.

### 1c. Snowball Chain Context

Instead of the current selective `_CHAIN_CONTEXT` map, the generator passes
**all previously generated contracts** to each subsequent generation call:

```python
# New approach — cumulative chain
generated_so_far: dict[str, str] = {}
for contract_type in CONTRACT_TYPES:       # new order: manifesto → ... → builder_directive
    prior_context = "\n\n---\n\n".join(
        f"## {ctype} (already generated)\n{content}"
        for ctype, content in generated_so_far.items()
    )
    # Cap total prior context: 24KB full / 12KB mini
    result = await _generate_contract_content(
        contract_type, answers, prior_context, example_template, ...
    )
    generated_so_far[contract_type] = result
```

This means:
- `manifesto` generates with no prior context (pure questionnaire answers)
- `blueprint` sees the manifesto it just generated
- `stack` sees manifesto + blueprint
- `schema` sees manifesto + blueprint + stack
- ...
- `phases` sees ALL 7 contracts above — full project picture
- `builder_directive` sees everything including phases

### 1d. Implementation Changes

| File | Change |
|------|--------|
| `app/services/project/contract_generator.py` | New `CONTRACT_TYPES` order, replace `_load_forge_example()` with template loader, replace `_CHAIN_CONTEXT` with cumulative snowball, cap prior context |
| `app/templates/builder_examples/` | 9 new generic template files (~9KB total) |
| `app/templates/contracts/phases.md` | Delete or reduce — no longer needed as ForgeGuard's 4093-line file |

---

## 2. MCP-Driven Builder Architecture

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

## 3. New MCP Tools for the Builder

### 3a. `forge_get_contract` (EXISTING — reuse as-is)

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

### 3b. `forge_get_phase_window` (NEW)

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

### 3c. `forge_list_contracts` (EXISTING — reuse as-is)

Already exists. Returns names + filenames + formats of all available contracts.
The builder calls this if it needs to discover what contracts exist.

### 3d. `forge_get_summary` (EXISTING — reuse as-is)

Returns a compact overview of the governance framework. Good for the builder's
first call to orient itself without fetching every contract.

### 3e. `forge_scratchpad` (NEW)

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

## 4. New System Prompt Design

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

## 5. First Message Redesign

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

## 6. Implementation Steps (Ordered)

### Step 1: Create Generic Builder Templates

**File**: `app/templates/builder_examples/`

Create 9 generic template files (~1-2KB each) using a neutral "TaskFlow" project.
These show ONLY the structure/format the LLM should follow — no ForgeGuard content.

**Effort**: Medium. ~9KB of carefully crafted template content.

### Step 2: Rewire Contract Generator

**File**: `app/services/project/contract_generator.py`

- Change `CONTRACT_TYPES` order: manifesto → blueprint → stack → schema → physics → boundaries → ui → phases → builder_directive
- Replace `_load_forge_example()` with `_load_generic_template()` reading from `app/templates/builder_examples/`
- Replace `_CHAIN_CONTEXT` selective map with cumulative snowball (all prior contracts)
- Cap cumulative prior context: 24KB full / 12KB mini

**Effort**: Medium. ~80 lines changed in existing code.

### Step 3: Add Forge Tools to `tool_executor.py`

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

### Step 4: Write New System Prompt

**File**: `app/services/build_service.py` (or extract to `app/services/build/prompts.py`)

Replace the current `system_prompt = (...)` block with the new prompt from
Section 3 above. Consider extracting to a separate file or template for
maintainability.

**Effort**: Small. ~100 lines of prompt text replacing ~60 lines.

### Step 5: Slim First Message Assembly

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

### Step 6: Update Phase Advancement

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

### Step 7: Update Context Compaction

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

### Step 8: Update `_write_contracts_to_workdir()`

**File**: `app/services/build/context.py`

This function already writes contracts to `Forge/Contracts/` in the working
directory. It must continue to work so `forge_get_contract` can read from disk.
**No changes needed** — it's already correct.

### Step 9: Tests

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

## 7. Migration Path

### Phase 0: Decouple Contract Generation (Non-Breaking)

Create generic builder templates. Rewire `contract_generator.py` to use them
instead of ForgeGuard's `Forge/Contracts/`. Change generation order to
manifesto-first. Implement cumulative snowball chain context.

**This is independent of the MCP builder work** — it fixes contract generation
quality immediately, regardless of how the builder consumes them later.

### Phase A: Add Forge Tools to Builder (Non-Breaking)

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

## 8. Risks & Mitigations

| Risk | Impact | Mitigation |
|------|--------|-----------|
| Builder doesn't fetch contracts | Builds without governance | System prompt EXPLICITLY lists what to fetch in Step 1. First-turn check: if no forge_get tool calls in turn 1, inject "You must call forge_get_phase_window(0) before coding." |
| Too many tool calls → slow turns | 2-3s per tool round-trip | Local disk reads are <1ms. Bundle discovery: builder calls forge_get_summary once, then targeted contract reads. Expect 3-5 tool calls in turn 1, 1-2 per phase thereafter. |
| Re-fetching after compaction | Extra tokens in later turns | Tool results are ~2-4K each, vs 27K permanent. Net positive even with 5+ refetches. Scratchpad stores key decisions so contracts needn't be re-read in full. |
| System prompt too large | Eats context overhead | Target 4.5K tokens. Current system + contracts = 28.5K. Still a massive reduction. |
| Phases contract too large for tool | 230K phases.md via forge_get_contract | Block `phases` from `forge_get_contract`. Builder MUST use `forge_get_phase_window`. |
| Generic templates too vague | LLM generates shallow contracts | Templates show structure AND depth (section names, expected detail level, field types). The LLM fills in project-specific content from questionnaire answers + prior contracts. |
| Snowball chain too large | Later contracts hit token limit with cumulative context | Cap prior context: 24KB full / 12KB mini. Truncate oldest contracts first if needed. |

---

## 9. Builder Behaviour — Expected Flow (Full Build)

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

## 10. File Change Summary

| File | Change | Lines |
|------|--------|------:|
| **Phase 0: Contract Generation Decoupling** | | |
| `app/templates/builder_examples/*.md/.yaml/.json` | 9 new generic template files | +~80 |
| `app/services/project/contract_generator.py` | New order, generic templates, snowball chain | ~±120 |
| `tests/test_contract_snapshots.py` | Update snapshots for new generation order | ~±30 |
| **Phase A–D: MCP Builder** | | |
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

## 11. Open Questions

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


---

# Phase E-H: MCP as Governed Context Broker

> **Status**: Planning -- all prior phases (0-D) are **complete** and gated
> behind `USE_MCP_CONTRACTS = False`.

## 12. Problem Statement

Three systems hold contract data and **don't talk to each other**:

| System | What it holds | Scope | Lifetime |
|--------|--------------|-------|----------|
| MCP governance tools (`forge_ide/mcp/local.py`) | Static `.md` / `.json` / `.yaml` files from `Forge/Contracts/` | Global -- same for every user/project | Permanent (filesystem) |
| PostgreSQL (`project_contracts`, `contract_snapshots`) | Per-project generated contracts with version history | Per-user, per-project | Persistent (DB) |
| Artifact store (`forge_ide/mcp/artifact_store.py`) | Process-local KV store keyed by `project:{id}:...` | Per-project (in key) | In-memory (24h TTL) + disk |

**The result**: sub-agents get static filesystem contracts that are identical
for every project, while the *actual* project-specific generated contracts sit
in the database untouched during builds.  The artifact store has a project-id
concept but is disconnected from the DB.

### What we want

MCP becomes the **single gateway** for all contract data.  Any agent
(IDE, build sub-agent, audit engine) calls MCP to get the right contracts
for the right project -- **on demand, not upfront**.  Per-user, per-project,
per-build scoping.  Principle of least privilege extended from tools (Layer 1)
to **context** (Layer 2).

```
+----------+   forge_get_project_contract(project_id, "stack")
| Sub-Agent | -----------------------------------------------------> +-----------+
| (Scout)   |<----------------------------------------------------- | MCP Server|
+----------+   { content: "...", version: 3, source: "db" }        |           |
                                                                     |  dispatch |
+----------+   forge_get_project_contract(project_id, "physics")    |     |     |
| Sub-Agent | -----------------------------------------------------> |     v     |
| (Coder)   |<----------------------------------------------------- |  HTTP to  |
+----------+   { content: "...", version: 3, source: "db" }        | ForgeGuard|
                                                                     |   API     |
+----------+   forge_get_build_contracts(build_id)                  |     |     |
| Sub-Agent | -----------------------------------------------------> |     v     |
| (Fixer)   |<----------------------------------------------------- | PostgreSQL|
+----------+   { contracts: [...], batch: 7, pinned: true }        +-----------+
```

### Token savings

| Scenario | Current (push) | Proposed (pull) | Savings |
|----------|---------------|-----------------|---------|
| Scout turn | ~27K tokens (all contracts in system prompt) | ~4K (manifesto + summary, lazy-load 1-2 more) | **~85%** |
| Coder turn | ~27K tokens | ~6K (stack + physics + boundaries on demand) | **~78%** |
| Auditor turn | ~27K tokens | ~5K (boundaries + physics) | **~81%** |
| Fixer turn | ~27K tokens | ~2K (only audit findings, contracts cited) | **~93%** |

---

## 13. Architecture

### 13.1 Data flow

```
                    +----------------------------------------------+
                    |            ForgeGuard API (FastAPI)           |
                    |                                               |
                    |  GET /api/projects/{pid}/contracts            |  <-- already exists
                    |  GET /api/projects/{pid}/contracts/{type}     |  <-- already exists
                    |  GET /api/projects/{pid}/contracts/history/N  |  <-- already exists
                    |  ------------------------------------------- |
                    |  GET /api/mcp/context/{pid}        (NEW)     |  <-- combined context
                    |  GET /api/mcp/context/{pid}/{type}  (NEW)    |  <-- single contract
                    |  GET /api/mcp/build/{bid}/contracts  (NEW)   |  <-- pinned snapshot
                    +----------------------+-----------------------+
                                           |
                                    PostgreSQL
                              +------------+------------+
                              |                         |
                       project_contracts         contract_snapshots
                       (live, versioned)         (pinned per build)
```

### 13.2 MCP dispatch routing (updated)

Current routing:

```python
# tools.py dispatch()
if name in _ARTIFACT_TOOLS:   # -> in-process artifact_store
elif LOCAL_MODE:               # -> local disk (Forge/Contracts/)
else:                          # -> remote API (ForgeGuard /forge/*)
```

New routing adds a **project-scoped** tier:

```python
# tools.py dispatch() -- Phase E
if name in _ARTIFACT_TOOLS:        # -> in-process artifact_store
elif name in _PROJECT_TOOLS:       # -> ForgeGuard API /api/mcp/* (NEW)
elif LOCAL_MODE:                   # -> local disk (Forge/Contracts/)
else:                              # -> remote API (ForgeGuard /forge/*)
```

**Key design decision**: project-scoped tools **always** go to the
ForgeGuard API, never to local disk.  This is because project contracts
only exist in the database -- there is no local-disk equivalent.

### 13.3 Auth context

The MCP server needs to authenticate against ForgeGuard when fetching
project-scoped data.  Two mechanisms:

1. **API key** (already exists): `FORGEGUARD_API_KEY` env var -> `Authorization: Bearer {key}`.
   ForgeGuard already has forge-key management at `/auth/forge-keys`.
   The MCP server is treated as an internal service with a service-scoped key.

2. **Session context** (new): The build orchestrator passes `project_id` and
   `build_id` when spawning the MCP server / configuring the session.
   These are injected as **session-level defaults** so sub-agents don't need
   to pass them explicitly on every call (but can override).

```python
# New: forge_ide/mcp/session.py
@dataclass
class MCPSession:
    project_id: str | None = None
    build_id: str | None = None
    user_id: str | None = None

_session = MCPSession()

def set_session(project_id: str, build_id: str | None = None, user_id: str | None = None):
    _session.project_id = project_id
    _session.build_id = build_id
    _session.user_id = user_id

def get_session() -> MCPSession:
    return _session
```

---

## 14. New MCP Tools

### 14.1 Project-scoped contract tools

| Tool | Description | Input | Output |
|------|-------------|-------|--------|
| `forge_get_project_contract` | Fetch a single generated contract for the current project | `project_id` (optional if session set), `contract_type` | `{ content, version, contract_type, source: "db" }` |
| `forge_list_project_contracts` | List all generated contracts for the current project | `project_id` (optional) | `{ items: [{ contract_type, version, updated_at }] }` |
| `forge_get_project_context` | Combined manifest: project info + list of available contracts + key metrics | `project_id` (optional) | `{ project: {...}, contracts: [...], build_count, latest_batch }` |
| `forge_get_build_contracts` | Fetch the pinned contract snapshot for a specific build | `build_id` | `{ items: [{ contract_type, content }], batch, pinned_at }` |

### 14.2 Session management tool

| Tool | Description | Input | Output |
|------|-------------|-------|--------|
| `forge_set_session` | Set session-level defaults (called once by build orchestrator) | `project_id`, `build_id?`, `user_id?` | `{ ok: true, project_id, build_id }` |

### 14.3 Relationship to existing tools

| Existing tool | Serves | Keep / Replace |
|---------------|--------|----------------|
| `forge_get_contract(name)` | Static `Forge/Contracts/` files | **Keep** -- these are the generic governance templates (reference architecture) |
| `forge_get_project_contract(type)` | Per-project DB contracts | **New** -- these are the project-specific generated versions |
| `forge_get_artifact(...)` | Process-local temp store | **Keep** -- for ephemeral build artifacts (scout dossiers, phase outputs) |

**Contract resolution order** for sub-agents:
1. If `project_id` is set -> fetch from DB via `forge_get_project_contract`
2. Fallback -> generic template via `forge_get_contract`
3. For ephemeral build data -> artifact store via `forge_get_artifact`

This gives us a three-tier hierarchy:
- **Generic templates** (`Forge/Contracts/`) -- the reference architecture, same for everyone
- **Project contracts** (PostgreSQL) -- generated per-project from questionnaire, versioned
- **Build artifacts** (artifact store) -- ephemeral per-build outputs, TTL'd

---

## 15. ForgeGuard API Endpoints (New)

Three new endpoints under `/api/mcp/` -- thin wrappers around existing
`project_repo` functions with forge-key auth.

### 15.1 `GET /api/mcp/context/{project_id}`

Returns combined project context manifest:

```json
{
  "project": {
    "id": "uuid",
    "name": "MyProject",
    "status": "contracts_generated"
  },
  "contracts": [
    { "contract_type": "manifesto", "version": 3, "size_chars": 2400, "updated_at": "..." },
    { "contract_type": "stack", "version": 2, "size_chars": 1800, "updated_at": "..." }
  ],
  "latest_batch": 7,
  "build_count": 3
}
```

Note: returns **metadata only**, not full content.  Sub-agents decide which
contracts to fetch based on this manifest.

### 15.2 `GET /api/mcp/context/{project_id}/{contract_type}`

Returns full contract content:

```json
{
  "contract_type": "stack",
  "content": "# Technology Stack\n...",
  "version": 2,
  "project_id": "uuid",
  "source": "project_db"
}
```

### 15.3 `GET /api/mcp/build/{build_id}/contracts`

Returns the pinned contract snapshot for a build:

```json
{
  "build_id": "uuid",
  "batch": 7,
  "pinned_at": "2025-01-15T10:30:00Z",
  "contracts": [
    { "contract_type": "manifesto", "content": "..." },
    { "contract_type": "stack", "content": "..." }
  ]
}
```

**Immutability guarantee**: once a build starts and pins a batch, the
snapshot is frozen.  Mid-build contract edits don't affect running builds.

### 15.4 Auth

All `/api/mcp/*` endpoints require a valid forge-key (`Authorization: Bearer {key}`).
The forge-key is already scoped to a user in the `forge_keys` table.
The endpoint validates that the user owning the key has access to the
requested project.

---

## 16. Sub-Agent Integration

### 16.1 Per-role contract access patterns

Building on the existing `_ROLE_TOOL_NAMES` in `subagent.py`, each role gets
access to different project-scoped tools:

```python
_ROLE_TOOL_NAMES: dict[SubAgentRole, frozenset[str]] = {
    SubAgentRole.SCOUT: frozenset({
        "read_file", "list_directory", "search_code",
        # Static governance
        "forge_get_contract", "forge_list_contracts", "forge_get_summary",
        # Project-scoped (NEW)
        "forge_get_project_contract",     # full read of any project contract
        "forge_list_project_contracts",   # see what's available
        "forge_get_project_context",      # combined manifest
        # Existing
        "forge_get_phase_window", "forge_scratchpad",
    }),
    SubAgentRole.CODER: frozenset({
        "read_file", "list_directory", "search_code",
        "write_file", "edit_file", "check_syntax", "run_command",
        # Project-scoped (NEW) -- focused on implementation contracts
        "forge_get_project_contract",     # stack, physics, boundaries
        "forge_list_project_contracts",
        # Existing
        "forge_get_contract", "forge_get_phase_window",
        "forge_list_contracts", "forge_get_summary", "forge_scratchpad",
    }),
    SubAgentRole.AUDITOR: frozenset({
        "read_file", "list_directory", "search_code",
        # Project-scoped (NEW) -- compliance verification
        "forge_get_project_contract",     # boundaries, physics for checks
        "forge_list_project_contracts",
        # Existing
        "forge_get_contract", "forge_list_contracts",
        "forge_get_summary", "forge_scratchpad",
    }),
    SubAgentRole.FIXER: frozenset({
        "read_file", "list_directory", "search_code",
        "edit_file", "check_syntax",
        # Project-scoped (NEW) -- pinned build snapshot only
        "forge_get_build_contracts",      # immutable reference
        # Existing
        "forge_scratchpad",
    }),
}
```

### 16.2 Context injection strategy per role

Instead of stuffing all contracts into the system prompt, each sub-agent gets
a **minimal brief** with instructions to pull what it needs:

**Scout prompt template:**
```
You are a Scout for project {project_id}.
Available contracts: {contract_list_from_manifest}
START by calling forge_get_project_contract("manifesto") and
forge_get_project_contract("stack") to understand the project.
Fetch additional contracts as needed for your analysis.
```

**Coder prompt template:**
```
You are a Coder for project {project_id}, build {build_id}.
Phase: {phase_number} -- {phase_name}
Before writing code, fetch the relevant contracts:
- forge_get_project_contract("stack") for tech requirements
- forge_get_project_contract("physics") for API spec
- forge_get_project_contract("boundaries") for architecture rules
Only fetch what you need for this phase's deliverables.
```

**Auditor prompt template:**
```
You are an Auditor for project {project_id}, build {build_id}.
Verify code against these contracts (fetch as needed):
- forge_get_project_contract("boundaries") -- architecture rules
- forge_get_project_contract("physics") -- API compliance
Report violations with contract references.
```

**Fixer prompt template:**
```
You are a Fixer for build {build_id}.
Audit findings: {findings_summary}
The build's pinned contracts are available via forge_get_build_contracts("{build_id}").
Fix ONLY the cited violations. Do not refactor.
```

### 16.3 Build loop integration

The build orchestrator (`build_service.py`) changes:

```python
# Before spawning sub-agents:
# 1. Snapshot contracts for this build
batch = await snapshot_contracts(project_id)
await set_build_contract_batch(build_id, batch)

# 2. Set MCP session context
await mcp_dispatch("forge_set_session", {
    "project_id": str(project_id),
    "build_id": str(build_id),
    "user_id": str(user_id),
})

# 3. Spawn sub-agents -- they inherit session context
#    and pull contracts on-demand via forge_get_project_contract
scout_result = await run_sub_agent(
    SubAgentHandoff(role=SubAgentRole.SCOUT, ...),
    ...
)
```

---

## 17. Implementation Steps

### Phase E: MCP Project Tools + ForgeGuard Endpoints

**Goal**: MCP can serve project-scoped contracts from the database.

| # | Task | File(s) | Depends |
|---|------|---------|---------|
| E1 | Create `forge_ide/mcp/session.py` -- session dataclass + get/set | `session.py` | -- |
| E2 | Create `forge_ide/mcp/project.py` -- project-scoped tool handlers that call ForgeGuard API | `project.py` | E1 |
| E3 | Add 5 new tool definitions to `forge_ide/mcp/tools.py` | `tools.py` | E2 |
| E4 | Update `dispatch()` routing -- add `_PROJECT_TOOLS` set | `tools.py` | E3 |
| E5 | Add `GET /api/mcp/context/{pid}` endpoint | `app/api/routers/mcp.py` (new) | -- |
| E6 | Add `GET /api/mcp/context/{pid}/{type}` endpoint | `app/api/routers/mcp.py` | E5 |
| E7 | Add `GET /api/mcp/build/{bid}/contracts` endpoint | `app/api/routers/mcp.py` | E5 |
| E8 | Forge-key auth middleware for `/api/mcp/*` routes | `app/api/routers/mcp.py` | E5 |
| E9 | Tests: MCP project tools unit tests | `tests/test_mcp_project_tools.py` | E1-E4 |
| E10 | Tests: MCP API endpoint integration tests | `tests/test_mcp_api.py` | E5-E8 |

### Phase F: Sub-Agent Prompt Redesign

**Goal**: Sub-agents use pull model instead of push.  System prompts shrink
from ~27K tokens to ~2-6K.

| # | Task | File(s) | Depends |
|---|------|---------|---------|
| F1 | Add project-scoped tools to `_ROLE_TOOL_NAMES` in `subagent.py` | `subagent.py` | E3 |
| F2 | Create per-role prompt templates with pull instructions | `subagent.py` or `app/templates/` | E3 |
| F3 | Update `build_context_pack()` to include project manifest (metadata only) | `subagent.py` | E2 |
| F4 | Add `forge_get_project_contract` + `forge_get_build_contracts` to `tool_executor.py` | `tool_executor.py` | E2 |
| F5 | Tests: sub-agent prompt token measurement (assert < 6K per role) | `tests/test_subagent.py` | F1-F3 |

### Phase G: Build Loop Integration

**Goal**: Build orchestrator snapshots contracts, sets MCP session,
sub-agents pull from DB throughout the build.

| # | Task | File(s) | Depends |
|---|------|---------|---------|
| G1 | Call `snapshot_contracts()` at build start in `build_service.py` | `build_service.py` | -- |
| G2 | Call `forge_set_session()` before spawning sub-agents | `build_service.py` | E1 |
| G3 | Pass `build_id` to fixer sub-agents for pinned snapshot access | `build_service.py`, `subagent.py` | E3, G1 |
| G4 | Contract cache in MCP remote client (avoid re-fetching same contract per build) | `forge_ide/mcp/remote.py` | E2 |
| G5 | Add build-level contract usage telemetry (which contracts each role fetched) | `subagent.py` | F1 |
| G6 | Tests: end-to-end build with pull model (mock DB) | `tests/test_build_pull_model.py` | G1-G3 |

### Phase H: Flip the Switch + IDE Integration

**Goal**: Enable the full MCP pull model and connect IDE MCP tools
to project-scoped contracts.

| # | Task | File(s) | Depends |
|---|------|---------|---------|
| H1 | Set `USE_MCP_CONTRACTS = True` as default | `app/config.py` | G6 |
| H2 | Update IDE MCP tool definitions for project-scoped access | `forge_ide/mcp/tools.py` | E3 |
| H3 | IDE session init: set project_id when user opens project | `forge_ide/agent.py` | E1 |
| H4 | Remove legacy "dump all contracts in system prompt" code path | `build_service.py` | H1 |
| H5 | Documentation update | `README.md`, `Forge/MCP_BUILDER_PLAN.md` | H1-H4 |
| H6 | Token usage comparison: before/after metrics | -- | H1 |

---

## 18. Migration Path (E-H)

### Step 1: Shadow mode (Phase E-F complete)

Project-scoped tools are available but optional.  The build orchestrator
continues to push contracts into prompts (existing behaviour) AND sets
the MCP session.  Sub-agents may call project tools but don't rely on them.

This lets us verify:
- MCP -> ForgeGuard API -> DB round-trip works
- Contract content matches what was previously pushed
- No auth/permission issues

### Step 2: Dual mode (Phase G complete)

`USE_MCP_CONTRACTS = True` enables the pull model.  Builds use the new
slim prompts.  A telemetry flag compares token usage between old and new
paths.  Rollback: set `USE_MCP_CONTRACTS = False`.

### Step 3: Pull-only (Phase H complete)

Legacy push code removed.  All sub-agents operate in pull mode.
IDE agents also use project-scoped tools.

---

## 19. Contract Resolution Hierarchy

When a sub-agent needs a contract, the resolution follows this priority:

```
1. Build-pinned snapshot   (forge_get_build_contracts)
   +-- Immutable.  Used by Fixer role.  Guarantees contract
       consistency throughout a build's lifetime.

2. Project DB contract     (forge_get_project_contract)
   +-- Versioned.  Used by Scout/Coder/Auditor.  Always the
       latest generated version for the project.

3. Generic template        (forge_get_contract)
   +-- Static reference.  Used when no project-specific
       contract exists, or for cross-cutting governance
       (builder_directive, auditor_prompt, etc.).

4. Artifact store          (forge_get_artifact)
   +-- Ephemeral build outputs.  Scout dossiers, phase
       summaries, diff logs.  Not contracts -- supporting data.
```

---

## 20. Risk Analysis (E-H)

| Risk | Severity | Mitigation |
|------|----------|------------|
| MCP -> API latency adds ~100ms per tool call | Low | Cache in MCP remote client; contracts rarely change mid-build |
| Sub-agent forgets to fetch contracts | Medium | "Must-read" gate: if turn 1 has no `forge_get_project_contract` calls, inject reminder (same pattern as Open Question #3) |
| Contract version drift during long builds | Low | Snapshot pinning already solves this -- build gets batch N, edits to batch N+1 don't affect it |
| Forge-key compromise gives access to all user contracts | Medium | Forge-keys are user-scoped; endpoint validates key-owner matches project-owner |
| Process-local artifact store doesn't survive MCP restart | Low | Disk persistence already handles this; DB contracts are authoritative anyway |
| Token savings less than projected | Low | Even pulling 4 of 9 contracts is 55% savings vs pushing all 9; floor is still significant |

---

## 21. Expected Token Flow (Per-Role)

### Scout (reconnaissance)

```
Turn 0 (system)  : ~800 tokens  -- role brief + pull instructions
Turn 1 (auto)    : forge_get_project_context(pid)         -> ~200 tokens (manifest)
Turn 2 (auto)    : forge_get_project_contract("manifesto") -> ~600 tokens
Turn 3 (auto)    : forge_get_project_contract("stack")     -> ~450 tokens
Turn 4+          : reads files, runs search_code, writes dossier
                   May lazy-load "blueprint" or "boundaries" if needed
Total contract tokens: ~1,250 (vs ~27,000 in push model)
```

### Coder (implementation)

```
Turn 0 (system)  : ~600 tokens  -- role brief + phase deliverables
Turn 1 (auto)    : forge_get_project_contract("stack")      -> ~450 tokens
Turn 2 (auto)    : forge_get_project_contract("physics")    -> ~800 tokens
Turn 3 (if needed): forge_get_project_contract("boundaries") -> ~500 tokens
Turn 4+          : writes code, checks syntax
Total contract tokens: ~1,750 (vs ~27,000)
```

### Auditor (verification)

```
Turn 0 (system)  : ~500 tokens  -- role brief + what to verify
Turn 1 (auto)    : forge_get_project_contract("boundaries") -> ~500 tokens
Turn 2 (auto)    : forge_get_project_contract("physics")    -> ~800 tokens
Turn 3+          : read_file on generated code, report findings
Total contract tokens: ~1,300 (vs ~27,000)
```

### Fixer (surgical repair)

```
Turn 0 (system)  : ~400 tokens  -- findings + fix instructions
Turn 1 (if needed): forge_get_build_contracts(bid) -> specific cited contracts
Turn 2+          : edit_file on violations
Total contract tokens: ~0-800 (vs ~27,000)
```

---

## 22. File Change Summary (Phase E-H)

### New files

| File | Purpose |
|------|---------|
| `forge_ide/mcp/session.py` | MCP session context (project_id, build_id, user_id) |
| `forge_ide/mcp/project.py` | Project-scoped tool handlers -> ForgeGuard API calls |
| `app/api/routers/mcp.py` | ForgeGuard API endpoints for MCP contract serving |
| `tests/test_mcp_project_tools.py` | Unit tests for new MCP tools |
| `tests/test_mcp_api.py` | Integration tests for new API endpoints |
| `tests/test_build_pull_model.py` | End-to-end build with pull model |

### Modified files

| File | Changes |
|------|---------|
| `forge_ide/mcp/tools.py` | Add 5 tool definitions, `_PROJECT_TOOLS` set, updated `dispatch()` |
| `forge_ide/mcp/remote.py` | Add `api_get_project_contract()`, `api_get_build_contracts()` |
| `app/services/build/subagent.py` | Add project tools to `_ROLE_TOOL_NAMES`, new prompt templates |
| `app/services/build/tool_executor.py` | Route new forge tools to MCP dispatch |
| `app/services/build/build_service.py` | Snapshot + session init before sub-agents, slim prompts |
| `app/config.py` | (Phase H) Flip `USE_MCP_CONTRACTS = True` |
| `app/main.py` | Register `/api/mcp` router |

---

## 23. Open Questions (Phase E-H)

1. **Should `forge_set_session` be a tool or an init parameter?**
   As a tool, the orchestrator calls it as the first action.  As an init
   param, it's passed when the MCP server subprocess is spawned.  Init param
   is cleaner but requires changes to MCP server startup.

2. **Should we add a `forge_get_project_contract` fallback to generic templates?**
   If a project doesn't have a "boundaries" contract yet (e.g., contracts not
   generated), should MCP automatically fall back to the generic
   `Forge/Contracts/boundaries.json`?  Pro: never fails.  Con: may confuse
   the agent into thinking project-specific contracts exist.

3. **Cache invalidation**: If a user edits a contract mid-build (via UI),
   the MCP cache should NOT update (build uses pinned snapshot).  But the
   next build should see the edit.  The snapshot mechanism already handles
   this -- just need to ensure the MCP cache TTL aligns.

4. **Should the Fixer have access to `forge_get_project_contract` or only
   `forge_get_build_contracts`?**  Current design restricts Fixer to pinned
   snapshot only (immutability guarantee).  But this means the Fixer can't
   see post-build contract edits, which is intentional.

5. **Rate limiting on `/api/mcp/*` endpoints**: Sub-agents might call
   `forge_get_project_contract` many times.  Should we add per-build rate
   limits?  The cache should prevent actual DB hits, but we need telemetry
   to confirm.

6. **IDE session lifecycle**: When a user switches projects in the IDE,
   should the MCP session auto-switch?  Or require explicit
   `forge_set_session` call?  Auto-switch is more ergonomic.

---

---

# Phase I — Tool-Use Contract Generation

> **Status**: Planned — Not yet implemented.
> **Supersedes**: Current snowball text-injection pattern in both
> `contract_generator.py` (questionnaire flow) and
> `scout_contract_generator.py` (Scout flow).
>
> **Why now**: Having built the Scout→Contract bridge and the MCP artifact
> store, the remaining weakness in contract quality is the generation
> mechanism itself.  Contracts are produced by sequential one-shot LLM
> calls with raw text injection — not by agents that reason about what
> context they actually need.

---

## 24. Current Contract Generation Architecture — The Problem

### 24.1 The Snowball Text-Injection Pattern

Both `contract_generator.py` (questionnaire / greenfield flow) and
`scout_contract_generator.py` (Scout / existing-repo flow) use the same
generation pattern:

```
for contract_type in CONTRACT_TYPES:          # 9 contracts, in order
    user_message = (
        intent_block                           # questionnaire answers OR scout context
        + snowball_chain                       # ALL prior contracts, raw text
    )
    content = await llm_chat_streaming(
        system_prompt=instructions[contract_type],
        messages=[{"role": "user", "content": user_message}],
    )
    prior_contracts[contract_type] = content  # grows with each iteration
```

By the time `builder_directive` (contract #9) is generated, `user_message`
contains up to **24,000 characters** of raw contract text injected as a
single monolithic block — regardless of which parts of those contracts
are actually relevant to a builder directive.

### 24.2 The Token Cost

| Contract | Prior contracts injected | Approx injected tokens |
|----------|--------------------------|------------------------|
| manifesto | none | 0 |
| blueprint | manifesto | ~400 |
| stack | manifesto + blueprint | ~850 |
| schema | + stack | ~1,350 |
| physics | + schema | ~2,150 |
| boundaries | + physics | ~3,200 |
| ui | + boundaries | ~4,600 |
| phases | + ui | ~6,200 |
| builder_directive | ALL 8 prior | **~8,500** (cap: 24K chars) |

Each contract generation is a separate LLM call.  Across all 9 calls the
system injects roughly **28–35K tokens** of prior-contract context that
the LLM must re-read each time, even though most of it is irrelevant to
the specific contract being generated.

The `per_cap = 12_000` (chars per contract) and `total_budget = 24_000`
(chars total injected per call) caps prevent catastrophic overflow, but
they force early truncation — `builder_directive` may see truncated
versions of `phases` and `ui` because the budget is exhausted before
reaching them.

### 24.3 The Selective Context Problem

Contracts need different context.  What `boundaries` needs from prior
contracts is fundamentally different from what `ui` needs:

| Contract | What it actually needs from prior work |
|----------|----------------------------------------|
| **stack** | manifesto (philosophy) + blueprint (what to build) |
| **schema** | blueprint (entities) + stack (ORM / DB choice) |
| **physics** | schema (data shape) + stack (framework/router) |
| **boundaries** | stack (layers) + physics (what endpoints exist) |
| **ui** | blueprint (screens) + physics (API calls available) + stack (frontend tech) |
| **phases** | ALL contracts — it must plan implementation of everything |
| **builder_directive** | boundaries + phases + stack (what rules the builder must follow) |

The snowball chain treats all prior contracts as equally valuable to all
subsequent contracts.  An LLM generating `ui` receives the full `schema`
(database tables) even though the UI contract cares only about the API
endpoints in `physics`, not the underlying tables.

### 24.4 The One-Shot Generation Problem

Each contract is generated in a **single LLM call**.  The LLM cannot:
- Ask for clarification on ambiguous scout data
- Request the full architecture map when the truncated version is insufficient
- Fetch a specific prior contract section without receiving the entire block
- Iteratively refine based on what it discovers it needs

The result is contracts that are internally consistent (because they see
all prior contracts) but may miss depth and accuracy because the LLM had
to reason about everything in a single pass without the ability to
selectively investigate.

---

## 25. User Intent Equivalence Principle

Before designing the tool-use architecture, it is critical to understand
the conceptual equivalence between the two contract generation flows:

```
Greenfield (new project)          Scout (existing repo)
─────────────────────────         ─────────────────────────
User fills questionnaire          User triggers Scout deep-scan
         ↓                                   ↓
Questionnaire answers             Scout results + Renovation plan
         ↓                                   ↓
"What does the user want          "What does this repo need?
 this project to become?"          What should it become?"
         ↓                                   ↓
Contract generator reads          Contract generator reads
answers_text (formatted           scout_context (formatted
questionnaire sections)           stack + arch + dossier)
                                  + renovation_plan (REQUIRED)
```

### 25.1 The Renovation Plan Is Non-Negotiable for Scout Contracts

In the greenfield flow, questionnaire answers ARE the user intent.  Without
them, the generator has no product direction to work from.

In the Scout flow, the **renovation plan** is the equivalent of questionnaire
answers.  It contains:
- `executive_brief.top_priorities` — what problems to solve first
- `executive_brief.health_grade` — current state of the codebase
- `executive_brief.forge_automation_note` — what Forge can automate
- `migration_recommendations` — the ordered task list (from_state → to_state)
- `version_report.outdated` — dependency upgrade targets
- `executive_brief.risk_summary` — what risks the builder must navigate

Without the renovation plan, the Scout contract generator is producing
contracts for an existing codebase with **no target state defined**.  The
contracts would describe "what the repo is" rather than "what it should
become after the build" — which is the entire purpose of the builder_directive
contract in particular.

**Current state (BUG)**: `renovation_plan` is extracted as optional:
```python
# scout_contract_generator.py line 409
renovation_plan: dict | None = raw_results.get("renovation_plan")
```
If `None`, the generator proceeds silently.  The renovation plan section
is simply omitted from the scout context.

**Required fix (Phase I1)**: Enforce renovation plan as a hard prerequisite:
```python
renovation_plan = raw_results.get("renovation_plan")
if not renovation_plan:
    raise ValueError(
        "No renovation plan found for this scout run. "
        "Generate a renovation plan first via POST /scout/runs/{run_id}/upgrade-plan "
        "before generating contracts."
    )
```

This mirrors the validation already applied for `scan_type == "deep"` and
`status == "completed"` — the renovation plan is simply another completeness
gate before contract generation can proceed.

---

## 26. Tool-Use Contract Generation Design

### 26.1 Core Concept

Instead of injecting a static block of context into the user message, each
contract is generated by an **agent that calls tools to fetch exactly what
it needs**, then calls `submit_contract(content)` when done.

```
System prompt  : "You are generating the {contract_type} contract.
                  Use the tools to fetch the context you need.
                  Call submit_contract(content) when you have the
                  complete contract."

Agent tools    : get_stack_profile()          → detected tech stack
                 get_renovation_priorities()   → executive brief + top priorities
                 get_migration_tasks()         → ordered migration task list
                 get_project_dossier()         → executive summary, risks, recs
                 get_compliance_checks()       → compliance check results
                 get_architecture_map()        → full architecture dict (uncapped)
                 get_prior_contract(type)      → specific prior-generated contract
                 submit_contract(content)      → terminates the tool loop

Tool-use loop  : multi-turn until stop_reason == "end_turn"
                 OR submit_contract tool is called
```

The agent reasons about what context it needs for **this specific contract**,
fetches only that, and produces a more focused and accurate output.

For `boundaries`, it will typically call `get_stack_profile()`,
`get_prior_contract("physics")`, and `get_prior_contract("stack")`.
It will NOT request the architecture map unless the detected stack suggests
it's relevant.

For `builder_directive`, it will call `get_renovation_priorities()`,
`get_migration_tasks()`, `get_prior_contract("boundaries")`, and
`get_prior_contract("phases")` — exactly the context a builder directive
needs.  It will not waste tokens reading the schema or UI contracts.

### 26.2 Tool Definitions

```python
CONTEXT_TOOLS_SCOUT = [
    {
        "name": "get_stack_profile",
        "description": (
            "Get the detected technology stack for this repository. "
            "Returns backend, frontend, testing, and infrastructure sections."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "get_renovation_priorities",
        "description": (
            "Get the executive brief from the renovation plan: health grade, "
            "headline, top priorities, risk summary, and forge automation note. "
            "This is the primary statement of WHAT the build should achieve."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "get_migration_tasks",
        "description": (
            "Get the ordered migration task list from the renovation plan. "
            "Each task has from_state, to_state, priority, effort, and "
            "forge_automatable flag. Use this to understand what the builder "
            "must accomplish."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "get_project_dossier",
        "description": (
            "Get the LLM dossier from the Scout deep-scan: executive summary, "
            "intent, quality assessment (strengths/weaknesses), risk areas, "
            "and recommendations."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "get_compliance_checks",
        "description": (
            "Get the compliance check results from the Scout scan. "
            "Returns check codes, names, results (pass/fail/warn), and details."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "get_architecture_map",
        "description": (
            "Get the full architecture map detected by Scout. Returns the "
            "complete architecture dict (NOT truncated). Use this when the "
            "architecture structure matters for the contract you're generating."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "get_prior_contract",
        "description": (
            "Fetch a previously generated contract by type. Use this to "
            "ensure consistency with contracts already generated in this run. "
            "Available types: manifesto, blueprint, stack, schema, physics, "
            "boundaries, ui, phases (only those generated before this one)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "contract_type": {
                    "type": "string",
                    "description": (
                        "Contract type to fetch: manifesto | blueprint | stack | "
                        "schema | physics | boundaries | ui | phases"
                    ),
                },
            },
            "required": ["contract_type"],
        },
    },
    {
        "name": "submit_contract",
        "description": (
            "Submit the completed contract content. Call this ONLY when you "
            "have the complete, final contract ready. This terminates the "
            "generation session. The content must be the full contract — "
            "no preamble, no explanation, no code fences."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "content": {
                    "type": "string",
                    "description": "The complete contract content to submit.",
                },
            },
            "required": ["content"],
        },
    },
]
```

For the **greenfield / questionnaire flow**, the tool set is similar but
replaces the Scout-specific context tools with questionnaire-oriented ones:

```python
CONTEXT_TOOLS_GREENFIELD = [
    # get_project_intent()       → formatted questionnaire answers
    # get_technical_preferences()→ tech stack section of questionnaire
    # get_user_stories()         → features/requirements section
    # get_prior_contract(type)   → same as Scout version
    # submit_contract(content)   → same as Scout version
]
```

The `submit_contract` tool is identical in both flows — it's the termination
signal for the tool-use loop regardless of context source.

### 26.3 The Tool-Use Execution Loop

The `chat_anthropic` function in `app/clients/llm_client.py` already supports
tool-use via its `tools` parameter (line 141).  When `tools` is provided, the
function returns the **full raw API response** instead of the simplified
`{"text": ..., "usage": ...}` dict.  The caller inspects `stop_reason` and
`content` blocks to detect tool_use.

```python
async def _generate_contract_with_tools(
    contract_type: str,
    repo_name: str,
    scout_data: dict,                  # raw extracted sections
    renovation_plan: dict,
    api_key: str,
    model: str,
    prior_contracts: dict[str, str],
) -> tuple[str, dict]:
    """Generate a single contract using Anthropic tool-use."""

    # ── Build tool context data (closed over by executor) ──────────────
    context_data = {
        "stack_profile":      scout_data.get("stack_profile") or {},
        "architecture":       scout_data.get("architecture") or {},
        "dossier":            scout_data.get("dossier") or {},
        "checks":             scout_data.get("checks") or [],
        "executive_brief":    renovation_plan.get("executive_brief") or {},
        "migration_tasks":    renovation_plan.get("migration_recommendations") or [],
        "version_report":     renovation_plan.get("version_report") or {},
    }

    def _execute_tool(name: str, tool_input: dict) -> str:
        """Execute a context tool and return JSON string result."""
        match name:
            case "get_stack_profile":
                return json.dumps(context_data["stack_profile"])
            case "get_renovation_priorities":
                return json.dumps(context_data["executive_brief"])
            case "get_migration_tasks":
                return json.dumps(context_data["migration_tasks"])
            case "get_project_dossier":
                return json.dumps(context_data["dossier"])
            case "get_compliance_checks":
                return json.dumps(context_data["checks"])
            case "get_architecture_map":
                return json.dumps(context_data["architecture"])
            case "get_prior_contract":
                ctype = tool_input.get("contract_type", "")
                content = prior_contracts.get(ctype)
                if not content:
                    return f"Contract '{ctype}' not yet generated."
                return content
            case _:
                return f"Unknown tool: {name}"

    example = _load_generic_template(contract_type)
    instructions = _CONTRACT_INSTRUCTIONS.get(contract_type, "")
    system_prompt = _build_tool_use_system_prompt(
        contract_type, repo_name, instructions, example
    )

    messages: list[dict] = [
        {
            "role": "user",
            "content": (
                f"Generate the {contract_type} contract for repository '{repo_name}'. "
                f"Use the available tools to fetch exactly the context you need, "
                f"then call submit_contract with the complete contract content."
            ),
        }
    ]

    total_usage = {"input_tokens": 0, "output_tokens": 0}
    max_turns = 12   # prevent infinite loops

    for _turn in range(max_turns):
        response = await chat_anthropic(
            api_key=api_key,
            model=model,
            system_prompt=system_prompt,
            messages=messages,
            max_tokens=16384,
            tools=CONTEXT_TOOLS_SCOUT,
        )

        # Accumulate token usage across all turns
        usage = response.get("usage", {})
        total_usage["input_tokens"] += usage.get("input_tokens", 0)
        total_usage["output_tokens"] += usage.get("output_tokens", 0)

        stop_reason = response.get("stop_reason")
        content_blocks = response.get("content", [])

        # ── Append assistant turn to message history ────────────────
        messages.append({"role": "assistant", "content": content_blocks})

        # ── Check for submit_contract in tool_use blocks ────────────
        for block in content_blocks:
            if block.get("type") == "tool_use" and block.get("name") == "submit_contract":
                submitted_content = block.get("input", {}).get("content", "")
                if submitted_content:
                    # Strip code fences if LLM wrapped the output
                    submitted_content = _strip_code_fences(submitted_content)
                    return submitted_content, total_usage

        # ── If stop_reason is end_turn with no submit, extract text ─
        if stop_reason == "end_turn":
            text_parts = [
                b["text"] for b in content_blocks if b.get("type") == "text"
            ]
            fallback = "\n".join(text_parts).strip()
            if fallback:
                return _strip_code_fences(fallback), total_usage
            # No text either — something went wrong
            raise ValueError(
                f"LLM ended turn for {contract_type} without "
                "submitting a contract or returning text."
            )

        # ── Execute tool calls and build tool_results turn ──────────
        if stop_reason == "tool_use":
            tool_results = []
            for block in content_blocks:
                if block.get("type") != "tool_use":
                    continue
                tool_name = block.get("name", "")
                tool_input = block.get("input", {})
                tool_id = block.get("id", "")
                result_content = _execute_tool(tool_name, tool_input)
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": tool_id,
                    "content": result_content,
                })
            messages.append({"role": "user", "content": tool_results})
            continue

        # Unexpected stop_reason
        break

    # Fallback: max turns exceeded
    raise ValueError(
        f"Tool-use loop for {contract_type} exceeded {max_turns} turns "
        "without producing a contract."
    )
```

### 26.4 Why chat_anthropic Already Supports This

From `app/clients/llm_client.py` (lines 141–229):

```python
async def chat_anthropic(
    api_key: str,
    model: str,
    system_prompt: str,
    messages: list[dict],
    max_tokens: int = 2048,
    tools: list[dict] | None = None,        # ← tool-use entry point
    thinking_budget: int = 0,
    enable_caching: bool = False,
) -> dict:
    ...
    # If tools were provided, return the full response so the caller
    # can process tool_use blocks and the stop_reason.
    if tools:
        return data                          # ← raw API dict

    # Without tools: return simplified {"text": ..., "usage": ...}
    ...
```

This means:
- **No changes to `llm_client.py`** are required for tool-use contract generation
- The tool-use loop in `_generate_contract_with_tools()` calls `chat_anthropic`
  directly (not `chat_streaming`) — streaming is incompatible with tool-use
  because the LLM may emit multiple tool calls in a single response
- The `enable_caching=True` flag can be passed to cache the system prompt and
  tool definitions, reducing input token costs by up to 90% on cache hits for
  repeated contract generation

### 26.5 Token Efficiency Comparison

```
Current (snowball text injection):
  Contract 9 (builder_directive):
    system_prompt    ~2,500 tokens  (instructions + structural reference)
    user_message     ~8,500 tokens  (all 8 prior contracts injected)
    TOTAL input      ~11,000 tokens per contract #9 call
    Cumulative total across all 9 calls: ~30,000 tokens input

Tool-use approach (same contract, estimated):
  Turn 1: user asks LLM to generate              ~200 tokens
  Turn 1: LLM calls get_prior_contract("phases") ~100 tokens
  Turn 2: tool result (phases contract)          ~1,200 tokens
  Turn 2: LLM calls get_prior_contract("bounds") ~100 tokens
  Turn 3: tool result (boundaries contract)      ~600 tokens
  Turn 3: LLM calls get_renovation_priorities()  ~100 tokens
  Turn 4: tool result (executive brief)          ~300 tokens
  Turn 4: LLM calls submit_contract              ~100 tokens
  ----------------------------------------------------------
  TOTAL input across 4 turns                   ~2,700 tokens
  (vs 11,000 for snowball — ~75% reduction)

  + Tool definition tokens (amortised via caching): ~800 tokens
  + Net: ~3,500 tokens vs 11,000 — still ~68% reduction
```

The tool-use approach is especially beneficial for early contracts
(manifesto, blueprint, stack) which currently have no prior-contract
injection but still receive a large structural reference in the system
prompt.  With tool-use, they can request the reference only if needed.

### 26.6 Connection to MCP Artifact Store

The tool-use loop executes in-process within the contract generator service.
Tools are implemented as local Python functions (not actual MCP tool calls).

However, after generation, contracts are still stored in the MCP artifact
store (`forge_ide.mcp.artifact_store.store_artifact`) as before.  This
means the generated contracts remain available to build sub-agents via
`forge_get_artifact` — there is no change to the downstream pipeline.

The tool-use change is isolated to the **generation phase** only.
Everything downstream (DB persistence, MCP artifact store, WS progress
events, snapshot mechanism) is unchanged.

---

## 27. Phase I Implementation Steps

### Phase I1: Enforce Renovation Plan (Quick Fix — Do First)

**Goal**: Make renovation plan a hard prerequisite for Scout contract
generation.  One-line change, zero risk.

| # | Task | File | Notes |
|---|------|------|-------|
| I1-1 | Replace `renovation_plan: dict \| None = raw_results.get(...)` with validation guard | `app/services/project/scout_contract_generator.py` (line ~409) | Raise `ValueError` with helpful message pointing to `/upgrade-plan` endpoint |
| I1-2 | Update `generate_contracts_from_scout` docstring | same file | Document that renovation plan is required |
| I1-3 | Add test: `test_no_renovation_plan_raises_value_error` | `tests/test_scout_contract_generator.py` | Mock run with no `renovation_plan` in results |
| I1-4 | Update API docs string on the `generate-contracts` endpoint | `app/api/routers/scout.py` | Note that upgrade-plan must be run first |

### Phase I2: Tool-Use Contract Generation — Scout Flow

**Goal**: Replace `_generate_scout_contract_content` (snowball text
injection) with `_generate_contract_with_tools` (multi-turn tool-use loop).

| # | Task | File | Notes |
|---|------|------|-------|
| I2-1 | Define `CONTEXT_TOOLS_SCOUT` list | `app/services/project/scout_contract_generator.py` | 8 tools as per section 26.2 |
| I2-2 | Implement `_execute_scout_tool(name, input, context_data, prior_contracts)` | same file | Pure function, no async, returns JSON string |
| I2-3 | Implement `_strip_code_fences(text)` helper | `app/services/project/contract_utils.py` (new shared module) | Extract from both generators to avoid duplication |
| I2-4 | Implement `_build_tool_use_system_prompt(contract_type, repo_name, instructions, example)` | same file | System prompt that tells LLM to use tools + submit_contract |
| I2-5 | Implement `_generate_contract_with_tools(...)` | same file | Multi-turn loop as per section 26.3 |
| I2-6 | Update `generate_contracts_from_scout` to call `_generate_contract_with_tools` instead of `_generate_scout_contract_content` | same file | Pass `scout_data` dict + `renovation_plan` dict separately (not pre-formatted context string) |
| I2-7 | Remove `_format_scout_context_for_prompt` (no longer needed) | same file | It existed only for text injection; tool-use fetches data on demand |
| I2-8 | Tests: tool-use loop happy path | `tests/test_scout_contract_generator.py` | Mock `chat_anthropic` to return tool_use then end_turn |
| I2-9 | Tests: tool-use each context tool returns correct data | same | One test per tool: stack_profile, priorities, tasks, dossier, checks, arch map, prior_contract |
| I2-10 | Tests: submit_contract terminates loop | same | Assert contract returned after submit_contract call |
| I2-11 | Tests: end_turn fallback (LLM returns text without submit_contract) | same | Should still succeed with extracted text |
| I2-12 | Tests: max_turns exceeded raises ValueError | same | Mock LLM to always return tool_use without ever submitting |
| I2-13 | Tests: token accumulation across turns | same | Assert total_usage sums all turns correctly |
| I2-14 | Integration: run Scout contract generation against real LLM in staging | manual | Verify 9 contracts generated, token count < snowball baseline |

### Phase I3: Tool-Use Contract Generation — Greenfield Flow

**Goal**: Apply the same pattern to `contract_generator.py`
(questionnaire-based generation).

| # | Task | File | Notes |
|---|------|------|-------|
| I3-1 | Define `CONTEXT_TOOLS_GREENFIELD` list | `app/services/project/contract_generator.py` | Questionnaire-oriented tools: get_project_intent, get_technical_preferences, get_user_stories, get_prior_contract, submit_contract |
| I3-2 | Implement `_execute_greenfield_tool(name, input, answers_data, prior_contracts)` | same file | Maps questionnaire sections to tool results |
| I3-3 | Refactor `_generate_contract_content` to use tool-use loop | same file | Replace snowball injection with `_generate_contract_with_tools_greenfield` |
| I3-4 | Tests: greenfield tool-use each tool returns correct questionnaire section | `tests/test_contract_generator.py` | |
| I3-5 | Tests: end-to-end greenfield generation with mock LLM | same | 9 contracts, verify each calls the expected tool subset |

---

## 28. File Changes Summary (Phase I)

### New files

| File | Purpose |
|------|---------|
| `app/services/project/contract_utils.py` | Shared helpers: `_strip_code_fences`, `_build_tool_use_system_prompt`, `_extract_text_from_blocks` |

### Modified files

| File | Changes |
|------|---------|
| `app/services/project/scout_contract_generator.py` | Remove `_format_scout_context_for_prompt`, remove `_generate_scout_contract_content`, add `CONTEXT_TOOLS_SCOUT`, add `_execute_scout_tool`, add `_generate_contract_with_tools`, update `generate_contracts_from_scout` to enforce renovation plan + use tool-use |
| `app/services/project/contract_generator.py` | (Phase I3) Replace `_generate_contract_content` snowball injection with tool-use loop |
| `app/api/routers/scout.py` | Update docstring on `generate-contracts` endpoint |
| `tests/test_scout_contract_generator.py` | Add tool-use tests (I2-8 through I2-13) + renovation plan enforcement test (I1-3) |
| `tests/test_contract_generator.py` | (Phase I3) Add greenfield tool-use tests |

---

## 29. Migration Path — Phase I

### Step I-A: Enforce renovation plan (Phase I1)

No functional changes to generation logic.  Any attempt to call
`generate_contracts_from_scout` on a run without a renovation plan will now
return HTTP 400 with a clear message.

Frontend must handle this: the "Generate Contracts" button on the Scout
results page should be disabled until a renovation plan exists (the plan
badge/status is already shown in the UI).

### Step I-B: Shadow-test tool-use on a single contract type (Phase I2 partial)

Before replacing all 9 generators, implement tool-use for `manifesto` only
and compare output quality and token count against the snowball-generated
version on real projects.

The `manifest` contract has no snowball chain (it's contract #1) so the
comparison is purely about output quality, not token savings.

### Step I-C: Roll out tool-use to all 9 scout contracts (Phase I2 complete)

Replace `_generate_scout_contract_content` with `_generate_contract_with_tools`
for all 9 contract types.  Monitor via `contract_progress` WebSocket events —
token counts will drop sharply if the tool-use approach is working correctly.

### Step I-D: Apply to greenfield flow (Phase I3)

Once the Scout flow is validated and stable, apply the same pattern to
`contract_generator.py`.  The greenfield flow is higher-traffic (every new
project goes through it) so validate on Scout first.

---

## 30. Open Questions (Phase I)

1. **Max turns for tool-use loop**: `max_turns = 12` is conservative.
   In practice, `builder_directive` might need 5–6 tool calls (3 prior
   contracts + renovation priorities + migration tasks + submit).  Should
   `max_turns` be per-contract-type rather than a global constant?

2. **Streaming progress during tool-use**: The current snowball generator
   uses `chat_streaming` which emits token counts via `on_token_progress`
   callback, allowing the frontend to show a streaming indicator.
   `chat_anthropic` with tools does not support streaming.  The frontend
   will see no token-count updates during tool-use generation — only the
   final `done` event.  Is this acceptable UX, or should we implement a
   periodic "still generating..." ping?

3. **Should `get_prior_contract` check the MCP artifact store?**
   If a previous generation session already stored contracts in the
   artifact store (via `store_artifact`), `get_prior_contract` could fetch
   from there instead of the in-memory `prior_contracts` dict.  This would
   enable resuming a tool-use generation session across a crash.  Not
   strictly needed for v1 but worth noting.

4. **Tool-use for OpenAI provider**: `chat_anthropic` supports tools.
   `chat_openai` does not currently include a `tools` parameter.  If the
   platform is configured with `LLM_PROVIDER=openai`, tool-use generation
   would fail.  Options: (a) fall back to snowball for OpenAI, (b) implement
   OpenAI function-calling support, (c) require Anthropic for tool-use.

5. **Contract quality regression testing**: How do we measure whether
   tool-use-generated contracts are better than snowball-generated ones?
   Need a rubric: completeness, accuracy against scout data, structural
   depth, absence of placeholder text, alignment with renovation plan.

---

---

# Phase J — Builder Clarification Tool (`forge_ask_clarification`)

> **Status**: Planned — Not yet implemented.
> **Scope**: All build flows (greenfield + renovation/Scout).
>
> **Why**: During autonomous builds the LLM sometimes encounters genuine
> implementation ambiguity — choices that significantly affect architecture
> but cannot be resolved from contracts alone (e.g. "JWT vs session auth?",
> "REST vs GraphQL?", "overwrite this file or merge?").  Currently the
> builder makes silent assumptions.  This feature adds a
> `forge_ask_clarification` tool that surfaces a structured question to the
> user, pauses the build loop, and resumes with the user's answer injected
> as the tool result.

---

## 31. Problem Statement

The builder is autonomous, but autonomy has limits.  When the user's intent
is genuinely ambiguous and the contracts don't specify a preference, the
builder must guess.  Silent guesses cause:

- Wasted build time (wrong direction → audit failures → full-phase retries)
- User frustration (final output doesn't match their mental model)
- Excess token cost (re-running phases after human course-correction)

Existing mitigation mechanisms are too blunt:
- `forge_scratchpad` — records decisions but doesn't surface them to the user
- Build pause (audit failure) — pauses after damage is done, not before
- `/interject` command — user-initiated only; builder can't request input

A lightweight question→answer loop at the tool level is the right granularity.

---

## 32. Design

### 32.1 Flow

```
Builder calls forge_ask_clarification(question, context?, options?)
  │
  ▼
build_service.py intercepts BEFORE execute_tool_async (line ~2550)
  │
  ├─ check question count vs MAX_CLARIFICATIONS_PER_BUILD
  ├─ emit build_clarification_request WS event to IDE
  ├─ register asyncio.Event in _state._clarification_events[build_id]
  └─ await event with CLARIFICATION_TIMEOUT_MINUTES timeout

User sees question card in ForgeIDEModal (amber border, auto-focus)
  │
  ├─ clicks option chip  OR  types free-text answer
  └─ POST /projects/{project_id}/build/clarify  { question_id, answer }

build_service.resume_clarification()
  │
  ├─ store answer in _state._clarification_answers[build_id]
  ├─ set _clarification_events[build_id]  (unblocks build loop)
  └─ emit build_clarification_resolved WS event

Build loop wakes up → tool result = answer string → builder continues
```

### 32.2 Tool Definition

Added to `FORGE_TOOLS` in `app/services/tool_executor.py`:

```python
{
    "name": "forge_ask_clarification",
    "description": (
        "Ask the user a clarifying question when you encounter genuine ambiguity "
        "that cannot be resolved from the available contracts, scout data, or "
        "renovation plan.  The build pauses until the user answers. "
        "Use SPARINGLY — only when the implementation direction depends on a "
        "user preference that cannot be inferred.  Do NOT ask about obvious "
        "choices or things already specified in contracts.  Max 10 per build."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "question": {
                "type": "string",
                "description": "The question to ask (max 200 characters, concise).",
            },
            "context": {
                "type": "string",
                "description": (
                    "Brief explanation of WHY you need to know (max 300 chars). "
                    "e.g. 'Implementing the login endpoint — choosing auth strategy.'"
                ),
            },
            "options": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Optional 2–4 suggested answers shown as chips in the IDE. "
                    "Always include a 'Let AI decide' option if providing choices."
                ),
            },
        },
        "required": ["question"],
    },
},
```

### 32.3 New Build State (`app/services/build/_state.py`)

Three new dicts added to the shared state block (after `_build_cost_user`):

```python
# Clarification (forge_ask_clarification) state
_clarification_events:  dict[str, asyncio.Event] = {}  # build_id → Event
_clarification_answers: dict[str, str]           = {}  # build_id → answer
_clarification_counts:  dict[str, int]           = {}  # build_id → # asked
```

Five new helper functions:

```python
def register_clarification(build_id: str) -> asyncio.Event
def resolve_clarification(build_id: str, answer: str) -> bool
def pop_clarification_answer(build_id: str) -> str | None
def increment_clarification_count(build_id: str) -> int   # returns new count
def cleanup_clarification(build_id: str) -> None           # called on build end
```

`_fail_build()` must call `cleanup_clarification(str(build_id))` after the
existing `_cancel_flags.discard` calls.

### 32.4 Tool Handler in `app/services/build_service.py`

**`_handle_clarification(build_id, user_id, tool_input) → str`** (new async helper):

```python
async def _handle_clarification(build_id, user_id, tool_input) -> str:
    count = increment_clarification_count(str(build_id))
    if count > settings.MAX_CLARIFICATIONS_PER_BUILD:
        return "Max clarification limit reached. Make your best decision and continue."

    question_id = str(uuid.uuid4())
    question = str(tool_input.get("question", ""))[:200]
    context  = str(tool_input.get("context", ""))[:300]
    options  = tool_input.get("options", [])

    await _broadcast_build_event(user_id, build_id, "build_clarification_request", {
        "build_id": str(build_id), "question_id": question_id,
        "question": question, "context": context, "options": options,
    })
    await build_repo.append_build_log(build_id, f"Awaiting clarification: {question}",
                                      source="builder", level="info")

    event = register_clarification(str(build_id))
    try:
        await asyncio.wait_for(event.wait(),
                               timeout=settings.CLARIFICATION_TIMEOUT_MINUTES * 60)
    except asyncio.TimeoutError:
        pop_clarification_answer(str(build_id))
        await _broadcast_build_event(user_id, build_id, "build_clarification_resolved", {
            "build_id": str(build_id), "question_id": question_id,
            "answer": "(timed out — AI will decide)",
        })
        return ("No answer received within the timeout. "
                "Make your best decision based on contracts and continue.")

    answer = pop_clarification_answer(str(build_id)) or "(no answer)"
    await build_repo.append_build_log(build_id, f"User answered: {answer}",
                                      source="user", level="info")
    await _broadcast_build_event(user_id, build_id, "build_clarification_resolved", {
        "build_id": str(build_id), "question_id": question_id, "answer": answer,
    })
    return answer
```

**Tool dispatch intercept** (line ~2550 in `build_service.py`):

```python
# Before:
tool_result = await execute_tool_async(item.name, item.input, working_dir or "")

# After:
if item.name == "forge_ask_clarification":
    tool_result = await _handle_clarification(build_id, user_id, item.input)
else:
    tool_result = await execute_tool_async(item.name, item.input, working_dir or "")
```

**`resume_clarification(project_id, user_id, question_id, answer) → dict`** (new service function, near `resume_build`):

```python
async def resume_clarification(project_id, user_id, question_id, answer) -> dict:
    build = await build_repo.get_active_build(project_id)
    if not build or str(build.get("user_id")) != str(user_id):
        raise ValueError("No active build found for this project")
    resolved = resolve_clarification(str(build["id"]), answer)
    if not resolved:
        raise ValueError("No pending clarification for this build")
    return {"ok": True, "build_id": str(build["id"])}
```

### 32.5 Sub-Agent Role Allowlists (`app/services/build/subagent.py`)

```python
SubAgentRole.SCOUT: frozenset({
    ...,
    "forge_ask_clarification",   # ← add
}),
SubAgentRole.CODER: frozenset({
    ...,
    "forge_ask_clarification",   # ← add
}),
# AUDITOR and FIXER: do NOT add (bounded, specific tasks — no ambiguity expected)
```

Also intercept `forge_ask_clarification` in the sub-agent tool dispatch loop inside
`run_sub_agent` (before calling `execute_tool_async`), using `handoff.build_id`
and `handoff.user_id`.

### 32.6 API Endpoint (`app/api/routers/builds.py`)

```python
class ClarifyRequest(BaseModel):
    question_id: str
    answer: str = Field(..., min_length=1, max_length=1000)

@router.post("/{project_id}/build/clarify")
async def clarify_build(
    project_id: UUID,
    body: ClarifyRequest,
    user: dict = Depends(get_current_user),
) -> dict:
    """Submit the user's answer to a builder clarification question."""
    try:
        return await build_service.resume_clarification(
            project_id, user["id"], body.question_id, body.answer
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
```

### 32.7 Config Settings (`app/config.py`)

Add after `BUILD_PAUSE_TIMEOUT_MINUTES`:

```python
CLARIFICATION_TIMEOUT_MINUTES: int = 10   # wait before auto-skip
MAX_CLARIFICATIONS_PER_BUILD:   int = 10   # abuse guard
```

---

## 33. Frontend Changes (`web/src/components/ForgeIDEModal.tsx`)

### 33.1 New State

```typescript
const [pendingClarification, setPendingClarification] = useState<{
  questionId: string;
  question: string;
  context?: string;
  options?: string[];
} | null>(null);
```

### 33.2 New WS Event Handlers

```typescript
case 'build_clarification_request': {
  const { question_id, question, context, options } = p;
  setPendingClarification({ questionId: question_id, question, context, options });
  setStatus('awaiting_input');
  setTimeout(() => cmdInputRef.current?.focus(), 100);
  break;
}

case 'build_clarification_resolved': {
  setPendingClarification(null);
  setStatus('running');
  setLogs((prev) => [...prev, {
    timestamp: new Date().toISOString(),
    source: 'user', level: 'info',
    message: `↳ You answered: ${p.answer}`,
  }]);
  break;
}
```

### 33.3 Question Card UI

Rendered just above the command input bar when `pendingClarification !== null`.
Compact amber card (not a modal — stays in the IDE activity area):

```
┌─────────────────────────────────────────────────────────┐  amber border
│ Should authentication use JWT or server-side sessions?  │  bold, amber
│ Implementing login endpoint — no strategy in contracts. │  muted, smaller
│  [JWT (stateless)]  [Sessions (Redis)]  [Let AI decide] │  chip buttons
└─────────────────────────────────────────────────────────┘
```

If no `options` provided: the card shows only the question + context + the existing
free-text command input (which submits via `submitClarification(cmdInput)`).

### 33.4 `submitClarification(answer)` Function

```typescript
const submitClarification = async (answer: string) => {
  if (!pendingClarification) return;
  await fetch(`${API_BASE}/projects/${projectId}/build/clarify`, {
    method: 'POST',
    headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
    body: JSON.stringify({
      question_id: pendingClarification.questionId,
      answer: answer.trim(),
    }),
  });
  // WS event build_clarification_resolved will update state
};
```

Hook the existing command input `onKeyDown` handler: if `pendingClarification` is
active and user presses Enter, call `submitClarification(cmdInput)` instead of
the slash-command path.

Update `placeholder` when `pendingClarification` is active:
```
"Type your answer or choose an option above…"
```

Update command input border/background: same amber style as existing `pendingPrompt`
state (lines 1862–1864) — reuse that visual language.

---

## 34. Implementation Steps (Phase J)

| # | Task | File(s) | Depends |
|---|------|---------|---------|
| J1 | Add `CLARIFICATION_TIMEOUT_MINUTES` and `MAX_CLARIFICATIONS_PER_BUILD` to config | `app/config.py` | — |
| J2 | Add `_clarification_events/answers/counts` dicts to `_state.py` | `app/services/build/_state.py` | J1 |
| J3 | Add 5 helper functions (`register/resolve/pop/increment/cleanup`) to `_state.py` | `app/services/build/_state.py` | J2 |
| J4 | Call `cleanup_clarification()` in `_fail_build()` | `app/services/build/_state.py` | J3 |
| J5 | Add `forge_ask_clarification` tool definition to `FORGE_TOOLS` | `app/services/tool_executor.py` | — |
| J6 | Add `"forge_ask_clarification"` to SCOUT and CODER allowlists | `app/services/build/subagent.py` | J5 |
| J7 | Intercept `forge_ask_clarification` in sub-agent tool dispatch | `app/services/build/subagent.py` | J3 |
| J8 | Add `_handle_clarification()` helper | `app/services/build_service.py` | J3 |
| J9 | Intercept `forge_ask_clarification` in main build loop tool dispatch | `app/services/build_service.py` | J8 |
| J10 | Add `resume_clarification()` service function | `app/services/build_service.py` | J3 |
| J11 | Add `ClarifyRequest` model + `POST /build/clarify` endpoint | `app/api/routers/builds.py` | J10 |
| J12 | Add `pendingClarification` state + 2 WS event handlers | `web/src/components/ForgeIDEModal.tsx` | — |
| J13 | Add question card UI component | `web/src/components/ForgeIDEModal.tsx` | J12 |
| J14 | Add `submitClarification()` + hook command input | `web/src/components/ForgeIDEModal.tsx` | J12 |
| J15 | Tests: state helpers unit tests | `tests/test_build_clarification.py` (new) | J3 |
| J16 | Tests: `_handle_clarification` happy path + timeout + limit | `tests/test_build_clarification.py` | J8 |
| J17 | Tests: `resume_clarification` service + API endpoint | `tests/test_build_clarification.py` + `tests/test_builds_router.py` | J10–J11 |

---

## 35. New WebSocket Events (Phase J)

| Event | Direction | Trigger | Key Payload Fields |
|-------|-----------|---------|-------------------|
| `build_clarification_request` | Server → Client | Builder calls `forge_ask_clarification` | `build_id`, `question_id`, `question`, `context?`, `options?[]` |
| `build_clarification_resolved` | Server → Client | User answers OR timeout fires | `build_id`, `question_id`, `answer` |

---

## 36. Safeguards

| Guard | Mechanism |
|-------|-----------|
| Max 10 questions per build | `increment_clarification_count()` → early return if exceeded |
| 10-minute answer timeout | `asyncio.wait_for(timeout=CLARIFICATION_TIMEOUT_MINUTES*60)` |
| Auto-continue on timeout | Returns "Make your best decision" fallback string as tool result |
| Answer length cap | `ClarifyRequest.answer = Field(max_length=1000)` |
| AUDITOR/FIXER excluded | Not in their `_ROLE_TOOL_NAMES` allowlists |
| Build cleanup | `cleanup_clarification()` in `_fail_build()` and normal build end |
| Build ownership validated | `resume_clarification` checks `build.user_id == requesting_user_id` |

---

## 37. Open Questions (Phase J)

1. **Should the question card support multi-select options?**
   For "which features to include" type questions, multi-select chip lists
   would be more expressive.  Start with single-select for simplicity.

2. **Should clarifications be persisted to the build log in the DB?**
   Currently `append_build_log` is called for both the question and the
   answer.  This means the Q&A pair appears in the build log, which is
   useful for audit trails.  Should it also be stored in a separate
   `build_clarifications` table for structured retrieval?

3. **Should the builder be told it has a question budget?**
   Adding "You have 10 clarification questions available per build" to the
   system prompt would help the builder ration its questions.  Without this,
   the LLM may not know it's being metered.

4. **What happens if the user closes the IDE before answering?**
   The build blocks for 10 minutes then auto-continues.  Should there be
   a reconnection mechanism that re-shows unanswered questions when the
   user reopens the build IDE?  The `build_clarification_request` payload
   could be re-emitted on WebSocket reconnect if a pending clarification
   event exists.

5. **Should the "Let AI decide" option always be injected even if no options
   are provided?**  A dedicated ghost/secondary button (not a chip) in the
   card footer saying "Skip — let AI decide" would always be available,
   avoiding the ambiguity of the user submitting an empty answer.


---

## 38. Errors Tab — Build Error Aggregation & Resolution Tracking

> **Goal**: Surface all build errors in a dedicated **Errors** tab inside the
> ForgeIDEModal console, with deduplication, LLM-authored resolution notes, and
> persistent storage in Neon so errors survive page refreshes and are available
> for post-build review.

### 38a. Motivation

Currently, errors are interleaved in the Activity log stream, easy to miss among
hundreds of info-level messages. Non-technical users need a single place that
answers: "What went wrong? Was it fixed?"

### 38b. Data Model — `build_errors` Table (Neon)

```sql
CREATE TABLE IF NOT EXISTS build_errors (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    build_id        UUID NOT NULL REFERENCES builds(id) ON DELETE CASCADE,
    fingerprint     TEXT NOT NULL,
    first_seen      TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen       TIMESTAMPTZ NOT NULL DEFAULT now(),
    occurrence_count INTEGER NOT NULL DEFAULT 1,
    phase           VARCHAR(100),
    file_path       TEXT,
    source          VARCHAR(50) NOT NULL DEFAULT 'build_log',
    severity        VARCHAR(20) NOT NULL DEFAULT 'error',
    message         TEXT NOT NULL,
    resolved        BOOLEAN NOT NULL DEFAULT false,
    resolved_at     TIMESTAMPTZ,
    resolution_method VARCHAR(30),
    resolution_summary TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_build_errors_build_id
    ON build_errors(build_id);
CREATE INDEX IF NOT EXISTS idx_build_errors_build_id_resolved
    ON build_errors(build_id, resolved);
CREATE INDEX IF NOT EXISTS idx_build_errors_fingerprint
    ON build_errors(build_id, fingerprint);
```

**Key fields:**

| Field | Purpose |
|-------|---------|
| `fingerprint` | Hash of `(source, severity, message_normalized)` — deduplication key |
| `occurrence_count` | Incremented on duplicates (iOS badge-style count in UI) |
| `first_seen` / `last_seen` | Time range the error was active |
| `phase` | Build phase when the error occurred, e.g. "Phase 2: API Routes" |
| `file_path` | Extracted file path from the error message (if parseable) |
| `severity` | `'error'` for normal errors, `'fatal'` for `build_failed` events |
| `resolved` | Flipped true by auto-fix, phase completion, or user dismiss |
| `resolution_method` | `'auto-fix'`, `'phase-complete'`, or `'dismissed'` |
| `resolution_summary` | LLM-authored note explaining what fixed it (not user-editable) |

### 38c. Deduplication Strategy

Errors are fingerprinted using a normalized hash:

```python
import hashlib

def _error_fingerprint(source: str, severity: str, message: str) -> str:
    """Stable fingerprint for deduplication.

    Strips line numbers, memory addresses, and UUIDs from the message
    before hashing so repeated identical errors collapse.
    """
    normalized = re.sub(r'line \d+', 'line N', message)
    normalized = re.sub(r'0x[0-9a-fA-F]+', '0xADDR', normalized)
    normalized = re.sub(
        r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}',
        'UUID', normalized,
    )
    raw = f"{source}:{severity}:{normalized}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]
```

On duplicate: `UPDATE build_errors SET occurrence_count = occurrence_count + 1, last_seen = now() WHERE build_id = $1 AND fingerprint = $2`

### 38d. Resolution Tracking

| Trigger | Method | Summary Source |
|---------|--------|---------------|
| `fix_attempt_result` with `passed: true` | `'auto-fix'` | LLM provides fix summary in event payload |
| `phase_complete` event | `'phase-complete'` | "Phase N completed — errors cleared" |
| User clicks ✕ Dismiss | `'dismissed'` | NULL (no note needed) |

Resolution summaries are authored by the LLM during the build — the build
service will include a `fix_summary` field in `fix_attempt_result` events when a
fix succeeds. Non-technical users cannot write resolution notes.

### 38e. Backend Changes

1. **Migration `023_build_errors.sql`** — creates the table + indices above
2. **`app/repos/build_repo.py`** — new functions:
   - `upsert_build_error(build_id, fingerprint, …)` — INSERT or increment count
   - `resolve_build_error(error_id, method, summary)` — mark resolved
   - `resolve_errors_for_phase(build_id, phase)` — bulk resolve on phase completion
   - `get_build_errors(build_id)` — fetch all errors for a build (unresolved first)
3. **`app/services/build_service.py`** — inside the `_log()` closure, when level == "error":
   call `upsert_build_error`. On `fix_attempt_result(passed=True)`: call
   `resolve_build_error`. On `phase_complete`: call `resolve_errors_for_phase`.
4. **New WS events**:
   - `build_error_tracked` — sent after upsert, payload includes the error record
   - `build_error_resolved` — sent after resolution, includes error_id + method + summary

### 38f. Frontend — `ErrorsPanel.tsx`

New component (~180 lines) rendering:

```
┌── ERRORS (3) ─────────────────────────────────────────────┐
│                                                           │
│  🔴  Phase 2 · api/routes.py              ×3   14:32:05  │
│  ┃   ImportError: cannot import 'Router' from 'fastapi'   │
│  ┃                                                        │
│  ┃   ✅ Auto-fix: Changed import to 'APIRouter'           │
│  ├────────────────────────────────────────────────────────│
│  🔴  Phase 3 · services/auth.py           ×1   14:35:12  │
│  ┃   TypeError: 'NoneType' has no attribute 'encode'      │
│  ┃                                                        │
│  ┃   [✕ Dismiss]                                          │
│  ┃                                                        │
│  ─── Resolved (1) ───────────────────────────────────────│
│  ✅  Phase 2 · api/routes.py  (collapsed)                 │
└───────────────────────────────────────────────────────────┘
```

- Badge count (×N) for deduplicated occurrences — iOS notification style
- Unresolved errors at the top, resolved collapsed at the bottom
- Resolution note shown inline when present (LLM-authored)
- ✕ Dismiss button for user to clear non-fatal errors they don't care about
- Tab label shows unresolved count: `Errors (3)`

### 38g. ForgeIDEModal Integration

- `activeTab` union expands: `'activity' | 'changes' | 'errors'`
- New state: `errors: BuildError[]`
- WS handlers for `build_error_tracked` → upsert into `errors` array
- WS handlers for `build_error_resolved` → update matching error in array
- Tab renders `<ErrorsPanel errors={errors} onDismiss={…} />`

### 38h. Implementation Steps

| # | Task | Scope |
|---|------|-------|
| 1 | Create migration `023_build_errors.sql` | DB |
| 2 | Add repo functions to `build_repo.py` | Backend |
| 3 | Hook into `build_service.py` — error tracking + WS events | Backend |
| 4 | Create `ErrorsPanel.tsx` component | Frontend |
| 5 | Integrate tab + state into `ForgeIDEModal.tsx` | Frontend |
| 6 | Test persistence and UI | Integration |

### 38i. What We're NOT Doing (Scoped Out)

- ❌ User-authored resolution notes (non-technical users)
- ❌ Click-to-scroll in Activity log (over-engineering for target users)
- ❌ Audio cues or notifications
- ❌ Export / copy-all-errors (data lives in Neon, accessed within the app)

---

## §39 — Branch-per-Build Isolation

ForgeGuard's autonomous build system must never push to the user's default branch.
Every build cycle runs on an isolated branch. The sealed branch **is** the deliverable —
the user reviews and merges (or discards) at their discretion.

### 39a. Branch Naming & Creation

The Forge IDE agent creates a branch at the start of each build cycle:

```
forge/build-{build_cycle_id}
```

- **`forge_ide/git_ops.py`** — new `create_build_branch(repo_url, base_sha, cycle_id) -> str`
  Creates `forge/build-{cycle_id}` from `base_sha` (the dossier baseline SHA).
  Returns the branch name. Fails loudly if the branch already exists.
- **`forge_ide/runner.py`** — at build start, call `create_build_branch()` using the
  `baseline_sha` from the linked dossier. All subsequent file writes target this branch.
- Branch is pushed immediately after creation so the remote tracks it from the first commit.

### 39b. Commit Authorship & Tagging

Every commit the Forge IDE makes must be clearly identifiable as machine-authored:

- **Author:** `ForgeGuard Bot <forgeguard-bot@users.noreply.github.com>`
- **Commit message prefix:** `[forge] ` — e.g. `[forge] scaffold project structure`
- **`forge_ide/git_ops.py`** — update `commit_and_push()` to always use the bot author
  and enforce the `[forge]` prefix. Human commits on the branch are allowed (the user can
  push to the branch too) but Forge never commits without the tag.
- **Metadata trailer:** each commit message includes a `Build-Cycle: {cycle_id}` trailer
  so any commit can be traced back to its build cycle.

### 39c. Branch Lifecycle in the IDE Modal

The IDE modal (`ForgeIDEModal.tsx`) must surface the branch context at all times:

| Element | Location | Content |
|---------|----------|---------|
| Branch badge | Header bar, next to project name | `forge/build-abc123` (truncated cycle ID) |
| Baseline SHA | Info panel | First 8 chars of dossier baseline SHA |
| Commit count | Activity tab | Running count of `[forge]` commits on the branch |
| Branch status | Header bar | `active` / `sealing` / `sealed` / `abandoned` |

- **`web/src/components/ForgeIDEModal.tsx`** — add branch badge and status indicator.
  Read from the build cycle API (`/api/build-cycles/{id}`).
- **`web/src/components/ActivityLog.tsx`** — show branch name in log header. Commits
  prefixed with `[forge]` get a bot icon; others get a user icon.

### 39d. Seal Triggers Branch Completion

When the build finishes and the Seal is issued:

1. `build_cycle_service.finish_cycle()` runs the Seal scorer against the branch.
2. The branch receives a final commit: `[forge] sealed — verdict: CERTIFIED` (or CONDITIONAL / FLAGGED).
3. The `build_cycles` row is updated: `status = 'sealed'`, `sealed_at = now()`, `seal_id` linked.
4. The branch is **not** merged or deleted — it remains as-is for the user to review.

- **`forge_ide/runner.py`** — after Seal, push the seal-summary commit and call
  `build_cycle_service.seal_cycle()`.
- **`app/services/build_cycle_service.py`** — `finish_cycle()` validates that the branch
  exists and has at least one `[forge]` commit before allowing seal.

### 39e. Abandoned Cycles

If a user cancels a build or the build fails irrecoverably:

- **`app/services/build_cycle_service.py`** — `abandon_cycle(cycle_id)` sets status to
  `abandoned`. The branch is **not** deleted (preserves forensic value). The dossier remains
  locked. A new build cycle can be started for the same project (new dossier, new branch).
- **`forge_ide/runner.py`** — on unrecoverable failure, call `abandon_cycle()` and log
  the failure reason to the build cycle record.

### 39f. ForgeGuard Never Touches Main

This is a hard invariant:

- **`forge_ide/git_ops.py`** — add a guard in `commit_and_push()`: if the target branch
  is `main`, `master`, or the repo's default branch, raise `ForgeInvariantError`.
  This guard is unconditional and cannot be overridden.
- **`forge_ide/invariants.py`** — add `NEVER_PUSH_DEFAULT_BRANCH` invariant check to the
  pre-commit hook chain.
- **Test:** `test_branch_isolation.py` — verify that any attempt to commit to the default
  branch raises an error, regardless of configuration.

### 39g. MCP Tool Exposure

The MCP server exposes branch management to the IDE agent:

| Tool | Description |
|------|-------------|
| `forge_create_branch` | Create build branch from dossier baseline SHA |
| `forge_get_branch_status` | Return branch name, commit count, status |
| `forge_list_branch_commits` | List commits on the build branch with authorship |
| `forge_seal_branch` | Trigger seal process on the branch |
| `forge_abandon_branch` | Mark cycle as abandoned |

- **`forge_ide/mcp/tools/`** — implement each tool. All tools require a valid
  `build_cycle_id` parameter. The agent cannot operate without a build cycle context.

### 39h. Implementation Order

| Step | Task | Layer |
|------|------|-------|
| 1 | `build_cycles` DB migration + repo | Backend |
| 2 | `create_build_branch()` + default-branch guard in `git_ops.py` | IDE |
| 3 | Bot authorship + `[forge]` prefix in `commit_and_push()` | IDE |
| 4 | Wire `runner.py` to create branch at build start | IDE |
| 5 | `build_cycle_service.py` — start / finish / abandon | Backend |
| 6 | MCP tools for branch management | IDE/MCP |
| 7 | IDE modal branch badge + status | Frontend |
| 8 | Seal-on-branch + final commit | IDE + Backend |
| 9 | Integration tests | Testing |

### 39i. What We're NOT Doing (Scoped Out)

- ❌ Auto-merge to main after seal (user responsibility — ForgeGuard delivers the branch)
- ❌ PR creation from ForgeGuard (future consideration, not in scope)
- ❌ Branch protection rule management (user's GitHub settings, not ours)
- ❌ Multi-branch concurrent builds per project (one active cycle at a time)
- ❌ Branch deletion (branches are permanent forensic records)

---

## §40 — User Notepad (Replaces Hypothesis)

The "hypothesis" field in the Scout deep scan was an attempt to let users provide context
before scanning. In practice it was confusing — users didn't know what to write, and the
LLM ignored it half the time. The concept is valid but belongs in the IDE, not Scout.

### 40a. Concept

A persistent, per-project notepad where the user jots down goals, context, and constraints
that the Forge IDE agent reads as part of its context window. Unlike hypothesis (a one-shot
input tied to a scan), the notepad persists across the entire build cycle and can be edited
at any time.

### 40b. Data Model

- **`db/migrations/`** — new `project_notes` table:
  ```
  id          UUID PRIMARY KEY
  project_id  UUID REFERENCES projects(id)
  user_id     UUID REFERENCES users(id)
  content     TEXT
  updated_at  TIMESTAMPTZ DEFAULT now()
  ```
- One active note per project per user. Updates overwrite (not append).

### 40c. IDE Integration

- **`web/src/components/ForgeIDEModal.tsx`** — add a collapsible "Notepad" panel in the
  sidebar. Auto-saves on blur (debounced 500ms). Shows character count and last-saved time.
- **`forge_ide/context_pack.py`** — when building the agent's context, include the
  notepad content under a `## User Notes` section. The agent sees it as first-class context.
- **`forge_ide/mcp/tools/`** — `forge_read_notepad` tool so the agent can explicitly
  re-read the notepad mid-build if notified of changes.

### 40d. What We're NOT Doing (Scoped Out)

- ❌ Rich text / markdown editing in the notepad (plain text only)
- ❌ Version history of notes (single overwrite)
- ❌ Note sharing between users on the same project
- ❌ LLM-assisted note suggestions
