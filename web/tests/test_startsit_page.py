from datetime import timedelta
from types import SimpleNamespace

import numpy as np
import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone

from analysis.matchups import Rating
from analysis.projections import Projection
from league.models import League, Matchup, Player, ProGame, RosterEntry, Team
from nfl.models import GameLine
from web import narrate, services
from web.templatetags.waiverwise import slot_label, status_label

pytestmark = pytest.mark.django_db


def add_player(team, slot, espn_id, name, pro_team):
    player = Player.objects.create(
        espn_id=espn_id, name=name, position="WR", pro_team=pro_team, eligible_slots=["WR", "BE"]
    )
    RosterEntry.objects.create(team=team, player=player, lineup_slot=slot)
    return player


@pytest.fixture
def league(settings):
    settings.ESPN_MY_TEAM_ID = 1
    return League.objects.create(
        espn_id=1, season=2026, name="L", team_count=2, current_week=8, reg_season_weeks=14,
        lineup_slots={"WR": 1, "BE": 1},
    )


@pytest.fixture
def mine(league):
    return Team.objects.create(league=league, espn_id=1, name="My Team", abbrev="MY")


@pytest.fixture
def rival(league):
    return Team.objects.create(league=league, espn_id=2, name="Rival Team", abbrev="RV")


@pytest.fixture
def signed_in(client, mine):
    client.force_login(get_user_model().objects.create_user("u", password="p"))
    return client


@pytest.fixture
def projections(monkeypatch):
    """Player name -> mean, or None for a bye."""
    means = {}

    def fake(league, players, weeks):
        return {
            (p.pk, w): (Projection(means[p.name], means[p.name] * 0.4) if means.get(p.name) is not None else None)
            for p in players
            for w in weeks
        }

    monkeypatch.setattr(services, "weekly_projections", fake)
    return means


@pytest.fixture
def lineup_with_a_move(mine, projections):
    add_player(mine, "WR", 1, "Stefon Diggs", "WSH")
    add_player(mine, "BE", 2, "Garrett Wilson", "NYJ")
    projections.update({"Stefon Diggs": 11.2, "Garrett Wilson": 14.0})


def test_page_leads_with_the_move_and_its_deadline(signed_in, lineup_with_a_move):
    ProGame.objects.create(season=2026, week=8, team="NYJ", opponent="LV", kickoff=timezone.now() + timedelta(days=1))
    html = signed_in.get("/startsit/").content.decode()
    assert "1 move to make" in html
    assert "Set by" in html
    assert "Why this move?" in html
    assert "+2.8 pts" in html
    for label in ("Balanced", "Safe floor", "Big upside"):
        assert label in html


def test_win_chance_shows_when_there_is_an_opponent(signed_in, mine, rival, lineup_with_a_move, projections):
    add_player(rival, "WR", 3, "Their Guy", "BUF")
    projections["Their Guy"] = 10.0
    Matchup.objects.create(league=mine.league, week=8, team_a=mine, team_b=rival)
    html = signed_in.get("/startsit/").content.decode()
    assert "chance to win" in html
    assert "vs Rival Team" in html


def test_no_opponent_still_renders(signed_in, lineup_with_a_move):
    html = signed_in.get("/startsit/").content.decode()
    assert "chance to win" not in html
    assert "No matchup this week" in html


def test_set_lineup_says_so(signed_in, mine, projections):
    add_player(mine, "WR", 1, "Stefon Diggs", "WSH")
    add_player(mine, "BE", 2, "Garrett Wilson", "NYJ")
    projections.update({"Stefon Diggs": 14.0, "Garrett Wilson": 11.2})
    html = signed_in.get("/startsit/?mode=floor").content.decode()
    assert "Your lineup is set" in html
    assert "Nothing to change for Safe floor" in html


def test_benching_a_player_on_bye_renders(signed_in, mine, projections):
    add_player(mine, "WR", 1, "Malik Nabers", "NYG")
    add_player(mine, "BE", 2, "Garrett Wilson", "NYJ")
    projections.update({"Malik Nabers": None, "Garrett Wilson": 14.0})
    html = signed_in.get("/startsit/").content.decode()
    assert "1 move to make" in html
    assert "bye" in html


