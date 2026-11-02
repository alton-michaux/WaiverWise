from datetime import timedelta
from types import SimpleNamespace

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.tokens import default_token_generator
from django.core.management import call_command
from django.db import IntegrityError, transaction
from django.utils import timezone
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode

from league import connect, espn_client, invites
from league.connect import ConnectError
from league.tests.test_connect import info
from league.models import EspnCredential, Invite, InviteUse, League, Membership
from web import narrate, views
from web.models import ExplainUsage, Explanation

pytestmark = pytest.mark.django_db

GOOD_PASSWORD = "correct-horse-battery-9"
User = get_user_model()


def signup(client, token, username="newbie", password=GOOD_PASSWORD):
    return client.post(f"/invite/{token}/", {"username": username, "password1": password, "password2": password})


@pytest.fixture
def league(two_leagues):
    full, _ = two_leagues
    return full


@pytest.fixture
def league_link(league):
    return invites.create(Invite.LEAGUE, league)


@pytest.fixture
def app_link(db):
    return invites.create(Invite.APP)


# --- the link itself


def test_only_a_hash_is_stored(league_link):
    invite, token = league_link
    assert token not in invite.token_hash
    assert invites.find(token) == invite
    assert invites.find(invite.token_hash) is None


def test_league_invite_fits_the_league_and_an_app_invite_one_person(league_link, app_link, league):
    assert league_link[0].max_uses == league.team_count
    assert app_link[0].max_uses == 1
    for invite, _ in (league_link, app_link):
        assert timedelta(days=13) < invite.expires_at - timezone.now() <= timedelta(days=14)


@pytest.mark.parametrize("kind, has_league", [(Invite.LEAGUE, False), (Invite.APP, True)])
def test_database_refuses_a_league_that_does_not_match_the_kind(league, kind, has_league):
    with pytest.raises(IntegrityError), transaction.atomic():
        Invite.objects.create(token_hash="x", kind=kind, league=league if has_league else None, expires_at=timezone.now(), max_uses=1)


def test_looking_at_a_link_spends_nothing(client, league_link):
    """Chat apps fetch links to build their previews."""
    invite, token = league_link
    assert client.get(f"/invite/{token}/").status_code == 200
    invite.refresh_from_db()
    assert invite.uses == 0


def test_preview_title_does_not_name_the_league(client, league_link, league):
    html = client.get(f"/invite/{league_link[1]}/").content.decode()
    assert league.name not in html.split("</title>")[0]


def test_invite_pages_are_never_cached(client, league_link):
    assert "no-store" in client.get(f"/invite/{league_link[1]}/")["Cache-Control"]


@pytest.mark.parametrize("change", [{"expires_at": timezone.now() - timedelta(seconds=1)}, {"uses": 2}])
def test_expired_or_used_up_links_refuse(client, league_link, change):
    invite, token = league_link
    Invite.objects.filter(pk=invite.pk).update(**change)
    assert client.get(f"/invite/{token}/").status_code == 404
    assert signup(client, token).status_code == 404
    assert not User.objects.exists()


def test_unknown_link_looks_the_same_as_a_dead_one(client, league_link):
    invite, token = league_link
    Invite.objects.filter(pk=invite.pk).update(uses=invite.max_uses)
    assert client.get("/invite/nope/").content == client.get(f"/invite/{token}/").content


# --- signing up


def test_signup_needs_a_link(client):
    assert client.post("/invite/nope/", {"username": "x", "password1": GOOD_PASSWORD, "password2": GOOD_PASSWORD}).status_code == 404
    assert not User.objects.exists()


def test_league_invite_signs_you_up_and_opens_a_private_league(client, league_link, league):
    invite, token = league_link
    resp = signup(client, token)
    assert (resp.status_code, resp.url) == (302, f"/connect/{league.pk}/team/")
    user = User.objects.get()
    invite.refresh_from_db()
    assert invite.uses == 1
    assert connect.can_join(user, league)
    assert not connect.may_add_league(user)
    assert client.get(resp.url).status_code == 200


def test_invited_member_picks_a_team_and_has_the_league(client, league_link, league):
    signup(client, league_link[1])
    team = league.teams.first()
    assert client.post(f"/connect/{league.pk}/team/", {"team": team.espn_id}).status_code == 302
    assert Membership.objects.get().team == team


