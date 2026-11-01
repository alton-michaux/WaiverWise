import httpx
from django.core.management.base import BaseCommand
from django.db import transaction

from analysis.scoring import score
from league.bulk import upsert
from league.models import Player, active_leagues
from nfl import sleeper
from nfl.consensus import remaining_weeks
from nfl.models import Projection


class Command(BaseCommand):
    help = "Pull Sleeper projections for the current week through the end of the season, scored for each league."

    def add_arguments(self, parser):
        parser.add_argument("--league", type=int, help="just this league")

    def handle(self, *args, league=None, **opts):
        fetched = {}  # Sleeper's numbers don't depend on the league, so each week is only fetched once
        leagues = active_leagues()
        for league in leagues.filter(pk=league) if league else leagues:
            players = {p.sleeper_id: p for p in Player.objects.filter(league_entries__league=league).exclude(sleeper_id="")}
            for week in remaining_weeks(league):
                if (league.season, week) not in fetched:
                    fetched[(league.season, week)] = self._fetch(league.season, week)
                rows = fetched[(league.season, week)]
                if rows is None:
                    continue
                saved = self._save(league, week, rows, players)
                self.stdout.write(f"league {league.pk} week {week}: {saved} projections")

    def _fetch(self, season, week) -> list | None:
        try:
            return sleeper.fetch_week(season, week)
        except httpx.HTTPError as e:
            self.stderr.write(f"week {week}: Sleeper fetch failed ({e}), skipping")
            return None

    @transaction.atomic
    def _save(self, league, week, rows, players) -> int:
        projections = [
            Projection(
                league=league,
                player=player,
                season=league.season,
                week=week,
                source="sleeper",
                points=score(row["stats"], league.scoring, player.position),
                opponent=row.get("opponent") or "",
                stats=row["stats"],
            )
            for row in rows
            if (player := players.get(row["player_id"]))
        ]
        upsert(
            Projection,
            projections,
            unique_fields=["league", "player", "season", "week", "source"],
            update_fields=["points", "opponent", "stats", "updated_at"],
        )
        return len(projections)
