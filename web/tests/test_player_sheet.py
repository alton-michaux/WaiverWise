from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone

from analysis.matchups import GOOD, TOUGH, Rating
from league.models import League, Player, PlayerWeek, ProGame, Team
from nfl.consensus import playoff_weeks
from nfl.models import GameLine, Usage
from web import services

pytestmark = pytest.mark.django_db

SEASON, WEEK = 2026, 8


@pytest.fixture
def league(settings):
    settings.ESPN_MY_TEAM_ID = 1
    league = League.objects.create(
        espn_id=1, season=SEASON, name="L", team_count=2, current_week=WEEK, reg_season_weeks=14, final_week=17,
        lineup_slots={"WR": 1, "K": 1, "BE": 1},
    )
    Team.objects.create(league=league, espn_id=1, name="Mine", abbrev="M")
    return league


@pytest.fixture
def signed_in(client, league):
    client.force_login(get_user_model().objects.create_user("u", password="p"))
    return client


@pytest.fixture
def receiver(league):
    kickoff = timezone.now() + timedelta(days=1)
    for week in range(WEEK, 18):
        if week != 10:  # bye
            ProGame.objects.create(season=SEASON, week=week, team="BUF", opponent=f"O{week}", kickoff=kickoff)
    return Player.objects.create(espn_id=1, name="Keon Coleman", position="WR", pro_team="BUF")


def sheet(signed_in, player):
    return signed_in.get(f"/player/{player.pk}/").content.decode()


def test_full_sheet_renders_every_section(signed_in, receiver):
    GameLine.objects.create(
        season=SEASON, week=WEEK, team="BUF", is_home=False, spread=-3.0, total=48.0, implied_total=25.5, rest_days=13,
        roof="dome",
    )
    PlayerWeek.objects.create(player=receiver, season=SEASON, week=7, points=21.0, breakdown={"rec": 1})
    Usage.objects.create(player=receiver, season=SEASON, week=7, snap_pct=0.8, target_share=0.25, air_yards_share=0.3, targets=8)
    html = sheet(signed_in, receiver)
    for text in ("Keon Coleman", "@ O8", "25.5", "-3.0", "Off a bye", "Dome", "Rest of season", "Fantasy playoffs",
                 "25%", "30%", "80%", "1 of 1", "BYE", "W17"):
        assert text in html


def test_this_week_bye_says_so(signed_in, receiver):
    ProGame.objects.filter(team="BUF", week=WEEK).delete()
    assert "Bye this week." in sheet(signed_in, receiver)


def test_no_lines_yet_hides_them(signed_in, receiver):
    GameLine.objects.create(season=SEASON, week=WEEK, team="BUF", rest_days=7)
    html = sheet(signed_in, receiver)
    assert "Team implied total" not in html
    assert "Spread" not in html
    assert "<dt>Rest</dt>" not in html


@pytest.mark.parametrize("days,text", [(4, "Short week (4 days)"), (10, None), (13, "Off a bye")])
def test_rest_is_only_mentioned_when_unusual(signed_in, receiver, days, text):
    GameLine.objects.create(season=SEASON, week=WEEK, team="BUF", rest_days=days)
    html = sheet(signed_in, receiver)
    if text:
        assert text in html
    else:
        assert "<dt>Rest</dt>" not in html


def test_kicker_has_no_role_section(signed_in, league):
    kicker = Player.objects.create(espn_id=2, name="Tyler Bass", position="K", pro_team="BUF")
    assert "Role, last 3 games" not in sheet(signed_in, kicker)


def test_no_game_log_hides_consistency(signed_in, receiver):
    assert "Startable games" not in sheet(signed_in, receiver)


def test_game_not_played_is_left_out_of_consistency(league, receiver):
    PlayerWeek.objects.create(player=receiver, season=SEASON, week=6, points=0, breakdown={})
    PlayerWeek.objects.create(player=receiver, season=SEASON, week=7, points=9.0, breakdown={"rec": 1})
    assert services.player_sheet(league, receiver)["consistency"].games == 1


def test_schedule_splits_at_the_playoffs_with_the_bye_kept(league, receiver):
    ctx = services.player_sheet(league, receiver)
    assert [s.week for s in ctx["regular_schedule"]] == list(range(8, 15))
    assert [s.week for s in ctx["playoff_schedule"]] == [15, 16, 17]
    assert next(s for s in ctx["regular_schedule"] if s.week == 10).opponent is None


def test_grades_show_up_on_the_schedule_and_outlook(signed_in, league, receiver, monkeypatch):
    table = {("O8", "WR"): Rating(5.0, 1, GOOD), ("O15", "WR"): Rating(-5.0, 32, TOUGH)}
    monkeypatch.setattr(services, "defense_ratings", lambda league: table)
    ctx = services.player_sheet(league, receiver)
    assert ctx["regular_schedule"][0].grade == GOOD
    assert ctx["matchup"].grade == GOOD
    assert ctx["playoff_schedule"][0].grade == TOUGH
    assert 'class="grade-Good"' in sheet(signed_in, receiver)


def test_no_playoff_section_after_the_season(signed_in, league, receiver):
    league.current_week = 18
    league.save()
    html = sheet(signed_in, receiver)
    assert "Fantasy playoffs" not in html
    assert "Rest of season" not in html


def test_playoff_weeks_only_counts_whats_left(league):
    assert playoff_weeks(league) == range(15, 18)
    league.current_week = 16
    assert playoff_weeks(league) == range(16, 18)


def test_unknown_player_is_a_404(signed_in):
    assert signed_in.get("/player/999999/").status_code == 404


def test_sheet_requires_login(client, receiver):
    assert client.get(f"/player/{receiver.pk}/").status_code == 302
