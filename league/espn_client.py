"""Thin wrapper over espn-api that returns plain dataclasses for the sync command."""

from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
from itertools import batched

from espn_api.football import League as EspnLeague
from espn_api.football.constant import POSITION_MAP, PRO_TEAM_MAP
from espn_api.football.player import Player as EspnPlayer

PLAYER_CARD_BATCH = 50
FREE_AGENT_POOL = 300


@dataclass
class TeamData:
    espn_id: int
    name: str
    abbrev: str
    owners: list[str]
    wins: int
    losses: int
    ties: int
    points_for: float
    points_against: float
    standing: int
    waiver_rank: int
    roster: list[tuple[int, str]]  # (player espn_id, lineup slot)
    # week -> (opponent espn_id, my score)
    schedule: dict[int, tuple[int, float]]


@dataclass
class PlayerData:
    espn_id: int
    name: str
    position: str
    pro_team: str
    eligible_slots: list[str]
    injury_status: str
    percent_owned: float
    season_projection: float
    fa_status: str = ""  # "FREEAGENT" / "WAIVERS" in this league; "" if rostered
    waiver_clears: datetime | None = None
    # week -> {"points", "projected_points", "breakdown"}
    weeks: dict[int, dict] = field(default_factory=dict)


@dataclass
class ProGameData:
    week: int
    team: str
    opponent: str
    kickoff: datetime | None


@dataclass
class LeagueData:
    espn_id: int
    season: int
    name: str
    team_count: int
    current_week: int
    reg_season_weeks: int
    final_week: int
    lineup_slots: dict[str, int]
    scoring: dict[str, dict]
    teams: list[TeamData]
    players: list[PlayerData]
    pro_games: list[ProGameData]


def fetch_league(league_id: int, season: int, espn_s2: str | None, swid: str | None) -> LeagueData:
    league = EspnLeague(league_id=league_id, year=season, espn_s2=espn_s2, swid=swid)
    settings = league.espn_request.league_get(params={"view": "mSettings"})["settings"]
    pro_schedule = league._get_all_pro_schedule()

    teams = [_team(t) for t in league.teams]
    rostered_ids = {pid for t in teams for pid, _ in t.roster}
    fa_status = _free_agent_status(league)
    players = _player_cards(league, sorted(rostered_ids | fa_status.keys()), pro_schedule)
    for p in players:
        if p.espn_id in fa_status and p.espn_id not in rostered_ids:
            p.fa_status, p.waiver_clears = fa_status[p.espn_id]

    return LeagueData(
        espn_id=league_id,
        season=season,
        name=league.settings.name,
        team_count=league.settings.team_count,
        current_week=league.current_week,
        reg_season_weeks=league.settings.reg_season_count,
        final_week=league.finalScoringPeriod,
        lineup_slots=_lineup_slots(settings["rosterSettings"]["lineupSlotCounts"]),
        scoring=_scoring(settings["scoringSettings"]["scoringItems"]),
        teams=teams,
        players=players,
        pro_games=_pro_games(pro_schedule),
    )


def _team(t) -> TeamData:
    schedule = {}
    for i, (opp, score) in enumerate(zip(t.schedule, t.scores)):
        opp_id = opp.team_id if hasattr(opp, "team_id") else opp
        schedule[i + 1] = (opp_id, score)
    return TeamData(
        espn_id=t.team_id,
        name=t.team_name,
        abbrev=t.team_abbrev,
        owners=[f"{o.get('firstName', '')} {o.get('lastName', '')}".strip() for o in t.owners],
        wins=t.wins,
        losses=t.losses,
        ties=t.ties,
        points_for=t.points_for,
        points_against=t.points_against,
        standing=t.standing,
        waiver_rank=t.waiver_rank,
        roster=[(p.playerId, p.lineupSlot) for p in t.roster],
        schedule=schedule,
    )


def _player_cards(league: EspnLeague, ids: list[int], pro_schedule) -> list[PlayerData]:
    """Player cards carry every week's actual + projected points for the season."""
    players = []
    for batch in batched(ids, PLAYER_CARD_BATCH):
        # "11{season}{week}" = weekly projection split; ESPN only publishes the current week's
        data = league.espn_request.get_player_card(
            list(batch),
            league.finalScoringPeriod,
            additional_filters=[f"11{league.year}{league.current_week}"],
        )
        for raw in data.get("players", []):
            p = EspnPlayer(raw, league.year, pro_schedule)
            players.append(
                PlayerData(
                    espn_id=p.playerId,
                    name=p.name,
                    position=p.position,
                    pro_team=p.proTeam,
                    eligible_slots=p.eligibleSlots,
                    injury_status=p.injuryStatus or "",
                    percent_owned=max(p.percent_owned, 0),
                    season_projection=p.projected_total_points or 0,
                    weeks={
                        week: {
                            "points": s.get("points"),
                            "projected_points": s.get("projected_points"),
                            "breakdown": s.get("breakdown", {}),
                        }
                        for week, s in p.stats.items()
                        if week  # week 0 is the season total
                    },
                )
            )
    return players


def _free_agent_status(league: EspnLeague) -> dict[int, tuple[str, datetime | None]]:
    """Top free agents by ownership with their status, including waiver clear time."""
    filters = {
        "players": {
            "filterStatus": {"value": ["FREEAGENT", "WAIVERS"]},
            "limit": FREE_AGENT_POOL,
            "sortPercOwned": {"sortPriority": 1, "sortAsc": False},
        }
    }
    data = league.espn_request.league_get(
        params={"view": "kona_player_info", "scoringPeriodId": league.current_week},
        headers={"x-fantasy-filter": json.dumps(filters)},
    )
    return {
        p["id"]: (p["status"], _from_ms(p.get("waiverProcessDate")) if p["status"] == "WAIVERS" else None)
        for p in data["players"]
    }


def _from_ms(ms: int | None) -> datetime | None:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc) if ms else None


def _lineup_slots(counts: dict[str, int]) -> dict[str, int]:
    return {POSITION_MAP[int(slot_id)]: n for slot_id, n in counts.items() if n}


def _scoring(items: list[dict]) -> dict[str, dict]:
    return {
        str(item["statId"]): {
            "points": item.get("points", 0),
            "overrides": item.get("pointsOverrides", {}),
        }
        for item in items
    }


def _pro_games(pro_schedule) -> list[ProGameData]:
    games = []
    for team_id, by_week in pro_schedule.items():
        for week, (game, *_) in by_week.items():
            opp_id = game["awayProTeamId"] if game["awayProTeamId"] != team_id else game["homeProTeamId"]
            games.append(
                ProGameData(
                    week=int(week),
                    team=PRO_TEAM_MAP[team_id],
                    opponent=PRO_TEAM_MAP[opp_id],
                    kickoff=_from_ms(game.get("date")),
                )
            )
    return games
