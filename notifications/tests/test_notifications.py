"""What gets said, to whom and when, and what reaches a push service or the public logs."""

import base64
import json
import logging
from datetime import timedelta
from types import SimpleNamespace

import http_ece
import pytest
import requests
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from django.core.management import call_command
from django.urls import reverse
from django.utils import timezone

from analysis.projections import Projection
from league import espn_client
from league.management.commands.sync_league import Command as SyncLeague
from league.models import League, PendingMove, Player, ProGame, RosterEntry, Team
from notifications import delivery, events
from notifications.models import Notification, NotificationSettings, PushSubscription
from web import services

pytestmark = pytest.mark.django_db

ENDPOINT = "https://fcm.googleapis.com/fcm/send/device-secret"


def b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


P256DH = b64(b"\x04" + b"k" * 64)
AUTH = b64(b"a" * 16)


@pytest.fixture(autouse=True)
def no_keys_from_env(settings):
    settings.VAPID_PUBLIC_KEY = settings.VAPID_PRIVATE_KEY = settings.VAPID_SUBJECT = None


@pytest.fixture
def push_keys(settings):
    key = ec.generate_private_key(ec.SECP256R1())
    settings.VAPID_PRIVATE_KEY = b64(key.private_numbers().private_value.to_bytes(32, "big"))
    settings.VAPID_PUBLIC_KEY = b64(key.public_key().public_bytes(Encoding.X962, PublicFormat.UncompressedPoint))
    settings.VAPID_SUBJECT = "mailto:test@example.com"


@pytest.fixture
def daytime(monkeypatch):
    monkeypatch.setattr(delivery, "quiet_hours", lambda: False)


@pytest.fixture
def sent(monkeypatch, push_keys, daytime):
    """Every message that would have left, as (endpoint, message)."""
    messages = []

    def fake_send(subscription, message, ttl):
        messages.append((subscription.endpoint, message))
        return delivery.SENT

    monkeypatch.setattr(delivery, "send", fake_send)
    return messages


@pytest.fixture
def league(settings):
    settings.ESPN_MY_TEAM_ID = 1
    return League.objects.create(
        espn_id=1, season=2026, name="L", team_count=2, current_week=8, reg_season_weeks=14, lineup_slots={"WR": 1, "BE": 1},
    )


@pytest.fixture
def mine(league):
    return Team.objects.create(league=league, espn_id=1, name="My Team", abbrev="MY")


@pytest.fixture
def rival(league):
    return Team.objects.create(league=league, espn_id=2, name="Rival Team", abbrev="RV")


@pytest.fixture
def user(django_user_model, mine):
    return django_user_model.objects.create_user("me", password="pw")


def device(user, endpoint=ENDPOINT):
    return PushSubscription.objects.create(user=user, endpoint=endpoint, p256dh=P256DH, auth=AUTH)


def add_player(team, slot, espn_id=100, name="Star Receiver", pro_team="BUF", injury="ACTIVE"):
    player = Player.objects.create(espn_id=espn_id, name=name, position="WR", pro_team=pro_team, eligible_slots=["WR", "BE"], injury_status=injury)
    RosterEntry.objects.create(team=team, player=player, lineup_slot=slot)
    return player


def game(team="BUF", starts_in=timedelta(hours=5), week=8):
    return ProGame.objects.create(season=2026, week=week, team=team, opponent="MIA", kickoff=timezone.now() + starts_in)


def notification(user, league, key="k", kind=Notification.INJURIES, expires_in=timedelta(hours=1)):
    return Notification.objects.create(
        user=user, league=league, kind=kind, key=key, title="T", body="B", url="/", expires_at=timezone.now() + expires_in,
    )


