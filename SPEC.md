Personal ticketing system.

DO NOT ADD ANY ASSUMED THINGS TO SPEC. No assets, no contact info, only do what is in spec.
Ask before adding any additional specs to ensure it is in line with designer priorities.

---

## Stack

- Python / Django
- SQLite
- Django built-in session auth
- Server-rendered Django templates (no SPA framework)

---

## Users

Two roles stored on a profile model:

| Role | Internal name |
|------|---------------|
| Ops | `ops` |
| Engineer | `engineer` |

Both roles can create tickets.

**Default landing page after login:**
- Ops → `/queue/`
- Engineer → `/calendar/` (day view)

---

## Models

### UserProfile
One-to-one with Django's built-in `User`.

| Field  | Type                  | Notes             |
|--------|-----------------------|-------------------|
| `user` | OneToOneField(User)   |                   |
| `role` | CharField(choices)    | `ops` / `engineer` |

### Ticket

| Field         | Type                        | Notes                        |
|---------------|-----------------------------|------------------------------|
| `id`          | AutoField (PK)              | Auto-increment ticket number |
| `title`       | CharField(max_length=200)   | Short summary of the ticket  |
| `description` | TextField                   | Full problem or task details |
| `status`      | CharField(choices)          | See statuses below           |
| `created_at`  | DateTimeField(auto_now_add) |                              |

**Statuses (in order):** `new` → `scheduled` → `in_progress` → `needs_new` → `waiting` → `complete`

### ScheduleEntry
One ticket can have many schedule entries across multiple engineers simultaneously.

| Field       | Type               | Notes                              |
|-------------|--------------------|------------------------------------|
| `id`        | AutoField (PK)     |                                    |
| `ticket`    | ForeignKey(Ticket) |                                    |
| `engineer`  | ForeignKey(User)   | Engineer this block is assigned to |
| `start`     | DateTimeField      |                                    |
| `end`       | DateTimeField      |                                    |

### TimeEntry
One ticket can have many time entries, each associated with one engineer.

| Field      | Type               | Notes                          |
|------------|--------------------|--------------------------------|
| `id`       | AutoField (PK)     |                                |
| `ticket`   | ForeignKey(Ticket) |                                |
| `engineer` | ForeignKey(User)   | Engineer who recorded the time |
| `notes`    | TextField          | Work notes                     |
| `start`    | DateTimeField      |                                |
| `end`      | DateTimeField      |                                |

### WorkSchedule
One-to-one with an engineer `User`. Stores working hours per day of the week.

| Field       | Type                   | Notes                              |
|-------------|------------------------|------------------------------------|
| `engineer`  | OneToOneField(User)    |                                    |
| `mon_start` | TimeField (nullable)   | null = not working that day        |
| `mon_end`   | TimeField (nullable)   |                                    |
| `tue_start` | TimeField (nullable)   |                                    |
| `tue_end`   | TimeField (nullable)   |                                    |
| `wed_start` | TimeField (nullable)   |                                    |
| `wed_end`   | TimeField (nullable)   |                                    |
| `thu_start` | TimeField (nullable)   |                                    |
| `thu_end`   | TimeField (nullable)   |                                    |
| `fri_start` | TimeField (nullable)   |                                    |
| `fri_end`   | TimeField (nullable)   |                                    |
| `sat_start` | TimeField (nullable)   |                                    |
| `sat_end`   | TimeField (nullable)   |                                    |
| `sun_start` | TimeField (nullable)   |                                    |
| `sun_end`   | TimeField (nullable)   |                                    |

Both start and end must be set or both left blank for a given day. `hours_for_date(d)` returns `(start, end)` for any date.

---

## Permissions

| Action                                                      | Ops | Engineer |
|-------------------------------------------------------------|-----|----------|
| Create ticket                                               | ✓   | ✓        |
| Change ticket status (`in_progress`, `needs_new`, `waiting`, `complete`) | ✓ | ✓ |
| Add schedule entry to ticket                                | ✓   | ✗        |
| Add time entry to ticket                                    | ✗   | ✓        |
| View engineer calendar                                      | ✗   | ✓        |
| View ops queue                                              | ✓   | ✗        |
| Edit own work schedule                                      | ✗   | ✓        |

There is no ticket ownership. Any engineer can change status or log time on any ticket.

---

## URLs & Views

