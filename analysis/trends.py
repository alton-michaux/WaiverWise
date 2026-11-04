"""Usage trends: recent opportunity vs earlier in the season."""

from dataclasses import dataclass
from statistics import mean

RECENT_GAMES = 3


@dataclass(frozen=True)
class UsageWeek:
    week: int
    snap_pct: float | None
    targets: int
    carries: int
    target_share: float | None = None
    air_yards_share: float | None = None


@dataclass(frozen=True)
class Trend:
    recent_opportunities: float  # targets + carries per game, last N games
    opportunity_change: float  # vs earlier games
    recent_snap_pct: float | None
    snap_pct_change: float | None
    recent_target_share: float | None = None
    recent_air_yards_share: float | None = None


def _avg(values):
    values = [v for v in values if v is not None]
    return mean(values) if values else None


def trend(weeks: list[UsageWeek]) -> Trend | None:
    if not weeks:
        return None
    weeks = sorted(weeks, key=lambda w: w.week)
    recent, earlier = weeks[-RECENT_GAMES:], weeks[:-RECENT_GAMES]
    opp = lambda ws: _avg([w.targets + w.carries for w in ws])  # noqa: E731
    snaps = lambda ws: _avg([w.snap_pct for w in ws])  # noqa: E731
    recent_opp, recent_snaps = opp(recent), snaps(recent)
    earlier_opp, earlier_snaps = opp(earlier), snaps(earlier)
    return Trend(
        recent_opportunities=round(recent_opp, 1),
        opportunity_change=round(recent_opp - earlier_opp, 1) if earlier_opp is not None else 0.0,
        recent_snap_pct=recent_snaps,
        snap_pct_change=(
            recent_snaps - earlier_snaps if recent_snaps is not None and earlier_snaps is not None else None
        ),
        recent_target_share=_avg([w.target_share for w in recent]),
        recent_air_yards_share=_avg([w.air_yards_share for w in recent]),
    )
