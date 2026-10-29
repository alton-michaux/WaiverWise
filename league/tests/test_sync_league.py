from dataclasses import replace
from datetime import datetime, timezone

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from league.espn_client import LeagueData, PendingMoveData, PlayerData, ProGameData, TeamData
from league.management.commands.sync_league import Command
from league.models import League, Matchup, PendingMove, Player, PlayerWeek, ProGame, RosterEntry, Team

pytestmark = pytest.mark.django_db

N_PLAYERS = 40
WEEKS = range(1, 9)


def league_data(points_bump=0.0, injury="ACTIVE"):
    players = [
        PlayerData(
            espn_id=1000 + i,
            name=f"Player {i}",
            position="RB" if i % 2 else "WR",
            pro_team="BUF",
            eligible_slots=["RB", "BE"],
            injury_status=injury,
            percent_owned=50.0,
            season_projection=100.0,
            fa_status="" if i < 20 else "FREEAGENT",
            weeks={w: {"points": 10.0 + w + points_bump, "projected_points": 11.0, "breakdown": {"rec": 1}} for w in WEEKS},
        )
        for i in range(N_PLAYERS)
    ]
    teams = [
        TeamData(
            espn_id=t,
            name=f"Team {t}",
            abbrev=f"T{t}",
            owners=["Owner"],
            wins=1,
            losses=1,
            ties=0,
            points_for=100.0,
            points_against=90.0,
            standing=t,
            waiver_rank=t,
            roster=[(1000 + t * 10 + i, "BE") for i in range(10)],
            schedule={w: (2 if t == 1 else 1, 90.0 + w + points_bump) for w in WEEKS},
        )
        for t in (1, 2)
    ]
    return LeagueData(
        espn_id=1,
        season=2026,
        name="Test League",
        team_count=2,
        current_week=8,
        reg_season_weeks=14,
        final_week=17,
        lineup_slots={"RB": 2, "BE": 7},
        scoring={"53": {"points": 0.75, "overrides": {}}},
        teams=teams,
        players=players,
        pro_games=[ProGameData(w, "BUF", "MIA", datetime(2026, 9, 10, 17, tzinfo=timezone.utc)) for w in WEEKS],
        pending=[PendingMoveData("abc", "waiver", 1, [{"player_id": 1000, "from_team": 0, "to_team": 1, "type": "ADD"}])],
    )


def counts():
    return {
        m.__name__: m.objects.count()
        for m in (League, Team, Player, PlayerWeek, ProGame, RosterEntry, Matchup, PendingMove)
    }


def test_save_creates_everything():
    Command()._save(league_data())
    assert counts() == {
        "League": 1,
        "Team": 2,
        "Player": N_PLAYERS,
        "PlayerWeek": N_PLAYERS * len(WEEKS),
        "ProGame": len(WEEKS),
        "RosterEntry": 20,
        "Matchup": len(WEEKS),
        "PendingMove": 1,
    }
    m = Matchup.objects.get(week=3)
    assert (m.team_a.espn_id, m.team_b.espn_id, m.score_a, m.score_b) == (1, 2, 93.0, 93.0)


def test_save_is_idempotent_and_updates():
    Command()._save(league_data())
    first = counts()
    Command()._save(league_data(points_bump=5.0, injury="OUT"))
    assert counts() == first
    assert PlayerWeek.objects.get(player__espn_id=1001, week=2).points == 17.0
    assert set(Player.objects.values_list("injury_status", flat=True)) == {"OUT"}
    assert Matchup.objects.get(week=1).score_a == 96.0


def test_players_missing_from_sync_lose_availability():
    Command()._save(league_data())
    data = league_data()
    Command()._save(replace(data, players=[p for p in data.players if p.espn_id != 1030]))
    assert Player.objects.get(espn_id=1030).fa_status == ""


def test_query_count_does_not_scale_with_rows():
    with CaptureQueriesContext(connection) as ctx:
        Command()._save(league_data())
    # one-row-at-a-time writes made this O(players x weeks); batched it's a small constant
    assert len(ctx.captured_queries) < 40, len(ctx.captured_queries)
