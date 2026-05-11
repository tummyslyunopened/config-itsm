from django.urls import path
from . import views, api_views

urlpatterns = [
    path('tickets/', views.ticket_list, name='ticket_list'),
    path('tickets/create/', views.ticket_create, name='ticket_create'),
    path('tickets/<int:pk>/', views.ticket_detail, name='ticket_detail'),
    path('tickets/<int:pk>/schedule/', views.schedule_entry_add, name='schedule_entry_add'),
    path('calendar/', views.calendar_view, name='calendar'),
    path('queue/', views.queue_view, name='queue'),
    path('hours/', views.work_schedule_view, name='work_schedule'),

    path('chat/', views.chat_view, name='chat'),
    path('chat/poll/', views.chat_poll, name='chat_poll'),
    path('chat/post/', views.chat_post, name='chat_post'),
    path('chat/clear/', views.chat_clear, name='chat_clear'),
    path('chat/stop-agents/', views.agents_stop, name='agents_stop'),
    path('chat/report/', views.chat_report, name='chat_report'),

    path('agents/', views.agent_list, name='agents_list'),
    path('agents/create/', views.agent_create, name='agent_create'),
    path('agents/suggest-prompt/', views.agent_suggest_prompt, name='agent_suggest_prompt'),
    path('agents/<int:pk>/', views.agent_chat, name='agent_chat'),
    path('agents/<int:pk>/edit/', views.agent_edit, name='agent_edit'),
    path('agents/<int:pk>/delete/', views.agent_delete, name='agent_delete'),

    # AI agent API
    path('api/tickets/', api_views.ticket_list, name='api_ticket_list'),
    path('api/tickets/<int:pk>/', api_views.ticket_detail, name='api_ticket_detail'),
    path('api/tickets/<int:pk>/status/', api_views.ticket_status, name='api_ticket_status'),
    path('api/tickets/<int:pk>/schedule/', api_views.ticket_schedule, name='api_ticket_schedule'),
    path('api/engineers/', api_views.engineer_list, name='api_engineer_list'),
    path('api/engineers/<int:pk>/schedule/', api_views.engineer_schedule, name='api_engineer_schedule'),
    path('api/engineers/<int:pk>/chat/', api_views.engineer_chat, name='api_engineer_chat'),
    path('api/agents/<int:agent_id>/state/', api_views.agent_state, name='api_agent_state'),
    path('api/agents/<int:agent_id>/schedule/future/', api_views.agent_clear_future_schedule, name='api_agent_clear_future'),
]
