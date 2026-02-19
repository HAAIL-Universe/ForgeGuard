# TaskFlow — Technology Stack

## Backend
- **Language:** Python 3.12+
- **Framework:** FastAPI 0.115+ (async, type-safe routing)
- **ASGI Server:** Uvicorn (production: with --workers flag)
- **Key Libraries:**
  - asyncpg — async PostgreSQL driver
  - pydantic v2 — request/response validation
  - python-jose — JWT token handling
  - passlib[bcrypt] — password hashing
  - httpx — async HTTP client (for any external calls)

## Database
- **Engine:** PostgreSQL 16+
- **Connection:** asyncpg connection pool (min=2, max=10)
- **Migrations:** Alembic with async driver

## Auth
- **Method:** JWT bearer tokens (access + refresh)
- **Flow:** email/password → login endpoint → JWT pair → Authorization header
- **Token lifetime:** access=30min, refresh=7d

## Frontend
- **Framework:** React 18+ with TypeScript
- **Bundler:** Vite 5+
- **Key Libraries:**
  - react-router v6 — client-side routing
  - @tanstack/react-query — data fetching & caching
  - tailwindcss — utility-first styling
  - lucide-react — icon set

## Testing
- **Backend:** pytest + pytest-asyncio (target: 80%+ coverage)
- **Frontend:** Vitest + React Testing Library
- **Coverage:** enforced in CI

## Deployment
- **Platform:** Docker Compose (dev + prod)
- **Containers:** api (FastAPI), db (PostgreSQL), web (Nginx + static build)
- **Expected scale:** <100 concurrent users (single instance)

## Environment Variables

| Name | Description | Required |
|------|------------|----------|
| DATABASE_URL | PostgreSQL connection string | Yes |
| JWT_SECRET | Secret key for JWT signing | Yes |
| CORS_ORIGINS | Comma-separated allowed origins | Yes |
| API_PORT | Backend port (default: 8000) | No |
| LOG_LEVEL | Logging level (default: info) | No |
