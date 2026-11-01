"""Syncing many leagues: whose cookies, whose claims, what fails alone, and what reaches public logs."""

import logging
from datetime import timedelta

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.utils import timezone
from espn_api.requests.espn_requests import ESPNAccessDenied

from league import credentials, espn_client
from league.espn_client import LeagueData, PendingMoveData, TeamData
from league.models import EspnCredential, League, LeaguePlayer, Membership, PendingMove, SyncRun, Team
from web import services

pytestmark = pytest.mark.django_db

SECRET = "league-name-or-cookie-text"
TRADE = PendingMoveData("t1", "trade", 2, [{"player_id": 1, "from_team": 2, "to_team": 1, "type": "TRADE"}])


def waiver(espn_id, team=1):
    return PendingMoveData(espn_id, "waiver", team, [{"player_id": 1, "from_team": 0, "to_team": team, "type": "ADD"}])


def data_for(league, pending=()):
    teams = [
        TeamData(espn_id=t, name=f"T{t}", abbrev=f"T{t}", owners=["N"], wins=0, losses=0, ties=0, points_for=0, points_against=0,
                 standing=t, waiver_rank=t, roster=[], schedule={}, owner_ids=[f"owner{t}"])
        for t in (1, 2)
    ]
    return LeagueData(
        espn_id=league.espn_id, season=league.season, name=league.name, team_count=2, current_week=8, reg_season_weeks=14,
        final_week=17, lineup_slots={"QB": 1, "BE": 1}, scoring={}, teams=teams, players=[], pro_games=[], pending=list(pending),
    )


def make(espn_id=1, name="L", public=False, **fields):
    league = League.objects.create(espn_id=espn_id, season=2026, name=name, team_count=2, current_week=8, reg_season_weeks=14, is_public=public, **fields)
    Team.objects.create(league=league, espn_id=1, name="T1", abbrev="A")
    Team.objects.create(league=league, espn_id=2, name="T2", abbrev="B")
    return league


def join(user, league, team=1, cookies=None):
    Membership.objects.create(user=user, league=league, team=league.teams.get(espn_id=team))
    if cookies:
        return EspnCredential.objects.create(
            user=user, league=league, encrypted_s2=credentials.encrypt(cookies), encrypted_swid=credentials.encrypt(f"swid-{cookies}")
        )


@pytest.fixture
def alice(django_user_model):
    return django_user_model.objects.create_user("alice")


@pytest.fixture
def bob(django_user_model):
    return django_user_model.objects.create_user("bob")


@pytest.fixture
def espn(monkeypatch):
    """Fake ESPN. `accepts` is the cookies that work (None = anonymous), `claims` is what each can see."""

    class State:
        accepts = {"a", "b"}
        claims = {}
        calls = []

    State.calls = []
    State.claims = {}

    def fetch(league_id, season, espn_s2, swid):
        State.calls.append(espn_s2)
        if espn_s2 not in State.accepts:
            raise ESPNAccessDenied("no")
        league = League.objects.get(espn_id=league_id, season=season)
        return data_for(league, [TRADE, waiver("w-from-fetch")])

    def claims(league_id, season, week, espn_s2, swid):
        if espn_s2 not in State.accepts:
            raise ESPNAccessDenied("no")
        return State.claims.get(espn_s2, [])

    monkeypatch.setattr(espn_client, "fetch_league", fetch)
    monkeypatch.setattr(espn_client, "fetch_claims", claims)
    return State


# --- whose cookies


def test_public_league_syncs_without_cookies(espn, alice):
    espn.accepts = {None}
    league = make(public=True)
    join(alice, league)
    call_command("sync_league", league.pk)
    assert espn.calls == [None]


def test_private_league_uses_cookies(espn, alice):
    league = make()
    join(alice, league, cookies="a")
    call_command("sync_league", league.pk)
    assert espn.calls == ["a"]
    assert EspnCredential.objects.get().last_ok_at is not None


def test_expired_cookies_are_marked_and_the_next_member_is_tried(espn, alice, bob):
    espn.accepts = {"b"}
    league = make()
    first = join(alice, league, 1, "a")
    second = join(bob, league, 2, "b")
    call_command("sync_league", league.pk)
    first.refresh_from_db()
    second.refresh_from_db()
    assert first.status == EspnCredential.EXPIRED
    assert second.status == EspnCredential.OK
    assert League.objects.get(pk=league.pk).has_data


