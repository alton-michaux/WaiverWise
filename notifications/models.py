from django.conf import settings
from django.db import models


class PushSubscription(models.Model):
    """One device's address at its browser's push service. The endpoint is a secret, so it never reaches logs."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="push_subscriptions")
    endpoint = models.URLField(max_length=1000, unique=True)
    p256dh = models.CharField(max_length=200)
    auth = models.CharField(max_length=100)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.user}'s device {self.pk}"


class NotificationSettings(models.Model):
    """Which kinds someone wants, on every device. No row means all of them."""

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="notification_settings")
    injuries = models.BooleanField(default=True)
    waivers = models.BooleanField(default=True)
    lineup = models.BooleanField(default=True)


class Notification(models.Model):
    """Something to tell someone, and the record that they've been told.

    A row waits here through quiet hours, and its key stops the next sync saying the same thing again.
    """

    INJURIES, WAIVERS, LINEUP = "injuries", "waivers", "lineup"  # also the NotificationSettings field names

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="notifications")
    league = models.ForeignKey("league.League", on_delete=models.CASCADE, related_name="+")
    kind = models.CharField(max_length=10)
    key = models.CharField(max_length=120)
    title = models.CharField(max_length=200)
    body = models.CharField(max_length=500)
    url = models.CharField(max_length=200)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()  # no use after this, so it's dropped rather than sent late
    sent_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["user", "key"], name="uniq_notification")]
        indexes = [models.Index(fields=["sent_at"])]
