"""Optimal lineup for an ESPN roster.

Players are seated best first, each taking the narrowest open slot he fits, and a player
already seated moves over if that's what makes room. Skip anyone who can't be seated and the
result is the best possible lineup, whatever the flex slots look like: sets of players who
can all start together form a transversal matroid, where this greedy order is exact.
A simple slot-by-slot fill isn't: with RB/WR and WR/TE slots, or OP and FLEX, it can give
a WR the slot only he and the RB could use and leave the RB on the bench.
"""

from dataclasses import dataclass

NON_STARTING = {"BE", "IR"}

# slots that take more than one position; D/ST has a slash but is a single position
FLEX_POSITIONS = {
    "RB/WR": ("RB", "WR"),
    "WR/TE": ("WR", "TE"),
    "RB/WR/TE": ("RB", "WR", "TE"),
    "OP": ("QB", "RB", "WR", "TE"),
}


@dataclass(frozen=True)
class Candidate:
    id: int
    eligible_slots: tuple[str, ...]
    value: float  # whatever we're maximizing: mean, floor, or ceiling


def slot_positions(slot: str) -> tuple[str, ...]:
    return FLEX_POSITIONS.get(slot, (slot,))


def slot_breadth(slot: str) -> int:
    return len(slot_positions(slot))


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

    # one seat per open starting spot, narrowest first
    seats = [slot for slot in sorted(lineup, key=slot_breadth) for _ in range(slots[slot] - len(lineup[slot]))]
    holder: list[Candidate | None] = [None] * len(seats)
    open_seats = len(seats)

    # this runs thousands of times per trade search, so don't redo work
    by_eligibility: dict[tuple, list[int]] = {}

    def fits(candidate):
        key = candidate.eligible_slots
        if key not in by_eligibility:
            by_eligibility[key] = [i for i, slot in enumerate(seats) if slot in key]
        return by_eligibility[key]

    # seats a failed search reached can't lead to an open one, and later successes can't change that
    dead: set[int] = set()

    def seat(candidate, tried) -> bool:
        options = fits(candidate)
        for i in options:
            if holder[i] is None:
                holder[i] = candidate
                return True
        for i in options:
            if i not in tried and i not in dead:
                tried.add(i)
                if seat(holder[i], tried):
                    holder[i] = candidate
                    return True
        return False

    available = sorted((c for c in candidates if c.id not in locked), key=lambda c: c.value, reverse=True)
    for candidate in available:
        if not open_seats:
            break
        tried: set[int] = set()
        if seat(candidate, tried):
            open_seats -= 1
        else:
            dead |= tried

    for slot, candidate in sorted(
        ((slot, c) for slot, c in zip(seats, holder) if c), key=lambda pair: pair[1].value, reverse=True
    ):
        lineup[slot].append(candidate.id)

    started = starters(lineup)
    lineup["BE"] = [c.id for c in candidates if c.id not in started]
    return lineup


def starters(lineup: dict[str, list[int]]) -> set[int]:
    return {pid for slot, ids in lineup.items() if slot not in NON_STARTING for pid in ids}