def test_a_league_invite_opens_only_that_league(client, league_link, two_leagues):
    _, other = two_leagues
    signup(client, league_link[1])
    assert not connect.can_join(User.objects.get(), other)
    assert client.get(f"/connect/{other.pk}/team/").status_code == 404


def test_app_invite_lets_you_add_a_league(client, app_link):
    resp = signup(client, app_link[1])
    assert resp.url == "/connect/"
    assert connect.may_add_league(User.objects.get())


def test_app_invite_works_once(client, app_link):
    signup(client, app_link[1])
    client.logout()
    assert signup(client, app_link[1], username="second").status_code == 404
    assert User.objects.count() == 1


def test_last_use_goes_to_only_one_person(league_link, django_user_model):
    invite, _ = league_link
    Invite.objects.filter(pk=invite.pk).update(uses=invite.max_uses - 1)
    assert invites.redeem(invite, django_user_model.objects.create_user("a"))
    assert not invites.redeem(invite, django_user_model.objects.create_user("b"))
    assert InviteUse.objects.count() == 1


def test_a_failed_signup_spends_nothing(client, league_link):
    invite, token = league_link
    resp = signup(client, token, password="password")
    assert b"too common" in resp.content
    invite.refresh_from_db()
    assert invite.uses == 0
    assert not User.objects.exists()


def test_running_out_mid_signup_leaves_no_account(client, league_link, monkeypatch):
    monkeypatch.setattr(invites, "redeem", lambda invite, user: False)
    assert signup(client, league_link[1]).status_code == 404
    assert not User.objects.exists()


def test_signing_up_keeps_the_token_out_of_the_logs(client, league_link, caplog):
    """A league invite still works after one person uses it."""
    token = league_link[1]
    signup(client, token, password="password")  # the failing form
    signup(client, token)
    assert token not in caplog.text


def test_signup_page_never_echoes_a_password(client, league_link):
    resp = signup(client, league_link[1], password="password")
    assert b'value="password"' not in resp.content


def test_signup_passwords_are_hidden_from_error_reports():
    from web.invite_views import invite

    assert "password1" in _sensitive(invite) and "password2" in _sensitive(invite)


def _sensitive(view):
    """What the decorator tells Django's error reports to hide, seen by calling the view with a marked request."""
    from django.test import RequestFactory

    request = RequestFactory().post("/x/", {})
    request.user = SimpleNamespace(is_authenticated=False)
    try:
        view(request, "nope")
    except Exception:
        pass
    return request.sensitive_post_parameters


# --- already signed in


def test_signed_in_user_joins_without_a_new_account(client, league_link, league, django_user_model):
    user = django_user_model.objects.create_user("existing")
    client.force_login(user)
    assert b"signed in as existing" in client.get(f"/invite/{league_link[1]}/").content
    assert client.post(f"/invite/{league_link[1]}/").url == f"/connect/{league.pk}/team/"
    assert User.objects.count() == 1
    assert connect.can_join(user, league)


def test_following_your_own_link_again_costs_nothing_even_after_it_expires(client, league_link, league):
    invite, token = league_link
    signup(client, token)
    Invite.objects.filter(pk=invite.pk).update(expires_at=timezone.now() - timedelta(days=1))
    assert client.post(f"/invite/{token}/").url == f"/connect/{league.pk}/team/"
    invite.refresh_from_db()
    assert invite.uses == 1


def test_a_member_of_the_league_does_not_spend_a_use(client, sign_in, league, league_link):
    invite, token = league_link
    sign_in(league.teams.first())
    assert client.post(f"/invite/{token}/").status_code == 302
    invite.refresh_from_db()
    assert invite.uses == 0


def test_a_double_submit_costs_one_use(league_link, django_user_model):
    invite, _ = league_link
    user = django_user_model.objects.create_user("a")
    assert invites.redeem(invite, user)
    assert invites.redeem(invite, user)
    invite.refresh_from_db()
    assert invite.uses == 1


def test_signed_in_post_on_a_dead_link_grants_nothing(client, league_link, league, django_user_model):
    invite, token = league_link
    user = django_user_model.objects.create_user("late")
    client.force_login(user)
    Invite.objects.filter(pk=invite.pk).update(uses=invite.max_uses)
    assert client.post(f"/invite/{token}/").status_code == 404
    assert not connect.can_join(user, league)


