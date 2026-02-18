# Builder Contract (Forge v1.0)

This contract governs any automated builder (Copilot/Codex/Claude/GPT/etc.) touching a Forge-managed repository.

It exists to prevent:
- godfiles
- drift
- duplicated features
- unverifiable changes
- regressions from "helpful refactors"

---

## 0) Folder structure convention

The Forge governance toolkit (`Forge/`) is a subfolder of the project root. It contains project specifications, audit evidence, and build scripts — **not** project source code.

```
ProjectRoot/              ← git root, builder's working directory
├── Forge/                ← governance toolkit (deletable after build)
│   ├── Contracts/        ← project specifications
│   ├── evidence/         ← diff logs, audit ledger, test run logs
│   └── scripts/          ← run_tests.ps1, run_audit.ps1, etc.
├── app/                  ← project source code (built here)
├── tests/                ← project tests (built here)
├── requirements.txt      ← dependency manifest (built here)
├── forge.json            ← project metadata (built here)
└── .env                  ← environment config (user-managed)
```

**Rules:**
- The builder's working directory is **always** the project root (parent of `Forge/`).
- All path references in this contract to `Contracts/`, `evidence/`, and `scripts/` are shorthand for `Forge/Contracts/`, `Forge/evidence/`, and `Forge/scripts/`.
- All project source code, tests, configuration files, and dependency manifests MUST be created at the **project root** — never inside `Forge/`.
- `forge.json` lives at the project root.
- The `Forge/` folder may be deleted after the project build is complete. No project code may import from or depend on files inside `Forge/`.

### 0.1) File block output format (mandatory for autonomous builds)

When the builder is operating as a ForgeGuard autonomous build agent (not a human-interactive session), all file creation MUST use the following canonical format so the orchestrator can parse and write files to disk:

```
=== FILE: path/to/file.py ===
```python
<file contents>
```
=== END FILE ===
```