class TestInjuries:
    def test_tells_the_owner_of_a_starter_once(self, user, league, mine):
        device(user)
        player = add_player(mine, "WR", injury="OUT")
        game()
        assert events.injured_starters(league) == 1
        assert events.injured_starters(league) == 0
        n = Notification.objects.get()
        assert (n.user, n.kind, n.title) == (user, Notification.INJURIES, "Star Receiver is out")
        assert "My Team" in n.body
        assert n.expires_at == ProGame.objects.get().kickoff
        assert player.pk and n.url == reverse("startsit")

    def test_a_worse_status_is_news(self, user, league, mine):
        device(user)
        player = add_player(mine, "WR", injury="DOUBTFUL")
        game()
        events.injured_starters(league)
        Player.objects.filter(pk=player.pk).update(injury_status="OUT")
        assert events.injured_starters(league) == 1

    @pytest.mark.parametrize(
        "slot,injury,starts_in",
        [
            ("BE", "OUT", timedelta(hours=5)),
            ("IR", "OUT", timedelta(hours=5)),
            ("WR", "QUESTIONABLE", timedelta(hours=5)),
            ("WR", "ACTIVE", timedelta(hours=5)),
            ("WR", "OUT", -timedelta(minutes=5)),
        ],
    )
    def test_stays_quiet(self, user, league, mine, slot, injury, starts_in):
        device(user)
        add_player(mine, slot, injury=injury)
        game(starts_in=starts_in)
        assert events.injured_starters(league) == 0

    def test_nothing_during_a_bye(self, user, league, mine):
        device(user)
        add_player(mine, "WR", injury="OUT")
        assert events.injured_starters(league) == 0

    def test_needs_a_device_and_respects_the_setting(self, user, league, mine):
        add_player(mine, "WR", injury="OUT")
        game()
        assert events.injured_starters(league) == 0
        device(user)
        NotificationSettings.objects.create(user=user, injuries=False)
        assert events.injured_starters(league) == 0

    def test_never_about_someone_elses_team(self, user, league, rival, django_user_model):
        device(user)
        add_player(rival, "WR", injury="OUT")
        game()
        assert events.injured_starters(league) == 0


def claim(espn_id, player_id, team=1):
    return PendingMove(
        espn_id=espn_id, kind=PendingMove.WAIVER, team_espn_id=team,
        items=[
            {"player_id": player_id, "from_team": 0, "to_team": team, "type": "ADD"},
            {"player_id": 999, "from_team": team, "to_team": 0, "type": "DROP"},
        ],
    )


class TestWaivers:
    def test_says_who_got_each_player(self, user, league, mine, rival):
        device(user)
        add_player(mine, "BE", 100, "Won Guy")
        add_player(rival, "BE", 101, "Lost Guy")
        assert events.waiver_results(league, [claim("a", 100), claim("b", 101)]) == 1
        assert Notification.objects.get().body == "You got Won Guy. Lost Guy went to Rival Team."

    def test_says_nothing_when_it_cant_tell(self, user, league, mine):
        """Cancelled, or lost out and the player is still unrostered. The dropped player doesn't count either."""
        device(user)
        Player.objects.create(espn_id=100, name="Nobody Has Him", position="WR", pro_team="BUF")
        assert events.waiver_results(league, [claim("a", 100)]) == 0

    def test_other_teams_claims_arent_news(self, user, league, mine, rival):
        """A commissioner's cookies can see them."""
        device(user)
        add_player(rival, "BE", 100, "Their Guy")
        assert events.waiver_results(league, [claim("a", 100, team=2)]) == 0

    def test_same_claim_only_once(self, user, league, mine):
        device(user)
        add_player(mine, "BE", 100, "Won Guy")
        assert events.waiver_results(league, [claim("a", 100)]) == 1
        assert events.waiver_results(league, [claim("a", 100)]) == 0

    def sync(self, monkeypatch, settings, pending):
        settings.ESPN_LEAGUE_ID = 1
        data = SimpleNamespace(espn_id=1, season=2026, pending=pending, teams=[], players=[])
        monkeypatch.setattr(espn_client, "fetch_league", lambda *a: data)
        monkeypatch.setattr(SyncLeague, "_save", lambda self, d: League.objects.get())
        call_command("sync_league")

    def test_sync_reports_claims_that_left_pending(self, user, league, mine, monkeypatch, settings):
        device(user)
        add_player(mine, "BE", 100, "Won Guy")
        for espn_id, player in (("gone", 100), ("still", 101)):
            move = claim(espn_id, player)
            move.league = league
            move.save()
        self.sync(monkeypatch, settings, [SimpleNamespace(espn_id="still")])
        n = Notification.objects.get()
        assert (n.user, n.kind, n.body) == (user, Notification.WAIVERS, "You got Won Guy.")

    def test_a_notification_problem_doesnt_fail_the_sync(self, user, league, monkeypatch, settings):
        monkeypatch.setattr(events, "waiver_results", lambda *a: 1 / 0)
        self.sync(monkeypatch, settings, [])