def test_signed_in_user_refused_at_the_last_moment_is_not_sent_on(client, league_link, django_user_model, monkeypatch):
    client.force_login(django_user_model.objects.create_user("late"))
    monkeypatch.setattr(invites, "redeem", lambda invite, user: False)
    assert client.post(f"/invite/{league_link[1]}/").status_code == 404


def test_a_link_expiring_mid_signup_is_refused(league_link, django_user_model):
    invite, _ = league_link
    stale_copy = Invite.objects.get(pk=invite.pk)
    Invite.objects.filter(pk=invite.pk).update(expires_at=timezone.now() - timedelta(seconds=1))
    user = django_user_model.objects.create_user("late")
    assert not invites.redeem(stale_copy, user)
    assert not InviteUse.objects.exists()
    assert not connect.can_join(user, invite.league)


def test_prompt_to_pick_a_team_stops_once_you_have_one(client, league_link, league):
    signup(client, league_link[1])
    user = User.objects.get()
    assert list(invites.leagues_awaiting_a_team(user)) == [league]
    Membership.objects.create(user=user, league=league, team=league.teams.get(espn_id=1))
    assert not invites.leagues_awaiting_a_team(user).exists()


def test_leaving_means_coming_back_takes_a_new_invite(client, league_link, league, django_user_model):
    # a second member keeps the league alive
    Membership.objects.create(user=django_user_model.objects.create_user("stays"), league=league, team=league.teams.get(espn_id=2))
    signup(client, league_link[1])
    user = User.objects.get(username="newbie")
    assert client.post(f"/connect/{league.pk}/team/", {"team": 1}).status_code == 302
    assert client.post(f"/account/leagues/{league.pk}/leave/").status_code == 302
    assert not Membership.objects.filter(user=user).exists()
    assert not connect.can_join(user, league)


def test_wandering_off_before_picking_a_team_is_not_a_dead_end(client, league_link, league):
    signup(client, league_link[1])
    html = client.get("/startsit/").content.decode()
    assert f"/connect/{league.pk}/team/" in html
    assert league.name in html


def test_no_league_page_offers_connecting_only_to_those_who_may(client, app_link):
    signup(client, app_link[1])
    assert b"Connect your ESPN league" in client.get("/startsit/").content


def test_deleting_an_invite_cancels_it_but_keeps_members(client, league_link, league):
    invite, token = league_link
    signup(client, token)
    client.post(f"/connect/{league.pk}/team/", {"team": league.teams.first().espn_id})
    invite.delete()
    assert Membership.objects.count() == 1
    assert signup(client, token, username="another").status_code in (302, 404)
    assert User.objects.count() == 1


# --- adding leagues


@pytest.fixture
def espn(monkeypatch):
    monkeypatch.setattr(espn_client, "probe_league", lambda espn_id, season, *a: info(espn_id=espn_id, season=season))


def test_league_invite_alone_cannot_add_a_league(espn, django_user_model, two_leagues):
    user = django_user_model.objects.create_user("mate")
    with pytest.raises(ConnectError, match="takes an invite"):
        connect.connect_league(user, 999)
    assert League.objects.count() == 2


def test_anyone_signed_in_can_still_join_a_public_league_already_here(espn, django_user_model, league):
    user = django_user_model.objects.create_user("mate")
    assert connect.connect_league(user, league.espn_id) == league


def test_app_invite_adds_a_league(espn, client, app_link, two_leagues):
    signup(client, app_link[1])
    connect.connect_league(User.objects.get(), 999)
    assert League.objects.filter(espn_id=999).exists()


def test_the_cap_stops_new_leagues_but_not_joining(espn, django_user_model, two_leagues, settings):
    settings.MAX_LEAGUES = 2
    boss = django_user_model.objects.create_superuser("boss")
    with pytest.raises(ConnectError, match="full"):
        connect.connect_league(boss, 999)
    assert League.objects.count() == 2
    assert connect.connect_league(boss, two_leagues[0].espn_id) == two_leagues[0]


