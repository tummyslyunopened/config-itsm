import anthropic
import threading
from datetime import date, timedelta
from django.contrib.auth.models import User
from django.db import connection
from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.decorators import login_required
from django.http import HttpResponseForbidden, JsonResponse
from .models import Agent, AgentMessage, ApiKey, Ticket, ScheduleEntry, TimeEntry, WorkSchedule, ChatMessage, DAY_KEYS, DAY_LABELS, UserProfile


def _trigger_agents(engineer_id, message):
    """Fire on_message in a background thread so the chat POST returns immediately."""
    def _run():
        try:
            from agents.runner import on_message
            on_message(engineer_id, message)
        except Exception:
            pass
        finally:
            connection.close()
    threading.Thread(target=_run, daemon=True).start()
from .forms import (
    TicketCreateForm, TicketStatusForm, ScheduleEntryForm,
    TimeEntryForm, WorkScheduleForm,
)

# Calendar grid constants
GRID_START = 7
GRID_END   = 20
PX_PER_HOUR = 64
GRID_HEIGHT = (GRID_END - GRID_START) * PX_PER_HOUR  # 832 px

HOUR_MARKERS = [
    {'hour': h, 'top': (h - GRID_START) * PX_PER_HOUR}
    for h in range(GRID_START, GRID_END + 1)
]
DROP_ZONES = [
    {'hour': h, 'top': (h - GRID_START) * PX_PER_HOUR}
    for h in range(GRID_START, GRID_END)
]


def _event_pos(start_dt, end_dt):
    start_m = (start_dt.hour - GRID_START) * 60 + start_dt.minute
    end_m   = (end_dt.hour   - GRID_START) * 60 + end_dt.minute
    top    = max(0, round(start_m * PX_PER_HOUR / 60))
    bottom = min(GRID_HEIGHT, round(end_m * PX_PER_HOUR / 60))
    return top, max(bottom - top, 22)


def _gray_zones(ws_start, ws_end):
    """Non-work-hour bands for a single day."""
    if ws_start is None:
        return [{'top': 0, 'height': GRID_HEIGHT}]
    zones = []
    pre_px = round(((ws_start.hour - GRID_START) * 60 + ws_start.minute) * PX_PER_HOUR / 60)
    if pre_px > 0:
        zones.append({'top': 0, 'height': pre_px})
    post_px = round(((ws_end.hour - GRID_START) * 60 + ws_end.minute) * PX_PER_HOUR / 60)
    if post_px < GRID_HEIGHT:
        zones.append({'top': post_px, 'height': GRID_HEIGHT - post_px})
    return zones


def _layout_events(events):
    """Assign left_pct/width_pct so overlapping events display side-by-side."""
    if not events:
        return events

    n = len(events)

    # Greedy column assignment
    ordered = sorted(range(n), key=lambda i: (events[i]['top'], -events[i]['height']))
    col_bottoms = []
    event_col = [0] * n
    for idx in ordered:
        ev = events[idx]
        top, bottom = ev['top'], ev['top'] + ev['height']
        placed = False
        for ci, cb in enumerate(col_bottoms):
            if top >= cb:
                col_bottoms[ci] = bottom
                event_col[idx] = ci
                placed = True
                break
        if not placed:
            event_col[idx] = len(col_bottoms)
            col_bottoms.append(bottom)

    # Union-find: group events that transitively overlap
    parent = list(range(n))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for i in range(n):
        ti, bi = events[i]['top'], events[i]['top'] + events[i]['height']
        for j in range(i + 1, n):
            tj, bj = events[j]['top'], events[j]['top'] + events[j]['height']
            if tj < bi and bj > ti:
                ri, rj = find(i), find(j)
                if ri != rj:
                    parent[ri] = rj

    comp_max = {}
    for i in range(n):
        r = find(i)
        comp_max[r] = max(comp_max.get(r, 0), event_col[i])

    result = []
    for i, ev in enumerate(events):
        ncols = comp_max[find(i)] + 1
        ev = dict(ev)
        ev['left_pct'] = round(event_col[i] / ncols * 100, 3)
        ev['width_pct'] = round(100 / ncols, 3)
        result.append(ev)
    return result


