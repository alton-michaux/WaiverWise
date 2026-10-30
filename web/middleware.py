from django.conf import settings
from django.db import connection
from django.http import JsonResponse
from django.shortcuts import render

from league.models import League, Team

HEALTH_PATH = "/health/"


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


class MissingLeagueDataMiddleware:
    """Show setup guidance instead of a 500 when the league hasn't been synced yet.

    Every page starts from League.objects.latest() and the user's Team, so an empty
    database (fresh deploy, first sync not run) or a wrong ESPN_MY_TEAM_ID lands here.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        return self.get_response(request)

    def process_exception(self, request, exception):
        if isinstance(exception, League.DoesNotExist):
            reason = "no-league"
        elif isinstance(exception, Team.DoesNotExist):
            reason = "no-team"
        else:
            return None
        return render(
            request,
            "web/no_data.html",
            {"reason": reason, "team_id": settings.ESPN_MY_TEAM_ID},
            status=503,
        )
