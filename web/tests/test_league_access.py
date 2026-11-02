"""Two users in two leagues: nothing about one league may reach someone who isn't in it."""

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management import CommandError, call_command
from django.db import IntegrityError, transaction
from django.utils import timezone

from analysis.projections import Projection
from conftest import make_league
from league.models import League, LeaguePlayer, Membership, Player, RosterEntry, SyncRun, Team
from web import services
from web.context_processors import sync_problem
from web.services import league_cookie, team_cookie

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def fake_projections(monkeypatch):
    def fake(league, players, weeks):
        return {(p.pk, w): Projection(5.0, 2.0) for p in players for w in weeks}

    monkeypatch.setattr(services, "weekly_projections", fake)


@pytest.fixture
def leagues():
    full = make_league(1, "Full", 1.0)
    half = make_league(2, "Half", 0.5)
    Team.objects.create(league=half, espn_id=3, name="Only In Half", abbrev="OH")
    return full, half


def team_of(league, espn_id=1):
    return league.teams.get(espn_id=espn_id)


def add_player(league, espn_id, name, team=None):
    player = Player.objects.create(espn_id=espn_id, name=name, position="WR", pro_team="BUF", eligible_slots=["WR", "BE"])
    LeaguePlayer.objects.create(league=league, player=player)
    if team:
        RosterEntry.objects.create(team=team, player=player, lineup_slot="WR")
    return player


@pytest.mark.parametrize("path", ["/startsit/", "/waivers/", "/trades/"])
def test_pages_show_only_your_league(sign_in, leagues, path):
    full, _ = leagues
    client = sign_in(team_of(full))
    resp = client.get(path)
    assert resp.status_code == 200
    assert resp.context["team"].league == full
    assert b"Full Team 1" in resp.content
    assert b"Half Team" not in resp.content
    assert b"Only In Half" not in resp.content


def test_you_land_on_your_own_team(sign_in, leagues):
    full, _ = leagues
    client = sign_in(team_of(full, 2))
    assert client.get("/trades/").context["team"] == team_of(full, 2)


@pytest.mark.parametrize("path", ["/startsit/", "/startsit/moves/", "/waivers/", "/trades/", "/trades/best/", "/explain/startsit/"])
def test_team_param_cannot_reach_another_league(sign_in, leagues, path):
    full, _ = leagues
    client = sign_in(team_of(full))
    assert client.get(path, {"team": 3}).status_code == 404  # team 3 only exists in the other league


def test_same_team_number_means_your_league(sign_in, leagues):
    full, _ = leagues
    client = sign_in(team_of(full))
    assert client.get("/trades/", {"team": 2}).context["team"] == team_of(full, 2)


def test_team_cookie_cannot_reach_another_league(sign_in, leagues):
    full, _ = leagues
    client = sign_in(team_of(full))
    client.cookies[team_cookie(Membership.objects.get())] = "3"
    assert client.get("/trades/").context["team"] == team_of(full)


def test_partner_cannot_be_in_another_league(sign_in, leagues):
    full, _ = leagues
    client = sign_in(team_of(full))
    assert client.get("/trades/", {"view": "build", "partner": 3}).status_code == 404
    assert client.get("/trades/best/", {"partner": 3}).status_code == 404


def test_trade_cannot_name_players_from_another_league(sign_in, leagues):
    full, half = leagues
    mine = add_player(full, 10, "My Guy", team_of(full, 1))
    theirs = add_player(full, 11, "Their Guy", team_of(full, 2))
    foreign = add_player(half, 12, "Half Guy", team_of(half, 2))
    client = sign_in(team_of(full))
    resp = client.get("/trades/", {"partner": 2, "give": [mine.pk], "get": [foreign.pk]})
    assert resp.context["get"] == []
    assert resp.context["result"] is None
    assert b"Half Guy" not in resp.content
    assert client.get("/trades/", {"partner": 2, "give": [mine.pk], "get": [theirs.pk]}).context["get"] == [theirs.pk]


def test_player_sheet_stays_in_your_league(sign_in, leagues):
    full, half = leagues
    mine = add_player(full, 10, "My Guy")
    foreign = add_player(half, 12, "Half Guy")
    client = sign_in(team_of(full))
    assert client.get(f"/player/{mine.pk}/").status_code == 200
    assert client.get(f"/player/{foreign.pk}/").status_code == 404


