"""League and team listing routes."""

from fastapi import APIRouter, HTTPException, Request
from psycopg import rows as pg_rows
from pydantic import BaseModel

router = APIRouter()


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


@router.get("/leagues", response_model=list[LeagueResponse])
async def list_leagues(request: Request) -> list[LeagueResponse]:
    """Return all supported leagues ordered by slug.

    Reads from the ``leagues`` table via the shared psycopg3 pool on
    ``request.app.state.db_pool``.  No authentication required — this is a
    public read-only registry listing (API-04).
    """
    pool = request.app.state.db_pool
    async with pool.connection() as conn:
        conn.row_factory = pg_rows.dict_row
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT slug, display_name, sport FROM leagues ORDER BY slug"
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
