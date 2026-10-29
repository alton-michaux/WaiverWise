import logging
from datetime import timedelta

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.utils import timezone

from league.models import League

log = logging.getLogger(__name__)

# (job id, sync steps, cron fields) in league-local time (settings.TIME_ZONE)
JOBS = [
    # overnight: rosters, waiver results, nflverse stats, refreshed projections
    ("nightly", ["league", "nfl", "projections"], {"hour": 5, "minute": 0}),
    # game days: injuries, inactives, live scores
    ("sunday_live", ["league"], {"day_of_week": "sun", "hour": "10-23", "minute": "*/30"}),
    ("primetime_live", ["league"], {"day_of_week": "mon,thu", "hour": "19-23", "minute": "*/30"}),
    # projection sources update through the morning and again after inactives (~90 min before kickoff)
    ("sunday_projections", ["projections"], {"day_of_week": "sun", "hour": "9,11", "minute": 45}),
    ("primetime_projections", ["projections"], {"day_of_week": "mon,thu", "hour": 18, "minute": 45}),
]
# WSL / Docker may have been off: catch up on start if data is older than this
STALE_AFTER = timedelta(hours=6)


def run(steps: list[str]):
    try:
        call_command("sync_all", only=steps)
    except Exception:
        log.exception("scheduled sync %s failed", steps)


class Command(BaseCommand):
    help = "Run scheduled syncs in the foreground (used by the compose `scheduler` service)."

    def handle(self, *args, **opts):
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
        scheduler = BlockingScheduler(timezone=settings.TIME_ZONE)
        for job_id, steps, cron in JOBS:
            scheduler.add_job(
                run,
                CronTrigger(**cron),
                args=[steps],
                id=job_id,
                coalesce=True,
                max_instances=1,
                misfire_grace_time=15 * 60,
            )

        last = League.objects.order_by("-synced_at").values_list("synced_at", flat=True).first()
        if last is None or timezone.now() - last > STALE_AFTER:
            log.info("data is stale (last sync %s), running a full sync now", last)
            run(["league", "nfl", "projections"])

        for job in scheduler.get_jobs():
            log.info("scheduled %s: %s", job.id, job.trigger)
        scheduler.start()