def someone_else_joins(league):
    """A league with members is the case that matters: it's a real target, not just a missing row."""
    user = get_user_model().objects.create_user("someone-else")
    return Membership.objects.create(user=user, league=league, team=team_of(league))


def test_forged_league_cookie_is_ignored(sign_in, leagues):
    full, half = leagues
    someone_else_joins(half)
    client = sign_in(team_of(full))
    user = get_user_model().objects.get(username="u")
    client.cookies[league_cookie(user)] = str(half.pk)
    assert client.get("/trades/").context["team"].league == full


@pytest.mark.parametrize("value", ["garbage", "", "999"])
def test_bad_league_cookie_falls_back(sign_in, leagues, value):
    full, _ = leagues
    client = sign_in(team_of(full))
    client.cookies[league_cookie(get_user_model().objects.get(username="u"))] = value
    assert client.get("/trades/").context["team"].league == full


def test_cannot_switch_to_a_league_you_are_not_in(sign_in, leagues):
    full, half = leagues
    someone_else_joins(half)
    client = sign_in(team_of(full))
    resp = client.post("/league/switch/", {"league": half.pk})
    assert resp.status_code == 404
    assert not any(name.startswith("league_") for name in resp.cookies)
    assert client.get("/trades/").context["team"].league == full


@pytest.mark.parametrize("value", ["", "garbage", "999"])
def test_switch_rejects_bad_values(sign_in, leagues, value):
    client = sign_in(team_of(leagues[0]))
    assert client.post("/league/switch/", {"league": value}).status_code == 404


def test_switch_needs_a_post(sign_in, leagues):
    full, half = leagues
    client = sign_in(team_of(full), team_of(half))
    assert client.get("/league/switch/", {"league": half.pk}).status_code == 405


def test_switching_requires_login(client, leagues):
    resp = client.post("/league/switch/", {"league": leagues[0].pk})
    assert resp.status_code == 302 and resp.url.startswith("/login/")


def test_switching_changes_every_page_and_lands_on_startsit(sign_in, leagues):
    full, half = leagues
    client = sign_in(team_of(full), team_of(half, 2))
    resp = client.post("/league/switch/", {"league": half.pk})
    assert resp.status_code == 302 and resp.url == "/startsit/"
    for path in ["/startsit/", "/waivers/", "/trades/"]:
        assert client.get(path).context["team"] == team_of(half, 2)


def test_each_league_remembers_its_own_picked_team(sign_in, leagues):
    full, half = leagues
    client = sign_in(team_of(full), team_of(half))
    client.post("/league/switch/", {"league": full.pk})
    client.get("/trades/", {"team": 2})
    client.post("/league/switch/", {"league": half.pk})
    assert client.get("/trades/").context["team"] == team_of(half, 1)
    client.post("/league/switch/", {"league": full.pk})
    assert client.get("/trades/").context["team"] == team_of(full, 2)


def test_league_picker_only_appears_with_several_leagues(sign_in, leagues):
    full, half = leagues
    client = sign_in(team_of(full))
    assert b"/league/switch/" not in client.get("/trades/").content
    Membership.objects.create(user=get_user_model().objects.get(username="u"), league=half, team=team_of(half))
    html = client.get("/trades/").content
    assert b'action="/league/switch/" data-clear-pages' in html


def test_league_picker_lists_only_your_leagues(sign_in, leagues):
    full, half = leagues
    third = make_league(3, "Stranger", 1.0)
    someone_else_joins(third)
    html = sign_in(team_of(full), team_of(half)).get("/trades/").content
    assert b"Half" in html
    assert b"Stranger" not in html


def test_someone_else_on_this_browser_does_not_inherit_the_choice(client, sign_in, leagues):
    full, half = leagues
    sign_in(team_of(full), team_of(half), username="first")
    client.post("/league/switch/", {"league": half.pk})
    client.get("/trades/", {"team": 2})
    # no client.logout(): it would wipe the cookies this test is about
    # the browser still has both cookies, and they're in both leagues, so neither choice is off limits
    sign_in(team_of(full, 2), team_of(half, 3), username="second")
    assert client.get("/trades/").context["team"] == team_of(full, 2)
    client.post("/league/switch/", {"league": half.pk})
    assert client.get("/trades/").context["team"] == team_of(half, 3)


def test_removing_a_membership_cuts_access_even_with_the_cookie_set(sign_in, leagues):
    full, half = leagues
    client = sign_in(team_of(full), team_of(half))
    client.post("/league/switch/", {"league": half.pk})
    Membership.objects.filter(league=half).delete()
    assert client.get("/trades/").context["team"].league == full


