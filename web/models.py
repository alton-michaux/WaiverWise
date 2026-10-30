from django.db import models


class Explanation(models.Model):
    """Cached Claude explanation, keyed by a hash of the model, page kind, and data sent."""

    key = models.CharField(max_length=64, unique=True)
    kind = models.CharField(max_length=20)
    text = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)


class TradeDeals(models.Model):
    """Trades where both teams gain, for one pair of teams at one version of the league data.

    The search takes seconds per partner, which a slow server can't afford on every page load.
    `deals` holds everything needed to display them, so showing them doesn't touch the analysis.
    """

    team = models.ForeignKey("league.Team", on_delete=models.CASCADE, related_name="+")
    partner = models.ForeignKey("league.Team", on_delete=models.CASCADE, related_name="+")
    version = models.CharField(max_length=64)
    total = models.PositiveIntegerField()
    deals = models.JSONField(default=list)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["team", "partner", "version"], name="uniq_trade_deals")]
