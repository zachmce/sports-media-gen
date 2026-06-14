"""Shared colour helpers for matchup-thumbs generators.

Centralises the D-15 grey fallback constants and the ``hex_to_rgb`` conversion
so that all generator modules share one implementation (WR-05 / CR-03 fix).
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# D-15 colour fallback constants
# Primary  #3A3A3A = (58, 58, 58)
# Secondary #6E6E6E = (110, 110, 110)
# ---------------------------------------------------------------------------

NULL_PRIMARY: tuple[int, int, int] = (58, 58, 58)
NULL_SECONDARY: tuple[int, int, int] = (110, 110, 110)


def hex_to_rgb(
    hex_color: str | None,
    fallback: tuple[int, int, int],
) -> tuple[int, int, int]:
    """Convert a ``#RRGGBB`` string to an RGB 3-tuple.

    Returns *fallback* for any of these degenerate cases (CR-03, D-15):
    - ``hex_color`` is ``None`` or empty string
    - value is not exactly 6 hex digits after stripping the leading ``#``
      (e.g. CSS shorthand ``"#abc"``, bare ``"#"``, or non-hex characters)

    This makes the generator a total function over ``str | None`` so a
    malformed ESPN color string never crashes the render thread (CR-03).
    """
    if not hex_color:
        return fallback
    h = hex_color.lstrip("#")
    if len(h) != 6:
        return fallback
    try:
        return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))
    except ValueError:
        return fallback
