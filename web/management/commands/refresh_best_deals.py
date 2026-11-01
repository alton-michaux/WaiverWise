import time

from django.core.management.base import BaseCommand

from league.models import Team, active_leagues
from web.deals import best_deals


class Command(BaseCommand):
    help = "Search claimed teams' trade deals ahead of time, so the Trades tab opens instantly on a slow server."

    def handle(self, *args, **opts):
        teams = Team.objects.filter(membership__isnull=False, league__in=active_leagues()).select_related("league")
        if not teams:
            self.stdout.write("no claimed teams to search deals for, skipping")
            return
        started = time.monotonic()
        found = sum(best_deals(team, budget=float("inf"))["total"] for team in teams.order_by("pk"))
        self.stdout.write(f"[best deals] {found} found for {len(teams)} teams in {time.monotonic() - started:.1f}s")