**Rules:**
- The path after `FILE:` is relative to the project root (e.g., `app/main.py`, `tests/test_health.py`).
- One file per block. Multiple files = multiple blocks.
- The code fence inside the block (` ```lang `) is optional but recommended for readability.
- If updating an existing file, emit the full file content (the orchestrator overwrites).
- Do NOT emit partial files or diffs -- always emit the complete file.
- The `=== END FILE ===` delimiter MUST appear on its own line after the file content.
- If the builder omits `=== END FILE ===`, the orchestrator logs a warning and skips the block (graceful fallback).
- Files larger than 1 MB will trigger a warning but are still written.

**Examples of correct file blocks:**

```
=== FILE: app/main.py ===
```python
from fastapi import FastAPI

app = FastAPI(title="MyApp")

@app.get("/health")
async def health():
    return {"status": "ok"}
```
=== END FILE ===

=== FILE: requirements.txt ===
fastapi>=0.100
uvicorn[standard]>=0.20
=== END FILE ===

=== FILE: tests/test_health.py ===
```python
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)

def test_health():
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"
```
=== END FILE ===
```

**Common mistakes (avoid these):**
- Missing `=== END FILE ===` → block is skipped
- Empty path `=== FILE:  ===` → block is skipped
- Using diffs or partial updates instead of full file content → file will be incomplete

### 0.2) Build plan format (mandatory)

At the start of the first response, the builder MUST emit a structured plan:

```
=== PLAN ===
1. First task description
2. Second task description
3. Third task description
=== END PLAN ===
```

As tasks are completed, emit: `=== TASK DONE: N ===` where N is the task number.
The orchestrator broadcasts plan updates to the UI in real time.

---

## 1) Contract read gate (mandatory)

Before making changes, the builder must read (in this order):

1) `Forge/Contracts/blueprint.md`
2) `Forge/Contracts/manifesto.md`
3) `Forge/Contracts/stack.md`
4) `Forge/Contracts/schema.md`
5) `Forge/Contracts/physics.yaml`
6) `Forge/Contracts/boundaries.json`
7) `Forge/Contracts/ui.md` (when frontend is enabled in `stack.md`)
8) `Forge/Contracts/directive.md` (when it exists)
9) `Forge/evidence/diff_log.md` (canonical)
10) `Forge/evidence/audit_ledger.md` (when it exists)
   - Summarize: last signed-off phase, last outcome (`AUTHORIZED`/`FAIL`/`SIGNED-OFF`), and any outstanding FAIL items.
   - If the last entry's outcome is `FAIL` or `SIGNED-OFF (awaiting AUTHORIZED)`, the builder MUST NOT begin new work until that state is resolved (either the user issues `AUTHORIZED`, or the builder re-enters the loopback protocol per §10.3).
   - If the file does not exist, note `"No audit ledger found"` and proceed normally.

If any file in items 1–7 is missing or not found (where applicable), STOP with: `EVIDENCE_MISSING`.

**Phase 0 exemption:** During Phase 0 (Genesis), items 1–7 are already present in `Forge/Contracts/` (placed there by the director). The builder reads them from there. The read gate becomes fully active at Phase 0 completion.

---

## 2) Evidence bundle (mandatory)

Every change request must include:

- repo tree (or confirm file paths exist)
- files to be edited (exact paths)
- current behavior + expected behavior
- logs/errors (if runtime issue)
- minimal diff plan
- verification steps + results

If evidence is insufficient, STOP with: `EVIDENCE_MISSING`.

---

## 3) Minimal-diff rule (default)

- Change as little as possible.
- No renames.
- No "cleanup".
- No refactors unless explicitly requested.

If the builder's proposed change touches unrelated files, STOP with: `CONTRACT_CONFLICT`.

### 3.1 PowerShell encoding rule (mandatory)

All generated `.ps1` files MUST:

1. **Use ASCII-only characters.** No em-dashes (`—`), box-drawing lines (`─`), smart quotes (`""`), or any character above U+007F. Use plain alternatives: `--` for dashes, `-` for horizontal rules, `"` for quotes.
2. **Be saved as UTF-8 with BOM** (byte order mark: `EF BB BF`). PowerShell 5.1 on Windows uses the system codepage (typically Windows-1252) for files without BOM, which corrupts multi-byte UTF-8 sequences and causes parse errors.

If the builder generates a `.ps1` file containing non-ASCII characters, this is a bug — even if the file works in PowerShell 7.

---

## 4) File boundary enforcement (anti-godfile)

Layer boundaries are defined in `Forge/Contracts/boundaries.json`. The audit script (`Forge/scripts/run_audit.ps1`) enforces these mechanically via check A4.

The boundaries file maps layers to directory globs and forbidden patterns. If any file in a layer's glob contains a forbidden pattern, the build fails.

**Default layering principle** (concrete rules in `boundaries.json`):

- **Routers/Controllers** are HTTP-only: parse request → call service → return response.
- **Services** own orchestration + business logic.
- **Repos/DAL** own DB reads/writes.
- **LLM wrapper** owns model/API calls.

No layer may do another layer's job. Violations are bugs, even if the feature works.

If any boundary is violated, STOP with: `CONTRACT_CONFLICT`.

---

## 5) Physics compliance

### 5.1 Physics-first gate (new/unallocated features)

If a requested change introduces a feature that does not have a dedicated place in `Forge/Contracts/physics.yaml` (new endpoint, new request/response field, new component capability), the builder MUST stop and ask for a contracts update first.

- STOP with: `EVIDENCE_MISSING`
- Report what is missing (which endpoint/schema/component needs to be added to physics)
- Propose a minimal contract diff (physics + any blueprint/manifesto touchpoints) before writing code

This prevents duplicated features and "AI amnesia" across sessions.

- `Forge/Contracts/physics.yaml` is canonical.
- Routes must match `Forge/Contracts/physics.yaml` paths, methods, and response shapes.
- Models/schemas must align with `Forge/Contracts/physics.yaml` definitions.

If code and physics diverge, STOP with: `CONTRACT_CONFLICT`.

---

## 6) Confirm-before-write rule (default)

For any application feature that mutates user data, the implementation must include a proposal/confirmation step unless the project's manifesto explicitly opts out for specific operations.

The specific confirmation UX (chat proposals, form confirmations, modal dialogs, etc.) is defined in the project's manifesto and blueprint. The builder contract enforces that the pattern exists — the manifesto defines what it looks like.

If implementation mutates user data without confirmation (and the manifesto does not explicitly exempt that operation), STOP with: `CONTRACT_CONFLICT`.

---

## 7) Source integrity rule

If the application retrieves, generates, or presents content from external or internal data sources:

- Responses MUST include source attribution when the data comes from user-provided or stored content.
- If retrieval fails or returns no results, the system must not fabricate content.
- The system must clearly distinguish between generated/inferred content and retrieved/stored content.

Specific citation formats and attribution rules are defined per-project in the manifesto.

If the implementation presents sourced content without attribution, STOP with: `CONTRACT_CONFLICT`.

### 7.1 Prefer proven components over custom builds

When a reliable, maintained component exists (auth, storage, ingestion, UI widgets, SDK helpers), prefer using it over building from scratch.

- New dependencies must be explicitly approved in the directive (include rationale + alternatives considered).
- Do not introduce heavyweight frameworks or sweeping architecture changes without explicit instruction.

### 7.2 Background work (allowed, never silent writes)

- Background agents/jobs may parse stored messages and pre-fill state.
- Background work may produce proposals or read-only derived state; it must not apply writes without explicit confirmation.

---

## 8) Typed STOP reasons (use exactly one)

- `EVIDENCE_MISSING` — missing repo tree, missing file, missing logs, missing contract update, missing acceptance criteria
- `AMBIGUOUS_INTENT` — unclear expected behavior
- `CONTRACT_CONFLICT` — violates manifesto/blueprint/physics/boundary rules
- `RISK_EXCEEDS_SCOPE` — high risk, large refactor implied
- `NON_DETERMINISTIC_BEHAVIOR` — cannot reproduce or verify
- `ENVIRONMENT_LIMITATION` — blocked by runtime/tooling constraints

---

## 9) Verification hierarchy (must be reported in order)

1) Static correctness
   - type check / lint (if configured)
   - import sanity
