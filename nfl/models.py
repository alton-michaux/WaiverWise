from django.db import models

from league.models import Player


class Projection(models.Model):
    """External weekly projections, scored with the league's rules."""

    player = models.ForeignKey(Player, on_delete=models.CASCADE, related_name="projections")
    season = models.PositiveSmallIntegerField()
    week = models.PositiveSmallIntegerField()
    source = models.CharField(max_length=20)
    points = models.FloatField()
    opponent = models.CharField(max_length=10, blank=True)
    stats = models.JSONField(default=dict)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["player", "season", "week", "source"], name="uniq_projection"
            )
        ]


class ProjectionSnapshot(models.Model):
    """What we projected for a week, frozen at kickoff. ESPN drops past projections, so this can't be backfilled."""

    player = models.ForeignKey(Player, on_delete=models.CASCADE, related_name="projection_snapshots")
    season = models.PositiveSmallIntegerField()
    week = models.PositiveSmallIntegerField()
    espn = models.FloatField(null=True)
    sleeper = models.FloatField(null=True)
    mean = models.FloatField()
    sd = models.FloatField()
    opponent = models.CharField(max_length=10, blank=True)
    # saved rather than joined later: both change through the season and can't be rebuilt as of this week
    opp_rating = models.FloatField(null=True)
    implied_total = models.FloatField(null=True)
    captured_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["player", "season", "week"], name="uniq_projection_snapshot")
        ]


class GameLine(models.Model):
    """One team's side of a game from nflverse. Betting lines only exist about a week ahead."""

    season = models.PositiveSmallIntegerField()
    week = models.PositiveSmallIntegerField()
    team = models.CharField(max_length=10)  # ESPN code, to match ProGame
    is_home = models.BooleanField(null=True)  # None = neutral site
    spread = models.FloatField(null=True)  # relative to this team; negative = favored
    total = models.FloatField(null=True)
    implied_total = models.FloatField(null=True)
    rest_days = models.PositiveSmallIntegerField(null=True)
    roof = models.CharField(max_length=10, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["season", "week", "team"], name="uniq_game_line")]


class Usage(models.Model):
    """Weekly opportunity from nflverse: the leading indicator for waiver risers."""

    player = models.ForeignKey(Player, on_delete=models.CASCADE, related_name="usage")
    season = models.PositiveSmallIntegerField()
    week = models.PositiveSmallIntegerField()
    snap_pct = models.FloatField(null=True)
    targets = models.PositiveSmallIntegerField(default=0)
    target_share = models.FloatField(null=True)
    air_yards_share = models.FloatField(null=True)
    carries = models.PositiveSmallIntegerField(default=0)
    receptions = models.PositiveSmallIntegerField(default=0)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["player", "season", "week"], name="uniq_usage")
        ]
