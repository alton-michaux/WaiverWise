from django.contrib import admin
from django.urls import path

from web import views

urlpatterns = [
    path("", views.home, name="home"),
    path("startsit/", views.startsit, name="startsit"),
    path("waivers/", views.waivers, name="waivers"),
    path("trades/", views.trades, name="trades"),
    path("trades/find/", views.trade_finder, name="trade_finder"),
    path("explain/<str:kind>/", views.explain, name="explain"),
    path("admin/", admin.site.urls),
    path("health/", views.health, name="health"),
]
