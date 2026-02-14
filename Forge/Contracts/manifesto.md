# ForgeGuard — Manifesto (v0.1)

This document defines the non-negotiable principles for building ForgeGuard.
If implementation conflicts with this manifesto, implementation is wrong.

---

## 1) Product principle: dashboard-first, data-backed

ForgeGuard is a dashboard-first application that monitors repository health through deterministic audits.

- The dashboard is the primary control surface.
- Audit results are the source of truth — never fabricated, never approximated.
- Health scores are derived from persisted audit data, not cached or guessed.

---

## 2) Contract-first, schema-first

ForgeGuard is built from contracts, not vibes.

- `physics.yaml` is the API specification and is canonical.
- Pydantic models mirror the physics spec.
- If it's not in `physics.yaml`, it isn't real.

---

## 3) No godfiles, no blurred boundaries

Everything has a lane.

- **Routers** — HTTP parsing and response formatting only.
- **Services** — Business logic, orchestration, audit coordination.
- **Repos** — Database reads and writes only.
- **Clients** — External API calls (GitHub) only.
- **Audit engine** — Pure analysis functions: files + rules → results.

No layer is allowed to do another layer's job.
Violations are bugs, even if the feature works.

---

## 4) Auditability over cleverness

We value "debuggable and correct" over "magic and fast."

- Every audit run is persisted with its full input (commit SHA, files checked) and output (per-check results).
- Every webhook event is logged with timestamp, repo, and processing status.
- A developer should be able to answer: "What exactly did ForgeGuard check on commit X, and what did it find?"

---

## 5) Reliability over false confidence

The system must be honest about what it reports.

- If an audit check cannot run (e.g., file content unavailable), the result is `ERROR`, not `PASS`.
- If a webhook fails processing, the failure is logged and visible — not silently swallowed.
- Health scores reflect actual audit data. A repo with no recent audits shows "unknown", not green.

---

## 6) Confirm-before-write (default)

ForgeGuard should not mutate user data based on ambiguous input.

Default flow for writes:
1. User initiates an action (connect repo, disconnect repo).
2. UI shows a confirmation dialog.
3. On confirm, the action is executed.
4. On decline, nothing changes.

### Exempt from confirmation
- Webhook-triggered audit runs (automatic, no user interaction)
- Dashboard refresh / real-time updates (read-only)
- Health score recalculation (derived, no user action)

---

## 7) Privacy by default

- GitHub tokens are encrypted at rest.
- ForgeGuard only reads repository data — it never writes to user repos (except registering/removing webhooks).
- Audit results are scoped to the repo owner. No cross-user visibility in MVP.
- File content fetched for auditing is processed in memory and not persisted. Only the audit results (pass/fail + reasons) are stored.