2) Runtime sanity
   - app boots
   - core endpoints respond
3) Behavioral intent
   - target scenario is fixed
4) Contract compliance
   - physics matches
   - file boundaries preserved

### 9.1 Test Gate (mandatory)

- Create/maintain a `tests/` folder at the project root (or as declared in `forge.json`).
- At the end of each phase (or any "feature complete" checkpoint):
  1) Add or update unit tests covering the new behavior.
  2) Run tests as part of verification: **static → runtime → behavior (tests) → contract**.
- Tests must be deterministic:
  - Do not depend on live network calls for auth/external services.
  - Use dependency overrides / fakes / mocks for external dependencies.
- A phase must not be marked COMPLETE until relevant tests pass.

### 9.2 Test Runner Script Gate (mandatory)

- Maintain the test runner at: `Forge/scripts/run_tests.ps1`.
- The test runner reads `forge.json` for stack-specific configuration.
- The test runner must be updated whenever new tests are added or test layout changes.
- The test runner must run the full deterministic suite used for "bulk" verification.
- Each invocation of `Forge/scripts/run_tests.ps1` must append a timestamped entry to `Forge/evidence/test_runs.md` capturing start/end time (UTC), runtime path, branch/HEAD (or "git unavailable"), git status/diff stat, and exit codes/summaries for each test phase. The log MUST append (never overwrite) even when a step fails.
- At the end of every implementation cycle marked COMPLETE, the builder MUST run `.\Forge\scripts\run_tests.ps1` and include results in verification.
- `Forge/scripts/run_tests.ps1` must also overwrite `Forge/evidence/test_runs_latest.md` on every run; the first line must be `Status: PASS|FAIL` and, if failing, include a brief failing-tests section.

Minimum required behavior for `Forge/scripts/run_tests.ps1`:
- Run static sanity (compile/lint/import check as appropriate for the stack)
- Run the test suite
- Return a non-zero exit code if any step fails

The builder must:
- Add/update `Forge/scripts/run_tests.ps1` in the same cycle as adding tests (or STOP with `CONTRACT_CONFLICT` if tests were added without updating the runner).
- Include the exact commands + outputs in the diff log verification section.

### 9.3 Drift guardrails (evidence discipline)

- Physics-first: no new routes/endpoints without updating `Contracts/physics.yaml` first.
- Minimal diffs per cycle; no refactors unless the contract forces it.
- The builder MUST overwrite `evidence/diff_log.md` with verification (static → runtime → behavior → contract) **before every commit** — whether that commit is a mid-phase checkpoint or an end-of-phase sign-off. Every commit in the git history must have a corresponding diff log snapshot at the time it was made.

### 9.4 Dependency gate

Any new third-party import or dependency added during a build cycle MUST be reflected in the project's dependency manifest (`requirements.txt`, `package.json`, `go.mod`, etc.) in the same cycle. The audit script checks this via A9.

If a new import is added without a corresponding dependency declaration, the builder MUST fix it before the cycle can be marked COMPLETE.

### 9.5 Cross-phase wiring gate

When `phases.md` specifies that a component (repo, service, client) is created in one phase and its caller (engine, CLI, endpoint) is built in a later phase, the **caller's phase** MUST include:

1. An explicit **wiring implementation item** in section C (Scope): e.g., "Wire `TradingEngine.run_once()` to call `TradeRepo.insert_trade()` after placing an order."
2. An **end-to-end acceptance test** in section E that starts from the caller and asserts on the downstream effect: e.g., "After `engine.run_once()` places an order, a trade row exists in SQLite."

The builder MUST verify wiring completeness at phase end:
- For every repo/service created in prior phases, check whether this phase's code calls it where the spec requires data flow.
- If a repo exists but no caller invokes it, and the spec requires data persistence at that point, the builder MUST wire it before marking the phase COMPLETE.

