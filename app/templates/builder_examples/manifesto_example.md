# TaskFlow — Manifesto

## Core Principles

### 1. User-First, Always
- Every feature decision starts with "how does this help the user?"
- Optimize for clarity of information over density of controls
- Errors must be actionable — never show raw stack traces or cryptic codes
- Loading states and empty states are first-class design concerns

### 2. Test-Driven Confidence
- No feature ships without automated test coverage
- Tests document intent — they explain what the code SHOULD do
- Regression tests are mandatory for every bug fix
- CI must pass before any merge to main

### 3. Schema-First, Contract-First
- Database schema is the single source of truth for data shape
- API contracts define the interface before implementation begins
- Frontend and backend evolve together against shared type definitions
- Breaking changes require explicit migration plans

### 4. Simplicity Over Cleverness
- Prefer readable code over compact code
- One obvious way to do things — avoid multiple paths to the same result
- Dependencies must justify their weight — no library for trivial tasks
- Comments explain WHY, not WHAT

### 5. Security by Default
- Authentication is mandatory for all mutating endpoints
- Input validation happens at the boundary (routers), never trusted downstream
- Secrets never appear in logs, responses, or version control
- Principle of least privilege for all service-to-service communication
