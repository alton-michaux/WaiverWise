from types import SimpleNamespace

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from league import espn_client
from league.models import League, LeaguePlayer, Player
from nfl.models import Projection
from league.support import check_supported


def data(scoring_type="H2H_POINTS", **slots):
    return SimpleNamespace(scoring_type=scoring_type, lineup_slots=slots or {"QB": 1, "RB": 2, "BE": 5})


def test_points_league_with_ordinary_slots_is_fine():
    assert check_supported(data()) == []


@pytest.mark.parametrize("slot", ["QB", "RB", "WR", "TE", "RB/WR", "WR/TE", "RB/WR/TE", "OP", "D/ST", "K", "BE", "IR"])
def test_every_slot_we_can_project_is_allowed(slot):
    assert check_supported(data(**{slot: 1})) == []


@pytest.mark.parametrize("scoring_type", ["H2H_CATEGORY", "ROTO", "H2H_MOST_CATEGORIES", "TOTAL_SEASON_POINTS", ""])
def test_other_scoring_types_are_rejected(scoring_type):
    problems = check_supported(data(scoring_type))
    assert len(problems) == 1 and "head-to-head points" in problems[0]


@pytest.mark.parametrize("slot", ["LB", "DL", "DB", "DT", "DE", "CB", "S", "DP", "ER", "TQB", "P", "HC", "Rookie"])
def test_slots_we_cannot_project_are_rejected(slot):
    problems = check_supported(data(QB=1, **{slot: 1}))
    assert len(problems) == 1 and slot in problems[0]


def test_every_problem_is_reported():
    assert len(check_supported(data("ROTO", LB=1))) == 2


def league_data(**fields):
    return espn_client.LeagueData(
        espn_id=1, season=2026, name="L", team_count=2, current_week=8, reg_season_weeks=14, final_week=17,
        lineup_slots=fields.pop("lineup_slots", {"QB": 1, "BE": 1}), scoring=fields.pop("scoring", {}),
        teams=[], players=[], pro_games=[], pending=[], **fields,
    )


@pytest.fixture
def waiting_league(db):
    return League.objects.create(
        espn_id=1, season=2026, name="L", team_count=2, current_week=8, reg_season_weeks=14, is_public=True, has_data=False
    )


def test_sync_refuses_an_unsupported_league_before_saving_anything(monkeypatch, waiting_league):
    monkeypatch.setattr(espn_client, "fetch_league", lambda *a: league_data(scoring_type="ROTO"))
    with pytest.raises(CommandError, match="head-to-head points"):
        call_command("sync_league", waiting_league.pk)
    waiting_league.refresh_from_db()
    assert not waiting_league.has_data


def test_sync_reports_rules_it_cannot_project(monkeypatch, waiting_league, capsys):
    scoring = {"17": {"points": 2.0, "overrides": {}}}
    monkeypatch.setattr(espn_client, "fetch_league", lambda *a: league_data(scoring=scoring))
    call_command("sync_league", waiting_league.pk)
    out = capsys.readouterr().out
    assert "1 scoring rules have no Sleeper projection" in out
    assert "17" not in out.split("waiver claims")[1]


@pytest.mark.django_db
@pytest.mark.parametrize("position, stats, expected", [("TE", {"rec": 4}, 6.0), ("WR", {"rec": 4}, 4.0), ("D/ST", {"int": 2}, 4.0)])
def test_projection_sync_scores_by_position(monkeypatch, position, stats, expected):
    league = League.objects.create(
        espn_id=1, season=2026, name="L", team_count=2, current_week=8, reg_season_weeks=14, final_week=8,
        scoring={"53": {"points": 1.0, "overrides": {"6": 1.5}}, "95": {"points": 0.0, "overrides": {"16": 2.0}}},
    )
    player = Player.objects.create(espn_id=1, name="P", position=position, pro_team="BUF", sleeper_id="s1")
    LeaguePlayer.objects.create(league=league, player=player)
    monkeypatch.setattr("nfl.sleeper.fetch_week", lambda season, week: [{"player_id": "s1", "stats": stats}])
    call_command("sync_projections")
    assert Projection.objects.get(league=league, player=player).points == expected
