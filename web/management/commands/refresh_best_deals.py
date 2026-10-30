import time

from django.conf import settings
from django.core.management.base import BaseCommand

from league.models import League
from web.deals import best_deals


class Command(BaseCommand):
    help = "Search your team's trade deals ahead of time, so the Trades tab opens instantly on a slow server."

    def handle(self, *args, **opts):
        league = League.objects.order_by("-season").first()
        team = league and league.teams.filter(espn_id=settings.ESPN_MY_TEAM_ID).first()
        if not team:
            self.stdout.write("no team to search deals for, skipping")
            return
        started = time.monotonic()
        result = best_deals(team, budget=float("inf"))
        self.stdout.write(f"[best deals] {result['total']} found in {time.monotonic() - started:.1f}s")
