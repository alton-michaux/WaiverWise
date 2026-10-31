from django.core.management.base import BaseCommand

from analysis.accuracy import Result, residual_fit, source_errors
from league.models import League, PlayerWeek
from nfl.models import ProjectionSnapshot

SIGNALS = {"opp_rating": "matchup rating", "implied_total": "implied total"}


class Command(BaseCommand):
    help = "Compare saved projections with what happened, and test whether matchup data explains the misses."

    def handle(self, *args, **opts):
        league = League.objects.order_by("-season").first()
        if not league:
            self.stdout.write("no league, nothing to measure")
            return
        results = self._results(league)
        if not results:
            self.stdout.write("no finished weeks with snapshots yet")
            return
        self.stdout.write(f"{len(results)} player-games")
        for source, error in source_errors(results).items():
            self.stdout.write(f"  {source}: {'n/a' if error is None else f'{error:.2f}'} points mean absolute error")
        self._fits("all", results)
        for position in sorted({r.position for r in results}):
            self._fits(position, [r for r in results if r.position == position])

    def _results(self, league) -> list[Result]:
        snapshots = ProjectionSnapshot.objects.filter(
            season=league.season,
            week__lt=league.current_week,
            mean__gt=0,  # ruled out before kickoff: 0 by rule, so it says nothing about the projections
        ).select_related("player")
        actual = {
            (pid, week): points or 0
            for pid, week, points in PlayerWeek.objects.filter(season=league.season, week__lt=league.current_week)
            .exclude(breakdown={})  # empty breakdown = didn't play
            .values_list("player_id", "week", "points")
        }
        return [
            Result(s.player.position, actual[(s.player_id, s.week)], s.espn, s.sleeper, s.mean, s.opp_rating, s.implied_total)
            for s in snapshots
            if (s.player_id, s.week) in actual
        ]

    def _fits(self, label, results):
        for signal, name in SIGNALS.items():
            fit = residual_fit(results, signal)
            if fit:
                self.stdout.write(
                    f"  {label}: projection miss vs {name}: r={fit.r:+.3f}, {fit.slope:+.3f} pts per unit, {fit.rows} games"
                )
