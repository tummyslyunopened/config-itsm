import json
from datetime import datetime, timedelta
from functools import wraps

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.contrib.auth.models import User
from django.utils.dateparse import parse_date
from django.utils import timezone

from .models import Agent, ApiKey, Ticket, ScheduleEntry, TimeEntry, ChatMessage
from .forms import _parse_time


def _api_auth(view_func):
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        auth = request.headers.get('Authorization', '')
        if not auth.startswith('Api-Key '):
            return JsonResponse({'error': 'unauthorized'}, status=401)
        raw_key = auth[len('Api-Key '):]
        try:
            api_key = ApiKey.objects.select_related('user__profile').get(key=raw_key)
        except ApiKey.DoesNotExist:
            return JsonResponse({'error': 'unauthorized'}, status=401)
        if api_key.user.profile.role != 'ops':
            return JsonResponse({'error': 'forbidden'}, status=403)
        request.api_user = api_key.user
        return view_func(request, *args, **kwargs)
    return wrapper


def _ticket_dict(ticket):
    return {
        'id': ticket.id,
        'title': ticket.title,
        'description': ticket.description,
        'status': ticket.status,
        'created_at': ticket.created_at.strftime('%Y-%m-%dT%H:%M:%S'),
    }


def _schedule_entry_dict(entry):
    return {
        'id': entry.id,
        'ticket_id': entry.ticket_id,
        'engineer_id': entry.engineer_id,
        'engineer': entry.engineer.username,
        'start': entry.start.strftime('%Y-%m-%dT%H:%M'),
        'end': entry.end.strftime('%Y-%m-%dT%H:%M'),
        'created_by': entry.created_by_id,
        'locked': entry.locked,
    }


def _time_entry_dict(entry):
    return {
        'id': entry.id,
        'ticket_id': entry.ticket_id,
        'engineer_id': entry.engineer_id,
        'engineer': entry.engineer.username,
        'notes': entry.notes,
        'start': entry.start.strftime('%Y-%m-%dT%H:%M'),
        'end': entry.end.strftime('%Y-%m-%dT%H:%M'),
    }


def _work_schedule_dict(ws):
    days = ['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun']
    result = {}
    for day in days:
        start = getattr(ws, f'{day}_start')
        end = getattr(ws, f'{day}_end')
        result[day] = (
            {'start': start.strftime('%H:%M'), 'end': end.strftime('%H:%M')}
            if start and end else None
        )
    return result


def _chat_message_dict(msg):
    return {
        'id': msg.id,
        'engineer_id': msg.engineer_id,
        'sender_id': msg.sender_id,
        'sender': msg.sender.username,
        'body': msg.body,
        'sent_at': msg.sent_at.strftime('%Y-%m-%dT%H:%M:%S'),
    }


def _parse_request_time(s):
    if not s:
        return None
    try:
        return _parse_time(s)
    except (ValueError, TypeError):
        return None


@csrf_exempt
@_api_auth
def ticket_list(request):
    if request.method == 'GET':
        tickets = Ticket.objects.exclude(status=Ticket.COMPLETE).order_by('-created_at')
        return JsonResponse([_ticket_dict(t) for t in tickets], safe=False)

    if request.method == 'POST':
        try:
            data = json.loads(request.body)
        except (json.JSONDecodeError, ValueError):
            return JsonResponse({'error': 'invalid JSON'}, status=400)
        title = data.get('title', '').strip()
        description = data.get('description', '').strip()
        if not title:
            return JsonResponse({'error': 'title is required'}, status=400)
        if not description:
            return JsonResponse({'error': 'description is required'}, status=400)
        ticket = Ticket.objects.create(title=title, description=description)
        return JsonResponse(_ticket_dict(ticket), status=201)

    return JsonResponse({'error': 'method not allowed'}, status=405)


@csrf_exempt
@_api_auth
def ticket_detail(request, pk):
    try:
        ticket = Ticket.objects.get(pk=pk)
    except Ticket.DoesNotExist:
        return JsonResponse({'error': 'not found'}, status=404)

    if request.method == 'GET':
        data = _ticket_dict(ticket)
        data['schedule_entries'] = [
            _schedule_entry_dict(e)
            for e in ticket.schedule_entries.select_related('engineer').all()
        ]
        data['time_entries'] = [
            _time_entry_dict(e)
            for e in ticket.time_entries.select_related('engineer').all()
        ]
        return JsonResponse(data)

    return JsonResponse({'error': 'method not allowed'}, status=405)


