# ForgeGuard — Remediation Plan (v2)

> Baseline: **8.2 / 10** — 923 tests passing (1 pre-existing CORS failure).
> Target: **10 / 10** across all categories.
> Total items: **47** across **18 phases**.
> Generated: 2026-02-17

---

## Table of Contents

| Phase | Name | Items | Effort | Status |
|-------|------|-------|--------|--------|
| [R1](#r1--critical-fixes) | Critical Fixes | 8 | Low | ✅ |
| [R2](#r2--memory-leaks--resource-safety) | Memory Leaks & Resource Safety | 4 | Low | ✅ |
| [R3](#r3--domain-exceptions--error-handling) | Domain Exceptions & Error Handling | 3 | Low | ✅ |
| [R4](#r4--database-hardening) | Database Hardening | 5 | Med | ✅ |
| [R5](#r5--auth-hardening) | Auth Hardening | 4 | Low | ✅ |
| [R6](#r6--http-client-reuse) | HTTP Client Reuse | 3 | Med | ✅ |
| [R7](#r7--decompose-build_servicepy) | Decompose `build_service.py` | 1 | High | ✅ |
| [R8](#r8--decompose-other-services) | Decompose Other Services | 2 | Med | ⬜ |
| [R9](#r9--websocket-reliability) | WebSocket Reliability | 4 | Low | ⬜ |
| [R10](#r10--logging--observability) | Logging & Observability | 4 | Low | ⬜ |
| [R11](#r11--api-polish) | API Polish | 4 | Med | ⬜ |
| [R12](#r12--frontend-stability) | Frontend Stability | 3 | Low | ⬜ |
| [R13](#r13--frontend-decomposition) | Frontend Decomposition | 3 | Med | ⬜ |
| [R14](#r14--frontend-styling) | Frontend Styling | 1 | High | ⬜ |
| [R15](#r15--testing-infrastructure) | Testing Infrastructure | 3 | High | ⬜ |
| [R16](#r16--code-quality-tooling) | Code Quality Tooling | 3 | Low | ⬜ |
| [R17](#r17--scalability) | Scalability | 4 | High | ⬜ |
| [R18](#r18--devex-polish) | DevEx Polish | 1 | Low | ⬜ |

---

## R1 — Critical Fixes

> Quick, targeted fixes for bugs, security issues, and correctness problems.
> All items are independent — can be done in any order within the phase.

### R1.1 Fix CORS test (port mismatch)

- **File:** `tests/test_hardening.py` ~ line 170
- **Bug:** `test_cors_allows_valid_origin` sends Origin `http://localhost:5173` but `settings.FRONTEND_URL` defaults to port `5174`. The `TestClient(app)` is created at module level *before* monkeypatch runs, so CORS middleware is already locked to `5174`.
- **Fix:** Change test Origin to `http://localhost:5174`, OR restructure test to create `TestClient` inside the test function after patching.
- **Verify:** The 1 pre-existing test failure should become 0.

### R1.2 Fix SQL OR precedence bug

- **File:** `app/repos/build_repo.py` ~ line 306 (`get_build_stats`)
- **Bug:** `WHERE build_id = $1 AND source = 'system' AND message LIKE 'Build started%' OR (...)` — AND binds tighter than OR.
- **Fix:** Wrap in parentheses: `AND (message LIKE 'Build started%' OR message LIKE 'Context compacted%')`.
- **Note:** Works "by accident" now because the OR branch repeats `build_id=$1`, but fragile.

### R1.3 Escape ILIKE wildcards in build log search

- **File:** `app/repos/build_repo.py` ~ line 269
- **Bug:** User search string containing `%` or `_` is passed directly into `ILIKE '%{search}%'` — wildcards aren't escaped.
- **Fix:** `search.replace('%', '\\%').replace('_', '\\_')` before wrapping with `%`.

### R1.4 Stop leaking `str(exc)` in interject_build

- **File:** `app/api/routers/builds.py` ~ line 185
- **Bug:** `except Exception` block returns `str(exc)` in the 500 response body, leaking internal details.
- **Fix:** Return generic message `"Internal server error"` or remove the except block entirely and let the global handler catch it (which R3 will create).

### R1.5 Replace custom HMAC with stdlib

- **File:** `app/webhooks.py` ~ lines 6–30
- **Bug:** Hand-rolled HMAC-SHA256 with manual XOR and manual constant-time compare. Python `hmac` module provides both `hmac.new()` and `hmac.compare_digest()` (C-level, guaranteed constant-time).
- **Fix:**
  ```python
  import hmac, hashlib
  def verify_github_signature(payload: bytes, signature: str, secret: str) -> bool:
      expected = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
      return hmac.compare_digest(f"sha256={expected}", signature)
  ```
- **Note:** Keep existing tests passing — they test the public verify function.

### R1.6 Fix audit_repo SQL string interpolation

- **File:** `app/repos/audit_repo.py` ~ line 55
- **Bug:** `completed_at` is set via f-string (`"now()"` or `"NULL"`) injected into SQL. Not exploitable (hardcoded values) but sets a dangerous precedent.
- **Fix:** Use parameterized query — pass `datetime.now(timezone.utc)` or `None` as a `$N` parameter.

### R1.7 Enforce SpendCapBody minimum

- **File:** `app/api/routers/auth.py` ~ line 148
- **Bug:** `SpendCapBody` model accepts any float. Handler rejects `<= 0` and `> 9999.99` but allows `0.01` despite `user_repo.py` docstring saying minimum is `0.50`.
- **Fix:** `spend_cap: float = Field(gt=0, ge=0.50, le=9999.99)` — move validation to the Pydantic model.

### R1.8 Show error on Login page

- **File:** `web/src/pages/Login.tsx` ~ line 11
- **Bug:** `catch` block does nothing. Comment says "show nothing for now, toast in Phase 4" but Phase 4 was completed long ago.
- **Fix:** Show toast or inline error message when network fetch fails.

---

## R2 — Memory Leaks & Resource Safety

> Fix unbounded data structures and deprecated API usage.

### R2.1 TTL-cap OAuth state set

- **File:** `app/api/routers/auth.py` ~ line 18
- **Bug:** `_oauth_states: set[str]` grows forever if users start OAuth but never complete callback.
- **Fix:** Replace with `TTLCache(maxsize=10_000, ttl=600)` from `cachetools` (already a common dep) or a dict with timestamps + periodic prune. Add to `requirements.txt` if needed.

### R2.2 Prune rate limiter idle keys

- **File:** `app/api/rate_limit.py` ~ line 28
- **Bug:** `_hits` dict keys are only pruned when re-accessed. Keys that stop sending requests (e.g., an IP that hit webhooks once) stay forever.
- **Fix:** Either switch to `TTLCache` keyed by IP, or add a `_prune()` method called periodically (e.g., every 1000th `is_allowed` call) that drops keys with no recent hits.

### R2.3 Remove dead WS connections on send failure

- **File:** `app/ws_manager.py` ~ line 38
- **Bug:** When `send_text` fails on a dead connection, the exception is caught with `pass` but the connection stays in `_connections`. Every subsequent `send_to_user` retries the dead socket.
- **Fix:** Move the dead connection removal into the except block:
  ```python
  except Exception:
      self._connections.pop(user_id, None)
  ```
- **Note:** Coordinate with R9 (WebSocket Reliability) — heartbeat loop will also need this.

### R2.4 Replace deprecated `asyncio.get_event_loop()`

- **Files:** `app/clients/git_client.py` ~ line 33, `app/services/tool_executor.py` ~ line 391
- **Bug:** `asyncio.get_event_loop().run_in_executor(...)` is deprecated since Python 3.10.
- **Fix:** Replace with `await asyncio.to_thread(fn, *args)` (simpler) or `asyncio.get_running_loop().run_in_executor(...)`.

---

## R3 — Domain Exceptions & Error Handling

> Centralize exception taxonomy and HTTP response mapping.

### R3.1 Create `app/errors.py`

- **New file:** `app/errors.py`
- **Hierarchy:**
  ```python
  class ForgeError(Exception):
      """Base for all domain exceptions."""
      def __init__(self, message: str, *, status_code: int = 500):
          super().__init__(message)
          self.status_code = status_code

  class NotFoundError(ForgeError):
      def __init__(self, message: str = "Not found"):
          super().__init__(message, status_code=404)

  class BuildError(ForgeError): ...
  class BuildPausedError(BuildError): ...
  class CostCapExceededError(BuildError): ...
  class ContractViolationError(ForgeError): ...
  class AuthError(ForgeError): ...
  ```
- **Note:** Services should raise these instead of `ValueError` with string checks.

### R3.2 Add global exception handler in `main.py`

- **File:** `app/main.py`
- **What:** Register `@app.exception_handler(ForgeError)` that maps `e.status_code` to HTTP response. Also add a catch-all `Exception` handler that logs the traceback and returns 500 with a generic message.
- **Note:** This replaces scattered `HTTPException(...)` raises in routers.

### R3.3 Remove duplicated try/except blocks in builds router

- **File:** `app/api/routers/builds.py`
- **Bug:** The same 5-line `try/except ValueError` pattern (check `"not found"` substring → 404, else 400) is repeated **13 times**.
- **Fix:** After R3.1 + R3.2, services raise `NotFoundError`/`BuildError` directly — routers become simple pass-through. Delete all 13 duplicated blocks.
- **Note:** This must come AFTER R3.1 and R3.2 — the services need to raise domain exceptions first.

---

## R4 — Database Hardening

> Connection safety, query timeouts, transaction integrity.

### R4.1 Add pool timeouts

- **File:** `app/repos/db.py` ~ line 14
- **Fix:** Add to `create_pool()`:
  ```python
  command_timeout=60,
  server_settings={
      'statement_timeout': '30000',          # 30s max query
      'idle_in_transaction_session_timeout': '60000',  # 60s
  },
  ```

### R4.2 Startup pool initialization (fail-fast)

- **File:** `app/main.py` lifespan + `app/repos/db.py`
- **Bug:** Pool is created lazily on first request. If DB is unreachable, the first user request gets a 500 instead of the app failing to start.
- **Fix:** Add `await get_pool()` in the `lifespan` startup phase (before `yield`).
- **Note:** Pairs with R4.1's retry logic (from original Remediation.md §7.2).

### R4.3 Real health check

- **File:** `app/api/routers/health.py`
- **Bug:** Returns `{"status": "ok"}` unconditionally — doesn't verify DB is reachable.
- **Fix:** Add a `SELECT 1` check:
  ```python
  @router.get("/health")
  async def health():
      pool = await get_pool()
      try:
          await pool.fetchval("SELECT 1")
          return {"status": "ok", "db": "connected"}
      except Exception:
          return JSONResponse({"status": "degraded", "db": "unreachable"}, status_code=503)
  ```

### R4.4 Transaction boundaries for multi-step writes

- **Files:** All repo files in `app/repos/`
- **Bug:** Multi-step operations (e.g., `snapshot_contracts`, `delete_builds` + cascade, project creation + contract upsert) each acquire separate connections with no transaction wrapping.
- **Fix:** Use `async with pool.acquire() as conn:` + `async with conn.transaction():` around multi-step operations.
- **Note:** Audit each repo file for operations that do 2+ writes. Key candidates:
  - `build_repo.py`: `snapshot_contracts` (read max batch + insert)
  - `project_repo.py`: project creation + contract upsert
  - `build_repo.py`: `delete_builds` with cascading data

### R4.5 Cascade check on project deletion

- **File:** `app/api/routers/projects.py` ~ line 121 → `app/repos/project_repo.py` ~ line 100
- **Bug:** `DELETE FROM projects WHERE id = $1` runs with no check for active builds. Running builds become orphaned.
- **Fix:** Check for active builds first and return 409. Ensure DB-level CASCADE constraints are correct for contracts, snapshots, and builds.

---

## R5 — Auth Hardening

> JWT best practices, token validation, config guards.

### R5.1 Add JWT `aud` and `iss` claims

- **File:** `app/auth.py` ~ line 13
- **Bug:** JWT payload has `sub`, `github_login`, `exp`, `iat` but no `aud`/`iss`. If the same `JWT_SECRET` is shared with another service, tokens are interchangeable.
- **Fix:** Add `"aud": "forgeguard"`, `"iss": "forgeguard"` to `create_token()`. Validate both in `decode_token()` with `options={"require": ["aud", "iss"]}`.

### R5.2 Parse `/auth/me` response in AuthContext

- **File:** `web/src/context/AuthContext.tsx` ~ line 84
- **Bug:** On mount, the context calls `GET /auth/me` but only checks `res.ok` — the response body (with updated `has_anthropic_key`, `avatar_url`, `build_spend_cap`) is discarded.
- **Fix:** Parse the response and call `setUser(data)` with fresh server-side user data.

### R5.3 Fix stale `useEffect` closure in AuthContext

- **File:** `web/src/context/AuthContext.tsx` ~ line 93
- **Bug:** `useEffect` uses `token` and `logout` inside the effect but deps array is `[]` with `eslint-disable`. If `token` changes mid-session, validation won't re-run.
- **Fix:** Add `token` and `logout` to the deps array. Remove the eslint-disable comment.

### R5.4 Disable `/docs` in production

- **File:** `app/main.py`
- **Fix:**
  ```python
  docs_url="/docs" if settings.DEBUG else None,
  redoc_url="/redoc" if settings.DEBUG else None,
  ```
- **Note:** Ensure `DEBUG` flag exists in config (check `app/config.py`). If not, add it with default `True` for dev.

---

## R6 — HTTP Client Reuse

> Eliminate per-request TCP+TLS overhead.

### R6.1 GitHub API client singleton

- **File:** `app/clients/github_client.py`
- **Bug:** Every function creates `async with httpx.AsyncClient() as client:` — new TCP connection + TLS handshake per call.
- **Fix:** Create a module-level `_client: httpx.AsyncClient | None` initialized on first use (or at startup), with connection pooling enabled. Close in `main.py` lifespan shutdown.

### R6.2 LLM API client singleton

- **File:** `app/clients/llm_client.py` ~ line 126
- **Bug:** Same pattern as R6.1 — new `httpx.AsyncClient` per LLM API call.
- **Fix:** Same singleton pattern. Especially important here since LLM calls can be long-lived and benefit from HTTP/2 multiplexing.

### R6.3 Lifespan cleanup

- **File:** `app/main.py` lifespan
- **Fix:** Add `await github_client.close()` and `await llm_client.close()` in the shutdown phase (after `yield`).

---

## R7 — Decompose `build_service.py`

> The single highest-risk refactor. 6,526 lines → 8 focused modules.

### R7.1 Split into `app/services/build/`

| New file | Responsibility | Approx lines |
|----------|---------------|-------------|
| `__init__.py` | Re-exports for backward compat (every existing import continues to work) | ~50 |
| `planner.py` | Phase planning, LLM plan generation, plan parsing | ~400 |
| `executor.py` | Build loop, streaming, tool dispatch, `_run_build()` | ~1500 |
| `cost.py` | Token tracking, cost caps, budget gates, `_check_cost_cap()` | ~300 |
| `phases.py` | Phase state machine, transitions, watchdog timer | ~400 |
| `git_push.py` | Git commit/push with retry logic, branch management | ~300 |
| `verification.py` | Test running, verification hierarchy, result parsing | ~500 |
| `context.py` | Context compaction, message construction, `_build_messages()` | ~400 |

**Strategy:**
1. Create the package directory `app/services/build/`
2. Extract one module at a time, starting with the most independent (`cost.py`)
3. After each extraction, run full test suite to verify backward compat
4. The `__init__.py` must re-export everything that `app.services.build_service` currently exposes
5. The final step is to delete `build_service.py` and update any remaining direct import paths

**Risk mitigation:** Every test file that imports from `build_service` should continue working via the `__init__.py` re-exports. Run tests after each module extraction.

---

## R8 — Decompose Other Services ✅

> Same pattern as R7, for the next two largest service files.

### R8.1 Split `project_service.py` (1,108 lines)

- **File:** `app/services/project_service.py`
- **Extract:**
  - `app/services/project/questionnaire.py` — Multi-turn chat state machine, LLM-driven questionnaire
  - `app/services/project/contract_generator.py` — LLM-based contract generation, push-to-git
  - `app/services/project/__init__.py` — Re-exports + remaining CRUD
- **Note:** The questionnaire and contract generator each have distinct LLM prompt templates that make natural extraction boundaries.

### R8.2 Split `scout_service.py` (974 lines)

- **File:** `app/services/scout_service.py`
- **Extract:**
  - `app/services/scout/quick_scan.py` — Quick scan logic (GitHub API calls, basic analysis)
  - `app/services/scout/deep_scan.py` — Deep scan logic (clone + full analysis)
  - `app/services/scout/dossier_builder.py` — Dossier construction, LLM summarization
  - `app/services/scout/__init__.py` — Re-exports
- **Note:** Quick scan and deep scan share some utilities (risk scoring, metric extraction) — those go in a shared `_utils.py`.

---

## R9 — WebSocket Reliability ✅

> Server heartbeat, client backoff, connection governance.

### R9.1 Server-side heartbeat + dead connection cleanup

- **File:** `app/ws_manager.py`
- **Fix:** Add a background task that pings all connections every 30s and removes dead ones:
  ```python
  async def _heartbeat_loop(self):
      while True:
          await asyncio.sleep(30)
          dead = []
          for uid, ws in self._connections.items():
              try:
                  await ws.send_json({"type": "ping"})
              except Exception:
                  dead.append(uid)
          for uid in dead:
              self._connections.pop(uid, None)
  ```
- **Note:** Start this as a background task in `main.py` lifespan startup.

### R9.2 Client exponential backoff + jitter + max retries

- **File:** `web/src/hooks/useWebSocket.ts` ~ line 30
- **Current:** Fixed 3-second retry, no limit, no jitter.
- **Fix:**
  ```typescript
  const maxRetries = 10;
  const delay = Math.min(1000 * 2 ** attempt, 30000) + Math.random() * 1000;
  ```
- **After max retries:** Show a "Connection lost" banner via toast/context. Stop retrying.

### R9.3 Per-user connection limits

- **File:** `app/ws_manager.py`
- **Fix:** Before accepting a new connection, check if user already has `N` connections (e.g., 3). If so, close the oldest.
- **Note:** Currently `_connections` is a dict keyed by `user_id` so only one connection per user is stored anyway. If switching to a list-per-user in R9.1, enforce the cap here.

### R9.4 WebSocket message size limit

- **File:** `app/api/routers/ws.py` ~ line 34
- **Bug:** No limit on incoming message size. Malicious client can send arbitrarily large messages.
- **Fix:** Either configure Starlette's `max_size` parameter, or read with a size check:
  ```python
  data = await websocket.receive_text()
  if len(data) > 4096:
      await websocket.close(code=1009, reason="Message too large")
      return
  ```

---

## R10 — Logging & Observability ✅

> Structured logging, request tracing, configurable verbosity.

### R10.1 Request ID middleware

- **New file:** `app/middleware/request_id.py`
- **Impl:**
  ```python
  class RequestIDMiddleware(BaseHTTPMiddleware):
      async def dispatch(self, request, call_next):
          request_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))
          request.state.request_id = request_id
          response = await call_next(request)
          response.headers["X-Request-ID"] = request_id
          return response
  ```
- **Register:** In `main.py` via `app.add_middleware(RequestIDMiddleware)`.

### R10.2 Add structlog

- **Dep:** `pip install structlog` → add to `requirements.txt`
- **Config:** In `app/main.py` or a new `app/logging_config.py`:
  ```python
  structlog.configure(
      processors=[
          structlog.processors.TimeStamper(fmt="iso"),
          structlog.processors.add_log_level,
          structlog.processors.JSONRenderer(),
      ],
  )
  ```
- **Usage:** Replace `print()` and `logging.info()` calls across the codebase with `structlog.get_logger()`.
- **Bind request_id:** In the middleware, bind `request_id` to the structlog context so every log line in the request is traceable.

### R10.3 `LOG_LEVEL` env var

- **File:** `app/config.py`
- **Fix:** Add `LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")` and configure `logging.basicConfig(level=settings.LOG_LEVEL)` at startup.

### R10.4 Structured build event logging

- **Where:** `app/services/build_service.py` (or post-R7: `app/services/build/executor.py`)
- **What:** Log key build lifecycle events as structured JSON: `build_started`, `phase_started`, `tool_called`, `phase_completed`, `build_completed`, `build_failed`.
- **Note:** These logs complement the existing WS events but provide a persistent, queryable record.

---

## R11 — API Polish ✅

> Pagination, versioning, OpenAPI quality.

### R11.1 Add pagination to list endpoints

- **Files:** `app/api/routers/projects.py` ~ line 94, `app/api/routers/repos.py` ~ line 62
- **Bug:** Both return all items with no `LIMIT` clause.
- **Fix:** Add `limit: int = Query(50, ge=1, le=200)` and `offset: int = Query(0, ge=0)` parameters. Match the pattern already used in `list_audits`.

### R11.2 API versioning

- **File:** `app/main.py` (router includes)
- **Fix:** Prefix all routes with `/api/v1/`. Create a `v1_router = APIRouter(prefix="/api/v1")` and mount all sub-routers under it.
- **Note:** Frontend `authFetch` base URL must be updated accordingly. Search for all `fetch('/` calls.

### R11.3 OpenAPI response models + tags

- **Files:** All router files in `app/api/routers/`
- **Fix:** Define Pydantic response models for every endpoint. Add `tags=["Builds"]` etc. to each router.
- **Stub:**
  ```python
  @router.get("/builds/{build_id}", response_model=BuildResponse, tags=["Builds"])
  ```
- **Note:** This is tedious but mechanical. Most response shapes already exist in the frontend TypeScript types — mirror them as Pydantic models in `app/api/schemas/`.

### R11.4 Auth scheme in OpenAPI

- **File:** `app/api/deps.py` or router-level
- **Fix:**
  ```python
  from fastapi.security import HTTPBearer
  security = HTTPBearer()
  ```
- **Note:** So Swagger UI shows the auth mechanism and allows testing with tokens.

---

## R12 — Frontend Stability

> Error boundaries, lazy loading, user feedback.

### R12.1 Add ErrorBoundary component

- **New file:** `web/src/components/ErrorBoundary.tsx`
- **Impl:** Class component with `getDerivedStateFromError` → renders fallback UI with "Something went wrong" + retry button.
- **Wrap:** Root `<App>` in `App.tsx` + each `<Route>` element independently so one page crash doesn't take down the whole app.

### R12.2 Add `React.lazy()` + `Suspense` for routes

- **File:** `web/src/App.tsx`
- **Bug:** All 10 page components eagerly imported — full bundle loaded on first paint.
- **Fix:**
  ```tsx
  const BuildProgress = React.lazy(() => import('./pages/BuildProgress'));
  const Scout = React.lazy(() => import('./pages/Scout'));
  // ... all pages

  <Suspense fallback={<LoadingSpinner />}>
    <Routes>
      <Route path="/builds/:id" element={<BuildProgress />} />
      ...
    </Routes>
  </Suspense>
  ```
- **Impact:** Reduces initial bundle by ~60-70%.

### R12.3 Login error feedback (same as R1.8)

- **Note:** Covered in R1.8. Listed here for category completeness — do NOT duplicate effort.

---

## R13 — Frontend Decomposition

> Break god-pages into focused components.

### R13.1 Decompose `BuildProgress.tsx` (2,403 lines)

| Extract | Approx lines | Responsibility |
|---------|-------------|----------------|
| `PhaseOverview.tsx` | ~200 | Phase cards, progress bars, status indicators |
| `ActivityFeed.tsx` | ~400 | Scrollable log of build events |
| `TaskChecklist.tsx` | ~200 | Task items with checkmarks |
| `CostSidebar.tsx` | ~150 | Token usage, cost display, budget bar |
| `VerificationPanel.tsx` | ~300 | Test results, verification status |

### R13.2 Decompose `Scout.tsx` (1,480 lines)

| Extract | Approx lines | Responsibility |
|---------|-------------|----------------|
| `StackProfileCard.tsx` | ~200 | Stack detection results display |
| `DossierPanel.tsx` | ~300 | Full dossier view with sections |
| `RiskTable.tsx` | ~200 | Risk items table with severity |
| `MetricsChart.tsx` | ~150 | Visual metrics (maybe chart.js / recharts) |

### R13.3 Decompose `ProjectDetail.tsx` (1,152 lines)

| Extract | Approx lines | Responsibility |
|---------|-------------|----------------|
| `ContractEditor.tsx` | ~300 | Contract display/edit |
| `BuildHistory.tsx` | ~250 | List of past builds with status |
| `PhaseTimeline.tsx` | ~200 | Visual phase progression |

---

## R14 — Frontend Styling

> Extract inline styles to reduce LOC and improve maintainability.

### R14.1 Extract inline styles to CSS Modules

- **Scope:** All `.tsx` files with significant inline `style={{...}}` objects.
- **Approach:** CSS Modules (zero new deps, co-located styles):
  ```tsx
  // Before:
  <div style={{ backgroundColor: '#1e293b', borderRadius: 8, padding: 16 }}>

  // After:
  import styles from './BuildProgress.module.css';
  <div className={styles.phaseCard}>
  ```
- **Impact:** ~30-40% LOC reduction per page.
- **Note:** Alternative is Tailwind CSS (one dep: `tailwindcss`). Decide before starting. CSS Modules is safer (no new dep).

---

## R15 — Testing Infrastructure

> Shared fixtures, frontend coverage, coverage thresholds.

### R15.1 Create `tests/conftest.py`

- **New file:** `tests/conftest.py`
- **Shared fixtures to extract:**
  - `mock_user` — standard test user dict
  - `auth_headers` — `{"Authorization": "Bearer <test_token>"}`
  - `mock_pool` — `AsyncMock` of `asyncpg.Pool`
  - `monkeypatch_settings` — auto-fixture that patches common config values (`JWT_SECRET`, `DATABASE_URL`, etc.)
- **Note:** Audit all 40+ test files for duplicated setup patterns first. Extract the most common ones.

### R15.2 Frontend test coverage

- **Currently:** Only 4 test files for 21 components + 10 pages.
- **Priority order:**
  | Priority | Test targets |
  |----------|-------------|
  | Critical | All 10 pages: `Dashboard`, `Scout`, `ProjectDetail`, `Settings`, `Login`, `AuthCallback`, `BuildProgress`, `BuildComplete`, `Repos`, `NewProject` |
  | High | Context providers: `AuthContext`, `ToastContext` |
  | High | Hook: `useWebSocket` (mock WebSocket) |
  | Medium | Components: `AppShell`, `RepoCard`, `PhaseProgressBar`, `EmptyState`, `ConfirmDialog` |

### R15.3 Vitest coverage thresholds

- **File:** `web/vitest.config.ts`
- **Fix:**
  ```typescript
  coverage: {
    provider: 'v8',
    reporter: ['text', 'lcov'],
    thresholds: {
      statements: 80,
      branches: 75,
      functions: 80,
      lines: 80,
    },
  },
  ```

---

## R16 — Code Quality Tooling

> Linters, type checking, validated config.

### R16.1 Add ruff + mypy

- **New files:** `pyproject.toml` (or add sections), `requirements-dev.txt`
- **Config:**
  ```toml
  [tool.ruff]
  line-length = 120
  target-version = "py312"

  [tool.mypy]
  python_version = "3.12"
  strict = true
  ```
- **Note:** First run will produce many errors — fix in batches, don't block on 100% clean.

### R16.2 Pydantic BaseSettings

- **File:** `app/config.py`
- **Bug:** Currently uses raw `os.getenv()` with no type coercion or validation.
- **Fix:**
  ```python
  from pydantic_settings import BaseSettings

  class Settings(BaseSettings):
      DATABASE_URL: str
      GITHUB_CLIENT_ID: str
      FRONTEND_URL: HttpUrl = "http://localhost:5174"
      LLM_PROVIDER: Literal["anthropic", "openai"] = "anthropic"
      PAUSE_THRESHOLD: int = Field(default=3, ge=1, le=20)
      model_config = SettingsConfigDict(env_file=".env")
  ```
- **Dep:** `pip install pydantic-settings` → add to `requirements.txt`.

### R16.3 Pre-commit hooks

- **New file:** `.pre-commit-config.yaml`
- **Config:**
  ```yaml
  repos:
    - repo: local
      hooks:
        - id: ruff
          name: ruff lint
          entry: ruff check --fix
          language: system
          types: [python]
        - id: ruff-format
          name: ruff format
          entry: ruff format
          language: system
          types: [python]
  ```
- **Dep:** `pip install pre-commit` → `pre-commit install`.

---

## R17 — Scalability

> Migration framework, task queue, caching, containerization.

### R17.1 Alembic migrations

- **Dep:** `pip install alembic`
- **Steps:**
  1. `alembic init db/alembic`
  2. Configure to use asyncpg
  3. Create baseline migration from current schema (the 19 raw SQL files in `db/migrations/`)
  4. All future schema changes as versioned Alembic migrations with rollback
- **Note:** The 19 existing migrations have no version tracking or rollback capability.

### R17.2 arq task queue

- **Dep:** `pip install arq` (requires Redis)
- **What:** Replace `asyncio.create_task` fire-and-forget patterns with persistent job queue.
- **Benefits:** Job persistence (builds survive restart), retry logic, result tracking, concurrency limits.
- **Note:** Requires Redis — bundle with R17.4 (Docker Compose) so local dev isn't harder.
- **Key locations to convert:**
  - Build start (`asyncio.create_task(_run_build(...))`)
  - Scout run (`asyncio.create_task(_execute_scout(...))`)
  - Audit run

### R17.3 GitHub API caching

- **File:** `app/clients/github_client.py`
- **Fix:**
  ```python
  from cachetools import TTLCache
  _repo_cache = TTLCache(maxsize=500, ttl=300)  # 5 min
  ```
- **What:** Cache `get_repos`, `get_repo_details`, and similar read-only GitHub API calls.
- **Impact:** Reduces GitHub API rate limit pressure significantly.

### R17.4 Docker Compose

- **New file:** `docker-compose.yml`
- **Services:** `db` (postgres:16-alpine), `redis` (for arq), `backend`, `frontend`
- **Note:** Eliminates Neon dependency for local dev. Pairs with R17.2.

---

## R18 — DevEx Polish

> Developer experience improvements.

### R18.1 Add `boot.ps1` flags

- **File:** `boot.ps1`
- **Fix:**
  ```powershell
  param(
    [switch]$SkipFrontend,
    [switch]$MigrateOnly,
    [switch]$TestOnly,      # Run all tests then exit
    [switch]$Check           # Lint + type-check then exit
  )
  ```

---

## Execution Notes

- **Phase ordering is intentional:** R1-R6 are all low-to-medium effort fixes that improve quality without structural risk. R7-R8 are the big refactors. R9-R18 are improvements that build on the cleaner foundation.
- **Test baseline:** 923 passing, 1 failing (`test_cors_allows_valid_origin` — fixed in R1.1).
- **After each phase:** Run full suite, commit, update status column in the table above.
- **After R1:** Expected test count: 923 passing, **0 failing**.
- **After R15:** Expected test count: 1000+ (frontend + backend).
