import nflreadpy
import polars as pl
from django.core.management.base import BaseCommand
from django.db import transaction

from analysis.matchups import implied_totals
from league.bulk import upsert
from league.models import League, Player
from nfl.ids import espn_team, update_crosswalk
from nfl.models import GameLine, Usage


class Command(BaseCommand):
    help = "Update the ESPN/Sleeper/nflverse id crosswalk, weekly usage and game lines from nflverse."

    def handle(self, *args, **opts):
        self.stdout.write(f"crosswalk: {update_crosswalk()} players matched")
        season = League.objects.latest("season").season
        self.stdout.write(f"usage: {self._usage(season)} player-weeks")
        self.stdout.write(f"lines: {self._lines(season)} team-games")

    @transaction.atomic
    def _lines(self, season: int) -> int:
        schedule = nflreadpy.load_schedules(season).filter(pl.col("game_type") == "REG")
        lines = []
        for g in schedule.iter_rows(named=True):
            total, spread = g["total_line"], g["spread_line"]
            home_implied, away_implied = implied_totals(total, spread)
            neutral = g["location"] == "Neutral"
            for team, is_home, implied, team_spread, rest in (
                (g["home_team"], True, home_implied, -spread if spread is not None else None, g["home_rest"]),
                (g["away_team"], False, away_implied, spread, g["away_rest"]),
            ):
                lines.append(
                    GameLine(
                        season=season,
                        week=g["week"],
                        team=espn_team(team),
                        is_home=None if neutral else is_home,
                        spread=team_spread,
                        total=total,
                        implied_total=implied,
                        rest_days=rest,
                        roof=g["roof"] or "",
                    )
                )
        upsert(
            GameLine,
            lines,
            unique_fields=["season", "week", "team"],
            update_fields=["is_home", "spread", "total", "implied_total", "rest_days", "roof"],
        )
        return len(lines)

    @transaction.atomic
    def _usage(self, season: int) -> int:
        stats = nflreadpy.load_player_stats(season).filter(pl.col("season_type") == "REG")
        snaps = nflreadpy.load_snap_counts(season).select(
            pl.col("pfr_player_id").alias("pfr_id"), "week", "offense_pct"
        )
        by_gsis = {p.gsis_id: p for p in Player.objects.exclude(gsis_id="")}
        by_pfr = {p.pfr_id: p for p in Player.objects.exclude(pfr_id="")}
        snap_pct = {
            (by_pfr[r["pfr_id"]].pk, r["week"]): r["offense_pct"]
            for r in snaps.iter_rows(named=True)
            if r["pfr_id"] in by_pfr
        }
        usage = [
            Usage(
                player=player,
                season=season,
                week=r["week"],
                snap_pct=snap_pct.get((player.pk, r["week"])),
                targets=r["targets"] or 0,
                target_share=r["target_share"],
                air_yards_share=r["air_yards_share"],
                carries=r["carries"] or 0,
                receptions=r["receptions"] or 0,
            )
            for r in stats.iter_rows(named=True)
            if (player := by_gsis.get(r["player_id"]))
        ]
        upsert(
            Usage,
            usage,
            unique_fields=["player", "season", "week"],
            update_fields=["snap_pct", "targets", "target_share", "air_yards_share", "carries", "receptions"],
        )
        return len(usage)