@csrf_exempt
@_api_auth
def ticket_status(request, pk):
    if request.method != 'POST':
        return JsonResponse({'error': 'method not allowed'}, status=405)
    try:
        ticket = Ticket.objects.get(pk=pk)
    except Ticket.DoesNotExist:
        return JsonResponse({'error': 'not found'}, status=404)
    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({'error': 'invalid JSON'}, status=400)
    status = data.get('status')
    valid = [s for s, _ in Ticket.STATUS_CHOICES]
    if status not in valid:
        return JsonResponse(
            {'error': f'invalid status; choices: {", ".join(valid)}'},
            status=400,
        )
    ticket.status = status
    ticket.save(update_fields=['status'])
    return JsonResponse(_ticket_dict(ticket))


@csrf_exempt
@_api_auth
def engineer_list(request):
    if request.method != 'GET':
        return JsonResponse({'error': 'method not allowed'}, status=405)
    engineers = User.objects.filter(profile__role='engineer').order_by('username')
    result = []
    for eng in engineers:
        ws = getattr(eng, 'work_schedule', None)
        result.append({
            'id': eng.id,
            'username': eng.username,
            'work_schedule': _work_schedule_dict(ws) if ws else None,
        })
    return JsonResponse(result, safe=False)


@csrf_exempt
@_api_auth
def engineer_schedule(request, pk):
    if request.method != 'GET':
        return JsonResponse({'error': 'method not allowed'}, status=405)
    try:
        engineer = User.objects.get(pk=pk, profile__role='engineer')
    except User.DoesNotExist:
        return JsonResponse({'error': 'not found'}, status=404)

    date_param = request.GET.get('date')
    week_param = request.GET.get('week')

    if date_param:
        d = parse_date(date_param)
        if not d:
            return JsonResponse({'error': 'invalid date; use YYYY-MM-DD'}, status=400)
        start_bound = datetime.combine(d, datetime.min.time())
        end_bound = datetime.combine(d, datetime.max.time())
    elif week_param:
        d = parse_date(week_param)
        if not d:
            return JsonResponse({'error': 'invalid date; use YYYY-MM-DD'}, status=400)
        monday = d - timedelta(days=d.weekday())
        start_bound = datetime.combine(monday, datetime.min.time())
        end_bound = datetime.combine(monday + timedelta(days=6), datetime.max.time())
    else:
        return JsonResponse(
            {'error': 'provide ?date=YYYY-MM-DD or ?week=YYYY-MM-DD'},
            status=400,
        )

    entries = (
        ScheduleEntry.objects
        .filter(engineer=engineer, start__gte=start_bound, start__lte=end_bound)
        .select_related('engineer')
        .order_by('start')
    )
    return JsonResponse([_schedule_entry_dict(e) for e in entries], safe=False)


