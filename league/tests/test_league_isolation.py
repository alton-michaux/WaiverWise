"""A second league must never change what the first one sees."""

from dataclasses import replace
from datetime import timedelta

import httpx
import pytest
from django.core.management import call_command
from django.utils import timezone

from analysis.projections import Projection as ProjectedPoints
from league.management.commands.sync_all import Command as SyncAll
from league.management.commands.sync_league import Command as SyncLeague
from league.models import (
    League,
    LeaguePlayer,
    PendingMove,
    Player,
    PlayerWeek,
    ProGame,
    RosterEntry,
    SyncRun,
    active_leagues,
    league_players,
)
from league.tests.test_sync_league import league_data
from nfl import sleeper
from nfl.consensus import weekly_projections
from nfl.management.commands.projection_accuracy import Command as ProjectionAccuracy
from nfl.matchups import defense_ratings
from nfl.models import Projection, ProjectionSnapshot
from web import deals, services
from web.context_processors import sync_problem
from web.models import TradeDeals

pytestmark = pytest.mark.django_db


def test_league_players_carry_that_leagues_values(two_leagues, shared_player):
    full, half = two_leagues
    assert list(league_players(full).values_list("pk", "fa_status", "season_projection")) == [
        (shared_player.pk, "FREEAGENT", 100.0)
    ]
    assert list(league_players(half).values_list("pk", "fa_status", "season_projection")) == [
        (shared_player.pk, "", 60.0)
    ]


def test_league_players_leaves_out_other_leagues_pools(two_leagues, shared_player):
    full, half = two_leagues
    only_full = Player.objects.create(espn_id=501, name="Only Full", position="WR", pro_team="BUF")
    LeaguePlayer.objects.create(league=full, player=only_full)
    assert only_full in league_players(full)
    assert only_full not in league_players(half)


def test_active_leagues_are_the_newest_season(two_leagues):
    League.objects.create(espn_id=1, season=2025, name="Old", team_count=2, current_week=18, reg_season_weeks=14)
    assert list(active_leagues()) == list(two_leagues)


def test_no_leagues_means_no_active_leagues(db):
    assert list(active_leagues()) == []


def snapshot_of(league):
    return {
        "pool": sorted(LeaguePlayer.objects.filter(league=league).values_list("player_id", "fa_status", "season_projection")),
        "weeks": sorted(PlayerWeek.objects.filter(league=league).values_list("player_id", "week", "points")),
        "roster": sorted(RosterEntry.objects.filter(team__league=league).values_list("team__espn_id", "player_id")),
        "pending": sorted(league.pending_moves.values_list("espn_id", flat=True)),
    }


def test_syncing_another_league_leaves_this_one_alone():
    SyncLeague()._save(league_data())
    first = League.objects.get(espn_id=1)
    before = snapshot_of(first)

    # the same players, but a different league: other rosters, statuses, and scoring
    other = league_data(points_bump=7.0)
    players = [replace(p, fa_status="WAIVERS", season_projection=1.0) for p in other.players[:30]]
    SyncLeague()._save(replace(other, espn_id=2, players=players, pending=[]))

    assert snapshot_of(first) == before
    second = League.objects.get(espn_id=2)
    assert LeaguePlayer.objects.filter(league=second).count() == 30
    assert PlayerWeek.objects.get(league=second, player__espn_id=1001, week=2).points == 19.0
    assert PlayerWeek.objects.get(league=first, player__espn_id=1001, week=2).points == 12.0


def test_shared_player_facts_update_for_every_league():
    SyncLeague()._save(league_data())
    SyncLeague()._save(replace(league_data(injury="OUT"), espn_id=2))
    assert league_players(League.objects.get(espn_id=1)).get(espn_id=1001).injury_status == "OUT"


def test_leaving_one_leagues_pool_only_clears_availability_there():
    SyncLeague()._save(league_data())
    data = replace(league_data(), espn_id=2)
    SyncLeague()._save(data)

    SyncLeague()._save(replace(data, players=[p for p in data.players if p.espn_id != 1030]))

    assert LeaguePlayer.objects.get(league__espn_id=2, player__espn_id=1030).fa_status == ""
    assert LeaguePlayer.objects.get(league__espn_id=1, player__espn_id=1030).fa_status == "FREEAGENT"


def test_sleeper_is_fetched_once_and_scored_for_each_league(two_leagues, shared_player, monkeypatch):
    full, half = two_leagues
    fetches = []

    def fetch_week(season, week):
        fetches.append(week)
        return [{"player_id": "s500", "stats": {"rec": 10}, "opponent": "MIA"}]

    monkeypatch.setattr(sleeper, "fetch_week", fetch_week)
    call_command("sync_projections")

    assert sorted(fetches) == [8, 9]
    assert Projection.objects.get(league=full, player=shared_player, week=8).points == 10.0
    assert Projection.objects.get(league=half, player=shared_player, week=8).points == 5.0


