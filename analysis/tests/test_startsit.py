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
