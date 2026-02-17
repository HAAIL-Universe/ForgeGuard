# ForgeGuard IDE — Architecture & Testing Guide

## What Is It?

The **ForgeGuard IDE** (`forge_ide/`) is an in-process, headless IDE runtime — a standalone Python package that gives the AI builder structured, type-safe access to the workspace during automated builds. Instead of raw `open()` calls and string parsing, every file read, code search, diff, patch, test run, and diagnostic check flows through a typed contract layer that returns Pydantic models.

It is **not** a visual editor. Think of it as the engine behind an IDE — the part that reads files, searches code, parses diffs, scores relevance, and assembles context — minus the UI. The builder agent (Claude) uses it as its toolkit during `plan-then-execute` builds.

---

## Architecture (6 Layers)

```
┌─────────────────────────────────────────────────────────────────┐
│  BRIDGE LAYER                                                   │
│  adapters.py — wraps tool_executor handlers → ToolResponse      │
│  register_builtin_tools(registry) wires all 7 tools             │
├─────────────────────────────────────────────────────────────────┤
│  COMPOSITION LAYER                                              │
│  context_pack.py — assemble_pack, pack_to_text, estimate_tokens │
│  relevance.py — find_related, score_import_graph, score_*       │
│  build_helpers.py — apply_response, run_and_summarise           │
├─────────────────────────────────────────────────────────────────┤
│  INTELLIGENCE LAYER                                             │
│  lang/python_intel.py — extract symbols, resolve imports, AST   │
│  lang/typescript_intel.py — TSC output, ESLint, TS symbols      │
│  diagnostics.py — merge_diagnostics, DiagnosticReport           │
│  log_parser.py — pytest/npm/build log summarisers               │
│  response_parser.py — classify diff vs full content             │
├─────────────────────────────────────────────────────────────────┤
│  TOOL LAYER                                                     │
│  reader.py — ide_read_file, read_range, read_symbol             │
│  searcher.py — regex/literal search with Match models           │
│  runner.py — sandboxed command execution, validate_command       │
│  patcher.py — parse_unified_diff, apply_patch, PatchResult      │
│  diff_generator.py — generate_diff, diff_to_text                │
│  file_index.py — FileIndex, FileMetadata (size, lang, mtime)    │
│  git_ops.py — git status, diff, log → ToolResponse              │
├─────────────────────────────────────────────────────────────────┤
│  INFRASTRUCTURE LAYER                                           │
│  workspace.py — Workspace (path sandbox, file tree, summary)    │
│  backoff.py — ExponentialBackoff, ConcurrencyLimiter            │
│  redactor.py — 12-pattern secret detection, redact()            │
│  sanitiser.py — deterministic sorting, noise stripping          │
├─────────────────────────────────────────────────────────────────┤
│  CONTRACT LAYER                                                 │
│  contracts.py — Pydantic models (ToolRequest, ToolResponse,     │
│                 ReadFileRequest, UnifiedDiff, Diagnostic, etc.)  │
│  errors.py — IDEError, SandboxViolation, PatchConflict, etc.    │
│  registry.py — Registry (register, dispatch, list_tools)        │
└─────────────────────────────────────────────────────────────────┘
```

### Module inventory (25 files, ~6,300 lines)

