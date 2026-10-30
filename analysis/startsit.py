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
    # (bench -> start, start -> bench) pairs, slot-compatible; None going out when filling an empty slot
    moves: list[tuple[int, int | None]]


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
    moves = pair_moves([by_id[i] for i in ins], [by_id[o] for o in outs])

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
        moves=moves,
    )


def pair_moves(ins: list[RosterSpot], outs: list[RosterSpot]) -> list[tuple[int, int | None]]:
    """Pair each incoming starter with an outgoing one whose slot he can fill.

    Bipartite matching (augmenting paths), so a chain like WR-in / RB-out-of-FLEX still pairs.
    Prefers same-position swaps by trying non-flex slots first.
    """
    def options(spot_in):
        fits = [o for o in outs if o.current_slot in spot_in.eligible_slots]
        return sorted(fits, key=lambda o: "/" in o.current_slot)

    match: dict[int, int] = {}  # out id -> in id

    def assign(spot_in, seen):
        for o in options(spot_in):
            if o.id in seen:
                continue
            seen.add(o.id)
            if o.id not in match or assign(by_in[match[o.id]], seen):
                match[o.id] = spot_in.id
                return True
        return False

    by_in = {s.id: s for s in ins}
    for spot_in in ins:
        assign(spot_in, set())
    pairs = {i: o for o, i in match.items()}
    # anything unmatched still gets listed; with nobody left to bench, the player fills an empty slot
    leftover_outs = [o.id for o in outs if o.id not in match]
    return [(s.id, pairs[s.id] if s.id in pairs else leftover_outs.pop(0) if leftover_outs else None) for s in ins]
