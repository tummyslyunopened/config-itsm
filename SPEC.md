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

| Field        | Type               | Notes                                                        |
|--------------|--------------------|--------------------------------------------------------------|
| `id`         | AutoField (PK)     |                                                              |
| `ticket`     | ForeignKey(Ticket) |                                                              |
| `engineer`   | ForeignKey(User)   | Engineer this block is assigned to                           |
| `start`      | DateTimeField      |                                                              |
| `end`        | DateTimeField      |                                                              |
| `created_by` | ForeignKey(Agent, null=True, on_delete=SET_NULL) | The agent that created this entry; `null` = manually created |
| `locked`     | BooleanField       | `True` when `created_by` is null — agents cannot move or delete these |

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

### ChatMessage
One message in an engineer's group chat. Each engineer has one chat thread shared with all ops users and agents.

| Field      | Type                        | Notes                                                   |
|------------|-----------------------------|---------------------------------------------------------|
| `id`       | AutoField (PK)              |                                                         |
| `engineer` | ForeignKey(User)            | The engineer whose chat this message belongs to         |
| `sender`   | ForeignKey(User)            | The user (ops, engineer, or agent ops account) who sent it |
| `body`     | TextField                   |                                                         |
| `sent_at`  | DateTimeField(auto_now_add) |                                                         |
| `hidden`   | BooleanField(default=False) | Hidden messages are excluded from all UI views and agent queries; they are not deleted |

Ordered by `sent_at` ascending.

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

### Agent
One row per agent instance. Agents belong to a single engineer and are created by that engineer through the web UI. Creating an agent automatically provisions a dedicated ops `User` + `UserProfile` + `ApiKey` for it.

| Field          | Type                        | Notes                                                         |
|----------------|-----------------------------|---------------------------------------------------------------|
| `id`           | AutoField (PK)              |                                                               |
| `engineer`     | ForeignKey(User)            | The engineer who owns this agent                              |
| `ops_user`     | OneToOneField(User)         | Auto-provisioned ops account used to post to group chat       |
| `name`         | CharField(max_length=100)   | Human-readable label, e.g. "Health & Fitness"                 |
| `system_prompt`| TextField                   | Instructions that define the agent's domain and behaviour     |
| `priority`     | IntegerField                | Unique per engineer. Lower number = higher priority.          |
| `status`       | CharField(choices)          | `deliberating` / `committed`                                  |
| `document`     | TextField                   | Agent's private running notes; never shown to the engineer    |
| `created_at`   | DateTimeField(auto_now_add) |                                                               |

Unique together: `(engineer, priority)`.

### AgentMessage
One message in a one-on-one chat between an agent and its engineer. This chat is for updating the agent's priorities only; it does not produce schedule changes directly.

| Field     | Type                        | Notes                            |
|-----------|-----------------------------|----------------------------------|
| `id`      | AutoField (PK)              |                                  |
| `agent`   | ForeignKey(Agent)           |                                  |
| `role`    | CharField(choices)          | `user` (engineer) / `agent`      |
| `body`    | TextField                   |                                  |
| `sent_at` | DateTimeField(auto_now_add) |                                  |

Ordered by `sent_at` ascending.

---

## Permissions

| Action                                                      | Ops | Engineer |
|-------------------------------------------------------------|-----|----------|
| Create ticket                                               | ✓   | ✓        |
| Change ticket status (`in_progress`, `needs_new`, `waiting`, `complete`) | ✓ | ✓ |
| Add schedule entry to ticket                                | ✓   | ✗        |
| Add time entry to ticket                                    | ✗   | ✓        |
| Delete own time entries                                     | ✗   | ✓        |
| View engineer calendar                                      | ✗   | ✓        |
| View ops queue                                              | ✓   | ✗        |
| Edit own work schedule                                      | ✗   | ✓        |
| Post to own chat                                            | ✗   | ✓        |
| Post to any engineer's chat                                 | ✓   | ✗        |
| Clear own chat (hide all messages)                          | ✗   | ✓        |
| Clear any engineer's chat                                   | ✓   | ✗        |
| View own chat                                               | ✗   | ✓        |
| View any engineer's chat                                    | ✓   | ✗        |
| Create / edit own agents                                    | ✗   | ✓        |
| View own agents list and one-on-one chats                   | ✗   | ✓        |
| Post to own agent's one-on-one chat                         | ✗   | ✓        |
| Trigger "commit to state" on own agent                      | ✗   | ✓        |