If `phases.md` does not include a wiring item for a cross-phase dependency and the builder detects the gap, STOP with: `AMBIGUOUS_INTENT` and report: "Phase {N} creates {component} but no subsequent phase wires it to a caller. Clarify whether wiring is intended."

### 9.6 Schema traceability gate

Every table defined in `Contracts/schema.md` MUST be claimed by at least one phase's implementation items (as a repo or data access layer). If a table exists in the schema but no phase creates a repo for it, the builder MUST:

1. During Phase 0, note which tables have no assigned phase.
2. STOP with `AMBIGUOUS_INTENT` and report: "Table `{table_name}` is defined in schema.md but no phase creates a repository for it."

This prevents orphaned tables that exist in migrations but are never used by application code.

### 9.7 User instructions gate (mandatory)

- A `USER_INSTRUCTIONS.md` file MUST exist at the project root by the end of the final phase.
- During Phase 0, the builder creates a stub with section headers only.
- During the **final phase**, the builder MUST populate `USER_INSTRUCTIONS.md` with complete content covering:
  1. **Prerequisites** — language version, required accounts/services
  2. **Install** — venv/package manager setup, dependency install commands
  3. **Credential/API setup** — where to obtain API keys, tokens, or account IDs (step-by-step for each external service)
  4. **Configure `.env`** — which variables are required vs optional, what each one does, example values
  5. **Run** — exact commands for every supported mode (dev, production, test, etc.)
  6. **Stop** — how to shut the app down gracefully
  7. **Key settings explained** — what tunable parameters do in plain language
  8. **Troubleshooting** — table of common errors and their fixes
- The instructions must be written for a **non-developer end user** — no jargon without explanation.
- The final phase's acceptance criteria MUST include: "`USER_INSTRUCTIONS.md` is complete and covers all sections."

### 9.8 Boot script gate (opt-in)

When the directive includes `boot_script: true`, the builder MUST create a `boot.ps1` PowerShell script at the project root during the **final phase**. This is a one-click setup-and-run script for end users.

**boot.ps1 must perform these steps in order:**

1. **Check prerequisites** — Verify Python/Node/Go (per stack) is installed and meets minimum version. If missing, print a clear error and exit.
2. **Create virtual environment** (if applicable) — `python -m venv .venv` or equivalent. Skip if already exists.
3. **Activate environment** — `.venv\Scripts\Activate.ps1` or equivalent.
4. **Install dependencies** — `pip install -r requirements.txt` / `npm install` / `go mod download`.
5. **Prompt for required credentials** — For each required `.env` variable that has no default: prompt the user interactively (`Read-Host`), explain what it is and where to get it. Pre-fill optional variables with defaults from `.env.example`.
6. **Write `.env`** — Generate the `.env` file from the user's answers + defaults. Do NOT overwrite an existing `.env` — ask the user if they want to keep or replace it.
7. **Run migrations** — Execute DB setup if applicable (`python -c "from app.repos.db import init_db; init_db('...')"` or equivalent).
8. **Start the app** — Launch the application in the default mode (e.g., `python -m app.main` or `npm start`).

**Error resilience:** Each step must check for errors before proceeding. If a step fails, print a clear message explaining what went wrong and how to fix it, then exit with a non-zero code.

**Builder iteration loop:** After creating `boot.ps1`, the builder MUST:

1. Run `pwsh -File boot.ps1` in a terminal (using non-interactive defaults or test values for credential prompts where possible).
2. If it fails: read the error output, fix the script, and re-run.
3. Repeat until the app starts successfully (health endpoint responds, or main process is running).
4. **Loop limit:** If 5 consecutive attempts fail, STOP with `ENVIRONMENT_LIMITATION` and report the blocking error.

The boot script is NOT included in audit scope (A1–A9) — it is a convenience tool, not governed code. However, it MUST work by the time the builder halts.

When `boot_script: false` or not specified, the builder skips this entirely.

---

## 10) Autonomous Execution Mode (AEM)

### 10.1 AEM Toggle

- AEM is **OFF by default**. Normal builder behavior (step-by-step with user confirmation) applies unless explicitly enabled.
- A directive MUST explicitly state `AEM: enabled` (or equivalent unambiguous activation) to enable Autonomous Execution Mode.
- A directive MAY additionally state `Auto-authorize: enabled`. This allows the builder to skip the manual HALT at Phase Sign-off when the audit passes — see §10.4.
- When AEM is enabled, the builder MAY sequence build steps autonomously (contract read → evidence bundle → implementation → verification) without waiting for user confirmation between steps.
- **Audit Ledger read gate:** At session start (or when AEM is first activated in a session), the builder MUST read `evidence/audit_ledger.md` (if it exists) and summarize the last signed-off phase and any outstanding FAIL items. This gives the builder and auditor role continuity across sessions. If the file does not exist, the builder MUST note `"No prior audit ledger found; this is the first AEM cycle."` and proceed.
- AEM does **NOT** expand scope. The builder MUST NOT:
  - Add features beyond those specified in the directive.
  - Touch files not listed in the evidence bundle or required by the change.
  - Skip any mandatory gate (contract read gate §1, evidence bundle §2, minimal-diff rule §3, physics compliance §5, confirm-before-write §6, test gate §9.1, diff log gate §9.3).
