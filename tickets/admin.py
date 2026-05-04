from django.contrib import admin
from .models import UserProfile, Ticket, ScheduleEntry, TimeEntry, WorkSchedule, ApiKey, ChatMessage, Agent, AgentMessage

admin.site.register(UserProfile)
admin.site.register(Ticket)
admin.site.register(ScheduleEntry)
admin.site.register(TimeEntry)
admin.site.register(WorkSchedule)
admin.site.register(ApiKey)
admin.site.register(ChatMessage)
admin.site.register(Agent)
admin.site.register(AgentMessage)
