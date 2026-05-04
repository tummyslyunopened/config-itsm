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

    def delete_conflicting_own_entries(self, engineer_id, for_date):
        """Delete own entries for for_date that overlap with locked or higher-priority entries."""
        from django.db.models import Q
        own = list(ScheduleEntry.objects.filter(
            engineer_id=engineer_id,
            created_by=self._agent,
            start__date=for_date,
        ))
        if not own:
            return
        blockers = list(ScheduleEntry.objects.filter(
            engineer_id=engineer_id,
            start__date=for_date,
        ).filter(
            Q(locked=True) | Q(created_by__priority__lt=self._agent.priority)
        ).exclude(created_by=self._agent))
        for entry in own:
            for blocker in blockers:
                if entry.start < blocker.end and entry.end > blocker.start:
                    entry.delete()
                    break

    # ── Tickets ───────────────────────────────────────────────────────────────

    def create_ticket(self, title, description):
        ticket = Ticket.objects.create(title=title, description=description)
        return ticket.id

    def schedule_ticket(self, ticket_id, engineer_id, start_dt, end_dt):
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
        """True if the message is asking this agent to remove/cancel/delete its entries."""
        answer = self._ask_claude(
            f'{self._agent.system_prompt}\n\n'
            f'Is this message asking you specifically to remove, cancel, clear, or delete your scheduled entries '
            f'(not add or reschedule them)?\n\nMessage: {message}\n\nAnswer YES or NO only.',
            max_tokens=5,
        )
        return answer.upper().startswith('Y')

    def cancel_entries(self, engineer_id, for_date):
        """Delete own entries for for_date and post a confirmation to group chat."""
        self.clear_future_entries(engineer_id, for_date=for_date)
        self.post_chat(
            engineer_id,
            f"[{self._agent.name}] Got it — removed my scheduled entries for {for_date:%A, %B %d}.",
        )
        self.write_status(Agent.COMMITTED)

    def propose(self, engineer_id, for_date):
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
            f"  [{'locked' if e.locked else 'agent'}]"
            for e in schedule
        ) or '(nothing scheduled)'

        prompt = f"""{self._agent.system_prompt}

Today is {for_date:%A, %B %d %Y}. Engineer work hours: {work_hours}.

YOUR PRIOR NOTES ON THIS ENGINEER:
{document or '(none yet)'}

RECENT CHAT HISTORY:
{history_text}

CURRENT SCHEDULE FOR TODAY:
{schedule_text}

Propose specific time blocks to schedule for this engineer today.
Each block must fit within work hours and not overlap existing schedule entries.
Respond in exactly this format:
PROPOSAL
- HH:MM–HH:MM | <title> | <one-sentence rationale>
END"""

        proposal = self._ask_claude(prompt, max_tokens=400)
        self.post_chat(
            engineer_id,
            f"[{self._agent.name} — Proposal for {for_date:%b %d}]\n{proposal}",
        )
        self.write_status(Agent.DELIBERATING)

    def resolve(self, engineer_id, for_date):
        history = self.get_chat_history(engineer_id)
        schedule = self.get_schedule(engineer_id, for_date)
        work_start, work_end = self.get_work_hours(engineer_id, for_date)
        work_hours = f"{work_start:%H:%M}–{work_end:%H:%M}" if work_start else 'not configured'

        own = next(
            (m.body for m in reversed(history)
             if m.sender_id == self._agent.ops_user_id and f'[{self._agent.name}' in m.body),
            '(none)',
        )

        # DB entries from other agents — the authoritative source of truth
        others_db = [
            e for e in schedule
            if not (e.created_by and e.created_by_id == self._agent.pk)
        ]
        agents_in_db = {e.created_by_id for e in others_db if e.created_by}

        def _fmt(e):
            label = 'locked' if e.locked else (e.created_by.name if e.created_by else 'unknown')
            return f"  {e.start:%H:%M}–{e.end:%H:%M}  {e.ticket.title}  [{label}]"

        higher_committed = [
            _fmt(e) for e in others_db
            if e.locked or (e.created_by and e.created_by.priority < self._agent.priority)
        ]
        lower_committed = [
            _fmt(e) for e in others_db
            if e.created_by and e.created_by.priority > self._agent.priority
        ]

        # Chat proposals only for agents NOT yet committed to DB (same-cycle coordination)
        higher_pending, lower_pending = [], []
        for m in history:
            if m.sender_id == self._agent.ops_user_id or 'PROPOSAL' not in m.body.upper():
                continue
            other = Agent.objects.filter(ops_user_id=m.sender_id, engineer_id=engineer_id).first()
            if other is None or other.pk in agents_in_db:
                continue  # Already in DB — the DB entry is definitive, ignore this chat proposal
            label = f"[{other.name} — proposed this cycle, not yet committed]\n{m.body}"
            if other.priority < self._agent.priority:
                higher_pending.append(label)
            else:
                lower_pending.append(label)

        must_yield = higher_committed + higher_pending
        may_override = lower_committed + lower_pending

        prompt = f"""{self._agent.system_prompt}

YOUR CURRENT PROPOSAL:
{own}

COMMITTED ENTRIES FROM HIGHER-PRIORITY AGENTS — you MUST move your blocks away from these (these are from the database and are definitive):
{chr(10).join(must_yield) if must_yield else '(none — you are the highest priority or no conflicts)'}

COMMITTED ENTRIES FROM LOWER-PRIORITY AGENTS — these yield to you, but avoid overlap anyway:
{chr(10).join(may_override) if may_override else '(none)'}

WORK HOURS: {work_hours}

Rules:
- The committed entries above come from the live database. Do NOT plan around any agent's blocks that do not appear in this list — if an agent is not listed, their entries have been cancelled or do not exist.
- Do NOT overlap any higher-priority block. Move your block to a different time instead.
- If there is no free slot for a block, drop it entirely rather than overlap.
- Keep all blocks within work hours.

Revise your proposal to be fully conflict-free. If no conflicts exist, repeat it unchanged.
Respond in exactly this format:
PROPOSAL
- HH:MM–HH:MM | <title> | <one-sentence rationale>
END"""

        revised = self._ask_claude(prompt, max_tokens=500)
        self.post_chat(
            engineer_id,
            f"[{self._agent.name} — Revised Proposal for {for_date:%b %d}]\n{revised}",
        )

    def commit(self, engineer_id, for_date):
        history = self.get_chat_history(engineer_id)
        work_start, work_end = self.get_work_hours(engineer_id, for_date)
        document = self.read_document()

        own = next(
            (m.body for m in reversed(history)
             if m.sender_id == self._agent.ops_user_id and f'[{self._agent.name}' in m.body),
            None,
        )
        if not own:
            self.write_status(Agent.COMMITTED)
            return

        # Extract only the PROPOSAL...END block to avoid picking up context text that
        # mentions other agents' blocks (which would create tickets for them by accident).
        proposal_match = re.search(r'PROPOSAL\s*\n(.*?)\nEND\b', own, re.DOTALL | re.IGNORECASE)
        proposal_body = proposal_match.group(1).strip() if proposal_match else own

        extract_prompt = f"""Extract scheduled activities from this agent proposal and return a JSON array.
Each object must have exactly these keys: "title" (string), "start" (HH:MM), "end" (HH:MM), "description" (string — 1-2 sentences explaining what this block is for and why).
Return only valid JSON — no markdown, no explanation.

PROPOSAL:
{proposal_body}

Example: [{{"title": "Morning stretch", "start": "09:00", "end": "09:15", "description": "Light mobility work to start the day and reduce back tension before desk work."}}]"""

        raw = self._ask_claude(extract_prompt, max_tokens=500)

        committed = []
        try:
            blocks = json.loads(raw)
            for block in blocks:
                s = datetime.strptime(block['start'], '%H:%M').time()
                e = datetime.strptime(block['end'], '%H:%M').time()
                if work_start and work_end:
                    if s < work_start or e > work_end:
                        continue
                start_dt = datetime.combine(for_date, s)
                end_dt = datetime.combine(for_date, e)
                description = block.get('description') or f"Scheduled by {self._agent.name} on {for_date:%Y-%m-%d}."
                ticket_id = self.create_ticket(
                    title=block['title'],
                    description=description,
                )
                self.schedule_ticket(ticket_id, engineer_id, start_dt, end_dt)
                committed.append(block)
        except (json.JSONDecodeError, KeyError, ValueError):
            pass

        history_text = '\n'.join(
            f"{m.sent_at:%Y-%m-%d %H:%M}  {m.sender.username}: {m.body}"
            for m in history[-10:]
        )
        update_prompt = f"""{self._agent.system_prompt}

Update your running notes on this engineer.

CURRENT NOTES:
{document or '(none)'}

TODAY ({for_date:%Y-%m-%d}) YOU COMMITTED:
{json.dumps(committed, indent=2) if committed else '(nothing scheduled this run)'}

RECENT CHAT (last 10 messages):
{history_text}

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
