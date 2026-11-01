"""Connecting, joining and leaving leagues, and deleting your account."""

import re
from urllib.parse import unquote

from django import forms
from django.conf import settings
from django.contrib.auth import logout
from django.db import IntegrityError, transaction
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.debug import sensitive_post_parameters
from django.views.decorators.http import require_POST

from league import connect as connecting
from league.connect import ConnectError, NeedsCookies
from league.models import EspnCredential, League, Membership, SyncRun
from web import dispatch, services
from web.views import _remember

SWID_FORMAT = re.compile(r"^\{[0-9A-Fa-f]{8}(-[0-9A-Fa-f]{4}){3}-[0-9A-Fa-f]{12}\}$")


class ConnectForm(forms.Form):
    league = forms.CharField(max_length=300)
    espn_s2 = forms.CharField(max_length=2000, required=False, strip=True)
    swid = forms.CharField(max_length=100, required=False, strip=True)

    def clean_league(self):
        """The bare id, or the address of any ESPN page in the league."""
        text = self.cleaned_data["league"].strip()
        found = re.search(r"leagueId=(\d+)", text) or re.fullmatch(r"(\d+)", text)
        if not found or len(found.group(1)) > 18:
            raise forms.ValidationError("That doesn't look like a league ID.")
        return int(found.group(1))

    def clean_swid(self):
        swid = unquote(self.cleaned_data["swid"])
        if not swid:
            return ""
        if not swid.startswith("{"):
            swid = "{" + swid + "}"
        if not SWID_FORMAT.match(swid):
            raise forms.ValidationError("SWID should look like {XXXXXXXX-XXXX-XXXX-XXXX-XXXXXXXXXXXX}.")
        return swid


@sensitive_post_parameters("espn_s2", "swid")
def connect(request):
    if request.method != "POST":
        return render(request, "web/connect.html", {"league_value": request.GET.get("league", ""), "ask_cookies": False})
    form = ConnectForm(request.POST)
    context = {"league_value": request.POST.get("league", ""), "ask_cookies": bool(request.POST.get("ask_cookies"))}
    if not form.is_valid():
        context["error"] = next(iter(form.errors.values()))[0]
        return render(request, "web/connect.html", context, status=400)
    data = form.cleaned_data
    try:
        league = connecting.connect_league(request.user, data["league"], data["espn_s2"], data["swid"])
    except NeedsCookies as e:
        context.update(ask_cookies=True, error=str(e) if data["espn_s2"] or data["swid"] else "")
        return render(request, "web/connect.html", context, status=403 if data["espn_s2"] else 200)
    except ConnectError as e:
        context["error"] = str(e)
        return render(request, "web/connect.html", context, status=400)
    return redirect("connect_team", league.pk)


def _joinable_league(request, pk) -> League:
    """The same 404 for a league that doesn't exist and one this user can't open."""
    league = get_object_or_404(League, pk=pk)
    if not connecting.can_join(request.user, league):
        raise Http404
    return league


def _joined(request, league: League):
    if not league.has_data:
        dispatch.request_first_sync(league.pk)
    response = redirect("startsit")
    _remember(response, services.league_cookie(request.user), league.pk)
    return response


def connect_team(request, pk):
    league = _joinable_league(request, pk)
    if Membership.objects.filter(user=request.user, league=league).exists():
        return _joined(request, league)
    mine = connecting.own_team(request.user, league)
    if mine is not None:
        Membership.objects.create(user=request.user, league=league, team=mine)
        return _joined(request, league)
    error = ""
    if request.method == "POST":
        chosen = request.POST.get("team", "")
        team = connecting.open_teams(league).filter(espn_id=chosen).first() if chosen.isdigit() else None
        if team is None:
            error = "Someone else just claimed that team. Pick another."
        else:
            try:
                with transaction.atomic():
                    Membership.objects.create(user=request.user, league=league, team=team)
            except IntegrityError:
                error = "Someone else just claimed that team. Pick another."
            else:
                return _joined(request, league)
    return render(request, "web/connect_team.html", {"joining": league, "open_teams": connecting.open_teams(league), "error": error})


def sync_status(request):
    """HTMX partial for the waiting page: moves on as soon as the league's first sync lands."""
    if request.league is None:
        raise Http404
    if request.league.has_data:
        response = HttpResponse()
        response["HX-Redirect"] = "/startsit/"
        return response
    latest = SyncRun.objects.filter(league=request.league, step="league").first()
    return render(request, "web/_sync_status.html", {"failed": bool(latest and not latest.ok)})


def account(request):
    credentials = {c.league_id: c for c in EspnCredential.objects.filter(user=request.user)}
    rows = [{"league": m.league, "team": m.team, "credential": credentials.get(m.league_id)} for m in request.memberships]
    return render(request, "web/account.html", {"rows": rows, "error": request.GET.get("error", "")})


def _my_league(request, pk) -> League:
    membership = next((m for m in request.memberships if m.league_id == pk), None)
    if membership is None:
        raise Http404
    return membership.league


@require_POST
def disconnect(request, pk):
    connecting.disconnect(request.user, _my_league(request, pk))
    return redirect("account")


@require_POST
def leave(request, pk):
    connecting.leave_league(request.user, _my_league(request, pk))
    return redirect("account")


@require_POST
@sensitive_post_parameters("password")
def delete_account(request):
    user = request.user
    if user.is_superuser:
        return redirect("/account/?error=superuser")
    if not user.check_password(request.POST.get("password", "")):
        return redirect("/account/?error=password")
    logout(request)
    connecting.delete_account(user)
    return redirect("login")
