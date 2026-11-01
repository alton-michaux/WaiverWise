from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from league import connect
from league.models import League, Membership, active_leagues


class Command(BaseCommand):
    help = "Attach an existing user to a team, for setting up your own account. Safe to run again."

    def add_arguments(self, parser):
        parser.add_argument("username")
        parser.add_argument("team", type=int, help="the teamId in your ESPN team page's URL")
        parser.add_argument("--league", type=int, help="ESPN league id, needed once there are several this season")

    def handle(self, *args, username, team, league=None, **opts):
        user = get_user_model().objects.filter(username=username).first()
        if user is None:
            raise CommandError("no user with that username; run createsuperuser first")
        chosen = self._league(league)
        claimed = chosen.teams.filter(espn_id=team).first()
        if claimed is None:
            raise CommandError(f"no team {team} in league {chosen.pk}")
        other = Membership.objects.filter(team=claimed).exclude(user=user).first()
        if other:
            raise CommandError(f"team {team} is already claimed by user {other.user_id}")
        Membership.objects.update_or_create(user=user, league=chosen, defaults={"team": claimed})
        self.stdout.write(f"user {user.pk} now has team {team} in league {chosen.pk}")
        if settings.ESPN_S2 and settings.ESPN_SWID:
            connect._store_cookies(user, chosen, settings.ESPN_S2, settings.ESPN_SWID)
            self.stdout.write("saved ESPN_S2 and ESPN_SWID as this user's encrypted login for the league")

    def _league(self, espn_id: int | None) -> League:
        leagues = active_leagues()
        if espn_id is not None:
            leagues = leagues.filter(espn_id=espn_id)
        found = list(leagues[:2])
        if not found:
            raise CommandError("no matching league; run the first sync")
        if len(found) > 1:
            raise CommandError("several leagues this season; pass --league with the ESPN league id")
        return found[0]
