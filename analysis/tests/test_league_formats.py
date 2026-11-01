"""Rules other than the one league this was built on: scoring variants, flex shapes, superflex."""

import itertools
import random

import pytest

from analysis.lineup import Candidate, optimal, starters
from analysis.projections import Projection
from analysis.scoring import rule_points, score, unscored_stats
from analysis.startsit import RosterSpot, pair_moves, recommend
from analysis.value import PlayerValue, replacement_levels, starters_by_position, vorp

# eligible slots as ESPN sends them
QB = ("QB", "OP", "BE", "IR")
RB = ("RB", "RB/WR", "RB/WR/TE", "OP", "BE", "IR")
WR = ("RB/WR", "WR", "WR/TE", "RB/WR/TE", "OP", "BE", "IR")
TE = ("WR/TE", "TE", "RB/WR/TE", "OP", "BE", "IR")
K = ("K", "BE", "IR")
DST = ("D/ST", "BE", "IR")


def rules(**by_stat):
    return {stat.removeprefix("s"): {"points": points, "overrides": {}} for stat, points in by_stat.items()}


# --- scoring

RECEIVER = {"rec": 6, "rec_yd": 80, "rec_td": 1}


@pytest.mark.parametrize("ppr, expected", [(0, 14), (0.5, 17), (1, 20)])
def test_reception_formats(ppr, expected):
    assert score(RECEIVER, rules(s53=ppr, s42=0.1, s43=6), "WR") == pytest.approx(expected)


@pytest.mark.parametrize("td, expected", [(4, 14), (6, 18)])
def test_passing_td_formats(td, expected):
    assert score({"pass_td": 2, "pass_yd": 150}, rules(s4=td, s3=0.04), "QB") == pytest.approx(expected)


def test_te_premium_only_applies_to_tes():
    r = {"53": {"points": 1.0, "overrides": {"6": 1.5}}}
    assert score({"rec": 4}, r, "TE") == 6
    assert score({"rec": 4}, r, "WR") == 4
    assert score({"rec": 4}, r, "RB") == 4


def test_a_position_override_of_zero_wins():
    r = {"53": {"points": 1.0, "overrides": {"2": 0.0}}}
    assert score({"rec": 4}, r, "RB") == 0


def test_dst_overrides_still_apply():
    assert rule_points({"95": {"points": 0.0, "overrides": {"16": 2.0}}}, 95, "D/ST") == 2.0
    assert score({"int": 2}, {"95": {"points": 0.0, "overrides": {"16": 2.0}}}, "D/ST") == 4


@pytest.mark.parametrize(
    "stat, stats, expected",
    [
        ("0", {"pass_att": 30}, 30),
        ("1", {"pass_cmp": 20}, 20),
        ("2", {"pass_inc": 10}, 10),
        ("23", {"rush_att": 15}, 15),
        ("41", {"rec": 5}, 5),
        ("58", {"rec_tgt": 8}, 8),
        ("64", {"pass_sack": 2}, 2),
        ("68", {"fum": 1}, 1),
        ("83", {"fgm": 2}, 2),
        ("84", {"fga": 3}, 3),
        ("87", {"xpa": 4}, 4),
        ("88", {"xpmiss": 1}, 1),
        ("82", {"fgmiss_30_39": 1}, 1),
    ],
)
def test_stats_that_used_to_score_zero(stat, stats, expected):
    assert score(stats, {stat: {"points": 1.0, "overrides": {}}}, "QB") == expected


@pytest.mark.parametrize(
    "stat, stats, expected",
    [("8", {"pass_yd": 250}, 10), ("9", {"pass_yd": 250}, 5), ("28", {"rush_yd": 80}, 8), ("48", {"rec_yd": 80}, 8), ("54", {"rec": 10}, 2)],
)
def test_points_per_n_yards(stat, stats, expected):
    assert score(stats, {stat: {"points": 1.0, "overrides": {}}}, "WR") == expected


def test_dst_per_point_and_per_yard_allowed():
    r = {"120": {"points": 0.0, "overrides": {"16": -0.1}}, "127": {"points": 0.0, "overrides": {"16": -0.01}}}
    assert score({"pts_allow": 20, "yds_allow": 300}, r, "D/ST") == -5


def test_unscored_stats_names_rules_with_no_projection():
    r = {
        "53": {"points": 1.0, "overrides": {}},
        "17": {"points": 2.0, "overrides": {}},  # 300-yard game bonus
        "18": {"points": 0.0, "overrides": {}},  # not worth anything here
        "206": {"points": 0.0, "overrides": {"16": 2.0}},  # only for D/STs
    }
    assert unscored_stats(r) == [17, 206]


# --- lineups: brute force is the definition of right


