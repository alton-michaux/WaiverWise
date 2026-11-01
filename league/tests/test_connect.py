"""The access rule: knowing a league id must never open a stored private league."""

import logging
from types import SimpleNamespace

import httpx
import pytest
from cryptography.fernet import Fernet
from django.core.exceptions import ImproperlyConfigured
from django.core.management import call_command
from espn_api.requests.espn_requests import ESPNAccessDenied, ESPNInvalidLeague

from league import connect, credentials, espn_client
from league.connect import ConnectError, NeedsCookies
from league.espn_client import LeagueInfo, TeamInfo
from league.models import EspnCredential, League, Membership, PendingMove, Team

pytestmark = pytest.mark.django_db

SWID = "{AAAAAAAA-0000-0000-0000-000000000001}"
OTHER_SWID = "{BBBBBBBB-0000-0000-0000-000000000002}"


def info(**fields):
    return LeagueInfo(**{**dict(
        espn_id=77, season=2026, name="Stored League", team_count=2, current_week=8, reg_season_weeks=14, final_week=17,
        scoring_type="H2H_POINTS", lineup_slots={"QB": 1, "BE": 1}, scoring={},
        teams=[TeamInfo(1, "Mine", "M", [SWID]), TeamInfo(2, "Theirs", "T", [OTHER_SWID])],
    ), **fields})


@pytest.fixture
def espn(monkeypatch):
    """Fake ESPN: which cookies open the league. None in `allowed` means it's public."""
    state = SimpleNamespace(allowed={SWID}, calls=[], info=info())

    def probe(league_id, season, espn_s2=None, swid=None):
        state.calls.append(swid)
        if None in state.allowed or swid in state.allowed:
            return state.info
        raise ESPNAccessDenied("no")

    monkeypatch.setattr(espn_client, "probe_league", probe)
    return state


@pytest.fixture
def alice(django_user_model):
    return django_user_model.objects.create_user("alice", password="p")


@pytest.fixture
def bob(django_user_model):
    return django_user_model.objects.create_user("bob", password="p")


# --- encryption


def test_cookies_are_stored_encrypted(espn, alice):
    league = connect.connect_league(alice, 77, "secret-s2", SWID)
    row = EspnCredential.objects.get(user=alice, league=league)
    assert "secret-s2" not in row.encrypted_s2
    assert SWID not in row.encrypted_swid
    assert row.cookies() == ("secret-s2", SWID)


def test_keys_can_rotate(settings):
    old = Fernet.generate_key().decode()
    settings.CREDENTIAL_KEYS = [old]
    token = credentials.encrypt("x")
    settings.CREDENTIAL_KEYS = [Fernet.generate_key().decode(), old]
    assert credentials.decrypt(token) == "x"


def test_saving_cookies_without_a_key_fails_clearly(espn, alice, settings):
    settings.CREDENTIAL_KEYS = []
    with pytest.raises(ImproperlyConfigured):
        connect.connect_league(alice, 77, "s2", SWID)
    assert not League.objects.exists()


def test_credential_text_never_includes_the_cookies(espn, alice):
    league = connect.connect_league(alice, 77, "secret-s2", SWID)
    row = EspnCredential.objects.get(league=league)
    assert "secret" not in str(row) and row.encrypted_s2 not in str(row)


# --- the access rule


def test_private_league_without_cookies_stores_nothing(espn, alice):
    with pytest.raises(NeedsCookies):
        connect.connect_league(alice, 77)
    assert not League.objects.exists()


def test_wrong_cookies_store_nothing(espn, alice):
    with pytest.raises(NeedsCookies):
        connect.connect_league(alice, 77, "s2", OTHER_SWID)
    assert not League.objects.exists()
    assert not EspnCredential.objects.exists()


def test_a_stored_league_needs_your_own_cookies_too(espn, alice, bob):
    league = connect.connect_league(alice, 77, "s2", SWID)
    with pytest.raises(NeedsCookies):
        connect.connect_league(bob, 77)
    with pytest.raises(NeedsCookies):
        connect.connect_league(bob, 77, "s2", OTHER_SWID)
    assert not connect.can_join(bob, league)
    assert not EspnCredential.objects.filter(user=bob).exists()


def test_a_stored_league_gives_the_same_answer_as_an_unknown_one(espn, alice, bob):
    def answer(user):
        with pytest.raises(NeedsCookies) as e:
            connect.connect_league(user, 77, "s2", OTHER_SWID)
        return str(e.value)

    before = answer(bob)
    connect.connect_league(alice, 77, "s2", SWID)
    assert answer(bob) == before


def test_someone_elses_saved_cookies_are_never_tried(espn, alice, bob):
    connect.connect_league(alice, 77, "s2", SWID)
    espn.calls.clear()
    with pytest.raises(NeedsCookies):
        connect.connect_league(bob, 77, "s2", OTHER_SWID)
    assert SWID not in espn.calls


