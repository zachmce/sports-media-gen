"""League, sport, and team listing routes."""

from fastapi import APIRouter, HTTPException, Request
from psycopg import rows as pg_rows
from pydantic import BaseModel

router = APIRouter()


class LeagueInSport(BaseModel):
    """Nested league entry within a SportResponse (SPORT-03, D-01)."""

    slug: str
    display_name: str


class SportResponse(BaseModel):
    """Response model for a single sport with its member leagues (SPORT-03, D-01)."""

    slug: str
    display_name: str
    leagues: list[LeagueInSport]


class LeagueResponse(BaseModel):
    """Response model for a single league entry (API-04)."""

    slug: str
    display_name: str
    sport: str


class TeamResponse(BaseModel):
    """Response model for a single team entry (API-03)."""

    slug: str
    display_name: str
    abbreviation: str
    aliases: list[str]


@router.get("/sports", response_model=list[SportResponse])
async def list_sports(request: Request) -> list[SportResponse]:
    """Return all canonical sports, each with a nested list of its leagues.

    Sports are ordered by sport slug; leagues within each sport are ordered by
    league slug.  A sport with zero leagues (LEFT JOIN all-NULL row) returns
    ``leagues: []``.  Uses the shared psycopg3 pool — no ORM (criterion 3,
    D-06).  Public read-only registry endpoint (SPORT-03).
    """
    pool = request.app.state.db_pool
    async with pool.connection() as conn:
        conn.row_factory = pg_rows.dict_row
        async with conn.cursor() as cur:
            await cur.execute(
                """
                SELECT sports.slug        AS sport_slug,
                       sports.display_name AS sport_display_name,
                       leagues.slug        AS league_slug,
                       leagues.display_name AS league_display_name
                FROM sports
                LEFT JOIN leagues ON leagues.sport_id = sports.id
                ORDER BY sports.slug, leagues.slug
                """
            )
            rows = await cur.fetchall()

    # Group flat (sport, league) rows into the nested SportResponse shape.
    # dict preserves insertion order (Python 3.7+), giving us sports in the
    # same order the DB returned them (ORDER BY sports.slug).
    sports_map: dict[str, SportResponse] = {}
    for row in rows:
        sport_slug: str = row["sport_slug"]
        if sport_slug not in sports_map:
            sports_map[sport_slug] = SportResponse(
                slug=sport_slug,
                display_name=row["sport_display_name"],
                leagues=[],
            )
        # LEFT JOIN: when a sport has zero leagues every league column is NULL.
        # Gate the append to avoid a ValidationError on a null slug (D-05,
        # RESEARCH Pitfall 2).
        if row["league_slug"] is not None:
            sports_map[sport_slug].leagues.append(
                LeagueInSport(
                    slug=row["league_slug"],
                    display_name=row["league_display_name"],
                )
            )
    return list(sports_map.values())


@router.get("/leagues", response_model=list[LeagueResponse])
async def list_leagues(request: Request) -> list[LeagueResponse]:
    """Return all supported leagues ordered by slug.

    The ``sport`` field is sourced from the ``leagues.sport_id → sports.slug``
    FK join (SPORT-04, D-07) rather than the legacy flat ``leagues.sport`` text
    column.  The ``LeagueResponse`` shape (slug, display_name, sport) is
    unchanged.  No authentication required — public read-only registry (API-04).
    """
    pool = request.app.state.db_pool
    async with pool.connection() as conn:
        conn.row_factory = pg_rows.dict_row
        async with conn.cursor() as cur:
            await cur.execute(
                """
                SELECT l.slug, l.display_name, s.slug AS sport
                FROM leagues l
                JOIN sports s ON s.id = l.sport_id
                ORDER BY l.slug
                """
            )
            rows = await cur.fetchall()
    return [LeagueResponse(**r) for r in rows]


@router.get("/{league}/teams", response_model=list[TeamResponse])
async def list_teams(league: str, request: Request) -> list[TeamResponse]:
    """Return all teams for a given league with their aliases.

    The ``league`` path segment is a parameterized lookup — never interpolated
    into SQL (T-02-12 mitigation).  An unknown league slug returns 404 with a
    structured JSON body naming the league (T-02-13 mitigation).

    Teams are returned ordered by display_name.  Aliases are aggregated via
    ``array_agg`` and sorted alphabetically.  Teams with no aliases yet have an
    empty list (LEFT JOIN coalesces ``[None]`` to ``[]``).
    """
    pool = request.app.state.db_pool
    async with pool.connection() as conn:
        conn.row_factory = pg_rows.dict_row
        async with conn.cursor() as cur:
            # Validate league exists — parameterized (T-02-12)
            await cur.execute("SELECT id FROM leagues WHERE slug = %s", (league,))
            if await cur.fetchone() is None:
                raise HTTPException(
                    status_code=404,
                    detail={"league": league, "error": "unknown league"},
                )

            # Fetch teams with aggregated aliases (LEFT JOIN → teams with zero
            # aliases produce [None]; filtered out below)
            await cur.execute(
                """
                SELECT t.slug, t.display_name, t.abbreviation,
                       array_agg(ta.alias ORDER BY ta.alias) AS aliases
                FROM teams t
                JOIN leagues l ON l.id = t.league_id
                LEFT JOIN team_aliases ta ON ta.team_id = t.id
                WHERE l.slug = %s
                GROUP BY t.id
                ORDER BY t.display_name
                """,
                (league,),
            )
            rows = await cur.fetchall()

    result: list[TeamResponse] = []
    for row in rows:
        # LEFT JOIN produces [None] for teams with zero aliases; coerce to []
        raw_aliases: list[str | None] = row["aliases"] or []
        aliases = [a for a in raw_aliases if a is not None]
        result.append(
            TeamResponse(
                slug=row["slug"],
                display_name=row["display_name"],
                abbreviation=row["abbreviation"],
                aliases=aliases,
            )
        )
    return result