- If the builder detects that autonomous progress requires scope expansion, it MUST STOP with `RISK_EXCEEDS_SCOPE` and report what additional scope is needed.

### 10.1.1 Watch Audit Startup

When AEM is enabled, the builder MUST ensure **exactly one instance** of the audit file watcher is running. The watcher persists across all phases -- the builder does NOT restart it between phases.

**Single-instance guard:** `watch_audit.ps1` writes a `.forge_watcher.lock` file at the project root on startup (containing its PID and timestamp). If the lock file already exists and the PID is still alive, the script exits cleanly with code 0. If the PID is dead (stale lock), the script removes the lock and starts normally. The lock file is deleted on clean shutdown (Ctrl+C / finally block).

**Exact trigger point:** The watcher MUST be launched **after** the diff log is marked `Status: IN_PROCESS` (step 4 of the diff log sequence in §11) and **before** any implementation work begins (step 6). The per-phase startup sequence is:

1. Read contracts (§1 read gate).
2. Read and summarize prior diff log and audit ledger.
3. Plan scope/files/tests.
4. Run `overwrite_diff_log.ps1`, replace placeholders, mark `Status: IN_PROCESS`.
5. **Launch the watcher** (safe to call every phase -- the lock file prevents duplicates):
   ```powershell
   pwsh -File .\Forge\scripts\watch_audit.ps1
   ```
   If the watcher is already running, the script exits immediately and the existing instance continues. If it is not running (first phase, stale lock, or terminal was lost), a new instance starts.
6. Begin implementation work.

The builder MUST call step 5 every phase. It does not need to manually check whether the watcher is running -- the lock file handles that.

This watcher monitors `Forge/evidence/diff_log.md`. It parses the diff log for claimed files (from `## Files Changed/Created/Modified` tables) and the phase identifier (from `Phase N` in the header), then automatically invokes `run_audit.ps1` with those parameters.

The watcher is **passive during builder work** -- it only triggers when the diff log `Status` field changes to `COMPLETE`. While `Status: IN_PROCESS`, the watcher explicitly skips auditing to avoid interrupting mid-cycle work.

### 10.2 Verification Gate → Audit Script Trigger

After the builder completes the verification hierarchy (§9: static → runtime → behavior → contract), and **only when AEM is enabled**, the builder MUST NOT proceed to sign-off. Instead, it MUST trigger the deterministic audit script.

The builder does **NOT** switch personas, self-audit, or read `auditor_contract.md` as a role. Auditing is performed by `scripts/run_audit.ps1` -- a deterministic script that checks facts, not opinions.

#### Audit trigger sequence (watcher-driven)

When `watch_audit.ps1` is running (per §10.1.1), the builder triggers the audit by writing the diff log:

1. **Write `Forge/evidence/diff_log.md`** with `Status: COMPLETE` and all required sections (Files Changed/Created/Modified tables, Verification Hierarchy, etc.). The watcher detects the file change and automatically:
   - Parses the claimed files from the diff log tables.
   - Parses the phase identifier from the header.
   - Invokes `run_audit.ps1 -ClaimedFiles "<parsed files>" -Phase "<parsed phase>"`.
   - The builder does NOT manually call `run_audit.ps1`.

2. **Wait for the audit to complete.** The watcher displays audit results in its terminal. Do NOT proceed until the audit result appears.

3. **Read the audit result** from the watcher terminal output or `evidence/audit_ledger.md`. The audit produces:
   - A structured checklist (A1--A9, each PASS or FAIL with justification).
   - An automatic append to `evidence/audit_ledger.md`.

4. **React to the result:**
   - **All PASS:** Proceed to Phase Sign-off (§10.4).
   - **Any FAIL:** Enter the Loopback Protocol (§10.3).

#### Fallback: manual audit invocation

If `watch_audit.ps1` is NOT running (e.g., terminal was closed, environment limitation), the builder MUST invoke the audit script directly:

```powershell
pwsh -File .\Forge\scripts\run_audit.ps1 -ClaimedFiles "<comma-separated list>" -Phase "<phase identifier>"
```

