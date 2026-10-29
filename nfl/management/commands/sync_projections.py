import httpx
from django.core.management.base import BaseCommand
from django.db import transaction

from analysis.scoring import score
from league.models import League, Player
from nfl import sleeper
from nfl.consensus import remaining_weeks
from nfl.models import Projection


class Command(BaseCommand):
    help = "Pull Sleeper projections for the current week through the end of the season."

    def handle(self, *args, **opts):
        league = League.objects.latest("season")
        players = {p.sleeper_id: p for p in Player.objects.exclude(sleeper_id="")}
        for week in remaining_weeks(league):
            try:
                rows = sleeper.fetch_week(league.season, week)
            except httpx.HTTPError as e:
                self.stderr.write(f"week {week}: Sleeper fetch failed ({e}), skipping")
                continue
            saved = self._save(league, week, rows, players)
            self.stdout.write(f"week {week}: {saved} projections")

    @transaction.atomic
    def _save(self, league, week, rows, players) -> int:
        saved = 0
        for row in rows:
            player = players.get(row["player_id"])
            if not player:
                continue
            Projection.objects.update_or_create(
                player=player,
                season=league.season,
                week=week,
                source="sleeper",
                defaults=dict(
                    points=score(row["stats"], league.scoring, is_dst=player.position == "D/ST"),
                    opponent=row.get("opponent") or "",
                    stats=row["stats"],
                ),
            )
            saved += 1
        return saved
