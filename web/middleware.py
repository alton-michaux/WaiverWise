from django.db import connection
from django.http import JsonResponse

HEALTH_PATH = "/health/"


class HealthCheckMiddleware:
    """Answer platform health checks before host validation, HTTPS redirects, and login.

    Fly's checks hit the machine directly, so their Host header isn't the public hostname
    and Django would reject them as DisallowedHost.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.path != HEALTH_PATH:
            return self.get_response(request)
        with connection.cursor() as cur:
            cur.execute("SELECT 1")
        return JsonResponse({"status": "ok"})
