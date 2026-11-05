"""How much each defense gives up to each position, for showing context next to a projection.

Projections already move with the opponent, so these explain a number and never adjust one.

A defense is judged by what offenses scored against it compared with what those same offenses
score elsewhere. Points allowed are noisy from one half of a season to the next, so ratings are
shrunk toward average and shown as coarse grades.
"""

from collections import defaultdict
from dataclasses import dataclass
from statistics import mean

GOOD, NEUTRAL, TOUGH = "Good", "Neutral", "Tough"

PRIOR_GAMES = 4  # weight of "this defense is average", in games
MIN_GAMES = 3  # fewer than this gets no grade
GRADED_SHARE = 4  # top and bottom quarter of defenses get a grade: 8 of 32


@dataclass(frozen=True)
class GameScore:
    """All of one offense's points at one position in one game."""

    week: int
    offense: str
    defense: str
    position: str
    points: float


@dataclass(frozen=True)
class Rating:
    points: float  # per game over what these offenses usually score; positive = soft
    rank: int  # 1 = softest at the position
    grade: str


def _grade(index: int, count: int) -> str:
    """Grade for the `index`-th softest of `count`."""
    graded = count // GRADED_SHARE
    return GOOD if index < graded else TOUGH if index >= count - graded else NEUTRAL


def _deltas(games: list[GameScore]) -> dict[tuple[str, str], list[float]]:
    """(defense, position) -> each game's points minus the offense's average in its other games."""
    by_offense = defaultdict(list)
    for g in games:
        by_offense[(g.offense, g.position)].append(g.points)
    deltas = defaultdict(list)
    for g in games:
        scores = by_offense[(g.offense, g.position)]
        if len(scores) > 1:
            deltas[(g.defense, g.position)].append(g.points - (sum(scores) - g.points) / (len(scores) - 1))
    return deltas


def ratings(games: list[GameScore]) -> dict[tuple[str, str], Rating]:
    """(defense, position) -> Rating. Missing when the defense has too few games to judge."""
    by_position = defaultdict(list)
    for (defense, position), deltas in _deltas(games).items():
        if len(deltas) >= MIN_GAMES:
            by_position[position].append((sum(deltas) / (len(deltas) + PRIOR_GAMES), defense))

    out = {}
    for position, rated in by_position.items():
        rated.sort(key=lambda r: (-r[0], r[1]))
        for i, (points, defense) in enumerate(rated):
            out[(defense, position)] = Rating(round(points, 2), i + 1, _grade(i, len(rated)))
    return out


def schedule_ratings(
    opponents: dict[str, list[str]], position: str, rated: dict[tuple[str, str], Rating]
) -> dict[str, Rating]:
    """Pro team -> how soft its upcoming opponents are at `position`, graded against the other teams.

    Unrated opponents count as average. Teams with no games in the stretch are left out.
    """
    averages = []
    for team, faced in opponents.items():
        if faced:
            points = [r.points if (r := rated.get((opp, position))) else 0.0 for opp in faced]
            averages.append((mean(points), team))
    averages.sort(key=lambda a: (-a[0], a[1]))
    return {
        team: Rating(round(points, 2), i + 1, _grade(i, len(averages)))
        for i, (points, team) in enumerate(averages)
    }


def implied_totals(total: float | None, home_spread_line: float | None) -> tuple[float | None, float | None]:
    """(home, away) points implied by the betting lines. `home_spread_line` is positive when home is favored."""
    if total is None or home_spread_line is None:
        return None, None
    home = (total + home_spread_line) / 2
    return round(home, 2), round(total - home, 2)