There is no ticket ownership. Any engineer can change status or log time on any ticket. An engineer can only delete their own time entries.

---

## AI Agents

AI agents authenticate with an **API key** rather than a browser session. Each API key is linked to an ops-role user account and grants identical permissions to a human ops user.

### API Key Model

| Field        | Type                        | Notes                      |
|--------------|-----------------------------|----------------------------|
| `id`         | AutoField (PK)              |                            |
| `user`       | ForeignKey(User)            | Must have `ops` role       |
| `key`        | CharField                   | Randomly generated, unique |
| `created_at` | DateTimeField(auto_now_add) |                            |

When an engineer creates an `Agent` record, the system automatically provisions a dedicated ops `User`, `UserProfile`, and `ApiKey` for that agent. There is no manual key creation flow.

Agents send the key in the `Authorization` header on every request:

```
Authorization: Api-Key <key>
```

### Agent Endpoints

All agent endpoints return JSON. They accept JSON request bodies where input is required. Session auth is not accepted on these endpoints.

| Method | URL                                           | Description                                                                                                                          |
|--------|-----------------------------------------------|--------------------------------------------------------------------------------------------------------------------------------------|
| GET    | `/api/tickets/`                               | List all tickets not in `complete` status (the ops queue)                                                                            |
| GET    | `/api/tickets/<id>/`                          | Ticket detail: fields, all schedule entries (including `created_by` and `locked`), all time entries                                  |
| POST   | `/api/tickets/`                               | Create a ticket (`title`, `description`)                                                                                             |
| POST   | `/api/tickets/<id>/status/`                   | Change ticket status (`status` field)                                                                                                |
| GET    | `/api/engineers/`                             | List all engineer users and their work schedules                                                                                     |
| GET    | `/api/engineers/<id>/schedule/`               | Schedule entries for one engineer (`?date=YYYY-MM-DD` for a single day or `?week=YYYY-MM-DD` for the week containing that date); includes `created_by` and `locked` per entry |
| POST   | `/api/tickets/<id>/schedule/`                 | Add a schedule entry (`engineer_id`, `date`, `start`, `end`); `created_by` is derived from the API key — the calling agent is the owner |
| GET    | `/api/engineers/<id>/chat/`                   | All non-hidden messages in an engineer's group chat, ordered oldest-first                                                            |
| POST   | `/api/engineers/<id>/chat/`                   | Post a message to an engineer's group chat (`body` field); sender is the API key's linked ops user                                   |
| GET    | `/api/agents/<agent_id>/state/`               | Read an agent's `status` and `document`                                                                                              |
| PUT    | `/api/agents/<agent_id>/state/`               | Update an agent's `status` and/or `document` (either or both fields)                                                                |
| DELETE | `/api/agents/<agent_id>/schedule/future/`     | Delete all non-locked schedule entries created by this agent for its engineer where `start > now`                                    |

Time values in request/response bodies use `HH:MM` format (24-hour). All datetimes are in the server's local timezone.

Chat message objects in API responses contain: `id`, `engineer_id`, `sender_id`, `sender` (username), `body`, `sent_at` (ISO 8601).

Schedule entry objects in API responses contain: `id`, `ticket_id`, `engineer_id`, `engineer` (username), `start`, `end`, `created_by` (agent id or null), `locked`.

### Agent Document

Each `Agent` record carries a `document` field — a free-form text block the agent owns entirely. It is never shown to the engineer in the calendar or queue views. The agent reads it at the start of every trigger and overwrites it at the end. It is used to carry forward context, priorities, and observations across runs.

The engineer can influence the document indirectly by chatting with the agent in the one-on-one chat and pressing **Commit to State**, which causes the agent to read the conversation and rewrite its document.

### Agent Cycle

Each engineer has their own set of personal agents. The scheduling cycle is run independently per engineer, using only that engineer's agents ordered by their priority.

The message trigger includes two additional pre-cycle steps: removal detection and relevance filtering.

