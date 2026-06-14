"""Bundled static assets for matchup-thumbs."""

from __future__ import annotations

import io
from importlib.resources import files

from PIL import ImageFont


def get_placeholder_logo() -> bytes:
    """Return the bundled neutral placeholder logo bytes.

    This is the terminal fallback in the D-10 / ESPN-02 logo fallback chain:
    primary ESPN CDN logo → alternate ESPN variant → this placeholder.
    Loaded from the package via importlib.resources so it ships correctly
    in both editable installs and built wheels.
    """
    return files("matchup_thumbs.assets").joinpath("placeholder_logo.png").read_bytes()


# Module-level: read TTF bytes once at import time (RESEARCH.md Pattern 6).
# The bytes are reused across all _load_font() calls; each call wraps them
# in a new BytesIO because FreeType seeks the stream.
_FONT_BYTES: bytes = (
    files("matchup_thumbs.assets").joinpath("BarlowCondensed-Bold.ttf").read_bytes()
)


def _load_font(size: int) -> ImageFont.FreeTypeFont:
    """Return a FreeTypeFont at the given pixel size from the vendored TTF.

    Creates a new BytesIO per call — FreeType seeks the stream during
    font initialisation.  The underlying bytes are read once at module
    import and reused without copying.
    """
    return ImageFont.truetype(io.BytesIO(_FONT_BYTES), size=size)
