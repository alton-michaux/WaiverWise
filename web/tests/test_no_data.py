import pytest
from django.contrib.auth import get_user_model

from league.models import League, SyncRun

pytestmark = pytest.mark.django_db

LEAGUE_PATHS = ["/startsit/", "/startsit/moves/", "/waivers/", "/trades/", "/trades/best/", "/explain/startsit/"]


def log_in(client, **fields):
    client.force_login(get_user_model().objects.create_user("u", password="p", **fields))
    return client


@pytest.mark.parametrize("path", LEAGUE_PATHS)
def test_user_without_a_league_is_told_who_to_ask(client, path):
    log_in(client)
    resp = client.get(path)
    assert resp.status_code == 403  # not a 2xx, or the service worker would save it as the page
    assert b"not in a league yet" in resp.content


def test_operator_instructions_are_for_staff_only(client):
    log_in(client)
    League.objects.create(espn_id=1, season=2026, name="L", team_count=2, current_week=8, reg_season_weeks=14)
    content = client.get("/waivers/").content
    assert b"bootstrap_owner" not in content
    assert b"sync_all" not in content


def test_staff_on_an_empty_database_see_how_to_sync(client):
    log_in(client, is_staff=True)
    resp = client.get("/startsit/")
    assert resp.status_code == 403
    assert b"No league data yet" in resp.content


def test_staff_after_the_first_sync_see_how_to_claim_a_team(client):
    log_in(client, is_staff=True)
    League.objects.create(espn_id=1, season=2026, name="L", team_count=2, current_week=8, reg_season_weeks=14)
    assert b"bootstrap_owner u" in client.get("/startsit/").content


def test_sync_errors_are_not_shown_to_users_without_a_league(client):
    log_in(client)
    SyncRun.objects.create(step="league", ok=False, error="ESPNAccessDenied: secret detail")
    assert b"secret detail" not in client.get("/startsit/").content


def test_staff_without_a_league_still_hear_the_first_sync_failed(client):
    log_in(client, is_staff=True)
    SyncRun.objects.create(step="league", ok=False, error="boom detail")
    assert b"boom detail" in client.get("/startsit/").content
