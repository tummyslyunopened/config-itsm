from datetime import datetime, time as dt_time
from django import forms
from django.contrib.auth.models import User
from .models import Ticket, ScheduleEntry, TimeEntry, WorkSchedule, DAY_KEYS, DAY_LABELS


def _parse_time(s):
    """Accept '900', '0900', '9:00', '09:00', '09:00:00' → dt_time. Raises ValueError on bad input."""
    if isinstance(s, dt_time):
        return s
    s = str(s).strip().replace(':', '')
    # Strip trailing seconds (HHMMSS → HHMM)
    if len(s) == 6:
        s = s[:4]
    if len(s) == 3:
        s = '0' + s
    if len(s) == 4:
        h, m = int(s[:2]), int(s[2:])
        if 0 <= h <= 23 and 0 <= m <= 59:
            return dt_time(h, m)
    raise ValueError(s)


class _TimeField(forms.CharField):
    def clean(self, value):
        value = super().clean(value)
        try:
            return _parse_time(value)
        except (ValueError, TypeError):
            raise forms.ValidationError('Enter a time like 0900 or 1430.')


class _OptionalTimeField(forms.CharField):
    def __init__(self, *args, **kwargs):
        kwargs.setdefault('required', False)
        kwargs.pop('max_length', None)  # no length limit — we accept HH:MM:SS from DB
        super().__init__(*args, **kwargs)

    def prepare_value(self, value):
        # Render existing time objects as HHMM shorthand, not HH:MM:SS
        if isinstance(value, dt_time):
            return f'{value.hour:02d}{value.minute:02d}'
        return value

    def clean(self, value):
        value = super().clean(value)
        if not value or not str(value).strip():
            return None
        try:
            return _parse_time(value)
        except (ValueError, TypeError):
            raise forms.ValidationError('Enter a time like 0900 or 1700, or leave blank.')


class TicketCreateForm(forms.ModelForm):
    class Meta:
        model = Ticket
        fields = ['title', 'description']
        widgets = {
            'title': forms.TextInput(attrs={'placeholder': 'Short summary'}),
            'description': forms.Textarea(attrs={'rows': 4}),
        }


class TicketStatusForm(forms.ModelForm):
    class Meta:
        model = Ticket
        fields = ['status']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['status'].choices = [
            (v, l) for v, l in Ticket.STATUS_CHOICES
            if v in Ticket.ENGINEER_SETTABLE
        ]


class ScheduleEntryForm(forms.Form):
    engineer = forms.ModelChoiceField(
        queryset=User.objects.filter(profile__role='engineer'),
        label='Engineer',
    )
    date = forms.DateField(
        widget=forms.DateInput(attrs={'type': 'date'}),
        label='Date',
    )
    start_time = _TimeField(
        max_length=5, label='Start time',
        widget=forms.TextInput(attrs={'placeholder': '0900'}),
    )
    end_time = _TimeField(
        max_length=5, label='End time',
        widget=forms.TextInput(attrs={'placeholder': '1700'}),
    )

    def get_start(self):
        return datetime.combine(self.cleaned_data['date'], self.cleaned_data['start_time'])

    def get_end(self):
        return datetime.combine(self.cleaned_data['date'], self.cleaned_data['end_time'])


class TimeEntryForm(forms.Form):
    notes = forms.CharField(widget=forms.Textarea(attrs={'rows': 3}), label='Notes')
    date = forms.DateField(
        widget=forms.DateInput(attrs={'type': 'date'}),
        label='Date',
    )
    start_time = _TimeField(
        max_length=5, label='Start time',
        widget=forms.TextInput(attrs={'placeholder': '0900'}),
    )
    end_time = _TimeField(
        max_length=5, label='End time',
        widget=forms.TextInput(attrs={'placeholder': '1700'}),
    )

    def get_start(self):
        return datetime.combine(self.cleaned_data['date'], self.cleaned_data['start_time'])

    def get_end(self):
        return datetime.combine(self.cleaned_data['date'], self.cleaned_data['end_time'])


_TIME_INPUT = {'placeholder': 'e.g. 0900'}


def _ws_field(label):
    return _OptionalTimeField(
        label=label,
        widget=forms.TextInput(attrs=_TIME_INPUT),
    )


class WorkScheduleForm(forms.ModelForm):
    # Override all time fields with shorthand-accepting optional fields.
    mon_start = _ws_field('Start'); mon_end = _ws_field('End')
    tue_start = _ws_field('Start'); tue_end = _ws_field('End')
    wed_start = _ws_field('Start'); wed_end = _ws_field('End')
    thu_start = _ws_field('Start'); thu_end = _ws_field('End')
    fri_start = _ws_field('Start'); fri_end = _ws_field('End')
    sat_start = _ws_field('Start'); sat_end = _ws_field('End')
    sun_start = _ws_field('Start'); sun_end = _ws_field('End')

    class Meta:
        model = WorkSchedule
        exclude = ['engineer']

    def clean(self):
        data = super().clean()
        for key in DAY_KEYS:
            start = data.get(f'{key}_start')
            end   = data.get(f'{key}_end')
            if bool(start) != bool(end):
                target = f'{key}_end' if start else f'{key}_start'
                self.add_error(target, 'Both start and end are required, or leave both blank.')
        return data