def test_empty_lineup_slot_renders_as_a_fill(signed_in, mine, lineup_with_a_move, projections):
    mine.league.lineup_slots = {"WR": 2, "BE": 1}
    mine.league.save()
    html = signed_in.get("/startsit/").content.decode()
    assert "1 move to make" in html
    assert "Fills an empty lineup spot" in html
    assert signed_in.get("/startsit/moves/").status_code == 200


def test_explaining_an_empty_slot_fill_builds_a_payload(mine, lineup_with_a_move):
    mine.league.lineup_slots = {"WR": 2, "BE": 1}
    mine.league.save()
    moves = narrate.startsit_payload(services.start_sit(mine))["recommended_moves"]
    assert moves == [{"start": "Garrett Wilson", "bench": None, "slot": "empty slot"}]


def test_unknown_mode_falls_back_to_balanced(signed_in, lineup_with_a_move):
    resp = signed_in.get("/startsit/?mode=nope")
    assert resp.context["mode"] == "mean"
    assert resp.context["mode_label"] == "Balanced"


def test_badge_counts_moves_without_simulating(signed_in, lineup_with_a_move, monkeypatch, rival):
    def no_sim(*args, **kwargs):
        raise AssertionError("the badge runs on every page, so it can't simulate")

    monkeypatch.setattr("analysis.startsit.win_probability", no_sim)
    html = signed_in.get("/startsit/moves/").content.decode()
    assert 'class="badge"' in html
    assert ">1<" in html


def test_badge_is_empty_with_nothing_to_do(signed_in, mine, projections):
    add_player(mine, "WR", 1, "Stefon Diggs", "WSH")
    projections["Stefon Diggs"] = 14.0
    assert b"badge" not in signed_in.get("/startsit/moves/").content


def row_for(name, kickoff_team="NYJ"):
    return SimpleNamespace(player=SimpleNamespace(name=name, pro_team=kickoff_team))


def test_deadline_is_the_first_game_still_to_come():
    now = timezone.now()
    moves = [(row_for("a", "EARLY"), row_for("b", "LATE")), (row_for("c", "PLAYED"), row_for("d", "NONE"))]
    kickoffs = {"EARLY": now + timedelta(hours=1), "LATE": now + timedelta(hours=5), "PLAYED": now - timedelta(hours=1)}
    assert services.move_deadline(moves, kickoffs) == kickoffs["EARLY"]


def test_no_deadline_without_kickoffs():
    assert services.move_deadline([(row_for("a"), row_for("b"))], {}) is None
    assert services.move_deadline([], {}) is None


def summary(now, best):
    return services.win_summary(SimpleNamespace(current_win_prob=now, best_win_prob=best))


def test_win_summary_reports_the_cost_of_a_safer_lineup():
    result = summary(0.793, 0.786)
    assert (result["now"], result["best"], result["cost"]) == (79.3, 78.6, 0.7)
    assert result["bar_low"] == 78.6


def test_win_summary_gain_has_no_cost():
    result = summary(0.7931, 0.8163)
    assert result["cost"] == 0
    assert (result["bar_low"], result["bar_gain"]) == (79.3, 2.3)


def test_win_summary_needs_an_opponent():
    assert summary(None, None) is None


def player_row(projection, locked=False):
    return services.PlayerRow(None, "WR", projection, "BUF", locked)


def test_bar_positions_are_percent_of_the_scale():
    bar = player_row(Projection(20.0, 10.0)).bar
    p = Projection(20.0, 10.0)
    assert bar["mark"] == 50.0
    assert bar["left"] == round(p.floor / 40 * 100, 1)


def test_bar_clamps_huge_ceilings():
    bar = player_row(Projection(45.0, 10.0)).bar
    assert bar["mark"] == 100.0
    assert bar["left"] + bar["width"] <= 100.0


@pytest.mark.parametrize("row", [player_row(None), player_row(Projection(10.0, 2.0), locked=True), player_row(Projection(0, 0))])
def test_no_bar_for_byes_locked_games_or_zero_projections(row):
    assert row.bar is None


@pytest.mark.parametrize(
    "status, expected",
    [("QUESTIONABLE", "Questionable"), ("INJURY_RESERVE", "IR"), ("OUT", "Out")],
)
def test_status_label(status, expected):
    assert status_label(status) == expected


