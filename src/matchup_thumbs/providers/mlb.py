"""MLB Stats API concrete DataProvider implementation.

Wraps the ``mlb/client.py`` fetch helper and maps MLB Stats API fields to the
provider-neutral ``ProviderTeam`` / ``ProviderLogoShield`` canonical types.

MiLB sportId SSRF gate (T-i3r-01)
------------------------------------
``_MILB_SPORT_IDS``: a **fixed module-level mapping** from the already
KNOWN_LEAGUES-validated slug to an integer sportId.  No user-supplied string
ever reaches the URL — the dict lookup is the gate.  Mirrors
``_NCAA_SPORTBANNER_SPORTS`` in ``providers/espn.py``.

``"milb"`` (the umbrella, T-ia6-02) is deliberately NOT a key of
``_MILB_SPORT_IDS``: it is a logical union of the 4 affiliate levels, not a
direct MLB Stats API sportId.  sportId 21 is literally named "Minor League
Baseball" and looks like the obvious backing for the umbrella — it is a
decoy that returns COVID-era "Alternate Training Site" rows and must never
be reachable.  ``fetch_teams`` special-cases ``_MILB_UMBRELLA_SLUG`` and
returns *before* the ``_MILB_SPORT_IDS`` lookup, fanning out sequentially
over the fixed ``_MILB_UMBRELLA_FEEDERS`` tuple — each feeder is itself
looked up through ``_MILB_SPORT_IDS``, so no user-supplied string ever
selects a sportId (T-ia6-01).

Rasterize-once / palette-extraction pattern (D-19, D-20, 15-06)
----------------------------------------------------------------
``fetch_teams`` fetches each team's SVG mark bytes once, rasterizes off the
event loop via ``anyio.to_thread.run_sync`` (Pitfall 1 — cairosvg is
CPU-bound), and calls ``extract_palette`` on the rasterized image to derive
``primary_color`` / ``secondary_color`` (bare 6-digit hex, no '#' prefix —
seed.py normalises to '#hex' per existing convention).  On any fetch or
rasterisation failure the team's colors remain ``None`` (MILB-05 safety net)
and the seed continues without aborting the whole league.
"""

from __future__ import annotations

import asyncio
import functools
import io
import re
from typing import Final

import anyio
import httpx
import structlog
from PIL import Image

from ..espn.client import fetch_logo_bytes
from ..mlb.client import fetch_mlb_teams
from ..mlb.palette import extract_palette
from ..settings import settings
from .types import ProviderLogoShield, ProviderTeam

logger = structlog.get_logger()

# ---------------------------------------------------------------------------
# MiLB level→sportId mapping (T-i3r-01 SSRF gate — mirrors _NCAA_SPORTBANNER_SPORTS)
# ---------------------------------------------------------------------------
# Fixed mapping from KNOWN_LEAGUES-validated slug to the MLB Stats API sportId.
# The URL is built only from settings.mlb_statsapi_base_url (a constant) +
# the integer from this dict.  No user/API string ever reaches the URL — the
# dict lookup is the gate: unknown slug → KeyError, no URL construction.
#
# The Single-A slug was hard-renamed 2026-07-16 (user-accepted breaking
# change) — no compatibility alias is kept; the old (pre-rename) slug 404s.
#
# "milb" is intentionally NOT a key here — see the umbrella section below.
_MILB_SPORT_IDS: Final[dict[str, int]] = {
    "milb-aaa": 11,
    "milb-aa": 12,
    "milb-high-a": 13,
    "milb-a": 14,
    "milb-rookie": 16,  # Rookie: DSL (51) + ACL (15) + FCL (15) all under sportId=16
    "milb-winter": 17,  # Winter Leagues: 48 teams, 0 collisions (verified 2026-07-16)
    "milb-independent": 23,  # Independent: 72 teams, 0 collisions (verified 2026-07-16)
}

# ---------------------------------------------------------------------------
# "milb" umbrella (T-ia6-01/02): logical union of the 4 affiliate levels
# ---------------------------------------------------------------------------
# _MILB_UMBRELLA_SLUG is the canonical slug seeded as its own `leagues` row
# (option (a) — see PLAN.md "Umbrella design decision").  _MILB_UMBRELLA_FEEDERS
# is a FIXED module-level tuple of the 4 affiliate slugs that back it —
# milb-rookie is deliberately EXCLUDED (game-thumbs' stated feeder list is only
# the 4 affiliate levels).  Every member of this tuple is itself a key of
# _MILB_SPORT_IDS, so fetch_teams() still resolves each feeder's sportId
# through the same fixed dict-lookup gate — no user-supplied string ever
# selects a sportId, and sportId 21 (the "Minor League Baseball" decoy that
# returns Alternate Training Site rows) is never consulted.
_MILB_UMBRELLA_SLUG: Final[str] = "milb"
_MILB_UMBRELLA_FEEDERS: Final[tuple[str, ...]] = (
    "milb-aaa",
    "milb-aa",
    "milb-high-a",
    "milb-a",
)

