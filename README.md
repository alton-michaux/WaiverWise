# fantasy_guru

Start/sit, waiver, and trade analysis for a private ESPN fantasy football league.

Data: ESPN (league, rosters, scoring, actuals, current-week projections), Sleeper (weekly projections for every remaining week, scored with your league's rules), nflverse (id crosswalk, usage).

## Setup

```sh
cp .env.example .env   # fill in ESPN_LEAGUE_ID, ESPN_S2, ESPN_SWID, ESPN_MY_TEAM_ID, SECRET_KEY
docker compose up -d --build
docker compose exec web python manage.py migrate
docker compose exec web python manage.py sync_all
```

App: http://localhost:8000 (start/sit, waivers, trades). Admin needs `createsuperuser`.

`ESPN_S2` / `ESPN_SWID` are cookies from fantasy.espn.com (devtools → Application → Cookies). Compose reads `.env` at container creation, so run `docker compose up -d` after editing it.

After changing dependencies, rebuild with fresh venv volumes or the containers keep the old one: `docker compose up -d --build --renew-anon-volumes`.

## Explanations

Each page has an "Explain this" button that sends the numbers the page already computed to Claude for a short plain-English write-up, at low effort. It needs `ANTHROPIC_API_KEY` in `.env`. The model is `CLAUDE_MODEL`, default `claude-haiku-5-5` (the cheapest, about $0.0003 per click); set `claude-sonnet-5-5` or `claude-opus-5-5` for richer write-ups. Opus and Sonnet requests also turn on server-side refusal fallback, which Haiku doesn't support. Explanations are cached by their input, so re-clicking on unchanged data doesn't make another API call.

## Syncing

The `scheduler` service runs syncs on its own (times in `TIME_ZONE`, America/New_York):

| Job | When | Steps |
|---|---|---|
| nightly | daily 5:00 | league, nfl, projections |
| sunday_live | Sun 10:00–23:30, every 30 min | league |
| primetime_live | Mon/Thu 19:00–23:30, every 30 min | league |
| sunday_projections | Sun 9:45, 11:45 | projections |
| primetime_projections | Mon/Thu 18:45 | projections |

On start it runs a full sync if data is more than 6 hours old (WSL/Docker may have been off). Logs: `docker compose logs -f scheduler`.

Manual: `docker compose exec web python manage.py sync_all [--only league nfl projections]`.

## Tests

```sh
uv run pytest
```
