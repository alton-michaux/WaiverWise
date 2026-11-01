"""Every page, from real database rows, for leagues set up differently from the one this was built on."""

import pytest

from analysis.projections import Projection
from league.models import League, LeaguePlayer, Matchup, Player, RosterEntry, Team
from web import services

pytestmark = pytest.mark.django_db

ELIGIBLE = {
    "QB": ["QB", "OP", "BE", "IR"],
    "RB": ["RB", "RB/WR", "RB/WR/TE", "OP", "BE", "IR"],
    "WR": ["RB/WR", "WR", "WR/TE", "RB/WR/TE", "OP", "BE", "IR"],
    "TE": ["WR/TE", "TE", "RB/WR/TE", "OP", "BE", "IR"],
    "K": ["K", "BE", "IR"],
    "D/ST": ["D/ST", "BE", "IR"],
}
STANDARD = {"QB": 1, "RB": 2, "WR": 2, "TE": 1, "RB/WR/TE": 1, "D/ST": 1, "K": 1, "BE": 3}
FORMATS = {
    "standard_8": (8, STANDARD),
    "standard_14": (14, STANDARD),
    "two_flex_12": (12, {**STANDARD, "RB/WR/TE": 2}),
    "superflex_12": (12, {**STANDARD, "OP": 1}),
    "overlapping_10": (10, {"QB": 1, "RB": 1, "WR": 1, "RB/WR": 1, "WR/TE": 1, "D/ST": 1, "K": 1, "BE": 3}),
}
ROSTER = ["QB", "QB", "RB", "RB", "RB", "RB", "WR", "WR", "WR", "TE", "TE", "K", "D/ST"]


@pytest.fixture(autouse=True)
def projections(monkeypatch):
    def fake(league, players, weeks):
        return {(p.pk, w): Projection(5.0 + p.pk % 17, 2.0) for p in players for w in weeks}

    monkeypatch.setattr(services, "weekly_projections", fake)


def build(team_count, slots):
    league = League.objects.create(
        espn_id=1, season=2026, name="L", team_count=team_count, current_week=15, reg_season_weeks=14, final_week=17,
        lineup_slots=slots, scoring={"53": {"points": 1.0, "overrides": {}}},
    )
    next_id = iter(range(1, 10_000))
    teams = [Team.objects.create(league=league, espn_id=i, name=f"Team {i}", abbrev=f"T{i}") for i in range(1, team_count + 1)]
    starting = [s for s, n in slots.items() if s not in ("BE", "IR") for _ in range(n)]
    for team in teams:
        open_slots = list(starting)
        for position in ROSTER:
            slot = next((s for s in open_slots if s in ELIGIBLE[position]), "BE")
            if slot != "BE":
                open_slots.remove(slot)
            add(league, next(next_id), position, team, slot)
    for position in ELIGIBLE:
        for _ in range(4):
            add(league, next(next_id), position)
    Matchup.objects.create(league=league, week=15, team_a=teams[0], team_b=teams[1])
    return league, teams[0]


def add(league, espn_id, position, team=None, slot="BE"):
    player = Player.objects.create(
        espn_id=espn_id, name=f"{position} {espn_id}", position=position, pro_team="BUF", eligible_slots=ELIGIBLE[position]
    )
    LeaguePlayer.objects.create(league=league, player=player, fa_status="" if team else "FREEAGENT")
    if team:
        RosterEntry.objects.create(team=team, player=player, lineup_slot=slot)


@pytest.mark.parametrize("name", FORMATS)
@pytest.mark.parametrize("path", ["/startsit/", "/waivers/", "/trades/", "/trades/?view=build", "/trades/best/"])
def test_pages_work_in_every_format(sign_in, name, path):
    _, team = build(*FORMATS[name])
    assert sign_in(team).get(path).status_code == 200


@pytest.mark.parametrize("name", FORMATS)
def test_start_sit_lists_flex_slots_before_the_bench(name):
    league, team = build(*FORMATS[name])
    slots = [row.slot for row, _ in services.start_sit(team)["rows"]]
    starting = [s for s in slots if s not in ("BE", "IR")]
    assert slots[: len(starting)] == starting
    assert set(starting) == {s for s in league.lineup_slots if s != "BE"}


def test_superflex_recommends_starting_two_qbs(monkeypatch):
    league, team = build(*FORMATS["superflex_12"])
    qbs = list(RosterEntry.objects.filter(team=team, player__position="QB").values_list("player_id", flat=True))

    def fake(league, players, weeks):
        return {(p.pk, w): Projection(25.0 if p.pk in qbs else 8.0, 2.0) for p in players for w in weeks}

    monkeypatch.setattr(services, "weekly_projections", fake)
    best = services.start_sit(team)["rec"].best
    assert sorted(best["QB"] + best["OP"]) == sorted(qbs)
