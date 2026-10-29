"""Build consensus Projection objects from stored ESPN + Sleeper data."""

from collections import defaultdict
from datetime import datetime, timedelta

from django.utils import timezone

from analysis.projections import Projection, consensus, project
from league.models import League, Player, PlayerWeek, ProGame
from nfl.models import Projection as SourceProjection

OUT_STATUSES = {"OUT", "INJURY_RESERVE", "SUSPENSION"}
GAME_LENGTH = timedelta(hours=4)


def weekly_projections(
    league: League, players: list[Player], weeks: range
) -> dict[tuple[int, int], Projection | None]:
    """(player_id, week) -> Projection, or None when the player has no game that week."""
    ids = [p.pk for p in players]
    season = league.season

    plays = defaultdict(set)  # pro team -> weeks with a game
    for team, week in ProGame.objects.filter(season=season).values_list("team", "week"):
        plays[team].add(week)

    sleeper = {
        (pid, week): pts
        for pid, week, pts in SourceProjection.objects.filter(
            player_id__in=ids, season=season, source="sleeper", week__in=weeks
        ).values_list("player_id", "week", "points")
    }
    espn_week = dict.fromkeys(ids)
    actual = {}
    kickoffs = week_kickoffs(season, league.current_week)
    history = defaultdict(list)
    for w in PlayerWeek.objects.filter(player_id__in=ids, season=season).order_by("week"):
        if w.week == league.current_week:
            espn_week[w.player_id] = w.projected_points
            if w.breakdown:
                actual[w.player_id] = w.points
        elif w.week < league.current_week and w.breakdown:  # empty breakdown = didn't play
            history[w.player_id].append(w.points or 0)

    out = {}
    for p in players:
        remaining = len([w for w in plays[p.pro_team] if w >= league.current_week])
        espn_ros_per_game = p.season_projection / remaining if remaining else None
        for week in weeks:
            if week not in plays[p.pro_team]:
                out[(p.pk, week)] = None
                continue
            if week == league.current_week and game_final(kickoffs.get(p.pro_team)):
                out[(p.pk, week)] = Projection(actual.get(p.pk) or 0.0, 0.0)
                continue
            if week == league.current_week and p.injury_status in OUT_STATUSES:
                out[(p.pk, week)] = Projection(0.0, 0.0)
                continue
            espn = espn_week[p.pk] if week == league.current_week else espn_ros_per_game
            mean_pts = consensus({"espn": espn, "sleeper": sleeper.get((p.pk, week))})
            out[(p.pk, week)] = project(mean_pts, history[p.pk], p.position) if mean_pts is not None else None
    return out


def week_kickoffs(season: int, week: int) -> dict[str, datetime]:
    return dict(
        ProGame.objects.filter(season=season, week=week, kickoff__isnull=False).values_list("team", "kickoff")
    )


def game_started(kickoff: datetime | None) -> bool:
    return kickoff is not None and kickoff <= timezone.now()


def game_final(kickoff: datetime | None) -> bool:
    """No live game-state feed yet; assume a game is over 4 hours after kickoff."""
    return kickoff is not None and kickoff + GAME_LENGTH <= timezone.now()


def remaining_weeks(league: League) -> range:
    return range(league.current_week, league.final_week + 1)