def test_the_cap_only_counts_this_season(espn, django_user_model, two_leagues, settings):
    settings.MAX_LEAGUES = 2
    League.objects.filter(pk=two_leagues[0].pk).update(season=2025)
    connect.connect_league(django_user_model.objects.create_superuser("boss"), 999)


def test_private_answer_comes_before_any_invite_answer(monkeypatch, django_user_model, league):
    """Someone without access learns the same thing whether or not the league is stored."""
    from espn_api.requests.espn_requests import ESPNAccessDenied

    def denied(*args, **kwargs):
        raise ESPNAccessDenied("no")

    monkeypatch.setattr(espn_client, "probe_league", denied)
    user = django_user_model.objects.create_user("mate")
    errors = []
    for espn_id in (league.espn_id, 999):
        with pytest.raises(connect.NeedsCookies) as caught:
            connect.connect_league(user, espn_id)
        errors.append(str(caught.value))
    assert errors[0] == errors[1]
    assert not EspnCredential.objects.exists()


# --- admin


@pytest.fixture
def admin_client_(client, django_user_model):
    client.force_login(django_user_model.objects.create_superuser("boss", password=GOOD_PASSWORD))
    return client


def test_admin_shows_the_link_once(admin_client_, league):
    resp = admin_client_.post("/admin/league/invite/add/", {"kind": "league", "league": league.pk}, follow=True)
    assert resp.status_code == 200
    message = next(str(m) for m in resp.context["messages"])
    token = message.rsplit("/invite/", 1)[1].strip("/")
    invite = Invite.objects.get()
    assert invites.find(token) == invite
    assert invite.max_uses == league.team_count
    later = admin_client_.get(f"/admin/league/invite/{invite.pk}/change/").content.decode()
    assert token not in later and invite.token_hash not in later


@pytest.mark.parametrize("kind, with_league", [("league", False), ("app", True)])
def test_admin_form_explains_a_mismatch(admin_client_, league, kind, with_league):
    resp = admin_client_.post("/admin/league/invite/add/", {"kind": kind, "league": league.pk if with_league else ""})
    assert resp.status_code == 200
    assert not Invite.objects.exists()


def test_admin_can_cancel_but_not_edit_an_invite(admin_client_, league_link):
    invite, _ = league_link
    admin_client_.post(f"/admin/league/invite/{invite.pk}/change/", {"kind": "app", "league": ""})
    invite.refresh_from_db()
    assert invite.kind == Invite.LEAGUE
    admin_client_.post(f"/admin/league/invite/{invite.pk}/delete/", {"post": "yes"})
    assert not Invite.objects.exists()


# --- password reset


def reset_link(admin, user):
    resp = admin.post(
        "/admin/auth/user/", {"action": "password_reset_link", "_selected_action": [user.pk]}, follow=True,
    )
    return next(str(m) for m in resp.context["messages"])


def test_reset_link_sets_a_new_password_once(client, admin_client_, django_user_model):
    user = django_user_model.objects.create_user("lost", password=GOOD_PASSWORD)
    message = reset_link(admin_client_, user)
    path = message[message.index("/reset/"):]
    client.logout()
    form_page = client.get(path, follow=True)
    assert form_page.status_code == 200 and b"Set a new password" in form_page.content
    new = "another-fine-phrase-4"
    done = client.post(form_page.redirect_chain[-1][0], {"new_password1": new, "new_password2": new})
    assert (done.status_code, done.url) == (302, "/login/")
    user.refresh_from_db()
    assert user.check_password(new)
    assert b"work anymore" in client.get(path, follow=True).content


def test_reset_rejects_weak_passwords(client, django_user_model):
    user = django_user_model.objects.create_user("lost", password=GOOD_PASSWORD)
    path = f"/reset/{urlsafe_base64_encode(force_bytes(user.pk))}/{default_token_generator.make_token(user)}/"
    page = client.get(path, follow=True)
    resp = client.post(page.redirect_chain[-1][0], {"new_password1": "password", "new_password2": "password"})
    assert resp.status_code == 200 and b"too common" in resp.content
    user.refresh_from_db()
    assert user.check_password(GOOD_PASSWORD)


def test_reset_link_action_takes_one_user(admin_client_, django_user_model):
    users = [django_user_model.objects.create_user(n) for n in ("a", "b")]
    resp = admin_client_.post("/admin/auth/user/", {"action": "password_reset_link", "_selected_action": [u.pk for u in users]}, follow=True)
    assert "one user" in next(str(m) for m in resp.context["messages"])


