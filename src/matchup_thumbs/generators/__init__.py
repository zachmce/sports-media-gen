"""Generator registry and public types for matchup-thumbs.

This package provides:
- ``TeamDict`` / ``DecodedAssets`` TypedDicts (contracts for generator functions)
- The ``(kind, style)`` registry with ``register`` / ``get_generator``
- Pure generator functions for all three image kinds (style=0)

Full re-exports are wired in Plan 02 once the registry and generator
modules are implemented.  This placeholder makes the package importable
during Wave 0 so test scaffolding can be created without errors.
"""