| URL                         | Who      | Description                                     |
|-----------------------------|----------|-------------------------------------------------|
| `/login/`                   | All      | Django built-in login                           |
| `/logout/`                  | All      | Django built-in logout (POST)                   |
| `/tickets/`                 | All      | Ticket list                                     |
| `/tickets/create/`          | All      | Create a ticket; accepts `?next=` for redirect  |
| `/tickets/<id>/`            | All      | Ticket detail, status change, add time entry    |
| `/tickets/<id>/schedule/`   | Ops      | Add a schedule entry to a ticket                |
| `/calendar/`                | Engineer | Engineer calendar view                          |
| `/queue/`                   | Ops      | Ops queue view                                  |
| `/hours/`                   | Engineer | Edit own weekly work schedule                   |

---

## Datetime Input UX

All `start` and `end` time fields across schedule entries, time entries, and work schedule share these input rules:

- **Default date:** pre-filled to today's date (or the entry's date for existing records).
- **Shorthand time input:** the user may type a 3 or 4-digit number; the server parses it as HHMM. Examples:
  - `900` or `0900` → 09:00
  - `1100` → 11:00
  - `1430` → 14:30
- Full colon-separated formats (`09:00`, `09:00:00`) are also accepted, so existing database values round-trip cleanly.
- Parsing is handled server-side in custom Django form fields (`_TimeField`, `_OptionalTimeField`).
- Existing time values are displayed back to the user in HHMM format (e.g. `0900`).

---

## View Details

### Ticket List — `/tickets/`
- All tickets, showing ticket number, status badge, title, and created date.

### Create Ticket — `/tickets/create/`
- Form fields: `title`, `description`.
- Status defaults to `new` on creation.
- Accepts a `?next=<url>` query parameter; after saving, redirects there instead of the ticket list. The cancel link also follows `next`. Used by the queue view to return ops to `/queue/` after ticket creation.

### Ticket Detail — `/tickets/<id>/`
- Displays: ticket number, title, description, current status, all schedule entries, all time entries.
- Any logged-in user can update status to `in_progress`, `needs_new`, `waiting`, or `complete`.
- Engineers see a form to add a time entry (notes, date, start time, end time).
- Ops see a link to the schedule entry form.

### Add Schedule Entry — `/tickets/<id>/schedule/`
- Ops only.
- Form: engineer (select from engineer users), date, start time, end time.
- Date defaults to today; times accept shorthand input.
- If the selected engineer has a `WorkSchedule`, the entry is validated to fall within their working hours for that day. Ops cannot schedule outside those hours.
- After saving, ticket status is set to `scheduled` if not already.

### Engineer Calendar — `/calendar/`
- Engineer only.
- Three view modes: **day** (default), **week**, **agenda**. Toggle via buttons.
- Shows schedule entries and time entries for the logged-in engineer.
- Schedule entries render in blue; time entries in green.
- Clicking an entry navigates to that ticket's detail page.
- **Calendar grid:**
  - Spans 07:00–20:00. Each hour = 64 px.
  - Event height is proportional to duration.
  - Overlapping events are displayed side-by-side (greedy column assignment, Google Calendar style).
  - Hours outside the engineer's `WorkSchedule` are shaded light gray.
  - On load, the grid auto-scrolls to 30 minutes before the earliest work-start time in the displayed range.
  - A red horizontal line marks the current time of day; it repositions every 10 minutes via `setInterval` without a page reload.

### Ops Queue — `/queue/`
- Ops only. Two-pane layout:
  - **Left pane — Ticket queue:** lists all tickets whose status is NOT `complete` and NOT `scheduled`. Each ticket is a draggable card showing ticket number, status badge, and title. A `+` button in the pane header opens `/tickets/create/?next=/queue/`.
  - **Right pane — Engineer calendar:** shows one engineer's schedule entries for the current week. A dropdown selects the engineer; defaults to the first engineer alphabetically. Week navigation preserves the engineer selection.
- Dragging a ticket card and dropping it onto a time slot opens the schedule-entry form pre-filled with that engineer and the target hour; ops confirm and save.
- Clicking a ticket card (without dragging) navigates to that ticket's detail page.
- **Calendar grid:** same rules as engineer calendar (proportional heights, side-by-side overlaps, gray zones, current-time line).

### My Hours — `/hours/`
- Engineer only.
- Table of all seven days; each row has a start time and end time input (shorthand accepted).
- Leave both fields blank for days the engineer does not work.
- Saved values are used by the calendar views for gray-zone shading and by the schedule-entry form for work-hours validation.
