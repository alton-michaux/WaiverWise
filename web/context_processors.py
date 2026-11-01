from datetime import timedelta

from django.db.models import Q
from django.urls import reverse
from django.utils import timezone
from django.utils.functional import SimpleLazyObject

from league.models import SyncRun
from web.services import remembered_team

STALE_AFTER = timedelta(hours=12)


def sync_status(request):
    # staff without a league still need to hear that the first sync failed
    if not request.user.is_authenticated or (request.league is None and not request.user.is_staff):
        return {}
    last_synced = request.league.synced_at if request.league else None
    return {"last_synced": last_synced, "sync_problem": sync_problem(last_synced, request.league, request.user.is_staff)}


def team_picker(request):
    """The header's team and league pickers. Lazy, so HTMX partials don't pay for it."""
    if not request.user.is_authenticated:
        return {}

    def build():
        membership = request.membership
        if membership is None:
            return {}
        teams = sorted(membership.league.teams.all(), key=lambda t: (t.pk != membership.team_id, t.name))
        # switching teams stays on the current tab, or lands on Start/Sit from anywhere else
        sections = {reverse("startsit"), reverse("waivers"), reverse("trades")}
        return {
            "team": remembered_team(request, membership) or membership.team,
            "teams": teams,
            "my_team_id": membership.team.espn_id,
            "league": membership.league,
            "memberships": request.memberships,
            "switch_url": request.path if request.path in sections else reverse("startsit"),
        }

    return {"picker": SimpleLazyObject(build)}


def sync_problem(last_synced, league=None, staff=False) -> str:
    """Banner text when this league's data can't be trusted, else "". How to fix it is for staff only."""
    # no league = the sync failed before the league existed
    latest = SyncRun.objects.filter(Q(league=league) | Q(league__isnull=True), step="league").first()
    if latest and not latest.ok:
        if "ESPNAccessDenied" in latest.error:
            text = "ESPN rejected this league's saved login, so its data isn't updating."
            if staff:
                text += (
                    " Grab fresh espn_s2 and SWID cookies and update the ESPN_S2 / ESPN_SWID secrets "
                    "(GitHub Actions in production, .env locally)."
                )
            return text
        detail = f" ({latest.error[:120]})" if staff else ""
        return f"The last league sync failed{detail}. Data may be out of date."
    if last_synced and timezone.now() - last_synced > STALE_AFTER:
        return "League data is more than 12 hours old." + (" Check that the scheduler is running." if staff else "")
    return ""
