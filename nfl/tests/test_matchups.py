import pytest

from league.models import League, Player, PlayerWeek, ProGame
from nfl.matchups import defense_ratings

pytestmark = pytest.mark.django_db

SEASON, WEEK = 2026, 8


@pytest.fixture
def league():
    return League.objects.create(
        espn_id=1, season=SEASON, name="L", team_count=2, current_week=WEEK, reg_season_weeks=14
    )


def add_week(player, week, points, opponent, played=True):
    ProGame.objects.get_or_create(
        season=SEASON, week=week, team=player.pro_team, defaults={"opponent": opponent}
    )
    PlayerWeek.objects.create(
        player=player, season=SEASON, week=week, points=points, breakdown={"rec": 1} if played else {}
    )


def player(espn_id, team, position="WR"):
    return Player.objects.create(espn_id=espn_id, name=f"P{espn_id}", position=position, pro_team=team)


def faced_mia_three_times(p, points=20.0):
    for week in (1, 2, 3):
        add_week(p, week, points, "MIA")
    for week in (4, 5, 6):
        add_week(p, week, 10.0, "KC")


def test_soft_defense_rates_above_tough_defense(league):
    faced_mia_three_times(player(1, "BUF"))

    rated = defense_ratings(league)

    assert rated[("MIA", "WR")].points > 0 > rated[("KC", "WR")].points


def test_teammates_are_added_into_one_game(league):
    faced_mia_three_times(player(1, "BUF"))
    alone = defense_ratings(league)[("MIA", "WR")].points

    faced_mia_three_times(player(2, "BUF"))

    assert defense_ratings(league)[("MIA", "WR")].points == pytest.approx(alone * 2, abs=0.02)


def test_players_who_did_not_play_are_left_out(league):
    faced_mia_three_times(player(1, "BUF"))
    alone = defense_ratings(league)

    benched = player(2, "BUF")
    for week in range(1, 7):
        PlayerWeek.objects.create(player=benched, season=SEASON, week=week, points=50.0, breakdown={})

    assert defense_ratings(league) == alone


def test_the_current_week_is_left_out(league):
    p = player(1, "BUF")
    faced_mia_three_times(p)
    alone = defense_ratings(league)

    add_week(p, WEEK, 99.0, "MIA")

    assert defense_ratings(league) == alone


def test_a_players_new_team_without_that_game_is_skipped(league):
    p = player(1, "BUF")
    faced_mia_three_times(p)
    alone = defense_ratings(league)

    PlayerWeek.objects.create(player=p, season=SEASON, week=7, points=99.0, breakdown={"rec": 1})  # bye week

    assert defense_ratings(league) == alone
