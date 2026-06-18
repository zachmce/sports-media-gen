"""Palette extraction for MiLB logos — mirrors game-thumbs extractPalette (D-20).

Pure function: ``PIL.Image`` in, ``(hex | None, hex | None)`` out.
No I/O — safe to call from a threadpool (``anyio.to_thread.run_sync``).

Algorithm:
    Mirrors ``sethwv/game-thumbs helpers/svgUtils.js :: extractPalette``
    (verified source, 2026-06-18):
    1. Resize to 200×200 LANCZOS to bound iteration time.
    2. Sample every ``_SAMPLE_STEP``-th pixel (DEFAULT_COLOR_SAMPLE_RATE=20).
    3. Skip pixels where ``alpha < _ALPHA_MIN`` (transparent).
    4. Skip pixels where all channels exceed ``_WHITE_THRESHOLD`` (near-white).
    5. Quantize each channel to the nearest multiple of ``_QUANT`` (=10).
    6. Count frequency of each quantized colour.
    7. Return the top-2 most-frequent colours as bare 6-digit lowercase hex strings
       (WITHOUT a leading '#' — seed.py normalises to '#hex' per existing convention).
    8. If fewer than 2 distinct colours are found, ``secondary = primary``.
    9. If no qualifying pixels exist (all-transparent or all-white logo), return
       ``(None, None)`` — the neutral-grey fallback in ``render.py`` handles this
       (MILB-05 safety net).
"""

from __future__ import annotations

from PIL import Image

# Mirror game-thumbs DEFAULT_COLOR_SAMPLE_RATE (sample every 20th pixel = ~5%).
_SAMPLE_STEP: int = 20

# Near-white threshold: game-thumbs skips pixels where r > 240 AND g > 240 AND b > 240.
_WHITE_THRESHOLD: int = 240

# Alpha threshold: game-thumbs skips pixels where alpha < 128 (mostly transparent).
_ALPHA_MIN: int = 128

# Quantization bucket size: round each channel to the nearest multiple of 10.
_QUANT: int = 10

# Downscale target before iteration (mirrors contrast.dominant_color pattern).
# 200×200 = 40 000 pixels max; at _SAMPLE_STEP=20 that is ~2 000 sampled pixels.
_PALETTE_SAMPLE_SIZE: int = 200


def extract_palette(img: Image.Image) -> tuple[str | None, str | None]:
    """Extract primary and alternate hex colors from a rasterized RGBA logo.

    Mirrors ``sethwv/game-thumbs svgUtils.extractPalette``:
    - Skips transparent pixels (alpha < ``_ALPHA_MIN``)
    - Skips near-white pixels (all channels > ``_WHITE_THRESHOLD``)
    - Quantizes each channel to nearest ``_QUANT``
    - Returns the two most-frequent quantized colours as hex strings
    - If only one distinct colour is found, ``secondary == primary``
    - If no qualifying pixels are found, returns ``(None, None)``

    Args:
        img: Any-mode ``PIL.Image``; converted to RGBA defensively (Pitfall 3).
             The loader guarantees RGBA from Phase 8 onward, but defensive
             conversion ensures correctness for any caller.

    Returns:
        A ``(primary_hex, secondary_hex)`` tuple where each value is a bare
        6-digit lowercase hex string WITHOUT a leading ``'#'`` (e.g. ``"002b5c"``).
        ``seed.py`` adds the ``'#'`` prefix per the existing ESPN convention.
        Both elements are ``None`` only when no opaque, non-white pixels exist
        (all-transparent or all-white logo) — triggers the neutral-grey fallback
        in ``render.py`` (MILB-05 safety net).
    """
    # Defensive RGBA conversion (Pitfall 3 — avoids ValueError on 3-tuple unpack).
    rgba = img.convert("RGBA")

    # Downscale to bound iteration time (mirrors contrast.dominant_color pattern).
    small = rgba.resize(
        (_PALETTE_SAMPLE_SIZE, _PALETTE_SAMPLE_SIZE),
        Image.Resampling.LANCZOS,
    )

    # Materialise pixels to a list for index arithmetic (Pitfall 4 — getdata()
    # returns a Pillow sequence, not a list; range-based indexing requires list).
    # cast is not needed: Pillow 12.2 getdata() on RGBA returns a sequence of
    # 4-tuples; the explicit annotation anchors the type for mypy.
    pixels: list[tuple[int, int, int, int]] = list(small.getdata())

    color_counts: dict[tuple[int, int, int], int] = {}
    for idx in range(0, len(pixels), _SAMPLE_STEP):
        r, g, b, a = pixels[idx]

        # Skip transparent pixels (game-thumbs: a < 128).
        if a < _ALPHA_MIN:
            continue

        # Skip near-white pixels (game-thumbs: r > 240 && g > 240 && b > 240).
        if r > _WHITE_THRESHOLD and g > _WHITE_THRESHOLD and b > _WHITE_THRESHOLD:
            continue

        # Quantize each channel to the nearest multiple of _QUANT, clamped to
        # 255: round(255/10)*10 == 260, which would format as the 3-hex-digit
        # "104" and yield a malformed 7-char colour (e.g. "#1045000"). Clamp so
        # every channel stays a valid 8-bit value → exactly 2 hex digits.
        qr = min(255, round(r / _QUANT) * _QUANT)
        qg = min(255, round(g / _QUANT) * _QUANT)
        qb = min(255, round(b / _QUANT) * _QUANT)

        key = (qr, qg, qb)
        color_counts[key] = color_counts.get(key, 0) + 1

    # No qualifying pixels → MILB-05 neutral-grey safety-net trigger.
    if not color_counts:
        return None, None

    # Sort by frequency descending; take top-2.
    sorted_colors = sorted(
        color_counts, key=lambda k: color_counts[k], reverse=True
    )

    def _to_hex(rgb: tuple[int, int, int]) -> str:
        r, g, b = rgb
        return f"{r:02x}{g:02x}{b:02x}"  # bare hex, no '#' — seed.py adds '#'

    primary = _to_hex(sorted_colors[0])
    secondary = _to_hex(sorted_colors[1]) if len(sorted_colors) > 1 else primary

    return primary, secondary
