from django.contrib import admin
from django.urls import path, include
from django.contrib.auth import views as auth_views
from tickets import views as ticket_views

urlpatterns = [
    path('admin/', admin.site.urls),
    path('login/', auth_views.LoginView.as_view(template_name='tickets/login.html'), name='login'),
    path('logout/', auth_views.LogoutView.as_view(), name='logout'),
    path('', ticket_views.home, name='home'),
    path('', include('tickets.urls')),
]
