# TaskFlow — UI Blueprint

## App Shell & Layout
- **Device priority:** Desktop-first, responsive down to tablet (1024px min)
- **Shell structure:** Fixed top navbar (logo + nav links + user menu) → main content area → no footer
- **Navigation:** Top nav with: Dashboard, Projects (dropdown of user's projects), Settings
- **Auth pages:** Centered card layout (login, register) — no navbar

## Screens

### Dashboard (`/`)
- Grid of project cards showing: name, task count per column, overdue count
- "New Project" button (top-right)
- Empty state: illustration + "Create your first project" CTA

### Project Board (`/projects/:id`)
- Kanban columns rendered left-to-right
- Each column: header (name + task count) → vertical task card list
- Task cards show: title, assignee avatar, due date badge (red if overdue)
- "Add Task" button at bottom of each column
- Click task card → slide-out detail panel (right side)

### Task Detail (slide-out panel)
- Title (editable inline)
- Description (markdown editor)
- Assignee selector (dropdown of project members)
- Due date picker
- Column/status selector
- Activity log (chronological list of changes)
- Delete button (with confirmation)

### Settings (`/settings`)
- Profile: display name, email (read-only), change password
- Account: delete account (with confirmation)

## Component Inventory

| Component | Description |
|-----------|------------|
| `ProjectCard` | Dashboard card showing project summary |
| `KanbanColumn` | Single board column with task list |
| `TaskCard` | Compact task representation in column |
| `TaskDetailPanel` | Slide-out panel for full task editing |
| `UserAvatar` | Circle avatar with initials fallback |
| `EmptyState` | Illustration + message + CTA button |
| `ConfirmDialog` | Modal confirmation for destructive actions |

## Visual Style
- **Palette:** Neutral grays (#f8f9fa background, #1a1a2e dark text) + primary blue (#4361ee) + danger red (#e63946)
- **Typography:** Inter (sans-serif), 14px base, 1.5 line-height
- **Density:** Comfortable spacing — cards have 16px padding, 12px gaps
- **Tone:** Clean, professional, minimal — no gradients or shadows heavier than `shadow-sm`

## Key User Flows

### 1. First-Time Setup
Register → land on empty dashboard → create first project → default columns created → add first task

### 2. Daily Task Management
Dashboard → click project → view board → drag task to "In Progress" → click task → update description → close panel

### 3. Review Overdue Items
Dashboard → see red overdue badges → click project → filter/sort by due date → reassign or update dates
