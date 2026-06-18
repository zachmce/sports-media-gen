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

# Transparent margin added around the rasterized mark, as a fraction of width per
# side.  MLB primary marks fill their viewBox edge-to-edge (content bbox == canvas),
# so without a margin the mark composites with straight hard-cropped edges AND the
# drop shadow (offset+blur, applied later by the generator) is clipped to a straight
# line at the raster rectangle.  ~8% per side gives the downscaled shadow room and
# keeps the mark off the edge.  ESPN PNGs skip this path entirely (no-op).
_SVG_RASTER_PAD_FRAC: float = 0.08

# Localized decompression-bomb guard (WR-05).  Both _pad_transparent and
# rasterize_svg_to_square_png Image.open() cairosvg output directly, bypassing
# the shared _MAX_LOGO_PIXELS cap in assets/loader.py.  Today this is NOT
# exploitable — cairosvg output width is pinned to _SVG_RASTER_SIZE (or the
# explicit `size` arg), so the pixel count is bounded regardless of the SVG's
# declared dimensions.  We still self-check the bound locally so the invariant
# ("never Image.open un-capped bytes") holds even if a future change feeds these
# helpers larger input.  k=3 comfortably covers the padded square path
# (size + 2*pad) and any non-square aspect raster up to ~3× the nominal side.
_SVG_BOMB_GUARD_K: int = 3
_SVG_MAX_RASTER_PIXELS: int = (_SVG_RASTER_SIZE * _SVG_BOMB_GUARD_K) ** 2


def _assert_within_raster_bound(img: Image.Image) -> None:
    """Raise if a decoded raster exceeds the _SVG_RASTER_SIZE-derived pixel cap.

    WR-05: a self-checking decompression-bomb guard local to svg.py, since these
    decodes bypass the shared _MAX_LOGO_PIXELS cap in assets/loader.py.
    """
    pixels = img.width * img.height
    if pixels > _SVG_MAX_RASTER_PIXELS:
        msg = (
            f"rasterized SVG ({img.width}x{img.height} = {pixels}px) exceeds the "
            f"render-bomb bound of {_SVG_MAX_RASTER_PIXELS}px "
            f"(_SVG_RASTER_SIZE={_SVG_RASTER_SIZE} x k={_SVG_BOMB_GUARD_K}, squared)"
        )
        raise ValueError(msg)


def _pad_transparent(png_bytes: bytes) -> bytes:
    """Re-encode a rasterized PNG with a transparent margin around its content.

    Pads by ``_SVG_RASTER_PAD_FRAC`` of the width on every side so an edge-filling
    mark gains breathing room (no hard-cropped mark edges; room for the generator's
    drop shadow).  Pure CPU work — callers already wrap rasterization off the loop.
    """
    img = Image.open(io.BytesIO(png_bytes)).convert("RGBA")
    _assert_within_raster_bound(img)  # WR-05 local decompression-bomb guard
    pad = round(img.width * _SVG_RASTER_PAD_FRAC)
    if pad <= 0:
        return png_bytes
    size = (img.width + 2 * pad, img.height + 2 * pad)
    padded = Image.new("RGBA", size, (0, 0, 0, 0))
    padded.alpha_composite(img, (pad, pad))
    buf = io.BytesIO()
    padded.save(buf, format="PNG")
    return buf.getvalue()


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
        PNG bytes (transparent background, RGBA, with a transparent margin) if
        ``raw`` was SVG; otherwise ``raw`` unchanged.
    """
    stripped = raw.lstrip()
    if stripped.startswith(b"<"):
        result: bytes = cairosvg.svg2png(
            bytestring=raw,
            output_width=_SVG_RASTER_SIZE,
            unsafe=_SVG_UNSAFE,
        )
        # Add a transparent margin so the edge-filling MLB mark isn't hard-cropped
        # and the downstream drop shadow has room to render (no straight clip).
        return _pad_transparent(result)
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
    _assert_within_raster_bound(img)  # WR-05 local decompression-bomb guard
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
