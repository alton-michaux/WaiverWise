from analysis.projections import Projection
from analysis.season import SeasonEvaluator
from analysis.trades import evaluate, find_trades
from analysis.waivers import WeeklyPlayer

SLOTS = {"QB": 1, "RB": 1, "WR": 1, "BE": 1}
WEEKS = range(8, 10)
CAP = 4


def wp(pid, pos, mean):
    return WeeklyPlayer(pid, pos, (pos, "BE"), {w: Projection(mean, 0) for w in WEEKS})


def season():
    return SeasonEvaluator(SLOTS, WEEKS)


def test_positional_need_makes_both_sides_better():
    # A is RB-rich/WR-poor, B the reverse
    a = [wp(1, "QB", 20), wp(2, "RB", 15), wp(3, "RB", 14), wp(4, "WR", 5)]
    b = [wp(11, "QB", 20), wp(12, "WR", 15), wp(13, "WR", 14), wp(14, "RB", 5)]
    t = evaluate(season(), a, b, [a[2]], [b[2]], CAP)
    assert t.a.gain == 2 * (14 - 5)
    assert t.b.gain == 2 * (14 - 5)


def test_two_for_one_forces_a_drop():
    a = [wp(1, "QB", 20), wp(2, "RB", 15), wp(3, "WR", 10), wp(4, "WR", 4)]
    b = [wp(11, "QB", 18), wp(12, "RB", 8), wp(13, "WR", 16), wp(14, "WR", 3)]
    t = evaluate(season(), a, b, [a[2]], [b[1], b[2]], CAP)
    # A gets WR 16 + RB 8, gives WR 10 -> 5 players, must cut one; the gets are protected
    assert t.a.dropped == [4]
    assert t.a.gain == 2 * (16 - 10)
    assert t.b.dropped == []


def test_symmetry():
    a = [wp(1, "QB", 20), wp(2, "RB", 15), wp(3, "WR", 6)]
    b = [wp(11, "QB", 18), wp(12, "RB", 9), wp(13, "WR", 12)]
    ab = evaluate(season(), a, b, [a[1]], [b[2]], CAP)
    ba = evaluate(season(), b, a, [b[2]], [a[1]], CAP)
    assert (ab.a.gain, ab.b.gain) == (ba.b.gain, ba.a.gain)


def test_finder_returns_mutual_wins_only():
    a = [wp(1, "QB", 20), wp(2, "RB", 15), wp(3, "RB", 14), wp(4, "WR", 5)]
    b = [wp(11, "QB", 20), wp(12, "WR", 15), wp(13, "WR", 14), wp(14, "RB", 5)]
    trades = find_trades(season(), a, b, CAP)
    assert trades
    assert all(t.a.gain >= 1 and t.b.gain >= 1 for t in trades)
    best = trades[0]
    assert len(best.a.gives) == len(best.a.gets) == 1  # simple RB-for-WR swap
    assert best.a.gives[0] in (2, 3) and best.a.gets[0] in (12, 13)


def test_finder_skips_excluded_players():
    a = [wp(1, "QB", 20), wp(2, "RB", 15), wp(3, "RB", 14), wp(4, "WR", 5)]
    b = [wp(11, "QB", 20), wp(12, "WR", 15), wp(13, "WR", 14), wp(14, "RB", 5)]
    trades = find_trades(season(), a, b, CAP, exclude={2, 3})
    assert all(not set(t.a.gives) & {2, 3} for t in trades)
