from django.conf import settings
from django.contrib.auth.decorators import login_not_required
from django.http import Http404, JsonResponse
from django.templatetags.static import static
from django.views.decorators.cache import cache_control
from django.shortcuts import get_object_or_404, redirect, render

from analysis.startsit import MODES
from league.models import Player
from nfl.consensus import remaining_weeks
from web import deals, narrate, services


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


# value, label, what it does; order matches the buttons on the page
START_SIT_MODES = [
    ("mean", "Balanced", "Most projected points."),
    ("floor", "Safe floor", "Protects your worst case."),
    ("ceiling", "Big upside", "Chases ceiling games."),
]


def startsit(request):
    league = services.current_league()
    team = _team(request, league)
    mode = request.GET.get("mode", "mean")
    if mode not in MODES:
        mode = "mean"
    context = services.start_sit(team, mode)
    context["mode_options"] = START_SIT_MODES
    context["mode_label"], context["mode_note"] = next((label, note) for v, label, note in START_SIT_MODES if v == mode)
    return _page(request, "web/startsit.html", context)


def moves_badge(request):
    """HTMX partial: the Start/Sit tab's count of moves waiting, loaded after the page so no tab waits on it."""
    league = services.current_league()
    return render(request, "web/_moves_badge.html", {"count": services.move_count(_team(request, league))})


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
    # a verdict or an edit of one means the builder; otherwise lead with the best deals
    if request.GET.get("view") == "build" or "give" in request.GET or "get" in request.GET:
        return _page(request, "web/trades.html", {**_trade_context(request), "view": "build"})
    league = services.current_league()
    team = _team(request, league)
    context = {"league": league, "team": team, "weeks": remaining_weeks(league), "view": "best"}
    # already searched (the sync does this for your team): show it now rather than after another request
    stored = deals.best_deals(team, search=False)
    return _page(request, "web/trades.html", {**context, "best": stored if stored["done"] else None})


def best_deals(request):
    """HTMX partial: ranked deals, with progress while a slow server works through the league."""
    league = services.current_league()
    team = _team(request, league)
    partner_id = request.GET.get("partner")
    partner = get_object_or_404(league.teams.exclude(pk=team.pk), espn_id=partner_id) if partner_id else None
    return render(request, "web/_best_deals.html", {"best": deals.best_deals(team, partner)})


def player_sheet(request, pk):
    """HTMX partial: the detail behind a player's row, loaded when the sheet opens."""
    league = services.current_league()
    return render(request, "web/_player_sheet.html", services.player_sheet(league, get_object_or_404(Player, pk=pk)))


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
