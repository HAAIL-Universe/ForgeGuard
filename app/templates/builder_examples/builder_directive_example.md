# TaskFlow — Builder Directive

AEM: enabled
Auto-authorize: enabled

## Build Sequence
1. Read all contracts in order: manifesto → blueprint → stack → schema → physics → boundaries → ui → phases → builder_directive
2. Execute Phase 0 — Genesis (scaffold, config, auth, database)
3. Execute Phase 1 — Core Features (projects, columns, tasks)
4. Execute Phase 2 — Frontend (React app, all pages, API integration)
5. Execute Phase 3 — Polish & Ship (tests, error handling, README, deployment)
6. After each phase: run verification, trigger audit, commit on pass

## Phase List
- Phase 0 — Genesis
- Phase 1 — Core Features
- Phase 2 — Frontend
- Phase 3 — Polish & Ship

## Project Summary
TaskFlow is a lightweight Kanban-style task management platform for small teams, built with Python/FastAPI backend, PostgreSQL database, and React/TypeScript frontend.

## Settings
boot_script: true
max_loopback: 3

## Contract Exclusion
Do NOT include Forge contract file contents, contract references, or
contract metadata in any committed source files, READMEs, or code comments.
The `Forge/` directory is server-side only and excluded from git pushes.