def brute_force_best(candidates, slots):
    seats = [slot for slot, n in slots.items() if slot not in ("BE", "IR") for _ in range(n)]
    best = None
    for size in range(len(seats), -1, -1):
        for group in itertools.combinations(candidates, size):
            if _can_seat(group, seats):
                total = sum(c.value for c in group)
                best = total if best is None else max(best, total)
        if best is not None:
            return best  # empty slots are never better, so the largest group wins
    return 0


def _can_seat(group, seats):
    def place(i, free):
        if i == len(group):
            return True
        return any(place(i + 1, free - {s}) for s in free if seats[s] in group[i].eligible_slots)

    return place(0, frozenset(range(len(seats))))


def total(lineup, candidates):
    by_id = {c.id: c for c in candidates}
    return sum(by_id[pid].value for pid in starters(lineup))


FORMATS = {
    "standard": {"QB": 1, "RB": 2, "WR": 2, "TE": 1, "RB/WR/TE": 1, "D/ST": 1, "K": 1, "BE": 7},
    "two_flex": {"QB": 1, "RB": 2, "WR": 2, "TE": 1, "RB/WR/TE": 2, "BE": 5},
    "superflex": {"QB": 1, "RB": 2, "WR": 2, "TE": 1, "RB/WR/TE": 1, "OP": 1, "BE": 5},
    "overlapping_flex": {"QB": 1, "RB": 1, "WR": 1, "RB/WR": 1, "WR/TE": 1, "BE": 5},
    "every_flex": {"RB/WR": 1, "WR/TE": 1, "RB/WR/TE": 1, "OP": 2, "BE": 5},
    "three_wr_no_flex": {"QB": 1, "RB": 2, "WR": 3, "TE": 1, "BE": 5},
}


@pytest.mark.parametrize("name", FORMATS)
@pytest.mark.parametrize("seed", range(40))
def test_lineup_matches_brute_force(name, seed):
    rng = random.Random(f"{name}{seed}")
    shapes = [QB, QB, RB, RB, RB, WR, WR, WR, TE, TE, K, DST]
    candidates = [Candidate(i, rng.choice(shapes), round(rng.uniform(0, 30), 2)) for i in range(rng.randint(5, 11))]
    slots = FORMATS[name]
    lineup = optimal(candidates, slots)
    assert total(lineup, candidates) == pytest.approx(brute_force_best(candidates, slots))


@pytest.mark.parametrize("name", FORMATS)
def test_lineups_are_legal(name):
    rng = random.Random(name)
    shapes = [QB, RB, WR, TE, K, DST]
    candidates = [Candidate(i, rng.choice(shapes), rng.uniform(0, 30)) for i in range(25)]
    lineup = optimal(candidates, FORMATS[name])
    by_id = {c.id: c for c in candidates}
    for slot, ids in lineup.items():
        if slot == "BE":
            continue
        assert len(ids) <= FORMATS[name][slot]
        assert all(slot in by_id[pid].eligible_slots for pid in ids)
    placed = [pid for ids in lineup.values() for pid in ids]
    assert sorted(placed) == sorted(by_id)  # everyone once, starting or on the bench


def test_superflex_starts_the_second_qb_over_a_worse_flex():
    # slot by slot, OP took the RB and the only flex option left was the worse RB: 18 instead of 19
    candidates = [Candidate(1, QB, 9), Candidate(2, RB, 10), Candidate(3, RB, 8)]
    lineup = optimal(candidates, {"OP": 1, "RB/WR/TE": 1, "BE": 1})
    assert lineup["OP"] == [1]
    assert lineup["RB/WR/TE"] == [2]
    assert lineup["BE"] == [3]


def test_overlapping_flex_slots_seat_everyone():
    # slot by slot, the WR took RB/WR and the RB sat for the TE: 18 instead of 19
    candidates = [Candidate(1, WR, 10), Candidate(2, RB, 9), Candidate(3, TE, 8)]
    lineup = optimal(candidates, {"RB/WR": 1, "WR/TE": 1, "BE": 1})
    assert lineup["RB/WR"] == [2]
    assert lineup["WR/TE"] == [1]


def test_best_players_take_the_narrow_slots():
    candidates = [Candidate(1, RB, 20), Candidate(2, RB, 15), Candidate(3, RB, 10)]
    lineup = optimal(candidates, {"RB": 2, "RB/WR/TE": 1})
    assert lineup["RB"] == [1, 2]
    assert lineup["RB/WR/TE"] == [3]


