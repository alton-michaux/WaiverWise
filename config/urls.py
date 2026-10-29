from django.contrib import admin
from django.urls import path

from web import views

urlpatterns = [
    path("admin/", admin.site.urls),
    path("health/", views.health, name="health"),
]
