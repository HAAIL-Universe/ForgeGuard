# TaskFlow — Blueprint

## Product Intent

TaskFlow is a lightweight task management platform for small teams. It lets users create projects, organize tasks into boards with customizable columns, assign team members, set due dates, and track progress through a clean dashboard. The target audience is 2-10 person teams who need something simpler than Jira but more structured than a shared spreadsheet.

## Core Interaction Invariants

- A task always belongs to exactly one project and one column
- Columns have a strict left-to-right ordering; tasks move forward through the workflow
- Every task mutation (create, move, assign, complete) is timestamped and attributed to a user
- Deleting a project archives all its tasks — nothing is permanently destroyed
- Search results are scoped to projects the user has access to

## MVP Scope

### Must-Ship Features
1. **User authentication** — email/password registration and login with JWT tokens
2. **Project CRUD** — create, rename, archive projects; invite members by email
3. **Board view** — Kanban-style columns (default: To Do, In Progress, Done); drag-and-drop reordering
4. **Task management** — create, edit, assign, set due date, add description (markdown), move between columns
5. **Dashboard** — overview of all projects with task counts per column, overdue task alerts
6. **Activity feed** — chronological log of recent actions across all user's projects

### Explicitly NOT in MVP
- File attachments
- Real-time collaboration (WebSocket sync)
- Third-party integrations (Slack, GitHub)
- Time tracking
- Custom fields on tasks
- Mobile app

## Hard Boundaries

- **Routers** — HTTP parsing only; no business logic, no direct DB queries
- **Services** — all business logic lives here; no HTTP concepts (Request/Response)
- **Repos** — data access only; no business decisions, no HTTP awareness
- **Clients** — external API wrappers; no business logic

## Deployment Target

- Docker Compose for local development
- Single VPS or cloud instance for initial deployment
- Expected scale: <100 concurrent users in MVP
