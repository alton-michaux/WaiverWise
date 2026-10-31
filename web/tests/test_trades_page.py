import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.template.loader import render_to_string

from analysis.matchups import Rating
from analysis.projections import Projection
from league.models import League, Player, RosterEntry, SyncRun, Team
from web import deals, services
from web.models import TradeDeals

pytestmark = pytest.mark.django_db


def add(team, slot, espn_id, name, position, mean, means):
    player = Player.objects.create(
        espn_id=espn_id, name=name, position=position, pro_team="NYJ", eligible_slots=[position, "BE"]
    )
    RosterEntry.objects.create(team=team, player=player, lineup_slot=slot)
    means[name] = mean
    return player


@pytest.fixture
def means(monkeypatch):
    values = {}

    def fake(league, players, weeks):
        return {(p.pk, w): Projection(values.get(p.name, 0), values.get(p.name, 0) * 0.4) for p in players for w in weeks}

    monkeypatch.setattr(services, "weekly_projections", fake)
    return values


@pytest.fixture
def league(settings):
    settings.ESPN_MY_TEAM_ID = 1
    return League.objects.create(
        espn_id=1, season=2026, name="L", team_count=3, current_week=16, reg_season_weeks=14, final_week=17,
        lineup_slots={"RB": 1, "WR": 1, "BE": 1},
    )


@pytest.fixture
def mine(league, means):
    """Deep at RB, weak at WR."""
    team = Team.objects.create(league=league, espn_id=1, name="My Team", abbrev="MY")
    add(team, "RB", 1, "My Star RB", "RB", 20.0, means)
    add(team, "BE", 2, "My Spare RB", "RB", 15.0, means)
    add(team, "WR", 3, "My Weak WR", "WR", 5.0, means)
    return team


@pytest.fixture
def partner(league, means):
    """Deep at WR, weak at RB: the natural trade partner."""
    team = Team.objects.create(league=league, espn_id=2, name="Partner", abbrev="PA")
    add(team, "WR", 4, "Their Star WR", "WR", 20.0, means)
    add(team, "BE", 5, "Their Spare WR", "WR", 15.0, means)
    add(team, "RB", 6, "Their Weak RB", "RB", 5.0, means)
    return team


@pytest.fixture
def signed_in(client, mine):
    client.force_login(get_user_model().objects.create_user("u", password="p"))
    return client


def test_finds_and_stores_a_deal_both_teams_like(mine, partner):
    result = deals.best_deals(mine)
    assert result["done"] and result["total"] >= 1
    top = result["deals"][0]
    assert [p["name"] for p in top["gets"]] == ["Their Star WR"]
    assert [p["name"] for p in top["gives"]] == ["My Spare RB"]
    # weeks 16 and 17: my WR goes 5 -> 20; their RB goes 5 -> 15 but their WR drops 20 -> 15
    assert (top["you"], top["them"]) == (30.0, 10.0)
    assert TradeDeals.objects.filter(team=mine, partner=partner).count() == 1


def test_second_read_does_not_search_again(mine, partner, monkeypatch):
    deals.best_deals(mine)

    def no_search(*args):
        raise AssertionError("already stored, so this shouldn't search")

    monkeypatch.setattr(deals, "TradeContext", no_search)
    assert deals.best_deals(mine)["done"]


def test_new_sync_searches_again_and_clears_the_old_answer(mine, partner):
    deals.best_deals(mine)
    old = TradeDeals.objects.get().version
    SyncRun.objects.create(step="league", ok=True)
    assert not deals.best_deals(mine, search=False)["done"]
    deals.best_deals(mine)
    assert TradeDeals.objects.get().version != old


def test_budget_returns_partial_results_and_then_finishes(mine, partner, league, means):
    other = Team.objects.create(league=league, espn_id=3, name="Other", abbrev="OT")
    add(other, "WR", 7, "Other WR", "WR", 1.0, means)
    first = deals.best_deals(mine, budget=0)
    assert (first["checked"], first["of"], first["done"]) == (1, 2, False)
    second = deals.best_deals(mine, budget=0)
    assert (second["checked"], second["done"]) == (2, True)


def test_ranking_caps_repeats_of_one_target_unless_filtered(mine, partner):
    def deal(you, target):
        person = {"id": target, "name": "x", "position": "WR", "ros": 1}
        return {"you": you, "them": 1, "gets": [person], "gives": [person]}

    row = TradeDeals(team=mine, partner=partner, version="v", total=4, deals=[deal(n, 9) for n in (4, 3, 2)] + [deal(1, 8)])
    assert [d["you"] for d in deals._rank(mine, [row], cap_targets=True)] == [4, 3, 1]
    assert [d["you"] for d in deals._rank(mine, [row], cap_targets=False)] == [4, 3, 2, 1]


def test_page_shows_stored_deals_without_another_request(signed_in, partner, mine):
    deals.best_deals(mine)
    html = signed_in.get("/trades/").content.decode()
    assert "Their Spare WR" in html
    assert "where both teams gain" in html
    assert "hx-trigger" not in html.split('id="best-deals"')[1].split(">")[0]


