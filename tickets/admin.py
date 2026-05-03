from django.contrib import admin
from .models import UserProfile, Ticket, ScheduleEntry, TimeEntry, WorkSchedule

admin.site.register(UserProfile)
admin.site.register(Ticket)
admin.site.register(ScheduleEntry)
admin.site.register(TimeEntry)
admin.site.register(WorkSchedule)
