"""Invites, the only way in. Staff make them, and a link stops working once it expires or runs out of uses."""

import hashlib
import secrets
from datetime import timedelta

from django.contrib.auth.models import Permission
from django.db import IntegrityError, transaction
from django.db.models import F
from django.utils import timezone

from league.models import Invite, InviteUse, League

LASTS = timedelta(days=14)


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def prepare(invite: Invite) -> str:
    """Fill in a new invite and return its token, which is never stored."""
    token = secrets.token_urlsafe(32)
    invite.token_hash = _hash(token)
    invite.expires_at = timezone.now() + LASTS
    invite.max_uses = invite.league.team_count if invite.kind == Invite.LEAGUE else 1
    return token


def create(kind: str, league: League | None = None) -> tuple[Invite, str]:
    invite = Invite(kind=kind, league=league)
    token = prepare(invite)
    invite.save()
    return invite, token


def find(token: str) -> Invite | None:
    """Whatever its state: someone who already used a link can still follow it after it runs out."""
    return Invite.objects.select_related("league").filter(token_hash=_hash(token)).first()


def is_open(invite: Invite) -> bool:
    return invite.uses < invite.max_uses and invite.expires_at > timezone.now()


def already_in(user, invite: Invite) -> bool:
    if InviteUse.objects.filter(invite=invite, user=user).exists():
        return True
    return invite.league_id is not None and user.memberships.filter(league_id=invite.league_id).exists()


def redeem(invite: Invite, user) -> bool:
    """Spend one use on this user. False if the invite ran out, including to someone a moment ago."""
    try:
        with transaction.atomic():
            InviteUse.objects.create(invite=invite, user=user)
            # one UPDATE decides it, so two people can't both take the last use
            open_now = Invite.objects.filter(pk=invite.pk, uses__lt=F("max_uses"), expires_at__gt=timezone.now())
            if not open_now.update(uses=F("uses") + 1):
                raise _RanOut
            if invite.kind == Invite.APP:
                user.user_permissions.add(Permission.objects.get(content_type__app_label="league", codename="connect_league"))
    except _RanOut:
        return False
    except IntegrityError:
        return True  # a double submit: this user already holds it, and it only cost one use
    return True


class _RanOut(Exception):
    pass


def leagues_awaiting_a_team(user):
    """Leagues this user was invited to but hasn't picked a team in, so they aren't stranded if they wander off."""
    if not user.is_authenticated:
        return League.objects.none()
    return League.objects.filter(invites__redemptions__user=user).exclude(memberships__user=user).distinct()
