from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import path

from web import connect_views, views

urlpatterns = [
    path("", views.home, name="home"),
    path("startsit/", views.startsit, name="startsit"),
    path("startsit/moves/", views.moves_badge, name="moves_badge"),
    path("waivers/", views.waivers, name="waivers"),
    path("trades/", views.trades, name="trades"),
    path("trades/best/", views.best_deals, name="best_deals"),
    path("player/<int:pk>/", views.player_sheet, name="player_sheet"),
    path("league/switch/", views.switch_league, name="switch_league"),
    path("connect/", connect_views.connect, name="connect"),
    path("connect/<int:pk>/team/", connect_views.connect_team, name="connect_team"),
    path("league/status/", connect_views.sync_status, name="league_status"),
    path("account/", connect_views.account, name="account"),
    path("account/delete/", connect_views.delete_account, name="delete_account"),
    path("account/leagues/<int:pk>/disconnect/", connect_views.disconnect, name="disconnect_league"),
    path("account/leagues/<int:pk>/leave/", connect_views.leave, name="leave_league"),
    path("explain/<str:kind>/", views.explain, name="explain"),
    path("login/", auth_views.LoginView.as_view(template_name="web/login.html", redirect_authenticated_user=True), name="login"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("admin/", admin.site.urls),
    path("manifest.webmanifest", views.manifest, name="manifest"),
    path("sw.js", views.service_worker, name="service_worker"),
    path("offline/", views.offline, name="offline"),
]