def test_newest_season_is_the_default(sign_in, leagues):
    full, _ = leagues
    old = League.objects.create(espn_id=1, season=2025, name="Old", team_count=2, current_week=18, reg_season_weeks=14)
    client = sign_in(Team.objects.create(league=old, espn_id=1, name="Old Team", abbrev="OL"), team_of(full))
    assert client.get("/trades/").context["team"].league == full


def test_banner_comes_from_your_league_only(sign_in, leagues):
    full, half = leagues
    SyncRun.objects.create(league=full, step="league", ok=True)
    SyncRun.objects.create(league=half, step="league", ok=False, error="ESPNAccessDenied: nope")
    client = sign_in(team_of(full))
    assert b"ESPN rejected" not in client.get("/trades/").content


def test_users_without_a_league_get_no_banner(client, django_user_model):
    SyncRun.objects.create(step="league", ok=False, error="boom")
    client.force_login(django_user_model.objects.create_user("u"))
    assert b'role="alert"' not in client.get("/startsit/").content


def test_error_text_is_for_staff():
    SyncRun.objects.create(step="league", ok=False, error="RuntimeError")
    assert "RuntimeError" not in sync_problem(timezone.now())
    assert "RuntimeError" in sync_problem(timezone.now(), staff=True)


def test_rejected_logins_tell_members_who_can_fix_it():
    SyncRun.objects.create(step="league", ok=False, error="ESPNAccessDenied")
    assert "connecting their ESPN login" in sync_problem(timezone.now())


# --- membership rules


def test_team_must_be_in_the_league(leagues, django_user_model):
    full, half = leagues
    user = django_user_model.objects.create_user("u")
    with pytest.raises(ValidationError):
        Membership.objects.create(user=user, league=full, team=team_of(half))


def test_a_team_has_one_owner(leagues, django_user_model):
    full, _ = leagues
    Membership.objects.create(user=django_user_model.objects.create_user("a"), league=full, team=team_of(full))
    with pytest.raises(IntegrityError), transaction.atomic():
        Membership.objects.create(user=django_user_model.objects.create_user("b"), league=full, team=team_of(full))


def test_one_team_per_league_per_user(leagues, django_user_model):
    full, _ = leagues
    user = django_user_model.objects.create_user("u")
    Membership.objects.create(user=user, league=full, team=team_of(full, 1))
    with pytest.raises(IntegrityError), transaction.atomic():
        Membership.objects.create(user=user, league=full, team=team_of(full, 2))


# --- bootstrap_owner


def test_bootstrap_attaches_and_can_be_rerun(leagues, django_user_model):
    full, _ = leagues
    user = django_user_model.objects.create_user("owner")
    call_command("bootstrap_owner", "owner", "1", league=1)
    call_command("bootstrap_owner", "owner", "2", league=1)  # fixing a wrong team
    assert Membership.objects.get(user=user).team == team_of(full, 2)


def test_bootstrap_picks_the_only_league(db, django_user_model):
    league = make_league(1, "Only", 1.0)
    user = django_user_model.objects.create_user("owner")
    call_command("bootstrap_owner", "owner", "1")
    assert Membership.objects.get(user=user).league == league


@pytest.mark.parametrize(
    "args, kwargs, message",
    [
        (["ghost", "1"], {"league": 1}, "no user"),
        (["owner", "9"], {"league": 1}, "no team"),
        (["owner", "1"], {}, "several leagues"),
        (["owner", "1"], {"league": 77}, "no matching league"),
    ],
)
def test_bootstrap_errors(leagues, django_user_model, args, kwargs, message):
    django_user_model.objects.create_user("owner")
    with pytest.raises(CommandError, match=message):
        call_command("bootstrap_owner", *args, **kwargs)


def test_bootstrap_team_must_be_in_the_chosen_league(leagues, django_user_model):
    django_user_model.objects.create_user("owner")
    with pytest.raises(CommandError, match="no team"):
        call_command("bootstrap_owner", "owner", "3", league=1)  # team 3 is only in the other league


def test_bootstrap_will_not_take_someone_elses_team(leagues, django_user_model):
    full, _ = leagues
    Membership.objects.create(user=django_user_model.objects.create_user("a"), league=full, team=team_of(full))
    django_user_model.objects.create_user("owner")
    with pytest.raises(CommandError, match="already claimed"):
        call_command("bootstrap_owner", "owner", "1", league=1)