def rows(starter="Starter", bench="Better Bench"):
    row = lambda name: SimpleNamespace(player=SimpleNamespace(name=name, pro_team="BUF"))  # noqa: E731
    return row(bench), row(starter)


class TestLineupReminder:
    def advice(self, monkeypatch, moves, gain=4.25):
        monkeypatch.setattr(services, "suggested_moves", lambda team: (moves, gain))

    def test_reminds_once_before_the_first_affected_game(self, user, league, monkeypatch):
        device(user)
        kickoff = game(starts_in=timedelta(minutes=75)).kickoff
        self.advice(monkeypatch, [rows()])
        assert events.lineup_reminder(league) == 1
        assert events.lineup_reminder(league) == 0
        n = Notification.objects.get()
        assert n.body == "Start Better Bench over Starter for +4.2 projected."
        assert n.expires_at == kickoff

    def test_mentions_how_many_more(self, user, league, monkeypatch):
        device(user)
        game(starts_in=timedelta(minutes=75))
        self.advice(monkeypatch, [rows(), rows("C", "D")])
        events.lineup_reminder(league)
        assert ", plus 1 more for" in Notification.objects.get().body

    @pytest.mark.parametrize(
        "starts_in,moves,gain",
        [
            (timedelta(hours=3), [rows()], 4.0),  # too early: inactives aren't out
            (-timedelta(minutes=5), [rows()], 4.0),  # too late
            (timedelta(minutes=75), [], 0.0),  # you're set
            (timedelta(minutes=75), [rows()], 0.4),  # not worth a buzz
        ],
    )
    def test_stays_quiet(self, user, league, monkeypatch, starts_in, moves, gain):
        device(user)
        game(starts_in=starts_in)
        self.advice(monkeypatch, moves, gain)
        assert events.lineup_reminder(league) == 0

    def test_the_real_suggestion(self, user, league, mine, monkeypatch):
        """The advice comes from the same recommender as the Start/Sit page."""
        device(user)
        starter, bench = add_player(mine, "WR", 100, "Starter"), add_player(mine, "BE", 101, "Better Bench")
        game(starts_in=timedelta(minutes=75))
        means = {starter.pk: 5.0, bench.pk: 15.0}
        monkeypatch.setattr(
            services, "weekly_projections",
            lambda lg, players, weeks: {(p.pk, w): Projection(means[p.pk], 1.0) for p in players for w in weeks},
        )
        assert events.lineup_reminder(league) == 1
        assert Notification.objects.get().body == "Start Better Bench over Starter for +10.0 projected."