def _build_day_cols(week_days, sched_by_day, time_by_day=None, get_work_hours=None):
    cols = []
    for day in week_days:
        ws_start, ws_end = get_work_hours(day) if get_work_hours else (None, None)
        events = []
        for e in sched_by_day.get(day, []):
            top, height = _event_pos(e.start, e.end)
            events.append({'kind': 'schedule', 'entry': e, 'top': top, 'height': height})
        if time_by_day:
            for e in time_by_day.get(day, []):
                top, height = _event_pos(e.start, e.end)
                events.append({'kind': 'time', 'entry': e, 'top': top, 'height': height})
        events.sort(key=lambda x: x['top'])
        events = _layout_events(events)
        cols.append({
            'date': day,
            'events': events,
            'gray_zones': _gray_zones(ws_start, ws_end),
        })
    return cols


def _scroll_top(week_days, get_work_hours):
    """Scroll to 30 min before earliest work start across the displayed week."""
    earliest = None
    for day in week_days:
        ws_start, _ = get_work_hours(day)
        if ws_start and (earliest is None or ws_start < earliest):
            earliest = ws_start
    if earliest is None:
        return 0
    mins = max(0, (earliest.hour - GRID_START) * 60 + earliest.minute - 30)
    return round(mins * PX_PER_HOUR / 60)


def _make_get_work_hours(ws):
    """Return a callable(date) → (start, end) using a WorkSchedule or None."""
    if ws is None:
        return lambda d: (None, None)
    return lambda d: ws.hours_for_date(d)


def role_of(request):
    return getattr(request.user, 'profile', None) and request.user.profile.role


def role(request):
    return role_of(request)


def require_role(r):
    def decorator(view_fn):
        @login_required
        def wrapped(request, *args, **kwargs):
            if role(request) != r:
                return HttpResponseForbidden()
            return view_fn(request, *args, **kwargs)
        return wrapped
    return decorator


@login_required
def home(request):
    r = role_of(request)
    if r == 'ops':
        return redirect('queue')
    if r == 'engineer':
        return redirect('calendar')
    return redirect('ticket_list')


@login_required
def ticket_list(request):
    tickets = Ticket.objects.order_by('-created_at')
    return render(request, 'tickets/ticket_list.html', {'tickets': tickets})


@login_required
def ticket_create(request):
    next_url = request.POST.get('next') or request.GET.get('next') or 'ticket_list'
    form = TicketCreateForm(request.POST or None)
    if form.is_valid():
        form.save()
        return redirect(next_url)
    return render(request, 'tickets/ticket_create.html', {'form': form, 'next_url': next_url})


@login_required
def ticket_detail(request, pk):
    ticket = get_object_or_404(Ticket, pk=pk)
    user_role = role(request)
    status_form = None
    time_form = None

    suggested_date = date.today()
    next_sched = ticket.schedule_entries.filter(start__date__gte=date.today()).first()
    if next_sched:
        suggested_date = next_sched.start.date()

    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'status':
            status_form = TicketStatusForm(request.POST, instance=ticket)
            if status_form.is_valid():
                status_form.save()
                return redirect('ticket_detail', pk=pk)
        elif action == 'time' and user_role == 'engineer':
            time_form = TimeEntryForm(request.POST)
            if time_form.is_valid():
                TimeEntry.objects.create(
                    ticket=ticket,
                    engineer=request.user,
                    notes=time_form.cleaned_data['notes'],
                    start=time_form.get_start(),
                    end=time_form.get_end(),
                )
                return redirect('ticket_detail', pk=pk)
        elif action == 'delete_time' and user_role == 'engineer':
            entry_pk = request.POST.get('entry_pk')
            TimeEntry.objects.filter(pk=entry_pk, engineer=request.user, ticket=ticket).delete()
            return redirect('ticket_detail', pk=pk)

    if status_form is None:
        status_form = TicketStatusForm(instance=ticket)
    if time_form is None and user_role == 'engineer':
        time_form = TimeEntryForm(initial={'date': suggested_date.isoformat()})

    return render(request, 'tickets/ticket_detail.html', {
        'ticket': ticket,
        'status_form': status_form,
        'time_form': time_form,
        'user_role': user_role,
    })


