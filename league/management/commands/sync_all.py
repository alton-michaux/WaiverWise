import logging
import time

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import connection

from league.models import League, SyncRun

log = logging.getLogger(__name__)

KEEP_RUNS = 200
# arbitrary app-wide key for pg_try_advisory_lock; one sync at a time across all processes
SYNC_LOCK_KEY = 7_220_001

STEPS = {
    "league": "sync_league",
    "nfl": "sync_nfl",
    "projections": "sync_projections",
}


class Command(BaseCommand):
    help = "Run every sync step in order. A failing step is logged and the rest still run."

    def add_arguments(self, parser):
        parser.add_argument("--only", nargs="+", choices=STEPS, help="run just these steps")

    def handle(self, *args, only=None, **opts):
        if not self._acquire_lock():
            self.stdout.write("another sync is already running, skipping")
            return
        try:
            self._run(only)
        finally:
            self._release_lock()

    def _acquire_lock(self) -> bool:
        if connection.vendor != "postgresql":
            return True
        with connection.cursor() as cur:
            cur.execute("SELECT pg_try_advisory_lock(%s)", [SYNC_LOCK_KEY])
            return cur.fetchone()[0]

    def _release_lock(self):
        if connection.vendor == "postgresql":
            with connection.cursor() as cur:
                cur.execute("SELECT pg_advisory_unlock(%s)", [SYNC_LOCK_KEY])

    def _run(self, only):
        failed = []
        for name, command in STEPS.items():
            if only and name not in only:
                continue
            started = time.monotonic()
            try:
                call_command(command, stdout=self.stdout, stderr=self.stderr)
            except Exception as e:
                log.exception("sync step %s failed", name)
                SyncRun.objects.create(league=self._league_of(name), step=name, ok=False, error=f"{type(e).__name__}: {e}"[:2000])
                failed.append(name)
                continue
            SyncRun.objects.create(league=self._league_of(name), step=name, ok=True)
            self.stdout.write(f"[{name}] done in {time.monotonic() - started:.1f}s")
        try:
            call_command("snapshot_projections", stdout=self.stdout, stderr=self.stderr)
        except Exception:
            # the app doesn't read these, so a missed snapshot only costs the next sync's refresh
            log.exception("snapshotting projections failed")
        # full syncs only: game-day syncs outdate the result within 30 minutes, and each team costs a full search
        if not only or set(STEPS) <= set(only):
            try:
                call_command("refresh_best_deals", stdout=self.stdout, stderr=self.stderr)
            except Exception:
                # the Trades tab searches on demand when this is missing, so it isn't worth failing the sync
                log.exception("refreshing best deals failed")
        self._prune_runs()
        if failed:
            raise CommandError(f"failed steps: {', '.join(failed)}")

    def _league_of(self, step: str) -> League | None:
        """Only the league step belongs to one league; the others are shared."""
        if step != "league":
            return None
        return League.objects.filter(espn_id=settings.ESPN_LEAGUE_ID, season=settings.ESPN_SEASON).first()

    def _prune_runs(self):
        """Keep KEEP_RUNS per league, and per the shared steps, so a busy league can't erase a quiet one's history."""
        for league_id in SyncRun.objects.order_by().values_list("league_id", flat=True).distinct():
            stale = SyncRun.objects.filter(league_id=league_id).values_list("pk", flat=True)[KEEP_RUNS:]
            SyncRun.objects.filter(pk__in=list(stale)).delete()
