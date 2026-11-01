import pytest

pytestmark = pytest.mark.django_db


def test_health_ignores_unknown_host(client):
    # platform health checks can arrive with the machine's internal address as Host
    resp = client.get("/health/", HTTP_HOST="172.19.0.5:8000")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_health_does_not_touch_the_database(client, django_assert_num_queries):
    # a query per check would keep Neon awake and use up the free plan's compute hours
    with django_assert_num_queries(0):
        assert client.get("/health/").status_code == 200


def test_other_paths_still_validate_host(client):
    assert client.get("/login/", HTTP_HOST="evil.example").status_code == 400
