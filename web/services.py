"""Glue between stored league data and the pure analysis functions."""

from dataclasses import dataclass

from django.conf import settings
from django.db.models import Q

from analysis.lineup import NON_STARTING
from analysis.projections import Projection
from analysis.startsit import Recommendation, RosterSpot, recommend
from league.models import League, Matchup, Player, ProGame, Team
from nfl.consensus import game_started, week_kickoffs, weekly_projections

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
