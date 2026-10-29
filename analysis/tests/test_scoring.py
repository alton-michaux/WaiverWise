import pytest

from analysis.scoring import score

# Subset of the real league rules: 5pt pass TD, 0.75 PPR, D/ST via slot-16 overrides
RULES = {
    "3": {"points": 0.04, "overrides": {}},
    "4": {"points": 5.0, "overrides": {}},
    "20": {"points": -2.0, "overrides": {}},
    "24": {"points": 0.1, "overrides": {}},
    "42": {"points": 0.1, "overrides": {}},
    "43": {"points": 6.0, "overrides": {}},
    "53": {"points": 0.75, "overrides": {}},
    "80": {"points": 3.0, "overrides": {}},
    "77": {"points": 4.0, "overrides": {}},
    "85": {"points": -1.0, "overrides": {}},
    "86": {"points": 1.0, "overrides": {}},
    "95": {"points": 0.0, "overrides": {"16": 2.0}},
    "99": {"points": 0.0, "overrides": {"16": 1.0}},
    "91": {"points": 0.0, "overrides": {"16": 3.0}},
    "123": {"points": 0.0, "overrides": {"16": -1.0}},
    "130": {"points": 0.0, "overrides": {"16": 2.0}},
}


def test_qb_line():
    stats = {"pass_yd": 250, "pass_td": 2, "pass_int": 1, "rush_yd": 30}
    assert score(stats, RULES) == pytest.approx(10 + 10 - 2 + 3)


def test_three_quarter_ppr():
    assert score({"rec": 8, "rec_yd": 100, "rec_td": 1}, RULES) == pytest.approx(6 + 10 + 6)


def test_kicker_buckets_sum():
    stats = {"fgm_20_29": 1, "fgm_30_39": 1, "fgm_40_49": 1, "fgmiss_50p": 1, "xpm": 3}
    assert score(stats, RULES) == pytest.approx(3 + 3 + 4 - 1 + 3)


def test_dst_uses_overrides_and_buckets():
    stats = {"int": 1, "sack": 3, "pts_allow": 9.6, "yds_allow": 250}
    assert score(stats, RULES, is_dst=True) == pytest.approx(2 + 3 + 3 + 2)


def test_dst_bucket_rounds_fractional_projection():
    # 27.6 rounds to 28 -> 28-34 bucket
    assert score({"pts_allow": 27.6}, RULES, is_dst=True) == pytest.approx(-1)


def test_dst_stats_ignored_for_players():
    assert score({"int": 2, "sack": 1}, RULES) == 0
