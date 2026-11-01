import pytest
from cryptography.fernet import Fernet

from league.models import League, LeaguePlayer, Membership, Player, PlayerWeek, Team


@pytest.fixture(autouse=True)
def credential_key(settings):
    settings.CREDENTIAL_KEYS = [Fernet.generate_key().decode()]
    settings.ESPN_S2 = settings.ESPN_SWID = None
    settings.GITHUB_DISPATCH_TOKEN = None


def make_league(espn_id: int, name: str, rec_points: float) -> League:
    league = League.objects.create(
        espn_id=espn_id,
        season=2026,
        name=name,
        team_count=2,
        current_week=8,
        reg_season_weeks=14,
        final_week=9,
        lineup_slots={"WR": 1, "BE": 1},
        scoring={"53": {"points": rec_points, "overrides": {}}},
    )
    Team.objects.create(league=league, espn_id=1, name=f"{name} Team 1", abbrev="ONE")
    Team.objects.create(league=league, espn_id=2, name=f"{name} Team 2", abbrev="TWO")
    return league


@pytest.fixture
def sign_in(client, django_user_model):
    """Log the test client in as a user who claims these teams."""

    def _sign_in(*teams, username="u"):
        user, _ = django_user_model.objects.get_or_create(username=username)
        for team in teams:
            Membership.objects.create(user=user, league=team.league, team=team)
        client.force_login(user)
        return client

    return _sign_in


@pytest.fixture
def two_leagues(db) -> tuple[League, League]:
    """Same season, different scoring: full PPR and half PPR."""
    return make_league(1, "Full", 1.0), make_league(2, "Half", 0.5)


@pytest.fixture
def shared_player(two_leagues) -> Player:
    """One real player, seen differently by each league."""
    full, half = two_leagues
    player = Player.objects.create(
        espn_id=500, name="Shared Receiver", position="WR", pro_team="BUF", eligible_slots=["WR", "BE"], sleeper_id="s500"
    )
    LeaguePlayer.objects.create(league=full, player=player, season_projection=100.0, fa_status="FREEAGENT")
    LeaguePlayer.objects.create(league=half, player=player, season_projection=60.0, fa_status="")
    for league, points, projected in ((full, 20.0, 18.0), (half, 14.0, 12.0)):
        PlayerWeek.objects.create(
            league=league, player=player, season=2026, week=7, points=points, breakdown={"rec": 5}
        )
        PlayerWeek.objects.create(
            league=league, player=player, season=2026, week=8, projected_points=projected, breakdown={}
        )
    return player
