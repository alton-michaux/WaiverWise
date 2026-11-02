from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import F, Max, QuerySet
from django.utils import timezone

from league import credentials


class League(models.Model):
    espn_id = models.BigIntegerField()
    season = models.PositiveSmallIntegerField()
    name = models.CharField(max_length=200)
    team_count = models.PositiveSmallIntegerField()
    current_week = models.PositiveSmallIntegerField()
    reg_season_weeks = models.PositiveSmallIntegerField()
    final_week = models.PositiveSmallIntegerField(default=17)  # last fantasy scoring period
    # {"QB": 1, "RB": 2, "RB/WR/TE": 1, "BE": 7, ...}
    lineup_slots = models.JSONField(default=dict)
    # {espn_stat_id: points}, e.g. {"53": 1.0} for full PPR
    scoring = models.JSONField(default=dict)
    synced_at = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(default=timezone.now)
    is_public = models.BooleanField(default=False)  # ESPN opens it without cookies
    has_data = models.BooleanField(default=True)  # False from connecting until the first sync lands

    class Meta:
        constraints = [models.UniqueConstraint(fields=["espn_id", "season"], name="uniq_league_season")]
        permissions = [("connect_league", "Can add a new league to WaiverWise")]

    def __str__(self):
        return f"{self.name} ({self.season})"


class Team(models.Model):
    league = models.ForeignKey(League, on_delete=models.CASCADE, related_name="teams")
    espn_id = models.PositiveSmallIntegerField()
    name = models.CharField(max_length=200)
    abbrev = models.CharField(max_length=10)
    owners = models.JSONField(default=list)
    owner_ids = models.JSONField(default=list)  # ESPN member ids, the same value as each owner's SWID cookie
    wins = models.PositiveSmallIntegerField(default=0)
    losses = models.PositiveSmallIntegerField(default=0)
    ties = models.PositiveSmallIntegerField(default=0)
    points_for = models.FloatField(default=0)
    points_against = models.FloatField(default=0)
    standing = models.PositiveSmallIntegerField(default=0)
    waiver_rank = models.PositiveSmallIntegerField(default=0)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["league", "espn_id"], name="uniq_team")]
        ordering = ["standing"]

    def __str__(self):
        return self.name


class Membership(models.Model):
    """A user's claim on a team, which is also what lets them see the league."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="memberships")
    league = models.ForeignKey(League, on_delete=models.CASCADE, related_name="memberships")
    team = models.OneToOneField(Team, on_delete=models.CASCADE, related_name="membership")
    last_seen = models.DateTimeField(default=timezone.now)  # at most a day old; leagues nobody opens stop syncing

    class Meta:
        constraints = [models.UniqueConstraint(fields=["user", "league"], name="uniq_membership")]

    def __str__(self):
        return f"{self.user} in {self.league}"

    def clean(self):
        # the database can't enforce this, and a mismatch would show one league's team inside another
        if self.team_id and self.league_id and self.team.league_id != self.league_id:
            raise ValidationError({"team": "This team isn't in that league."})

    def save(self, *args, **kwargs):
        self.clean()
        super().save(*args, **kwargs)


class EspnCredential(models.Model):
    """One member's ESPN cookies for a private league. They're a live login, so they stay encrypted."""

    OK, EXPIRED = "ok", "expired"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="espn_credentials")
    league = models.ForeignKey(League, on_delete=models.CASCADE, related_name="espn_credentials")
    encrypted_s2 = models.TextField()
    encrypted_swid = models.TextField()
    status = models.CharField(max_length=10, choices=[(OK, "Working"), (EXPIRED, "Expired")], default=OK)
    last_ok_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["user", "league"], name="uniq_espn_credential")]

    def __str__(self):
        return f"{self.user} in {self.league}: {self.status}"

    def cookies(self) -> tuple[str, str]:
        return credentials.decrypt(self.encrypted_s2), credentials.decrypt(self.encrypted_swid)

    def mark_ok(self):
        self.status, self.last_ok_at = self.OK, timezone.now()
        self.save(update_fields=["status", "last_ok_at"])

    def mark_expired(self):
        self.status = self.EXPIRED
        self.save(update_fields=["status"])


class Invite(models.Model):
    """A link that lets someone in. Only its hash is stored, so a copy of the database can't hand out working links."""

    APP, LEAGUE = "app", "league"

    token_hash = models.CharField(max_length=64, unique=True, editable=False)
    kind = models.CharField(
        max_length=10,
        choices=[(APP, "App: one person connects their own league"), (LEAGUE, "League: leaguemates join this league")],
    )
    league = models.ForeignKey(League, on_delete=models.CASCADE, null=True, blank=True, related_name="invites")
    expires_at = models.DateTimeField(editable=False)
    uses = models.PositiveSmallIntegerField(default=0, editable=False)
    max_uses = models.PositiveSmallIntegerField(editable=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(kind="league", league__isnull=False) | models.Q(kind="app", league__isnull=True),
                name="invite_league_matches_kind",
            ),
        ]

    def __str__(self):
        return f"{self.get_kind_display()}, {self.uses}/{self.max_uses} used"

    def clean(self):
        if self.kind == self.LEAGUE and self.league_id is None:
            raise ValidationError({"league": "A league invite needs a league."})
        if self.kind == self.APP and self.league_id is not None:
            raise ValidationError({"league": "An app invite isn't for one league. Leave this empty."})


class InviteUse(models.Model):
    """Who used an invite. For a league invite, this is what opens a private league to them."""

    invite = models.ForeignKey(Invite, on_delete=models.CASCADE, related_name="redemptions")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="invite_uses")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["invite", "user"], name="uniq_invite_use")]