The `-ClaimedFiles` parameter MUST list every file the builder modified in this cycle (exact relative paths, no invented paths).

5. **If `scripts/run_audit.ps1` does not exist or fails to execute:** STOP with `ENVIRONMENT_LIMITATION`. Report: `"run_audit.ps1 not found or not executable; cannot complete AEM audit gate."` Do NOT fall back to self-audit. Do NOT proceed.

### 10.3 Loopback Protocol (Audit FAIL → Fix → Re-audit)

If **any** audit checklist item is marked FAIL:

1. The builder MUST emit a STOP reason corresponding to the failure:
   - Scope/minimal-diff/contract violations → `CONTRACT_CONFLICT`
   - Missing evidence/logs/tests → `EVIDENCE_MISSING`
   - Non-deterministic test results → `NON_DETERMINISTIC_BEHAVIOR`
   - Environment blockers → `ENVIRONMENT_LIMITATION`

2. The builder MUST then formulate a constrained Fix Plan:
   - The Fix Plan MUST address **only** the FAIL items from the audit script output.
   - The Fix Plan MUST NOT introduce new features or expand scope.
   - The Fix Plan MUST be stated explicitly before execution (list each FAIL item and the intended fix).
   - The Fix Plan is recorded in the audit ledger by the next `run_audit.ps1` invocation.

3. After executing the Fix Plan, the builder MUST:
   - Re-run the full verification hierarchy (§9: static → runtime → behavior → contract).
   - Re-run `scripts/run_audit.ps1` (§10.2) with the updated `-ClaimedFiles` list.

4. This loop repeats until:
   - **All audit items PASS** → proceed to Phase Sign-off (§10.4), OR
   - **A STOP reason blocks further progress** (e.g., the fix itself requires scope expansion, or the environment cannot satisfy a check) → the builder MUST HALT with the blocking STOP reason and report the unresolvable items.

5. **Loop limit:** If the loopback executes **3 consecutive cycles** without achieving full PASS, the builder MUST STOP with `RISK_EXCEEDS_SCOPE` and report: `"AEM audit loop exceeded 3 iterations without full PASS. Manual review required."` The builder MUST NOT continue autonomously.

### 10.4 Phase Sign-off Gate

When the audit produces **all PASS** (A1–A9), the builder MUST emit a Phase Sign-off block in this exact format:

```
=== PHASE SIGN-OFF: PASS ===
Phase:               <phase identifier from directive>
Files changed:       <list of filenames only, no invented paths>
Minimal diff summary: <1–3 sentence description of what changed; do not paste full diff if large>
Verification evidence:
  - Static:    <PASS/FAIL + one-line summary>
  - Runtime:   <PASS/FAIL + one-line summary>
  - Behavior:  <PASS/FAIL + one-line summary>
  - Contract:  <PASS/FAIL + one-line summary>
  - Tests:     <PASS/FAIL + test count summary>
Audit result:        ALL PASS (A1–A9)
Loopback iterations: <N> (0 if first audit passed)

ACTION REQUIRED: HALT. Awaiting user token "AUTHORIZED" before commit/push.
=== END PHASE SIGN-OFF ===
```

After emitting this block, the builder MUST check the directive for the `Auto-authorize` flag:

**If `Auto-authorize: enabled` is set AND the audit exited with code 0 (all PASS):**
1. Overwrite `evidence/diff_log.md` (per §9.3).
2. Append the Phase Sign-off to the Audit Ledger with `outcome: AUTO-AUTHORIZED`.
3. Run `git commit` (include the phase identifier in the commit message), then `git push`.
4. Proceed directly to the next phase without halting.
5. If there are no more phases, HALT and report: `"All phases complete."`

**If `Auto-authorize` is NOT set (default behavior):**
1. Append the Phase Sign-off to the Audit Ledger (`evidence/audit_ledger.md`) per §10.6 format, with `outcome: SIGNED-OFF (awaiting AUTHORIZED)`.
2. **HALT.** It MUST NOT:
   - Run `git commit` or `git push`.
   - Proceed to the next phase.
   - Modify any files (except the audit ledger append above).
3. The builder resumes **only** upon receiving the explicit user token `AUTHORIZED`.

**In both modes:** If the audit exited non-zero (any FAIL), auto-authorize is ignored. The builder enters the Loopback Protocol (§10.3) regardless of the flag.

### 10.5 Script Authority Note

- The audit script's exit code is **authoritative and final**. The builder MUST NOT override, ignore, reinterpret, or work around a FAIL result from `scripts/run_audit.ps1`.
- The builder MUST NOT modify `scripts/run_audit.ps1` during an AEM cycle to make checks pass. Modifying the audit script is a scope change that requires explicit directive approval.
- If the builder believes a FAIL is incorrect (e.g., a check is misconfigured), it MUST STOP with `CONTRACT_CONFLICT` and report the suspected misconfiguration. It MUST NOT "fix" the script to unblock itself.
- If parallel agents are available, the audit script MAY be invoked by a separate agent session. The script's exit code remains the single authoritative gate regardless of which agent invokes it.