@require_role('ops')
def schedule_entry_add(request, pk):
    ticket = get_object_or_404(Ticket, pk=pk)

    initial = {}
    start_param = request.GET.get('start', '')
    end_param   = request.GET.get('end', '')
    if start_param:
        initial['date']       = start_param[:10]
        initial['start_time'] = start_param[11:16].replace(':', '') if len(start_param) > 10 else ''
    else:
        initial['date'] = date.today().isoformat()
    if end_param:
        initial['end_time'] = end_param[11:16].replace(':', '') if len(end_param) > 10 else ''
    eng_id = request.GET.get('engineer')
    if eng_id:
        initial['engineer'] = eng_id

    schedule_error = None
    form = ScheduleEntryForm(request.POST or None, initial=initial)
    if form.is_valid():
        engineer = form.cleaned_data['engineer']
        entry_start = form.get_start()
        entry_end   = form.get_end()

        # Validate against engineer's work schedule
        try:
            ws = engineer.work_schedule
            ws_start, ws_end = ws.hours_for_date(form.cleaned_data['date'])
            if ws_start is None:
                schedule_error = (
                    f"{engineer.username} is not scheduled to work on "
                    f"{form.cleaned_data['date'].strftime('%A')}."
                )
            elif entry_start.time() < ws_start or entry_end.time() > ws_end:
                schedule_error = (
                    f"Outside {engineer.username}'s work hours "
                    f"({ws_start:%H:%M}–{ws_end:%H:%M} on {form.cleaned_data['date'].strftime('%A')})."
                )
        except WorkSchedule.DoesNotExist:
            pass  # no schedule set — allow any time

        if not schedule_error:
            ScheduleEntry.objects.create(
                ticket=ticket, engineer=engineer,
                start=entry_start, end=entry_end,
                created_by=None, locked=True,
            )
            if ticket.status != Ticket.SCHEDULED:
                ticket.status = Ticket.SCHEDULED
                ticket.save()
            return redirect('ticket_detail', pk=pk)

    return render(request, 'tickets/schedule_entry_add.html', {
        'form': form, 'ticket': ticket, 'schedule_error': schedule_error,
    })


def _week_monday(anchor):
    return anchor - timedelta(days=anchor.weekday())


@require_role('engineer')
def calendar_view(request):
    today = date.today()
    view_mode = request.GET.get('view', 'day')

    try:
        ws = request.user.work_schedule
    except WorkSchedule.DoesNotExist:
        ws = None
    get_work_hours = _make_get_work_hours(ws)

    if view_mode == 'agenda':
        schedule_entries = list(
            ScheduleEntry.objects
            .filter(engineer=request.user, start__date__gte=today)
            .select_related('ticket').order_by('start')[:60]
        )
        time_entries = list(
            TimeEntry.objects
            .filter(engineer=request.user, start__date__gte=today)
            .select_related('ticket').order_by('start')[:60]
        )
        agenda = sorted(
            [{'kind': 'schedule', 'entry': e} for e in schedule_entries] +
            [{'kind': 'time',     'entry': e} for e in time_entries],
            key=lambda x: x['entry'].start,
        )
        chat_msgs = (
            ChatMessage.objects
            .filter(engineer=request.user, hidden=False)
            .select_related('sender')
            .order_by('sent_at')
        )
        return render(request, 'tickets/calendar.html', {
            'view_mode': view_mode,
            'agenda_entries': agenda,
            'today': today,
            'chat_msgs': chat_msgs,
            'last_message_id': chat_msgs.values_list('id', flat=True).last() or 0,
            'scroll_to': 0,
            'grid_height': GRID_HEIGHT,
        })

    if view_mode == 'day':
        day_str = request.GET.get('day', today.isoformat())
        try:
            current_day = date.fromisoformat(day_str)
        except ValueError:
            current_day = today
        week_days = [current_day]
        prev_nav = f"?view=day&day={(current_day - timedelta(days=1)).isoformat()}"
        next_nav = f"?view=day&day={(current_day + timedelta(days=1)).isoformat()}"
        range_start = range_end = current_day
    else:
        week_str = request.GET.get('week', '')
        try:
            anchor = date.fromisoformat(week_str) if week_str else today
        except ValueError:
            anchor = today
        monday = _week_monday(anchor)
        week_days = [monday + timedelta(days=i) for i in range(7)]
        prev_nav = f"?view=week&week={(monday - timedelta(days=7)).isoformat()}"
        next_nav = f"?view=week&week={(monday + timedelta(days=7)).isoformat()}"
        range_start, range_end = week_days[0], week_days[-1]

    schedule_entries = list(
        ScheduleEntry.objects
        .filter(engineer=request.user, start__date__range=(range_start, range_end))
        .select_related('ticket').order_by('start')
    )
    time_entries = list(
        TimeEntry.objects
        .filter(engineer=request.user, start__date__range=(range_start, range_end))
        .select_related('ticket').order_by('start')
    )
    sched_by_day = {}
    for e in schedule_entries:
        sched_by_day.setdefault(e.start.date(), []).append(e)
    time_by_day = {}
    for e in time_entries:
        time_by_day.setdefault(e.start.date(), []).append(e)

    day_cols = _build_day_cols(week_days, sched_by_day, time_by_day, get_work_hours)

    # Fragment JSON — calendar polls this to refresh events without a page reload
    if request.GET.get('fragment') == '1':
        return JsonResponse({
            'cols': [
                {
                    'date': col['date'].isoformat(),
                    'events': [
                        {
                            'kind': ev['kind'],
                            'ticket_id': ev['entry'].ticket_id,
                            'title': ev['entry'].ticket.title,
                            'start': ev['entry'].start.strftime('%H:%M'),
                            'end': ev['entry'].end.strftime('%H:%M'),
                            'top': ev['top'],
                            'height': ev['height'],
                            'left_pct': ev['left_pct'],
                            'width_pct': ev['width_pct'],
                        }
                        for ev in col['events']
                    ],
                }
                for col in day_cols
            ]
        })

    chat_msgs = (
        ChatMessage.objects
        .filter(engineer=request.user, hidden=False)
        .select_related('sender')
        .order_by('sent_at')
    )
    last_message_id = chat_msgs.values_list('id', flat=True).last() or 0

    return render(request, 'tickets/calendar.html', {
        'view_mode': view_mode,
        'day_cols': day_cols,
        'week_days': week_days,
        'hour_markers': HOUR_MARKERS,
        'grid_height': GRID_HEIGHT,
        'scroll_to': _scroll_top(week_days, get_work_hours),
        'prev_nav': prev_nav,
        'next_nav': next_nav,
        'today': today,
        'chat_msgs': chat_msgs,
        'last_message_id': last_message_id,
    })


