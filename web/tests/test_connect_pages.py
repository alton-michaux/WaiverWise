import logging
from types import SimpleNamespace

import httpx
import pytest
from django.contrib.auth import get_user_model
from espn_api.requests.espn_requests import ESPNAccessDenied

from analysis.projections import Projection
from league import connect, credentials, espn_client
from league.espn_client import LeagueInfo, TeamInfo
from league.models import EspnCredential, League, LeaguePlayer, Membership, PendingMove, Player, RosterEntry, SyncRun, Team
from web import deals, dispatch, services
from web.models import TradeDeals
from web.services import league_cookie

pytestmark = pytest.mark.django_db

SWID = "{AAAAAAAA-0000-0000-0000-000000000001}"
OTHER = "{BBBBBBBB-0000-0000-0000-000000000002}"
S2 = "secret-s2-value"


@pytest.fixture
def espn(monkeypatch):
    state = SimpleNamespace(allowed={SWID})

    def probe(league_id, season, espn_s2=None, swid=None):
        if None in state.allowed or swid in state.allowed:
            return LeagueInfo(
                espn_id=league_id, season=season, name="Private Name", team_count=2, current_week=8, reg_season_weeks=14, final_week=17,
                scoring_type="H2H_POINTS", lineup_slots={"QB": 1, "BE": 1}, scoring={},
                teams=[TeamInfo(1, "Team One", "A", [SWID]), TeamInfo(2, "Team Two", "B", [OTHER])],
            )
        raise ESPNAccessDenied("no")

    monkeypatch.setattr(espn_client, "probe_league", probe)
    return state


@pytest.fixture
def dispatched(monkeypatch):
    calls = []
    monkeypatch.setattr(dispatch, "request_first_sync", lambda pk: calls.append(pk) or True)
    return calls


@pytest.fixture
def user(client, django_user_model):
    user = django_user_model.objects.create_user("u", password="right")
    client.force_login(user)
    return user


# --- connecting


def test_connect_requires_login(client):
    assert client.get("/connect/").status_code == 302
    assert client.post("/connect/", {"league": "77"}).status_code == 302


def test_private_league_asks_for_cookies_and_reveals_nothing(client, user, espn):
    resp = client.post("/connect/", {"league": "77"})
    assert resp.status_code == 200
    assert b'name="espn_s2"' in resp.content
    assert b"your ESPN login" in resp.content
    assert b"Private Name" not in resp.content and b"Team One" not in resp.content
    assert not League.objects.exists()


def test_stored_private_league_is_not_opened_by_its_id(client, user, espn, django_user_model):
    connect.connect_league(django_user_model.objects.create_user("owner"), 77, "s", SWID)
    resp = client.post("/connect/", {"league": "77"})
    assert b'name="espn_s2"' in resp.content
    assert b"Private Name" not in resp.content and b"Team One" not in resp.content


def test_address_of_a_league_page_works(client, user, espn):
    espn.allowed = {None}
    resp = client.post("/connect/", {"league": "https://fantasy.espn.com/football/team?leagueId=77&teamId=1"})
    assert resp.status_code == 302
    assert League.objects.get().espn_id == 77


@pytest.mark.parametrize("value", ["abc", "", "1" * 19, "https://example.com/"])
def test_bad_league_values(client, user, espn, value):
    assert client.post("/connect/", {"league": value}).status_code == 400
    assert not League.objects.exists()


def test_wrong_cookies_get_a_clear_message_and_are_not_echoed(client, user, espn):
    resp = client.post("/connect/", {"league": "77", "espn_s2": S2, "swid": OTHER})
    assert resp.status_code == 403
    assert b"didn&#x27;t accept" in resp.content
    assert S2.encode() not in resp.content
    assert OTHER.encode() not in resp.content


def test_malformed_swid_is_not_echoed(client, user, espn):
    resp = client.post("/connect/", {"league": "77", "espn_s2": S2, "swid": "not-a-swid"})
    assert resp.status_code == 400
    assert b"not-a-swid" not in resp.content
    assert S2.encode() not in resp.content


@pytest.mark.parametrize("swid", [SWID, SWID[1:-1], "%7B" + SWID[1:-1] + "%7D"])
def test_swid_is_accepted_however_it_was_copied(client, user, espn, swid):
    resp = client.post("/connect/", {"league": "77", "espn_s2": S2, "swid": swid})
    assert resp.status_code == 302
    assert EspnCredential.objects.get().cookies() == (S2, SWID)


