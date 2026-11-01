"""Connecting a user to an ESPN league.

The rule that matters: knowing a league id must not open a stored private league. Every decision
here comes from what ESPN says right now about this user's own cookies, or about no cookies at all.
Nothing looks at what's already stored until ESPN has said yes, and no one else's cookies are ever used.
"""

from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from espn_api.requests.espn_requests import ESPNAccessDenied, ESPNInvalidLeague

from league import credentials, espn_client
from league.models import EspnCredential, League, Membership, PendingMove, Team
from league.support import check_supported


class ConnectError(Exception):
    """The message is safe to show to the person connecting."""


class NeedsCookies(ConnectError):
    pass


def connect_league(user, espn_id: int, espn_s2: str = "", swid: str = "") -> League:
    """Check access with ESPN, then store the league and, for a private one, this user's cookies."""
    season = settings.ESPN_SEASON
    try:
        info, is_public = _probe(espn_id, season, espn_s2, swid)
    except ESPNInvalidLeague:
        raise ConnectError("ESPN doesn't have a league with that ID this season.")
    except espn_client.EspnUnavailable:
        raise ConnectError("Couldn't reach ESPN just now. Try again in a minute.")
    if problems := check_supported(info):
        raise ConnectError(" ".join(problems))
    with transaction.atomic():
        league = _store(info, is_public)
        if not is_public:
            _store_cookies(user, league, espn_s2, swid)
    return league


def _probe(espn_id: int, season: int, espn_s2: str, swid: str) -> tuple[espn_client.LeagueInfo, bool]:
    try:
        return espn_client.probe_league(espn_id, season), True
    except ESPNAccessDenied:
        pass
    if not (espn_s2 and swid):
        raise NeedsCookies("This league is private, so ESPN needs your login cookies to open it.")
    try:
        return espn_client.probe_league(espn_id, season, espn_s2, swid), False
    except ESPNAccessDenied:
        raise NeedsCookies("ESPN didn't accept those cookies for this league. Check that you copied both, from an account in the league.")


def _store(info: espn_client.LeagueInfo, is_public: bool) -> League:
    league, created = League.objects.get_or_create(
        espn_id=info.espn_id,
        season=info.season,
        defaults=dict(
            name=info.name,
            team_count=info.team_count,
            current_week=info.current_week,
            reg_season_weeks=info.reg_season_weeks,
            final_week=info.final_week,
            lineup_slots=info.lineup_slots,
            scoring=info.scoring,
            is_public=is_public,
            has_data=False,  # teams only, until the first sync finishes
        ),
    )
    if not created and league.is_public != is_public:
        league.is_public = is_public
        league.save(update_fields=["is_public"])
    for team in info.teams:
        Team.objects.update_or_create(
            league=league,
            espn_id=team.espn_id,
            defaults=dict(name=team.name, abbrev=team.abbrev, owner_ids=team.owner_ids),
        )
    return league


def _store_cookies(user, league: League, espn_s2: str, swid: str) -> None:
    EspnCredential.objects.update_or_create(
        user=user,
        league=league,
        defaults=dict(
            encrypted_s2=credentials.encrypt(espn_s2),
            encrypted_swid=credentials.encrypt(swid),
            status=EspnCredential.OK,
            last_ok_at=timezone.now(),
        ),
    )


def can_join(user, league: League) -> bool:
    """A public league needs nothing. A private one needs cookies ESPN accepted from this user."""
    return league.is_public or EspnCredential.objects.filter(user=user, league=league).exists()


def open_teams(league: League):
    return league.teams.filter(membership__isnull=True).order_by("name")


def own_team(user, league: League) -> Team | None:
    """The unclaimed team ESPN says belongs to this user's cookies, if there is exactly one."""
    credential = EspnCredential.objects.filter(user=user, league=league).first()
    if credential is None:
        return None
    _, swid = credential.cookies()
    mine = [t for t in open_teams(league) if swid.casefold() in (o.casefold() for o in t.owner_ids)]
    return mine[0] if len(mine) == 1 else None


def disconnect(user, league: League) -> None:
    """Forget this user's cookies, and the waiver claims only they could see."""
    with transaction.atomic():
        EspnCredential.objects.filter(user=user, league=league).delete()
        PendingMove.objects.filter(league=league, owner=user).delete()


def leave_league(user, league: League) -> None:
    with transaction.atomic():
        disconnect(user, league)
        Membership.objects.filter(user=user, league=league).delete()
        delete_if_empty([league.pk])


def delete_account(user) -> None:
    with transaction.atomic():
        leagues = list(Membership.objects.filter(user=user).values_list("league_id", flat=True))
        user.delete()
        delete_if_empty(leagues)


def delete_if_empty(league_ids: list[int]) -> None:
    League.objects.filter(Q(pk__in=league_ids), memberships__isnull=True).delete()