@require_role('ops')
def queue_view(request):
    tickets = Ticket.objects.exclude(
        status__in=[Ticket.COMPLETE, Ticket.SCHEDULED]
    ).order_by('-created_at')

    engineers = User.objects.filter(profile__role='engineer').order_by('username')
    today = date.today()

    week_str = request.GET.get('week', '')
    try:
        anchor = date.fromisoformat(week_str) if week_str else today
    except ValueError:
        anchor = today
    monday = _week_monday(anchor)
    week_days = [monday + timedelta(days=i) for i in range(7)]

    eng_id = request.GET.get('engineer')
    if not eng_id and engineers.exists():
        eng_id = str(engineers.first().pk)

    selected_engineer = None
    day_cols = []
    scroll_to = 0

    if eng_id:
        try:
            selected_engineer = engineers.get(pk=eng_id)
            try:
                ws = selected_engineer.work_schedule
            except WorkSchedule.DoesNotExist:
                ws = None
            get_work_hours = _make_get_work_hours(ws)

            schedule_entries = list(
                ScheduleEntry.objects
                .filter(
                    engineer=selected_engineer,
                    start__date__range=(week_days[0], week_days[-1]),
                )
                .select_related('ticket').order_by('start')
            )
            sched_by_day = {}
            for e in schedule_entries:
                sched_by_day.setdefault(e.start.date(), []).append(e)
            day_cols = _build_day_cols(week_days, sched_by_day, get_work_hours=get_work_hours)
            scroll_to = _scroll_top(week_days, get_work_hours)
        except User.DoesNotExist:
            pass

    base_qs = f"engineer={eng_id or ''}"
    prev_week = f"?{base_qs}&week={(monday - timedelta(days=7)).isoformat()}"
    next_week = f"?{base_qs}&week={(monday + timedelta(days=7)).isoformat()}"

    return render(request, 'tickets/queue.html', {
        'tickets': tickets,
        'engineers': engineers,
        'selected_engineer': selected_engineer,
        'day_cols': day_cols,
        'week_days': week_days,
        'hour_markers': HOUR_MARKERS,
        'drop_zones': DROP_ZONES,
        'grid_height': GRID_HEIGHT,
        'scroll_to': scroll_to,
        'monday': monday,
        'prev_week': prev_week,
        'next_week': next_week,
        'today': today,
        'eng_id': eng_id or '',
    })