def test_cookies_stay_out_of_logs(client, user, espn, caplog):
    with caplog.at_level(logging.DEBUG):
        client.post("/connect/", {"league": "77", "espn_s2": S2, "swid": SWID})
    assert S2 not in caplog.text


def test_cookie_holder_lands_on_their_own_team(client, user, espn, dispatched):
    resp = client.post("/connect/", {"league": "77", "espn_s2": S2, "swid": SWID}, follow=True)
    league = League.objects.get()
    assert Membership.objects.get(user=user).team.espn_id == 1
    assert dispatched == [league.pk]
    assert resp.redirect_chain[-1][0] == "/startsit/"
    assert resp.status_code == 503  # first sync hasn't landed
    assert b"Setting up your league" in resp.content


# --- picking a team


def private_league(owner_name="owner"):
    owner = get_user_model().objects.create_user(owner_name)
    league = League.objects.create(espn_id=77, season=2026, name="Private Name", team_count=2, current_week=8, reg_season_weeks=14)
    Team.objects.create(league=league, espn_id=1, name="Team One", abbrev="A", owner_ids=[SWID])
    Team.objects.create(league=league, espn_id=2, name="Team Two", abbrev="B", owner_ids=[OTHER])
    EspnCredential.objects.create(user=owner, league=league, encrypted_s2=credentials.encrypt("x"), encrypted_swid=credentials.encrypt(SWID))
    return league


@pytest.mark.parametrize("method", ["get", "post"])
def test_team_page_of_a_private_league_is_closed_without_your_own_cookies(client, user, method):
    league = private_league()
    resp = getattr(client, method)(f"/connect/{league.pk}/team/", {"team": 2})
    assert resp.status_code == 404
    assert b"Team One" not in resp.content
    assert not Membership.objects.exists()


def test_closed_and_missing_leagues_look_the_same(client, user):
    league = private_league()
    closed = client.get(f"/connect/{league.pk}/team/")
    missing = client.get(f"/connect/{league.pk + 999}/team/")
    assert closed.status_code == missing.status_code == 404


def test_public_league_lets_you_pick_an_open_team(client, user, dispatched):
    league = League.objects.create(espn_id=7, season=2026, name="P", team_count=2, current_week=8, reg_season_weeks=14, is_public=True, has_data=False)
    Team.objects.create(league=league, espn_id=1, name="Team One", abbrev="A")
    resp = client.get(f"/connect/{league.pk}/team/")
    assert b"Team One" in resp.content
    resp = client.post(f"/connect/{league.pk}/team/", {"team": 1})
    assert resp.status_code == 302
    assert Membership.objects.get(user=user).team.name == "Team One"
    assert resp.cookies[league_cookie(user)].value == str(league.pk)


def test_claimed_teams_are_neither_offered_nor_takeable(client, user, django_user_model, dispatched):
    league = League.objects.create(espn_id=7, season=2026, name="P", team_count=2, current_week=8, reg_season_weeks=14, is_public=True)
    taken = Team.objects.create(league=league, espn_id=1, name="Taken Team", abbrev="A")
    Team.objects.create(league=league, espn_id=2, name="Open Team", abbrev="B")
    Membership.objects.create(user=django_user_model.objects.create_user("other"), league=league, team=taken)
    resp = client.get(f"/connect/{league.pk}/team/")
    assert b"Taken Team" not in resp.content and b"Open Team" in resp.content
    resp = client.post(f"/connect/{league.pk}/team/", {"team": 1})
    assert resp.status_code == 200 and b"Pick another" in resp.content
    assert not Membership.objects.filter(user=user).exists()


def test_a_team_from_another_league_cannot_be_claimed(client, user):
    league = League.objects.create(espn_id=7, season=2026, name="P", team_count=2, current_week=8, reg_season_weeks=14, is_public=True)
    Team.objects.create(league=league, espn_id=1, name="Here", abbrev="A")
    other = private_league()
    resp = client.post(f"/connect/{league.pk}/team/", {"team": 2})  # team 2 only exists in the other league
    assert not Membership.objects.filter(user=user).exists()
    assert resp.status_code == 200
    assert other.teams.get(espn_id=2).name.encode() not in resp.content


