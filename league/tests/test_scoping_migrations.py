"""The league-scoping migrations run once against real data, and snapshots can't be rebuilt."""

from datetime import datetime, timezone

import pytest
from django.core.management import call_command
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

pytestmark = pytest.mark.django_db(transaction=True)

BEFORE = [("league", "0007_sync_run"), ("nfl", "0003_game_lines")]
AFTER = [("league", "0010_league_scoped_finish"), ("nfl", "0006_league_scoped_finish")]
CLEARS = datetime(2026, 11, 3, 9, tzinfo=timezone.utc)


def migrate(targets):
    executor = MigrationExecutor(connection)
    executor.migrate(targets)
    return MigrationExecutor(connection).loader.project_state(targets).apps


@pytest.fixture
def old_apps():
    yield migrate(BEFORE)
    # leave the schema as the rest of the suite expects, even if the test failed partway
    call_command("flush", verbosity=0, interactive=False)
    call_command("migrate", verbosity=0)


def seed(apps, seasons=(2026,)):
    League = apps.get_model("league", "League")
    leagues = {
        s: League.objects.create(espn_id=1, season=s, name="L", team_count=2, current_week=8, reg_season_weeks=14)
        for s in seasons
    }
    player = apps.get_model("league", "Player").objects.create(
        espn_id=1, name="P", position="WR", pro_team="BUF", season_projection=88.0, fa_status="WAIVERS", waiver_clears=CLEARS
    )
    for s in seasons:
        apps.get_model("league", "PlayerWeek").objects.create(player=player, season=s, week=1, points=9.0)
        apps.get_model("nfl", "Projection").objects.create(player=player, season=s, week=8, source="sleeper", points=7.0)
        apps.get_model("nfl", "ProjectionSnapshot").objects.create(player=player, season=s, week=8, mean=12.5, sd=3.0)
    apps.get_model("league", "SyncRun").objects.create(step="league", ok=True)
    apps.get_model("league", "SyncRun").objects.create(step="projections", ok=True)
    return leagues, player


def test_existing_rows_move_to_the_league(old_apps):
    leagues, player = seed(old_apps)
    league_id = leagues[2026].pk

    apps = migrate(AFTER)

    entry = apps.get_model("league", "LeaguePlayer").objects.get()
    assert (entry.league_id, entry.player_id) == (league_id, player.pk)
    assert (entry.season_projection, entry.fa_status, entry.waiver_clears) == (88.0, "WAIVERS", CLEARS)
    assert apps.get_model("league", "PlayerWeek").objects.get().league_id == league_id
    assert apps.get_model("nfl", "Projection").objects.get().league_id == league_id
    runs = dict(apps.get_model("league", "SyncRun").objects.values_list("step", "league_id"))
    assert runs == {"league": league_id, "projections": None}


def test_snapshots_survive_with_their_values(old_apps):
    leagues, player = seed(old_apps)
    apps = migrate(AFTER)
    snap = apps.get_model("nfl", "ProjectionSnapshot").objects.get()
    assert (snap.league_id, snap.player_id, snap.mean, snap.sd) == (leagues[2026].pk, player.pk, 12.5, 3.0)


def test_each_season_goes_to_that_seasons_league(old_apps):
    leagues, _ = seed(old_apps, seasons=(2025, 2026))
    apps = migrate(AFTER)
    for model in (apps.get_model("league", "PlayerWeek"), apps.get_model("nfl", "ProjectionSnapshot")):
        assert {row.season: row.league_id for row in model.objects.all()} == {s: l.pk for s, l in leagues.items()}
    # the pool is the current league's, since that's the only one the app shows
    assert apps.get_model("league", "LeaguePlayer").objects.get().league_id == leagues[2026].pk


def test_an_empty_database_migrates(old_apps):
    apps = migrate(AFTER)
    assert not apps.get_model("league", "LeaguePlayer").objects.exists()


def test_rows_with_no_league_stop_the_migration(old_apps):
    # dropping these silently would lose snapshots for good
    player = old_apps.get_model("league", "Player").objects.create(espn_id=1, name="P", position="WR", pro_team="BUF")
    old_apps.get_model("nfl", "ProjectionSnapshot").objects.create(player=player, season=2026, week=8, mean=1.0, sd=1.0)
    with pytest.raises(RuntimeError, match="no league"):
        migrate(AFTER)


def test_rolling_back_restores_player_fields(old_apps):
    seed(old_apps)
    migrate(AFTER)
    apps = migrate(BEFORE)
    player = apps.get_model("league", "Player").objects.get()
    assert (player.season_projection, player.fa_status, player.waiver_clears) == (88.0, "WAIVERS", CLEARS)
    assert apps.get_model("nfl", "ProjectionSnapshot").objects.count() == 1