### 10.6 Audit Ledger (Persistent Evidence Trail)

**File:** `evidence/audit_ledger.md`
**Mode:** Append-only. MUST NOT be overwritten or truncated. Each audit pass appends one entry.

The audit ledger provides:
- **Cross-session continuity** — any new AI session reads the ledger to see what phases have been audited, signed off, or are stuck in loopback.
- **Accountability trail** — every PASS and FAIL is recorded with justifications.
- **Loopback visibility** — FAIL entries include the Fix Plan, so subsequent sessions can see what was attempted.

#### Ledger Entry Format (one per audit pass)

Each entry MUST be appended in this exact format:

```
---
## Audit Entry: <phase identifier> — Iteration <N>
Timestamp: <UTC datetime>
AEM Cycle: <directive reference or phase label>
Outcome: PASS | FAIL | SIGNED-OFF (awaiting AUTHORIZED) | AUTHORIZED (committed) | AUTO-AUTHORIZED (committed)

### Checklist
- A1 Scope compliance:      PASS|FAIL — <justification>
- A2 Minimal-diff:          PASS|FAIL — <justification>
- A3 Evidence completeness: PASS|FAIL — <justification>
- A4 Boundary compliance:   PASS|FAIL — <justification>
- A5 Diff Log Gate:         PASS|FAIL — <justification>
- A6 Authorization Gate:    PASS|FAIL — <justification>
- A7 Verification order:    PASS|FAIL — <justification>
- A8 Test gate:             PASS|FAIL — <justification>
- A9 Dependency gate:       PASS|FAIL — <justification>

### Fix Plan (only if FAIL)
- <FAIL item>: <intended fix>
- ...

### Files Changed
- <filename list>

### Notes
<any additional context, e.g., loopback count, blockers, or sign-off details>
```

#### Ledger Lifecycle Rules

1. **Session start:** Builder MUST read `evidence/audit_ledger.md` (if it exists) and summarize the last entry before beginning work.
2. **After every audit script run (§10.2):** The script appends one entry automatically. Both PASS and FAIL results are recorded.
3. **After Loopback Fix Plan (§10.3):** The FAIL entry's Fix Plan section is included. The subsequent re-audit appends a new entry (incremented iteration number).
4. **After Phase Sign-off (§10.4):** Entry with `Outcome: SIGNED-OFF (awaiting AUTHORIZED)`.
5. **After user issues `AUTHORIZED`:** The builder MUST append one final update changing the last entry's outcome to `Outcome: AUTHORIZED (committed)` with the commit hash.
6. **If the ledger file does not exist:** The script MUST create it with a header.
7. **If the ledger file is missing mid-session (deleted externally):** STOP with `EVIDENCE_MISSING`.

### 10.7 Audit Script Specification (`scripts/run_audit.ps1`)

**Purpose:** Deterministic, script-based auditing. No AI judgment, no persona switching. The script checks verifiable facts about the repo state, evidence files, and git history.

**Location:** `Forge/scripts/run_audit.ps1`

**Invocation:**
```powershell
pwsh -File .\Forge\scripts\run_audit.ps1 -ClaimedFiles "file1.py,file2.ts,..." -Phase "<phase>"
```

**Parameters:**
- `-ClaimedFiles` (required): Comma-separated list of files the builder claims to have changed.
- `-Phase` (optional): Phase identifier for ledger entries. Defaults to `"unknown"`.

**Exit codes:**
- `0` — All blocking checks PASS.
- `1` — One or more blocking checks FAIL.
- `2` — Script itself failed to execute.

#### Blocking checks (A1–A9)

| # | Check | How to verify | FAIL condition |
|---|-------|---------------|----------------|
| A1 | **Scope compliance** | `git diff --name-only` (staged + unstaged) matches `-ClaimedFiles` exactly | Any file in diff not in claimed list, or vice versa |
| A2 | **Minimal-diff discipline** | `git diff --summary` scanned for `rename` entries | Any `rename` found |
| A3 | **Evidence completeness** | `evidence/test_runs_latest.md` exists and line 1 starts with `Status: PASS`; `evidence/diff_log.md` exists and is non-empty | Missing files, or test status is not PASS |
| A4 | **Boundary compliance** | Read `Contracts/boundaries.json`, scan each layer's files for forbidden patterns | Any forbidden pattern found |
| A5 | **Diff Log Gate** | `evidence/diff_log.md` exists, contains no `TODO:` placeholders | File missing or contains `TODO:` |
| A6 | **Authorization Gate** | `git log` — no commits since the last known AUTHORIZED hash (from `evidence/audit_ledger.md`) | Unauthorized commits detected |
| A7 | **Verification hierarchy order** | `evidence/diff_log.md` contains `Static`, `Runtime`, `Behavior`, `Contract` in order | Keywords missing or out of order |
| A8 | **Test gate** | `evidence/test_runs_latest.md` line 1 is `Status: PASS` | Line 1 is not `Status: PASS` or file missing |
| A9 | **Dependency gate** | Every import in changed files has a corresponding entry in the dependency manifest | Import without declared dependency |