def test_a_league_with_two_cookie_holders(espn, alice, bob):
    espn.allowed = {SWID, OTHER_SWID}
    league = connect.connect_league(alice, 77, "a", SWID)
    connect.connect_league(bob, 77, "b", OTHER_SWID)
    assert EspnCredential.objects.filter(league=league).count() == 2


def test_public_league_needs_nothing_and_stores_no_cookies(espn, alice, bob):
    espn.allowed = {None}
    league = connect.connect_league(alice, 77)
    assert league.is_public and not league.has_data
    assert connect.can_join(bob, league)
    connect.connect_league(bob, 77, "ignored-s2", SWID)
    assert not EspnCredential.objects.exists()


def test_reconnecting_replaces_expired_cookies(espn, alice):
    league = connect.connect_league(alice, 77, "old", SWID)
    EspnCredential.objects.get().mark_expired()
    connect.connect_league(alice, 77, "new", SWID)
    row = EspnCredential.objects.get(league=league)
    assert row.status == EspnCredential.OK and row.cookies()[0] == "new"


def test_a_league_going_private_is_noticed_at_connect(espn, alice):
    espn.allowed = {None}
    league = connect.connect_league(alice, 77)
    espn.allowed = {SWID}
    connect.connect_league(alice, 77, "s2", SWID)
    league.refresh_from_db()
    assert not league.is_public


@pytest.mark.parametrize("fields", [{"scoring_type": "ROTO"}, {"lineup_slots": {"LB": 1}}])
def test_unsupported_leagues_are_refused_with_nothing_stored(espn, alice, fields):
    espn.info = info(**fields)
    with pytest.raises(ConnectError):
        connect.connect_league(alice, 77, "s2", SWID)
    assert not League.objects.exists()
    assert not EspnCredential.objects.exists()


@pytest.mark.parametrize(
    "raised, message", [(ESPNInvalidLeague("x"), "doesn't have a league"), (espn_client.EspnUnavailable("x"), "Couldn't reach")]
)
def test_espn_problems_are_explained(monkeypatch, alice, raised, message):
    def boom(*a, **k):
        raise raised

    monkeypatch.setattr(espn_client, "probe_league", boom)
    with pytest.raises(ConnectError, match=message):
        connect.connect_league(alice, 77)


# --- teams


def test_own_team_comes_from_the_swid(espn, alice):
    league = connect.connect_league(alice, 77, "s2", SWID)
    assert connect.own_team(alice, league).espn_id == 1


def test_swid_case_does_not_matter(espn, alice):
    espn.allowed = {SWID.lower()}
    league = connect.connect_league(alice, 77, "s2", SWID.lower())
    assert connect.own_team(alice, league).espn_id == 1


def test_no_match_without_cookies_or_on_a_claimed_team(espn, alice, bob):
    league = connect.connect_league(alice, 77, "s2", SWID)
    assert connect.own_team(bob, league) is None
    Membership.objects.create(user=bob, league=league, team=league.teams.get(espn_id=1))
    assert connect.own_team(alice, league) is None


def test_co_owned_two_teams_means_picking(espn, alice):
    espn.info = info(teams=[TeamInfo(1, "A", "A", [SWID]), TeamInfo(2, "B", "B", [SWID])])
    league = connect.connect_league(alice, 77, "s2", SWID)
    assert connect.own_team(alice, league) is None


# --- leaving


@pytest.fixture
def shared_league(espn, alice, bob):
    espn.allowed = {SWID, OTHER_SWID}
    league = connect.connect_league(alice, 77, "a", SWID)
    connect.connect_league(bob, 77, "b", OTHER_SWID)
    Membership.objects.create(user=alice, league=league, team=league.teams.get(espn_id=1))
    Membership.objects.create(user=bob, league=league, team=league.teams.get(espn_id=2))
    PendingMove.objects.create(league=league, espn_id="a", kind=PendingMove.WAIVER, team_espn_id=1, owner=alice)
    PendingMove.objects.create(league=league, espn_id="b", kind=PendingMove.WAIVER, team_espn_id=2, owner=bob)
    PendingMove.objects.create(league=league, espn_id="t", kind=PendingMove.TRADE, team_espn_id=2)
    return league


def test_disconnect_removes_only_your_cookies_and_claims(shared_league, alice, bob):
    connect.disconnect(alice, shared_league)
    assert not EspnCredential.objects.filter(user=alice).exists()
    assert EspnCredential.objects.filter(user=bob).exists()
    assert set(PendingMove.objects.values_list("espn_id", flat=True)) == {"b", "t"}
    assert Membership.objects.filter(user=alice).exists()  # still in the league


def test_leaving_keeps_the_league_for_everyone_else(shared_league, alice):
    connect.leave_league(alice, shared_league)
    assert League.objects.filter(pk=shared_league.pk).exists()
    assert not Membership.objects.filter(user=alice).exists()
    assert not EspnCredential.objects.filter(user=alice).exists()


