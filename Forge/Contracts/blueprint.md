# ForgeGuard — Blueprint (v0.1)

Status: Draft (authoritative for v0.1 build)
Owner: User
Purpose: Define product fundamentals, hard boundaries, and build targets so implementation cannot drift.

---

## 1) Product intent

ForgeGuard is a dashboard-first web application that monitors Git repositories for code quality, architectural boundary violations, and build compliance using the Forge audit framework.

Users connect their GitHub repositories via OAuth. On every push, ForgeGuard runs a subset of Forge's deterministic audit checks (boundary compliance, dependency hygiene, secrets detection) and displays results on a live dashboard. It provides a health score per repo and a historical audit trail — things GitHub alone does not surface.

The primary interaction model is a two-panel dashboard: a live feed of recent commits with pass/fail status, and a detailed per-commit audit breakdown.

---

## Core interaction invariants (must hold)

- Every connected repo MUST have a health score visible on the repo list.
- Every push event MUST trigger an audit run within 30 seconds of webhook receipt.
- Audit results MUST be persisted — they are never lost, even if the user is not viewing the dashboard.
- The dashboard MUST update in real-time (WebSocket or SSE) without manual refresh.
- Users can ONLY see repos they have access to on GitHub. No cross-user data leakage.

---

## 2) MVP scope (v0.1)

### Must ship

1. **GitHub OAuth login**
   - User authenticates via GitHub OAuth. App stores a GitHub access token (encrypted) to read repo data and register webhooks.

2. **Connect a repo**
   - User selects a GitHub repository from their accessible repos. App registers a webhook on that repo for push events.

3. **Push-triggered audit**
   - On webhook receipt, app fetches the push's changed files and runs boundary compliance (A4), dependency check (A9), and secrets scan (W1) against the diff.

4. **Repo list with health scores**
   - Main dashboard lists all connected repos. Each shows a health badge (green/yellow/red) derived from recent audit pass rate.

5. **Commit audit timeline**
   - Click a repo to see a chronological list of commits with their audit results (pass/fail per check).

6. **Commit detail view**
   - Click a commit to see the full audit breakdown: which checks passed, which failed, with file-level details and reasons.

7. **Real-time updates**
   - Dashboard updates live via WebSocket when new audit results arrive.

### Explicitly not MVP (v0.1)

- Full Forge builder integration (A1, A2, A3, A5, A6, A7, A8 checks — these require a Forge build loop)
- Team/org management (multi-user sharing of repo views)
- Email or Slack notifications on failures
- Custom boundary rules (repos use a default or repo-provided boundaries.json)
- PR-level checks (only push events, not pull request reviews)
- Mobile app
- Billing / subscription management

---

## 3) Hard boundaries (anti-godfile rules)

### Routers (API layer)
- Parse HTTP request, call service, return response.
- MUST NOT contain business logic, database queries, GitHub API calls, or audit logic.

### Services (business logic layer)
- Orchestrate audit runs, compute health scores, manage webhook lifecycle.
- MUST NOT import FastAPI, contain SQL, or make direct HTTP calls to GitHub.

### Repos (data access layer)
- All database reads and writes.
- MUST NOT contain business logic, HTTP concerns, or GitHub API calls.

### Clients (external API layer)
- GitHub API wrapper: OAuth, webhooks, commit data, file content.
- MUST NOT contain business logic, database access, or HTTP framework imports.

### Audit Engine (analysis layer)
- Runs boundary, dependency, and secrets checks against file content.
- MUST NOT access the database, call GitHub, or import HTTP framework code.
- This is a pure function layer: input files + rules → output results.

---

## 4) Deployment target

- Target: Render (web service + managed PostgreSQL)
- Expected users: Under 100 initially
- The app must run as a single process (FastAPI with uvicorn). No worker queues in MVP — audit runs are synchronous within the webhook handler (they're fast enough for single-repo use).
