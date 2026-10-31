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
    captured_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["player", "season", "week"], name="uniq_projection_snapshot")
        ]


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
