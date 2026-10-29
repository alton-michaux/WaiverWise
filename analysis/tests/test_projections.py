import pytest

from analysis.projections import POSITION_CV, consensus, player_cv, project


def test_consensus_weighted_mean():
    assert consensus({"espn": 10, "sleeper": 20}) == 15
    assert consensus({"espn": 10, "sleeper": 20}, {"espn": 3, "sleeper": 1}) == 12.5


def test_consensus_skips_missing_sources():
    assert consensus({"espn": None, "sleeper": 12}) == 12
    assert consensus({"espn": None, "sleeper": None}) is None


def test_cv_falls_back_to_prior_without_history():
    assert player_cv([], "WR") == POSITION_CV["WR"]
    assert player_cv([15], "WR") == POSITION_CV["WR"]


def test_cv_shrinks_toward_prior():
    steady = [15, 15, 15, 15, 15, 15, 15, 15]  # observed CV 0
    cv = player_cv(steady, "QB")
    assert 0 < cv < POSITION_CV["QB"]


def test_floor_never_negative():
    p = project(5, [0, 20, 0, 20], "WR")
    assert p.floor == 0
    assert p.ceiling > p.mean
    assert p.mean - p.floor <= p.ceiling - p.mean


def test_zero_projection_has_zero_spread():
    p = project(0, [10, 12], "RB")
    assert (p.floor, p.ceiling) == (0, 0)