@require_role('engineer')
def work_schedule_view(request):
    try:
        instance = request.user.work_schedule
    except WorkSchedule.DoesNotExist:
        instance = None

    form = WorkScheduleForm(request.POST or None, instance=instance)
    if form.is_valid():
        ws = form.save(commit=False)
        ws.engineer = request.user
        ws.save()
        return redirect('work_schedule')

    # Build day rows for the template
    day_rows = []
    for i, (key, label) in enumerate(zip(
        ['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun'],
        ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday'],
    )):
        day_rows.append({
            'label': label,
            'start_field': form[f'{key}_start'],
            'end_field': form[f'{key}_end'],
        })

    return render(request, 'tickets/work_schedule.html', {'form': form, 'day_rows': day_rows})


@login_required
def chat_view(request):
    from django.urls import reverse
    user_role = role(request)
    engineers = User.objects.filter(profile__role='engineer').order_by('username')

    if user_role == 'engineer':
        engineer = request.user
        if request.method == 'POST':
            body = request.POST.get('body', '').strip()
            if body:
                ChatMessage.objects.create(engineer=engineer, sender=request.user, body=body)
                _trigger_agents(engineer.id, body)
            return redirect('chat')
        messages = ChatMessage.objects.filter(engineer=engineer, hidden=False).select_related('sender')
        last_message_id = messages.values_list('id', flat=True).last() or 0
        return render(request, 'tickets/chat.html', {
            'messages': messages,
            'engineer': engineer,
            'user_role': user_role,
            'last_message_id': last_message_id,
        })

    if user_role == 'ops':
        eng_id = request.GET.get('engineer') or request.POST.get('engineer_id')
        if not eng_id and engineers.exists():
            eng_id = str(engineers.first().pk)

        selected_engineer = None
        if eng_id:
            try:
                selected_engineer = engineers.get(pk=eng_id)
            except User.DoesNotExist:
                pass

        if request.method == 'POST':
            body = request.POST.get('body', '').strip()
            if body and selected_engineer:
                ChatMessage.objects.create(
                    engineer=selected_engineer, sender=request.user, body=body,
                )
            dest = reverse('chat')
            if selected_engineer:
                dest += f'?engineer={selected_engineer.pk}'
            return redirect(dest)

        messages_qs = (
            ChatMessage.objects.filter(engineer=selected_engineer, hidden=False).select_related('sender')
            if selected_engineer else ChatMessage.objects.none()
        )
        last_message_id = messages_qs.values_list('id', flat=True).last() or 0
        return render(request, 'tickets/chat.html', {
            'messages': messages_qs,
            'engineer': selected_engineer,
            'engineers': engineers,
            'selected_engineer': selected_engineer,
            'eng_id': eng_id or '',
            'user_role': user_role,
            'last_message_id': last_message_id,
        })

    return HttpResponseForbidden()


@login_required
def chat_poll(request):
    """Return new messages since `?since=<id>` and whether any agents are typing."""
    user_role = role(request)
    try:
        since_id = int(request.GET.get('since', 0))
    except (ValueError, TypeError):
        since_id = 0

    if user_role == 'engineer':
        engineer = request.user
    elif user_role == 'ops':
        eng_id = request.GET.get('engineer')
        if not eng_id:
            return JsonResponse({'messages': [], 'typing': False})
        try:
            engineer = User.objects.get(pk=eng_id, profile__role='engineer')
        except User.DoesNotExist:
            return JsonResponse({'error': 'not found'}, status=404)
    else:
        return JsonResponse({'error': 'forbidden'}, status=403)

    new_msgs = (
        ChatMessage.objects
        .filter(engineer=engineer, id__gt=since_id, hidden=False)
        .select_related('sender')
        .order_by('sent_at')
    )
    typing = Agent.objects.filter(engineer=engineer, status=Agent.DELIBERATING).exists()

    return JsonResponse({
        'messages': [
            {
                'id': m.id,
                'sender': m.sender.username,
                'body': m.body,
                'sent_at': m.sent_at.strftime('%b %d, %H:%M'),
            }
            for m in new_msgs
        ],
        'typing': typing,
    })


