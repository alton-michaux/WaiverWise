from datetime import timedelta

import pytest
from django.core.management import call_command
from django.utils import timezone

from league.models import League, Player, PlayerWeek, ProGame
from nfl.models import GameLine, Projection, ProjectionSnapshot

pytestmark = pytest.mark.django_db

SEASON, WEEK = 2026, 8


@pytest.fixture
def league():
    return League.objects.create(
        espn_id=1, season=SEASON, name="L", team_count=2, current_week=WEEK, reg_season_weeks=14
    )


def add_game(team, opponent, kickoff, week=WEEK):
    ProGame.objects.create(season=SEASON, week=week, team=team, opponent=opponent, kickoff=kickoff)


def add_player(espn_id, pro_team, espn=None, sleeper=None):
    player = Player.objects.create(espn_id=espn_id, name=f"P{espn_id}", position="WR", pro_team=pro_team)
    if espn is not None:
        PlayerWeek.objects.create(player=player, season=SEASON, week=WEEK, projected_points=espn)
    if sleeper is not None:
        Projection.objects.create(player=player, season=SEASON, week=WEEK, source="sleeper", points=sleeper)
    return player


def later():
    return timezone.now() + timedelta(days=1)


def test_saves_consensus_and_both_sources(league):
    add_game("BUF", "MIA", later())
    player = add_player(1, "BUF", espn=10.0, sleeper=14.0)

    call_command("snapshot_projections")

    snap = ProjectionSnapshot.objects.get(player=player, season=SEASON, week=WEEK)
    assert (snap.espn, snap.sleeper, snap.mean, snap.opponent) == (10.0, 14.0, 12.0, "MIA")
    assert snap.sd > 0


def test_one_missing_source_is_stored_as_null(league):
    add_game("BUF", "MIA", later())
    player = add_player(1, "BUF", sleeper=14.0)

    call_command("snapshot_projections")

    snap = ProjectionSnapshot.objects.get(player=player)
    assert (snap.espn, snap.mean) == (None, 14.0)


def test_rerun_refreshes_a_game_not_started(league):
    add_game("BUF", "MIA", later())
    player = add_player(1, "BUF", sleeper=14.0)
    call_command("snapshot_projections")

    Projection.objects.filter(player=player).update(points=20.0)
    call_command("snapshot_projections")

    assert ProjectionSnapshot.objects.get(player=player).mean == 20.0


def test_kickoff_freezes_the_last_value(league):
    add_game("BUF", "MIA", later())
    player = add_player(1, "BUF", sleeper=14.0)
    call_command("snapshot_projections")

    ProGame.objects.filter(team="BUF").update(kickoff=timezone.now() - timedelta(minutes=1))
    Projection.objects.filter(player=player).update(points=20.0)
    call_command("snapshot_projections")

    assert ProjectionSnapshot.objects.get(player=player).mean == 14.0


def test_started_game_with_no_earlier_snapshot_is_skipped(league):
    add_game("BUF", "MIA", timezone.now() - timedelta(minutes=1))
    add_player(1, "BUF", sleeper=14.0)

    call_command("snapshot_projections")

    assert not ProjectionSnapshot.objects.exists()


def test_bye_is_skipped(league):
    add_game("BUF", "MIA", later(), week=WEEK + 1)  # no game this week
    add_player(1, "BUF", sleeper=14.0)

    call_command("snapshot_projections")

    assert not ProjectionSnapshot.objects.exists()


def test_no_league_is_a_no_op():
    call_command("snapshot_projections")

    assert not ProjectionSnapshot.objects.exists()


def test_saves_the_line_and_rating_as_of_this_week(league):
    add_game("BUF", "MIA", later())
    player = add_player(1, "BUF", sleeper=14.0)
    GameLine.objects.create(season=SEASON, week=WEEK, team="BUF", implied_total=27.5)
    for week in (1, 2, 3):
        add_game("BUF", "MIA", later(), week=week)
        PlayerWeek.objects.create(player=player, season=SEASON, week=week, points=20.0, breakdown={"rec": 1})
    for week in (4, 5):
        add_game("BUF", "KC", later(), week=week)
        PlayerWeek.objects.create(player=player, season=SEASON, week=week, points=10.0, breakdown={"rec": 1})

    call_command("snapshot_projections")

    snap = ProjectionSnapshot.objects.get(player=player)
    assert snap.implied_total == 27.5
    assert snap.opp_rating > 0


def test_no_line_or_rating_is_stored_as_null(league):
    add_game("BUF", "MIA", later())
    player = add_player(1, "BUF", sleeper=14.0)

    call_command("snapshot_projections")

    snap = ProjectionSnapshot.objects.get(player=player)
    assert (snap.implied_total, snap.opp_rating) == (None, None)
