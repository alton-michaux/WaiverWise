"""Sending push messages. Runs in public Actions logs, so nothing here logs an endpoint, a name or an error's text."""

import json
import logging
from datetime import timedelta
from urllib.parse import urlsplit

from django.conf import settings
from django.utils import timezone
from pywebpush import WebPushException, webpush

from notifications.models import Notification, NotificationSettings, PushSubscription

log = logging.getLogger(__name__)

# We post to whatever address a browser gives us, so it has to be a real push service and not, say, an internal host
PUSH_SERVICES = ("fcm.googleapis.com", "push.services.mozilla.com", "push.apple.com", "notify.windows.com")
QUIET_FROM, QUIET_UNTIL = 22, 8  # hours in settings.TIME_ZONE; everyone's on one clock until we store time zones
KEEP_SENT = timedelta(days=30)  # long enough that a key can't come round again
TIMEOUT_SECONDS = 10
SENT, GONE, FAILED = "sent", "gone", "failed"


def enabled() -> bool:
    return bool(settings.VAPID_PUBLIC_KEY and settings.VAPID_PRIVATE_KEY and settings.VAPID_SUBJECT)


def is_push_service(endpoint: str) -> bool:
    parts = urlsplit(endpoint)
    host = parts.hostname or ""
    try:
        port = parts.port
    except ValueError:
        return False
    return (
        parts.scheme == "https" and port in (None, 443) and not parts.username
        and any(host == s or host.endswith(f".{s}") for s in PUSH_SERVICES)
    )


def quiet_hours() -> bool:
    hour = timezone.localtime().hour
    return hour >= QUIET_FROM or hour < QUIET_UNTIL


def send(subscription: PushSubscription, message: dict, ttl: int) -> str:
    try:
        webpush(
            subscription_info={"endpoint": subscription.endpoint, "keys": {"p256dh": subscription.p256dh, "auth": subscription.auth}},
            data=json.dumps(message),
            vapid_private_key=settings.VAPID_PRIVATE_KEY,
            vapid_claims={"sub": settings.VAPID_SUBJECT},
            ttl=ttl,
            timeout=TIMEOUT_SECONDS,
            headers={"Urgency": "high"},  # a phone saving battery holds back anything lower
        )
    except WebPushException as e:
        status = e.response.status_code if e.response is not None else None
        if status in (404, 410):  # the browser dropped it, or that person signed out on this device
            subscription.delete()
            return GONE
        log.warning("push service answered %s", status)
    except Exception as e:
        log.warning("couldn't send a push: %s", type(e).__name__)
    else:
        return SENT
    return FAILED


def _prune(now) -> None:
    Notification.objects.filter(sent_at__isnull=True, expires_at__lte=now).delete()
    Notification.objects.filter(sent_at__lt=now - KEEP_SENT).delete()


def deliver() -> int:
    """Send what's waiting, unless it's the middle of the night. Returns how many went out."""
    now = timezone.now()
    _prune(now)
    if not enabled() or quiet_hours():
        return 0
    pending = list(Notification.objects.filter(sent_at__isnull=True).order_by("pk"))
    if not pending:
        return 0
    users = {n.user_id for n in pending}
    preferences = {s.user_id: s for s in NotificationSettings.objects.filter(user_id__in=users)}
    devices: dict[int, list[PushSubscription]] = {}
    for subscription in PushSubscription.objects.filter(user_id__in=users):
        devices.setdefault(subscription.user_id, []).append(subscription)

    delivered = 0
    for n in pending:
        # they may have changed their mind since this was queued
        if n.user_id in preferences and not getattr(preferences[n.user_id], n.kind):
            n.delete()
            continue
        # taking it first means two overlapping syncs can't both send it
        if not Notification.objects.filter(pk=n.pk, sent_at__isnull=True).update(sent_at=now):
            continue
        message = {"title": n.title, "body": n.body, "url": n.url, "tag": n.key}
        ttl = int((n.expires_at - now).total_seconds())
        outcomes = []
        for subscription in list(devices.get(n.user_id, [])):
            outcome = send(subscription, message, ttl)
            outcomes.append(outcome)
            if outcome == GONE:
                devices[n.user_id].remove(subscription)
        if SENT in outcomes:
            delivered += 1
        elif FAILED in outcomes:  # try again next sync, until it expires
            Notification.objects.filter(pk=n.pk).update(sent_at=None)
    return delivered


def send_test(user) -> int:
    """To every device this user has, whatever the hour. Returns how many took it."""
    message = {"title": "WaiverWise", "body": "Notifications are working.", "url": "/notifications/", "tag": "test"}
    return sum(send(s, message, 300) == SENT for s in PushSubscription.objects.filter(user=user))
