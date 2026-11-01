# WaiverWise

Start/sit, waiver, and trade analysis for a private ESPN fantasy football league.

Data: ESPN (league, rosters, scoring, actuals, current-week projections), Sleeper (weekly projections for every remaining week, scored with your league's rules), nflverse (id crosswalk, usage).

## Setup

```sh
cp .env.example .env   # fill in ESPN_LEAGUE_ID, ESPN_S2, ESPN_SWID, ESPN_MY_TEAM_ID, SECRET_KEY
docker compose up -d --build
docker compose exec web python manage.py migrate
docker compose exec web python manage.py sync_all
```

App: http://localhost:8000 (start/sit, waivers, trades). Every page requires sign-in, so create an account first: `docker compose exec web python manage.py createsuperuser`.

`ESPN_S2` / `ESPN_SWID` are cookies from fantasy.espn.com (devtools → Application → Cookies). Compose reads `.env` at container creation, so run `docker compose up -d` after editing it.

After changing dependencies, rebuild with fresh venv volumes or the containers keep the old one: `docker compose up -d --build --renew-anon-volumes`.

## Explanations

Each page has an "Explain this" button that sends the numbers the page already computed to Claude for a short plain-English write-up, at low effort. It needs `ANTHROPIC_API_KEY` in `.env`. The model is `CLAUDE_MODEL`, default `claude-haiku-5-5` (the cheapest, about $0.0003 per click); set `claude-sonnet-5-5` or `claude-opus-5-5` for richer write-ups. Opus and Sonnet requests also turn on server-side refusal fallback, which Haiku doesn't support. Explanations are cached by their input, so re-clicking on unchanged data doesn't make another API call.

## Syncing

Locally, the compose `scheduler` service runs syncs on its own (times in `TIME_ZONE`, America/New_York). In production, `.github/workflows/sync.yml` runs the same schedule (see Deploying):

| Job | When | Steps |
|---|---|---|
| nightly | daily 5:00 | league, nfl, projections |
| sunday_live | Sun 10:00–23:30, every 30 min | league |
| primetime_live | Mon/Thu 19:00–23:30, every 30 min | league |
| sunday_projections | Sun 9:45, 11:45 | projections |
| primetime_projections | Mon/Thu 18:45 | projections |

On start it runs a full sync if data is more than 6 hours old (WSL/Docker may have been off). Logs: `docker compose logs -f scheduler`.

Manual: `docker compose exec web python manage.py sync_all [--only league nfl projections]`.

## Projection accuracy

`sync_all` saves each week's projections until kickoff, because ESPN doesn't keep past ones. `manage.py projection_accuracy` compares them with what happened: mean absolute error per source, and whether matchup rating or implied total still explains the misses. Matchup data only adjusts recommendations if this shows it would help, and it takes a few weeks of snapshots to mean anything.

## Tests

```sh
uv run pytest
```

CI (`.github/workflows/tests.yml`) runs the suite against Postgres 16 on every PR and on pushes to `dev` and `main`, and fails if a model change is missing its migration. Locally, tests use SQLite unless `DATABASE_URL` is set.

## Deploying (Render + GitHub Actions, free)

- **Web app:** Render free web service (Docker), defined in `render.yaml`. Migrations run when the container starts.
- **Syncs:** `.github/workflows/sync.yml` runs `sync_all` on a schedule (nightly full sync, every 30 min during game windows, projection refreshes before kickoffs). It replaces the always-on `scheduler` process, which is still used for local dev.
- **Database:** Neon free Postgres. Only `DATABASE_URL` is read. Always use Neon's **direct** connection string, not the pooled one (no `-pooler` in the hostname): `sync_all` holds a session advisory lock, which transaction pooling breaks.

### 1. Web service on Render

1. Render dashboard → **New → Blueprint** → pick this repo. It reads `render.yaml`.
2. Before applying, set `region` in `render.yaml` to the region closest to your Neon database (this repo uses `ohio`).
3. Fill in the prompted values: `DATABASE_URL` (Neon's direct connection string), `ANTHROPIC_API_KEY`, and `ESPN_MY_TEAM_ID`. `SECRET_KEY` is generated for you. ESPN cookies aren't needed on Render, since syncs run in GitHub Actions.
4. Create an account once it's live: Render dashboard → the service → **Shell**, then `python manage.py createsuperuser`.

The service deploys from `main` (the default branch). Merging `dev` into `main` is a release.

### 2. Scheduled syncs on GitHub Actions

Repo → **Settings → Secrets and variables → Actions → New repository secret** for each of:

| Secret | Value |
|---|---|
| `DATABASE_URL` | Neon's direct connection string, the same value as on Render |
| `SECRET_KEY` | any random string (`python3 -c 'import secrets; print(secrets.token_urlsafe(50))'`) |
| `ESPN_LEAGUE_ID`, `ESPN_SEASON`, `ESPN_S2`, `ESPN_SWID`, `ESPN_MY_TEAM_ID` | same values as `.env` |

Then **Actions → Sync league data → Run workflow** to do the first sync by hand. After that it runs on its own. A failed sync (for example, expired ESPN cookies) fails the workflow and GitHub emails you.

GitHub cron uses UTC and can run a few minutes late when GitHub is busy.

### Free-tier tradeoffs

- **Cold starts:** the free web service sleeps after 15 minutes without traffic, and waking takes about a minute. The installed app shows your last-seen copy of a page after 4 seconds while the server wakes.
- **Database limits:** Neon's free plan has 0.5 GB of storage and 100 compute-hours a month, and suspends the database until next month if compute runs out. Set the compute size to the minimum, and check usage in the Neon dashboard after a full game day. `/health/` skips the database so health checks don't keep it awake.

### Moving the database from Render to Neon

League data re-syncs in a minute, but accounts, cached explanations, and projection snapshots can't be rebuilt, so copy the data rather than starting empty.

1. Create a Neon project on the same major Postgres version as the Render database, in the region closest to Ohio. Copy its direct connection string.
2. Repo → **Actions → Sync league data → ⋯ → Disable workflow**, so no sync writes to the old database after the dump.
3. Dump and restore, using a client at least as new as the source server:

   ```sh
   docker run --rm -e SRC="$RENDER_EXTERNAL_URL" -e DST="$NEON_DIRECT_URL" postgres:16-alpine \
     sh -c 'pg_dump --format=custom --no-owner --no-privileges "$SRC" | pg_restore --no-owner --no-privileges --exit-on-error -d "$DST"'
   ```

4. Compare row counts for `league_player`, `league_playerweek`, `nfl_projectionsnapshot`, and `auth_user` on both databases.
5. Set `DATABASE_URL` to the Neon string on Render (this redeploys) and in the Actions secret.
6. Sign in to the app and check that your pages load, then re-enable the workflow and run it once by hand.

Keep the Render database until a game day has passed cleanly, then delete it.

### Install on your phone

Open `https://<service>.onrender.com` and sign in, then:

- **iPhone (Safari):** Share → Add to Home Screen.
- **Android (Chrome):** ⋮ menu → Install app (or Add to Home screen).

It opens full-screen like a native app. Recently viewed pages stay readable offline.

### When ESPN cookies expire

A red banner appears in the app and the sync workflow fails. Grab fresh `espn_s2` / `SWID` cookies and update the `ESPN_S2` / `ESPN_SWID` GitHub Actions secrets. Render doesn't hold them: only the sync workflow talks to ESPN. Locally, update `.env`.
