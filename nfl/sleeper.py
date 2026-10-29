"""Sleeper weekly projections. Undocumented endpoint, so treat failures as non-fatal."""

import httpx

URL = "https://api.sleeper.app/projections/nfl/{season}/{week}"
POSITIONS = ["QB", "RB", "WR", "TE", "K", "DEF"]


def fetch_week(season: int, week: int) -> list[dict]:
    params = [("season_type", "regular")] + [("position[]", p) for p in POSITIONS]
    resp = httpx.get(URL.format(season=season, week=week), params=params, timeout=30)
    resp.raise_for_status()
    return resp.json()