@csrf_exempt
@_api_auth
def ticket_schedule(request, pk):
    if request.method != 'POST':
        return JsonResponse({'error': 'method not allowed'}, status=405)
    try:
        ticket = Ticket.objects.get(pk=pk)
    except Ticket.DoesNotExist:
        return JsonResponse({'error': 'not found'}, status=404)
    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({'error': 'invalid JSON'}, status=400)

    engineer_id = data.get('engineer_id')
    date_str = data.get('date')
    start_str = data.get('start')
    end_str = data.get('end')

    if not all([engineer_id, date_str, start_str, end_str]):
        return JsonResponse(
            {'error': 'engineer_id, date, start, and end are required'},
            status=400,
        )

    try:
        engineer = User.objects.get(pk=engineer_id, profile__role='engineer')
    except User.DoesNotExist:
        return JsonResponse({'error': 'engineer not found'}, status=404)

    d = parse_date(date_str)
    if not d:
        return JsonResponse({'error': 'invalid date; use YYYY-MM-DD'}, status=400)

    start_time = _parse_request_time(start_str)
    end_time = _parse_request_time(end_str)
    if not start_time:
        return JsonResponse({'error': 'invalid start time; use HH:MM'}, status=400)
    if not end_time:
        return JsonResponse({'error': 'invalid end time; use HH:MM'}, status=400)

    start_dt = datetime.combine(d, start_time)
    end_dt = datetime.combine(d, end_time)
    if end_dt <= start_dt:
        return JsonResponse({'error': 'end must be after start'}, status=400)

    ws = getattr(engineer, 'work_schedule', None)
    if ws:
        work_start, work_end = ws.hours_for_date(d)
        if work_start is None:
            return JsonResponse(
                {'error': f'engineer does not work on {d.strftime("%A")}'},
                status=400,
            )
        if start_time < work_start or end_time > work_end:
            return JsonResponse(
                {'error': (
                    f'entry falls outside engineer work hours '
                    f'({work_start.strftime("%H:%M")}–{work_end.strftime("%H:%M")})'
                )},
                status=400,
            )

    # Derive agent from API key's user; null if no agent record (plain ops user)
    try:
        agent = Agent.objects.get(ops_user=request.api_user)
    except Agent.DoesNotExist:
        agent = None

    entry = ScheduleEntry.objects.create(
        ticket=ticket, engineer=engineer, start=start_dt, end=end_dt,
        created_by=agent, locked=(agent is None),
    )

    if ticket.status != Ticket.SCHEDULED:
        ticket.status = Ticket.SCHEDULED
        ticket.save(update_fields=['status'])

    return JsonResponse(_schedule_entry_dict(entry), status=201)


@csrf_exempt
@_api_auth
def engineer_chat(request, pk):
    try:
        engineer = User.objects.get(pk=pk, profile__role='engineer')
    except User.DoesNotExist:
        return JsonResponse({'error': 'not found'}, status=404)

    if request.method == 'GET':
        msgs = ChatMessage.objects.filter(engineer=engineer).select_related('sender')
        return JsonResponse([_chat_message_dict(m) for m in msgs], safe=False)

    if request.method == 'POST':
        try:
            data = json.loads(request.body)
        except (json.JSONDecodeError, ValueError):
            return JsonResponse({'error': 'invalid JSON'}, status=400)
        body = data.get('body', '').strip()
        if not body:
            return JsonResponse({'error': 'body is required'}, status=400)
        msg = ChatMessage.objects.create(
            engineer=engineer, sender=request.api_user, body=body,
        )
        return JsonResponse(_chat_message_dict(msg), status=201)

    return JsonResponse({'error': 'method not allowed'}, status=405)


@csrf_exempt
@_api_auth
def agent_state(request, agent_id):
    try:
        agent = Agent.objects.get(pk=agent_id, ops_user=request.api_user)
    except Agent.DoesNotExist:
        return JsonResponse({'error': 'agent not found'}, status=404)

    if request.method == 'GET':
        return JsonResponse({
            'id': agent.id,
            'name': agent.name,
            'status': agent.status,
            'document': agent.document,
        })

    if request.method == 'PUT':
        try:
            data = json.loads(request.body)
        except (json.JSONDecodeError, ValueError):
            return JsonResponse({'error': 'invalid JSON'}, status=400)

        valid_statuses = [Agent.DELIBERATING, Agent.COMMITTED]
        if 'status' in data:
            if data['status'] not in valid_statuses:
                return JsonResponse(
                    {'error': f'status must be one of: {", ".join(valid_statuses)}'},
                    status=400,
                )
            agent.status = data['status']
        if 'document' in data:
            agent.document = data['document']
        agent.save()
        return JsonResponse({
            'id': agent.id,
            'name': agent.name,
            'status': agent.status,
            'document': agent.document,
        })

    return JsonResponse({'error': 'method not allowed'}, status=405)


@csrf_exempt
@_api_auth
def agent_clear_future_schedule(request, agent_id):
    if request.method != 'DELETE':
        return JsonResponse({'error': 'method not allowed'}, status=405)
    try:
        agent = Agent.objects.get(pk=agent_id, ops_user=request.api_user)
    except Agent.DoesNotExist:
        return JsonResponse({'error': 'agent not found'}, status=404)

    now = timezone.now()
    deleted, _ = ScheduleEntry.objects.filter(
        engineer=agent.engineer,
        created_by=agent,
        locked=False,
        start__gt=now,
    ).delete()
    return JsonResponse({'deleted': deleted})
