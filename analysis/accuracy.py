"""Did the projections hold up, and does matchup data explain what they missed?

A residual that still moves with a matchup signal is the only evidence that adjusting projections
would help. Without it, adjusting just double-counts what the projections already know.
"""

from dataclasses import dataclass
from statistics import mean

import numpy as np

MIN_FIT_ROWS = 3


@dataclass(frozen=True)
class Result:
    position: str
    actual: float
    espn: float | None
    sleeper: float | None
    consensus: float
    opp_rating: float | None
    implied_total: float | None


@dataclass(frozen=True)
class Fit:
    rows: int
    r: float
    slope: float  # points of projection miss per unit of the signal


def mean_absolute_error(pairs: list[tuple[float, float]]) -> float | None:
    return mean(abs(a - b) for a, b in pairs) if pairs else None


def source_errors(results: list[Result]) -> dict[str, float | None]:
    """Compared on rows with both sources, so one source can't look better just by skipping hard players."""
    both = [r for r in results if r.espn is not None and r.sleeper is not None]
    return {
        "espn": mean_absolute_error([(r.actual, r.espn) for r in both]),
        "sleeper": mean_absolute_error([(r.actual, r.sleeper) for r in both]),
        "consensus": mean_absolute_error([(r.actual, r.consensus) for r in both]),
    }


def residual_fit(results: list[Result], signal: str) -> Fit | None:
    """How actual minus consensus moves with `signal`. None when there's too little data to fit."""
    pairs = [(v, r.actual - r.consensus) for r in results if (v := getattr(r, signal)) is not None]
    if len(pairs) < MIN_FIT_ROWS or len({x for x, _ in pairs}) < 2 or len({y for _, y in pairs}) < 2:
        return None
    xs, ys = zip(*pairs)
    return Fit(len(pairs), float(np.corrcoef(xs, ys)[0, 1]), float(np.polyfit(xs, ys, 1)[0]))
