from django.contrib import admin
from django.contrib.staticfiles.views import serve as staticfiles_serve
from django.urls import path, include, re_path
from django.contrib.auth import views as auth_views
from tickets import views as ticket_views

urlpatterns = [
    path('admin/', admin.site.urls),
    path('login/', auth_views.LoginView.as_view(template_name='tickets/login.html'), name='login'),
    path('logout/', auth_views.LogoutView.as_view(), name='logout'),
    path('', ticket_views.home, name='home'),
    path('', include('tickets.urls')),
    # Always serve /static/ via the staticfiles app (insecure=True bypasses
    # the DEBUG check). This site is LAN-only — see ALLOWED_HOSTS in settings.
    re_path(r'^static/(?P<path>.*)$', staticfiles_serve, {'insecure': True}),
]
