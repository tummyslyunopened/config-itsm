from django.db import models
from django.contrib.auth.models import User


class UserProfile(models.Model):
    OPS = 'ops'
    ENGINEER = 'engineer'
    ROLE_CHOICES = [(OPS, 'Ops'), (ENGINEER, 'Engineer')]

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='profile')
    role = models.CharField(max_length=10, choices=ROLE_CHOICES)

    def __str__(self):
        return f'{self.user.username} ({self.role})'


class Ticket(models.Model):
    NEW = 'new'
    SCHEDULED = 'scheduled'
    IN_PROGRESS = 'in_progress'
    NEEDS_NEW = 'needs_new'
    WAITING = 'waiting'
    COMPLETE = 'complete'
    STATUS_CHOICES = [
        (NEW, 'New'),
        (SCHEDULED, 'Scheduled'),
        (IN_PROGRESS, 'In Progress'),
        (NEEDS_NEW, 'Needs New'),
        (WAITING, 'Waiting'),
        (COMPLETE, 'Complete'),
    ]
    ENGINEER_SETTABLE = [IN_PROGRESS, NEEDS_NEW, WAITING, COMPLETE]

    title = models.CharField(max_length=200, default='')
    description = models.TextField(blank=True, default='')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=NEW)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f'#{self.pk} — {self.title}'


class ScheduleEntry(models.Model):
    ticket = models.ForeignKey(Ticket, on_delete=models.CASCADE, related_name='schedule_entries')
    engineer = models.ForeignKey(User, on_delete=models.CASCADE, related_name='schedule_entries')
    start = models.DateTimeField()
    end = models.DateTimeField()

    class Meta:
        ordering = ['start']

    def __str__(self):
        return f'#{self.ticket_id} — {self.engineer.username} {self.start:%Y-%m-%d %H:%M}'


DAY_KEYS = ['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun']
DAY_LABELS = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']


class WorkSchedule(models.Model):
    engineer = models.OneToOneField(
        User, on_delete=models.CASCADE, related_name='work_schedule',
        limit_choices_to={'profile__role': 'engineer'},
    )
    mon_start = models.TimeField(null=True, blank=True)
    mon_end   = models.TimeField(null=True, blank=True)
    tue_start = models.TimeField(null=True, blank=True)
    tue_end   = models.TimeField(null=True, blank=True)
    wed_start = models.TimeField(null=True, blank=True)
    wed_end   = models.TimeField(null=True, blank=True)
    thu_start = models.TimeField(null=True, blank=True)
    thu_end   = models.TimeField(null=True, blank=True)
    fri_start = models.TimeField(null=True, blank=True)
    fri_end   = models.TimeField(null=True, blank=True)
    sat_start = models.TimeField(null=True, blank=True)
    sat_end   = models.TimeField(null=True, blank=True)
    sun_start = models.TimeField(null=True, blank=True)
    sun_end   = models.TimeField(null=True, blank=True)

    def hours_for_date(self, d):
        """Return (start_time, end_time) for a given date, or (None, None) if not a work day."""
        key = DAY_KEYS[d.weekday()]
        return getattr(self, f'{key}_start'), getattr(self, f'{key}_end')

    def __str__(self):
        return f'{self.engineer.username} work schedule'


class TimeEntry(models.Model):
    ticket = models.ForeignKey(Ticket, on_delete=models.CASCADE, related_name='time_entries')
    engineer = models.ForeignKey(User, on_delete=models.CASCADE, related_name='time_entries')
    notes = models.TextField()
    start = models.DateTimeField()
    end = models.DateTimeField()

    class Meta:
        ordering = ['start']

    def __str__(self):
        return f'#{self.ticket_id} — {self.engineer.username} {self.start:%Y-%m-%d %H:%M}'
