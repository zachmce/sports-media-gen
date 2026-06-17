@AGENTS.md

<!-- GSD:project-start source:PROJECT.md -->

## Project

**matchup-thumbs**

An HTTP service that generates sports matchup images — thumbnails (16:9), logos, and posters (2:3) — on the fly for arbitrary team pairings across many leagues. Given `(league, away, home, kind, style)` it composites team logos and colors into an image and returns it. Outputs are deterministic and aggressively cached at every tier. All team metadata and logos come from public ESPN APIs/CDN — no paid services. It is reimplemented in Python/FastAPI, inspired by `sethwv/game-thumbs`, and intended for personal/homelab use as a public open-source repo.

### Data sources

**Primary:** Public ESPN APIs/CDN (`site.api.espn.com`, `sports.core.api.espn.com`, `a.espncdn.com`).

**Sanctioned second public source (ncaaf/ncaab league shields only):** NCAA.com's public sportbanner CDN at
`https://www.ncaa.com/modules/custom/casablanca_core/img/sportbanners/{sport}.png`.
ESPN returns only a generic same-URL icon for NCAA leagues (identical `default` and `dark` hrefs),
so the ncaa.com source is used for the real per-sport shield. This is free/public — the "no paid
services" spirit is preserved (user-approved 2026-06-17).

**SSRF safety:** The sport filename is derived exclusively from `_NCAA_SPORTBANNER_SPORTS`, a
fixed module-level mapping in `seed.py` keyed by the already KNOWN_LEAGUES-validated `league_slug`.
No user-supplied or ESPN-supplied string ever reaches the URL — the dict lookup is the gate
(unmapped slug → placeholder, no fetch). The base URL is the `ncaa_sportbanner_base_url`
setting (a constant default). This satisfies T-i3r-01.

**Core Value:** Given any valid `(league, away, home, kind)` request, return a correct, good-looking matchup image — and serve repeat requests from cache with near-zero app involvement.

### Constraints

- **Tech stack**: FastAPI + Pillow, Postgres (team registry, `pg_trgm` fuzzy), Redis (cache), nginx (caching reverse proxy). Fixed by PRD/conventions.
- **Python**: 3.14+, async-first, `mypy --strict`/`ty` clean, `ruff` clean.
- **Generators**: pure functions `(away_team, home_team, decoded_assets) -> PIL.Image` — no I/O inside.
- **External calls**: single shared `httpx` client with timeouts + retries; every ESPN call has a timeout and a stale-cache fallback.
- **Containers**: multi-stage Dockerfile, non-root final image, no baked secrets; `docker compose up` with no manual host setup.
- **Networking**: nginx HTTP-only; trust `X-Forwarded-*`; TLS upstream.
- **CI/security**: public repo; pin Actions to SHAs; minimal `GITHUB_TOKEN`; scanners gate publish; images signed + SBOM-attached, published to GHCR.

<!-- GSD:project-end -->

<!-- GSD:stack-start source:research/STACK.md -->

## Technology Stack

## Recommended Stack

### Core Technologies

| Technology | Version | Purpose | Why Recommended |
|------------|---------|---------|-----------------|
| Python | 3.14+ | Runtime | Locked by AGENTS.md; `match`, `|` unions, `type` statements are used freely; 3.14 is current stable |
| FastAPI | 0.136.3 | ASGI web framework | Fixed by PRD; fastest typed Python API framework, native async, automatic OpenAPI, lifespan context for shared client lifecycle |
| Pillow | 12.2.0 | Image composition | Fixed by PRD; most mature Python imaging library, supports PNG + WebP via libwebp, pure Python API for CPU-bound composition work |
| Postgres 17 | 17.10 (docker) | Team registry DB | Fixed by PRD; `pg_trgm` extension for fuzzy alias matching is a first-class Postgres feature, no external search engine needed |
| Redis 7 | 7.4.9 (docker) | Multi-tier cache | Fixed by PRD; sub-millisecond get/set for team resolution, logo bytes, and rendered image blobs; native TTL per key |
| nginx | stable-alpine (1.28.x) | Caching reverse proxy | Fixed by PRD; `proxy_cache` zones, `limit_req` per-IP rate limiting; HTTP-only, TLS upstream |
| Alembic | 1.18.4 | DB schema migrations | Fixed by PRD; SQLAlchemy ecosystem, async-compatible, runs as init-container before API starts |
| uv | 0.11.21 | Dependency management | Locked by AGENTS.md; replaces pip + venv, deterministic lockfile, fast resolution, `uv run` in CI |