def test_staff_cannot_get_a_superusers_reset_link(client, django_user_model):
    from django.contrib.auth.models import Permission

    staff = django_user_model.objects.create_user("staff", is_staff=True)
    staff.user_permissions.add(*Permission.objects.filter(codename__in=["change_user", "view_user"]))
    boss = django_user_model.objects.create_superuser("boss")
    client.force_login(staff)
    message = reset_link(client, boss)
    assert "/reset/" not in message


# --- sign-in throttle


def fail_login(client, username="target"):
    return client.post("/login/", {"username": username, "password": "wrong"})


def test_five_wrong_passwords_pause_that_username(client, django_user_model):
    django_user_model.objects.create_user("target", password=GOOD_PASSWORD)
    for _ in range(4):
        assert fail_login(client).status_code == 200
    locked = fail_login(client)
    assert locked.status_code == 429
    assert b"Too many tries" in locked.content
    # the right password doesn't help now, and that's the point
    assert client.post("/login/", {"username": "target", "password": GOOD_PASSWORD}).status_code == 429


def test_a_forged_address_does_not_dodge_the_pause(client, django_user_model):
    django_user_model.objects.create_user("target", password=GOOD_PASSWORD)
    for n in range(5):
        resp = client.post("/login/", {"username": "target", "password": "wrong"}, HTTP_X_FORWARDED_FOR=f"10.0.0.{n}", REMOTE_ADDR=f"10.0.1.{n}")
    assert resp.status_code == 429


def test_one_username_pausing_does_not_touch_others(client, django_user_model):
    django_user_model.objects.create_user("target", password=GOOD_PASSWORD)
    django_user_model.objects.create_user("bystander", password=GOOD_PASSWORD)
    for _ in range(5):
        fail_login(client)
    assert client.post("/login/", {"username": "bystander", "password": GOOD_PASSWORD}).status_code == 302


def test_success_clears_the_count(client, django_user_model):
    django_user_model.objects.create_user("target", password=GOOD_PASSWORD)
    for _ in range(4):
        fail_login(client)
    assert client.post("/login/", {"username": "target", "password": GOOD_PASSWORD}).status_code == 302
    client.logout()
    assert fail_login(client).status_code == 200


def test_unknown_usernames_pause_the_same_way(client):
    """Or the pause would reveal which usernames exist."""
    statuses = [fail_login(client, "nobody").status_code for _ in range(5)]
    assert statuses == [200, 200, 200, 200, 429]


def test_admin_login_is_throttled_too(client, django_user_model):
    django_user_model.objects.create_superuser("boss", password=GOOD_PASSWORD)
    for _ in range(5):
        resp = client.post("/admin/login/", {"username": "boss", "password": "wrong"})
    assert resp.status_code == 429


# --- Claude allowance


@pytest.fixture
def claude(monkeypatch, settings):
    settings.EXPLAIN_DAILY_CAP = 2
    calls = []

    def ask(kind, body, description):
        calls.append(body)
        return "Start Kraft."

    monkeypatch.setattr(narrate, "_ask_claude", ask)
    monkeypatch.setattr(narrate, "league_description", lambda league: "a league")
    return calls


@pytest.fixture
def mate(django_user_model):
    return django_user_model.objects.create_user("mate")


def ask(user, n):
    return narrate.explain("startsit", {"n": n}, None, user)


def test_cap_stops_new_explanations_without_calling_claude(claude, mate):
    ask(mate, 1), ask(mate, 2)
    with pytest.raises(narrate.NarrationError, match="today's 2"):
        ask(mate, 3)
    assert len(claude) == 2
    assert ExplainUsage.objects.get(user=mate).count == 2


def test_cached_explanations_are_free_and_work_past_the_cap(claude, mate):
    ask(mate, 1), ask(mate, 1), ask(mate, 2)
    assert ExplainUsage.objects.get(user=mate).count == 2
    assert ask(mate, 1) == "Start Kraft."


def test_one_person_at_the_cap_does_not_stop_another(claude, mate, django_user_model):
    ask(mate, 1), ask(mate, 2)
    ask(django_user_model.objects.create_user("other"), 3)


