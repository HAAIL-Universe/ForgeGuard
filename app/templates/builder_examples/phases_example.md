# TaskFlow — Phases

## Phase 0 — Genesis

**Objective:** Bootstrap the project scaffold, configuration, database foundation, and authentication system.

**Deliverables:**
- Project scaffold: directory structure, config files, dependency manifests
- Docker Compose setup: API container, PostgreSQL container
- Database connection pool and migration framework (Alembic)
- Core tables: `users` with indexes
- Authentication: registration, login, JWT token issuance and validation
- Health check endpoint (`GET /health`)
- Environment variable loading and validation
- Boot/setup script for first-time initialization

**Schema coverage:** `users` table created.

**Exit criteria:**
- `docker compose up` starts both containers successfully
- `POST /auth/register` creates a user and returns JWT tokens
- `POST /auth/login` authenticates and returns tokens
- `GET /health` returns 200
- All tests pass

---

## Phase 1 — Core Features

**Objective:** Build the project, column, and task management system — the core product functionality.

**Deliverables:**
- Tables: `projects`, `project_members`, `columns`, `tasks` with all indexes
- Project CRUD endpoints: create, get, list, archive
- Column management: default columns created with new project, reorder support
- Task CRUD: create, update, move between columns, assign, set due date
- Authorization: project membership checks on all project-scoped endpoints
- Input validation on all endpoints (Pydantic models)
- Database migrations for all new tables

**Schema coverage:** `projects`, `project_members`, `columns`, `tasks` tables created.

**Exit criteria:**
- All CRUD endpoints respond correctly with valid and invalid input
- Tasks can be moved between columns
- Non-members cannot access project resources
- All migrations run cleanly on a fresh database
- All tests pass

---

## Phase 2 — Frontend

**Objective:** Build the complete React frontend with all pages, components, and API integration.

**Deliverables:**
- Vite + React + TypeScript project scaffold
- Auth pages: login, register with form validation
- Dashboard: project grid with task count summaries
- Project board: Kanban columns with task cards
- Task detail: slide-out panel with full editing
- API client: typed fetch wrapper with JWT token management
- Routing: react-router with auth guards
- Responsive layout: navbar, main content area
- Empty states and loading indicators
- Tailwind CSS styling matching the UI contract

**Schema coverage:** No new tables.

**Exit criteria:**
- All pages render correctly
- User can register, login, create project, add tasks, move tasks
- Auth tokens are persisted and refreshed
- Frontend builds with zero TypeScript errors
- All tests pass

---

## Phase 3 — Polish & Ship

**Objective:** Final integration testing, error handling hardening, documentation, and deployment readiness.

**Deliverables:**
- End-to-end smoke tests (API + frontend)
- Error handling: global error boundary (frontend), structured error responses (backend)
- Activity feed: log task/project mutations, display in task detail panel
- Dashboard overdue task indicators
- Comprehensive README.md: description, features, tech stack, setup instructions, environment variables, usage examples, API reference
- Production Docker Compose configuration
- Database seed script with sample data

**Schema coverage:** No new tables.

**Exit criteria:**
- Full user flow works end-to-end (register → create project → manage tasks)
- Error states handled gracefully (network errors, 404s, validation errors)
- README.md is complete and accurate
- `docker compose up` runs the production build
- All tests pass
