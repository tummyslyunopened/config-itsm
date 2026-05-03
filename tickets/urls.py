from django.urls import path
from . import views

urlpatterns = [
    path('tickets/', views.ticket_list, name='ticket_list'),
    path('tickets/create/', views.ticket_create, name='ticket_create'),
    path('tickets/<int:pk>/', views.ticket_detail, name='ticket_detail'),
    path('tickets/<int:pk>/schedule/', views.schedule_entry_add, name='schedule_entry_add'),
    path('calendar/', views.calendar_view, name='calendar'),
    path('queue/', views.queue_view, name='queue'),
    path('hours/', views.work_schedule_view, name='work_schedule'),
]