```
TRIGGER: 4am daily  — fires for every engineer who has working hours that day
TRIGGER: new chat   — fires for the engineer whose group chat received the message

═══════════════════════════════════════════════════════
MESSAGE TRIGGER ONLY — BROADCAST CHECK
  If the message explicitly addresses all agents
  ("all agents", "every agent", "all of you"):
    All agents are marked relevant. Skip self-evaluation.
  Otherwise:
    Each agent independently reads the new message.
    Asks Claude: "Is this relevant to my domain?"
    Not relevant → status: committed, no further action.
    Relevant     → continues below.

MESSAGE TRIGGER ONLY — REMOVAL CHECK
  Each relevant agent asks Claude:
    "Is this message asking me to remove/cancel/delete my entries?"
  If YES:
    Agent deletes its own entries for the target date(s).
    Agent posts a confirmation message to group chat.
    Agent status → committed. Does not enter scheduling cycle.
  If NO:
    Agent enters the scheduling cycle below.

MESSAGE TRIGGER ONLY — DATE PARSING
  Target dates are extracted from the message:
    "tomorrow"              → next calendar day
    "next <weekday>"        → that weekday of the following week
    "<weekday>"             → the nearest upcoming occurrence of that day
    "next week" /
    "whole week" /
    "this week" /
    "all week" /
    "every day"             → Mon–Fri of the relevant week
  The scheduling cycle runs once per extracted date.
═══════════════════════════════════════════════════════

For each target date:

1. RESET
   All (relevant) agents → status: deliberating.
   Each agent deletes its own non-locked ScheduleEntry records
   for this engineer on this specific date where start > now.

2. PROPOSAL PASS  (all agents, any order)
   Each agent reads its document, the engineer's full non-hidden
   group chat history, and the current DB schedule for the date.
   Posts proposed time blocks to the engineer's group chat.
   The current DB schedule is shown as authoritative context.

3. RESOLUTION PASS  (lowest priority → highest priority)
   Each agent queries the DB for entries on this date from other
   agents and locked entries — this is the authoritative source.
   Chat proposals are only consulted for agents that have not yet
   committed to the DB in this cycle (same-cycle coordination).
   Posts a revised proposal to group chat.

4. COMMIT PASS  (highest priority → lowest priority)
   Each agent reads the PROPOSAL...END block from its latest
   revised proposal (only that block — context text is excluded).
   Creates tickets and ScheduleEntry records (created_by=<agent>,
   locked=False). Updates its document. Status → committed.
   Blocks outside the engineer's work hours are skipped.

5. CONFLICT CLEANUP  (lowest priority → highest priority)
   Each agent deletes its own entries for this date that overlap
   with locked entries or entries from higher-priority agents.

6. All agents reach committed. Cycle complete for this date.
```

Agents may only schedule within the engineer's `WorkSchedule` hours for that day, regardless of trigger time. Manually created schedule entries (`locked=True`, `created_by=null`) are never moved or deleted by agents.

### Agent Code Structure

Agent code lives inside the Django project at `agents/`. There are no per-domain Python files — all agents are driven by their `system_prompt` stored in the database.

```
agents/
  base.py              GenericAgent class
                         Initialised with an Agent DB record.
                         Claude API client (api key from environment).
                         Shared methods:
                           get_chat_history(engineer_id)
                             — excludes hidden=True messages
                           post_chat(engineer_id, body)
                           read_document() → str
                           write_document(document)
                           write_status(status)
                           get_schedule(engineer_id, for_date)
                             — includes created_by via select_related
                           get_work_hours(engineer_id, for_date)
                           clear_future_entries(engineer_id, for_date=None)
                             — scoped to for_date when provided
                           delete_conflicting_own_entries(engineer_id, for_date)
                             — removes own entries that overlap higher-priority
                               or locked entries committed to DB
                           create_ticket(title, description) → ticket_id
                           schedule_ticket(ticket_id, engineer_id, start_dt, end_dt)
                           is_relevant(message) → bool
                           is_removal_directive(message) → bool
                           cancel_entries(engineer_id, for_date)
                             — clears entries + posts confirmation to chat
                           propose(engineer_id, for_date)
                           resolve(engineer_id, for_date)
                             — uses DB entries as authoritative source;
                               only consults chat proposals for agents
                               not yet committed to DB this cycle
                           commit(engineer_id, for_date)
                             — extracts only from PROPOSAL...END block
                           commit_chat_to_document()

  runner.py            Orchestration
                         _parse_single_date(message) → date
                         _parse_target_dates(message) → [date, ...]
                           — handles single day, weekday names,
                             "next week", "whole week", etc.
                         _all_agents_addressed(message) → bool
                         _run_cycle(engineer_id, for_date, agents)
                           — reset → propose → resolve → commit →
                             conflict cleanup for one date
                         standup(engineer_id, for_date)
                         on_message(engineer_id, message)
                           — broadcast check → relevance/removal
                             check → _run_cycle per date
                         run_morning()
                           — fires standup() for all engineers with
                             working hours today
```

