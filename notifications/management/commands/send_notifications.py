from django.core.management.base import BaseCommand

from notifications import delivery


class Command(BaseCommand):
    help = "Send waiting notifications. Syncs do this too; this catches what quiet hours held back."

    def handle(self, *args, **opts):
        self.stdout.write(f"notifications: {delivery.deliver()} sent")
