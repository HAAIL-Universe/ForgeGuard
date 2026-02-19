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
| `tools.py` | Tool definitions + dispatch routing | `config`, `local`, `remote` |
| `server.py` | MCP stdio server wiring (list_tools, call_tool) | `tools` |
| `__main__.py` | Entry point (`python -m forge_ide.mcp`) | `server` |
| `__init__.py` | Package re-export | `server` |

### Rules

1. **No reverse imports** — `config` and `cache` never import other MCP modules.
2. **`tools.py` is the single dispatch boundary** — `server.py` calls `dispatch()` only.
3. **`local.py` and `remote.py` are peers** — neither imports the other. `tools.py` picks one based on `LOCAL_MODE`.
4. **No cross-package side effects** — MCP modules don't import from `app/` (they read disk or call API).

---

## Tool Catalogue

| # | Tool | Input | Returns | Dispatch |
|---|------|-------|---------|----------|
| 1 | `forge_summary` | — | Framework overview: contracts, layers, invariants, endpoints | `get_summary()` / `GET /forge/summary` |
| 2 | `forge_list_contracts` | — | Contract names, filenames, formats, availability | `list_contracts()` / `GET /forge/contracts` |
| 3 | `forge_get_contract` | `name: string` | Full contract content (parsed JSON/YAML or raw markdown) | `load_contract(name)` / `GET /forge/contracts/{name}` |
| 4 | `forge_get_invariants` | — | Invariant gate definitions, constraint types, defaults | `get_invariants()` / `GET /forge/invariants` |
| 5 | `forge_get_boundaries` | — | Layer boundary rules (shortcut for `get_contract("boundaries")`) | `load_contract("boundaries")` / `GET /forge/boundaries` |
| 6 | `forge_get_physics` | — | Canonical API spec (shortcut for `get_contract("physics")`) | `load_contract("physics")` / `GET /forge/physics` |
| 7 | `forge_get_directive` | — | Builder directive (shortcut for `get_contract("builder_directive")`) | `load_contract("builder_directive")` / `GET /forge/directive` |
| 8 | `forge_get_stack` | — | Tech stack contract (shortcut for `get_contract("stack")`) | `load_contract("stack")` / `GET /forge/stack` |

### Planned (MCP Builder Plan — Phase A)

| # | Tool | Input | Returns |
|---|------|-------|---------|
| 9 | `forge_get_phase_window` | `phase_number: int` | Current + next phase deliverables from phases.md |
| 10 | `forge_scratchpad` | `operation: read\|write\|append\|list`, `key?`, `value?` | Persistent KV store per build |

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
- **Keys**: `"contract:{name}"`, `"list_contracts"`, `"invariants"`, `"summary"`, `"api:{path}"`
- **Eviction**: None (stale entries linger until re-checked)
- **Invalidation**: `cache_clear()` resets entire store

---

## Modes

| Mode | Trigger | Data source |
|------|---------|-------------|
| **Local** | `FORGEGUARD_LOCAL=1` | Reads `Forge/Contracts/` from disk |
| **Remote** | Default | Proxies to `FORGEGUARD_URL` (default `localhost:8000`) via httpx |
