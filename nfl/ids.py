"""Attach Sleeper / GSIS / PFR ids to ESPN players via nflverse ff_playerids."""

import nflreadpy

from league.models import Player

ESPN_TO_SLEEPER_TEAM = {"WSH": "WAS"}


def sleeper_team(espn_team: str) -> str:
    return ESPN_TO_SLEEPER_TEAM.get(espn_team, espn_team)


def update_crosswalk() -> int:
    ids = nflreadpy.load_ff_playerids().drop_nulls("espn_id")
    by_espn = {
        row["espn_id"]: row
        for row in ids.select("espn_id", "sleeper_id", "gsis_id", "pfr_id").iter_rows(named=True)
    }
    updated = []
    for p in Player.objects.all():
        if p.position == "D/ST":
            # Sleeper keys team defenses by team abbreviation
            p.sleeper_id = sleeper_team(p.pro_team)
        elif row := by_espn.get(p.espn_id):
            p.sleeper_id = str(row["sleeper_id"] or "")
            p.gsis_id = row["gsis_id"] or ""
            p.pfr_id = row["pfr_id"] or ""
        else:
            continue
        updated.append(p)
    Player.objects.bulk_update(updated, ["sleeper_id", "gsis_id", "pfr_id"])
    return len(updated)
