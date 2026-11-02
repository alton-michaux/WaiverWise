import logging
import time
import traceback
from datetime import timedelta

from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import connection
from django.utils import timezone
from espn_api.requests.espn_requests import ESPNAccessDenied

from league.models import League, SyncRun, leagues_in_use
from web.models import ExplainUsage, Explanation

log = logging.getLogger(__name__)

KEEP_RUNS = 200
KEEP_EXPLANATIONS = timedelta(days=30)
# arbitrary keys for pg_try_advisory_lock: one sync at a time for the shared steps and for each league
SYNC_LOCK_KEY = 7_220_001
LEAGUE_LOCK_KEY = 7_220_002
ACTIVE_WITHIN = timedelta(days=14)  # a league nobody has opened in this long stops using up the sync schedule
ABANDONED_AFTER = timedelta(days=1)

SHARED_STEPS = {
    "nfl": "sync_nfl",
    "projections": "sync_projections",
}
STEPS = ["league", *SHARED_STEPS]


def safe_error(e: Exception) -> str:
    """The error's type, never its text: messages can name a league or echo a request, and these reach public logs."""
    return type(e).__name__


class Command(BaseCommand):
    help = "Sync leagues and shared data. A failing step or league is logged and the rest still run."

    def add_arguments(self, parser):
        parser.add_argument("--only", nargs="+", choices=STEPS, help="run just these steps")
        parser.add_argument("--league", type=int, help="run one league's first sync, whatever its activity")

    def handle(self, *args, only=None, league=None, **opts):
        failed = self._run_one(league) if league else self._run_all(only)
        self._prune_runs()
        self._prune_explanations()
        if failed:
            raise CommandError(f"failed steps: {', '.join(failed)}")

    def _run_all(self, only) -> list[str]:
        if not League.objects.exists():
            self.stdout.write("no leagues yet, skipping")
            return []
        self._forget_abandoned()
        failed = []
        if not only or "league" in only:
            for league in leagues_in_use(timezone.now() - ACTIVE_WITHIN).order_by("pk"):
                failed += self._sync_league(league)
        if not self._try_lock(SYNC_LOCK_KEY):
            self.stdout.write("another sync is running the shared steps, skipping them")
            return failed
        try:
            for name, command in SHARED_STEPS.items():
                if not only or name in only:
                    failed += self._shared_step(name, command)
            self._snapshot()
            # full syncs only: game-day syncs outdate the result within 30 minutes, and each team costs a full search
            if not only or set(STEPS) <= set(only):
                self._refresh_deals()
        finally:
            self._unlock(SYNC_LOCK_KEY)
        return failed

    def _run_one(self, pk: int) -> list[str]:
        league = League.objects.filter(pk=pk).first()
        if league is None:
            self.stdout.write(f"no league {pk}, skipping")
            return []
        failed = self._sync_league(league)
        league.refresh_from_db()
        if failed or not league.has_data:
            return failed
        # new players need the id crosswalk before they can get projections
        self._wait_for_lock(SYNC_LOCK_KEY)
        try:
            failed += self._shared_step("nfl", "sync_nfl")
            failed += self._shared_step("projections", "sync_projections", league=pk)
            self._snapshot(league=pk)
        finally:
            self._unlock(SYNC_LOCK_KEY)
        return failed

    def _sync_league(self, league: League) -> list[str]:
        if not self._try_lock(LEAGUE_LOCK_KEY, league.pk):
            self.stdout.write(f"league {league.pk} is already syncing, skipping")
            return []
        started = time.monotonic()
        try:
            call_command("sync_league", league.pk, stdout=self.stdout, stderr=self.stderr)
        except ESPNAccessDenied as e:
            # nobody's cookies work, which the app tells its members. It isn't a fault in the sync.
            SyncRun.objects.create(league=league, step="league", ok=False, error=safe_error(e))
            self.stdout.write(f"league {league.pk}: ESPN refused every login")
            return []
        except Exception as e:
            self._log_failure(f"league {league.pk}", e)
            SyncRun.objects.create(league=league, step="league", ok=False, error=safe_error(e))
            return [f"league {league.pk}"]
        else:
            SyncRun.objects.create(league=league, step="league", ok=True)
            self.stdout.write(f"[league {league.pk}] done in {time.monotonic() - started:.1f}s")
            return []
        finally:
            self._unlock(LEAGUE_LOCK_KEY, league.pk)

    def _shared_step(self, name: str, command: str, **options) -> list[str]:
        started = time.monotonic()
        try:
            call_command(command, stdout=self.stdout, stderr=self.stderr, **options)
        except Exception as e:
            self._log_failure(name, e)
            SyncRun.objects.create(step=name, ok=False, error=safe_error(e))
            return [name]
        SyncRun.objects.create(step=name, ok=True)
        self.stdout.write(f"[{name}] done in {time.monotonic() - started:.1f}s")
        return []

    def _snapshot(self, **options):
        try:
            call_command("snapshot_projections", stdout=self.stdout, stderr=self.stderr, **options)
        except Exception as e:
            # the app doesn't read these, so a missed snapshot only costs the next sync's refresh
            self._log_failure("snapshot", e)

    def _refresh_deals(self):
        try:
            call_command("refresh_best_deals", stdout=self.stdout, stderr=self.stderr)
        except Exception as e:
            # the Trades tab searches on demand when this is missing, so it isn't worth failing the sync
            self._log_failure("best deals", e)

    def _log_failure(self, what: str, e: Exception):
        """Where it failed in our code, with no values from the error itself."""
        where = "".join(traceback.format_tb(e.__traceback__))
        log.error("%s failed: %s\n%s", what, safe_error(e), where)

    def _forget_abandoned(self):
        """Someone started connecting a league and never picked a team. Takes their cookies with it."""
        League.objects.filter(
            has_data=False, memberships__isnull=True, created_at__lt=timezone.now() - ABANDONED_AFTER
        ).delete()

    def _try_lock(self, *key) -> bool:
        if connection.vendor != "postgresql":
            return True
        with connection.cursor() as cur:
            cur.execute(f"SELECT pg_try_advisory_lock({', '.join(['%s'] * len(key))})", list(key))
            return cur.fetchone()[0]

    def _wait_for_lock(self, *key):
        if connection.vendor == "postgresql":
            with connection.cursor() as cur:
                cur.execute(f"SELECT pg_advisory_lock({', '.join(['%s'] * len(key))})", list(key))

    def _unlock(self, *key):
        if connection.vendor == "postgresql":
            with connection.cursor() as cur:
                cur.execute(f"SELECT pg_advisory_unlock({', '.join(['%s'] * len(key))})", list(key))

    def _prune_runs(self):
        """Keep KEEP_RUNS per league, and per the shared steps, so a busy league can't erase a quiet one's history."""
        for league_id in SyncRun.objects.order_by().values_list("league_id", flat=True).distinct():
            stale = SyncRun.objects.filter(league_id=league_id).values_list("pk", flat=True)[KEEP_RUNS:]
            SyncRun.objects.filter(pk__in=list(stale)).delete()

    def _prune_explanations(self):
        """Old write-ups describe numbers that have changed, so they'd never be asked for again."""
        Explanation.objects.filter(created_at__lt=timezone.now() - KEEP_EXPLANATIONS).delete()
        ExplainUsage.objects.filter(day__lt=timezone.localdate() - timedelta(days=1)).delete()