# Fixed complex-tag dict keyed by MLB Stats API league.id (integer — immune to
# name drift).  Source: live statsapi.mlb.com query 2026-06-18.
# T-i3r-01 SSRF gate: the integer from this dict is used only in slug/alias
# derivation — never in any URL.  Mirrors _MILB_SPORT_IDS / _NCAA_SPORTBANNER_SPORTS.
_MILB_COMPLEX_TAG_IDS: Final[dict[int, str]] = {
    130: "dsl",  # Dominican Summer League
    121: "acl",  # Arizona Complex League
    124: "fcl",  # Florida Complex League
}

# Leading tokens the MLB Stats API embeds in Rookie teamName values.
# Used by _derive_rookie_slug to strip the prefix before slug derivation
# and by the D-04 name-sniff fallback when league.id is absent.
_COMPLEX_PREFIXES: Final[tuple[str, ...]] = ("DSL ", "ACL ", "FCL ")

# MiLB league shield (center "VS" slot). The MLB Stats API exposes no per-affiliate
# league logo, so every MiLB level shares the single MiLB-wide mark — matching
# game-thumbs' "use a higher-level shield" approach (D-23 reopened, user-approved
# 2026-06-18). These are FIXED relative paths appended to the constant
# settings.mlb_logos_base_url — no user/API string reaches the URL (SSRF-safe;
# the _MILB_SPORT_IDS-membership gate still applies upstream).
_MILB_SHIELD_LIGHT_PATH: Final[str] = "league-on-light/milb.svg"
_MILB_SHIELD_DARK_PATH: Final[str] = "league-on-dark/milb.svg"


def _derive_mlb_slug(location_name: str, team_name: str) -> str:
    """Derive a kebab-case slug from MLB locationName + teamName.

    Verified zero intra-level collisions across all 4 levels (120 teams).
    Mirrors the normalize style used in seed.py::normalize_input for
    consistency — but produces hyphen-separated (slug) not stripped (alias).
    """
    raw = f"{location_name} {team_name}".lower()
    return re.sub(r"[^a-z0-9]+", "-", raw).strip("-")


def _derive_rookie_slug(tag: str, team_name: str) -> str:
    """Derive slug for a Rookie complex team: ``{tag}-{stripped_teamName}``.

    Strips the leading DSL/ACL/FCL token from teamName (which the MLB Stats
    API already embeds) then prepends the canonical tag from
    ``_MILB_COMPLEX_TAG_IDS``.  Verified zero slug collisions across all 81
    Rookie teams (2026-06-18).

    Examples::

        _derive_rookie_slug("dsl", "DSL CLE Goryl")  -> "dsl-cle-goryl"
        _derive_rookie_slug("acl", "ACL Angels")      -> "acl-angels"
        _derive_rookie_slug("fcl", "FCL Rays")        -> "fcl-rays"
    """
    stripped = team_name
    for pfx in _COMPLEX_PREFIXES:
        if team_name.startswith(pfx):
            stripped = team_name[len(pfx) :]
            break
    raw = f"{tag}-{stripped}".lower()
    return re.sub(r"[^a-z0-9]+", "-", raw).strip("-")


