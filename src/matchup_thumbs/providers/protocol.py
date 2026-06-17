"""DataProvider structural typing.Protocol.

Defines the contract that all concrete providers must satisfy.  Uses
``typing.Protocol`` (D-01) so implementations need NOT inherit from this class —
structural compatibility is checked at call sites where ``DataProvider`` is used
as a type annotation (mypy validates this at assignment / call time).

Import guard: this module MUST NOT import from ``seed.py``, ``resolver.py``, or
``espn/client.py``.  Its only non-stdlib/non-httpx import is from ``.types``.
"""

from __future__ import annotations

from typing import Protocol

import httpx

from .types import ProviderLogoShield, ProviderTeam


class DataProvider(Protocol):
    """Structural protocol for a provider that supplies team and league data.

    Implementations need not inherit from this class — structural compatibility
    is checked at call sites where DataProvider is used as a type annotation.

    Methods are async and accept the shared ``httpx.AsyncClient`` as a parameter
    (D-02).  Providers do not own or create their own client; the caller (seed.py)
    creates the client in ``_amain()`` and passes it down.

    Method surface (D-03):
    - ``list_leagues`` — synchronous; returns the slugs this provider covers.
    - ``fetch_teams``  — async; returns provider-neutral canonical team models.
    - ``fetch_league_shield`` — async; returns provider-neutral shield data including
      pre-fetched bytes so seed.py stays provider-neutral.
    """

    def list_leagues(self) -> list[str]: ...

    async def fetch_teams(
        self, client: httpx.AsyncClient, league_slug: str
    ) -> list[ProviderTeam]: ...

    async def fetch_league_shield(
        self, client: httpx.AsyncClient, league_slug: str
    ) -> ProviderLogoShield: ...
