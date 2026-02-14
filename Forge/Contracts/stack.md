# ForgeGuard — Technology Stack

Canonical technology decisions for this project. The builder contract (S1) requires reading this file before making changes. All implementation must use the technologies declared here unless a directive explicitly approves a change.

---

## Backend

- **Language:** Python 3.12+
- **Framework:** FastAPI
- **Package manager:** pip
- **Dependency file:** requirements.txt

## Database

- **Engine:** PostgreSQL 15+
- **Driver/client:** asyncpg
- **ORM strategy:** Raw SQL via repos (no ORM)
- **Schema management:** Manual SQL migrations in db/migrations/

## Auth

- **Strategy:** GitHub OAuth2 (authorization code flow) + JWT session tokens
- **Provider:** GitHub (OAuth app)

## Frontend

- **Enabled:** Yes
- **Language:** TypeScript
- **Framework:** React + Vite
- **Directory:** web/
- **Build tool:** Vite

## LLM / AI Integration

- **Enabled:** No
- **Provider:** N/A
- **Integration point:** N/A
- **Embedding / vector search:** N/A

## Testing

- **Backend tests:** pytest
- **Frontend tests:** Vitest
- **Frontend e2e:** N/A (not in MVP)
- **Test directory:** tests/ (backend), web/src/__tests__/ (frontend)

## Deployment

- **Target:** Render (web service + managed PostgreSQL)
- **Server:** uvicorn
- **Containerized:** No (Render native Python)

---

## Environment Variables (required)

| Variable | Purpose | Example |
|----------|---------|---------|
| `DATABASE_URL` | PostgreSQL connection string | `postgresql://user:pass@localhost:5432/forgeguard` |
| `GITHUB_CLIENT_ID` | GitHub OAuth app client ID | `Iv1.abcdef123456` |
| `GITHUB_CLIENT_SECRET` | GitHub OAuth app client secret | `abcdef123456789...` |
| `GITHUB_WEBHOOK_SECRET` | Secret for verifying webhook payloads | `whsec_random_string` |
| `JWT_SECRET` | Secret for signing session JWTs | `a-random-256-bit-key` |
| `FRONTEND_URL` | Frontend origin for CORS and OAuth redirect | `http://localhost:5173` |

---

## forge.json Schema

The builder must create `forge.json` at the project root during Phase 0.

```json
{
  "project_name": "ForgeGuard",
  "backend": {
    "language": "python",
    "entry_module": "app.main",
    "test_framework": "pytest",
    "test_dir": "tests",
    "dependency_file": "requirements.txt",
    "venv_path": ".venv"
  },
  "frontend": {
    "enabled": true,
    "dir": "web",
    "build_cmd": "build",
    "test_cmd": "test"
  }
}
```