async def _extract_team_colors(
    client: httpx.AsyncClient,
    svg_url: str,
    semaphore: asyncio.Semaphore,
    team_slug: str,
) -> tuple[str | None, str | None]:
    """Fetch SVG mark bytes, rasterize once off the event loop, and extract palette.

    This is the "rasterize-once" seam: the same SVG URL is used for both
    logo_url (seed pre-warm path) and palette extraction (D-19/D-20).  Two
    fetches per team during seed is the accepted minor overhead (Pitfall 7).

    On any failure (network error, rasterisation error, palette error) returns
    ``(None, None)`` so the team still seeds with the neutral-grey fallback
    (MILB-05) and the whole league is never aborted.

    Args:
        client:    Shared ``httpx.AsyncClient`` (D-02).
        svg_url:   The team's SVG primary-mark URL.
        semaphore: Shared concurrency limiter (mirrors seed.run() semaphore).
        team_slug: For log context only.

    Returns:
        ``(primary_hex, secondary_hex)`` bare 6-digit lowercase hex strings, or
        ``(None, None)`` on any error.
    """
    try:
        # Lazy import: svg.py imports cairosvg at module level which raises OSError
        # when libcairo2 is absent (not an ImportError — normal skipif pattern).
        # Deferring to call-time means the provider still loads and list_leagues()
        # / the registry work fine; only palette extraction is skipped locally.
        from ..svg import rasterize_svg_to_square_png

        svg_bytes = await fetch_logo_bytes(
            client, svg_url, semaphore, settings.espn_jitter_max
        )
        # Rasterise off the event loop (Pitfall 1 — cairosvg is CPU-bound).
        # functools.partial binds svg_bytes so the callable takes no args.
        png_bytes: bytes = await anyio.to_thread.run_sync(
            functools.partial(rasterize_svg_to_square_png, svg_bytes)
        )
        logo_img = Image.open(io.BytesIO(png_bytes)).convert("RGBA")
        primary_hex, secondary_hex = extract_palette(logo_img)
        return primary_hex, secondary_hex
    except Exception as exc:
        # aerror (not awarning) so transient mlbstatic.com CDN flakiness during
        # seed is not lost in log noise (WR-07).  Read-timeout retry recovery is
        # intentionally out of scope here: it is handled only by the shared
        # transport's connection retries (AsyncHTTPTransport(retries=…)); we do
        # NOT broaden the shared espn/client.py tenacity predicate (Phase-15 scope).
        await logger.aerror(
            "mlb_palette_extraction_failed",
            url=svg_url,
            team=team_slug,
            error=str(exc),
        )
        return None, None