def test_cap_resets_the_next_day(claude, mate):
    ask(mate, 1), ask(mate, 2)
    ExplainUsage.objects.filter(user=mate).update(day=timezone.localdate() - timedelta(days=1))
    ask(mate, 3)


def test_a_failed_call_gives_the_allowance_back(monkeypatch, mate, settings):
    settings.EXPLAIN_DAILY_CAP = 1

    def fail(*args):
        raise narrate.NarrationError("nope")

    monkeypatch.setattr(narrate, "_ask_claude", fail)
    monkeypatch.setattr(narrate, "league_description", lambda league: "a league")
    with pytest.raises(narrate.NarrationError):
        ask(mate, 1)
    assert ExplainUsage.objects.get(user=mate).count == 0


def test_an_unexpected_failure_gives_it_back_too(monkeypatch, mate):
    def fail(*args):
        raise RuntimeError("nope")

    monkeypatch.setattr(narrate, "_ask_claude", fail)
    monkeypatch.setattr(narrate, "league_description", lambda league: "a league")
    with pytest.raises(RuntimeError):
        ask(mate, 1)
    assert ExplainUsage.objects.get(user=mate).count == 0


def test_giving_it_back_leaves_other_days_alone(monkeypatch, mate):
    yesterday = timezone.localdate() - timedelta(days=1)
    ExplainUsage.objects.create(user=mate, day=yesterday, count=5)

    def fail(*args):
        raise narrate.NarrationError("nope")

    monkeypatch.setattr(narrate, "_ask_claude", fail)
    monkeypatch.setattr(narrate, "league_description", lambda league: "a league")
    with pytest.raises(narrate.NarrationError):
        ask(mate, 1)
    assert ExplainUsage.objects.get(user=mate, day=yesterday).count == 5


def test_the_page_charges_whoever_is_signed_in(claude, sign_in, league, django_user_model, monkeypatch):
    bystander = django_user_model.objects.create_user("bystander")
    monkeypatch.setitem(views.EXPLAINERS, "startsit", lambda request: {"n": 1})
    client = sign_in(league.teams.get(espn_id=1), username="reader")
    assert client.get("/explain/startsit/").status_code == 200
    assert ExplainUsage.objects.get().user == User.objects.get(username="reader")
    assert not ExplainUsage.objects.filter(user=bystander).exists()


def test_only_staff_see_how_to_fix_the_key(monkeypatch, mate, django_user_model, settings):
    settings.ANTHROPIC_API_KEY = None
    monkeypatch.setattr(narrate, "league_description", lambda league: "a league")
    with pytest.raises(narrate.NarrationError) as seen:
        ask(mate, 1)
    assert "ANTHROPIC" not in str(seen.value)
    with pytest.raises(narrate.NarrationError, match="ANTHROPIC_API_KEY"):
        ask(django_user_model.objects.create_user("s", is_staff=True), 2)


# --- pruning


def test_sync_prunes_old_explanations_and_usage(mate, monkeypatch):
    monkeypatch.setattr("league.management.commands.sync_all.STEPS", {})
    old = Explanation.objects.create(key="old", kind="k", text="t")
    Explanation.objects.filter(pk=old.pk).update(created_at=timezone.now() - timedelta(days=31))
    Explanation.objects.create(key="new", kind="k", text="t")
    today = timezone.localdate()
    ExplainUsage.objects.create(user=mate, day=today)
    ExplainUsage.objects.create(user=mate, day=today - timedelta(days=5))
    call_command("sync_all")
    assert list(Explanation.objects.values_list("key", flat=True)) == ["new"]
    assert list(ExplainUsage.objects.values_list("day", flat=True)) == [today]


# --- password rules


@pytest.mark.parametrize(
    "username, password, reason",
    [
        ("newbie", "xk7#q", b"too short"),
        ("newbie", "password", b"too common"),
        ("newbie", "804719263551", b"entirely numeric"),
        ("kraftfan", "kraftfan1", b"too similar"),
    ],
)
def test_each_password_rule_is_on(league_link, client, username, password, reason):
    resp = signup(client, league_link[1], username=username, password=password)
    assert resp.status_code == 200 and reason in resp.content
    assert not User.objects.exists()
