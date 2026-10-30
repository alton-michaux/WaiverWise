import pytest
from django.contrib.auth import get_user_model

from league.models import League

pytestmark = pytest.mark.django_db


@pytest.fixture
def signed_in(client):
    client.force_login(get_user_model().objects.create_user("u", password="p"))
    return client


@pytest.mark.parametrize("path", ["/startsit/", "/waivers/", "/trades/"])
def test_empty_database_shows_setup_page(signed_in, path):
    resp = signed_in.get(path)
    assert resp.status_code == 503
    assert b"No league data yet" in resp.content


def test_unknown_team_id_explained(signed_in, settings):
    settings.ESPN_MY_TEAM_ID = 99
    League.objects.create(
        espn_id=1, season=2026, name="L", team_count=10, current_week=8, reg_season_weeks=14
    )
    resp = signed_in.get("/startsit/")
    assert resp.status_code == 503
    assert b"ESPN_MY_TEAM_ID=99" in resp.content