class Player(models.Model):
    espn_id = models.BigIntegerField(unique=True)
    name = models.CharField(max_length=200)
    position = models.CharField(max_length=10)
    pro_team = models.CharField(max_length=10)
    eligible_slots = models.JSONField(default=list)
    injury_status = models.CharField(max_length=30, blank=True)
    percent_owned = models.FloatField(default=0)
    # crosswalk ids from nflverse ff_playerids
    sleeper_id = models.CharField(max_length=20, blank=True, db_index=True)
    gsis_id = models.CharField(max_length=20, blank=True, db_index=True)
    pfr_id = models.CharField(max_length=20, blank=True, db_index=True)

    def __str__(self):
        return f"{self.name} ({self.position}, {self.pro_team})"


class LeaguePlayer(models.Model):
    """A player as one league sees him. These rows also define the league's player pool."""

    league = models.ForeignKey(League, on_delete=models.CASCADE, related_name="league_players")
    player = models.ForeignKey(Player, on_delete=models.CASCADE, related_name="league_entries")
    season_projection = models.FloatField(default=0)  # ESPN rest-of-season total (inferred)
    fa_status = models.CharField(max_length=10, blank=True)  # FREEAGENT / WAIVERS, blank if rostered
    waiver_clears = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["league", "player"], name="uniq_league_player")]


def league_players(league: League) -> QuerySet[Player]:
    """The league's player pool, with its LeaguePlayer values available as attributes on each Player."""
    return (
        Player.objects.filter(league_entries__league=league)
        .annotate(
            season_projection=F("league_entries__season_projection"),
            fa_status=F("league_entries__fa_status"),
            waiver_clears=F("league_entries__waiver_clears"),
        )
        .order_by("pk")
    )


def active_leagues() -> QuerySet[League]:
    """Every league in the newest season, which is what syncs keep up to date."""
    season = League.objects.aggregate(Max("season"))["season__max"]
    return League.objects.filter(season=season).order_by("pk")


def leagues_in_use(cutoff) -> QuerySet[League]:
    """Active leagues someone has opened since `cutoff`, which are the ones worth syncing."""
    return active_leagues().filter(memberships__last_seen__gte=cutoff).distinct()


class RosterEntry(models.Model):
    """Current roster snapshot; replaced wholesale on each sync."""

    team = models.ForeignKey(Team, on_delete=models.CASCADE, related_name="roster")
    player = models.ForeignKey(Player, on_delete=models.CASCADE, related_name="roster_entries")
    lineup_slot = models.CharField(max_length=10)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["team", "player"], name="uniq_roster_entry")]


class PlayerWeek(models.Model):
    """ESPN's per-week actual and projected points, already in league scoring."""

    league = models.ForeignKey(League, on_delete=models.CASCADE, related_name="player_weeks")
    player = models.ForeignKey(Player, on_delete=models.CASCADE, related_name="weeks")
    season = models.PositiveSmallIntegerField()
    week = models.PositiveSmallIntegerField()
    points = models.FloatField(null=True)
    projected_points = models.FloatField(null=True)
    breakdown = models.JSONField(default=dict)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["league", "player", "season", "week"], name="uniq_player_week")
        ]


class Matchup(models.Model):
    league = models.ForeignKey(League, on_delete=models.CASCADE, related_name="matchups")
    week = models.PositiveSmallIntegerField()
    team_a = models.ForeignKey(Team, on_delete=models.CASCADE, related_name="+")
    team_b = models.ForeignKey(Team, on_delete=models.CASCADE, related_name="+")
    score_a = models.FloatField(default=0)
    score_b = models.FloatField(default=0)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["league", "week", "team_a"], name="uniq_matchup")
        ]


class ProGame(models.Model):
    """One row per NFL team per week; a missing week means a bye."""

    season = models.PositiveSmallIntegerField()
    week = models.PositiveSmallIntegerField()
    team = models.CharField(max_length=10)
    opponent = models.CharField(max_length=10)
    kickoff = models.DateTimeField(null=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["season", "week", "team"], name="uniq_pro_game")
        ]


class PendingMove(models.Model):
    """An accepted trade awaiting processing, or a pending waiver claim. Replaced on each sync."""

    TRADE, WAIVER = "trade", "waiver"

    league = models.ForeignKey(League, on_delete=models.CASCADE, related_name="pending_moves")
    espn_id = models.CharField(max_length=40)
    kind = models.CharField(max_length=10, choices=[(TRADE, "Accepted trade"), (WAIVER, "Waiver claim")])
    team_espn_id = models.PositiveSmallIntegerField()
    # [{"player_id", "from_team", "to_team", "type"}] with ESPN ids; team 0 = free agency
    items = models.JSONField(default=list)
    # whose cookies showed this waiver claim, and the only person who may see it; trades have none
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, null=True, blank=True, related_name="+")

    class Meta:
        constraints = [models.UniqueConstraint(fields=["league", "espn_id"], name="uniq_pending_move")]


class SyncRun(models.Model):
    """Outcome of one sync step, for the stale-data / expired-cookie banner."""

    # null = a step shared by every league, or a league sync that failed before the league existed
    league = models.ForeignKey(League, on_delete=models.CASCADE, null=True, blank=True, related_name="sync_runs")
    step = models.CharField(max_length=20)
    ok = models.BooleanField()
    error = models.TextField(blank=True)
    finished_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-finished_at", "-pk"]
        indexes = [models.Index(fields=["step", "-finished_at"])]
