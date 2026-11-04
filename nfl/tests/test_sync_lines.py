import polars as pl
import pytest

from nfl.management.commands import sync_nfl
from nfl.models import GameLine

pytestmark = pytest.mark.django_db


def schedule(**overrides):
    game = {
        "game_type": "REG",
        "week": 9,
        "home_team": "BAL",
        "away_team": "JAX",
        "location": "Home",
        "spread_line": 3.0,
        "total_line": 48.5,
        "home_rest": 4,
        "away_rest": 7,
        "roof": "outdoors",
    }
    return pl.DataFrame([{**game, **overrides}], schema_overrides={"spread_line": pl.Float64, "total_line": pl.Float64, "roof": pl.String})


def run(monkeypatch, frame):
    monkeypatch.setattr(sync_nfl.nflreadpy, "load_schedules", lambda season: frame)
    return sync_nfl.Command()._lines(2026)


def test_each_team_gets_its_side_of_the_game(monkeypatch):
    assert run(monkeypatch, schedule()) == 2

    home = GameLine.objects.get(team="BAL")
    away = GameLine.objects.get(team="JAX")
    assert (home.is_home, home.spread, home.implied_total, home.rest_days) == (True, -3.0, 25.75, 4)
    assert (away.is_home, away.spread, away.implied_total, away.rest_days) == (False, 3.0, 22.75, 7)
    assert home.total == away.total == 48.5
    assert home.roof == "outdoors"


def test_team_codes_are_mapped_to_espns(monkeypatch):
    run(monkeypatch, schedule(home_team="LA", away_team="WAS"))

    assert set(GameLine.objects.values_list("team", flat=True)) == {"LAR", "WSH"}


def test_game_without_lines_yet_still_saves(monkeypatch):
    run(monkeypatch, schedule(spread_line=None, total_line=None, roof=None))

    line = GameLine.objects.get(team="BAL")
    assert (line.spread, line.total, line.implied_total, line.roof) == (None, None, None, "")
    assert line.rest_days == 4


def test_neutral_site_has_no_home_team(monkeypatch):
    run(monkeypatch, schedule(location="Neutral"))

    assert set(GameLine.objects.values_list("is_home", flat=True)) == {None}


def test_rerun_updates_the_line(monkeypatch):
    run(monkeypatch, schedule())
    run(monkeypatch, schedule(total_line=50.5))

    assert GameLine.objects.count() == 2
    assert GameLine.objects.get(team="BAL").total == 50.5


def test_postseason_is_left_out(monkeypatch):
    assert run(monkeypatch, schedule(game_type="WC", week=19)) == 0
