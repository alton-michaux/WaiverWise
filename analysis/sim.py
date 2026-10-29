"""Monte Carlo matchup simulation. Player scores are independent normals clipped at 0."""

import numpy as np

from analysis.projections import Projection

DRAWS = 10_000


def _totals(lineup: list[Projection], rng: np.random.Generator, draws: int) -> np.ndarray:
    if not lineup:
        return np.zeros(draws)
    means = np.array([p.mean for p in lineup])
    sds = np.array([p.sd for p in lineup])
    return np.clip(rng.normal(means, sds, size=(draws, len(lineup))), 0, None).sum(axis=1)


def win_probability(
    mine: list[Projection], theirs: list[Projection], draws: int = DRAWS, seed: int | None = 0
) -> float:
    """P(my total > their total); ties count as half."""
    rng = np.random.default_rng(seed)
    a, b = _totals(mine, rng, draws), _totals(theirs, rng, draws)
    return float((a > b).mean() + 0.5 * (a == b).mean())
