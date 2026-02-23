# MCP Package Specification

> Lightweight contract governing `forge_ide/mcp/` — module boundaries,
> tool catalogue, and dependency rules.

---

## Module Boundaries

| Module | Responsibility | May import from |
|--------|---------------|-----------------|
| `config.py` | Constants, paths, contract registry, env vars | stdlib only |
| `cache.py` | In-memory TTL cache (300s) | stdlib only |
| `local.py` | Disk-based contract reads + invariant loading | `config`, `cache` |
| `remote.py` | HTTP API proxy via httpx | `config`, `cache` |
| `artifact_store.py` | Memory + disk artifact store (TTL-aware) | `config` |
| `session.py` | Session singleton (project_id, build_id) | — |
| `project.py` | Project-scoped DB contract handlers | `config`, `session`, `remote` |
| `tools.py` | Tool definitions + dispatch routing | `config`, `local`, `remote`, `artifact_store`, `session`, `project` |
| `server.py` | MCP stdio server wiring (list_tools, call_tool) | `tools` |
| `__main__.py` | Entry point (`python -m forge_ide.mcp`) | `server` |
| `__init__.py` | Package re-export | `server` |

### Rules

1. **No reverse imports** — `config` and `cache` never import other MCP modules.
2. **`tools.py` is the single dispatch boundary** — `server.py` calls `dispatch()` only.
3. **`local.py` and `remote.py` are peers** — neither imports the other. `tools.py` picks one based on `LOCAL_MODE`.
4. **No cross-package side effects** — MCP modules don't import from `app/` (they read disk or call API).

---

## Tool Catalogue (16 tools)

Reduced from 19 by removing 4 redundant shortcut tools (`forge_get_boundaries`,
`forge_get_physics`, `forge_get_directive`, `forge_get_stack`) and adding
`forge_get_governance`. Shortcuts remain callable via dispatch for backward
compatibility but are not advertised in `list_tools()`.

### Governance (5 tools)

| # | Tool | Input | Returns | Dispatch |
|---|------|-------|---------|----------|
| 1 | `forge_get_governance` | — | Consolidated read gate: builder contract + boundaries + stack + invariants | `get_governance()` |
| 2 | `forge_summary` | — | Framework overview: contracts, layers, invariants, endpoints | `get_summary()` / `GET /forge/summary` |
| 3 | `forge_get_contract` | `name: enum` | Full contract content (parsed JSON/YAML or raw markdown) | `load_contract(name)` / `GET /forge/contracts/{name}` |
| 4 | `forge_list_contracts` | — | Contract names, filenames, formats, availability | `list_contracts()` / `GET /forge/contracts` |
| 5 | `forge_get_invariants` | — | Invariant gate definitions, constraint types, defaults | `get_invariants()` / `GET /forge/invariants` |

### Artifact Store (4 tools)

| # | Tool | Input | Returns | Dispatch |
|---|------|-------|---------|----------|
| 6 | `forge_store_artifact` | `project_id`, `artifact_type: enum`, `key`, `content`, `ttl_hours?`, `persist?` | Store confirmation | In-process memory + disk |
| 7 | `forge_get_artifact` | `project_id`, `artifact_type: enum`, `key` | Artifact content | Memory (TTL) → disk fallback |
| 8 | `forge_list_artifacts` | `project_id`, `artifact_type?` | Key list with metadata | In-process scan |
| 9 | `forge_clear_artifacts` | `project_id`, `artifact_type?` | Deletion confirmation | Memory + disk delete |

### Session Management (2 tools)

| # | Tool | Input | Returns | Dispatch |
|---|------|-------|---------|----------|
| 10 | `forge_set_session` | `project_id`, `build_id?`, `user_id?` | Session confirmation | Module-level singleton |
| 11 | `forge_clear_session` | — | Reset confirmation | Module-level singleton |

### Project-Scoped DB Tools (4 tools)

| # | Tool | Input | Returns | Dispatch |
|---|------|-------|---------|----------|
| 12 | `forge_get_project_context` | `project_id?` | Project manifest (metadata) | `GET /api/mcp/context/{id}` |
| 13 | `forge_list_project_contracts` | `project_id?` | Contract types, versions, timestamps | `GET /api/mcp/context/{id}` |
| 14 | `forge_get_project_contract` | `project_id?`, `contract_type: enum` | Full contract content | `GET /api/mcp/context/{id}/{type}` |
| 15 | `forge_get_build_contracts` | `build_id?` | Pinned contract snapshot (immutable) | `GET /api/mcp/build/{id}/contracts` |

### Planner (1 tool)

| # | Tool | Input | Returns | Dispatch |
|---|------|-------|---------|----------|
| 16 | `forge_run_planner` | `project_request` | Plan path, phases, token usage, turn trace | In-executor thread |

### Annotations

All tools include MCP annotations:
- `readOnlyHint: true` for all read tools (governance, get_artifact, list_*, get_project_*, get_build_*)
- `destructiveHint: true` for `forge_clear_artifacts`
- `idempotentHint: true` for all reads + store/set operations
- `openWorldHint: true` for `forge_run_planner` (calls external LLM API)

### Deprecated Shortcuts (backward compat only)

These tools are **not advertised** in `list_tools()` but still handled by dispatch:
- `forge_get_boundaries` → use `forge_get_contract(name="boundaries")`
- `forge_get_physics` → use `forge_get_contract(name="physics")`
- `forge_get_directive` → use `forge_get_contract(name="builder_directive")`
- `forge_get_stack` → use `forge_get_contract(name="stack")`

---

## Contract Registry

15 contracts in `Forge/Contracts/`, mapped by `CONTRACT_MAP` in `config.py`:

| Logical Name | File | Format |
|-------------|------|--------|
| boundaries | boundaries.json | JSON |
| physics | physics.yaml | YAML |
| blueprint | blueprint.md | Markdown |
| builder_contract | builder_contract.md | Markdown |
| builder_directive | builder_directive.md | Markdown |
| manifesto | manifesto.md | Markdown |
| phases | phases.md | Markdown |
| schema | schema.md | Markdown |
| stack | stack.md | Markdown |
| system_prompt | system_prompt.md | Markdown |
| ui | ui.md | Markdown |
| auditor_prompt | auditor_prompt.md | Markdown |
| recovery_planner_prompt | recovery_planner_prompt.md | Markdown |
| remediation | Remediation.md | Markdown |
| desktop_distribution_plan | Desktop_Distribution_Plan.md | Markdown |

---

## Cache Behaviour

- **TTL**: 300 seconds (5 minutes)
- **Scope**: Process-local `dict[str, tuple[float, Any]]`
- **Keys**: `"contract:{name}"`, `"list_contracts"`, `"invariants"`, `"summary"`, `"governance"`, `"api:{path}"`
- **Eviction**: None (stale entries linger until re-checked)
- **Invalidation**: `cache_clear()` resets entire store

---

## Modes

| Mode | Trigger | Data source | Tools |
|------|---------|-------------|-------|
| **Local** | `FORGEGUARD_LOCAL=1` | Reads `Forge/Contracts/` from disk | All 16 |
| **Remote** | Default | Proxies to `FORGEGUARD_URL` (default `localhost:8000`) via httpx | All 16 |
