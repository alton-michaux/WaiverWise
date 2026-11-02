"""Turning push notifications on and off for a device, and choosing what they cover."""

import base64
import binascii
import json

from django.http import HttpResponseBadRequest, JsonResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from notifications import delivery
from notifications.models import NotificationSettings, PushSubscription

MAX_DEVICES = 5
P256DH_BYTES, AUTH_BYTES = 65, 16


def _decodes_to(value, size: int) -> bool:
    if not isinstance(value, str) or len(value) > 200:
        return False
    try:
        return len(base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))) == size
    except (ValueError, binascii.Error):
        return False


def _body(request) -> dict:
    try:
        body = json.loads(request.body)
    except (ValueError, UnicodeDecodeError):
        return {}
    return body if isinstance(body, dict) else {}


@require_POST
def subscribe(request):
    body = _body(request)
    endpoint, keys = body.get("endpoint"), body.get("keys")
    if not isinstance(endpoint, str) or len(endpoint) > 1000 or not delivery.is_push_service(endpoint) or not isinstance(keys, dict):
        return HttpResponseBadRequest()
    p256dh, auth = keys.get("p256dh"), keys.get("auth")
    if not _decodes_to(p256dh, P256DH_BYTES) or not _decodes_to(auth, AUTH_BYTES):
        return HttpResponseBadRequest()
    # by endpoint alone: a shared device now belongs to whoever signed in on it last
    PushSubscription.objects.update_or_create(endpoint=endpoint, defaults={"user": request.user, "p256dh": p256dh, "auth": auth})
    stale = PushSubscription.objects.filter(user=request.user).order_by("-pk").values_list("pk", flat=True)[MAX_DEVICES:]
    PushSubscription.objects.filter(pk__in=list(stale)).delete()
    return JsonResponse({"ok": True})


@require_POST
def unsubscribe(request):
    endpoint = _body(request).get("endpoint")
    if isinstance(endpoint, str):
        PushSubscription.objects.filter(user=request.user, endpoint=endpoint).delete()
    return JsonResponse({"ok": True})


@require_POST
def test(request):
    return JsonResponse({"sent": delivery.send_test(request.user)})


def page(request):
    if request.method == "POST":
        NotificationSettings.objects.update_or_create(
            user=request.user,
            defaults={kind: kind in request.POST for kind in ("injuries", "waivers", "lineup")},
        )
        return redirect("notifications")
    preferences = NotificationSettings.objects.filter(user=request.user).first() or NotificationSettings()
    return render(request, "web/notifications.html", {"notify": preferences})