@login_required
def chat_post(request):
    """Post a chat message via AJAX; returns JSON."""
    if request.method != 'POST':
        return JsonResponse({'error': 'method not allowed'}, status=405)
    user_role = role(request)
    body = request.POST.get('body', '').strip()
    if not body:
        return JsonResponse({'error': 'body required'}, status=400)

    if user_role == 'engineer':
        engineer = request.user
        msg = ChatMessage.objects.create(engineer=engineer, sender=request.user, body=body)
        _trigger_agents(engineer.id, body)
    elif user_role == 'ops':
        try:
            engineer = User.objects.get(
                pk=int(request.POST.get('engineer_id', 0)),
                profile__role='engineer',
            )
        except (User.DoesNotExist, ValueError, TypeError):
            return JsonResponse({'error': 'engineer not found'}, status=404)
        msg = ChatMessage.objects.create(engineer=engineer, sender=request.user, body=body)
    else:
        return JsonResponse({'error': 'forbidden'}, status=403)

    return JsonResponse({'ok': True, 'id': msg.id})


@login_required
def chat_clear(request):
    """POST — mark all visible messages for the engineer as hidden."""
    if request.method != 'POST':
        return JsonResponse({'error': 'method not allowed'}, status=405)
    user_role = role(request)
    if user_role == 'engineer':
        engineer = request.user
    elif user_role == 'ops':
        try:
            engineer = User.objects.get(
                pk=int(request.POST.get('engineer_id', 0)),
                profile__role='engineer',
            )
        except (User.DoesNotExist, ValueError, TypeError):
            return JsonResponse({'error': 'engineer not found'}, status=404)
    else:
        return JsonResponse({'error': 'forbidden'}, status=403)

    ChatMessage.objects.filter(engineer=engineer, hidden=False).update(hidden=True)
    return JsonResponse({'ok': True})


@login_required
def agents_stop(request):
    """POST — mark all deliberating agents for the engineer as committed."""
    if request.method != 'POST':
        return JsonResponse({'error': 'method not allowed'}, status=405)
    user_role = role(request)
    if user_role == 'engineer':
        engineer = request.user
    elif user_role == 'ops':
        try:
            engineer = User.objects.get(
                pk=int(request.POST.get('engineer_id', 0)),
                profile__role='engineer',
            )
        except (User.DoesNotExist, ValueError, TypeError):
            return JsonResponse({'error': 'engineer not found'}, status=404)
    else:
        return JsonResponse({'error': 'forbidden'}, status=403)

    Agent.objects.filter(engineer=engineer, status=Agent.DELIBERATING).update(status=Agent.COMMITTED)
    return JsonResponse({'ok': True})


# ── Agent views (engineer only) ───────────────────────────────────────────────

import re
import secrets as _secrets


def _provision_agent_ops_user(name):
    """Create a dedicated ops User + UserProfile + ApiKey for a new agent."""
    slug = re.sub(r'[^a-z0-9]', '_', name.lower())[:20].strip('_')
    suffix = _secrets.token_hex(4)
    username = f'agent_{slug}_{suffix}'
    ops_user = User.objects.create_user(username=username)
    ops_user.set_unusable_password()
    ops_user.save()
    UserProfile.objects.create(user=ops_user, role='ops')
    ApiKey.objects.create(user=ops_user)
    return ops_user


@require_role('engineer')
def agent_list(request):
    agents = Agent.objects.filter(engineer=request.user).order_by('priority')
    return render(request, 'tickets/agents_list.html', {'agents': agents})


@require_role('engineer')
def agent_create(request):
    error = None
    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        system_prompt = request.POST.get('system_prompt', '').strip()
        priority_raw = request.POST.get('priority', '').strip()

        if not name:
            error = 'Name is required.'
        elif not system_prompt:
            error = 'System prompt is required.'
        elif not priority_raw.isdigit():
            error = 'Priority must be a positive integer.'
        elif Agent.objects.filter(engineer=request.user, priority=int(priority_raw)).exists():
            error = f'You already have an agent with priority {priority_raw}.'
        else:
            ops_user = _provision_agent_ops_user(name)
            Agent.objects.create(
                engineer=request.user,
                ops_user=ops_user,
                name=name,
                system_prompt=system_prompt,
                priority=int(priority_raw),
            )
            return redirect('agents_list')

    return render(request, 'tickets/agents_create.html', {'error': error})


