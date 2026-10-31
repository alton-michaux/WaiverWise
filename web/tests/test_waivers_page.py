from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone

from analysis.matchups import Rating
from analysis.projections import Projection
from league.models import League, Player, RosterEntry, Team
from web import narrate, services

pytestmark = pytest.mark.django_db

SLOTS = {"WR": 1, "BE": 1}


@pytest.fixture
def mine(settings):
    settings.ESPN_MY_TEAM_ID = 1
    league = League.objects.create(
        espn_id=1, season=2026, name="L", team_count=2, current_week=16, reg_season_weeks=14, final_week=17,
        lineup_slots=SLOTS,
    )
    return Team.objects.create(league=league, espn_id=1, name="My Team", abbrev="MY")


@pytest.fixture
def signed_in(client, mine):
    client.force_login(get_user_model().objects.create_user("u", password="p"))
    return client


@pytest.fixture
def means(monkeypatch):
    """Player name -> weekly mean."""
    values = {}

    def fake(league, players, weeks):
        return {(p.pk, w): Projection(values[p.name], values[p.name] * 0.4) for p in players for w in weeks}

    monkeypatch.setattr(services, "weekly_projections", fake)
    return values


def add(espn_id, name, position="WR", **fields):
    fields.setdefault("eligible_slots", [position, "BE"])
    return Player.objects.create(espn_id=espn_id, name=name, position=position, pro_team="NYJ", **fields)


def rostered(team, slot, espn_id, name):
    RosterEntry.objects.create(team=team, player=add(espn_id, name), lineup_slot=slot)


@pytest.fixture
def full_roster(mine, means):
    rostered(mine, "WR", 1, "Starter")
    rostered(mine, "BE", 2, "Bench Guy")
    means.update({"Starter": 10.0, "Bench Guy": 2.0})


def test_pickups_that_help_lead_and_the_rest_fold_away(signed_in, full_roster, means):
    add(3, "Great Add", fa_status="FREEAGENT")
    add(4, "Useless Add", fa_status="FREEAGENT")
    means.update({"Great Add": 20.0, "Useless Add": 1.0})
    resp = signed_in.get("/waivers/")
    assert [r.player.name for r in resp.context["helpful_rows"]] == ["Great Add"]
    assert [r.player.name for r in resp.context["other_rows"]] == ["Useless Add"]
    html = resp.content.decode()
    assert "1 more with no lineup gain" in html
    assert '<details class="more-rows" >' in html
    assert "Drop Bench Guy" in html


def test_nothing_helpful_opens_the_fold_and_says_so(signed_in, full_roster, means):
    add(4, "Useless Add", fa_status="FREEAGENT")
    means["Useless Add"] = 1.0
    html = signed_in.get("/waivers/").content.decode()
    assert "Nobody improves your lineup right now." in html
    assert '<details class="more-rows" open>' in html


def test_position_chip_filters(signed_in, full_roster, means):
    add(3, "Great WR", fa_status="FREEAGENT")
    add(5, "Some QB", position="QB", fa_status="FREEAGENT")
    means.update({"Great WR": 20.0, "Some QB": 15.0})
    resp = signed_in.get("/waivers/", {"position": "QB"})
    assert [r.player.name for r in resp.context["rows"]] == ["Some QB"]
    assert "Nobody at QB improves your lineup right now." in resp.content.decode()


def test_position_chip_for_dst_filters(signed_in, full_roster):
    assert 'href="?position=D/ST"' in signed_in.get("/waivers/").content.decode()
    assert signed_in.get("/waivers/", {"position": "D/ST"}).context["position"] == "D/ST"


def test_no_free_agents_at_all(signed_in, full_roster):
    html = signed_in.get("/waivers/").content.decode()
    assert "No free agents with remaining projections." in html
    assert "more-rows" not in html


def test_free_drop_is_called_safe(signed_in, full_roster):
    assert "Safe to drop: Bench Guy." in signed_in.get("/waivers/").content.decode()


def test_costly_drop_states_the_cost(signed_in, mine, means):
    # no bench, so whoever goes was starting
    mine.league.lineup_slots = {"WR": 2}
    mine.league.save()
    rostered(mine, "WR", 1, "Starter")
    rostered(mine, "WR", 2, "Other WR")
    means.update({"Starter": 10.0, "Other WR": 9.0})
    resp = signed_in.get("/waivers/")
    assert resp.context["drop_cost"] == 18.0  # 9 points over weeks 16 and 17
    assert "Cheapest drop: Other WR, costing 18.0 points." in resp.content.decode()


def test_open_roster_spot_is_explained(signed_in, mine, means):
    rostered(mine, "WR", 1, "Starter")
    add(3, "Great Add", fa_status="FREEAGENT")
    means.update({"Starter": 10.0, "Great Add": 20.0})
    html = signed_in.get("/waivers/").content.decode()
    assert "You have an open roster spot" in html
    assert "Open roster spot" in html


