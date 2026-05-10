import json
import os
import re
from datetime import datetime

import anthropic
from django.utils import timezone

from tickets.models import Agent, AgentMessage, ChatMessage, ScheduleEntry, Ticket, WorkSchedule


class GenericAgent:
    def __init__(self, agent_record):
        self._agent = agent_record
        self._claude = None

    @property
    def claude(self):
        if self._claude is None:
            self._claude = anthropic.Anthropic(api_key=os.environ.get('ANTHROPIC_API_KEY') or None)
        return self._claude

    @property
    def is_scheduler(self):
        return self._agent.is_scheduler

    # ── Group chat ────────────────────────────────────────────────────────────

    def get_chat_history(self, engineer_id):
        return list(
            ChatMessage.objects
            .filter(engineer_id=engineer_id, hidden=False)
            .select_related('sender')
            .order_by('sent_at')
        )

    def post_chat(self, engineer_id, body):
        ChatMessage.objects.create(
            engineer_id=engineer_id,
            sender=self._agent.ops_user,
            body=body,
        )

    # ── Document ──────────────────────────────────────────────────────────────

    def read_document(self):
        return self._agent.document

    def write_document(self, document):
        self._agent.document = document
        self._agent.save(update_fields=['document'])

    def write_status(self, status):
        self._agent.status = status
        self._agent.save(update_fields=['status'])

    # ── Schedule ──────────────────────────────────────────────────────────────

    def get_schedule(self, engineer_id, for_date):
        return list(
            ScheduleEntry.objects
            .filter(engineer_id=engineer_id, start__date=for_date)
            .select_related('ticket', 'created_by')
            .order_by('start')
        )

    def get_work_hours(self, engineer_id, for_date):
        try:
            ws = WorkSchedule.objects.get(engineer_id=engineer_id)
            return ws.hours_for_date(for_date)
        except WorkSchedule.DoesNotExist:
            return None, None

    def clear_future_entries(self, engineer_id, for_date=None):
        """Scheduler-only operation: delete this agent's non-locked future entries."""
        if not self._agent.is_scheduler:
            return
        now = timezone.now()
        qs = ScheduleEntry.objects.filter(
            engineer_id=engineer_id,
            created_by=self._agent,
            locked=False,
            start__gt=now,
        )
        if for_date is not None:
            qs = qs.filter(start__date=for_date)
        qs.delete()

    # ── Tickets ───────────────────────────────────────────────────────────────

    def create_ticket(self, title, description):
        ticket = Ticket.objects.create(title=title, description=description)
        return ticket.id

    def schedule_ticket(self, ticket_id, engineer_id, start_dt, end_dt):
        """Scheduler-only operation: create a ScheduleEntry owned by this agent."""
        if not self._agent.is_scheduler:
            raise PermissionError(
                f'Agent "{self._agent.name}" is a suggester and cannot edit the schedule.'
            )
        ticket = Ticket.objects.get(pk=ticket_id)
        entry = ScheduleEntry.objects.create(
            ticket=ticket,
            engineer_id=engineer_id,
            start=start_dt,
            end=end_dt,
            created_by=self._agent,
            locked=False,
        )
        if ticket.status != Ticket.SCHEDULED:
            ticket.status = Ticket.SCHEDULED
            ticket.save(update_fields=['status'])
        return entry.id

    # ── Agent cycle ───────────────────────────────────────────────────────────

    def is_relevant(self, message):
        answer = self._ask_claude(
            f'{self._agent.system_prompt}\n\n'
            f'Is this message relevant to your domain?\n\nMessage: {message}\n\nAnswer YES or NO only.',
            max_tokens=5,
        )
        return answer.upper().startswith('Y')

    def is_removal_directive(self, message):
        """True if the message is asking the scheduler to remove/cancel/delete entries."""
        answer = self._ask_claude(
            f'{self._agent.system_prompt}\n\n'
            f'Is this message asking to remove, cancel, clear, or delete scheduled entries '
            f'(not add or reschedule them)?\n\nMessage: {message}\n\nAnswer YES or NO only.',
            max_tokens=5,
        )
        return answer.upper().startswith('Y')

    def cancel_entries(self, engineer_id, for_date):
        """Scheduler-only: delete own entries for for_date and confirm in chat."""
        if not self._agent.is_scheduler:
            return
        self.clear_future_entries(engineer_id, for_date=for_date)
        self.post_chat(
            engineer_id,
            f"[{self._agent.name}] Got it — removed scheduled entries for {for_date:%A, %B %d}.",
        )
        self.write_status(Agent.COMMITTED)

    def suggest(self, engineer_id, for_date):
        """Suggester pass: post a proposal to group chat. Does NOT write to DB."""
        document = self.read_document()
        history = self.get_chat_history(engineer_id)
        schedule = self.get_schedule(engineer_id, for_date)
        work_start, work_end = self.get_work_hours(engineer_id, for_date)
        work_hours = f"{work_start:%H:%M}–{work_end:%H:%M}" if work_start else 'not configured'

        history_text = '\n'.join(
            f"{m.sent_at:%Y-%m-%d %H:%M}  {m.sender.username}: {m.body}"
            for m in history[-40:]
        ) or '(no messages yet)'

        schedule_text = '\n'.join(
            f"  {e.start:%H:%M}–{e.end:%H:%M}  #{e.ticket_id} {e.ticket.title}"
            for e in schedule
        ) or '(nothing scheduled)'

        prompt = f"""{self._agent.system_prompt}

Today is {for_date:%A, %B %d %Y}. Engineer work hours: {work_hours}.

You are a SUGGESTER. You can only post ideas to the group chat — you cannot
edit the schedule. A separate scheduler agent reads everyone's suggestions
and decides what actually goes on the calendar.

YOUR PRIOR NOTES ON THIS ENGINEER:
{document or '(none yet)'}

RECENT CHAT HISTORY:
{history_text}

CURRENT SCHEDULE FOR TODAY:
{schedule_text}

Post a clear, prioritised list of suggestions for what should be scheduled.
Each suggestion should include a preferred time window, a title, an estimated
duration, and a one-sentence rationale.
Respond in exactly this format:
SUGGESTION
- HH:MM–HH:MM | <title> | <one-sentence rationale>
END"""

        suggestion = self._ask_claude(prompt, max_tokens=400)
        self.post_chat(
            engineer_id,
            f"[{self._agent.name} — Suggestion for {for_date:%b %d}]\n{suggestion}",
        )
        self.write_status(Agent.COMMITTED)

    def schedule(self, engineer_id, for_date):
        """Scheduler-only pass: read all suggestions, decide, write DB entries."""
        if not self._agent.is_scheduler:
            return

        history = self.get_chat_history(engineer_id)
        schedule = self.get_schedule(engineer_id, for_date)
        work_start, work_end = self.get_work_hours(engineer_id, for_date)
        work_hours = f"{work_start:%H:%M}–{work_end:%H:%M}" if work_start else 'not configured'
        document = self.read_document()

        # Collect every other agent's most recent SUGGESTION...END block from chat,
        # in priority order (highest priority first).
        agents_by_user = {
            a.ops_user_id: a for a in Agent.objects.filter(engineer_id=engineer_id)
            if a.pk != self._agent.pk
        }
        latest_by_agent = {}
        for m in history:
            if m.sender_id not in agents_by_user:
                continue
            if 'SUGGESTION' not in m.body.upper():
                continue
            latest_by_agent[m.sender_id] = m.body

        ordered = sorted(
            latest_by_agent.items(),
            key=lambda item: agents_by_user[item[0]].priority,
        )
        suggestions_text = '\n\n'.join(
            f"[{agents_by_user[uid].name} | priority {agents_by_user[uid].priority}]\n{body}"
            for uid, body in ordered
        ) or '(no suggestions this cycle)'

        history_text = '\n'.join(
            f"{m.sent_at:%Y-%m-%d %H:%M}  {m.sender.username}: {m.body}"
            for m in history[-40:]
        ) or '(no messages yet)'

        existing_text = '\n'.join(
            f"  {e.start:%H:%M}–{e.end:%H:%M}  #{e.ticket_id} {e.ticket.title}"
            f"  [{'locked' if e.locked else 'agent'}]"
            for e in schedule
        ) or '(nothing scheduled)'

        prompt = f"""{self._agent.system_prompt}

You are the SCHEDULER for this engineer. You are the only agent that can
write to the calendar. The other agents are domain experts that posted
suggestions to the group chat below; weigh them, then commit a final plan.

Today is {for_date:%A, %B %d %Y}. Engineer work hours: {work_hours}.

YOUR PRIOR NOTES:
{document or '(none yet)'}

CURRENT SCHEDULE FOR TODAY (locked entries from ops are immovable):
{existing_text}

SUGGESTIONS FROM OTHER AGENTS (in priority order — lower number is higher priority):
{suggestions_text}

RECENT CHAT HISTORY:
{history_text}

Decide the final time blocks to schedule for today. Respect:
- All locked entries (do not overlap).
- The engineer's working hours.
- Higher-priority suggestions over lower-priority ones when they conflict.
You may merge, drop, or shift suggestions. Do not invent activities outside
the suggesters' domains unless the chat history explicitly asks for it.
Respond in exactly this format:
PLAN
- HH:MM–HH:MM | <title> | <one-sentence rationale>
END"""

        plan = self._ask_claude(prompt, max_tokens=600)
        self.post_chat(
            engineer_id,
            f"[{self._agent.name} — Final plan for {for_date:%b %d}]\n{plan}",
        )

        plan_match = re.search(r'PLAN\s*\n(.*?)\nEND\b', plan, re.DOTALL | re.IGNORECASE)
        plan_body = plan_match.group(1).strip() if plan_match else plan

        extract_prompt = f"""Extract scheduled activities from this plan and return a JSON array.
Each object must have exactly these keys: "title" (string), "start" (HH:MM), "end" (HH:MM), "description" (string — 1-2 sentences explaining what this block is for and why).
Return only valid JSON — no markdown, no explanation.

PLAN:
{plan_body}

Example: [{{"title": "Morning stretch", "start": "09:00", "end": "09:15", "description": "Light mobility work to start the day."}}]"""

        raw = self._ask_claude(extract_prompt, max_tokens=600)

        committed = []
        try:
            blocks = json.loads(raw)
            existing_ranges = [(e.start, e.end) for e in schedule if e.locked]
            for block in blocks:
                s = datetime.strptime(block['start'], '%H:%M').time()
                e = datetime.strptime(block['end'], '%H:%M').time()
                if work_start and work_end:
                    if s < work_start or e > work_end:
                        continue
                start_dt = datetime.combine(for_date, s)
                end_dt = datetime.combine(for_date, e)
                if any(start_dt < lend and end_dt > lstart for lstart, lend in existing_ranges):
                    continue
                description = block.get('description') or (
                    f"Scheduled by {self._agent.name} on {for_date:%Y-%m-%d}."
                )
                ticket_id = self.create_ticket(
                    title=block['title'],
                    description=description,
                )
                self.schedule_ticket(ticket_id, engineer_id, start_dt, end_dt)
                committed.append(block)
        except (json.JSONDecodeError, KeyError, ValueError):
            pass

        update_prompt = f"""{self._agent.system_prompt}

Update your running notes on this engineer.

CURRENT NOTES:
{document or '(none)'}

TODAY ({for_date:%Y-%m-%d}) YOU COMMITTED:
{json.dumps(committed, indent=2) if committed else '(nothing scheduled this run)'}

RECENT CHAT (last 10 messages):
{chr(10).join(f"{m.sent_at:%Y-%m-%d %H:%M}  {m.sender.username}: {m.body}" for m in history[-10:])}

Write updated notes in plain text (under 400 words)."""

        new_doc = self._ask_claude(update_prompt, max_tokens=600)
        self.write_document(new_doc)
        self.write_status(Agent.COMMITTED)

    def commit_chat_to_document(self):
        """Read one-on-one chat and rewrite document. Called by Commit to State button."""
        messages = list(
            AgentMessage.objects
            .filter(agent=self._agent)
            .order_by('sent_at')
        )
        history_text = '\n'.join(
            f"{'Engineer' if m.role == AgentMessage.USER else self._agent.name}: {m.body}"
            for m in messages
        ) or '(no messages yet)'

        prompt = f"""{self._agent.system_prompt}

The engineer has been chatting with you to update your understanding of their priorities and context.

ONE-ON-ONE CONVERSATION:
{history_text}

CURRENT NOTES:
{self._agent.document or '(none)'}

Rewrite your private state document based on this conversation. Be concise and specific (under 500 words)."""

        new_doc = self._ask_claude(prompt, max_tokens=800)
        self.write_document(new_doc)

    # ── Claude ────────────────────────────────────────────────────────────────

    def _ask_claude(self, prompt, max_tokens=1024):
        response = self.claude.messages.create(
            model='claude-sonnet-4-6',
            max_tokens=max_tokens,
            messages=[{'role': 'user', 'content': prompt}],
        )
        return response.content[0].text.strip()
