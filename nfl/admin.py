from django.contrib import admin

from .models import GameLine, Projection, ProjectionSnapshot, Usage


@admin.register(Projection)
class ProjectionAdmin(admin.ModelAdmin):
    list_display = ["player", "week", "source", "points", "opponent"]
    list_filter = ["source", "week"]
    search_fields = ["player__name"]


@admin.register(ProjectionSnapshot)
class ProjectionSnapshotAdmin(admin.ModelAdmin):
    list_display = ["player", "week", "mean", "espn", "sleeper", "opponent", "captured_at"]
    list_filter = ["week"]
    search_fields = ["player__name"]


@admin.register(GameLine)
class GameLineAdmin(admin.ModelAdmin):
    list_display = ["team", "week", "is_home", "spread", "total", "implied_total", "rest_days", "roof"]
    list_filter = ["week"]
    search_fields = ["team"]


@admin.register(Usage)
class UsageAdmin(admin.ModelAdmin):
    list_display = ["player", "week", "snap_pct", "targets", "target_share", "carries"]
    list_filter = ["week"]
    search_fields = ["player__name"]
