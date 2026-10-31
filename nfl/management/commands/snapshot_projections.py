from django.core.management.base import BaseCommand

from league.bulk import upsert
from league.models import League, Player, PlayerWeek, ProGame
from nfl.consensus import game_started, week_kickoffs, weekly_projections
from nfl.matchups import defense_ratings
from nfl.models import GameLine, Projection, ProjectionSnapshot


class Command(BaseCommand):
    help = "Save this week's projections for games not yet started, so the last pre-kickoff value is what stays."

    def handle(self, *args, **opts):
        league = League.objects.order_by("-season").first()
        if not league:
            self.stdout.write("no league to snapshot, skipping")
            return
        season, week = league.season, league.current_week
        kickoffs = week_kickoffs(season, week)
        players = [p for p in Player.objects.all() if not game_started(kickoffs.get(p.pro_team))]
        proj = weekly_projections(league, players, range(week, week + 1))
        opponents = dict(ProGame.objects.filter(season=season, week=week).values_list("team", "opponent"))
        espn = dict(PlayerWeek.objects.filter(season=season, week=week).values_list("player_id", "projected_points"))
        sleeper = dict(
            Projection.objects.filter(season=season, week=week, source="sleeper").values_list("player_id", "points")
        )
        ratings = defense_ratings(league)
        implied = dict(GameLine.objects.filter(season=season, week=week).values_list("team", "implied_total"))

        def opp_rating(p):
            rating = ratings.get((opponents.get(p.pro_team), p.position))
            return rating.points if rating else None

        snapshots = [
            ProjectionSnapshot(
                player=p,
                season=season,
                week=week,
                espn=espn.get(p.pk),
                sleeper=sleeper.get(p.pk),
                mean=x.mean,
                sd=x.sd,
                opponent=opponents.get(p.pro_team, ""),
                opp_rating=opp_rating(p),
                implied_total=implied.get(p.pro_team),
            )
            for p in players
            if (x := proj[(p.pk, week)])
        ]
        upsert(
            ProjectionSnapshot,
            snapshots,
            unique_fields=["player", "season", "week"],
            update_fields=["espn", "sleeper", "mean", "sd", "opponent", "opp_rating", "implied_total", "captured_at"],
        )
        self.stdout.write(f"[snapshot] week {week}: {len(snapshots)} projections")
