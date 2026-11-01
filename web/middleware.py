from datetime import timedelta

from django.db import connection
from django.http import JsonResponse
from django.shortcuts import render
from django.utils import timezone

from league.models import Membership
from web import services

HEALTH_PATH = "/health/"
SEEN_EVERY = timedelta(days=1)  # no need for a database write on every page


class HealthCheckMiddleware:
    """Answer platform health checks before host validation, HTTPS redirects, and login.

    Platform checks can hit the container directly, so their Host header isn't the public
    hostname and Django would reject them as DisallowedHost.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.path != HEALTH_PATH:
            return self.get_response(request)
        with connection.cursor() as cur:
            cur.execute("SELECT 1")
        return JsonResponse({"status": "ok"})


class LeagueScopeMiddleware:
    """Decide which league this request is about, from the user's memberships and nothing else.

    Someone with no league, or waiting on a first sync, gets an explanation instead of a 500.
    It's an error status so the service worker doesn't save it as the offline copy of the page.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.memberships = services.memberships_of(request.user)
        request.membership = services.active_membership(request, request.memberships)
        request.league = request.membership.league if request.membership else None
        if request.membership and timezone.now() - request.membership.last_seen > SEEN_EVERY:
            Membership.objects.filter(pk=request.membership.pk).update(last_seen=timezone.now())
        return self.get_response(request)

    def process_exception(self, request, exception):
        if isinstance(exception, services.LeagueNotReady):
            return render(request, "web/syncing.html", status=503)
        if isinstance(exception, Membership.DoesNotExist):
            return render(request, "web/no_data.html", status=403)
        return None
