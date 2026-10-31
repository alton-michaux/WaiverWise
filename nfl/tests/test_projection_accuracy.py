from io import StringIO

import pytest
from django.core.management import call_command

from league.models import League, Player, PlayerWeek
from nfl.management.commands.projection_accuracy import Command
from nfl.models import ProjectionSnapshot

pytestmark = pytest.mark.django_db

SEASON, WEEK = 2026, 8


@pytest.fixture
def league():
    return League.objects.create(
        espn_id=1, season=SEASON, name="L", team_count=2, current_week=WEEK, reg_season_weeks=14
    )


def game(espn_id, week=7, mean=10.0, points=12.0, played=True, position="WR"):
    player = Player.objects.create(espn_id=espn_id, name=f"P{espn_id}", position=position, pro_team="BUF")
    ProjectionSnapshot.objects.create(player=player, season=SEASON, week=week, mean=mean, sd=1.0, espn=mean, sleeper=mean)
    PlayerWeek.objects.create(player=player, season=SEASON, week=week, points=points, breakdown={"rec": 1} if played else {})


def output():
    out = StringIO()
    call_command("projection_accuracy", stdout=out)
    return out.getvalue()


def test_reports_error_per_source(league):
    game(1)
    report = output()
    assert "1 player-games" in report
    assert "consensus: 2.00 points" in report


def test_ruled_out_players_are_left_out(league):
    game(1, mean=0.0, points=0.0)
    assert Command()._results(league) == []


def test_players_who_did_not_play_are_left_out(league):
    game(1, played=False)
    assert Command()._results(league) == []


def test_the_current_week_is_left_out(league):
    game(1, week=WEEK)
    assert Command()._results(league) == []


def test_no_finished_weeks_says_so(league):
    assert "no finished weeks" in output()


def test_no_league_says_so():
    assert "no league" in output()
