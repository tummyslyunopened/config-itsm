import logging
import re
from datetime import date, timedelta

from tickets.models import Agent
from .base import GenericAgent

logger = logging.getLogger(__name__)

_WEEKDAYS = ['monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday']

# Words that deterministically anchor the message to today, regardless of
# whether the engineer also names the current weekday ("rest of my Sunday"
# while it IS Sunday should still be today, not next Sunday).
_TODAY_KEYWORDS = re.compile(
    r'\b(today|tonight|this\s+(?:morning|afternoon|evening|night))\b'
    r'|\brest\s+of\s+(?:the|my|today|this)\b'
    r'|\blater\s+today\b',
    re.IGNORECASE,
)

# Removal / cancellation phrasing that should always trigger the scheduler's
# clear path without consulting Claude. Kept narrow on purpose so harmless
# uses ("delete this typo") in non-scheduling contexts don't trigger it on
# their own — the scheduler still has the LLM check as a final filter for
# everything else.
_REMOVAL_KEYWORDS = re.compile(
    r'\b(clear|cancel|wipe|drop)\s+(?:the\s+|my\s+|all\s+|out\s+|the\s+rest\s+of\s+)?'
    r'(schedule|calendar|today|tonight|tomorrow|sunday|monday|tuesday|wednesday|thursday|friday|saturday|week|day|rest)',
    re.IGNORECASE,
)


def _parse_single_date(message):
    """Return the single target date from a message, defaulting to today."""
    msg = message.lower()
    today = date.today()

    # Explicit "today" wording wins over a weekday match — "rest of my Sunday"
    # on a Sunday must stay today, not jump a week ahead.
    if _TODAY_KEYWORDS.search(msg):
        return today

    if 'tomorrow' in msg:
        return today + timedelta(days=1)

    has_next = bool(re.search(r'\bnext\b', msg))
    for i, day_name in enumerate(_WEEKDAYS):
        if re.search(rf'\b{day_name}\b', msg):
            days_ahead = i - today.weekday()
            if has_next:
                # "next <weekday>" always lands in the following week.
                days_ahead += 7
            elif days_ahead < 0:
                # Bare weekday: today if it's that day, else nearest upcoming.
                days_ahead += 7
            return today + timedelta(days=days_ahead)

    return today


def _parse_target_dates(message):
    """Return a list of dates to schedule. Handles week-range phrases."""
    msg = message.lower()
    today = date.today()

    if re.search(r'\bnext\s+week\b', msg):
        monday = today - timedelta(days=today.weekday()) + timedelta(weeks=1)
        return [monday + timedelta(days=i) for i in range(5)]

    # Tightened from the previous "(whole|all|this|the)\s+week" — bare "the
    # week" overmatches casual phrases like "finish the week strong" and used
    # to filter to an empty list when today is past Friday, silently dropping
    # the whole cycle.
    if re.search(
        r'\b(whole|all|this|rest\s+of\s+(?:the|my|this))\s+week\b'
        r'|\beach\s+day\b|\bevery\s+day\b|\ball\s+week\b',
        msg,
    ):
        monday = today - timedelta(days=today.weekday())
        days = [monday + timedelta(days=i) for i in range(5)
                if (monday + timedelta(days=i)) >= today]
        # If the engineer asked about the week but every weekday is already
        # in the past, fall back to today so the scheduler still runs.
        return days or [today]

    return [_parse_single_date(message)]


def _all_agents_addressed(message):
    """True when the message explicitly directs all agents (skip per-agent relevance check)."""
    msg = message.lower()
    return bool(re.search(r'\ball\s+agents?\b|\bevery\s+agent\b|\ball\s+of\s+you\b', msg))


def _has_removal_keywords(message):
    """Cheap deterministic check: does the message look like a removal directive?

    Used to short-circuit the LLM removal-check when the wording is
    unambiguous (e.g. "clear my schedule for sunday") so that the scheduler
    always reacts to the standard cancel verbs even when the API is slow,
    rate-limited, or unavailable."""
    return bool(_REMOVAL_KEYWORDS.search(message))


