"""Start/sit recommendation for one week."""

from dataclasses import dataclass

from analysis.lineup import Candidate, optimal, starters
from analysis.projections import Projection
from analysis.sim import win_probability

MODES = ("mean", "floor", "ceiling")


@dataclass(frozen=True)
class RosterSpot:
    id: int
    eligible_slots: tuple[str, ...]
    current_slot: str
    projection: Projection | None  # None = bye / no projection
    locked: bool = False  # game already started


@dataclass
class Recommendation:
    current: dict[str, list[int]]
    best: dict[str, list[int]]
    current_points: float
    best_points: float
    current_win_prob: float | None
    best_win_prob: float | None
    moves: list[tuple[int, int]]  # (bench -> start, start -> bench) pairs


def value(proj: Projection | None, mode: str) -> float:
    if proj is None:
        return 0.0
    return getattr(proj, mode)


def best_lineup(roster: list[RosterSpot], slots: dict[str, int], mode: str = "mean") -> dict[str, list[int]]:
    playable = [r for r in roster if r.current_slot != "IR"]
    candidates = [Candidate(r.id, r.eligible_slots, value(r.projection, mode)) for r in playable]
    locked = {r.id: r.current_slot for r in playable if r.locked}
    return optimal(candidates, slots, locked)


def recommend(
    roster: list[RosterSpot],
    slots: dict[str, int],
    opponent: list[RosterSpot] | None = None,
    mode: str = "mean",
) -> Recommendation:
    by_id = {r.id: r for r in roster}
    current = {}
    for r in roster:
        current.setdefault(r.current_slot, []).append(r.id)
    best = best_lineup(roster, slots, mode)

    def projections(lineup):
        return [by_id[pid].projection or Projection(0, 0) for pid in starters(lineup) if pid in by_id]

    def total(lineup):
        return round(sum(p.mean for p in projections(lineup)), 2)

    current_starters = starters(current)
    best_starters = starters(best)
    ins = sorted(best_starters - current_starters, key=lambda pid: -value(by_id[pid].projection, mode))
    outs = sorted(current_starters - best_starters, key=lambda pid: value(by_id[pid].projection, mode))

    cur_wp = best_wp = None
    if opponent:
        opp_by_id = {r.id: r for r in opponent}
        opp_best = best_lineup(opponent, slots, "mean")
        theirs = [opp_by_id[pid].projection or Projection(0, 0) for pid in starters(opp_best)]
        cur_wp = win_probability(projections(current), theirs)
        best_wp = win_probability(projections(best), theirs)

    return Recommendation(
        current=current,
        best=best,
        current_points=total(current),
        best_points=total(best),
        current_win_prob=cur_wp,
        best_win_prob=best_wp,
        moves=list(zip(ins, outs)),
    )