@require_role('engineer')
def agent_chat(request, pk):
    agent = get_object_or_404(Agent, pk=pk, engineer=request.user)

    if request.method == 'POST':
        action = request.POST.get('action')

        if action == 'commit':
            from agents.base import GenericAgent
            GenericAgent(agent).commit_chat_to_document()
            return redirect('agent_chat', pk=pk)

        body = request.POST.get('body', '').strip()
        if body:
            AgentMessage.objects.create(agent=agent, role=AgentMessage.USER, body=body)
        return redirect('agent_chat', pk=pk)

    messages = agent.messages.all()
    return render(request, 'tickets/agents_chat.html', {
        'agent': agent,
        'messages': messages,
    })


@require_role('engineer')
def agent_edit(request, pk):
    agent = get_object_or_404(Agent, pk=pk, engineer=request.user)
    error = None

    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        system_prompt = request.POST.get('system_prompt', '').strip()
        priority_raw = request.POST.get('priority', '').strip()

        if not name:
            error = 'Name is required.'
        elif not system_prompt:
            error = 'System prompt is required.'
        elif not priority_raw.isdigit():
            error = 'Priority must be a positive integer.'
        elif (
            Agent.objects
            .filter(engineer=request.user, priority=int(priority_raw))
            .exclude(pk=agent.pk)
            .exists()
        ):
            error = f'You already have an agent with priority {priority_raw}.'
        else:
            agent.name = name
            agent.system_prompt = system_prompt
            agent.priority = int(priority_raw)
            agent.save()
            return redirect('agents_list')

    return render(request, 'tickets/agents_edit.html', {'agent': agent, 'error': error})


@require_role('engineer')
def agent_suggest_prompt(request):
    """POST {name} → JSON {prompt}. Uses Claude to draft a system prompt for the agent."""
    if request.method != 'POST':
        return JsonResponse({'error': 'method not allowed'}, status=405)
    name = request.POST.get('name', '').strip()
    if not name:
        return JsonResponse({'error': 'name required'}, status=400)

    existing_prompt = request.POST.get('existing_prompt', '').strip()
    user_prompt = request.POST.get('user_prompt', '').strip()

    # Build a human-readable work schedule for the engineer
    try:
        ws = WorkSchedule.objects.get(engineer=request.user)
        hours_lines = []
        for key, label in zip(DAY_KEYS, DAY_LABELS):
            start = getattr(ws, f'{key}_start')
            end = getattr(ws, f'{key}_end')
            if start and end:
                hours_lines.append(f"  {label}: {start:%H:%M}–{end:%H:%M}")
            else:
                hours_lines.append(f"  {label}: not working")
        work_hours_text = '\n'.join(hours_lines)
    except WorkSchedule.DoesNotExist:
        work_hours_text = '  (not configured)'

    import os
    client = anthropic.Anthropic(api_key=os.environ.get('ANTHROPIC_API_KEY') or None)

    existing_section = f"""
EXISTING SYSTEM PROMPT (rewrite or refine this — do not copy it verbatim unless it is already good):
{existing_prompt}
""" if existing_prompt else ''

    instructions_section = f"""
SPECIFIC INSTRUCTIONS FROM THE USER (follow these closely):
{user_prompt}
""" if user_prompt else ''

    meta = f"""Rewrite the system prompt for an AI scheduling assistant named "{name}".

Context: this agent runs inside a multi-agent ITSM calendar system. Several agents (each with a different domain) collaborate to fill an engineer's workday with scheduled blocks. Each agent has an integer priority; lower = higher priority. Higher-priority agents claim time slots first; lower-priority ones must work around them.

ENGINEER'S WORKING HOURS:
{work_hours_text}
{existing_section}{instructions_section}
The system prompt you produce must:
1. Clearly state the agent's domain (what kinds of activities it schedules and why they matter).
2. Give 3–5 concrete example activity titles this agent would create, with realistic times drawn from the engineer's actual working hours above.
3. Specify sensible default durations (e.g. "30-minute", "1-hour").
4. Explicitly reference the engineer's working days and hours so the agent knows when it can schedule.
5. Tell the agent to move its own blocks rather than overlap a higher-priority agent's blocks.
6. Be specific and decisive — no vague placeholders.

Write only the system prompt itself (150–250 words). No title, no preamble."""

    response = client.messages.create(
        model='claude-sonnet-4-6',
        max_tokens=500,
        messages=[{'role': 'user', 'content': meta}],
    )
    return JsonResponse({'prompt': response.content[0].text.strip()})
