"""SVG→PNG rasterizer utility for MiLB primary-mark logos (D-19).

This module provides two public functions:

- ``rasterize_svg_if_needed(raw: bytes) -> bytes``
  Pass-through for PNG/JPEG/WebP bytes (ESPN no-op — D-22); converts SVG bytes
  to a bounded-width PNG.  Safe to call on ANY logo bytes fetched from a CDN.

- ``rasterize_svg_to_square_png(svg_bytes: bytes, size: int) -> bytes``
  Rasterizes SVG to a square transparent-background PNG.  Non-square rasters
  are centred on a transparent canvas.  Used at palette-extraction time in the
  provider (D-20).

Security:
    Every ``cairosvg.svg2png`` call passes ``unsafe=False`` (cairosvg's default,
    set explicitly here to document intent) — this is cairosvg's SSRF/XXE gate
    (T-15-SVG-SSRF). In safe mode cairosvg does NOT resolve external XML entities
    and does NOT fetch external resources referenced inside the SVG (e.g.
    ``<image href="http…">`` or local ``file://`` paths), so a hostile SVG cannot
    trigger an outbound request or local-file read during rasterization. cairosvg
    additionally uses ``defusedxml`` at the parser level. (cairosvg 2.x ``svg2png``
    has no ``url_fetcher`` parameter — ``unsafe=False`` is the supported control.)

    The output width is fixed to ``_SVG_RASTER_SIZE`` — cairosvg cannot produce
    an output larger than this regardless of the SVG's declared dimensions.  This
    bounds the rasterization cost (T-15-SVG-BOMB).  The downstream
    ``_MAX_LOGO_PIXELS`` guard in ``assets/loader.py`` still applies.

Note:
    ``rasterize_svg_if_needed`` and ``rasterize_svg_to_square_png`` are
    synchronous (CPU-bound).  Async callers (seed.py, loader.py) should wrap
    them with ``anyio.to_thread.run_sync`` to avoid blocking the event loop.
    See ``15-RESEARCH-REVISION.md`` Pitfall 1 and OQ-3.
"""

from __future__ import annotations

import io

import cairosvg  # type: ignore[import-untyped]
from PIL import Image

# Fixed rasterization target width (T-15-SVG-BOMB render-bomb mitigation).
# 500 px is larger than the generator's _LOGO_SIZE=280; LANCZOS downscales cleanly.
# Callers may NOT override this bound via rasterize_svg_if_needed — use
# rasterize_svg_to_square_png(size=N) only when an explicit size is intentional.
_SVG_RASTER_SIZE: int = 500

# cairosvg SSRF/XXE gate (T-15-SVG-SSRF): safe mode blocks external entity
# resolution and external resource (network/file) fetches during rasterization.
# This is the supported control in cairosvg 2.x — there is no url_fetcher param.
_SVG_UNSAFE: bool = False


def rasterize_svg_if_needed(raw: bytes) -> bytes:
    """Rasterize SVG bytes to bounded-width PNG; pass through all other formats.

    Detects SVG by checking whether the leading bytes (after stripping ASCII
    whitespace) start with ``b"<"``.  This matches both ``<svg`` and ``<?xml``
    prefixes while correctly passing through PNG (``\\x89PNG``), JPEG
    (``\\xFF\\xD8\\xFF``), and WebP (``RIFF``) bytes unchanged (D-22 ESPN no-op).

    Security:
        - Passes ``unsafe=False`` to cairosvg so SVG-referenced external URLs and
          XML entities are never resolved/fetched (T-15-SVG-SSRF).
        - Output width is fixed to ``_SVG_RASTER_SIZE``; the caller cannot inflate
          it (T-15-SVG-BOMB).

    Args:
        raw: Raw bytes from a CDN response (any logo format).

    Returns:
        PNG bytes (transparent background, RGBA) if ``raw`` was SVG; otherwise
        ``raw`` unchanged.
    """
    stripped = raw.lstrip()
    if stripped.startswith(b"<"):
        result: bytes = cairosvg.svg2png(
            bytestring=raw,
            output_width=_SVG_RASTER_SIZE,
            unsafe=_SVG_UNSAFE,
        )
        return result
    return raw


def rasterize_svg_to_square_png(
    svg_bytes: bytes,
    size: int = _SVG_RASTER_SIZE,
) -> bytes:
    """Rasterize SVG to a square transparent-background PNG at ``size`` pixels.

    Uses the same ``unsafe=False`` SSRF mitigation as
    ``rasterize_svg_if_needed``.  If cairosvg produces a non-square raster
    (common for MLB logos whose viewBox is not 1:1), the raster is centred on
    a ``size × size`` transparent canvas so downstream code always receives a
    square RGBA image.

    This function is designed for palette-extraction use in the provider
    (D-20): pass the result to ``extract_palette`` from
    ``matchup_thumbs.mlb.palette``.

    Args:
        svg_bytes: Raw SVG bytes (must be SVG — not validated; call
            ``rasterize_svg_if_needed`` first if the format is unknown).
        size:  Target side length in pixels.  Defaults to ``_SVG_RASTER_SIZE``.

    Returns:
        PNG bytes encoding a ``size × size`` RGBA image with the rasterized SVG
        centred on a transparent background.
    """
    png_bytes: bytes = cairosvg.svg2png(
        bytestring=svg_bytes,
        output_width=size,
        unsafe=_SVG_UNSAFE,
    )
    img = Image.open(io.BytesIO(png_bytes)).convert("RGBA")
    if img.size == (size, size):
        out: Image.Image = img
    else:
        # Centre the non-square raster on a transparent square canvas.
        canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        paste_x = (size - img.width) // 2
        paste_y = (size - img.height) // 2
        canvas.paste(img, (paste_x, paste_y), img)
        out = canvas
    buf = io.BytesIO()
    out.save(buf, format="PNG")
    return buf.getvalue()
