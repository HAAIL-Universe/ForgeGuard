# ForgeGuard Desktop Distribution Plan

> Purpose: turn ForgeGuard from a dev-run web stack into a downloadable app users can install on Windows/macOS/Linux.
> Date: 2026-02-17

---

## 1. Executive Summary

ForgeGuard should be shipped as a **desktop shell + hosted backend**.

- Desktop app: Electron (fastest path with current React/Vite frontend).
- Backend/API: existing FastAPI service deployed to production.
- Database: hosted PostgreSQL.
- GitHub OAuth + webhooks: stay server-side on the hosted backend.

This avoids trying to run webhooks and OAuth callbacks on end-user local machines, which is fragile.

---

## 2. Scope and Non-Goals

### In scope

- Installable app packages (`.exe`, `.dmg`, `.AppImage` or `.deb/.rpm`).
- Single-click launch experience for end users.
- Secure auth flow from desktop to hosted backend.
- Signed builds, versioning, and update path.

### Out of scope (v1)

- Fully offline/local-only mode.
- Bundling PostgreSQL on user machines.
- Receiving GitHub webhooks directly on local desktop apps.

---

## 3. Target Architecture

1. User installs ForgeGuard Desktop.
2. Desktop shell loads bundled React build (`web/dist`).
3. Frontend calls production API over HTTPS.
4. OAuth login opens system browser and returns token via callback/deep link.
5. Webhook processing remains on hosted backend.
6. WebSocket updates flow from backend to desktop UI as they do today.

---

## 4. Delivery Phases

## Phase A - Product and Security Decisions (1-2 days)

Deliverables:
- Final decision doc: Electron vs Tauri (recommend Electron for v1 speed).
- Distribution targets: Windows first, then macOS/Linux.
- Release channel policy: stable + optional prerelease.
- Secret handling policy for desktop app.

Acceptance criteria:
- Team agrees on runtime, supported OSes, and release cadence.

## Phase B - Production Backend Readiness (2-5 days)

Deliverables:
- Stable production deployment for FastAPI + Postgres.
- Production env vars and domain config.
- CORS/FRONTEND_URL updated for desktop/web production origins.
- Monitoring + logs + error tracking in place.

Acceptance criteria:
- Hosted backend can serve current web app reliably.
- OAuth and webhook paths are fully functional in production.

## Phase C - Desktop Shell Foundation (3-6 days)

Deliverables:
- New `desktop/` project with Electron entrypoint.
- Local loading of bundled Vite output.
- Environment switch for API base URL (dev/stage/prod).
- IPC boundary for any native features (if needed).

Acceptance criteria:
- App launches locally and renders the existing UI without functional regressions.

## Phase D - Auth and Session Flow for Desktop (2-4 days)

Deliverables:
- System-browser OAuth launch from desktop app.
- Callback handling strategy:
  - Preferred: custom protocol deep link (`forgeguard://auth/callback`), or
  - Fallback: local loopback callback port.
- Token storage policy (OS keychain/credential vault preferred).
- Logout/token refresh behavior documented and implemented.

Acceptance criteria:
- User can install app, log in with GitHub, relaunch app, and stay signed in securely.

## Phase E - Packaging and Installers (2-4 days)

Deliverables:
- Build pipeline using `electron-builder`.
- Artifacts:
  - Windows: `.exe` installer.
  - macOS: `.dmg` (and notarization plan).
  - Linux: `.AppImage` (or distro packages).
- App metadata: icon, app name, version, publisher.

Acceptance criteria:
- Fresh machine install succeeds and app launches on each targeted OS.

## Phase F - Updates, Signing, and Release Ops (2-5 days)

Deliverables:
- Code signing certificates integrated.
- CI/CD jobs that produce versioned desktop artifacts.
- Release notes template and rollback procedure.
- Optional auto-update channel for minor versions.

Acceptance criteria:
- A tagged release builds signed artifacts and can be distributed safely.

## Phase G - QA and Hardening (3-7 days)

Deliverables:
- End-to-end test checklist (install -> login -> connect repo -> receive audit updates).
- Network failure behavior tests (offline, timeout, API 5xx).
- Crash/error telemetry validation.
- Support runbook for common user issues.

Acceptance criteria:
- Critical flows pass on all target OSes.
- No high-severity security/usability blockers remain.

---

## 5. Work Breakdown (Practical Checklist)

- [ ] Confirm v1 target: Electron + hosted backend.
- [ ] Create `desktop/` app shell and wire local `web/dist`.
- [ ] Externalize frontend API base URL by environment.
- [ ] Implement desktop OAuth callback handling.
- [ ] Implement secure local token storage.
- [ ] Add packaging config for Windows/macOS/Linux.
- [ ] Add CI pipeline for tagged desktop releases.
- [ ] Add signing/notarization steps.
- [ ] Create installer QA checklist and run it per release.
- [ ] Publish first internal beta installer.

---

## 6. Risks and Mitigations

- OAuth callback complexity on desktop:
  - Mitigation: use deep-link protocol first; keep loopback fallback.
- Platform signing friction (especially macOS):
  - Mitigation: set up signing/notarization before beta launch.
- API/backend instability affecting desktop UX:
  - Mitigation: finish production hardening before broad desktop rollout.
- Auto-update failures:
  - Mitigation: keep manual download fallback for every release.

---

## 7. Suggested Timeline

- Week 1: Phases A-B (decisions + production backend baseline).
- Week 2: Phases C-D (desktop shell + auth).
- Week 3: Phases E-F (packaging + signing + release pipeline).
- Week 4: Phase G and internal beta rollout.

---

## 8. Definition of Done (v1)

ForgeGuard is considered "downloadable" when all are true:

1. Users can download a signed installer for at least Windows.
2. Installed app launches without dev tooling.
3. User can complete GitHub login and connect a repo.
4. Audits and live updates work from the desktop app.
5. Team can publish repeatable versioned releases from CI.
