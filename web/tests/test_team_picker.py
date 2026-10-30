from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone

from league.models import League, Team
from web.services import TEAM_COOKIE
from web.templatetags.waiverwise import short_age

pytestmark = pytest.mark.django_db


@pytest.fixture
def league(settings):
    settings.ESPN_MY_TEAM_ID = 1
    league = League.objects.create(
        espn_id=1, season=2026, name="L", team_count=2, current_week=8, reg_season_weeks=14
    )
    Team.objects.create(league=league, espn_id=1, name="My Team", abbrev="MY")
    Team.objects.create(league=league, espn_id=2, name="Rival Team", abbrev="RV")
    return league


@pytest.fixture
def signed_in(client, league):
    client.force_login(get_user_model().objects.create_user("u", password="p"))
    return client


def viewed_team(resp):
    return resp.context["team"].name


def test_defaults_to_my_team(signed_in):
    resp = signed_in.get("/trades/")
    assert viewed_team(resp) == "My Team"
    assert resp.context["picker"]["team"].name == "My Team"
    assert TEAM_COOKIE not in resp.cookies


def test_picked_team_shows_in_header_right_away(signed_in):
    resp = signed_in.get("/trades/", {"team": 2})
    assert viewed_team(resp) == "Rival Team"
    assert resp.context["picker"]["team"].name == "Rival Team"
    assert resp.cookies[TEAM_COOKIE].value == "2"


def test_picked_team_carries_across_tabs(signed_in):
    signed_in.get("/trades/", {"team": 2})
    assert viewed_team(signed_in.get("/trades/")) == "Rival Team"


def test_can_pick_my_team_again(signed_in):
    signed_in.get("/trades/", {"team": 2})
    signed_in.get("/trades/", {"team": 1})
    assert viewed_team(signed_in.get("/trades/")) == "My Team"


@pytest.mark.parametrize("value", ["99", "garbage", "-1"])
def test_bad_cookie_falls_back_to_my_team(signed_in, value):
    signed_in.cookies[TEAM_COOKIE] = value
    assert viewed_team(signed_in.get("/trades/")) == "My Team"


@pytest.mark.parametrize("value", ["99", "garbage"])
def test_bad_team_param_is_not_found(signed_in, value):
    resp = signed_in.get("/waivers/", {"team": value})
    assert resp.status_code == 404
    assert TEAM_COOKIE not in resp.cookies


def test_deal_requests_do_not_change_the_choice(signed_in):
    # one still running when you switch teams must not switch you back
    resp = signed_in.get("/trades/best/", {"team": 2})
    assert resp.status_code == 200
    assert TEAM_COOKIE not in resp.cookies


def test_picker_lists_my_team_first(signed_in):
    Team.objects.create(league=League.objects.get(), espn_id=3, name="Aardvarks", abbrev="AA")
    names = [t.name for t in signed_in.get("/trades/").context["picker"]["teams"]]
    assert names == ["My Team", "Aardvarks", "Rival Team"]


def test_empty_database_still_offers_sign_out(client):
    client.force_login(get_user_model().objects.create_user("u", password="p"))
    resp = client.get("/waivers/")
    assert resp.status_code == 503
    assert b"Sign out" in resp.content


@pytest.mark.parametrize(
    "age, expected",
    [(timedelta(minutes=5), "5m"), (timedelta(hours=3, minutes=48), "3h"), (timedelta(days=2, hours=1), "2d")],
)
def test_short_age(age, expected):
    assert short_age(timezone.now() - age) == expected
