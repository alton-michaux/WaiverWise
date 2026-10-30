from analysis.projections import Projection
from analysis.sim import win_probability
from analysis.startsit import RosterSpot, recommend

SLOTS = {"QB": 1, "RB": 1, "BE": 2, "IR": 1}


def spot(pid, pos, slot, mean, sd=3.0, locked=False):
    return RosterSpot(pid, (pos, "BE"), slot, Projection(mean, sd) if mean is not None else None, locked)


def test_recommends_swap():
    roster = [spot(1, "QB", "QB", 20), spot(2, "RB", "RB", 8), spot(3, "RB", "BE", 14)]
    rec = recommend(roster, SLOTS)
    assert rec.moves == [(3, 2)]
    assert rec.best_points - rec.current_points == 6


def test_bye_player_gets_benched():
    roster = [spot(1, "QB", "QB", 20), spot(2, "RB", "RB", None), spot(3, "RB", "BE", 5)]
    assert recommend(roster, SLOTS).moves == [(3, 2)]


def test_ir_player_never_starts():
    roster = [spot(1, "QB", "QB", 20), spot(2, "RB", "RB", 5), spot(3, "RB", "IR", 30)]
    assert recommend(roster, SLOTS).moves == []


def test_floor_mode_prefers_safe_player():
    roster = [
        spot(1, "QB", "QB", 20),
        spot(2, "RB", "RB", 12, sd=8),  # boom/bust
        spot(3, "RB", "BE", 11, sd=1),  # steady
    ]
    assert recommend(roster, SLOTS, mode="mean").moves == []
    assert recommend(roster, SLOTS, mode="floor").moves == [(3, 2)]


def test_win_probability_symmetry_and_order():
    a, b = [Projection(100, 15)], [Projection(80, 15)]
    assert win_probability(a, b) > 0.8
    assert abs(win_probability(a, b) + win_probability(b, a) - 1) < 0.02
    assert abs(win_probability(a, a, seed=1) - 0.5) < 0.03


def test_opponent_win_prob_improves_with_best_lineup():
    roster = [spot(1, "QB", "QB", 20), spot(2, "RB", "RB", 2), spot(3, "RB", "BE", 15)]
    opp = [spot(10, "QB", "QB", 18), spot(11, "RB", "RB", 12)]
    rec = recommend(roster, SLOTS, opponent=opp)
    assert rec.best_win_prob > rec.current_win_prob


FULL = {"QB": 1, "RB": 2, "WR": 2, "TE": 1, "RB/WR/TE": 1, "BE": 5, "IR": 1}


def flex_spot(pid, pos, slot, mean, sd=3.0):
    return RosterSpot(pid, (pos, "RB/WR/TE", "BE"), slot, Projection(mean, sd))


def test_moves_pair_by_position():
    # regression: TE in was paired with WR out because pairs were zipped by value
    roster = [
        spot(1, "QB", "QB", 20),
        flex_spot(2, "RB", "RB", 15), flex_spot(3, "RB", "RB", 14),
        flex_spot(4, "WR", "WR", 6), flex_spot(5, "WR", "WR", 12),
        flex_spot(6, "TE", "TE", 5), flex_spot(7, "RB", "RB/WR/TE", 13),
        flex_spot(8, "TE", "BE", 9.7), flex_spot(9, "WR", "BE", 9.8),
    ]
    moves = recommend(roster, FULL).moves
    assert sorted(moves) == [(8, 6), (9, 4)]


def test_moves_pair_through_flex_chain():
    # WR comes in; current FLEX RB is the one who sits
    roster = [
        spot(1, "QB", "QB", 20),
        flex_spot(2, "RB", "RB", 15), flex_spot(3, "RB", "RB", 14),
        flex_spot(4, "WR", "WR", 12), flex_spot(5, "WR", "WR", 11),
        flex_spot(6, "TE", "TE", 8), flex_spot(7, "RB", "RB/WR/TE", 5),
        flex_spot(9, "WR", "BE", 10),
    ]
    assert recommend(roster, FULL).moves == [(9, 7)]


def test_filling_an_empty_slot_has_nobody_to_bench():
    # a real roster: two WR slots, one empty, and an extra player on the bench
    slots = {"WR": 2, "BE": 2}
    roster = [spot(1, "WR", "WR", 12), spot(2, "WR", "BE", 10), spot(3, "WR", "BE", 8)]
    assert recommend(roster, slots).moves == [(2, None)]
