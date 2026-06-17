# Changelog

All notable changes to matchup-thumbs are documented here.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versions correspond to annotated git tags (`v1.X.Y`) and GitHub Releases.

---

## [1.3.0] — 2026-06-17

### Breaking Changes

#### NCAA alias route removed (Phase 13 / ROUTE-01)

The dedicated 5-segment alias route `GET /ncaa/{sport}/{away}/{home}/{kind}` has been
removed. Requests to the old path now return **404 Not Found** (no redirect — this is a
homelab service and a clean break was chosen over a deprecation shim per ROUTE-01).

**Caller migration mapping:**

| Old path | New path |
|----------|----------|
| `/ncaa/football/{away}/{home}/{kind}` | `/ncaaf/{away}/{home}/{kind}` |
| `/ncaa/basketball/{away}/{home}/{kind}` | `/ncaab/{away}/{home}/{kind}` |

`ncaaf` and `ncaab` have always been members of `KNOWN_LEAGUES` and resolve via the
general 4-segment route `GET /{league}/{away}/{home}/{kind}` — no other change to
resolution, caching, or rate-limiting behavior.

### Added

- Per-league logo asset pipeline: additive `leagues.logo_url` / `logo_variants` migration,
  ESPN core-API fetch, idempotent seed persistence, Redis pre-warm under
  `leaguelogo:{league}:{variant}`, `load_league_logo()` loader, and
  `DecodedAssets.league_logo` field threaded through `render.py` (Phase 11).
- NCAA league logos served from the ncaa.com public sportbanners CDN
  (`https://www.ncaa.com/modules/custom/casablanca_core/img/sportbanners/{sport}.png`)
  as a sanctioned second public source (ESPN returns a non-distinguishable generic icon
  for NCAA leagues). Mapping is a fixed module-level dict; no user-supplied string reaches
  the URL (SSRF gate: T-i3r-01).

### Changed

- Both generators (`thumb.py`, `poster.py`) now render the league logo in place of the
  "VS" wordmark (VS-text path preserved behind `if league_logo is None:` fallback).
- Poster seam replaced with a GaussianBlur blend; no visible hard color boundary between
  halves.
- `render_version` bumped 3 → 4, invalidating all prior cached renders on first deploy
  (Phase 12 / CACHE-08).
- WCAG contrast enforced for the league logo against the blended seam background using
  the existing `contrast.py` luminance math; outline/halo applied as last resort.

---

## [1.2.1] — 2026-06-16

### Fixed

- Invisible-logo bug: contrast-aware vibrant rendering (Phase 10). See PR #7.

## [1.2.0] — 2026-06-16

### Added

- Logo variant data pipeline, WCAG contrast engine, generator integration (Phases 8–10).

---

## [1.1.0] — 2026-06-15

### Changed

- Tech-debt paydown (DEBT-01/02/03); zero runtime behavior change (Phase 7).

---

## [1.0.0] — 2026-06-15

Initial release. Full matchup-image service (Phases 1–6): FastAPI + Pillow generators,
Postgres team registry with `pg_trgm` fuzzy resolution, Redis multi-tier cache, nginx
caching reverse proxy, Docker Compose stack, GitHub Actions CI/CD.