class TestDelivery:
    def test_sends_to_every_device_once(self, user, league, sent):
        device(user)
        device(user, "https://web.push.apple.com/other")
        n = notification(user, league)
        assert delivery.deliver() == 1
        assert delivery.deliver() == 0
        assert {endpoint for endpoint, _ in sent} == {ENDPOINT, "https://web.push.apple.com/other"}
        assert sent[0][1] == {"title": "T", "body": "B", "url": "/", "tag": "k"}
        n.refresh_from_db()
        assert n.sent_at

    def test_holds_through_quiet_hours(self, user, league, sent, monkeypatch):
        device(user)
        notification(user, league)
        monkeypatch.setattr(delivery, "quiet_hours", lambda: True)
        assert delivery.deliver() == 0
        monkeypatch.setattr(delivery, "quiet_hours", lambda: False)
        assert delivery.deliver() == 1

    @pytest.mark.parametrize("hour,quiet", [(21, False), (22, True), (2, True), (7, True), (8, False)])
    def test_quiet_hours(self, monkeypatch, hour, quiet):
        monkeypatch.setattr(timezone, "localtime", lambda: SimpleNamespace(hour=hour))
        assert delivery.quiet_hours() is quiet

    def test_expired_is_dropped_not_sent_late(self, user, league, sent):
        device(user)
        notification(user, league, expires_in=-timedelta(minutes=1))
        assert delivery.deliver() == 0
        assert not sent and not Notification.objects.exists()

    def test_nothing_after_turning_the_kind_off(self, user, league, sent):
        device(user)
        notification(user, league)
        NotificationSettings.objects.create(user=user, injuries=False)
        assert delivery.deliver() == 0
        assert not sent and not Notification.objects.exists()

    def test_old_history_is_pruned(self, user, league, sent):
        old = notification(user, league)
        Notification.objects.filter(pk=old.pk).update(sent_at=timezone.now() - delivery.KEEP_SENT - timedelta(days=1))
        delivery.deliver()
        assert not Notification.objects.exists()

    def test_off_without_keys(self, user, league, daytime):
        device(user)
        notification(user, league)
        assert delivery.deliver() == 0
        assert Notification.objects.get().sent_at is None

    def test_tries_again_after_a_failure(self, user, league, push_keys, daytime, monkeypatch):
        device(user)
        n = notification(user, league)
        monkeypatch.setattr(delivery, "send", lambda *a: delivery.FAILED)
        assert delivery.deliver() == 0
        n.refresh_from_db()
        assert n.sent_at is None
        monkeypatch.setattr(delivery, "send", lambda *a: delivery.SENT)
        assert delivery.deliver() == 1

    def test_a_dead_device_doesnt_skip_the_next_one(self, user, league, push_keys, daytime, monkeypatch):
        dead, live = device(user, "https://fcm.googleapis.com/dead"), device(user, "https://fcm.googleapis.com/live")
        tried = []

        def fake(subscription, message, ttl):
            tried.append(subscription.pk)
            return delivery.GONE if subscription == dead else delivery.SENT

        monkeypatch.setattr(delivery, "send", fake)
        notification(user, league)
        assert delivery.deliver() == 1
        assert tried == [dead.pk, live.pk]


class TestSend:
    """Real encryption and signing, stopping only at the HTTP request."""

    @pytest.fixture
    def browser(self):
        key = ec.generate_private_key(ec.SECP256R1())
        return SimpleNamespace(
            key=key, auth=b"a" * 16,
            public=b64(key.public_key().public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)),
        )

    def post(self, monkeypatch, status):
        calls = []

        def fake_post(url, **kwargs):
            calls.append((url, kwargs))
            return SimpleNamespace(status_code=status, reason="R", text="endpoint echoed here", headers={})

        monkeypatch.setattr(requests, "post", fake_post)
        return calls

    def test_a_browser_can_read_it(self, user, push_keys, browser, monkeypatch):
        calls = self.post(monkeypatch, 201)
        subscription = PushSubscription.objects.create(user=user, endpoint=ENDPOINT, p256dh=browser.public, auth=b64(browser.auth))
        assert delivery.send(subscription, {"title": "Hello"}, 60) == delivery.SENT
        url, request = calls[0]
        assert url == ENDPOINT
        assert request["headers"]["ttl"] == "60"
        assert request["headers"]["Urgency"] == "high"
        assert request["headers"]["Authorization"].startswith("vapid ")
        plain = http_ece.decrypt(request["data"], private_key=browser.key, auth_secret=browser.auth, version="aes128gcm")
        assert json.loads(plain) == {"title": "Hello"}

    @pytest.mark.parametrize("status", [404, 410])
    def test_forgets_a_device_thats_gone(self, user, push_keys, browser, monkeypatch, status):
        self.post(monkeypatch, status)
        subscription = PushSubscription.objects.create(user=user, endpoint=ENDPOINT, p256dh=browser.public, auth=b64(browser.auth))
        assert delivery.send(subscription, {}, 60) == delivery.GONE
        assert not PushSubscription.objects.exists()

    @pytest.mark.parametrize("status", [429, 500, 403])
    def test_keeps_a_device_on_other_errors_and_logs_no_secrets(self, user, push_keys, browser, monkeypatch, caplog, status):
        self.post(monkeypatch, status)
        subscription = PushSubscription.objects.create(user=user, endpoint=ENDPOINT, p256dh=browser.public, auth=b64(browser.auth))
        with caplog.at_level(logging.DEBUG):
            assert delivery.send(subscription, {}, 60) == delivery.FAILED
        assert PushSubscription.objects.exists()
        assert "device-secret" not in caplog.text and "echoed" not in caplog.text

    def test_network_errors_stay_out_of_the_logs_too(self, user, push_keys, browser, monkeypatch, caplog):
        def refused(url, **kwargs):
            raise requests.ConnectionError(f"couldn't reach {url}")

        monkeypatch.setattr(requests, "post", refused)
        subscription = PushSubscription.objects.create(user=user, endpoint=ENDPOINT, p256dh=browser.public, auth=b64(browser.auth))
        assert delivery.send(subscription, {}, 60) == delivery.FAILED
        assert "device-secret" not in caplog.text


