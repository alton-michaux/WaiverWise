import logging
import time

from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError

log = logging.getLogger(__name__)

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
        failed = []
        for name, command in STEPS.items():
            if only and name not in only:
                continue
            started = time.monotonic()
            try:
                call_command(command, stdout=self.stdout, stderr=self.stderr)
            except Exception:
                log.exception("sync step %s failed", name)
                failed.append(name)
                continue
            self.stdout.write(f"[{name}] done in {time.monotonic() - started:.1f}s")
        if failed:
            raise CommandError(f"failed steps: {', '.join(failed)}")
