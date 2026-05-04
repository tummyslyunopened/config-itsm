import logging
import re
from datetime import date, timedelta

from django.contrib.auth.models import User

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


def _run_cycle(engineer_id, for_date, agents):
    """Run propose → resolve → commit for a list of GenericAgent instances on one date."""
    # RESET — scoped to for_date so multi-day loops don't wipe each other
    for agent in agents:
        try:
            agent.clear_future_entries(engineer_id, for_date=for_date)
            agent.write_status(Agent.DELIBERATING)
        except Exception:
            logger.exception(f'Reset failed for agent / engineer {engineer_id} / {for_date}')

    # PROPOSAL PASS
    for agent in agents:
        try:
            agent.propose(engineer_id, for_date)
        except Exception:
            logger.exception(f'Proposal failed for agent / engineer {engineer_id} / {for_date}')

    # RESOLUTION PASS — lowest priority yields first
    for agent in reversed(agents):
        try:
            agent.resolve(engineer_id, for_date)
        except Exception:
            logger.exception(f'Resolution failed for agent / engineer {engineer_id} / {for_date}')

    # COMMIT PASS — highest priority commits first
    for agent in agents:
        try:
            agent.commit(engineer_id, for_date)
        except Exception:
            logger.exception(f'Commit failed for agent / engineer {engineer_id} / {for_date}')

    # CONFLICT CLEANUP — delete lower-priority entries that overlap higher-priority ones
    for agent in reversed(agents):
        try:
            agent.delete_conflicting_own_entries(engineer_id, for_date)
        except Exception:
            logger.exception(f'Conflict cleanup failed for agent / engineer {engineer_id} / {for_date}')


def _build_agents_for_engineer(engineer_id):
    """Return [GenericAgent, ...] ordered by priority (lowest number = highest priority)."""
    records = list(Agent.objects.filter(engineer_id=engineer_id).order_by('priority'))
    result = []
    for record in records:
        try:
            result.append(GenericAgent(record))
        except Exception:
            logger.exception(f'Failed to initialise agent {record.id} for engineer {engineer_id}')
    return result


def standup(engineer_id, for_date=None):
    if for_date is None:
        for_date = date.today()

    agents = _build_agents_for_engineer(engineer_id)
    if not agents:
        return

    _run_cycle(engineer_id, for_date, agents)
    logger.info(f'Standup complete: engineer {engineer_id} / {for_date}')


def on_message(engineer_id, message):
    """Triggered when an engineer posts a new chat message."""
    for_dates = _parse_target_dates(message)
    all_agents = _build_agents_for_engineer(engineer_id)
    broadcast = _all_agents_addressed(message)

    relevant = []
    for agent in all_agents:
        if broadcast:
            relevant.append(agent)
            agent.write_status(Agent.DELIBERATING)
        else:
            try:
                if agent.is_relevant(message):
                    relevant.append(agent)
                else:
                    agent.write_status(Agent.COMMITTED)
            except Exception:
                logger.exception(f'Self-eval failed for agent / engineer {engineer_id}')

    if not relevant:
        return

    # Split relevant agents into those being asked to remove vs. those being asked to schedule
    to_schedule = []
    for agent in relevant:
        try:
            if agent.is_removal_directive(message):
                for for_date in for_dates:
                    agent.cancel_entries(engineer_id, for_date)
            else:
                to_schedule.append(agent)
        except Exception:
            logger.exception(f'Removal check failed for agent / engineer {engineer_id}')
            to_schedule.append(agent)

    for for_date in for_dates:
        if to_schedule:
            _run_cycle(engineer_id, for_date, to_schedule)


def run_morning():
    """Fire standup() for every engineer who has working hours today. Called by cron at 04:00."""
    today = date.today()
    engineers = User.objects.filter(profile__role='engineer')
    for eng in engineers:
        ws = getattr(eng, 'work_schedule', None)
        if ws is None:
            continue
        work_start, _ = ws.hours_for_date(today)
        if work_start is None:
            continue
        try:
            standup(eng.id, today)
        except Exception:
            logger.exception(f'Standup failed for engineer {eng.id}')
