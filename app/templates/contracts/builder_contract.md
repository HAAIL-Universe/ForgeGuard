# Builder Contract — {project_name}

## §1 Read Gate

The builder MUST read these contract files before any work:
1. `builder_contract.md` (this file)
2. `phases.md`
3. `blueprint.md`
4. `manifesto.md`
5. `stack.md`
6. `schema.md`
7. `physics.yaml`
8. `boundaries.json`
9. `ui.md`
10. `builder_directive.md`

## §2 Verification Hierarchy

Every commit requires four-step verification:
1. **Static** — Syntax checks, linting
2. **Runtime** — Application boots successfully
3. **Behavior** — All tests pass
4. **Contract** — Boundary compliance, schema conformance

## §3 Diff Log

The builder MUST overwrite `evidence/updatedifflog.md` before every commit.

## §4 Audit Ledger

Append-only audit trail in `evidence/audit_ledger.md`.

{builder_contract_extras}