@pytest.mark.parametrize("value", ["", "abc", "-1", "99999999999"])
def test_bad_team_values(client, user, value):
    league = League.objects.create(espn_id=7, season=2026, name="P", team_count=2, current_week=8, reg_season_weeks=14, is_public=True)
    Team.objects.create(league=league, espn_id=1, name="Here", abbrev="A")
    resp = client.post(f"/connect/{league.pk}/team/", {"team": value})
    assert not Membership.objects.exists()
    assert resp.status_code in (200, 400)


# --- waiting for the first sync


@pytest.fixture
def waiting(client, user):
    league = League.objects.create(espn_id=7, season=2026, name="P", team_count=2, current_week=8, reg_season_weeks=14, is_public=True, has_data=False)
    team = Team.objects.create(league=league, espn_id=1, name="Here", abbrev="A")
    Membership.objects.create(user=user, league=league, team=team)
    return league


@pytest.mark.parametrize("path", ["/startsit/", "/waivers/", "/trades/", "/trades/best/", "/startsit/moves/", "/explain/startsit/", "/player/1/"])
def test_pages_wait_for_the_first_sync(client, waiting, path):
    resp = client.get(path)
    assert resp.status_code == 503  # not a 2xx, so the service worker won't save it
    assert b"Setting up your league" in resp.content


def test_waiting_page_moves_on_when_data_lands(client, waiting):
    assert b"HX-Redirect" not in client.get("/league/status/").content
    assert "HX-Redirect" not in client.get("/league/status/")
    League.objects.update(has_data=True)
    assert client.get("/league/status/")["HX-Redirect"] == "/startsit/"


def test_waiting_page_says_when_the_sync_failed(client, waiting):
    SyncRun.objects.create(league=waiting, step="league", ok=False, error="ESPNAccessDenied")
    assert b"didn't work" in client.get("/league/status/").content


def test_status_needs_a_league(client, user):
    assert client.get("/league/status/").status_code == 404


def test_status_requires_login(client):
    assert client.get("/league/status/").status_code == 302


# --- starting the first sync


def test_first_sync_needs_a_token(settings, monkeypatch):
    def fail(*a, **k):
        raise AssertionError("should not call GitHub")

    monkeypatch.setattr(httpx, "post", fail)
    assert dispatch.request_first_sync(1) is False


def test_first_sync_asks_github_for_this_league_only(settings, monkeypatch):
    settings.GITHUB_DISPATCH_TOKEN = "tok"
    seen = {}

    def post(url, **kwargs):
        seen.update(kwargs, url=url)
        return httpx.Response(204, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "post", post)
    assert dispatch.request_first_sync(12) is True
    assert seen["json"] == {"ref": "main", "inputs": {"league": "12"}}
    assert seen["url"].endswith("/actions/workflows/sync.yml/dispatches")
    assert seen["headers"]["Authorization"] == "Bearer tok"


def test_github_failing_does_not_break_connecting(settings, monkeypatch, caplog):
    settings.GITHUB_DISPATCH_TOKEN = "secret-token"

    def post(url, **kwargs):
        raise httpx.ConnectError("secret-token in message")

    monkeypatch.setattr(httpx, "post", post)
    assert dispatch.request_first_sync(12) is False
    assert "secret-token" not in caplog.text


# --- account page


def member_with_cookies(client, user, espn):
    league = connect.connect_league(user, 77, S2, SWID)
    Membership.objects.create(user=user, league=league, team=league.teams.get(espn_id=1))
    client.force_login(user)
    return league


def test_account_lists_your_league_without_any_cookie_text(client, user, espn):
    member_with_cookies(client, user, espn)
    html = client.get("/account/").content.decode()
    assert "Private Name" in html and "Team One" in html
    assert S2 not in html and SWID not in html
    assert EspnCredential.objects.get().encrypted_s2 not in html


def test_disconnect_deletes_your_login(client, user, espn):
    league = member_with_cookies(client, user, espn)
    assert client.post(f"/account/leagues/{league.pk}/disconnect/").status_code == 302
    assert not EspnCredential.objects.exists()
    assert Membership.objects.filter(user=user).exists()


def test_leave_removes_you_and_an_empty_league(client, user, espn):
    league = member_with_cookies(client, user, espn)
    assert client.post(f"/account/leagues/{league.pk}/leave/").status_code == 302
    assert not League.objects.exists()


@pytest.mark.parametrize("action", ["disconnect", "leave"])
def test_cannot_act_on_a_league_you_are_not_in(client, user, action):
    league = private_league()
    assert client.post(f"/account/leagues/{league.pk}/{action}/").status_code == 404
    assert EspnCredential.objects.filter(league=league).exists()


