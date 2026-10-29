from django.db import connection
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render

from analysis.startsit import MODES
from web import services


def health(request):
    with connection.cursor() as cur:
        cur.execute("SELECT 1")
    return JsonResponse({"status": "ok"})


def home(request):
    return redirect("startsit")


def startsit(request):
    league = services.current_league()
    team_id = request.GET.get("team")
    team = get_object_or_404(league.teams, espn_id=team_id) if team_id else services.my_team(league)
    mode = request.GET.get("mode", "mean")
    if mode not in MODES:
        mode = "mean"
    context = services.start_sit(team, mode)
    context["teams"] = league.teams.order_by("name")
    context["modes"] = MODES
    return render(request, "web/startsit.html", context)
