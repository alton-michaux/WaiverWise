from django.db import models


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

    class Meta:
        constraints = [models.UniqueConstraint(fields=["espn_id", "season"], name="uniq_league_season")]

    def __str__(self):
        return f"{self.name} ({self.season})"


class Team(models.Model):
    league = models.ForeignKey(League, on_delete=models.CASCADE, related_name="teams")
    espn_id = models.PositiveSmallIntegerField()
    name = models.CharField(max_length=200)
    abbrev = models.CharField(max_length=10)
    owners = models.JSONField(default=list)
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


class Player(models.Model):
    espn_id = models.BigIntegerField(unique=True)
    name = models.CharField(max_length=200)
    position = models.CharField(max_length=10)
    pro_team = models.CharField(max_length=10)
    eligible_slots = models.JSONField(default=list)
    injury_status = models.CharField(max_length=30, blank=True)
    percent_owned = models.FloatField(default=0)
    season_projection = models.FloatField(default=0)  # ESPN rest-of-season total (inferred)
    fa_status = models.CharField(max_length=10, blank=True)  # FREEAGENT / WAIVERS, blank if rostered
    waiver_clears = models.DateTimeField(null=True, blank=True)
    # crosswalk ids from nflverse ff_playerids
    sleeper_id = models.CharField(max_length=20, blank=True, db_index=True)
    gsis_id = models.CharField(max_length=20, blank=True, db_index=True)
    pfr_id = models.CharField(max_length=20, blank=True, db_index=True)

    def __str__(self):
        return f"{self.name} ({self.position}, {self.pro_team})"


class RosterEntry(models.Model):
    """Current roster snapshot; replaced wholesale on each sync."""

    team = models.ForeignKey(Team, on_delete=models.CASCADE, related_name="roster")
    player = models.ForeignKey(Player, on_delete=models.CASCADE, related_name="roster_entries")
    lineup_slot = models.CharField(max_length=10)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["team", "player"], name="uniq_roster_entry")]


class PlayerWeek(models.Model):
    """ESPN's per-week actual and projected points, already in league scoring."""

    player = models.ForeignKey(Player, on_delete=models.CASCADE, related_name="weeks")
    season = models.PositiveSmallIntegerField()
    week = models.PositiveSmallIntegerField()
    points = models.FloatField(null=True)
    projected_points = models.FloatField(null=True)
    breakdown = models.JSONField(default=dict)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["player", "season", "week"], name="uniq_player_week")
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
