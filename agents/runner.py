import logging
import re
from datetime import date, timedelta

from tickets.models import Agent
from .base import GenericAgent

logger = logging.getLogger(__name__)

_WEEKDAYS = ['monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday']


def _parse_single_date(message):
    """Return the single target date from a message, defaulting to today."""
    msg = message.lower()
    today = date.today()

    if 'tomorrow' in msg:
        return today + timedelta(days=1)

    for i, day_name in enumerate(_WEEKDAYS):
        if day_name in msg:
            days_ahead = i - today.weekday()
            if days_ahead <= 0 or 'next' in msg:
                days_ahead += 7
            return today + timedelta(days=days_ahead)

    return today


def _parse_target_dates(message):
    """Return a list of dates to schedule. Handles week-range phrases."""
    msg = message.lower()
    today = date.today()

    if re.search(r'\bnext week\b', msg):
        monday = today - timedelta(days=today.weekday()) + timedelta(weeks=1)
        return [monday + timedelta(days=i) for i in range(5)]

    if re.search(r'\b(whole|all|this|the)\s+week\b|\beach day\b|\bevery day\b', msg):
        monday = today - timedelta(days=today.weekday())
        return [monday + timedelta(days=i) for i in range(5) if (monday + timedelta(days=i)) >= today]

    return [_parse_single_date(message)]


def _all_agents_addressed(message):
    """True when the message explicitly directs all agents (skip per-agent relevance check)."""
    msg = message.lower()
    return bool(re.search(r'\ball agents?\b|\bevery agent\b|\ball of you\b', msg))


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
            agent.write_status(Agent.DELIBERATING)
        else:
            try:
                if agent.is_relevant(message):
                    relevant_suggesters.append(agent)
                else:
                    agent.write_status(Agent.COMMITTED)
            except Exception:
                logger.exception(f'Self-eval failed for agent / engineer {engineer_id}')

    # Removal directive — only the scheduler can clear entries.
    try:
        if scheduler.is_removal_directive(message):
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