class MLBStatsProvider:
    """Concrete DataProvider for the free, key-free MLB Stats API.

    Structurally satisfies the ``DataProvider`` Protocol (D-01) — no
    inheritance required.  Stateless and cheap; one shared singleton in
    ``providers/registry.py``.

    The shared ``httpx.AsyncClient`` is always passed as a parameter (D-02).

    Attribute ``provider_name`` is read by seed.py to populate the
    ``teams.provider`` discriminator column (Pitfall 3 / RESEARCH open question 1).
    """

    provider_name: str = "mlb"

    def list_leagues(self) -> list[str]:
        """Return the 8 MiLB slugs this provider covers.

        The 7 direct sportId slugs (_MILB_SPORT_IDS) plus the logical "milb"
        umbrella (_MILB_UMBRELLA_SLUG), which has no sportId of its own.
        """
        return [*_MILB_SPORT_IDS, _MILB_UMBRELLA_SLUG]

    async def fetch_teams(
        self,
        client: httpx.AsyncClient,
        league_slug: str,
    ) -> list[ProviderTeam]:
        """Fetch active MiLB teams for a level and return canonical ProviderTeam models.

        Each team's SVG mark is fetched once (rasterize-once, D-19/D-20) to
        derive palette colors.  A per-league semaphore mirrors seed.run()'s
        concurrency control.  Per-team SVG fetch failures set colors to None
        (MILB-05) without aborting the league.

        Args:
            client:      Shared ``httpx.AsyncClient`` (D-02).
            league_slug: One of the 8 supported slugs (KNOWN_LEAGUES gate applied
                         upstream by seed.py before this call) — the 7
                         ``_MILB_SPORT_IDS`` slugs, or the ``"milb"`` umbrella.

        Returns:
            List of active ``ProviderTeam`` instances (active=True only).  For
            the ``"milb"`` umbrella, the concatenated union of all 4 feeder
            levels (120 teams) — rookie is excluded by design.

        Raises:
            KeyError: if ``league_slug`` is not in ``_MILB_SPORT_IDS`` and is
                not the umbrella slug (SSRF gate).
            httpx.HTTPStatusError: on MLB Stats API 4xx/5xx.
            pydantic.ValidationError: on unexpected MLB API response schema drift.
        """
        # Umbrella branch — FIRST statement, before the _MILB_SPORT_IDS lookup
        # (T-ia6-01/02): "milb" is a logical union with no sportId of its own.
        # Feeders are awaited sequentially (not gathered concurrently) because
        # each feeder already fans its own ~30 palette extractions out under
        # its own semaphore — concurrent feeders would multiply in-flight CDN
        # fetches by 4 and defeat espn_semaphore_size (T-ia6-04).
        if league_slug == _MILB_UMBRELLA_SLUG:
            umbrella_teams: list[ProviderTeam] = []
            for feeder in _MILB_UMBRELLA_FEEDERS:
                umbrella_teams.extend(await self.fetch_teams(client, feeder))
            await logger.ainfo(
                "milb_umbrella_fanout",
                feeders=list(_MILB_UMBRELLA_FEEDERS),
                team_count=len(umbrella_teams),
            )
            return umbrella_teams

        # KeyError on unknown slug (SSRF gate — never build URL before this lookup)
        sport_id = _MILB_SPORT_IDS[league_slug]

        # Gate the Rookie complex-tag branch on slug identity (D-13 / Pitfall 6).
        # All non-Rookie slug paths are byte-identical to before — _MILB_COMPLEX_TAG_IDS
        # is NEVER called for non-Rookie entries (those leagues also have league.id
        # fields but their IDs are outside the dict; calling .get() on them would
        # return None and silently skip every AAA team).
        is_rookie = league_slug == "milb-rookie"

        response = await fetch_mlb_teams(
            client, settings.mlb_statsapi_base_url, sport_id
        )

        # Semaphore mirrors seed.run()'s espn_semaphore_size (D-08 pattern).  It
        # is only meaningful if the palette extractions actually run
        # concurrently (WR-06) — a sequential `for ... await` loop never
        # contends it.  Below we gather the per-team extractions so the limiter
        # genuinely bounds in-flight SVG fetches.
        semaphore = asyncio.Semaphore(settings.espn_semaphore_size)

        all_entries = [e for e in response.teams if e.active]

        # Derive slugs + tags in order FIRST (WR-02 intra-batch collision guard).
        # The team upsert is ON CONFLICT (league_id, slug) DO UPDATE, so two teams
        # deriving the same slug would silently overwrite each other in `teams`.
        # For Rookie: derive the complex tag via _MILB_COMPLEX_TAG_IDS[league.id]
        # (T-i3r-01 gate), then build the slug with _derive_rookie_slug.
        # Skipped Rookie entries (unknown complex) are excluded from kept_entries,
        # slugs, and tags so asyncio.gather and the final zip stay index-aligned.
        kept_entries = []
        slugs: list[str] = []
        tags: list[str | None] = []
        seen_slugs: dict[str, str] = {}
        for entry in all_entries:
            if is_rookie:
                # Primary: derive tag from the fixed league.id dict (T-i3r-01)
                tag: str | None = _MILB_COMPLEX_TAG_IDS.get(
                    entry.league.id if entry.league is not None else -1
                )
                if tag is None:
                    # D-04 fallback: detect leading DSL/ACL/FCL token in teamName
                    for pfx in _COMPLEX_PREFIXES:
                        if entry.teamName.startswith(pfx):
                            tag = pfx.strip().lower()
                            break
                if tag is None:
                    # Unknown complex — skip this entry; never guess, never crash (D-04)
                    await logger.awarning(
                        "milb_rookie_unknown_complex",
                        team_id=entry.id,
                        team_name=entry.teamName,
                    )
                    continue
                slug = _derive_rookie_slug(tag, entry.teamName)
            else:
                tag = None
                slug = _derive_mlb_slug(entry.locationName, entry.teamName)

            if slug in seen_slugs:
                await logger.awarning(
                    "milb_slug_collision",
                    slug=slug,
                    league=league_slug,
                    existing=seen_slugs[slug],
                    colliding=str(entry.id),
                )
            seen_slugs[slug] = str(entry.id)
            kept_entries.append(entry)
            slugs.append(slug)
            tags.append(tag)

        # Rasterize-once, CONCURRENTLY (WR-06): dispatch every team's palette
        # extraction at once and let the shared semaphore (above) bound how many
        # SVG fetches are in flight.  asyncio.gather preserves input order, so
        # `colors[i]` lines up with `kept_entries[i]`.  Each task degrades to
        # (None, None) internally on any error (MILB-05) — never aborts the
        # league — so gather never raises here.
        colors = await asyncio.gather(
            *(
                _extract_team_colors(
                    client,
                    f"{settings.mlb_logos_base_url}/{entry.id}.svg",
                    semaphore,
                    slug,
                )
                for entry, slug in zip(kept_entries, slugs, strict=True)
            )
        )

        teams: list[ProviderTeam] = []
        for entry, slug, tag, (primary_color, secondary_color) in zip(
            kept_entries, slugs, tags, colors, strict=True
        ):
            svg_url = f"{settings.mlb_logos_base_url}/{entry.id}.svg"
            spot_url = f"{settings.mlb_spots_base_url}/v1/team/{entry.id}/spots/500"

            # Build prefixed alias variants for Rookie teams (D-06 / D-07).
            # seed.py loops generate_aliases(team) + team.extra_aliases, so these
            # arrive in addition to the standard alias set.  Bare locationName
            # aliases (e.g. "Boca Chica") will still be generated and may CONFLICT
            # on the 28 city-sharing cases — handled by ON CONFLICT DO NOTHING.
            extra_aliases: list[str] = []
            if is_rookie and tag is not None:
                stripped = entry.teamName
                for pfx in _COMPLEX_PREFIXES:
                    if stripped.startswith(pfx):
                        stripped = stripped[len(pfx) :]
                        break
                extra_aliases = [
                    f"{tag} {stripped}".lower(),  # e.g. "dsl cle goryl"
                    f"{tag}-{stripped}".lower(),  # e.g. "dsl-cle-goryl" (same as slug)
                ]

            teams.append(
                ProviderTeam(
                    provider_id=str(entry.id),  # int → str (Pitfall 8)
                    slug=slug,
                    display_name=entry.name,
                    abbreviation=entry.abbreviation,
                    short_display_name=entry.teamName,  # mascot (Pitfall 7)
                    location=entry.locationName,
                    name=entry.teamName,  # mascot (Pitfall 7)
                    primary_color=primary_color,  # D-20: palette-extracted bare hex
                    secondary_color=secondary_color,
                    logo_url=svg_url,  # D-19: SVG primary mark
                    logo_variants={
                        "spot": spot_url,  # D-21: spot PNG for provenance
                        "svg": svg_url,  # D-21: SVG URL for provenance
                    },
                    is_active=entry.active,
                    extra_aliases=extra_aliases,  # D-06/D-07: Rookie prefixed variants
                )
            )

        return teams

    async def fetch_league_shield(
        self,
        client: httpx.AsyncClient,
        league_slug: str,
    ) -> ProviderLogoShield:
        """Return the shared MiLB league shield for any affiliate level (D-23).

        The MLB Stats API has no per-affiliate league logo, so all MiLB levels use
        the single MiLB-wide mark fetched from the MLB CDN
        (``{mlb_logos_base_url}/league-on-light/milb.svg`` + ``-dark`` variant).
        The SVG is rasterized to PNG off the event loop (cairosvg is CPU-bound) so
        seed.py can warm ``leaguelogo:{level}:{variant}`` with Pillow-readable bytes.

        SSRF: the URL is built only from the constant ``settings.mlb_logos_base_url``
        plus the fixed ``_MILB_SHIELD_*_PATH`` constants — no user/API string reaches
        it. On any fetch/rasterize failure returns an empty shield so seed falls back
        to the placeholder (degrade, never crash).
        """
        light_url = f"{settings.mlb_logos_base_url}/{_MILB_SHIELD_LIGHT_PATH}"
        dark_url = f"{settings.mlb_logos_base_url}/{_MILB_SHIELD_DARK_PATH}"
        semaphore = asyncio.Semaphore(settings.espn_semaphore_size)

        async def _fetch_raster(url: str) -> bytes | None:
            try:
                # Lazy import: cairosvg raises OSError without libcairo2 (skipif
                # pattern); deferring keeps the provider importable regardless.
                from ..svg import rasterize_svg_if_needed

                raw = await fetch_logo_bytes(
                    client, url, semaphore, settings.espn_jitter_max
                )
                # Rasterize the (wide, aspect-preserved) MiLB mark off the event loop.
                return await anyio.to_thread.run_sync(rasterize_svg_if_needed, raw)
            except Exception as exc:
                # aerror (not awarning) so transient MLB CDN flakiness during
                # seed is not lost in noise (WR-07).  Transport-timeout retry
                # recovery is intentionally out of scope (handled only by the
                # shared transport's connection retries; we do not broaden the
                # shared espn/client.py tenacity predicate).
                await logger.aerror(
                    "mlb_league_shield_fetch_failed",
                    league=league_slug,
                    url=url,
                    error=str(exc),
                )
                return None

        bytes_default = await _fetch_raster(light_url)
        bytes_dark = await _fetch_raster(dark_url)

        if bytes_default is None:
            # No usable shield — empty shield → seed warms the placeholder (no crash).
            await logger.awarning("mlb_league_shield_unavailable", league=league_slug)
            return ProviderLogoShield(
                logo_url=None, variant_map={}, bytes_default=None, bytes_dark=None
            )

        await logger.adebug("mlb_league_shield_ok", league=league_slug, url=light_url)
        return ProviderLogoShield(
            logo_url=light_url,
            variant_map={"default": light_url, "dark": dark_url},
            bytes_default=bytes_default,
            bytes_dark=bytes_dark,
        )
