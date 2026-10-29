from django.contrib import admin

from .models import League, Matchup, Player, PlayerWeek, ProGame, RosterEntry, Team


@admin.register(League)
class LeagueAdmin(admin.ModelAdmin):
    list_display = ["name", "season", "current_week", "team_count", "synced_at"]


@admin.register(Team)
class TeamAdmin(admin.ModelAdmin):
    list_display = ["name", "standing", "wins", "losses", "points_for"]


@admin.register(Player)
class PlayerAdmin(admin.ModelAdmin):
    list_display = ["name", "position", "pro_team", "injury_status", "percent_owned"]
    search_fields = ["name"]
    list_filter = ["position"]


@admin.register(RosterEntry)
class RosterEntryAdmin(admin.ModelAdmin):
    list_display = ["team", "player", "lineup_slot"]
    list_filter = ["team"]


@admin.register(PlayerWeek)
class PlayerWeekAdmin(admin.ModelAdmin):
    list_display = ["player", "week", "points", "projected_points"]
    list_filter = ["week"]
    search_fields = ["player__name"]


@admin.register(Matchup)
class MatchupAdmin(admin.ModelAdmin):
    list_display = ["week", "team_a", "score_a", "team_b", "score_b"]


admin.site.register(ProGame)