def test_sleeper_projections_only_cover_this_leagues_pool(two_leagues, shared_player, monkeypatch):
    full, half = two_leagues
    only_full = Player.objects.create(espn_id=501, name="Only Full", position="WR", pro_team="BUF", sleeper_id="s501")
    LeaguePlayer.objects.create(league=full, player=only_full)
    monkeypatch.setattr(sleeper, "fetch_week", lambda season, week: [{"player_id": "s501", "stats": {"rec": 10}}])

    call_command("sync_projections")

    assert Projection.objects.filter(league=full, player=only_full).exists()
    assert not Projection.objects.filter(league=half, player=only_full).exists()


def test_a_failed_sleeper_week_is_not_retried_per_league(two_leagues, shared_player, monkeypatch):
    fetches = []

    def fetch_week(season, week):
        fetches.append(week)
        raise httpx.ConnectError("down")

    monkeypatch.setattr(sleeper, "fetch_week", fetch_week)
    call_command("sync_projections")

    assert sorted(fetches) == [8, 9]
    assert not Projection.objects.exists()


def test_projections_use_only_this_leagues_numbers(two_leagues, shared_player):
    full, half = two_leagues
    ProGame.objects.create(season=2026, week=8, team="BUF", opponent="MIA", kickoff=timezone.now() + timedelta(days=1))
    for league, points in ((full, 22.0), (half, 16.0)):
        Projection.objects.create(league=league, player=shared_player, season=2026, week=8, source="sleeper", points=points)

    def mean(league):
        return weekly_projections(league, list(league_players(league)), range(8, 9))[(shared_player.pk, 8)].mean

    assert mean(full) == 20.0  # (18 + 22) / 2
    assert mean(half) == 14.0  # (12 + 16) / 2


def test_defense_ratings_use_only_this_leagues_points(two_leagues):
    full, half = two_leagues
    player = Player.objects.create(espn_id=502, name="Receiver", position="WR", pro_team="BUF")
    for week in range(1, 7):
        opponent = "MIA" if week <= 3 else "KC"
        ProGame.objects.create(season=2026, week=week, team="BUF", opponent=opponent)
        # the same games, worth much more against Miami under full's scoring
        for league, vs_mia in ((full, 30.0), (half, 12.0)):
            points = vs_mia if opponent == "MIA" else 10.0
            PlayerWeek.objects.create(league=league, player=player, season=2026, week=week, points=points, breakdown={"rec": 1})

    assert defense_ratings(full)[("MIA", "WR")].points > defense_ratings(half)[("MIA", "WR")].points


def test_snapshots_are_saved_per_league(two_leagues, shared_player):
    full, half = two_leagues
    ProGame.objects.create(season=2026, week=8, team="BUF", opponent="MIA", kickoff=timezone.now() + timedelta(days=1))

    call_command("snapshot_projections")

    assert ProjectionSnapshot.objects.get(league=full, player=shared_player).espn == 18.0
    assert ProjectionSnapshot.objects.get(league=half, player=shared_player).espn == 12.0


def test_snapshots_save_this_leagues_sleeper_points(two_leagues, shared_player):
    full, half = two_leagues
    ProGame.objects.create(season=2026, week=8, team="BUF", opponent="MIA", kickoff=timezone.now() + timedelta(days=1))
    for league, points in ((full, 22.0), (half, 16.0)):
        Projection.objects.create(league=league, player=shared_player, season=2026, week=8, source="sleeper", points=points)

    call_command("snapshot_projections")

    assert ProjectionSnapshot.objects.get(league=full, player=shared_player).sleeper == 22.0
    assert ProjectionSnapshot.objects.get(league=half, player=shared_player).sleeper == 16.0


def test_snapshots_only_cover_this_leagues_pool(two_leagues, shared_player):
    full, half = two_leagues
    ProGame.objects.create(season=2026, week=8, team="BUF", opponent="MIA", kickoff=timezone.now() + timedelta(days=1))
    only_full = Player.objects.create(espn_id=501, name="Only Full", position="WR", pro_team="BUF")
    LeaguePlayer.objects.create(league=full, player=only_full)
    PlayerWeek.objects.create(league=full, player=only_full, season=2026, week=8, projected_points=9.0)

    call_command("snapshot_projections")

    assert ProjectionSnapshot.objects.filter(league=full, player=only_full).exists()
    assert not ProjectionSnapshot.objects.filter(league=half, player=only_full).exists()


def test_snapshots_reach_players_only_in_a_later_league(two_leagues, shared_player):
    _, half = two_leagues
    ProGame.objects.create(season=2026, week=8, team="BUF", opponent="MIA", kickoff=timezone.now() + timedelta(days=1))
    only_half = Player.objects.create(espn_id=501, name="Only Half", position="WR", pro_team="BUF")
    LeaguePlayer.objects.create(league=half, player=only_half)
    PlayerWeek.objects.create(league=half, player=only_half, season=2026, week=8, projected_points=9.0)

    call_command("snapshot_projections")

    assert ProjectionSnapshot.objects.filter(league=half, player=only_half).exists()


def test_accuracy_compares_each_league_with_its_own_results(two_leagues, shared_player):
    full, half = two_leagues
    for league, mean in ((full, 19.0), (half, 13.0)):
        ProjectionSnapshot.objects.create(league=league, player=shared_player, season=2026, week=7, mean=mean, sd=1.0)

    def result(league):
        [r] = ProjectionAccuracy()._results(league)
        return r.consensus, r.actual

    assert result(full) == (19.0, 20.0)
    assert result(half) == (13.0, 14.0)