def test_slot_label():
    assert slot_label("RB/WR/TE") == "FLEX"
    assert slot_label("QB") == "QB"


GOOD = Rating(3.0, 2, "Good")
TOUGH = Rating(-3.0, 31, "Tough")
AVERAGE = Rating(0.1, 15, "Neutral")


@pytest.fixture
def rated(monkeypatch):
    """(defense, position) -> Rating."""
    table = {}
    monkeypatch.setattr(services, "defense_ratings", lambda league: table)
    return table


@pytest.fixture
def game_against_mia():
    ProGame.objects.create(season=2026, week=8, team="NYJ", opponent="MIA", kickoff=timezone.now() + timedelta(days=1))


def test_good_and_tough_matchups_get_a_chip(signed_in, lineup_with_a_move, game_against_mia, rated):
    rated[("MIA", "WR")] = GOOD
    assert "Good matchup" in signed_in.get("/startsit/").content.decode()
    rated[("MIA", "WR")] = TOUGH
    assert "Tough matchup" in signed_in.get("/startsit/").content.decode()


def test_neutral_matchup_has_no_chip(signed_in, lineup_with_a_move, game_against_mia, rated):
    rated[("MIA", "WR")] = AVERAGE
    assert " matchup</span>" not in signed_in.get("/startsit/").content.decode()


def test_started_game_has_no_chip(signed_in, lineup_with_a_move, rated):
    ProGame.objects.create(season=2026, week=8, team="NYJ", opponent="MIA", kickoff=timezone.now() - timedelta(hours=1))
    rated[("MIA", "WR")] = GOOD
    assert "Good matchup" not in signed_in.get("/startsit/").content.decode()


def test_away_game_reads_at_and_home_or_unknown_reads_vs(signed_in, lineup_with_a_move, game_against_mia):
    assert "NYJ vs MIA" in signed_in.get("/startsit/").content.decode()
    line = GameLine.objects.create(season=2026, week=8, team="NYJ", is_home=False)
    assert "NYJ @ MIA" in signed_in.get("/startsit/").content.decode()
    line.is_home = True
    line.save()
    assert "NYJ vs MIA" in signed_in.get("/startsit/").content.decode()


def test_matchups_never_change_the_recommendation(mine, rival, lineup_with_a_move, game_against_mia, projections, rated):
    add_player(rival, "WR", 3, "Their Guy", "BUF")
    projections["Their Guy"] = 10.0
    Matchup.objects.create(league=mine.league, week=8, team_a=mine, team_b=rival)
    np.random.seed(0)
    before = services.start_sit(mine)["rec"]

    rated[("MIA", "WR")] = TOUGH
    GameLine.objects.create(season=2026, week=8, team="NYJ", is_home=False, spread=10.0, total=30.0, implied_total=10.0)
    np.random.seed(0)
    after = services.start_sit(mine)["rec"]

    assert (after.best, after.moves, after.best_points, after.current_points) == (
        before.best, before.moves, before.best_points, before.current_points
    )
    assert after.best_win_prob == pytest.approx(before.best_win_prob, abs=0.03)


def test_explanation_gets_the_game_context(mine, lineup_with_a_move, game_against_mia, rated):
    rated[("MIA", "WR")] = TOUGH
    GameLine.objects.create(season=2026, week=8, team="NYJ", spread=3.0, total=44.0, implied_total=20.5)
    roster = narrate.startsit_payload(services.start_sit(mine))["roster"]
    wilson = next(r for r in roster if r["name"] == "Garrett Wilson")
    assert (wilson["matchup"], wilson["team_implied_total"], wilson["team_spread"]) == ("Tough", 20.5, 3.0)


def test_explanation_leaves_out_unremarkable_context(mine, lineup_with_a_move, game_against_mia, rated):
    rated[("MIA", "WR")] = AVERAGE
    GameLine.objects.create(season=2026, week=8, team="NYJ")  # lines not posted yet
    roster = narrate.startsit_payload(services.start_sit(mine))["roster"]
    wilson = next(r for r in roster if r["name"] == "Garrett Wilson")
    assert not {"matchup", "team_implied_total", "team_spread"} & wilson.keys()