### Async Runtime Layer

| Library | Version | Purpose | Why |
|---------|---------|---------|-----|
| uvicorn | 0.49.0 | ASGI server (HTTP) | Standard ASGI server for FastAPI; in containers run gunicorn + UvicornWorker for process supervision |
| gunicorn | 26.0.0 | Process manager | Manages multiple uvicorn workers in production containers; `UvicornWorker` class bridges sync process model to async workers |
| anyio | 4.13.0 | Async primitives + thread-pool | FastAPI/Starlette depends on it; `anyio.to_thread.run_sync` is the correct way to offload blocking Pillow work to a threadpool without blocking the event loop |

### Database Layer

| Library | Version | Purpose | Why |
|---------|---------|---------|-----|
| psycopg | 3.3.4 | Async PostgreSQL driver | **Chosen over asyncpg** — see decision below. Implements DB-API 2.0, full type annotations, native async/await, mypy stubs included. Handles pg_trgm queries identically to asyncpg. |
| psycopg-pool | 3.3.1 | Async connection pool | `AsyncConnectionPool` for psycopg3; provides bounded pool with health checks and reconnect logic without requiring SQLAlchemy overhead |
| SQLAlchemy | 2.0.50 | NOT used for ORM | See "What NOT to Use" — SQLAlchemy is skipped; raw psycopg3 async is appropriate for a simple read-mostly registry |

### Cache / Redis Layer

| Library | Version | Purpose | Why |
|---------|---------|---------|-----|
| redis | 8.0.0 | Async Redis client | `redis.asyncio.Redis` provides the async client; v8.0 defaults to RESP3 wire protocol (better type mapping), backward-compatible API. Single shared client created in lifespan. |

### HTTP Client

| Library | Version | Purpose | Why |
|---------|---------|---------|-----|
| httpx | 0.28.1 | Async HTTP client for ESPN | Fixed by AGENTS.md; `AsyncClient` with shared lifespan, configurable timeouts. httpx `HTTPTransport(retries=N)` handles `ConnectError`/`ConnectTimeout` retry at transport layer. |
| tenacity | 9.1.4 | Advanced retry logic | For status-code-aware retries (e.g. 503 backoff) that `HTTPTransport.retries` does not cover; decorator-based, fully async-compatible |

### Settings & Validation

| Library | Version | Purpose | Why |
|---------|---------|---------|-----|
| pydantic | 2.13.4 | Request/response models, validation | Locked by AGENTS.md; v2 core in Rust, dramatically faster than v1 |
| pydantic-settings | 2.14.1 | Settings from env vars | `BaseSettings` reads from env; eliminates direct `os.environ` access per AGENTS.md |

### Observability

| Library | Version | Purpose | Why |
|---------|---------|---------|-----|
| prometheus-fastapi-instrumentator | 8.0.0 | Prometheus metrics endpoint | Exposes `/metrics` with per-route HTTP duration histograms automatically; v8 raised min to Starlette >=1.0.0 (compatible with FastAPI 0.136.x). Add custom metrics (render latency, cache hit ratio, ESPN failures) via `prometheus_client` directly. |
| prometheus-client | 0.25.0 | Custom metrics primitives | `Counter`, `Histogram`, `Gauge` for domain-specific metrics (cache tier hit ratio, ESPN fetch failures, resolution miss rate) |
| structlog | 26.1.0 | Structured logging | JSON-line output for log aggregation; integrates cleanly with stdlib logging; no magic global state; async-safe |