def test_working_cookies_are_tried_before_expired_ones(espn, alice, bob):
    league = make()
    join(alice, league, 1, "a").mark_expired()
    join(bob, league, 2, "b")
    call_command("sync_league", league.pk)
    assert espn.calls == ["b"]


def test_a_false_alarm_heals_itself(espn, alice):
    league = make()
    credential = join(alice, league, cookies="a")
    credential.mark_expired()
    call_command("sync_league", league.pk)
    credential.refresh_from_db()
    assert credential.status == EspnCredential.OK


def test_every_login_failing_raises_access_denied(espn, alice):
    espn.accepts = set()
    league = make()
    join(alice, league, cookies="a")
    with pytest.raises(ESPNAccessDenied):
        call_command("sync_league", league.pk)
    assert EspnCredential.objects.get().status == EspnCredential.EXPIRED


def test_public_league_going_private_falls_back_to_cookies(espn, alice):
    league = make(public=True)
    join(alice, league, cookies="a")
    call_command("sync_league", league.pk)
    assert espn.calls == [None, "a"]
    assert not League.objects.get(pk=league.pk).is_public


def test_private_league_going_public_is_noticed(espn, alice):
    espn.accepts = {None}
    league = make()
    join(alice, league, cookies="a")
    call_command("sync_league", league.pk)
    assert League.objects.get(pk=league.pk).is_public


# --- whose claims


def test_claims_belong_to_whoever_can_see_them(espn, alice, bob):
    league = make()
    espn.claims = {"a": [waiver("w-a", 1)], "b": [waiver("w-b", 2)]}
    join(alice, league, 1, "a")
    join(bob, league, 2, "b")
    call_command("sync_league", league.pk)
    owners = dict(PendingMove.objects.filter(kind="waiver").values_list("espn_id", "owner__username"))
    assert owners == {"w-a": "alice", "w-b": "bob"}


def test_claims_in_the_main_fetch_are_not_saved_for_the_league(espn, alice):
    league = make()
    join(alice, league, cookies="a")
    call_command("sync_league", league.pk)
    assert not PendingMove.objects.filter(espn_id="w-from-fetch").exists()
    assert PendingMove.objects.get(espn_id="t1").owner is None


def test_a_commissioner_only_keeps_their_own_teams_claims(espn, alice):
    league = make()
    espn.claims = {"a": [waiver("mine", 1), waiver("someone-elses", 2)]}
    join(alice, league, 1, "a")
    call_command("sync_league", league.pk)
    assert list(PendingMove.objects.filter(kind="waiver").values_list("espn_id", flat=True)) == ["mine"]


def test_processed_claims_go_away_and_trades_are_replaced(espn, alice):
    league = make()
    join(alice, league, cookies="a")
    espn.claims = {"a": [waiver("old")]}
    call_command("sync_league", league.pk)
    espn.claims = {"a": []}
    call_command("sync_league", league.pk)
    assert not PendingMove.objects.filter(kind="waiver").exists()
    assert PendingMove.objects.filter(kind="trade").count() == 1


def test_a_failed_claims_fetch_keeps_the_last_ones_and_the_sync(espn, alice, monkeypatch):
    league = make()
    join(alice, league, cookies="a")
    espn.claims = {"a": [waiver("kept")]}
    call_command("sync_league", league.pk)

    def boom(*a):
        raise RuntimeError(SECRET)

    monkeypatch.setattr(espn_client, "fetch_claims", boom)
    call_command("sync_league", league.pk)
    assert PendingMove.objects.filter(espn_id="kept").exists()


def test_expired_while_fetching_claims_is_noted(espn, alice, bob, monkeypatch):
    league = make()
    join(alice, league, 1, "a")
    join(bob, league, 2, "b")

    def claims(league_id, season, week, espn_s2, swid):
        if espn_s2 == "b":
            raise ESPNAccessDenied("no")
        return []

    monkeypatch.setattr(espn_client, "fetch_claims", claims)
    call_command("sync_league", league.pk)
    assert EspnCredential.objects.get(user=bob).status == EspnCredential.EXPIRED
    assert EspnCredential.objects.get(user=alice).status == EspnCredential.OK