#### Non-blocking warnings (W1–W3)

| # | Check | How to verify |
|---|-------|---------------|
| W1 | **No secrets in diff** | Scan `git diff` for patterns: `sk-`, `AKIA`, `-----BEGIN`, `password=`, `secret=`, `token=` |
| W2 | **Audit ledger integrity** | `evidence/audit_ledger.md` exists and is non-empty |
| W3 | **Physics route coverage** | Every path in `Contracts/physics.yaml` has a corresponding handler file |

#### Script maintenance rules

- `scripts/run_audit.ps1` MUST be maintained alongside `scripts/run_tests.ps1`.
- The builder MUST NOT modify `scripts/run_audit.ps1` during an active AEM cycle unless the directive explicitly includes audit script changes in scope.
- If the script needs to be created for the first time, it MUST be created as a prerequisite step **before** AEM is activated.

---

## 11) Diff log gate (mandatory)

Canonical diff log path: `evidence/diff_log.md`

The builder must:

1) Read the canonical diff log at the start (if present) and summarize last change.
2) Overwrite the canonical diff log at the end with:
   - summary of this cycle
   - files changed
   - minimal diff hunks
   - verification evidence
   - next steps

If the repo has a PowerShell helper for diff logs, it must be used:
- `scripts/overwrite_diff_log.ps1`

Helper workflow: the script writes skeleton + git/diff metadata but leaves TODO placeholders. The builder must:
1) At START of a cycle: run the helper, then replace TODO placeholders with Status=IN_PROCESS, planned/current files, summary bullets, and notes/next steps (no TODOs left).
2) At END of a cycle: re-run the helper to refresh metadata, then replace all TODO placeholders with final Status=COMPLETE, real summary, verification (static → runtime → behavior → contract), and notes/next steps (explicit "None" if empty).
No cycle is COMPLETE if any "TODO:" placeholders remain in `evidence/diff_log.md`.

Mandatory per-cycle diff log sequence:
1) Read `evidence/diff_log.md` and summarize the previous cycle (1–5 bullets) before any overwrite.
2) Plan scope/files/tests.
3) Only after planning, run `scripts/overwrite_diff_log.ps1` to regenerate the scaffold.
4) Immediately replace placeholders with Status=IN_PROCESS, planned summary, planned files, notes, and next steps (no TODOs left).
5) **Launch the watch audit watcher** per §10.1.1. Run `pwsh -File .\Forge\scripts\watch_audit.ps1`. This is safe to call every phase -- the lock file prevents duplicates. No implementation work may begin until this step completes.
6) Do the work.
7) End-of-cycle: re-run the helper, then manually finalize Status=COMPLETE, Summary, Verification (static → runtime → behavior → contract), Notes, and Next Steps.
8) After manual edits, run `pwsh -File .\Forge\scripts\overwrite_diff_log.ps1 -Finalize`; if it reports TODO placeholders or missing log, treat as CONTRACT_CONFLICT and stop.

Non-negotiable rule:
- Overwriting before summarizing the prior cycle or leaving TODO placeholders is a CONTRACT_CONFLICT (work incomplete).

If diff log is not updated, work is incomplete.

---

## 12) Auditor Oversight

- Assume every cycle is audited for scope, evidence anchors, contract compliance, diff-log integrity, and token discipline. The cycle fails if the scope is broadened without permission, the verification hierarchy is incomplete, or the canonical log contains TODO placeholders.
- Before asking for `OVERWRITE` (the dirty-tree diff log gate), print `git status -sb` and `git diff --staged --name-only`. That token only authorizes the canonical log overwrite and never a commit.
- Before asking for `AUTHORIZED` (the commit/push gate), again print those status lines. Treat `OVERWRITE` and `AUTHORIZED` as distinct tokens authorizing different actions.
- Only `evidence/diff_log.md` is authoritative for approvals.
- Stick to the allowed file set, do not invent files, and keep evidence ready before requesting tokens.

--- End of Builder Contract ---