### Development & Testing

| Library | Version | Purpose | Why |
|---------|---------|---------|-----|
| ruff | 0.15.17 | Linter + formatter | Locked by AGENTS.md; replaces flake8 + black + isort in one binary |
| mypy | 2.1.0 | Static type checking | `--strict` mode required by AGENTS.md; psycopg3 and Pydantic v2 both ship inline stubs |
| pytest | 9.0.3 | Test runner | Locked by AGENTS.md |
| pytest-asyncio | 1.1.0 | Async test support | Locked by AGENTS.md; set `asyncio_mode = "auto"` in `pyproject.toml` to avoid per-test `@pytest.mark.asyncio` decoration |
| pytest-httpx | 0.36.2 | Mock httpx in tests | Intercept ESPN CDN calls without network; pinned to httpx 0.28.x |
| pytest-cov | 7.1.0 | Coverage reporting | Standard coverage tooling; configured in `pyproject.toml` with source paths |
| pytest-image-snapshot | 0.5.3 | Golden-image comparison for generators | See golden-image section below |

## Gray-Area Decisions (Detailed Rationale)

### Decision 1: Postgres Driver — psycopg3 over asyncpg

- This is a **read-mostly, simple schema** (leagues → teams → aliases). Three tables, foreign keys, one trigram index. Zero need for asyncpg's bulk COPY, binary protocol for high-throughput inserts, or bespoke type codecs.
- psycopg3 ships **inline type stubs** compatible with `mypy --strict`. asyncpg's stubs require a separate `types-asyncpg` package that lags behind releases.
- psycopg3 implements **DB-API 2.0**, which means familiar parameterized `%s` / `%(name)s` syntax, cursor-based fetching, and standard `LISTEN/NOTIFY` if ever needed.
- The 28% QPS advantage psycopg3 has in pipeline mode is irrelevant for a service that caches resolved teams in Redis — the DB is rarely hit on the hot path.
- `AsyncConnectionPool` from `psycopg-pool` is straightforward to configure (min/max, reconnect) and fits the FastAPI lifespan pattern.
- SQLAlchemy async + asyncpg is the highest-performance combo for write-heavy workloads, but adds ~700KB of ORM machinery and halves throughput vs raw drivers anyway.

### Decision 2: Async Redis Client — redis-py 8.0 (built-in async)

### Decision 3: ASGI Server — uvicorn + gunicorn workers

### Decision 4: Metrics — prometheus-fastapi-instrumentator over manual middleware

- Automatically instruments all routes with `http_request_duration_seconds` histogram and `http_requests_total` counter — zero boilerplate.
- Exposes `/metrics` endpoint cleanly.
- v8.0 bumped Starlette floor to >=1.0.0, which FastAPI 0.136.x pulls in (compatible).
- Add **domain-specific metrics** (render latency, per-tier cache hit/miss, ESPN fetch errors, resolution miss rate) using `prometheus_client.Histogram` / `Counter` directly — the instrumentator and manual metrics coexist on the default registry.

### Decision 5: Structured Logging — structlog over python-json-logger

### Decision 6: Golden-Image Testing — pytest-image-snapshot

- `pytest-image-diff` — less maintained, fewer stars, similar API.
- `pytest-mpl` — designed for matplotlib figure output, not general PIL Images.
- Manual `ImageChops.difference` — works but requires writing the fixture scaffolding yourself; pytest-image-snapshot gives you the update-flag workflow for free.

### Decision 7: httpx Retries — built-in transport + tenacity

## WebP Support in Pillow

## Installation

# Core runtime

# Dev / test

### pyproject.toml test configuration

## Alternatives Considered

