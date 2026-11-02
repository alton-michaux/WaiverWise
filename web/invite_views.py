"""Signing up through an invite, and resetting a lost password."""

from django.contrib.auth import login
from django.contrib.auth.decorators import login_not_required
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.views import PasswordResetConfirmView
from django.db import transaction
from django.shortcuts import redirect, render
from django.urls import reverse_lazy
from django.views.decorators.cache import never_cache
from django.views.decorators.debug import sensitive_post_parameters

from league import invites
from league.models import Invite


class _RanOut(Exception):
    pass


def _carry_on(invite: Invite):
    if invite.kind == Invite.LEAGUE:
        return redirect("connect_team", invite.league_id)
    return redirect("connect")


def _gone(request):
    return render(request, "web/invite_gone.html", status=404)


@login_not_required
@never_cache
@sensitive_post_parameters("password1", "password2")
def invite(request, token):
    """GET never spends a use, because chat apps fetch links to build their previews."""
    found = invites.find(token)
    if found is None:
        return _gone(request)
    signed_in = request.user.is_authenticated
    if signed_in and invites.already_in(request.user, found):
        return _carry_on(found)
    if not invites.is_open(found):
        return _gone(request)
    form = UserCreationForm(request.POST if request.method == "POST" and not signed_in else None)
    if request.method == "POST":
        if signed_in:
            return _carry_on(found) if invites.redeem(found, request.user) else _gone(request)
        if form.is_valid():
            try:
                with transaction.atomic():
                    user = form.save()
                    if not invites.redeem(found, user):
                        raise _RanOut
            except _RanOut:
                return _gone(request)
            login(request, user, backend="django.contrib.auth.backends.ModelBackend")
            return _carry_on(found)
    # always a 200: Django logs the path of any 400, and this one holds a working invite
    return render(request, "web/invite.html", {"invite": found, "form": form})


class ResetPassword(PasswordResetConfirmView):
    template_name = "web/password_reset.html"
    success_url = reverse_lazy("login")
