"""How often a player's week was good enough to start in this league."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Consistency:
    games: int
    startable: int  # games finishing within the league's starters at the position
    high: float
    low: float


def consistency(history: dict[int, float], weekly: dict[int, list[float]], starters: int) -> Consistency | None:
    """`history` is week -> this player's points; `weekly` is week -> everyone's points at that position."""
    if not history:
        return None

    def startable(week, points):
        ranked = sorted(weekly.get(week, []), reverse=True)
        return starters > 0 and (len(ranked) < starters or points >= ranked[starters - 1])

    return Consistency(
        games=len(history),
        startable=sum(startable(w, p) for w, p in history.items()),
        high=max(history.values()),
        low=min(history.values()),
    )
