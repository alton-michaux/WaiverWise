from django.conf import settings
from django.contrib.auth.decorators import login_not_required
from django.http import Http404, JsonResponse
from django.templatetags.static import static
from django.views.decorators.cache import cache_control
from django.views.decorators.http import require_POST
from django.shortcuts import get_object_or_404, redirect, render

from analysis.startsit import MODES
from league.models import Membership, league_players
from nfl.consensus import remaining_weeks
from web import deals, narrate, services


def home(request):
    return redirect("startsit")


def _membership(request) -> Membership:
    """Every league page starts here; LeagueScopeMiddleware explains the miss to the user."""
    if request.membership is None:
        raise Membership.DoesNotExist
    return request.membership


def _team(request):
    membership = _membership(request)
    team_id = request.GET.get("team")
    if team_id:
        if not team_id.isdigit():
            raise Http404
        return get_object_or_404(membership.league.teams, espn_id=team_id)
    return services.remembered_team(request, membership) or membership.team


def _remember(response, name, value):
    """A cookie, not the session: SESSION_SAVE_EVERY_REQUEST means a trade finder request
    still running when you switch would save its older session over the new choice.
    """
    response.set_cookie(
        name,
        value,
        max_age=settings.SESSION_COOKIE_AGE,
        secure=settings.SESSION_COOKIE_SECURE,
        httponly=True,
        samesite="Lax",
    )


def _page(request, template, context):
    """Render a full page, remembering a team picked with ?team= for the other tabs."""
    response = render(request, template, context)
    if "team" in request.GET:
        _remember(response, services.team_cookie(request.membership), context["team"].espn_id)
    return response


@require_POST
def switch_league(request):
    chosen = request.POST.get("league")
    membership = next((m for m in request.memberships if str(m.league_id) == chosen), None)
    if membership is None:
        raise Http404
    response = redirect("startsit")
    _remember(response, services.league_cookie(request.user), membership.league_id)
    return response


# value, label, what it does; order matches the buttons on the page
START_SIT_MODES = [
    ("mean", "Balanced", "Most projected points."),
    ("floor", "Safe floor", "Protects your worst case."),
    ("ceiling", "Big upside", "Chases ceiling games."),
]


def startsit(request):
    team = _team(request)
    mode = request.GET.get("mode", "mean")
    if mode not in MODES:
        mode = "mean"
    context = services.start_sit(team, mode)
    context["mode_options"] = START_SIT_MODES
    context["mode_label"], context["mode_note"] = next((label, note) for v, label, note in START_SIT_MODES if v == mode)
    return _page(request, "web/startsit.html", context)


def moves_badge(request):
    """HTMX partial: the Start/Sit tab's count of moves waiting, loaded after the page so no tab waits on it."""
    return render(request, "web/_moves_badge.html", {"count": services.move_count(_team(request))})


WAIVER_POSITIONS = ["QB", "RB", "WR", "TE", "K", "D/ST"]


def waivers(request):
    team = _team(request)
    position = request.GET.get("position") or None
    if position not in WAIVER_POSITIONS:
        position = None
    context = services.waivers(team, position)
    context["positions"] = WAIVER_POSITIONS
    return _page(request, "web/waivers.html", context)


def _trade_context(request) -> dict:
    team = _team(request)
    league = team.league
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
    team = _team(request)
    league = team.league
    context = {"league": league, "team": team, "weeks": remaining_weeks(league), "view": "best"}
    # already searched (the nightly sync does this for claimed teams): show it now rather than after another request
    stored = deals.best_deals(team, search=False)
    return _page(request, "web/trades.html", {**context, "best": stored if stored["done"] else None})


def best_deals(request):
    """HTMX partial: ranked deals, with progress while a slow server works through the league."""
    team = _team(request)
    partner_id = request.GET.get("partner")
    partner = get_object_or_404(team.league.teams.exclude(pk=team.pk), espn_id=partner_id) if partner_id else None
    return render(request, "web/_best_deals.html", {"best": deals.best_deals(team, partner)})


def player_sheet(request, pk):
    """HTMX partial: the detail behind a player's row, loaded when the sheet opens."""
    league = _membership(request).league
    player = get_object_or_404(league_players(league), pk=pk)  # Player rows are shared, so stay inside this league's pool
    return render(request, "web/_player_sheet.html", services.player_sheet(league, player))


def _startsit_payload(request):
    mode = request.GET.get("mode", "mean")
    return narrate.startsit_payload(services.start_sit(_team(request), mode if mode in MODES else "mean"))


def _waivers_payload(request):
    position = request.GET.get("position") or None
    return narrate.waivers_payload(
        services.waivers(_team(request), position if position in WAIVER_POSITIONS else None)
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
        text, error = narrate.explain(kind, EXPLAINERS[kind](request), request.league), None
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