class TestSubscribing:
    def body(self, endpoint=ENDPOINT, p256dh=P256DH, auth=AUTH):
        return json.dumps({"endpoint": endpoint, "keys": {"p256dh": p256dh, "auth": auth}})

    def post(self, client, name, body):
        return client.post(reverse(name), body, content_type="application/json")

    def test_subscribes_and_unsubscribes(self, client, user):
        client.force_login(user)
        assert self.post(client, "push_subscribe", self.body()).status_code == 200
        assert self.post(client, "push_subscribe", self.body()).status_code == 200
        assert PushSubscription.objects.get().user == user
        self.post(client, "push_unsubscribe", json.dumps({"endpoint": ENDPOINT}))
        assert not PushSubscription.objects.exists()

    def test_requires_sign_in(self, client):
        response = self.post(client, "push_subscribe", self.body())
        assert response.status_code == 302 and not PushSubscription.objects.exists()

    def test_requires_csrf(self, user):
        from django.test import Client

        strict = Client(enforce_csrf_checks=True)
        strict.force_login(user)
        assert self.post(strict, "push_subscribe", self.body()).status_code == 403

    @pytest.mark.parametrize(
        "endpoint",
        [
            "http://fcm.googleapis.com/x",
            "https://evil.example.com/x",
            "https://fcm.googleapis.com.evil.example.com/x",
            "https://evilfcm.googleapis.com.example/x",
            "https://user@fcm.googleapis.com/x",
            "https://fcm.googleapis.com:8443/x",
            "https://fcm.googleapis.com:notaport/x",
            "http://169.254.169.254/latest/meta-data",
            "https://localhost/x",
            123,
        ],
    )
    def test_only_real_push_services(self, client, user, endpoint):
        client.force_login(user)
        assert self.post(client, "push_subscribe", self.body(endpoint=endpoint)).status_code == 400
        assert not PushSubscription.objects.exists()

    @pytest.mark.parametrize(
        "endpoint",
        ["https://fcm.googleapis.com/x", "https://updates.push.services.mozilla.com/x", "https://web.push.apple.com/x", "https://wns2-par02p.notify.windows.com/x"],
    )
    def test_the_real_ones_pass(self, endpoint):
        assert delivery.is_push_service(endpoint)

    @pytest.mark.parametrize("keys", [{"p256dh": "short", "auth": AUTH}, {"p256dh": P256DH, "auth": "!!"}, {"p256dh": 1, "auth": None}, {}])
    def test_rejects_bad_keys(self, client, user, keys):
        client.force_login(user)
        response = self.post(client, "push_subscribe", json.dumps({"endpoint": ENDPOINT, "keys": keys}))
        assert response.status_code == 400

    @pytest.mark.parametrize("body", ["not json", "[1]", json.dumps({"endpoint": ENDPOINT, "keys": "x"}), json.dumps({"endpoint": ENDPOINT})])
    def test_rejects_junk(self, client, user, body):
        client.force_login(user)
        assert self.post(client, "push_subscribe", body).status_code == 400

    def test_a_shared_device_follows_whoever_signed_in(self, client, user, django_user_model):
        device(django_user_model.objects.create_user("before"))
        client.force_login(user)
        self.post(client, "push_subscribe", self.body())
        assert PushSubscription.objects.get().user == user

    def test_cant_unsubscribe_someone_elses_device(self, client, user, django_user_model):
        device(django_user_model.objects.create_user("before"))
        client.force_login(user)
        self.post(client, "push_unsubscribe", json.dumps({"endpoint": ENDPOINT}))
        assert PushSubscription.objects.exists()

    def test_oldest_device_makes_way(self, client, user):
        client.force_login(user)
        for i in range(6):
            self.post(client, "push_subscribe", self.body(endpoint=f"https://fcm.googleapis.com/{i}"))
        endpoints = set(PushSubscription.objects.values_list("endpoint", flat=True))
        assert len(endpoints) == 5 and "https://fcm.googleapis.com/0" not in endpoints

    def test_test_button_reaches_only_your_devices(self, client, user, sent, django_user_model):
        device(user)
        device(django_user_model.objects.create_user("else"), "https://fcm.googleapis.com/else")
        client.force_login(user)
        assert client.post(reverse("push_test")).json() == {"sent": 1}
        assert [endpoint for endpoint, _ in sent] == [ENDPOINT]


