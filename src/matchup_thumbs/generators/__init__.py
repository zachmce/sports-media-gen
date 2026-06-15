"""Generator registry and public types for matchup-thumbs.

This package provides:
- ``TeamDict`` / ``DecodedAssets`` TypedDicts (contracts for generator functions)
- ``GeneratorFn`` type alias for the pure ``(away, home, assets) -> Image`` protocol
- ``register`` / ``get_generator`` for the ``(kind, style)`` registry (GEN-05)
- Pure generator functions for all three image kinds at ``style=0``
  (GEN-01/GEN-02/GEN-03), registered via side-effect imports below.

Importing this package is sufficient to populate the registry:

    from matchup_thumbs.generators import get_generator
    fn = get_generator("thumb", 0)   # generate_thumb_style0
    fn = get_generator("bogus", 0)   # None → 400 (GEN-07)
"""

from __future__ import annotations

# Side-effect imports: each module decorates its function with @register so that
# importing this package populates _REGISTRY for all style=0 generators.
from . import logo, poster, thumb  # noqa: F401
from .registry import GeneratorFn, get_generator, register, registered_kinds
from .types import DecodedAssets, TeamDict

__all__ = [
    "DecodedAssets",
    "GeneratorFn",
    "TeamDict",
    "get_generator",
    "register",
    "registered_kinds",
]