def test_kicker_pickup_replaces_your_kicker(signed_in, mine, means):
    mine.league.lineup_slots = {"WR": 1, "K": 1, "BE": 1}
    mine.league.save()
    rostered(mine, "WR", 1, "Starter")
    rostered(mine, "BE", 2, "Bench Guy")
    RosterEntry.objects.create(team=mine, player=add(3, "My Kicker", position="K"), lineup_slot="K")
    add(4, "New Kicker", position="K", fa_status="FREEAGENT")
    means.update({"Starter": 10.0, "Bench Guy": 2.0, "My Kicker": 8.0, "New Kicker": 12.0})

    assert "Safe to drop: Bench Guy." in signed_in.get("/waivers/").content.decode()
    html = signed_in.get("/waivers/", {"position": "K"}).content.decode()
    assert "Drop My Kicker" in html
    assert "Cheapest drop: My Kicker" in html


def test_team_with_no_players_renders(signed_in, mine, means):
    assert signed_in.get("/waivers/").status_code == 200


def test_waiver_tag_handles_missing_clear_time(signed_in, full_roster, means):
    add(3, "Great Add", fa_status="WAIVERS")
    add(4, "Timed Add", fa_status="WAIVERS", waiver_clears=timezone.now() + timedelta(days=1))
    means.update({"Great Add": 20.0, "Timed Add": 19.0})
    html = signed_in.get("/waivers/").content.decode()
    assert "On waivers" in html
    assert "Waivers · clears" in html


def test_injury_status_reads_plainly(signed_in, full_roster, means):
    add(3, "Hurt Add", fa_status="FREEAGENT", injury_status="INJURY_RESERVE")
    means["Hurt Add"] = 20.0
    html = signed_in.get("/waivers/").content.decode()
    assert "Injury_Reserve" not in html
    assert ">IR<" in html


def row(ros_gain, week_gain):
    return services.WaiverRow(None, None, 0, 0, week_gain, ros_gain, None)


@pytest.mark.parametrize(
    "ros_gain, week_gain, helps",
    [
        (12.4, 0.0, True),
        (0.0, 4.6, True),  # a one-week fill-in still helps
        (0.0, 0.0, False),
        (0.04, 0.04, False),  # would display as +0.0
        (-3.0, 0.0, False),
    ],
)
def test_what_counts_as_helping(ros_gain, week_gain, helps):
    assert row(ros_gain, week_gain).helps is helps


TOUGH_SCHEDULE = Rating(-3.0, 30, "Tough")
GOOD_SCHEDULE = Rating(3.0, 2, "Good")
AVERAGE_SCHEDULE = Rating(0.1, 15, "Neutral")


def grade_schedules(monkeypatch, ros, playoffs):
    monkeypatch.setattr(services.ScheduleGrades, "ros", lambda self, player: ros)
    monkeypatch.setattr(services.ScheduleGrades, "playoffs", lambda self, player: playoffs)


def test_tough_schedule_gets_a_chip_and_detail(signed_in, full_roster, means, monkeypatch):
    add(5, "Pickup", fa_status="FREEAGENT")
    means["Pickup"] = 20.0
    grade_schedules(monkeypatch, TOUGH_SCHEDULE, GOOD_SCHEDULE)
    html = signed_in.get("/waivers/").content.decode()
    assert "Tough schedule" in html
    assert "<dt>Playoff schedule</dt><dd>Good</dd>" in html


def test_average_schedule_has_no_chip(signed_in, full_roster, means, monkeypatch):
    add(5, "Pickup", fa_status="FREEAGENT")
    means["Pickup"] = 20.0
    grade_schedules(monkeypatch, AVERAGE_SCHEDULE, None)
    html = signed_in.get("/waivers/").content.decode()
    assert " schedule</span>" not in html
    assert "<dt>Schedule</dt><dd>Neutral</dd>" in html
    assert "Playoff schedule" not in html


def test_schedules_never_change_the_ranking(mine, full_roster, means, monkeypatch):
    add(5, "Pickup", fa_status="FREEAGENT")
    add(6, "Other Pickup", fa_status="FREEAGENT")
    means.update({"Pickup": 20.0, "Other Pickup": 15.0})

    def ranking():
        return [(r.player.name, r.ros_gain, r.next_week_gain) for r in services.waivers(mine)["rows"]]

    grade_schedules(monkeypatch, GOOD_SCHEDULE, GOOD_SCHEDULE)
    good = ranking()
    grade_schedules(monkeypatch, TOUGH_SCHEDULE, TOUGH_SCHEDULE)
    assert ranking() == good


def test_explanation_gets_only_notable_schedules(mine, full_roster, means, monkeypatch):
    add(5, "Pickup", fa_status="FREEAGENT")
    means["Pickup"] = 20.0
    grade_schedules(monkeypatch, TOUGH_SCHEDULE, AVERAGE_SCHEDULE)
    target = narrate.waivers_payload(services.waivers(mine))["targets"][0]
    assert target["ros_schedule"] == "Tough"
    assert "playoff_schedule" not in target
