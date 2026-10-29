"""Build consensus Projection objects from stored ESPN + Sleeper data."""

from collections import defaultdict

from analysis.projections import Projection, consensus, project
from league.models import League, Player, PlayerWeek, ProGame
from nfl.models import Projection as SourceProjection

OUT_STATUSES = {"OUT", "INJURY_RESERVE", "SUSPENSION"}


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
    history = defaultdict(list)
    for w in PlayerWeek.objects.filter(player_id__in=ids, season=season).order_by("week"):
        if w.week == league.current_week:
            espn_week[w.player_id] = w.projected_points
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
            if week == league.current_week and p.injury_status in OUT_STATUSES:
                out[(p.pk, week)] = Projection(0.0, 0.0)
                continue
            espn = espn_week[p.pk] if week == league.current_week else espn_ros_per_game
            mean_pts = consensus({"espn": espn, "sleeper": sleeper.get((p.pk, week))})
            out[(p.pk, week)] = project(mean_pts, history[p.pk], p.position) if mean_pts is not None else None
    return out


def remaining_weeks(league: League) -> range:
    return range(league.current_week, league.final_week + 1)