@pytest.mark.parametrize("action", ["disconnect", "leave"])
def test_account_actions_need_a_post(client, user, espn, action):
    league = member_with_cookies(client, user, espn)
    assert client.get(f"/account/leagues/{league.pk}/{action}/").status_code == 405
    assert League.objects.filter(pk=league.pk).exists()


def test_delete_account_needs_the_password(client, user, espn):
    member_with_cookies(client, user, espn)
    resp = client.post("/account/delete/", {"password": "wrong"})
    assert resp.status_code == 302 and "error=password" in resp.url
    assert get_user_model().objects.filter(pk=user.pk).exists()


def test_delete_account_needs_a_post(client, user):
    assert client.get("/account/delete/").status_code == 405


def test_delete_account_removes_everything_and_signs_out(client, user, espn):
    member_with_cookies(client, user, espn)
    resp = client.post("/account/delete/", {"password": "right"})
    assert resp.status_code == 302 and resp.url.endswith("/login/")
    assert not get_user_model().objects.filter(pk=user.pk).exists()
    assert not EspnCredential.objects.exists()
    assert not League.objects.exists()
    assert client.get("/startsit/").status_code == 302


def test_superuser_cannot_delete_itself_here(client, django_user_model):
    admin = django_user_model.objects.create_superuser("root", password="right")
    client.force_login(admin)
    resp = client.post("/account/delete/", {"password": "right"})
    assert "error=superuser" in resp.url
    assert django_user_model.objects.filter(pk=admin.pk).exists()


# --- banners


def test_expired_login_banner_only_for_its_owner(client, user, espn, django_user_model):
    league = member_with_cookies(client, user, espn)
    League.objects.update(has_data=True)
    EspnCredential.objects.get().mark_expired()
    html = client.get("/account/").content.decode()
    assert f"/connect/?league={league.espn_id}" in html
    assert "stopped accepting your login" in html
    other = django_user_model.objects.create_user("other")
    Membership.objects.create(user=other, league=league, team=league.teams.get(espn_id=2))
    client.force_login(other)
    assert "stopped accepting your login" not in client.get("/account/").content.decode()


# --- admin


def test_admin_never_shows_ciphertext_or_allows_editing(client, espn, django_user_model):
    admin = django_user_model.objects.create_superuser("root", password="x")
    connect.connect_league(admin, 77, S2, SWID)
    row = EspnCredential.objects.get()
    client.force_login(admin)
    for path in ["/admin/league/espncredential/", f"/admin/league/espncredential/{row.pk}/change/"]:
        resp = client.get(path)
        assert resp.status_code == 200
        assert row.encrypted_s2.encode() not in resp.content
        assert row.encrypted_swid.encode() not in resp.content
    assert client.get("/admin/league/espncredential/add/").status_code == 403


# --- claims in the pages, and in what's stored for the whole league


@pytest.fixture
def league_with_claim(client, monkeypatch, django_user_model):
    monkeypatch.setattr(services, "weekly_projections", lambda league, players, weeks: {
        (p.pk, w): Projection(MEANS.get(p.name, 1.0), 0.5) for p in players for w in weeks
    })
    league = League.objects.create(
        espn_id=7, season=2026, name="L", team_count=2, current_week=16, reg_season_weeks=14, final_week=17, lineup_slots={"RB": 1, "WR": 1, "BE": 1}
    )
    a = Team.objects.create(league=league, espn_id=1, name="Alice Team", abbrev="A")
    b = Team.objects.create(league=league, espn_id=2, name="Bob Team", abbrev="B")
    for team, slot, espn_id, name, position in [
        (a, "RB", 1, "Alice Star RB", "RB"), (a, "BE", 2, "Alice Spare RB", "RB"), (a, "WR", 3, "Alice Weak WR", "WR"),
        (b, "WR", 4, "Bob Star WR", "WR"), (b, "BE", 5, "Bob Spare WR", "WR"), (b, "RB", 6, "Bob Weak RB", "RB"),
    ]:
        add_player(league, espn_id, name, position, team, slot)
    alice = django_user_model.objects.create_user("alice")
    bob = django_user_model.objects.create_user("bob")
    Membership.objects.create(user=alice, league=league, team=a)
    Membership.objects.create(user=bob, league=league, team=b)
    # alice is dropping her spare RB in a waiver claim only she can see
    PendingMove.objects.create(
        league=league, espn_id="w", kind="waiver", team_espn_id=1, owner=alice,
        items=[{"player_id": 2, "from_team": 1, "to_team": 0, "type": "DROP"}],
    )
    return SimpleNamespace(league=league, a=a, b=b, alice=alice, bob=bob)


