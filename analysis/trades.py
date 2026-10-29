"""Trade evaluation by rest-of-season lineup impact on both teams.

Each side's value is the change in its optimal-lineup points over the remaining weeks,
after cutting down to roster capacity if it receives more players than it sends.
"""

from dataclasses import dataclass, field
from itertools import combinations

from analysis.season import SeasonEvaluator
from analysis.waivers import WeeklyPlayer

# Trade finder search limits
FINDER_POOL = 8  # most valuable players per roster considered
FINDER_MAX_SIZE = 2  # up to 2-for-1 / 1-for-2
FINDER_SKIP_POSITIONS = {"K", "D/ST"}


@dataclass
class TradeSide:
    gives: list[int]
    gets: list[int]
    before: float
    after: float
    dropped: list[int] = field(default_factory=list)

    @property
    def gain(self) -> float:
        return round(self.after - self.before, 2)


@dataclass
class Trade:
    a: TradeSide
    b: TradeSide


def _side(season: SeasonEvaluator, roster: frozenset, gives, gets, capacity: int) -> TradeSide:
    before = season.ros(roster)
    after_roster = (roster - set(gives)) | set(gets)
    after_roster, dropped = season.trim_to(after_roster, capacity, protected=frozenset(gets))
    return TradeSide(
        gives=[p.id for p in gives],
        gets=[p.id for p in gets],
        before=round(before, 2),
        after=round(season.ros(after_roster), 2),
        dropped=[p.id for p in dropped],
    )


def evaluate(
    season: SeasonEvaluator,
    roster_a: list[WeeklyPlayer],
    roster_b: list[WeeklyPlayer],
    a_gives: list[WeeklyPlayer],
    b_gives: list[WeeklyPlayer],
    capacity: int,
) -> Trade:
    """Rosters should exclude IR-slotted players. `capacity` is roster spots excluding IR."""
    cap_a = max(capacity, len(roster_a))  # don't force cuts on a roster already over (IR shuffles)
    cap_b = max(capacity, len(roster_b))
    return Trade(
        a=_side(season, frozenset(roster_a), a_gives, b_gives, cap_a),
        b=_side(season, frozenset(roster_b), b_gives, a_gives, cap_b),
    )


def _pool(season: SeasonEvaluator, roster: list[WeeklyPlayer], exclude: set[int]) -> list[WeeklyPlayer]:
    tradeable = [p for p in roster if p.position not in FINDER_SKIP_POSITIONS and p.id not in exclude]
    return sorted(tradeable, key=lambda p: -sum(p.mean(w) for w in season.weeks))[:FINDER_POOL]


def find_trades(
    season: SeasonEvaluator,
    mine: list[WeeklyPlayer],
    theirs: list[WeeklyPlayer],
    capacity: int,
    min_gain: float = 1.0,
    exclude: set[int] = frozenset(),
) -> list[Trade]:
    """Packages up to FINDER_MAX_SIZE per side where both teams gain at least `min_gain`.

    `exclude` holds player ids that can't be offered (e.g. already in an accepted trade).
    """
    my_pool, their_pool = _pool(season, mine, exclude), _pool(season, theirs, exclude)
    found = []
    for n_give in range(1, FINDER_MAX_SIZE + 1):
        for n_get in range(1, FINDER_MAX_SIZE + 1):
            if n_give + n_get > FINDER_MAX_SIZE + 1:  # 1-1, 2-1, 1-2 only
                continue
            for gives in combinations(my_pool, n_give):
                for gets in combinations(their_pool, n_get):
                    trade = evaluate(season, mine, theirs, list(gives), list(gets), capacity)
                    if trade.a.gain >= min_gain and trade.b.gain >= min_gain:
                        found.append(trade)
    found.sort(key=lambda t: (t.a.gain, t.b.gain), reverse=True)  # every result already helps them
    return found
