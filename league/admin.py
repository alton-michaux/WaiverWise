from django.contrib import admin

from .models import EspnCredential, League, LeaguePlayer, Matchup, Membership, PendingMove, Player, PlayerWeek, ProGame, RosterEntry, Team


@admin.register(League)
class LeagueAdmin(admin.ModelAdmin):
    list_display = ["name", "season", "current_week", "team_count", "synced_at"]


@admin.register(Team)
class TeamAdmin(admin.ModelAdmin):
    list_display = ["name", "standing", "wins", "losses", "points_for"]


@admin.register(Membership)
class MembershipAdmin(admin.ModelAdmin):
    list_display = ["user", "league", "team"]
    list_filter = ["league"]


@admin.register(EspnCredential)
class EspnCredentialAdmin(admin.ModelAdmin):
    """Status only: ciphertext has no business on a page, and staff can't add or edit someone's login."""

    list_display = ["user", "league", "status", "last_ok_at"]
    list_filter = ["status", "league"]
    fields = ["user", "league", "status", "last_ok_at"]
    readonly_fields = fields

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(Player)
class PlayerAdmin(admin.ModelAdmin):
    list_display = ["name", "position", "pro_team", "injury_status", "percent_owned"]
    search_fields = ["name"]
    list_filter = ["position"]


@admin.register(LeaguePlayer)
class LeaguePlayerAdmin(admin.ModelAdmin):
    list_display = ["player", "league", "season_projection", "fa_status", "waiver_clears"]
    list_filter = ["league", "fa_status"]
    search_fields = ["player__name"]


@admin.register(RosterEntry)
class RosterEntryAdmin(admin.ModelAdmin):
    list_display = ["team", "player", "lineup_slot"]
    list_filter = ["team"]


@admin.register(PlayerWeek)
class PlayerWeekAdmin(admin.ModelAdmin):
    list_display = ["player", "league", "week", "points", "projected_points"]
    list_filter = ["league", "week"]
    search_fields = ["player__name"]


@admin.register(Matchup)
class MatchupAdmin(admin.ModelAdmin):
    list_display = ["week", "team_a", "score_a", "team_b", "score_b"]


admin.site.register(ProGame)


@admin.register(PendingMove)
class PendingMoveAdmin(admin.ModelAdmin):
    list_display = ["kind", "team_espn_id", "espn_id"]