| Module | Lines | Purpose |
|---|---|---|
| `contracts.py` | ~420 | All Pydantic request/response models |
| `registry.py` | ~120 | Tool dispatch + validation |
| `errors.py` | ~90 | Exception hierarchy |
| `workspace.py` | ~210 | Path sandboxing, file tree, summary |
| `reader.py` | ~170 | File reading (full / range / symbol) |
| `searcher.py` | ~120 | Regex + literal code search |
| `runner.py` | ~170 | Sandboxed command execution |
| `patcher.py` | ~375 | Unified diff parse + apply |
| `diff_generator.py` | ~140 | `difflib.unified_diff` → `UnifiedDiff` model |
| `file_index.py` | ~200 | Per-file metadata index |
| `git_ops.py` | ~280 | Git status/diff/log wrappers |
| `backoff.py` | ~120 | Exponential backoff + concurrency limiter |
| `redactor.py` | ~150 | Secret detection & `[REDACTED]` replacement |
| `sanitiser.py` | ~175 | Deterministic sort, noise strip, path normalise |
| `log_parser.py` | ~550 | Test/build output parsing (pytest, npm, generic) |
| `diagnostics.py` | ~150 | Merge diagnostics → `DiagnosticReport` |
| `response_parser.py` | ~160 | Classify LLM output as diff or full content |
| `build_helpers.py` | ~170 | Apply LLM response to files, summarise runs |
| `context_pack.py` | ~330 | Assemble context packs for the builder |
| `relevance.py` | ~250 | Score file relevance (imports, proximity, recency) |
| `lang/python_intel.py` | ~320 | Python AST: symbols, imports, errors |
| `lang/typescript_intel.py` | ~310 | TS: symbols, TSC/ESLint parsing |
| `lang/common.py` | ~50 | Language detection |
| `adapters.py` | ~375 | Bridge to existing `tool_executor` handlers |
| `__init__.py` | ~365 | 110 public exports |

---

## How It Connects to the App Layer

### Current wiring (minimal)

Today, the app layer uses **one** forge_ide component:

```
app/services/tool_executor.py
  └── from forge_ide.workspace import Workspace
      └── _resolve_sandboxed(ws, path) → safe absolute path
```

The `tool_executor` has 7 `_exec_*` handlers (`read_file`, `list_directory`, `search_code`, `write_file`, `run_tests`, `check_syntax`, `run_command`) that the builder agent calls during builds. These handlers use `Workspace` for path sandboxing but do their own file I/O directly.

### The bridge layer (built, not yet wired)

`forge_ide/adapters.py` contains `register_builtin_tools(registry)` which wraps all 7 `_exec_*` handlers into the typed `Registry → ToolResponse` system. When wired:

```python
# In build_service.py (future)
from forge_ide import Registry, register_builtin_tools

registry = Registry()
register_builtin_tools(registry)

# Each tool call returns a ToolResponse instead of a raw string
response = await registry.dispatch("read_file", {"path": "src/main.py"}, work_dir)
# response.success → True
# response.data → {"content": "...", "lines": 42, "size": 1234}
```

### What appears in the activity log today

During a build, `build_service.py` broadcasts WebSocket events through `ws_manager.send_to_user()`. These show up on the BuildProgress page as activity log entries:

| Event type | When | What you see |
|---|---|---|
| `build_started` | Build begins | "Build started for project X" |
| `workspace_ready` | Repo cloned/checked out | "Workspace ready at /path" |
| `build_log` | Each builder step | Free-text log messages |
| `tool_use` | Agent calls a tool | "Tool: read_file — src/main.py" |
| `file_created` / `file_modified` | Agent writes a file | Path + byte count |
| `test_run` | Tests executed | Pass/fail summary |
| `phase_complete` | Plan-exec phase finishes | Phase name + status |
| `audit_pass` / `audit_fail` | Audit completes | Audit verdict + details |
| `build_complete` | Build finishes | Final status + token usage |

The `tool_use` events come from `tool_executor` and currently carry the raw tool name and parameters. When the adapter bridge is wired, these would carry structured `ToolResponse` data.

---

## Test Suite

### Coverage breakdown

