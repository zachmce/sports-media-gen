# matchup-thumbs

An HTTP service that generates sports matchup images — thumbnails (16:9), logos, and posters (2:3) — on the fly for arbitrary team pairings across many leagues.

Given `(league, away, home, kind, style)` it composites team logos and colors into an image and returns it. Outputs are deterministic and aggressively cached at every tier.

All team metadata and logos come from public ESPN APIs/CDN — no paid services. Intended for personal/homelab use.

## Quick Start

```bash
cp .env.example .env
docker compose up
curl http://localhost:8000/healthz
```

> Note: `docker compose up` brings up Postgres, Redis, the API, and nginx and runs
> database migrations automatically — but it does **not** load team data. The `seed`
> service is profile-gated, so without an explicit seed the registry is empty and
> matchup requests will 404. See **Operations** below for the full bring-up.

## Operations / Running the stack

How the compose stack fits together:

- **Migrations run automatically.** `api` depends on the one-shot `migrate` service
  (`condition: service_completed_successfully`), so any `docker compose up` applies
  `alembic upgrade head` before the API starts.
- **Seeding is explicit.** The `seed` service is behind `profiles: ["seed"]`, so a
  plain `docker compose up` never seeds. Run it on demand with
  `docker compose run --rm seed` (naming the service runs it even though it's
  profiled). Seeding is **idempotent** — it upserts on `(league_id, slug)`, so
  re-running refreshes data in place without wiping anything.
- **Rebuild images after code changes.** `migrate`, `seed`, and `api` share one
  image (`build: .`). Compose does **not** rebuild on code changes unless you pass
  `--build` or run `docker compose build` first. Skipping this is the classic
  stale-image gotcha — e.g. an old image silently seeds empty `logo_variants`.
- **Ports** come from `.env`: the API is published on `API_HOST_PORT` (default
  `8000`) and nginx on `NGINX_HOST_PORT` (default `8080`).

### A. Completely fresh (from nothing)

```bash
cp .env.example .env          # first time only — set API_HOST_PORT, etc.
docker compose down -v        # wipe any leftover volumes for a clean slate
docker compose build          # build the image from current source
docker compose run --rm seed  # starts pg+redis, runs migrations, seeds all leagues, pre-warms Redis
docker compose up -d          # bring up postgres, redis, api, nginx (migrate is a no-op now)
```

Verify (substitute your `API_HOST_PORT`):

```bash
curl -s http://localhost:8000/healthz
curl -s -o /dev/null -w '%{http_code}\n' http://localhost:8000/nfl/chiefs/eagles/thumb
```

`docker compose run --rm seed` pulls up its dependencies (postgres → migrate →
redis) and leaves them running, so the following `up -d` just adds `api` + `nginx`.

> The default `SEED_LEAGUES` seeds all six leagues
> (`nba,nfl,mlb,nhl,ncaaf,ncaab`). The `ncaaf`/`ncaab` crawls are the slow ones
> (~1–2 min each). For a faster start, seed a subset:
> `SEED_LEAGUES=nfl,nba docker compose run --rm seed`.

### B. Update with new images (keep existing data)

```bash
git pull
docker compose build          # rebuild the shared image from updated code
docker compose run --rm seed  # applies new migrations (dep) + re-seeds idempotently
                              #   ← only needed when seed/data logic changed
docker compose up -d          # recreates api + nginx on the new image
```

Notes:

- **`docker compose build` is the step people forget.** Without it, `up` and `run`
  reuse the old image and your changes don't take effect.
- If an update **only adds a migration** (no seed/data change), skip the seed step —
  `docker compose up -d --build` alone rebuilds and runs the migration via the `api`
  dependency.
- To be certain containers are replaced after a build: `docker compose up -d
  --force-recreate`.

## Development

```bash
uv sync
uv run pytest
uv run ruff check src tests
uv run mypy --strict src/matchup_thumbs
```

## Legal

See [LICENSE](LICENSE) (MIT) and [DISCLAIMER.md](DISCLAIMER.md) for trademark attribution and ESPN sourcing notes.
