from analysis.projections import Projection
from analysis.trends import UsageWeek, trend
from analysis.waivers import WeeklyPlayer, evaluate, streamers

SLOTS = {"QB": 1, "RB": 1, "BE": 1}
WEEKS = range(8, 10)


def wp(pid, pos, *means):
    return WeeklyPlayer(
        pid, pos, (pos, "BE"), {w: (Projection(m, 0) if m is not None else None) for w, m in zip(WEEKS, means)}
    )


def test_drops_least_valuable_and_ranks_by_ros_gain():
    roster = [wp(1, "QB", 20, 20), wp(2, "RB", 10, 10), wp(3, "RB", 5, 5)]
    fas = [wp(10, "RB", 12, 12), wp(11, "RB", 8, 8), wp(12, "QB", 15, 15)]
    report = evaluate(roster, fas, SLOTS, WEEKS)
    assert report.drop_id == 3
    assert [t.id for t in report.targets] == [10, 11, 12]
    assert report.targets[0].ros_gain == 4  # 12 replaces 10 as starter both weeks
    assert report.targets[1].ros_gain == 0  # only a bench upgrade


def test_bye_week_fill_in_counts():
    roster = [wp(1, "QB", 20, None), wp(2, "RB", 10, 10), wp(3, "RB", 5, 5)]
    fas = [wp(12, "QB", 15, 15)]
    report = evaluate(roster, fas, SLOTS, WEEKS)
    assert report.targets[0].ros_gain == 15  # covers week 9 bye, drops RB 3


def test_open_roster_spot_means_no_drop():
    roster = [wp(1, "QB", 20, 20), wp(2, "RB", 10, 10)]
    report = evaluate(roster, [wp(10, "RB", 12, 12)], SLOTS, WEEKS)
    assert report.drop_id is None
    assert report.targets[0].ros_gain == 4


def test_trend_compares_recent_to_earlier():
    weeks = [UsageWeek(w, 0.4, 2, 1) for w in range(1, 5)] + [UsageWeek(w, 0.8, 6, 2) for w in range(5, 8)]
    t = trend(weeks)
    assert t.recent_opportunities == 8
    assert t.opportunity_change == 5
    assert round(t.snap_pct_change, 2) == 0.4


def test_trend_without_history():
    assert trend([]) is None
    assert trend([UsageWeek(1, None, 3, 0)]).opportunity_change == 0


def test_streamer_baseline_discounts_bye_coverage():
    roster = [wp(1, "QB", 20, None), wp(2, "RB", 10, 10), wp(3, "RB", 5, 5)]
    fas = [wp(12, "QB", 15, 15), wp(13, "QB", 14, 14), wp(14, "QB", 12, 12)]
    report = evaluate(roster, fas, SLOTS, WEEKS, baseline=streamers(fas, WEEKS))
    gains = {t.id: t.ros_gain for t in report.targets}
    assert gains == {12: 3, 13: 2, 14: 0}  # only the edge over the 3rd-best streamer counts


def test_streamers_level_is_nth_best():
    fas = [wp(1, "K", 9, 9), wp(2, "K", 8, 7), wp(3, "K", 6, 8), wp(4, "K", 1, 1)]
    (k,) = streamers(fas, WEEKS)
    assert (k.position, k.mean(8), k.mean(9)) == ("K", 6, 7)
