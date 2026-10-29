"""Glue between stored league data and the pure analysis functions."""

from collections import defaultdict
from dataclasses import dataclass

from django.conf import settings
from django.db.models import Q

from analysis.lineup import NON_STARTING
from analysis.projections import Projection
from analysis.startsit import Recommendation, RosterSpot, recommend
from analysis.trends import Trend, UsageWeek, trend
from analysis.value import PlayerValue, vorp
from analysis.waivers import WeeklyPlayer, evaluate, streamers
from league.models import League, Matchup, Player, ProGame, RosterEntry, Team
from nfl.consensus import game_started, remaining_weeks, week_kickoffs, weekly_projections
from nfl.models import Usage

# display order for lineup slots
SLOT_ORDER = ["QB", "RB", "WR", "TE", "RB/WR/TE", "OP", "D/ST", "K", "BE", "IR"]


@dataclass
class PlayerRow:
    player: Player
    slot: str
    projection: Projection | None
    opponent: str
    locked: bool


def current_league() -> League:
    return League.objects.latest("season")


def my_team(league: League) -> Team:
    return league.teams.get(espn_id=settings.ESPN_MY_TEAM_ID)


def opponent_of(team: Team, week: int) -> Team | None:
    m = Matchup.objects.filter(league=team.league, week=week).filter(Q(team_a=team) | Q(team_b=team)).first()
    if not m:
        return None
    return m.team_b if m.team_a_id == team.pk else m.team_a


def roster_spots(team: Team, week: int) -> tuple[list[RosterSpot], dict[int, PlayerRow]]:
    league = team.league
    entries = list(team.roster.select_related("player"))
    players = [e.player for e in entries]
    proj = weekly_projections(league, players, range(week, week + 1))
    kickoffs = week_kickoffs(league.season, week)
    opponents = dict(ProGame.objects.filter(season=league.season, week=week).values_list("team", "opponent"))

    spots, rows = [], {}
    for e in entries:
        p = e.player
        locked = game_started(kickoffs.get(p.pro_team))
        projection = proj[(p.pk, week)]
        spots.append(RosterSpot(p.pk, tuple(p.eligible_slots), e.lineup_slot, projection, locked))
        rows[p.pk] = PlayerRow(p, e.lineup_slot, projection, opponents.get(p.pro_team, "BYE"), locked)
    return spots, rows


def start_sit(team: Team, mode: str = "mean") -> dict:
    league = team.league
    week = league.current_week
    spots, rows = roster_spots(team, week)
    opponent = opponent_of(team, week)
    opp_spots = roster_spots(opponent, week)[0] if opponent else None
    rec: Recommendation = recommend(spots, league.lineup_slots, opp_spots, mode)

    best_slot = {pid: slot for slot, ids in rec.best.items() for pid in ids}
    for spot in spots:
        # only surface real bench/start changes, not shuffles between starting slots
        if spot.current_slot == "IR" or (
            spot.current_slot not in NON_STARTING and best_slot.get(spot.id) not in NON_STARTING
        ):
            best_slot[spot.id] = spot.current_slot

    def ordered(lineup_slot_of):
        return sorted(
            rows.values(),
            key=lambda r: (
                SLOT_ORDER.index(lineup_slot_of(r)) if lineup_slot_of(r) in SLOT_ORDER else 99,
                -(r.projection.mean if r.projection else 0),
            ),
        )

    return {
        "league": league,
        "team": team,
        "opponent": opponent,
        "week": week,
        "mode": mode,
        "rec": rec,
        "rows": [(r, best_slot.get(r.player.pk, "BE")) for r in ordered(lambda r: r.slot)],
        "moves": [(rows[i], rows[o]) for i, o in rec.moves],
        "non_starting": NON_STARTING,
    }


TREND_POSITIONS = {"QB", "RB", "WR", "TE"}


@dataclass
class WaiverRow:
    player: Player
    week_proj: Projection | None
    ros_points: float
    vorp: float
    next_week_gain: float
    ros_gain: float
    trend: Trend | None


def waivers(team: Team, position: str | None = None, limit: int = 40) -> dict:
    league = team.league
    weeks = remaining_weeks(league)
    players = list(Player.objects.all())
    proj = weekly_projections(league, players, weeks)

    def weekly(p: Player) -> WeeklyPlayer:
        return WeeklyPlayer(p.pk, p.position, tuple(p.eligible_slots), {w: proj[(p.pk, w)] for w in weeks})

    def ros_points(p: Player) -> float:
        return round(sum(x.mean for w in weeks if (x := proj[(p.pk, w)])), 2)

    entries = RosterEntry.objects.filter(team__league=league).select_related("player")
    rostered = {e.player_id for e in entries}
    mine = [e.player for e in entries if e.team_id == team.pk and e.lineup_slot != "IR"]
    free_agents = [p for p in players if p.fa_status and p.pk not in rostered and ros_points(p) > 0]

    fa_weekly = [weekly(p) for p in free_agents]
    report = evaluate(
        [weekly(p) for p in mine], fa_weekly, league.lineup_slots, weeks, baseline=streamers(fa_weekly, weeks)
    )
    values = vorp(
        [PlayerValue(p.pk, p.position, ros_points(p)) for p in players if p.pk in rostered or p in free_agents],
        league.lineup_slots,
        league.team_count,
    )

    usage = defaultdict(list)
    for u in Usage.objects.filter(season=league.season, player__in=free_agents):
        usage[u.player_id].append(UsageWeek(u.week, u.snap_pct, u.targets, u.carries))

    by_id = {p.pk: p for p in players}
    rows = []
    for t in report.targets:
        p = by_id[t.id]
        if position and p.position != position:
            continue
        rows.append(
            WaiverRow(
                player=p,
                week_proj=proj[(p.pk, weeks[0])],
                ros_points=ros_points(p),
                vorp=values.get(p.pk, 0.0),
                next_week_gain=t.next_week_gain,
                ros_gain=t.ros_gain,
                trend=trend(usage[p.pk]) if p.position in TREND_POSITIONS else None,
            )
        )
    # most targets gain nothing; fall back to league-wide value so the list stays useful
    rows.sort(key=lambda r: (r.ros_gain, r.next_week_gain, r.vorp), reverse=True)
    return {
        "league": league,
        "team": team,
        "week": weeks[0],
        "weeks": weeks,
        "position": position,
        "rows": rows[:limit],
        "drop": by_id.get(report.drop_id),
        "drop_cost": report.drop_cost,
    }
