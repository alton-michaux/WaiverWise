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

## Tests

```sh
uv run pytest
```

CI (`.github/workflows/tests.yml`) runs the suite against Postgres 16 on every PR and on pushes to `dev` and `master`, and fails if a model change is missing its migration. Locally, tests use SQLite unless `DATABASE_URL` is set.

## Deploying (Render + GitHub Actions, free)

- **Web app:** Render free web service (Docker), defined in `render.yaml`. Migrations run when the container starts.
- **Syncs:** `.github/workflows/sync.yml` runs `sync_all` on a schedule (nightly full sync, every 30 min during game windows, projection refreshes before kickoffs). It replaces the always-on `scheduler` process, which is still used for local dev.
- **Database:** Render Postgres. Only `DATABASE_URL` is read.

### 1. Web service on Render

1. Render dashboard → **New → Blueprint** → pick this repo. It reads `render.yaml`.
2. Before applying, set `region` in `render.yaml` to your database's region (the hostname in its URL shows it, e.g. `dpg-xxxx-a.ohio-postgres.render.com`; this repo uses `ohio`). The internal database URL only works within the same region.
3. Fill in the prompted values: `DATABASE_URL` (the database's **Internal** URL), `ANTHROPIC_API_KEY`, and `ESPN_MY_TEAM_ID`. `SECRET_KEY` is generated for you. ESPN cookies aren't needed on Render, since syncs run in GitHub Actions.
4. Create an account once it's live: Render dashboard → the service → **Shell**, then `python manage.py createsuperuser`.

The service deploys from `master`. Merging `dev` into `master` is a release.

### 2. Scheduled syncs on GitHub Actions

Repo → **Settings → Secrets and variables → Actions → New repository secret** for each of:

| Secret | Value |
|---|---|
| `DATABASE_URL` | the database's **External** URL with `?sslmode=require` on the end |
| `SECRET_KEY` | any random string (`python3 -c 'import secrets; print(secrets.token_urlsafe(50))'`) |
| `ESPN_LEAGUE_ID`, `ESPN_SEASON`, `ESPN_S2`, `ESPN_SWID`, `ESPN_MY_TEAM_ID` | same values as `.env` |

Then **Actions → Sync league data → Run workflow** to do the first sync by hand. After that it runs on its own. A failed sync (for example, expired ESPN cookies) fails the workflow and GitHub emails you.

GitHub cron uses UTC and can run a few minutes late when GitHub is busy.

### Free-tier tradeoffs

- **Cold starts:** the free web service sleeps after 15 minutes without traffic, and waking takes about a minute. The installed app shows your last-seen copy of a page after 4 seconds while the server wakes.
- **Database expiry:** Render's free Postgres expires 30 days after creation, and is deleted after a 14-day grace period. League data re-syncs in a minute, but accounts and cached explanations would be lost. Upgrade or move the database before then.

### Install on your phone

Open `https://<service>.onrender.com` and sign in, then:

- **iPhone (Safari):** Share → Add to Home Screen.
- **Android (Chrome):** ⋮ menu → Install app (or Add to Home screen).

It opens full-screen like a native app. Recently viewed pages stay readable offline.

### When ESPN cookies expire

A red banner appears in the app and the sync workflow fails. Grab fresh `espn_s2` / `SWID` cookies and update the `ESPN_S2` / `ESPN_SWID` GitHub Actions secrets. Render doesn't hold them: only the sync workflow talks to ESPN. Locally, update `.env`.
