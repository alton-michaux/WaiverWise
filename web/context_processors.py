from datetime import timedelta

from django.utils import timezone

from league.models import League, SyncRun

STALE_AFTER = timedelta(hours=12)


def sync_status(request):
    if not request.user.is_authenticated:
        return {}
    last_synced = League.objects.order_by("-synced_at").values_list("synced_at", flat=True).first()
    return {"last_synced": last_synced, "sync_problem": sync_problem(last_synced)}


def sync_problem(last_synced) -> str:
    """Banner text when league data can't be trusted, else ""."""
    latest = SyncRun.objects.filter(step="league").first()
    if latest and not latest.ok:
        if "ESPNAccessDenied" in latest.error:
            return (
                "ESPN rejected the saved login cookies, so league data isn't updating. "
                "Grab fresh espn_s2 and SWID cookies and update the ESPN_S2 / ESPN_SWID secrets "
                "(GitHub Actions in production, .env locally)."
            )
        return f"The last league sync failed ({latest.error[:120]}). Data may be out of date."
    if last_synced and timezone.now() - last_synced > STALE_AFTER:
        return f"League data is more than 12 hours old. Check that the scheduler is running."
    return ""