def test_last_one_out_deletes_the_league(shared_league, alice, bob):
    connect.leave_league(alice, shared_league)
    connect.leave_league(bob, shared_league)
    assert not League.objects.filter(pk=shared_league.pk).exists()
    assert not Team.objects.exists()


def test_deleting_an_account_removes_its_cookies_and_memberships(shared_league, alice, bob):
    connect.delete_account(alice)
    assert not EspnCredential.objects.filter(league=shared_league, user__username="alice").exists()
    assert Membership.objects.filter(league=shared_league).count() == 1
    assert set(PendingMove.objects.values_list("espn_id", flat=True)) == {"b", "t"}
    connect.delete_account(bob)
    assert not League.objects.exists()


def test_deleting_an_account_leaves_other_peoples_leagues_alone(espn, shared_league, alice, django_user_model):
    bystander = django_user_model.objects.create_user("c")
    other = League.objects.create(espn_id=9, season=2026, name="O", team_count=2, current_week=8, reg_season_weeks=14)
    Membership.objects.create(user=bystander, league=other, team=Team.objects.create(league=other, espn_id=1, name="X", abbrev="X"))
    connect.delete_account(alice)
    assert League.objects.filter(pk=other.pk).exists()


# --- probe against ESPN's responses


class FakeResponse:
    def __init__(self, status, body=None):
        self.status_code, self.body = status, body

    def json(self):
        return self.body


ESPN_BODY = {
    "scoringPeriodId": 9,
    "status": {"finalScoringPeriod": 17},
    "settings": {
        "name": "L",
        "size": 2,
        "scheduleSettings": {"matchupPeriodCount": 14},
        "scoringSettings": {"scoringType": "H2H_POINTS", "scoringItems": [{"statId": 53, "points": 1.0}]},
        "rosterSettings": {"lineupSlotCounts": {"0": 1, "20": 2, "6": 0}},
    },
    "teams": [{"id": 3, "name": "T", "abbrev": "TT", "owners": [SWID]}],
}


def test_probe_reads_the_league(monkeypatch):
    seen = {}

    def get(url, **kwargs):
        seen.update(kwargs, url=url)
        return FakeResponse(200, ESPN_BODY)

    monkeypatch.setattr(httpx, "get", get)
    result = espn_client.probe_league(5, 2026, "s2", SWID)
    assert (result.name, result.current_week, result.final_week, result.scoring_type) == ("L", 9, 17, "H2H_POINTS")
    assert result.lineup_slots == {"QB": 1, "BE": 2}
    assert result.teams[0].owner_ids == [SWID]
    assert seen["cookies"] == {"espn_s2": "s2", "SWID": SWID}
    assert seen["timeout"]
    assert "/leagues/5" in seen["url"]


def test_probe_without_cookies_sends_none(monkeypatch):
    seen = {}

    def get(url, **kwargs):
        seen.update(kwargs)
        return FakeResponse(200, ESPN_BODY)

    monkeypatch.setattr(httpx, "get", get)
    espn_client.probe_league(5, 2026)
    assert seen["cookies"] is None


@pytest.mark.parametrize(
    "status, raised",
    [(401, ESPNAccessDenied), (400, ESPNInvalidLeague), (404, ESPNInvalidLeague), (500, espn_client.EspnUnavailable)],
)
def test_probe_statuses(monkeypatch, status, raised):
    monkeypatch.setattr(httpx, "get", lambda *a, **k: FakeResponse(status))
    with pytest.raises(raised):
        espn_client.probe_league(5, 2026)


def test_probe_network_failure(monkeypatch):
    def boom(*a, **k):
        raise httpx.ConnectTimeout("secret-in-message")

    monkeypatch.setattr(httpx, "get", boom)
    with pytest.raises(espn_client.EspnUnavailable) as e:
        espn_client.probe_league(5, 2026, "s2", SWID)
    assert "secret" not in str(e.value)


# --- bootstrap_owner


def test_bootstrap_saves_cookies_from_settings(espn, alice, settings):
    league = League.objects.create(espn_id=1, season=2026, name="L", team_count=2, current_week=8, reg_season_weeks=14)
    Team.objects.create(league=league, espn_id=1, name="X", abbrev="X")
    settings.ESPN_S2, settings.ESPN_SWID = "s2", SWID
    call_command("bootstrap_owner", "alice", "1")
    assert EspnCredential.objects.get(user=alice, league=league).cookies() == ("s2", SWID)


def test_bootstrap_works_without_cookies(espn, alice):
    league = League.objects.create(espn_id=1, season=2026, name="L", team_count=2, current_week=8, reg_season_weeks=14)
    Team.objects.create(league=league, espn_id=1, name="X", abbrev="X")
    call_command("bootstrap_owner", "alice", "1")
    assert not EspnCredential.objects.exists()
    assert Membership.objects.filter(user=alice).exists()


def test_http_clients_do_not_log_league_addresses():
    assert logging.getLogger("httpx").getEffectiveLevel() >= logging.WARNING
    assert logging.getLogger("httpcore").getEffectiveLevel() >= logging.WARNING