MEANS = {"Alice Star RB": 20, "Alice Spare RB": 15, "Alice Weak WR": 5, "Bob Star WR": 20, "Bob Spare WR": 15, "Bob Weak RB": 5}


def add_player(league, espn_id, name, position, team=None, slot="BE"):
    player = Player.objects.create(espn_id=espn_id, name=name, position=position, pro_team="BUF", eligible_slots=[position, "BE"])
    LeaguePlayer.objects.create(league=league, player=player)
    if team:
        RosterEntry.objects.create(team=team, player=player, lineup_slot=slot)
    return player


@pytest.mark.parametrize("path", ["/startsit/", "/waivers/", "/trades/?view=build"])
def test_a_leaguemate_never_sees_your_claim_even_viewing_your_team(client, league_with_claim, path):
    client.force_login(league_with_claim.bob)
    html = client.get(path, {"team": 1}).content.decode()
    assert "pending claim" not in html.lower()
    assert "claim is pending" not in html.lower()


@pytest.mark.parametrize("path", ["/startsit/", "/trades/?view=build"])
def test_you_see_your_own_claim(client, league_with_claim, path):
    client.force_login(league_with_claim.alice)
    assert "Dropping in your pending claim" in client.get(path).content.decode()


def test_stored_deals_do_not_depend_on_anyones_claims(league_with_claim):
    with_claim = deals.best_deals(league_with_claim.a, viewer=league_with_claim.alice)
    stored = list(TradeDeals.objects.filter(team=league_with_claim.a).values_list("total", "deals"))
    PendingMove.objects.all().delete()
    TradeDeals.objects.all().delete()
    without = deals.best_deals(league_with_claim.a)
    assert list(TradeDeals.objects.filter(team=league_with_claim.a).values_list("total", "deals")) == stored
    assert with_claim["checked"] == without["checked"]


def test_deals_touching_your_claim_are_hidden_only_from_you(league_with_claim):
    spare = Player.objects.get(name="Alice Spare RB").pk

    def involves_spare(result):
        return any(spare in {p["id"] for p in d["gives"] + d["gets"]} for d in result["deals"])

    assert involves_spare(deals.best_deals(league_with_claim.a, viewer=league_with_claim.bob))
    assert not involves_spare(deals.best_deals(league_with_claim.a, viewer=league_with_claim.alice))


def test_no_viewer_on_a_deal_search_means_no_claims_used(league_with_claim):
    ctx = services.TradeContext(league_with_claim.league)
    assert ctx.tied_up == {}


def test_last_seen_is_updated_at_most_daily(client, user, django_user_model):
    from datetime import timedelta

    from django.utils import timezone

    league = private_league()
    Membership.objects.create(user=user, league=league, team=league.teams.get(espn_id=1))
    Membership.objects.update(last_seen=timezone.now() - timedelta(days=5))
    client.get("/account/")
    assert timezone.now() - Membership.objects.get().last_seen < timedelta(minutes=1)
    pinned = timezone.now() - timedelta(hours=3)
    Membership.objects.update(last_seen=pinned)
    client.get("/account/")
    assert Membership.objects.get().last_seen == pinned


def test_a_leaguemate_cannot_infer_your_claim_from_the_suggested_drop(client, league_with_claim):
    # alice's claim protects her spare RB from being suggested, which would give the claim away
    client.force_login(league_with_claim.bob)
    assert client.get("/waivers/", {"team": 1}).context["drop"].name == "Alice Spare RB"
    client.force_login(league_with_claim.alice)
    assert client.get("/waivers/", {"team": 1}).context["drop"].name != "Alice Spare RB"


def test_cookies_and_passwords_are_hidden_from_error_reports(client, user, espn):
    resp = client.post("/connect/", {"league": "77", "espn_s2": S2, "swid": SWID})
    assert set(resp.wsgi_request.sensitive_post_parameters) == {"espn_s2", "swid"}
    resp = client.post("/account/delete/", {"password": "wrong"})
    assert set(resp.wsgi_request.sensitive_post_parameters) == {"password"}