def _build_agents_for_engineer(engineer_id):
    """Return (scheduler, [suggesters]) for an engineer.

    The highest-priority (lowest priority number) agent is automatically the
    scheduler. All others are suggesters, returned in priority order.
    Scheduler is None when the engineer has no agents."""
    records = list(Agent.objects.filter(engineer_id=engineer_id).order_by('priority'))
    scheduler = None
    suggesters = []
    for record in records:
        try:
            agent = GenericAgent(record)
        except Exception:
            logger.exception(f'Failed to initialise agent {record.id} for engineer {engineer_id}')
            continue
        if scheduler is None:
            scheduler = agent
        else:
            suggesters.append(agent)
    return scheduler, suggesters


def _run_cycle(engineer_id, for_date, scheduler, suggesters):
    """Run suggestion → scheduling for one date.

    Suggesters post proposals to chat; the scheduler reads them and writes
    ScheduleEntry records."""
    # RESET — only the scheduler clears its own non-locked future entries.
    try:
        scheduler.clear_future_entries(engineer_id, for_date=for_date)
    except Exception:
        logger.exception(f'Reset failed for scheduler / engineer {engineer_id} / {for_date}')

    # SUGGESTION PASS — every suggester posts to chat (priority order).
    for agent in suggesters:
        try:
            agent.write_status(Agent.DELIBERATING)
            agent.suggest(engineer_id, for_date)
        except Exception:
            logger.exception(f'Suggestion failed for engineer {engineer_id} / {for_date}')
            try:
                agent.write_status(Agent.COMMITTED)
            except Exception:
                pass

    # SCHEDULE PASS — scheduler reads suggestions, writes DB entries.
    try:
        scheduler.write_status(Agent.DELIBERATING)
        scheduler.schedule(engineer_id, for_date)
    except Exception:
        logger.exception(f'Schedule pass failed for engineer {engineer_id} / {for_date}')
        try:
            scheduler.write_status(Agent.COMMITTED)
        except Exception:
            pass


def on_message(engineer_id, message):
    """Triggered when an engineer posts a new chat message."""
    for_dates = _parse_target_dates(message)
    scheduler, suggesters = _build_agents_for_engineer(engineer_id)

    if scheduler is None:
        return

    broadcast = _all_agents_addressed(message)

    # Relevance filter (suggesters only — scheduler always engages when there
    # is a scheduling-related message, since it is the one writing the calendar).
    relevant_suggesters = []
    for agent in suggesters:
        if broadcast:
            relevant_suggesters.append(agent)
            try:
                agent.write_status(Agent.DELIBERATING)
            except Exception:
                logger.exception(f'Failed to mark suggester deliberating / engineer {engineer_id}')
        else:
            try:
                if agent.is_relevant(message):
                    relevant_suggesters.append(agent)
                else:
                    agent.write_status(Agent.COMMITTED)
            except Exception:
                logger.exception(f'Self-eval failed for agent / engineer {engineer_id}')
                # Failure must not leave the suggester deliberating forever;
                # drop it from this cycle and reset its status.
                try:
                    agent.write_status(Agent.COMMITTED)
                except Exception:
                    pass

    # Removal directive — only the scheduler can clear entries. Use a
    # deterministic keyword pre-check first so unambiguous cancellations
    # always trigger the clear path even if the LLM call fails or returns
    # NO. The LLM is consulted only for ambiguous wording.
    try:
        if _has_removal_keywords(message) or scheduler.is_removal_directive(message):
            for for_date in for_dates:
                scheduler.cancel_entries(engineer_id, for_date)
            # Suggesters have nothing to remove; mark them committed.
            for agent in relevant_suggesters:
                try:
                    agent.write_status(Agent.COMMITTED)
                except Exception:
                    pass
            return
    except Exception:
        logger.exception(f'Removal check failed for engineer {engineer_id}')

    for for_date in for_dates:
        _run_cycle(engineer_id, for_date, scheduler, relevant_suggesters)