def test_accuracy_leaves_out_other_leagues_snapshots(two_leagues, shared_player):
    full, half = two_leagues
    ProjectionSnapshot.objects.create(league=full, player=shared_player, season=2026, week=7, mean=19.0, sd=1.0)
    assert ProjectionAccuracy()._results(half) == []


def test_player_sheet_game_log_uses_this_leagues_points(two_leagues, shared_player):
    full, half = two_leagues
    consistency = lambda league: services._consistency(league, shared_player)[0]  # noqa: E731
    assert consistency(full) != consistency(half)


def test_roster_rows_carry_this_leagues_values(two_leagues, shared_player):
    full, half = two_leagues
    for league in two_leagues:
        RosterEntry.objects.create(team=league.teams.get(espn_id=1), player=shared_player, lineup_slot="BE")

    def row(league):
        _, rows = services.roster_spots(league.teams.get(espn_id=1), 8)
        return rows[shared_player.pk].player

    assert (row(full).season_projection, row(full).fa_status) == (100.0, "FREEAGENT")
    assert (row(half).season_projection, row(half).fa_status) == (60.0, "")


def test_pending_labels_only_cover_this_leagues_pool(two_leagues, shared_player):
    full, half = two_leagues
    only_half = Player.objects.create(espn_id=501, name="Only Half", position="WR", pro_team="BUF")
    LeaguePlayer.objects.create(league=half, player=only_half)
    PendingMove.objects.create(
        league=full,
        espn_id="m1",
        kind=PendingMove.TRADE,
        team_espn_id=1,
        items=[
            {"player_id": shared_player.espn_id, "from_team": 0, "to_team": 1, "type": "ADD"},
            {"player_id": only_half.espn_id, "from_team": 0, "to_team": 1, "type": "ADD"},
        ],
    )
    assert set(services.tied_up(full)) == {shared_player.pk}
    assert services.tied_up(half) == {}


def test_waivers_use_this_leagues_free_agents(two_leagues, shared_player, monkeypatch):
    monkeypatch.setattr(
        services,
        "weekly_projections",
        lambda league, players, weeks: {(p.pk, w): ProjectedPoints(10.0, 4.0) for p in players for w in weeks},
    )
    full, half = two_leagues
    names = lambda league: [r.player.name for r in services.waivers(league.teams.get(espn_id=1))["rows"]]  # noqa: E731
    assert names(full) == ["Shared Receiver"]
    assert names(half) == []


def test_trade_search_keeps_other_leagues_deals(two_leagues):
    full, half = two_leagues
    theirs = TradeDeals.objects.create(
        team=half.teams.get(espn_id=1), partner=half.teams.get(espn_id=2), version=deals.data_version(half), total=0, deals=[]
    )
    deals.best_deals(full.teams.get(espn_id=1))
    assert TradeDeals.objects.filter(pk=theirs.pk).exists()


def test_a_trade_search_clears_this_leagues_old_deals(two_leagues):
    full, _ = two_leagues
    old = TradeDeals.objects.create(
        team=full.teams.get(espn_id=1), partner=full.teams.get(espn_id=2), version="old", total=0, deals=[]
    )
    deals.best_deals(full.teams.get(espn_id=1))
    assert not TradeDeals.objects.filter(pk=old.pk).exists()


def test_another_leagues_sync_does_not_invalidate_deals(two_leagues):
    full, half = two_leagues
    before = deals.data_version(full)
    SyncRun.objects.create(league=half, step="league", ok=True)
    assert deals.data_version(full) == before


def test_a_shared_step_invalidates_every_leagues_deals(two_leagues):
    full, half = two_leagues
    before = deals.data_version(full), deals.data_version(half)
    SyncRun.objects.create(step="projections", ok=True)
    assert deals.data_version(full) != before[0]
    assert deals.data_version(half) != before[1]


def test_sync_banner_ignores_other_leagues_failures(two_leagues):
    full, half = two_leagues
    SyncRun.objects.create(league=full, step="league", ok=True)
    SyncRun.objects.create(league=half, step="league", ok=False, error="ESPNAccessDenied: nope")
    assert sync_problem(timezone.now(), full) == ""
    assert "ESPN rejected" in sync_problem(timezone.now(), half)


def test_pruning_runs_keeps_each_leagues_history(two_leagues, monkeypatch):
    full, half = two_leagues
    monkeypatch.setattr("league.management.commands.sync_all.KEEP_RUNS", 2)
    SyncRun.objects.create(league=half, step="league", ok=True)
    for _ in range(5):
        SyncRun.objects.create(league=full, step="league", ok=True)
        SyncRun.objects.create(step="projections", ok=True)

    SyncAll()._prune_runs()

    assert SyncRun.objects.filter(league=full).count() == 2
    assert SyncRun.objects.filter(league=half).count() == 1
    assert SyncRun.objects.filter(league__isnull=True).count() == 2