`run_morning()` is invoked by a management command (`python manage.py run_morning_standup`) scheduled via cron at 04:00 daily.

---

## URLs & Views

| URL                           | Who      | Description                                                       |
|-------------------------------|----------|-------------------------------------------------------------------|
| `/login/`                     | All      | Django built-in login                                             |
| `/logout/`                    | All      | Django built-in logout (POST)                                     |
| `/tickets/`                   | All      | Ticket list                                                       |
| `/tickets/create/`            | All      | Create a ticket; accepts `?next=` for redirect                    |
| `/tickets/<id>/`              | All      | Ticket detail, status change, add/delete time entry               |
| `/tickets/<id>/schedule/`     | Ops      | Add a schedule entry to a ticket                                  |
| `/calendar/`                  | Engineer | Engineer calendar — two-pane with live chat panel                 |
| `/queue/`                     | Ops      | Ops queue view                                                    |
| `/hours/`                     | Engineer | Edit own weekly work schedule                                     |
| `/chat/`                      | All      | Chat view (engineer sees own chat; ops sees per-engineer dropdown)|
| `/chat/poll/`                 | All      | JSON polling endpoint — returns new messages and typing status    |
| `/chat/post/`                 | All      | JSON POST endpoint — saves a message and triggers agents          |
| `/chat/clear/`                | All      | POST — marks all visible messages as hidden                       |
| `/agents/`                    | Engineer | List the engineer's own agents                                    |
| `/agents/create/`             | Engineer | Create a new agent                                                |
| `/agents/suggest-prompt/`     | Engineer | POST `{name, existing_prompt, user_prompt}` → JSON `{prompt}`; generates a system prompt via Claude using the agent name, current prompt, user instructions, and engineer's work schedule |
| `/agents/<id>/`               | Engineer | One-on-one chat with an agent + Commit to State button            |
| `/agents/<id>/edit/`          | Engineer | Edit an agent's name, priority, and system prompt                 |

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
- Engineers see a **Delete** button next to each of their own time entries. Deletion requires confirmation and is scoped to the logged-in engineer's own entries only.
- Ops see a link to the schedule entry form.

### Add Schedule Entry — `/tickets/<id>/schedule/`
- Ops only.
- Form: engineer (select from engineer users), date, start time, end time.
- Date defaults to today; times accept shorthand input.
- If the selected engineer has a `WorkSchedule`, the entry is validated to fall within their working hours for that day. Ops cannot schedule outside those hours.
- After saving, ticket status is set to `scheduled` if not already.

### Engineer Calendar — `/calendar/`
- Engineer only.
- **Two-pane layout:** the calendar fills the left pane; a live chat panel occupies a fixed-width right pane. The two panes fill the full viewport height below the nav bar.
- Three view modes: **day** (default), **week**, **agenda**. Toggle via buttons in the calendar pane header.
- Shows schedule entries and time entries for the logged-in engineer.
- Schedule entries render in blue; time entries in green.
- Clicking an entry navigates to that ticket's detail page.
- **Calendar grid:**
  - Spans 07:00–20:00. Each hour = 64 px.
  - Event height is proportional to duration.
  - Overlapping events are displayed side-by-side (greedy column assignment).
  - Hours outside the engineer's `WorkSchedule` are shaded light gray.
  - On load, the grid auto-scrolls so the engineer's work-start time appears at the top (30 minutes of padding above).
  - A red horizontal line marks the current time of day; it repositions every minute via `setInterval`.
