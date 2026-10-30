from django.conf import settings
from django.contrib.auth.decorators import login_not_required
from django.http import Http404, JsonResponse
from django.templatetags.static import static
from django.views.decorators.cache import cache_control
from django.shortcuts import get_object_or_404, redirect, render

from analysis.startsit import MODES
from web import narrate, services


def home(request):
    return redirect("startsit")


def _team(request, league):
    team_id = request.GET.get("team")
    if team_id:
        if not team_id.isdigit():
            raise Http404
        return get_object_or_404(league.teams, espn_id=team_id)
    return services.remembered_team(request, league) or services.my_team(league)


def _page(request, template, context):
    """Render a full page, remembering a team picked with ?team= for the other tabs.

    A cookie, not the session: SESSION_SAVE_EVERY_REQUEST means a trade finder request
    still running when you switch teams would save its older session over the new choice.
    """
    response = render(request, template, context)
    if "team" in request.GET:
        response.set_cookie(
            services.TEAM_COOKIE,
            context["team"].espn_id,
            max_age=settings.SESSION_COOKIE_AGE,
            secure=settings.SESSION_COOKIE_SECURE,
            httponly=True,
            samesite="Lax",
        )
    return response


def startsit(request):
    league = services.current_league()
    team = _team(request, league)
    mode = request.GET.get("mode", "mean")
    if mode not in MODES:
        mode = "mean"
    context = services.start_sit(team, mode)
    context["modes"] = MODES
    return _page(request, "web/startsit.html", context)


WAIVER_POSITIONS = ["QB", "RB", "WR", "TE", "K", "D/ST"]


def waivers(request):
    league = services.current_league()
    team = _team(request, league)
    position = request.GET.get("position") or None
    if position not in WAIVER_POSITIONS:
        position = None
    context = services.waivers(team, position)
    context["positions"] = WAIVER_POSITIONS
    return _page(request, "web/waivers.html", context)


FINDER_LIMIT = 5


def _trade_context(request) -> dict:
    league = services.current_league()
    team = _team(request, league)
    others = league.teams.exclude(pk=team.pk).order_by("name")
    partner_id = request.GET.get("partner")
    partner = get_object_or_404(others, espn_id=partner_id) if partner_id else others.first()
    ctx = services.TradeContext(league)

    give = [int(i) for i in request.GET.getlist("give") if i.isdigit()]
    get = [int(i) for i in request.GET.getlist("get") if i.isdigit()]
    mine = {r.player.pk for r in services.roster_choices(ctx, team) if not r.pending}
    theirs = {r.player.pk for r in services.roster_choices(ctx, partner) if not r.pending}
    give = [i for i in give if i in mine]
    get = [i for i in get if i in theirs]
    result = services.describe(ctx, ctx.evaluate(team, partner, give, get)) if give and get else None
    return {
        "league": league,
        "team": team,
        "partner": partner,
        "others": others,
        "my_roster": services.roster_choices(ctx, team),
        "their_roster": services.roster_choices(ctx, partner),
        "give": give,
        "get": get,
        "result": result,
        "weeks": ctx.weeks,
    }


def trades(request):
    return _page(request, "web/trades.html", _trade_context(request))


def trade_finder(request):
    """HTMX partial: best mutually beneficial trades with one partner."""
    league = services.current_league()
    team = _team(request, league)
    partner = get_object_or_404(league.teams, espn_id=request.GET.get("partner"))
    ctx = services.TradeContext(league)
    found = ctx.find(team, partner)
    return render(
        request,
        "web/_trade_finder.html",
        {
            "team": team,
            "partner": partner,
            "trades": [services.describe(ctx, t) for t in found[:FINDER_LIMIT]],
            "total": len(found),
        },
    )


def _startsit_payload(request):
    league = services.current_league()
    mode = request.GET.get("mode", "mean")
    return narrate.startsit_payload(services.start_sit(_team(request, league), mode if mode in MODES else "mean"))


def _waivers_payload(request):
    league = services.current_league()
    position = request.GET.get("position") or None
    return narrate.waivers_payload(
        services.waivers(_team(request, league), position if position in WAIVER_POSITIONS else None)
    )


def _trade_payload(request):
    ctx = _trade_context(request)
    if not ctx["result"]:
        raise Http404("pick players on both sides first")
    return narrate.trade_payload(ctx)


EXPLAINERS = {"startsit": _startsit_payload, "waivers": _waivers_payload, "trade": _trade_payload}


def explain(request, kind):
    """HTMX partial: Claude's explanation of the page's current numbers (same query params as the page)."""
    if kind not in EXPLAINERS:
        raise Http404
    try:
        text, error = narrate.explain(kind, EXPLAINERS[kind](request)), None
    except narrate.NarrationError as e:
        text, error = None, str(e)
    return render(request, "web/_explanation.html", {"text": text, "error": error})


# --- PWA ---


@login_not_required
def manifest(request):
    """Fetched by browsers without cookies, so it must not require login."""
    icon = lambda name, size, purpose="any": {  # noqa: E731
        "src": static(f"icons/{name}"),
        "sizes": size,
        "type": "image/png",
        "purpose": purpose,
    }
    return JsonResponse(
        {
            "id": "/",
            "name": "WaiverWise",
            "short_name": "WaiverWise",
            "description": "Start/sit, waiver, and trade help for your fantasy football league.",
            "start_url": "/",
            "scope": "/",
            "display": "standalone",
            "background_color": "#ffffff",
            "theme_color": "#0b2a42",
            "icons": [
                icon("icon-192.png", "192x192"),
                icon("icon-512.png", "512x512"),
                icon("icon-maskable-512.png", "512x512", "maskable"),
            ],
        },
        content_type="application/manifest+json",
    )


@login_not_required
@cache_control(no_cache=True)
def service_worker(request):
    # served from the site root so its scope covers every page
    return render(
        request,
        "web/sw.js",
        {"version": settings.APP_VERSION},
        content_type="application/javascript",
    )


@login_not_required
def offline(request):
    return render(request, "web/offline.html")
