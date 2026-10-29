from datetime import timedelta

import pytest
from django.utils import timezone

from league.models import SyncRun
from web.context_processors import sync_problem

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize("path", ["/startsit/", "/waivers/", "/trades/", "/explain/startsit/"])
def test_pages_require_login(client, path):
    resp = client.get(path)
    assert resp.status_code == 302
    assert resp.url.startswith("/login/")


@pytest.mark.parametrize("path", ["/health/", "/manifest.webmanifest", "/sw.js", "/offline/", "/login/"])
def test_public_endpoints(client, path):
    assert client.get(path).status_code == 200


def test_manifest_is_installable(client):
    data = client.get("/manifest.webmanifest").json()
    assert data["name"] == "WaiverWise"
    assert data["display"] == "standalone"
    assert {"192x192", "512x512"} <= {i["sizes"] for i in data["icons"]}
    assert any(i["purpose"] == "maskable" for i in data["icons"])


def test_service_worker_served_as_js_without_caching(client):
    resp = client.get("/sw.js")
    assert resp["Content-Type"].startswith("application/javascript")
    assert "no-cache" in resp["Cache-Control"]


def test_banner_for_rejected_espn_cookies():
    SyncRun.objects.create(step="league", ok=False, error="ESPNAccessDenied: League 1 cannot be accessed")
    assert "ESPN rejected" in sync_problem(timezone.now())


def test_banner_for_stale_data():
    SyncRun.objects.create(step="league", ok=True)
    assert sync_problem(timezone.now() - timedelta(hours=13))
    assert sync_problem(timezone.now() - timedelta(hours=1)) == ""


def test_recovered_sync_clears_banner():
    SyncRun.objects.create(step="league", ok=False, error="ESPNAccessDenied: nope")
    SyncRun.objects.create(step="league", ok=True)
    assert sync_problem(timezone.now()) == ""