def test_cookies_without_a_team_get_no_claims(espn, alice):
    league = make()
    EspnCredential.objects.create(user=alice, league=league, encrypted_s2=credentials.encrypt("a"), encrypted_swid=credentials.encrypt("s"))
    espn.claims = {"a": [waiver("x")]}
    call_command("sync_league", league.pk)
    assert not PendingMove.objects.filter(kind="waiver").exists()


# --- the loop


@pytest.fixture
def no_shared_steps(monkeypatch):
    monkeypatch.setattr("league.management.commands.sync_all.SHARED_STEPS", {})


def test_one_failure_does_not_stop_the_others(espn, alice, bob, monkeypatch, no_shared_steps):
    bad, good = make(1, "Bad"), make(2, "Good")
    join(alice, bad, cookies="a")
    join(bob, good, cookies="b")
    real = espn_client.fetch_league

    def fetch(league_id, *a):
        if league_id == 1:
            raise RuntimeError(SECRET)
        return real(league_id, *a)

    monkeypatch.setattr(espn_client, "fetch_league", fetch)
    with pytest.raises(CommandError, match=f"league {bad.pk}"):
        call_command("sync_all", only=["league"])
    assert SyncRun.objects.get(league=good).ok
    assert not SyncRun.objects.get(league=bad).ok


def test_everyones_cookies_expiring_is_not_a_failed_run(espn, alice, no_shared_steps):
    espn.accepts = set()
    league = make()
    join(alice, league, cookies="a")
    call_command("sync_all", only=["league"])
    run = SyncRun.objects.get(league=league)
    assert not run.ok and run.error == "ESPNAccessDenied"


def test_errors_never_reach_the_database_or_logs(espn, alice, monkeypatch, caplog, capsys, no_shared_steps):
    league = make(name=SECRET)
    join(alice, league, cookies="a")

    def fetch(*a):
        raise RuntimeError(SECRET)

    monkeypatch.setattr(espn_client, "fetch_league", fetch)
    with caplog.at_level(logging.INFO), pytest.raises(CommandError):
        call_command("sync_all", only=["league"])
    out = capsys.readouterr()
    assert SECRET not in SyncRun.objects.get().error
    assert SyncRun.objects.get().error == "RuntimeError"
    assert SECRET not in caplog.text
    assert SECRET not in out.out + out.err
    assert "test_sync_loop" in caplog.text  # still says where it failed


def test_a_successful_sync_prints_no_league_details(espn, alice, capsys, no_shared_steps):
    league = make(name=SECRET, espn_id=987654321)
    join(alice, league, cookies="a")
    call_command("sync_all", only=["league"])
    out = capsys.readouterr()
    assert SECRET not in out.out + out.err
    assert "987654321" not in out.out + out.err
    assert f"league {league.pk}" in out.out


def test_leagues_nobody_opens_are_skipped(espn, alice, no_shared_steps):
    league = make()
    join(alice, league, cookies="a")
    Membership.objects.update(last_seen=timezone.now() - timedelta(days=15))
    call_command("sync_all", only=["league"])
    assert espn.calls == []


def test_one_recent_member_keeps_a_league_syncing(espn, alice, bob, no_shared_steps):
    league = make()
    join(alice, league, 1, "a")
    join(bob, league, 2)
    Membership.objects.filter(user=alice).update(last_seen=timezone.now() - timedelta(days=15))
    call_command("sync_all", only=["league"])
    assert espn.calls == ["a"]


def test_past_seasons_are_not_synced(espn, alice, no_shared_steps):
    old = League.objects.create(espn_id=1, season=2025, name="O", team_count=2, current_week=8, reg_season_weeks=14)
    Membership.objects.create(user=alice, league=old, team=Team.objects.create(league=old, espn_id=1, name="O", abbrev="O"))
    current = make(espn_id=2)
    join(alice, current, cookies="a")
    call_command("sync_all", only=["league"])
    assert SyncRun.objects.filter(league=old).count() == 0


