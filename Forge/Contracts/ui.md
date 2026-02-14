# ForgeGuard -- UI/UX Blueprint

Canonical UI/UX specification for this project. The builder contract (S1) requires reading this file before making changes.

---

## 1) App Shell & Layout

### Device priority
- **Primary:** Desktop
- **Responsive strategy:** Desktop-first, scale down for tablet. No dedicated mobile layout in MVP.

### Shell structure
```
+------------------------------------------+
|  HEADER (logo, user avatar, logout)      |
+--------+---------------------------------+
|        |                                 |
| SIDEBAR|      MAIN CONTENT AREA          |
| (repo  |                                 |
|  list) |                                 |
|        |                                 |
+--------+---------------------------------+
```

### Navigation model
- **Primary nav:** Sidebar (persistent repo list)
- **Navigation items:** Repo list (sidebar), Commit timeline (main area), Audit detail (main area)

---

## 2) Screens / Views

### Screen: Login
- **Route:** `/login`
- **Purpose:** GitHub OAuth entry point
- **Content:**
  - ForgeGuard logo
  - "Sign in with GitHub" button
  - Brief tagline: "Monitor your repos. Catch violations before they ship."
- **Actions:**
  - Click "Sign in with GitHub" -> redirects to GitHub OAuth
- **Reached via:** Unauthenticated access to any route

### Screen: Repo List (Dashboard Home)
- **Route:** `/`
- **Purpose:** Overview of all connected repos with health status
- **Content:**
  - List of connected repos, each showing:
    - Repo name (org/repo format)
    - Health badge (green/yellow/red circle)
    - Last audit timestamp
    - Recent pass rate (e.g., "8/10 passed")
  - "Connect a Repo" button at top
  - Empty state if no repos connected
- **Actions:**
  - Click a repo -> navigate to Commit Timeline
  - Click "Connect a Repo" -> open repo picker modal
  - Click "Disconnect" (per repo, with confirmation)
- **Reached via:** Login redirect, sidebar click

### Screen: Repo Picker Modal
- **Route:** N/A (modal overlay on current screen)
- **Purpose:** Select a GitHub repo to connect
- **Content:**
  - Searchable list of user's GitHub repos not yet connected
  - Each entry shows repo name, visibility (public/private), default branch
- **Actions:**
  - Search/filter repos
  - Click a repo -> confirm connection -> webhook registered -> modal closes -> repo appears in list
  - Cancel -> close modal
- **Reached via:** "Connect a Repo" button

### Screen: Commit Timeline
- **Route:** `/repos/:repoId`
- **Purpose:** Chronological list of audited commits for a specific repo
- **Content:**
  - Repo name and health badge in header
  - Timeline of commits, newest first, each showing:
    - Commit SHA (short, 7 chars)
    - Commit message (truncated)
    - Author
    - Timestamp
    - Overall result badge (PASS green / FAIL red / PENDING gray / ERROR orange)
    - Mini check summary (e.g., "A4:PASS A9:PASS W1:WARN")
  - Pagination (load more)
- **Actions:**
  - Click a commit -> navigate to Audit Detail
  - Scroll for more commits
- **Reached via:** Clicking a repo on the dashboard

### Screen: Audit Detail
- **Route:** `/repos/:repoId/audits/:auditId`
- **Purpose:** Full breakdown of a single audit run
- **Content:**
  - Commit info header: SHA, message, author, branch, timestamp
  - Overall result banner (large PASS/FAIL)
  - Check results list, each showing:
    - Check code (A4, A9, W1)
    - Check name (Boundary compliance, Dependency gate, Secrets scan)
    - Result badge (PASS/FAIL/WARN/ERROR)
    - Detail text (what was found, if anything)
  - Files checked count
  - Duration (started_at to completed_at)
- **Actions:**
  - Back to timeline (breadcrumb or back button)
  - Click commit SHA to open on GitHub (external link)
- **Reached via:** Clicking a commit on the timeline

---

## 3) Component Inventory

| Component | Used on | Description |
|-----------|---------|-------------|
| HealthBadge | Repo List, Timeline | Colored circle (green/yellow/red) indicating repo/commit health |
| RepoCard | Repo List | Card showing repo name, health badge, last audit, pass rate |
| CommitRow | Timeline | Row showing commit info + result badges |
| CheckResult | Audit Detail | Card showing check code, name, result, detail text |
| ResultBanner | Audit Detail | Large colored banner showing overall PASS/FAIL |
| RepoPickerModal | Dashboard | Modal with searchable repo list for connecting |
| ConfirmDialog | Dashboard | Generic confirmation dialog for destructive actions |
| UserMenu | Header | Avatar dropdown with logout option |
| EmptyState | All screens | Friendly message + action when no data exists |

---

## 4) Visual Style

### Color palette
- **Primary:** Forge blue (#2563EB)
- **Background:** Dark (#0F172A slate-900)
- **Surface:** Dark gray (#1E293B slate-800)
- **Accent:** Green for PASS (#22C55E), Red for FAIL (#EF4444), Yellow for WARN (#EAB308), Gray for PENDING (#64748B)
- **Text:** White (#F8FAFC) primary, slate-400 (#94A3B8) secondary

### Typography
- **Font family:** System default (Inter if available)
- **Scale:** Comfortable

### Visual density
- Moderate -- dashboard-style with clear spacing between cards and rows

### Tone
- Professional, minimal, no decorative elements

---

## 5) Interaction Patterns

### Data loading
- Skeleton loaders for initial page load
- Inline spinners for action buttons (connect/disconnect)

### Empty states
- Repo list empty: "No repos connected yet. Click 'Connect a Repo' to get started."
- Timeline empty: "No audit results yet. Push a commit to trigger the first audit."

### Error states
- Toast notifications for transient errors (network failures, webhook issues)
- Inline error messages for form validation

### Confirmation pattern
- Modal dialog for destructive actions (disconnect repo)
- No confirmation needed for navigation or read actions

### Responsive behavior
- Sidebar collapses to hamburger menu below 1024px
- Cards stack vertically on narrow viewports
- Table rows become cards on mobile

---

## 6) User Flows (Key Journeys)

### Flow: First-time setup

1. User visits ForgeGuard -> sees Login screen
2. User clicks "Sign in with GitHub" -> redirected to GitHub -> authorizes -> redirected back
3. User lands on empty Repo List -> sees "No repos connected yet"
4. User clicks "Connect a Repo" -> repo picker modal opens -> user searches and selects a repo
5. Webhook is registered -> modal closes -> repo appears in list with "PENDING" health
6. User pushes a commit to the connected repo -> audit runs -> dashboard updates in real-time -> health badge turns green/red

### Flow: Reviewing an audit failure

1. User sees a repo with a red health badge on the dashboard
2. User clicks the repo -> sees commit timeline with a red FAIL commit at top
3. User clicks the failed commit -> sees Audit Detail
4. User sees: A4 FAIL -- "app/api/routers/webhook.py contains 'asyncpg' (DB access belongs in repos, not routers)"
5. User clicks commit SHA -> opens GitHub -> fixes the violation -> pushes

### Flow: Disconnecting a repo

1. User clicks "Disconnect" on a repo card
2. Confirmation dialog: "Remove RepoName from ForgeGuard? This will delete all audit history for this repo."
3. User confirms -> webhook removed -> repo and audit data deleted -> card disappears

---

## 7) What This Is NOT

- No settings page in MVP (config is .env only)
- No dark mode toggle (always dark)
- No drag-and-drop
- No animations beyond CSS transitions
- No notification preferences
- No user profile editing (GitHub profile is canonical)
- No team/org management
