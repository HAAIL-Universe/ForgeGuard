# [PROJECT NAME] — Blueprint

⚠ FORMAT SPECIFICATION ONLY.
Do NOT carry any names, features, or decisions from this skeleton into your output.
All bracketed text is a placeholder. Replace every item with the user's actual project details.

## Product Intent

- [What the application does — one line]
- [Who it is for — target user or team]
- [Why it exists — the problem it solves]

## Core Interaction Invariants

- [Primary invariant about data ownership or scoping]
- [Invariant about user permissions or access control]
- [Invariant about data mutation rules — what must always be true]
- [Invariant about what cannot be permanently destroyed]
- [Invariant about data isolation or multi-tenancy]

## MVP Scope

### Must-Ship Features

1. **[Feature name]** — [one-line description of what the feature does]
2. **[Feature name]** — [one-line description]
3. **[Feature name]** — [one-line description]

### Explicitly NOT in MVP

- [Out-of-scope item]
- [Out-of-scope item]

## Hard Boundaries

- **Routers** — HTTP parsing only; no business logic, no direct DB queries
- **Services** — all business logic; no HTTP concepts (Request/Response)
- **Repos** — data access only; no business decisions
- **Clients** — external API wrappers only; no business logic

## Deployment Target

- [Deployment method and environment — one line]
- [Expected scale — one line]
