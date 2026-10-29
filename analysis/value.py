"""Rest-of-season value over a replacement-level player.

Replacement level comes from "drafting" every team's starting lineup from the player pool:
each team fills its dedicated slots, then flex slots take the best leftovers. The first
player at a position not needed to start anywhere sets that position's replacement level.
"""

from collections import defaultdict
from dataclasses import dataclass

from analysis.lineup import NON_STARTING


@dataclass(frozen=True)
class PlayerValue:
    id: int
    position: str
    ros_points: float


def starters_by_position(
    pool: list[PlayerValue], slots: dict[str, int], team_count: int
) -> dict[str, int]:
    """How many players at each position the whole league starts."""
    by_pos = defaultdict(list)
    for p in pool:
        by_pos[p.position].append(p)
    for players in by_pos.values():
        players.sort(key=lambda p: p.ros_points, reverse=True)

    counts = defaultdict(int)
    starting = {s: n for s, n in slots.items() if s not in NON_STARTING}
    for slot, n in starting.items():
        if "/" not in slot:
            counts[slot] += n * team_count
    for slot, n in starting.items():
        if "/" not in slot:
            continue
        eligible = slot.split("/")
        for _ in range(n * team_count):
            best = max(
                (by_pos[pos][counts[pos]] for pos in eligible if counts[pos] < len(by_pos[pos])),
                key=lambda p: p.ros_points,
                default=None,
            )
            if best is None:
                break
            counts[best.position] += 1
    return dict(counts)


def replacement_levels(
    pool: list[PlayerValue], slots: dict[str, int], team_count: int
) -> dict[str, float]:
    counts = starters_by_position(pool, slots, team_count)
    levels = {}
    for pos in {p.position for p in pool}:
        ranked = sorted((p.ros_points for p in pool if p.position == pos), reverse=True)
        idx = counts.get(pos, 0)
        levels[pos] = ranked[idx] if idx < len(ranked) else (ranked[-1] if ranked else 0.0)
    return levels


def vorp(pool: list[PlayerValue], slots: dict[str, int], team_count: int) -> dict[int, float]:
    levels = replacement_levels(pool, slots, team_count)
    return {p.id: round(p.ros_points - levels[p.position], 2) for p in pool}