def test_locked_players_keep_their_slot_and_use_it_up():
    candidates = [Candidate(1, RB, 1), Candidate(2, RB, 20), Candidate(3, QB, 15)]
    lineup = optimal(candidates, {"RB": 1, "OP": 1, "BE": 1}, locked={1: "RB"})
    assert lineup["RB"] == [1]
    assert lineup["OP"] == [2]
    assert lineup["BE"] == [3]


def test_locked_on_the_bench_stays_there():
    candidates = [Candidate(1, RB, 30), Candidate(2, RB, 5)]
    lineup = optimal(candidates, {"RB": 1, "BE": 1}, locked={1: "BE"})
    assert lineup["RB"] == [2]
    assert 1 in lineup["BE"]


def test_recommendation_moves_pair_up_in_superflex():
    roster = [
        RosterSpot(1, QB, "QB", Projection(20, 5)),
        RosterSpot(2, RB, "OP", Projection(5, 2)),
        RosterSpot(3, QB, "BE", Projection(18, 5)),
    ]
    rec = recommend(roster, {"QB": 1, "OP": 1, "BE": 1})
    assert rec.moves == [(3, 2)]
    assert rec.best_points == 38


# --- replacement level


def pool(position, *points, start=0):
    return [PlayerValue(start + i, position, p) for i, p in enumerate(points)]


def test_superflex_starts_more_qbs():
    players = pool("QB", 300, 250, 200, 150, 100, 50) + pool("RB", 90, 80, 70, 60, 40, 30, start=10)
    assert starters_by_position(players, {"QB": 1, "RB": 1, "BE": 1}, 2) == {"QB": 2, "RB": 2}
    counts = starters_by_position(players, {"QB": 1, "RB": 1, "OP": 1, "BE": 1}, 2)
    assert (counts["QB"], counts["RB"]) == (4, 2)
    levels = replacement_levels(players, {"QB": 1, "RB": 1, "OP": 1, "BE": 1}, 2)
    assert levels["QB"] == 100


def test_dst_counts_as_a_starting_position():
    # the slash in D/ST used to read as a flex slot, so no D/ST ever counted as started
    players = pool("D/ST", 120, 110, 100, 90, 80)
    assert starters_by_position(players, {"D/ST": 1, "BE": 1}, 2) == {"D/ST": 2}
    assert replacement_levels(players, {"D/ST": 1, "BE": 1}, 2)["D/ST"] == 100
    assert vorp(players, {"D/ST": 1, "BE": 1}, 2)[0] == 20


@pytest.mark.parametrize("teams", [8, 10, 12, 14])
def test_league_size_moves_replacement_level(teams):
    players = pool("WR", *range(300, 100, -5))
    assert starters_by_position(players, {"WR": 2, "BE": 1}, teams) == {"WR": 2 * teams}
    assert replacement_levels(players, {"WR": 2, "BE": 1}, teams)["WR"] == 300 - 5 * 2 * teams


def test_overlapping_flex_counts_each_seat_once():
    players = pool("RB", 100, 90, 80, 70) + pool("WR", 95, 85, 75, 65, start=10) + pool("TE", 60, 50, start=20)
    counts = starters_by_position(players, {"RB/WR": 1, "WR/TE": 1, "BE": 1}, 2)
    assert sum(counts.values()) == 4


# --- cases the random lineups can miss


def test_points_per_n_yards_keeps_the_fraction():
    assert score({"pass_yd": 260}, {"8": {"points": 1.0, "overrides": {}}}, "QB") == pytest.approx(10.4)


def test_narrow_slots_fill_first_whatever_order_the_league_lists_them():
    candidates = [Candidate(1, RB, 20), Candidate(2, RB, 15), Candidate(3, RB, 10)]
    lineup = optimal(candidates, {"RB/WR/TE": 1, "RB": 2})
    assert lineup["RB"] == [1, 2]
    assert lineup["RB/WR/TE"] == [3]


def test_someone_left_out_does_not_stop_later_players_moving_over():
    # the second QB can't be seated, and after him the RB still needs the WR to shift
    candidates = [Candidate(1, QB, 20), Candidate(2, QB, 19), Candidate(3, WR, 10), Candidate(4, RB, 9), Candidate(5, TE, 8)]
    lineup = optimal(candidates, {"QB": 1, "RB/WR": 1, "WR/TE": 1, "BE": 2})
    assert lineup["RB/WR"] == [4]
    assert lineup["WR/TE"] == [3]
    assert sorted(lineup["BE"]) == [2, 5]


def test_moves_swap_into_the_narrowest_slot_first():
    incoming = RosterSpot(1, RB, "BE", Projection(20, 5))
    flex = RosterSpot(2, RB, "OP", Projection(1, 1))
    narrow = RosterSpot(3, RB, "RB", Projection(1, 1))
    assert pair_moves([incoming], [flex, narrow]) == [(1, 3)]