class TestPages:
    def test_settings_save(self, client, user):
        client.force_login(user)
        client.post(reverse("notifications"), {"injuries": "on"})
        saved = NotificationSettings.objects.get(user=user)
        assert (saved.injuries, saved.waivers, saved.lineup) == (True, False, False)

    def test_menu_link_only_when_set_up(self, client, user, settings):
        client.force_login(user)
        assert b"push.js" not in client.get(reverse("notifications")).content
        settings.VAPID_PUBLIC_KEY = "public-key"
        page = client.get(reverse("notifications")).content
        assert b"push-panel" in page and b'data-key="public-key"' in page and b"push.js" in page

    def test_everything_starts_on(self, client, user, settings):
        settings.VAPID_PUBLIC_KEY = "k"
        client.force_login(user)
        assert client.get(reverse("notifications")).content.count(b"checked") == 3

    def test_private_key_never_reaches_a_page(self, client, user, settings):
        settings.VAPID_PUBLIC_KEY, settings.VAPID_PRIVATE_KEY = "public-key", "private-key"
        client.force_login(user)
        assert b"private-key" not in client.get(reverse("notifications")).content

    def test_requires_sign_in(self, client):
        assert client.get(reverse("notifications")).status_code == 302

    def test_worker_handles_push(self, client):
        worker = client.get(reverse("service_worker")).content.decode()
        assert 'addEventListener("push"' in worker and 'addEventListener("notificationclick"' in worker


class TestCommands:
    def test_keys_it_generates_work(self, capsys, user, league, settings, monkeypatch, daytime):
        call_command("generate_vapid_keys")
        keys = dict(line.split("=", 1) for line in capsys.readouterr().out.strip().splitlines())
        settings.VAPID_PUBLIC_KEY, settings.VAPID_PRIVATE_KEY = keys["VAPID_PUBLIC_KEY"], keys["VAPID_PRIVATE_KEY"]
        settings.VAPID_SUBJECT = "mailto:test@example.com"
        browser = ec.generate_private_key(ec.SECP256R1())
        public = b64(browser.public_key().public_bytes(Encoding.X962, PublicFormat.UncompressedPoint))
        PushSubscription.objects.create(user=user, endpoint=ENDPOINT, p256dh=public, auth=AUTH)
        monkeypatch.setattr(requests, "post", lambda url, **kw: SimpleNamespace(status_code=201, reason="Created", text="", headers={}))
        notification(user, league)
        assert delivery.deliver() == 1

    def test_send_notifications_prints_only_a_count(self, capsys, user, league, sent):
        device(user)
        notification(user, league)
        call_command("send_notifications")
        assert capsys.readouterr().out.strip() == "notifications: 1 sent"

    def test_sync_all_sends_and_a_failure_doesnt_fail_it(self, user, league, sent, monkeypatch, capsys):
        monkeypatch.setattr("league.management.commands.sync_all.call_command", lambda *a, **k: None)
        monkeypatch.setattr(events, "check_league", lambda lg: 1 / 0)
        call_command("sync_all", only=["league"])
        monkeypatch.setattr(events, "check_league", lambda lg: 0)
        device(user)
        notification(user, league)
        call_command("sync_all", only=["league"])
        assert len(sent) == 1
        assert "notifications: 0 new, 1 sent" in capsys.readouterr().out
