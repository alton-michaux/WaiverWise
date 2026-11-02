# WaiverWise

Start/sit, waiver, and trade analysis for ESPN fantasy football leagues.

WaiverWise is invite-only. Follow your invite link, connect your ESPN league or join one, and pick your team. Public leagues need nothing else. Private leagues need one member's `espn_s2` and `SWID` cookies, saved encrypted. Every member with cookies gets their own waiver claims, which nobody else sees.

Supports head-to-head points leagues with QB, RB, WR, TE, FLEX (`RB/WR`, `WR/TE`, `RB/WR/TE`), superflex (`OP`), D/ST and K slots. Category, roto and IDP leagues are refused at sync. Scoring rules Sleeper can't price, like 40+ yard TD or 300-yard bonuses, count only through the ESPN half of the projection consensus.

Data: ESPN (league, rosters, scoring, actuals, current-week projections), Sleeper (weekly projections for every remaining week, scored with your league's rules), nflverse (id crosswalk, usage).

## Setup

```sh
cp .env.example .env   # fill in SECRET_KEY and CREDENTIAL_KEY
docker compose up -d --build
docker compose exec web python manage.py migrate
docker compose exec web python manage.py createsuperuser
```

App: http://localhost:8000. Sign in and use **Connect a league**. Without `GITHUB_DISPATCH_TOKEN` nothing starts the first sync for you, so run it yourself: `docker compose exec web python manage.py sync_all --league <id>` (the waiting page shows the id to staff).

A user only sees leagues they have a membership in. Admin → Memberships fixes a wrong team claim.

## Inviting people

Staff make invites in Admin → Invites. The link appears once, right after saving, because only its hash is stored. Both kinds last 14 days, and deleting one cancels it.

- **App invite:** one use, for someone connecting their own league. Only this kind, or a superuser, can add a new league.
- **League invite:** usable once per team in the league, for leaguemates. It opens that league even when it's private, so they don't need cookies. Their waiver claims won't show unless they also save their own.

`MAX_LEAGUES` (default 10) caps leagues this season, because syncs run one league at a time.

**Lost passwords:** there's no email. In Admin → Users, select the user and run **Make a password reset link**, then send them the link. It's made in the admin because it's signed with the server's `SECRET_KEY`.

**Locked out:** five wrong passwords pause that username for 15 minutes. Admin → Access attempts clears it sooner. The pause goes by username because every request reaches the app through Render's proxy, which means anyone who knows a username can trigger it.

`bootstrap_owner <username> <team id>` attaches an existing account to a team already in the database. With `ESPN_S2` and `ESPN_SWID` set, it also saves them as that account's encrypted login, which is how a league from before the connect flow gets its cookies.

After changing dependencies, rebuild with fresh venv volumes or the containers keep the old one: `docker compose up -d --build --renew-anon-volumes`.

## Explanations

Each page has an "Explain this" button that sends the numbers the page already computed to Claude for a short plain-English write-up, at low effort. It needs `ANTHROPIC_API_KEY` in `.env`. The model is `CLAUDE_MODEL`, default `claude-haiku-5-5` (the cheapest, about $0.0003 per click); set `claude-sonnet-5-5` or `claude-opus-5-5` for richer write-ups. Opus and Sonnet requests also turn on server-side refusal fallback, which Haiku doesn't support. Explanations are cached by their input, so re-clicking on unchanged data doesn't make another API call. `EXPLAIN_DAILY_CAP` (default 25) limits how many new explanations each user can get per day, and cached ones don't count.

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
- **Database:** Render Postgres. Only `DATABASE_URL` is read.

### 1. Web service on Render

1. Render dashboard → **New → Blueprint** → pick this repo. It reads `render.yaml`.
2. Before applying, set `region` in `render.yaml` to your database's region (the hostname in its URL shows it, e.g. `dpg-xxxx-a.ohio-postgres.render.com`; this repo uses `ohio`). The internal database URL only works within the same region.
3. Fill in the prompted values: `DATABASE_URL` (the database's **Internal** URL), `ANTHROPIC_API_KEY`, `CREDENTIAL_KEY` and `GITHUB_DISPATCH_TOKEN`. `SECRET_KEY` is generated for you. `GITHUB_DISPATCH_TOKEN` is a fine-grained token limited to this repo with **Actions: read and write**, which lets the app start a new league's first sync.
4. Create an account once it's live: from your machine, run `python manage.py createsuperuser` with `DATABASE_URL` set to the database's external URL. Then sign in and connect a league. Don't do this in a workflow, since Actions logs are public.

The service deploys from `main` (the default branch). Merging `dev` into `main` is a release.

### 2. Scheduled syncs on GitHub Actions

Repo → **Settings → Secrets and variables → Actions → New repository secret** for each of:

| Secret | Value |
|---|---|
| `DATABASE_URL` | the database's **External** URL with `?sslmode=require` on the end |
| `SECRET_KEY` | any random string (`python3 -c 'import secrets; print(secrets.token_urlsafe(50))'`) |
| `CREDENTIAL_KEY` | same value as on Render |
| `BACKUP_PASSPHRASE` | a long random phrase that encrypts backups; only the backup workflow uses it |
| `ESPN_SEASON` | optional, the season new leagues connect to (default in `config/settings.py`) |

Syncs then run on their own. A failed sync fails the workflow and GitHub emails you. Expired ESPN cookies don't count as a failure: the app asks that member to reconnect. A league nobody has opened in 14 days stops syncing until someone does. Logs on this public repo carry only ids and counts.

GitHub cron uses UTC and can run a few minutes late when GitHub is busy.

### 3. Weekly backups

`.github/workflows/backup.yml` dumps the database every Wednesday and keeps it 60 days as an Actions artifact. This repo is public, so anyone can download that artifact: it's encrypted with `BACKUP_PASSPHRASE` before it leaves the runner. Add that secret, and keep a copy of it and `CREDENTIAL_KEY` outside GitHub, since neither can be recovered.

To restore into an empty database:

```bash
gh run download <run id> -n backup
gpg --decrypt backup.dump.gpg | pg_restore --no-owner -d "$DATABASE_URL"
```

Use `pg_restore` 18, the version that made the dump. Restoring into an older server reports an error about `transaction_timeout`, which is harmless.

### Free-tier tradeoffs

- **Cold starts:** the free web service sleeps after 15 minutes without traffic, and waking takes about a minute. The installed app shows your last-seen copy of a page after 4 seconds while the server wakes.
- **Database expiry:** Render's free Postgres expires 30 days after creation, and is deleted after a 14-day grace period. League data re-syncs in a minute, but accounts, cached explanations, and projection snapshots would be lost, and snapshots can't be rebuilt. Upgrade or move the database (`pg_dump`, then restore) before then.

### Install on your phone

Open `https://<service>.onrender.com` and sign in, then:

- **iPhone (Safari):** Share → Add to Home Screen.
- **Android (Chrome):** ⋮ menu → Install app (or Add to Home screen).

It opens full-screen like a native app. Recently viewed pages stay readable offline.

### When ESPN cookies expire

The member whose cookies stopped working sees a banner with a reconnect link, and syncs carry on with any other member's. If nobody's cookies work, everyone in the league sees that its data isn't updating. Only the sync workflow and the connect page talk to ESPN.

### Rotating `CREDENTIAL_KEY`

Put the new key first and keep the old one after a comma (`CREDENTIAL_KEY=new,old`) on Render and in Actions. New saves use the new key, and the old one still reads existing rows until they're re-saved.
