from datetime import date, timedelta
from django.contrib.auth.models import User
from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.decorators import login_required
from django.http import HttpResponseForbidden
from .models import Ticket, ScheduleEntry, TimeEntry, WorkSchedule, DAY_KEYS
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
        return render(request, 'tickets/calendar.html', {
            'view_mode': view_mode, 'agenda_entries': agenda, 'today': today,
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
