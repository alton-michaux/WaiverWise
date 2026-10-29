"""Consensus projections and weekly variance.

Spread is modeled as a coefficient of variation (sd / mean) so it scales with the projection.
A player's observed CV is shrunk toward a positional prior when he has few games.
"""

import math
from dataclasses import dataclass
from statistics import mean, pstdev

DEFAULT_WEIGHTS = {"espn": 0.5, "sleeper": 0.5}

# Typical week-to-week CV by position (my estimate; tune once a season of data is stored)
POSITION_CV = {"QB": 0.35, "RB": 0.55, "WR": 0.6, "TE": 0.65, "K": 0.45, "D/ST": 0.7}
PRIOR_GAMES = 4  # weight of the prior, in games
HISTORY_WEEKS = 8
Z_10 = 1.2816  # 10th/90th percentile of a normal


@dataclass(frozen=True)
class Projection:
    mean: float
    sd: float

    @property
    def floor(self) -> float:
        return max(0.0, round(self.mean - Z_10 * self.sd, 2))

    @property
    def ceiling(self) -> float:
        return round(self.mean + Z_10 * self.sd, 2)


def consensus(by_source: dict[str, float | None], weights: dict[str, float] = DEFAULT_WEIGHTS) -> float | None:
    """Weighted mean over whichever sources have a value."""
    available = {s: v for s, v in by_source.items() if v is not None and weights.get(s)}
    if not available:
        return None
    total_weight = sum(weights[s] for s in available)
    return round(sum(v * weights[s] for s, v in available.items()) / total_weight, 2)


def player_cv(history: list[float], position: str) -> float:
    """Shrunk CV from recent games played (most recent last)."""
    prior = POSITION_CV.get(position, 0.6)
    recent = history[-HISTORY_WEEKS:]
    if len(recent) < 2 or mean(recent) <= 0:
        return prior
    observed = pstdev(recent) / mean(recent)
    n = len(recent)
    # pool variances rather than CVs so a single outlier doesn't dominate
    return math.sqrt((n * observed**2 + PRIOR_GAMES * prior**2) / (n + PRIOR_GAMES))


def project(mean_points: float, history: list[float], position: str) -> Projection:
    return Projection(mean=mean_points, sd=round(mean_points * player_cv(history, position), 2))
