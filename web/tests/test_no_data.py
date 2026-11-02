import pytest
from django.contrib.auth import get_user_model

from league.models import SyncRun

pytestmark = pytest.mark.django_db

LEAGUE_PATHS = ["/startsit/", "/startsit/moves/", "/waivers/", "/trades/", "/trades/best/", "/explain/startsit/"]


def log_in(client, **fields):
    client.force_login(get_user_model().objects.create_user("u", password="p", **fields))
    return client


@pytest.mark.parametrize("path", LEAGUE_PATHS)
def test_user_without_a_league_is_offered_connecting_one(client, path):
    log_in(client)
    resp = client.get(path)
    assert resp.status_code == 403  # not a 2xx, or the service worker would save it as the page
    assert b"not in a league yet" in resp.content
    assert b"takes an invite link" in resp.content


def test_sync_errors_are_not_shown_to_users_without_a_league(client):
    log_in(client)
    SyncRun.objects.create(step="league", ok=False, error="ESPNAccessDenied")
    assert b"ESPNAccessDenied" not in client.get("/startsit/").content


def test_staff_without_a_league_still_hear_the_last_sync_failed(client):
    log_in(client, is_staff=True)
    SyncRun.objects.create(step="league", ok=False, error="RuntimeError")
    assert b"RuntimeError" in client.get("/startsit/").content