def test_a_first_sync_ignores_activity(espn, alice, monkeypatch):
    league = make(has_data=False)
    join(alice, league, cookies="a")
    Membership.objects.update(last_seen=timezone.now() - timedelta(days=99))
    ran = []
    real = call_command

    def spy(name, *a, **k):
        ran.append((name, a, {x: y for x, y in k.items() if x == "league"}))
        if name == "sync_league":
            real(name, *a, **k)

    monkeypatch.setattr("league.management.commands.sync_all.call_command", spy)
    call_command("sync_all", league=league.pk)
    assert League.objects.get(pk=league.pk).has_data
    assert [r[0] for r in ran] == ["sync_league", "sync_nfl", "sync_projections", "snapshot_projections"]
    assert ran[2][2] == {"league": league.pk}
    assert ran[3][2] == {"league": league.pk}


def test_a_failed_first_sync_skips_the_shared_steps(espn, alice, monkeypatch):
    espn.accepts = set()
    league = make(has_data=False)
    join(alice, league, cookies="a")
    ran = []
    real = call_command

    def spy(name, *a, **k):
        ran.append(name)
        if name == "sync_league":
            real(name, *a, **k)

    monkeypatch.setattr("league.management.commands.sync_all.call_command", spy)
    call_command("sync_all", league=league.pk)
    assert ran == ["sync_league"]


def test_an_unknown_first_sync_league_is_skipped_quietly():
    call_command("sync_all", league=999)


def test_an_empty_database_skips_quietly(capsys):
    call_command("sync_all")
    assert "no leagues yet" in capsys.readouterr().out


def test_abandoned_connects_are_forgotten_with_their_cookies(espn, alice, no_shared_steps):
    old = make(1, has_data=False, created_at=timezone.now() - timedelta(days=2))
    EspnCredential.objects.create(user=alice, league=old, encrypted_s2="x", encrypted_swid="y")
    recent = make(2, has_data=False)
    call_command("sync_all", only=["league"])
    assert not League.objects.filter(pk=old.pk).exists()
    assert not EspnCredential.objects.exists()
    assert League.objects.filter(pk=recent.pk).exists()


def test_a_league_with_data_is_never_deleted_for_having_no_members(no_shared_steps):
    # the snapshots in it can't be rebuilt
    league = make(created_at=timezone.now() - timedelta(days=30))
    call_command("sync_all", only=["league"])
    assert League.objects.filter(pk=league.pk).exists()


def test_a_waiting_league_with_a_member_is_kept(espn, alice, no_shared_steps):
    league = make(has_data=False, created_at=timezone.now() - timedelta(days=30))
    join(alice, league, cookies="a")
    call_command("sync_all", only=["league"])
    assert League.objects.get(pk=league.pk).has_data


# --- nothing private in what's shared


def test_tied_up_hides_claims_from_everyone_but_their_owner(alice, bob):
    league = make()
    player = __import__("league.models").models.Player.objects.create(espn_id=1, name="P", position="WR", pro_team="BUF")
    LeaguePlayer.objects.create(league=league, player=player)
    PendingMove.objects.create(league=league, espn_id="w", kind="waiver", team_espn_id=1, owner=alice, items=[{"player_id": 1, "to_team": 1}])
    assert player.pk in services.tied_up(league, alice)
    assert services.tied_up(league, bob) == {}
    assert services.tied_up(league) == {}


def test_claims_from_before_owners_existed_are_visible_to_nobody(alice):
    league = make()
    player = __import__("league.models").models.Player.objects.create(espn_id=1, name="P", position="WR", pro_team="BUF")
    LeaguePlayer.objects.create(league=league, player=player)
    PendingMove.objects.create(league=league, espn_id="w", kind="waiver", team_espn_id=1, items=[{"player_id": 1, "to_team": 1}])
    assert services.tied_up(league) == {}
    assert services.tied_up(league, alice) == {}


def test_trades_are_visible_to_everyone(alice):
    league = make()
    player = __import__("league.models").models.Player.objects.create(espn_id=1, name="P", position="WR", pro_team="BUF")
    LeaguePlayer.objects.create(league=league, player=player)
    PendingMove.objects.create(league=league, espn_id="t", kind="trade", team_espn_id=1, items=[{"player_id": 1, "to_team": 2}])
    assert "Accepted trade" in services.tied_up(league)[player.pk]
    assert "Accepted trade" in services.tied_up(league, alice)[player.pk]