| Category | Recommended | Alternative | Why Not |
|----------|-------------|-------------|---------|
| PostgreSQL driver | psycopg3 3.3.4 | asyncpg 0.31.0 | asyncpg is faster for write-heavy workloads but adds non-DB-API friction and lags on mypy stubs; overkill for read-mostly registry |
| DB access layer | Raw psycopg3 SQL | SQLAlchemy 2.0 async ORM | SQLAlchemy adds ~700KB, halves throughput vs raw driver, and the schema (3 tables) doesn't warrant ORM machinery |
| DB access layer | Raw psycopg3 SQL | SQLModel 0.0.38 | SQLModel wraps SQLAlchemy ORM + Pydantic v2; all the ORM overhead with less flexibility; appropriate for CRUD-heavy apps, not for a read-mostly service |
| Async Redis | redis-py 8.0 asyncio | aioredis | aioredis was merged into redis-py v4.2 and is deprecated; do not use the standalone package |
| Retry library | tenacity 9.1.4 | httpx-retries 0.5.0 | httpx-retries duplicates tenacity's capabilities and adds a dep with less community adoption |
| Logging | structlog 26.1.0 | python-json-logger | structlog has richer context binding, async-safe design, better processor pipeline; python-json-logger is adequate but structlog is the production standard |
| Metrics integration | prometheus-fastapi-instrumentator 8.0.0 | Manual Starlette middleware | Instrumentator covers 90% of the boilerplate; manual middleware only makes sense if you need to customize the HTTP duration metric shape fundamentally |
| Image comparison | pytest-image-snapshot 0.5.3 | Manual ImageChops.difference | Plugin gives --snapshot-update workflow, pixelmatch threshold, and pytest report integration for free |
| ASGI server | gunicorn + UvicornWorker | bare uvicorn | Bare uvicorn has no process supervision; crash = container exit; gunicorn provides graceful restart and worker management |

## What NOT to Use

| Avoid | Why | Use Instead |
|-------|-----|-------------|
| SQLAlchemy ORM | Halves DB throughput, 700KB overhead, 3-table schema doesn't warrant it | Raw psycopg3 async cursors |
| SQLModel | Same overhead as SQLAlchemy ORM + less flexibility | Raw psycopg3 async cursors |
| asyncpg directly | Non-DB-API interface, mypy stubs lag, no benefit for simple read-mostly queries | psycopg3 + psycopg-pool |
| aioredis (standalone) | Deprecated; merged into redis-py v4.2 | redis[asyncio] 8.0.0 |
| celery / arq / task queue | PRD explicitly out-of-scope; generation is fast and synchronous via threadpool | anyio.to_thread.run_sync for CPU-bound Pillow work |
| httpx-retries | Thin wrapper around httpx transport; adds a dep without capability beyond tenacity | tenacity + httpx HTTPTransport(retries=N) |
| python-dotenv in app code | AGENTS.md forbids reading os.environ directly | pydantic-settings BaseSettings |
| bare uvicorn in production | No process supervision; crash ends container | gunicorn + UvicornWorker |
| pytest-image-diff | Less maintained than pytest-image-snapshot, similar API | pytest-image-snapshot |
| PIL (old fork) | Dead; Pillow is the maintained fork | Pillow 12.2.0 |

## Docker Image Tags

| Service | Image | Tag | Notes |
|---------|-------|-----|-------|
| postgres | postgres | 17.10 | Use `17` tag for rolling patch updates; pin to `17.10` for reproducibility |
| redis | redis | 7.4.9-bookworm | Redis 8.x is out but is not yet the established LTS; 7.4 is stable LTS |
| nginx | nginx | stable-alpine3.23 | Alpine-based for minimal attack surface; `stable` track for production |
| api | python | 3.14-slim-bookworm | Base for multi-stage build; slim variant, non-root final user |

## Version Compatibility Matrix

