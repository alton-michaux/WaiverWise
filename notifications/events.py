"""Deciding what's worth telling someone. Sending is delivery.py's job."""

import hashlib
import logging
from datetime import timedelta

from django.conf import settings
from django.urls import reverse
from django.utils import timezone

from analysis.lineup import NON_STARTING
from league.models import League, PendingMove, Player, RosterEntry, Team
from nfl.consensus import game_started, week_kickoffs
from notifications.models import Notification, NotificationSettings, PushSubscription
from web import services

log = logging.getLogger(__name__)

# QUESTIONABLE is left out: it's on most injury reports by Thursday and usually clears by Sunday
ALERT_STATUSES = {"OUT": "out", "DOUBTFUL": "doubtful", "INJURY_RESERVE": "on IR", "SUSPENSION": "suspended"}
# after the 11:45 ET run for 1:00 games, so the suggestion has seen the inactives and fresh projections
REMIND_BEFORE = timedelta(minutes=80)
SMALLEST_GAIN = 1.0  # projected points; less than this isn't worth buzzing someone's phone
WAIVER_NEWS_LASTS = timedelta(days=3)


def recipients(kind: str) -> set[int]:
    """Users with a device to reach who haven't turned this kind off."""
    subscribed = set(PushSubscription.objects.values_list("user_id", flat=True))
    return subscribed - set(NotificationSettings.objects.filter(**{kind: False}).values_list("user_id", flat=True))


def _my_team(league: League) -> Team | None:
    return league.teams.filter(espn_id=settings.ESPN_MY_TEAM_ID).first()


def _record(users: set[int], league: League, kind: str, key: str, title: str, body: str, expires_at) -> int:
    """How many people this was news to."""
    created = 0
    for user_id in users:
        _, new = Notification.objects.get_or_create(
            user_id=user_id,
            key=key,
            defaults={
                "league": league, "kind": kind, "title": title[:200], "body": body[:500], "expires_at": expires_at,
                "url": reverse("startsit"),
            },
        )
        created += new
    return created


def injured_starters(league: League) -> int:
    """A starter who can't play and whose game hasn't started. The key means one alert per status per week."""
    users, team = recipients(Notification.INJURIES), _my_team(league)
    if not users or team is None:
        return 0
    week = league.current_week
    kickoffs = week_kickoffs(league.season, week)
    entries = (
        RosterEntry.objects.filter(team=team, player__injury_status__in=ALERT_STATUSES)
        .exclude(lineup_slot__in=NON_STARTING)
        .select_related("player")
    )
    created = 0
    for entry in entries:
        player = entry.player
        kickoff = kickoffs.get(player.pro_team)
        if kickoff is None or game_started(kickoff):  # on bye, or too late to do anything
            continue
        created += _record(
            users, league, Notification.INJURIES,
            f"injury:{league.pk}:{week}:{player.pk}:{player.injury_status}",
            f"{player.name} is {ALERT_STATUSES[player.injury_status]}",
            f"Still in your {team.name} lineup for week {week}.",
            kickoff,
        )
    return created


def waiver_results(league: League, gone: list[PendingMove]) -> int:
    """My claims that just left ESPN's pending list, judged by where the player ended up.

    A claim disappears whether it won, lost or was cancelled. A player nobody rostered says nothing about which,
    so those stay silent rather than guess.
    """
    users, team = recipients(Notification.WAIVERS), _my_team(league)
    if team is None:
        return 0
    # a commissioner's cookies can show other teams' claims, and those aren't news here
    gone = [m for m in gone if m.team_espn_id == team.espn_id]
    if not users or not gone:
        return 0
    wanted = {i["player_id"] for move in gone for i in move.items if i["to_team"] == team.espn_id}
    names = dict(Player.objects.filter(espn_id__in=wanted).values_list("espn_id", "name"))
    holders = {e.player.espn_id: e.team for e in RosterEntry.objects.filter(team__league=league, player__espn_id__in=wanted).select_related("player", "team")}
    got = [names[p] for p, holder in holders.items() if holder == team]
    lost = [f"{names[p]} went to {holder.name}." for p, holder in holders.items() if holder != team]
    if not got and not lost:
        return 0
    parts = ([f"You got {', '.join(sorted(got))}."] if got else []) + sorted(lost)
    digest = hashlib.sha256(":".join(sorted(m.espn_id for m in gone)).encode()).hexdigest()[:16]
    return _record(
        users, league, Notification.WAIVERS, f"waivers:{league.pk}:{digest}",
        "Waiver results", " ".join(parts), timezone.now() + WAIVER_NEWS_LASTS,
    )


def lineup_reminder(league: League) -> int:
    """Nudge only when there's a suggested move worth making and its first game is close. Silence means you're set."""
    now = timezone.now()
    week = league.current_week
    kickoffs = week_kickoffs(league.season, week)
    if not any(now < k <= now + REMIND_BEFORE for k in kickoffs.values()):
        return 0  # the usual case, and it skips the projections work
    users, team = recipients(Notification.LINEUP), _my_team(league)
    if not users or team is None:
        return 0
    moves, gain = services.suggested_moves(team)
    if not moves or gain < SMALLEST_GAIN:
        return 0
    deadline = services.move_deadline(moves, kickoffs)
    if deadline is None or deadline > now + REMIND_BEFORE:
        return 0
    start, sit = moves[0]
    first = f"Start {start.player.name}" + (f" over {sit.player.name}" if sit else "")
    more = f", plus {len(moves) - 1} more" if len(moves) > 1 else ""
    minutes = max(1, round((deadline - now).total_seconds() / 60))
    return _record(
        users, league, Notification.LINEUP,
        f"lineup:{league.pk}:{week}:{int(deadline.timestamp())}",
        f"Lineup check: {minutes} min to kickoff",
        f"{first}{more} for +{gain:.1f} projected.",
        deadline,
    )


def check_league(league: League) -> int:
    return injured_starters(league) + lineup_reminder(league)