def test_page_without_stored_deals_loads_them_after(signed_in, partner):
    html = signed_in.get("/trades/").content.decode()
    assert "Finding deals across the league" in html
    assert "Their Spare WR" not in html
    assert "/trades/best/" in html


def test_partial_keeps_asking_until_done(signed_in, partner, league, means, monkeypatch):
    other = Team.objects.create(league=league, espn_id=3, name="Other", abbrev="OT")
    add(other, "WR", 7, "Other WR", "WR", 1.0, means)
    monkeypatch.setattr(deals, "BUDGET_SECONDS", 0)
    first = signed_in.get("/trades/best/").content.decode()
    assert "Checked 1 of 2 teams" in first
    assert 'hx-trigger="load' in first
    second = signed_in.get("/trades/best/").content.decode()
    assert "hx-trigger=\"load" not in second
    assert "where both teams gain" in second


def test_filter_by_partner(signed_in, partner):
    html = signed_in.get("/trades/best/", {"partner": 2}).content.decode()
    assert "with Partner" in html


def test_filter_rejects_your_own_team(signed_in, partner):
    assert signed_in.get("/trades/best/", {"partner": 1}).status_code == 404


def test_builder_view(signed_in, partner):
    resp = signed_in.get("/trades/", {"view": "build"})
    assert resp.context["view"] == "build"
    assert "Their Star WR" in resp.content.decode()
    assert "Evaluate" in resp.content.decode()


def test_picked_players_open_the_builder_with_a_verdict(signed_in, mine, partner):
    give = Player.objects.get(name="My Spare RB").pk
    get = Player.objects.get(name="Their Spare WR").pk
    resp = signed_in.get("/trades/", {"partner": 2, "give": give, "get": get})
    assert resp.context["view"] == "build"
    assert "Both teams improve" in resp.content.decode()


def test_deal_links_explain_and_builder_to_the_same_trade(signed_in, mine, partner):
    deals.best_deals(mine)
    deal = deals.best_deals(mine, search=False)["deals"][0]
    assert "partner=2" in deal["query"] and "give=" in deal["query"] and "get=" in deal["query"]
    resp = signed_in.get("/trades/?" + deal["query"])
    assert resp.context["result"] is not None


def test_sync_searches_deals_ahead_of_time(mine, partner, monkeypatch):
    monkeypatch.setattr("league.management.commands.sync_all.STEPS", {})
    call_command("sync_all")
    assert TradeDeals.objects.filter(team=mine).exists()


def test_failed_deal_search_does_not_fail_the_sync(mine, partner, monkeypatch):
    monkeypatch.setattr("league.management.commands.sync_all.STEPS", {})

    def boom(*args, **kwargs):
        raise RuntimeError("nope")

    monkeypatch.setattr("web.management.commands.refresh_best_deals.best_deals", boom)
    call_command("sync_all")


def test_refresh_skips_quietly_without_a_team(db, settings):
    settings.ESPN_MY_TEAM_ID = None
    call_command("refresh_best_deals")


def grade_schedules(monkeypatch, ros, playoffs):
    monkeypatch.setattr(services.ScheduleGrades, "ros", lambda self, player: ros)
    monkeypatch.setattr(services.ScheduleGrades, "playoffs", lambda self, player: playoffs)


def test_verdict_shows_each_players_schedule(signed_in, partner, mine, monkeypatch):
    grade_schedules(monkeypatch, Rating(-3.0, 30, "Tough"), Rating(3.0, 2, "Good"))
    deals.best_deals(mine)
    html = signed_in.get("/trades/").content.decode()
    assert "Schedule: Tough · Playoffs: Good" in html


def test_deals_stored_before_schedules_existed_still_render(signed_in, mine, partner):
    person = {"id": 1, "name": "Old Deal Guy", "position": "WR", "ros": 1}
    deal = {
        "you": 2, "them": 1, "you_before": 1, "you_after": 3, "them_before": 1, "them_after": 2,
        "gets": [person], "gives": [person], "you_drop": [], "them_drop": [],
    }
    version = deals.data_version(mine.league)
    TradeDeals.objects.create(team=mine, partner=partner, version=version, total=1, deals=[deal])
    html = render_to_string("web/_best_deals.html", {"best": deals.best_deals(mine, partner, search=False)})
    assert "Old Deal Guy" in html
    assert "Schedule:" not in html


def test_schedules_never_change_the_deals(mine, partner, monkeypatch):
    def found():
        ctx = services.TradeContext(mine.league)
        return [(t.a.gives, t.a.gets, t.a.gain, t.b.gain) for t in ctx.find(mine, partner)]

    grade_schedules(monkeypatch, Rating(3.0, 2, "Good"), Rating(3.0, 2, "Good"))
    good = found()
    grade_schedules(monkeypatch, Rating(-3.0, 30, "Tough"), Rating(-3.0, 30, "Tough"))
    assert found() == good