| Package | Compatible With | Notes |
|---------|-----------------|-------|
| FastAPI 0.136.3 | Starlette >=1.0.0, Pydantic v2 >=2.0 | Starlette 1.x required for prometheus-fastapi-instrumentator 8.0 |
| prometheus-fastapi-instrumentator 8.0.0 | Starlette >=1.0.0,<2.0.0 | FastAPI 0.136.x pulls Starlette 1.x — compatible |
| pytest-httpx 0.36.2 | httpx 0.28.*, pytest 9.* | Pin these together; pytest-httpx is pinned to exact httpx minor |
| psycopg 3.3.4 | Python >=3.10, Postgres 13-17 | Fully compatible with Postgres 17 and pg_trgm |
| redis 8.0.0 | Redis server 7.2–8.8 | RESP3 default; backward-compatible with Redis 7.4 server |
| Alembic 1.18.4 | SQLAlchemy >=1.3.0 | Uses SQLAlchemy core for DDL generation even without the ORM; runs via sync psycopg connection in migrations |
| anyio 4.13.0 | FastAPI/Starlette (transitive) | Do not pin separately — let FastAPI/Starlette pull the compatible version; add as explicit dep only if using anyio APIs directly |

## Sources

- PyPI JSON API (https://pypi.org/pypi/{package}/json) — version verification for all Python packages (HIGH confidence)
- Docker Hub tags pages — Postgres 17.10, Redis 7.4.9, nginx stable-alpine confirmed
- Pillow docs (https://pillow.readthedocs.io/en/stable/installation/building-from-source.html) — libwebp requirement confirmed
- FastAPI release notes (https://fastapi.tiangolo.com/release-notes/) — 0.136.3 confirmed current
- SQLAlchemy docs (https://docs.sqlalchemy.org/en/20/dialects/postgresql.html) — asyncpg and psycopg3 both supported
- Community comparison: https://fernandoarteaga.dev/blog/psycopg-vs-asyncpg/ and https://dasroot.net/posts/2026/02/python-postgresql-sqlalchemy-asyncpg-performance-comparison/
- httpx transport docs (https://www.python-httpx.org/advanced/transports/) — retry via HTTPTransport confirmed
- pytest-asyncio docs (https://pytest-asyncio.readthedocs.io/en/stable/reference/configuration.html) — asyncio_mode=auto confirmed for v1.1.0
- uv releases (https://github.com/astral-sh/uv/releases/latest) — 0.11.21 confirmed current

<!-- GSD:stack-end -->

<!-- GSD:conventions-start source:CONVENTIONS.md -->

## Conventions

Conventions not yet established. Will populate as patterns emerge during development.
<!-- GSD:conventions-end -->

<!-- GSD:architecture-start source:ARCHITECTURE.md -->

## Architecture

Architecture not yet mapped. Follow existing patterns found in the codebase.
<!-- GSD:architecture-end -->

<!-- GSD:skills-start source:skills/ -->

## Project Skills

No project skills found. Add skills to any of: `.claude/skills/`, `.agents/skills/`, `.cursor/skills/`, `.github/skills/`, or `.codex/skills/` with a `SKILL.md` index file.
<!-- GSD:skills-end -->

<!-- GSD:workflow-start source:GSD defaults -->

## GSD Workflow Enforcement

Before using Edit, Write, or other file-changing tools, start work through a GSD command so planning artifacts and execution context stay in sync.

Use these entry points:

- `/gsd-quick` for small fixes, doc updates, and ad-hoc tasks
- `/gsd-debug` for investigation and bug fixing
- `/gsd-execute-phase` for planned phase work

Do not make direct repo edits outside a GSD workflow unless the user explicitly asks to bypass it.
<!-- GSD:workflow-end -->

<!-- GSD:profile-start -->

## Developer Profile

> Profile not yet configured. Run `/gsd-profile-user` to generate your developer profile.
> This section is managed by `generate-claude-profile` -- do not edit manually.
<!-- GSD:profile-end -->