- **Live calendar refresh:** the chat panel polls every 2 seconds. When the poll detects new messages from agents (sender username starts with `agent_`) or the typing indicator transitions from on to off, the calendar immediately re-fetches events via `?fragment=1` (a JSON endpoint returning pre-positioned event data) and re-renders the event elements in place — no page reload.
- **Chat panel (right pane):**
  - Displays the engineer's own chat thread with live polling (same as `/chat/`).
  - Animated typing indicator (`...`) appears while any of the engineer's agents has `status = deliberating`.
  - The engineer can post messages directly from the calendar without navigating away.
  - A **Clear** button at the top of the chat panel marks all visible messages as hidden.

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
- Saved values are used by the calendar views for gray-zone shading and by the schedule-entry form for work-hours validation. Also used by the agent system-prompt generator.

### Chat — `/chat/`
- Accessible to all logged-in users.
- **Engineer view:** shows the logged-in engineer's own chat thread. All non-hidden messages displayed oldest-first. A text input + submit button posts a message via `/chat/post/` (AJAX — no page reload). An animated typing indicator (`...`) appears while any of the engineer's agents has `status = deliberating`.
- **Ops view:** same layout, plus a dropdown to select which engineer's chat to display.
- Each message displays: sender username, timestamp, and body.
- The page polls `/chat/poll/` every 2 seconds to append new messages and update the typing indicator without a reload.
- A **Clear chat** button marks all currently-visible messages as `hidden=True`. Hidden messages disappear from the UI immediately and are excluded from all future agent queries and chat polls.

### Chat Poll — `/chat/poll/`
- GET. Returns `{messages: [...], typing: bool}`.
- `messages`: all non-hidden `ChatMessage` records with `id > ?since` for the engineer, ordered by `sent_at`.
- `typing`: `true` if any of the engineer's agents has `status = deliberating`.
- Engineer role uses `request.user` as the engineer. Ops role requires `?engineer=<id>`.

### Chat Post — `/chat/post/`
- POST `{body, [engineer_id]}`. Saves a `ChatMessage` and triggers `on_message` in a background thread.
- Returns `{ok: true, id: <message_id>}`.

### Chat Clear — `/chat/clear/`
- POST `{[engineer_id]}`. Sets `hidden=True` on all currently-visible messages for the engineer.
- Returns `{ok: true}`.

### Agent List — `/agents/`
- Engineer only.
- Lists all agents belonging to the logged-in engineer, showing name, priority, current status, and links to the chat and edit views.
- A **Create Agent** button links to `/agents/create/`.

### Create Agent — `/agents/create/`
- Engineer only.
- Form fields: `name`, `priority` (integer, unique among the engineer's agents), `system_prompt`.
- A **Generate from name & instructions** button calls `/agents/suggest-prompt/` (AJAX). It sends the agent name, any text already in the system prompt field, and an optional free-text instructions box. The returned suggestion fills the system prompt textarea for review before saving.
- On save, the system auto-provisions a dedicated ops `User`, `UserProfile`, and `ApiKey` for the new agent.
- Redirects to `/agents/` after creation.

### Agent Suggest Prompt — `/agents/suggest-prompt/`
- Engineer only. POST `{name, existing_prompt, user_prompt}` → JSON `{prompt}`.
- Calls Claude to generate or refine a system prompt. The meta-prompt includes:
  - The agent name.
  - The existing system prompt (rewrite or refine, not verbatim copy).
  - The user's specific instructions (followed closely).
  - The engineer's full `WorkSchedule` (days and hours), so the generated prompt references actual working hours.
- The generated prompt instructs the agent on its domain, example activities, durations, and conflict-handling behaviour (yield to higher-priority agents).

### Agent Chat — `/agents/<id>/`
- Engineer only. The engineer must own the agent.
- Displays the one-on-one conversation between the engineer and this agent, oldest-first.
- A text input + submit button lets the engineer post a message.
- A **Commit to State** button triggers `commit_chat_to_document()` on the agent. The agent reads the full one-on-one conversation history and rewrites its `document`.

### Edit Agent — `/agents/<id>/edit/`
- Engineer only. The engineer must own the agent.
- Form fields: `name`, `priority`, `system_prompt`.
- A **Regenerate from name, current prompt & instructions** button works identically to the one on the create form, pre-seeding the existing system prompt as the `existing_prompt` input.
- Redirects to `/agents/` after saving.