| Test location | Count | What it tests |
|---|---|---|
| `Forge/IDE/tests/test_contracts.py` | 60 | Pydantic model validation |
| `Forge/IDE/tests/test_registry.py` | 36 | Tool registration, dispatch, errors |
| `Forge/IDE/tests/test_errors.py` | 16 | Exception hierarchy |
| `Forge/IDE/tests/test_workspace.py` | 40 | Path sandbox, file tree, summary |
| `Forge/IDE/tests/test_reader.py` | 34 | File reading modes |
| `Forge/IDE/tests/test_searcher.py` | 32 | Regex/literal search |
| `Forge/IDE/tests/test_runner.py` | 20 | Command sandboxing |
| `Forge/IDE/tests/test_patcher.py` | 48 | Diff parse + patch apply |
| `Forge/IDE/tests/test_diff_generator.py` | 30 | Diff generation |
| `Forge/IDE/tests/test_file_index.py` | 26 | File metadata indexing |
| `Forge/IDE/tests/test_git_ops.py` | 32 | Git operation wrappers |
| `Forge/IDE/tests/test_backoff.py` | 22 | Backoff + concurrency |
| `Forge/IDE/tests/test_redactor.py` | 34 | Secret detection/redaction |
| `Forge/IDE/tests/test_sanitiser.py` | 34 | Output sanitisation |
| `Forge/IDE/tests/test_log_parser.py` | 48 | Log parsing |
| `Forge/IDE/tests/test_diagnostics.py` | 30 | Diagnostic merging |
| `Forge/IDE/tests/test_response_parser.py` | 28 | Response classification |
| `Forge/IDE/tests/test_build_helpers.py` | 32 | Apply response + summaries |
| `Forge/IDE/tests/test_context_pack.py` | 30 | Context pack assembly |
| `Forge/IDE/tests/test_relevance.py` | 28 | Relevance scoring |
| `Forge/IDE/tests/test_lang.py` | 44 | Python + TS intelligence |
| `Forge/IDE/tests/test_adapters.py` | 34 | Bridge adapters |
| **`Forge/IDE/tests/test_smoke.py`** | **35** | **End-to-end smoke tests** |
| **Total IDE tests** | **~904** | |

### Smoke tests (the proof it works)

The 35 smoke tests in `test_smoke.py` are integration tests that exercise **real filesystem I/O** and **cross-module data flow** — no mocking. They prove the modules work together, not just in isolation:

| Smoke test class | Tests | What it proves |
|---|---|---|
| `TestWorkspaceReaderSearcher` | 7 | Write files to disk → Workspace scans them → reader reads → searcher finds patterns |
| `TestFileIndexRelevanceContextPack` | 4 | FileIndex builds metadata → relevance scoring ranks files → context pack assembles with token estimates |
| `TestPatcherDiffRoundTrip` | 3 | `generate_diff` → `diff_to_text` → `apply_patch` roundtrip preserves content |
| `TestResponseParserBuildHelpers` | 3 | `classify_response` + `apply_response` for full/diff/fenced content |
| `TestLogParsers` | 2 | Real pytest + npm test output → structured summaries |
| `TestLanguageIntelligence` | 4 | AST symbol extraction + import resolution + diagnostics |
| `TestRedactorSanitiser` | 3 | Secret redaction + output sanitisation pipeline |
| `TestBackoffConcurrency` | 2 | Backoff timing + concurrency limiter tracking |
| `TestRegistry` | 1 | Register handler → dispatch with Pydantic validation → get response |
| `TestCrossModuleIntegration` | 3 | Full round-trips: read → extract symbols → check diagnostics; write → diff → patch → verify; context pack with relevance |
| `TestRunner` | 3 | Command validation: safe commands pass, `rm -rf` / injection blocked |

---

## How to Manually Test & Verify

### 1. Run the smoke tests

```powershell
# From the workspace root
python -m pytest Forge\IDE\tests\test_smoke.py -v
```

All 35 should pass. These use real temp directories and real file I/O — no mocks.

### 2. Run the full IDE test suite

```powershell
python -m pytest Forge\IDE\tests\ -q
```

Should show ~904 passed.

### 3. Interactive REPL exploration

Open a Python REPL and try the IDE yourself:

