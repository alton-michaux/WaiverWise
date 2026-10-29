"""Optimal lineup for an ESPN roster.

Slots are filled most-restrictive first (QB, RB, ... before FLEX), each with the best
remaining eligible player. For single-position players feeding nested flex slots this
greedy fill is exact.
"""

from dataclasses import dataclass

NON_STARTING = {"BE", "IR"}


@dataclass(frozen=True)
class Candidate:
    id: int
    eligible_slots: tuple[str, ...]
    value: float  # whatever we're maximizing: mean, floor, or ceiling


def slot_breadth(slot: str) -> int:
    return len(slot.split("/"))


def optimal(
    candidates: list[Candidate], slots: dict[str, int], locked: dict[int, str] | None = None
) -> dict[str, list[int]]:
    """Return {slot: [player ids]} for starting slots; everyone else goes to "BE".

    `locked` maps player id -> slot for players whose game has started and can't move.
    """
    locked = locked or {}
    lineup = {slot: [] for slot, n in slots.items() if slot not in NON_STARTING}
    for pid, slot in locked.items():
        if slot in lineup:
            lineup[slot].append(pid)

    available = sorted(
        (c for c in candidates if c.id not in locked), key=lambda c: c.value, reverse=True
    )
    for slot in sorted(lineup, key=slot_breadth):
        while len(lineup[slot]) < slots[slot]:
            pick = next((c for c in available if slot in c.eligible_slots), None)
            if pick is None:
                break
            lineup[slot].append(pick.id)
            available.remove(pick)

    started = starters(lineup)
    lineup["BE"] = [c.id for c in candidates if c.id not in started]
    return lineup


def starters(lineup: dict[str, list[int]]) -> set[int]:
    return {pid for slot, ids in lineup.items() if slot not in NON_STARTING for pid in ids}
