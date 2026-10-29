from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import path

from web import views

urlpatterns = [
    path("", views.home, name="home"),
    path("startsit/", views.startsit, name="startsit"),
    path("waivers/", views.waivers, name="waivers"),
    path("trades/", views.trades, name="trades"),
    path("trades/find/", views.trade_finder, name="trade_finder"),
    path("explain/<str:kind>/", views.explain, name="explain"),
    path("login/", auth_views.LoginView.as_view(template_name="web/login.html", redirect_authenticated_user=True), name="login"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("admin/", admin.site.urls),
    path("manifest.webmanifest", views.manifest, name="manifest"),
    path("sw.js", views.service_worker, name="service_worker"),
    path("offline/", views.offline, name="offline"),
]
