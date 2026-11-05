"""Build matchup ratings from stored league data.

Computed per request rather than stored: the points are in one league's scoring, so a global
table would have to be undone for multi-league.
"""

from collections import defaultdict

from analysis.matchups import GameScore, Rating, ratings
from league.models import League, PlayerWeek, ProGame


def upcoming_opponents(league: League, weeks: range) -> dict[str, list[str]]:
    """Pro team -> its opponents over `weeks`, in order. Byes just aren't listed."""
    opponents = defaultdict(list)
    games = ProGame.objects.filter(season=league.season, week__in=weeks).order_by("week")
    for team, opponent in games.values_list("team", "opponent"):
        opponents[team].append(opponent)
    return opponents


def defense_ratings(league: League) -> dict[tuple[str, str], Rating]:
    """(defense, position) -> Rating, from completed weeks only.

    Uses each player's current team, so a traded player's old games count against the wrong defense.
    """
    season, week = league.season, league.current_week
    opponents = {
        (team, w): opponent
        for team, w, opponent in ProGame.objects.filter(season=season, week__lt=week).values_list(
            "team", "week", "opponent"
        )
    }
    totals = defaultdict(float)
    rows = (
        PlayerWeek.objects.filter(season=season, week__lt=week)
        .exclude(breakdown={})  # empty breakdown = didn't play
        .values_list("player__pro_team", "player__position", "week", "points")
    )
    for team, position, w, points in rows:
        totals[(team, position, w)] += points or 0
    return ratings(
        [
            GameScore(w, team, opponents[(team, w)], position, points)
            for (team, position, w), points in totals.items()
            if (team, w) in opponents
        ]
    )
