# [PROJECT NAME] — Builder Directive

⚠ FORMAT SPECIFICATION ONLY.
Replace all bracketed content with project-specific instructions.
Phase list MUST match the phases contract exactly.
All tech references MUST match the canonical anchor and stack contract.

## Build Instructions

1. Read all contracts: blueprint, stack, schema, physics, boundaries, ui, phases
2. Execute phases in order, starting with Phase 0 — Genesis
3. After each phase: run tests, run forge audit, commit if passing
4. On audit failure: stop and report — do not proceed to next phase

## Autonomy Rules

- **Auto-commit:** file creation, passing tests, documentation updates
- **Stop and ask:** database schema changes, external service credentials, irreversible destructive operations, security policy decisions

## Phase List

- Phase 0: Genesis
- Phase 1: [name from phases contract]
- Phase N: [name from phases contract — repeat for each phase]

## Project Summary

[One sentence describing what is being built — from the blueprint product intent.]
