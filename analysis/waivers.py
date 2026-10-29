"""Rank free agents by how much they improve *your* optimal lineup.

Gain is measured after dropping your most expendable player (when the roster is full),
so a free agent only scores if he'd actually start for you at some point.

Every lineup also has access to "streamers": phantom players worth the Nth-best free agent
at each position each week. Covering a bye only counts if a player beats what you could
pick up that week anyway.
"""

from collections import defaultdict
from dataclasses import dataclass

from analysis.projections import Projection
from analysis.season import SeasonEvaluator


@dataclass(frozen=True)
class WeeklyPlayer:
    id: int
    position: str
    eligible_slots: tuple[str, ...]
    weeks: dict[int, Projection | None]  # week -> projection, None = bye

    def mean(self, week: int) -> float:
        p = self.weeks.get(week)
        return p.mean if p else 0.0

    def __hash__(self):
        return hash(self.id)

    def __eq__(self, other):
        return isinstance(other, WeeklyPlayer) and other.id == self.id


@dataclass
class WaiverTarget:
    id: int
    next_week_gain: float
    ros_gain: float
    drop_id: int | None  # best drop to make room for this player; None if there's an open spot


@dataclass
class WaiverReport:
    drop_id: int | None  # most expendable player overall; None when there's an open roster spot
    drop_cost: float  # ROS lineup points lost by dropping him
    targets: list[WaiverTarget]


STREAMER_RANK = 3  # assume the top couple of free agents get claimed by someone else


def streamers(free_agents: list[WeeklyPlayer], weeks: range, rank: int = STREAMER_RANK) -> list[WeeklyPlayer]:
    """One phantom per position, worth the `rank`-th best free agent's projection each week."""
    by_pos = defaultdict(list)
    for fa in free_agents:
        by_pos[fa.position].append(fa)
    phantoms = []
    for i, (pos, fas) in enumerate(sorted(by_pos.items()), start=1):
        weekly = {}
        for w in weeks:
            means = sorted((fa.mean(w) for fa in fas), reverse=True)
            level = means[rank - 1] if len(means) >= rank else 0.0
            weekly[w] = Projection(level, 0.0)
        phantoms.append(WeeklyPlayer(-i, pos, fas[0].eligible_slots, weekly))
    return phantoms


def roster_capacity(slots: dict[str, int]) -> int:
    return sum(n for slot, n in slots.items() if slot != "IR")


def evaluate(
    roster: list[WeeklyPlayer],
    free_agents: list[WeeklyPlayer],
    slots: dict[str, int],
    weeks: range,
    baseline: list[WeeklyPlayer] = (),
    protected: set[int] = frozenset(),
) -> WaiverReport:
    """`roster` should exclude IR-slotted players; they don't use a roster spot.

    `baseline` players (see `streamers`) are available to every lineup without a roster spot.
    `protected` player ids are never chosen as the drop (e.g. already in a pending move).
    """
    season = SeasonEvaluator(slots, weeks, baseline)
    base = frozenset(roster)
    base_ros = season.ros(base)
    base_next = season.week_points(base, weeks[0])

    full = len(roster) >= roster_capacity(slots)
    locked = frozenset(p for p in base if p.id in protected)

    drop, drop_cost = None, 0.0
    if full:
        drop, cost = season.cheapest_drop(base, locked)
        drop_cost = round(cost, 2)

    targets = []
    for fa in free_agents:
        with_fa = base | {fa}
        fa_drop = None
        if full:
            # the best drop depends on who's added: a new kicker should replace the old kicker
            fa_drop, _ = season.cheapest_drop(with_fa, locked | {fa})
            with_fa = with_fa - {fa_drop}
        targets.append(
            WaiverTarget(
                id=fa.id,
                next_week_gain=round(season.week_points(with_fa, weeks[0]) - base_next, 2),
                ros_gain=round(season.ros(with_fa) - base_ros, 2),
                drop_id=fa_drop.id if fa_drop else None,
            )
        )
    targets.sort(key=lambda t: (t.ros_gain, t.next_week_gain), reverse=True)
    return WaiverReport(drop.id if drop else None, drop_cost, targets)
