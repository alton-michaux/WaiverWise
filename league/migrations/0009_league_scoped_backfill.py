from django.db import migrations

BATCH_SIZE = 500


def backfill(apps, schema_editor):
    """Everything so far belongs to the one league there has been. Stops rather than guessing if that's not so."""
    League = apps.get_model("league", "League")
    Player = apps.get_model("league", "Player")
    LeaguePlayer = apps.get_model("league", "LeaguePlayer")
    PlayerWeek = apps.get_model("league", "PlayerWeek")
    SyncRun = apps.get_model("league", "SyncRun")

    by_season = {}
    for league in League.objects.order_by("season"):
        if league.season in by_season:
            raise RuntimeError(f"season {league.season} has more than one league, so rows can't be assigned safely")
        by_season[league.season] = league
        PlayerWeek.objects.filter(season=league.season).update(league=league)

    orphans = PlayerWeek.objects.filter(league__isnull=True)
    if orphans.exists():
        seasons = sorted(set(orphans.values_list("season", flat=True)))
        raise RuntimeError(f"player weeks in seasons {seasons} have no league to belong to")

    if not by_season:
        return
    latest = by_season[max(by_season)]
    LeaguePlayer.objects.bulk_create(
        (
            LeaguePlayer(
                league=latest,
                player_id=pk,
                season_projection=season_projection,
                fa_status=fa_status,
                waiver_clears=waiver_clears,
            )
            for pk, season_projection, fa_status, waiver_clears in Player.objects.values_list(
                "pk", "season_projection", "fa_status", "waiver_clears"
            )
        ),
        batch_size=BATCH_SIZE,
    )
    # other steps are shared by every league, so they stay league-less
    SyncRun.objects.filter(step="league").update(league=latest)


def restore(apps, schema_editor):
    """Copy the newest league's values back onto Player. Only meaningful with a single league."""
    League = apps.get_model("league", "League")
    Player = apps.get_model("league", "Player")
    LeaguePlayer = apps.get_model("league", "LeaguePlayer")

    latest = League.objects.order_by("-season").first()
    rows = LeaguePlayer.objects.filter(league=latest)
    players = []
    for pk, season_projection, fa_status, waiver_clears in rows.values_list(
        "player_id", "season_projection", "fa_status", "waiver_clears"
    ):
        players.append(
            Player(pk=pk, season_projection=season_projection, fa_status=fa_status, waiver_clears=waiver_clears)
        )
    Player.objects.bulk_update(players, ["season_projection", "fa_status", "waiver_clears"], batch_size=BATCH_SIZE)


class Migration(migrations.Migration):
    """Step 2 of 3: assign existing rows to their league."""

    dependencies = [
        ("league", "0008_league_scoped_add"),
    ]

    operations = [
        migrations.RunPython(backfill, restore),
    ]
