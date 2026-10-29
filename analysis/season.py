"""Rest-of-season lineup points for a roster, shared by waivers and trades."""

from functools import cache

from analysis.lineup import Candidate, optimal, starters


class SeasonEvaluator:
    """Sums a roster's optimal-lineup points over `weeks`.

    `baseline` players (see waivers.streamers) are available to every lineup without
    using a roster spot. Results are cached per roster, so reuse one evaluator per request.
    """

    def __init__(self, slots: dict[str, int], weeks: range, baseline=()):
        self.slots = slots
        self.weeks = weeks
        self.baseline = frozenset(baseline)
        self.week_lineup = cache(self._week_lineup)
        self.ros = cache(self._ros)

    def _week_lineup(self, players: frozenset, week: int) -> tuple[float, frozenset]:
        """(points, starting player ids) for one week."""
        pool = players | self.baseline
        lineup = optimal([Candidate(p.id, p.eligible_slots, p.mean(week)) for p in pool], self.slots)
        by_id = {p.id: p for p in pool}
        ids = starters(lineup)
        return sum(by_id[pid].mean(week) for pid in ids), frozenset(ids)

    def week_points(self, players: frozenset, week: int) -> float:
        return self.week_lineup(players, week)[0]

    def _ros(self, players: frozenset) -> float:
        return sum(self.week_points(players, w) for w in self.weeks)

    def cheapest_drop(self, players: frozenset, protected: frozenset = frozenset()):
        """(player, ROS points lost) for the player whose removal hurts least."""
        # removing a player who never starts can't change any optimal lineup, so it's free
        ever_started = frozenset().union(*(self.week_lineup(players, w)[1] for w in self.weeks))
        idle = [p for p in players if p not in protected and p.id not in ever_started]
        if idle:
            return min(idle, key=lambda p: (sum(p.mean(w) for w in self.weeks), p.id)), 0.0
        base = self.ros(players)
        return min(
            ((p, base - self.ros(players - {p})) for p in players if p not in protected),
            key=lambda pc: pc[1],
        )

    def trim_to(self, players: frozenset, capacity: int, protected: frozenset = frozenset()):
        """Drop cheapest players one at a time until the roster fits. Returns (roster, dropped)."""
        dropped = []
        while len(players) > capacity:
            p, _ = self.cheapest_drop(players, protected)
            players = players - {p}
            dropped.append(p)
        return players, dropped
