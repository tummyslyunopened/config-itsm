# config-itsm

A personal IT service management tool built with Django and SQLite. Two roles — **ops** and **engineer** — with a ticket queue, drag-and-drop scheduling, and a calendar view that overlays scheduled and logged time.

Vendored as a submodule of
[`tummyslyunopened/config`](https://github.com/tummyslyunopened/config).

---

## Setup

**Requirements:** Python 3.11+

```bash
python -m venv .venv
.venv\Scripts\activate        # Windows
# source .venv/bin/activate   # macOS / Linux

pip install -r requirements.txt
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

Create user accounts via the Django admin at `/admin/`. Assign each user a `UserProfile` with role `ops` or `engineer`.

---

## Roles

| Role | Default landing | Key access |
|------|----------------|------------|
| **Ops** | `/queue/` | Schedule tickets, view engineer calendars |
| **Engineer** | `/calendar/` | Log time, manage own work schedule |

Both roles can create tickets and update ticket status.

---

## Features

### Ticket lifecycle
Statuses flow `new → scheduled → in_progress → needs_new → waiting → complete`. Ops schedule tickets; engineers update status and log time worked.

### Ops queue (`/queue/`)
Two-pane view: ticket queue on the left, engineer calendar on the right. Drag a ticket onto a calendar time slot to open a pre-filled schedule form. The `+` button creates a new ticket and returns to the queue.

### Engineer calendar (`/calendar/`)
Day view by default, with week and agenda modes. Schedule entries (blue) and logged time (green) are rendered as proportional blocks. Overlapping events tile side-by-side. A red line tracks the current time and updates every 10 minutes.

### Work schedule (`/hours/`)
Engineers set their working hours per day of the week. Non-working hours are shaded gray on all calendar views. Ops cannot schedule outside an engineer's working hours.

### Time input shorthand
All time fields accept plain 4-digit input — `0900`, `1430` — as well as standard `HH:MM` format.
