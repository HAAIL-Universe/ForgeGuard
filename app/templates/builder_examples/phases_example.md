# [PROJECT NAME] — Build Phases

⚠ FORMAT SPECIFICATION ONLY.
Do NOT reference phase names or deliverables from this skeleton.
All phases must be derived from the user's actual project requirements.
Every feature in the blueprint MVP scope must be scheduled in exactly one phase.

## Phase 0 — Genesis

**Objective:** Bootstrap the project scaffold, configure dependencies, and set up the database.

**Deliverables:**
- Project repository structure initialised
- Dependency manifest (requirements.txt / package.json / etc.)
- Database migration scripts for all tables
- [Any additional genesis deliverable specific to the project]

**Schema coverage:** [list every table created in this phase by name]

**Exit criteria:**
- [concrete, testable criterion — e.g. "Server starts and returns 200 on GET /health"]
- [concrete, testable criterion — e.g. "All migrations apply cleanly on empty database"]

## Phase 1 — [Name derived from user's project]

**Objective:** [One sentence describing what this phase delivers.]

**Deliverables:**
- [Deliverable — one line]
- [Deliverable — one line]

**Schema coverage:** [table names from schema contract relevant to this phase]

**Exit criteria:**
- [Concrete, testable criterion]
- [Concrete, testable criterion]

## Phase N — [Final Phase Name]

**Objective:** [One sentence.]

**Deliverables:**
- README.md
- [Other deliverables]

**Schema coverage:** [all remaining tables]

**Exit criteria:**
- [Final acceptance criterion]
- [All tests pass]
