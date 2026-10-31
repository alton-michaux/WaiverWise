import pytest

from analysis.accuracy import Result, residual_fit, source_errors


def result(actual=10.0, espn=10.0, sleeper=10.0, consensus=10.0, opp_rating=None, implied_total=None):
    return Result("WR", actual, espn, sleeper, consensus, opp_rating, implied_total)


def test_errors_per_source():
    errors = source_errors([result(actual=10, espn=12, sleeper=9, consensus=10.5)])
    assert errors == {"espn": 2, "sleeper": 1, "consensus": 0.5}


def test_errors_compare_only_rows_with_both_sources():
    errors = source_errors([result(actual=10, espn=10, sleeper=10), result(actual=10, espn=None, sleeper=99)])
    assert errors["sleeper"] == 0


def test_no_comparable_rows_has_no_error():
    assert source_errors([result(espn=None)])["espn"] is None


def test_fit_finds_a_miss_that_moves_with_the_signal():
    results = [result(actual=10 + 2 * x, consensus=10, opp_rating=x) for x in (-2.0, -1.0, 0.0, 1.0, 2.0)]
    fit = residual_fit(results, "opp_rating")
    assert (fit.rows, fit.r, fit.slope) == (5, pytest.approx(1), pytest.approx(2))


def test_fit_skips_rows_without_the_signal():
    results = [result(actual=x, opp_rating=x) for x in (1.0, 2.0, 3.0)] + [result(opp_rating=None)]
    assert residual_fit(results, "opp_rating").rows == 3


@pytest.mark.parametrize(
    "results",
    [
        [result(opp_rating=1.0), result(actual=11.0, opp_rating=2.0)],  # too few
        [result(actual=x, opp_rating=1.0) for x in (1.0, 2.0, 3.0)],  # signal never varies
        [result(opp_rating=x) for x in (1.0, 2.0, 3.0)],  # no misses at all
    ],
)
def test_no_fit_without_enough_variety(results):
    assert residual_fit(results, "opp_rating") is None
