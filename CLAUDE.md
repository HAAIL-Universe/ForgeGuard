# ForgeGuard — Claude Code Instructions

## Governance

Before modifying ForgeGuard source code, call the `forge_get_governance` MCP tool to read:
- The builder contract (master governance rules)
- Architecture layer boundaries (forbidden imports per layer)
- Technology stack constraints (required frameworks and versions)
- Invariant gates (hard constraints that must never be violated)

Use `forge_get_contract(name)` to read any individual contract in full.

## Architecture Layers (from boundaries.json)

- **routers** (`app/api/routers/`) — HTTP only. No DB, no httpx, no audit_engine.
- **repos** (`app/repos/`) — DB access only. No httpx, no fastapi, no audit_engine.
- **clients** (`app/clients/`) — External API calls only. No DB, no fastapi.
- **audit_engine** (`app/audit/`) — Pure analysis. No DB, no HTTP, no fastapi.
- **services** (`app/services/`) — Business logic. No raw SQL, no fastapi.

## Key Rules

1. **Minimal-diff rule**: Change as little as possible. No renames, no cleanup, no refactors unless requested.
2. **No godfiles**: Each layer does only its job. A router must not contain SQL. A service must not import fastapi.
3. **Test invariants**: Test count must never decrease. Test failures must remain at zero.
4. **Stack compliance**: Python 3.12+, FastAPI, asyncpg (no ORM), React + Vite + TypeScript frontend.
