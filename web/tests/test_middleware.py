import pytest

pytestmark = pytest.mark.django_db


def test_health_ignores_unknown_host(client):
    # Fly health checks arrive with the machine's internal address as Host
    resp = client.get("/health/", HTTP_HOST="172.19.0.5:8000")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_other_paths_still_validate_host(client):
    assert client.get("/login/", HTTP_HOST="evil.example").status_code == 400