```python
from forge_ide import Workspace, ide_read_file, search, FileIndex

# Point at the project itself
ws = Workspace("z:/ForgeCollection/ForgeGuard")
print(ws.summary())
# → WorkspaceSummary(total_files=..., total_size_bytes=..., languages={...})

# Read a file
resp = ide_read_file(ws, "app/main.py")
print(resp.success)           # True
print(resp.data["lines"])     # line count
print(resp.data["content"][:200])  # first 200 chars

# Search for a pattern
import asyncio
matches = asyncio.run(search(ws, r"def _exec_\w+", regex=True))
for m in matches[:5]:
    print(f"  {m.path}:{m.line}: {m.text.strip()}")

# Build a file index
idx = FileIndex(ws)
print(f"Indexed {len(idx)} files")
meta = idx.get("app/main.py")
print(f"  language={meta.language}, size={meta.size}")
```

### 4. Diff → Patch cycle

```python
from forge_ide import generate_diff, diff_to_text, apply_response

old = "x = 1\ny = 2\n"
new = "x = 10\ny = 2\nz = 3\n"

diff = generate_diff(old, new, path="example.py")
print(f"  +{diff.insertions} -{diff.deletions}")

text = diff_to_text(diff)
print(text)
# --- a/example.py
# +++ b/example.py
# @@ -1,2 +1,3 @@
# -x = 1
# +x = 10
#  y = 2
# +z = 3

result = apply_response(old, text)
print(result.method)   # "patch"
print(result.content)  # "x = 10\ny = 2\nz = 3\n"
```

### 5. Secret redaction

```python
from forge_ide import redact, has_secrets, find_secrets

text = "API_KEY=sk-abc123secret\nDB_HOST=localhost"
print(has_secrets(text))  # True

matches = find_secrets(text)
for m in matches:
    print(f"  pattern={m.pattern_name} span={m.start}-{m.end}")

print(redact(text))
# API_KEY=[REDACTED]
# DB_HOST=localhost
```

### 6. Context pack assembly

```python
from forge_ide import (
    Workspace, FileIndex, TargetFile,
    assemble_pack, pack_to_text, estimate_tokens
)

ws = Workspace("z:/ForgeCollection/ForgeGuard")
idx = FileIndex(ws)

pack = assemble_pack(
    workspace=ws,
    index=idx,
    targets=[TargetFile(path="app/services/tool_executor.py")],
    task="Add error handling to the read_file tool",
    max_tokens=8000,
)

text = pack_to_text(pack)
print(f"Context pack: {estimate_tokens(text)} tokens")
print(text[:500])
# Shows: repo summary, structure tree, target file content,
# dependency snippets (scored by relevance), task description
```

### 7. Verify during a real build

Start a build from the UI and watch the **Activity Log** on the BuildProgress page. You'll see:

1. `workspace_ready` — the `Workspace` object was created (forge_ide)
2. `tool_use` events — each time the builder calls `read_file`, `write_file`, `search_code`, etc. These flow through `tool_executor._exec_*` handlers, which use `Workspace._resolve_sandboxed()` (forge_ide) for path safety
3. `test_run` — test results (could be parsed by forge_ide log parsers in future)
4. `file_created` / `file_modified` — file writes that went through the sandbox

The path sandboxing is the critical connection: every file path the builder agent provides gets resolved through `forge_ide.Workspace`, which ensures the agent can **never** read or write outside the project directory.

---

## Key Architectural Insight

The forge_ide package is a **fully tested library** (904 tests) that provides everything needed for structured, typed tool execution. The connection to the app layer is currently **narrow by design** — only `Workspace` for sandboxing.

The bridge layer (`adapters.py`) exists and is tested, ready to be wired in when needed. When activated, it will replace raw string returns from `tool_executor` with typed `ToolResponse` objects, giving the activity log richer structured data instead of plain text.

The library is designed so each layer can be used independently. You can use `generate_diff` without `Workspace`, `search` without `Registry`, or `redact` without `context_pack`. This makes it easy to adopt incrementally.
