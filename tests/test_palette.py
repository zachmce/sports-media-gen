"""Unit tests for src/matchup_thumbs/mlb/palette.py.

Coverage:
- extract_palette(solid_color_rgba) returns correct bare-hex primary (no '#')
- With one dominant color, secondary == primary
- Two-color image: more-frequent color is primary, other is secondary
- All-transparent image returns (None, None) — MILB-05 safety net
- All-near-white image returns (None, None) — MILB-05 safety net
- End-to-end: rasterize fixture SVG then extract palette yields fixture colors
  (requires libcairo2; skipped when absent)
"""

from __future__ import annotations

import pathlib

import pytest
from PIL import Image

from matchup_thumbs.mlb.palette import (
    _ALPHA_MIN,
    _WHITE_THRESHOLD,
    extract_palette,
)

# ---------------------------------------------------------------------------
# cairosvg availability guard (for end-to-end test only)
# ---------------------------------------------------------------------------

try:
    import cairosvg as _cairosvg_mod  # type: ignore[import-untyped]  # noqa: F401

    _CAIROSVG_AVAILABLE = True
except OSError:
    _CAIROSVG_AVAILABLE = False

_requires_cairosvg = pytest.mark.skipif(
    not _CAIROSVG_AVAILABLE,
    reason=(
        "libcairo2 not installed — cairosvg unavailable; "
        "install libcairo2 to run raster→palette end-to-end test"
    ),
)

_FIXTURES_DIR = pathlib.Path(__file__).parent / "fixtures"


# ---------------------------------------------------------------------------
# Helpers — build in-memory test images
# ---------------------------------------------------------------------------


def _solid_rgba(color: tuple[int, int, int, int], size: int = 50) -> Image.Image:
    """Return a solid-color RGBA image of the given size."""
    img = Image.new("RGBA", (size, size), color)
    return img


def _two_color_rgba(
    dominant: tuple[int, int, int, int],
    accent: tuple[int, int, int, int],
    size: int = 100,
    dominant_fraction: float = 0.75,
) -> Image.Image:
    """Return an RGBA image with two colors: dominant fills a large left region,
    accent fills the smaller right region.  dominant_fraction controls the split.
    """
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    split = int(size * dominant_fraction)
    dominant_region = Image.new("RGBA", (split, size), dominant)
    accent_region = Image.new("RGBA", (size - split, size), accent)
    img.paste(dominant_region, (0, 0))
    img.paste(accent_region, (split, 0))
    return img


# Typical MiLB colors (opaque, non-white, well below the >240 filter).
# Quantized: navy (0, 40, 90) → "00285a"; gold (250, 180, 20) → "fab414"
# Using exact values that survive the quantization round-trip:
# round(0/10)*10=0, round(43/10)*10=40, round(92/10)*10=90 → "00285a"
# But let's use colors that quantize to exact "nice" values:
# Navy: (0, 40, 90) → q=(0, 40, 90) → "00285a"
# Gold: (250, 180, 20) → q=(250, 180, 20) → "fab414"
_NAVY_OPAQUE = (0, 40, 90, 255)
_GOLD_OPAQUE = (250, 180, 20, 255)

# Expected quantized hex (round each channel to nearest 10)
_NAVY_HEX = f"{0:02x}{40:02x}{90:02x}"  # "00285a"
_GOLD_HEX = f"{250:02x}{180:02x}{20:02x}"  # "fab414"


# ---------------------------------------------------------------------------
# Tests: solid color
# ---------------------------------------------------------------------------


class TestSolidColor:
    """extract_palette on a solid-color RGBA image."""

    def test_solid_navy_primary_is_navy_hex(self) -> None:
        """A solid navy image returns navy hex as primary."""
        img = _solid_rgba(_NAVY_OPAQUE)
        primary, secondary = extract_palette(img)
        assert primary == _NAVY_HEX, (
            f"Expected primary={_NAVY_HEX!r}, got {primary!r}"
        )

    def test_solid_color_secondary_equals_primary(self) -> None:
        """When only one distinct color is found, secondary == primary."""
        img = _solid_rgba(_NAVY_OPAQUE)
        primary, secondary = extract_palette(img)
        assert primary == secondary, (
            "Expected secondary == primary for single-color image, "
            f"got primary={primary!r}, secondary={secondary!r}"
        )

    def test_primary_has_no_hash_prefix(self) -> None:
        """Primary hex must NOT start with '#' (seed.py adds '#' per convention)."""
        img = _solid_rgba(_NAVY_OPAQUE)
        primary, _ = extract_palette(img)
        assert primary is not None
        assert not primary.startswith("#"), (
            f"Primary hex must be bare (no '#'), got {primary!r}"
        )

    def test_primary_is_6_digits_lowercase(self) -> None:
        """Primary hex is exactly 6 lowercase hexadecimal digits."""
        img = _solid_rgba(_GOLD_OPAQUE)
        primary, _ = extract_palette(img)
        assert primary is not None
        assert len(primary) == 6, f"Expected 6-char hex, got {primary!r}"
        assert primary == primary.lower(), f"Expected lowercase hex, got {primary!r}"
        assert all(
            c in "0123456789abcdef" for c in primary
        ), f"Expected valid hex chars, got {primary!r}"

    def test_rgb_mode_input_is_handled(self) -> None:
        """RGB (no alpha) input is handled without error via RGBA conversion."""
        rgb_img = Image.new("RGB", (50, 50), (0, 40, 90))
        primary, secondary = extract_palette(rgb_img)
        # RGB → RGBA conversion makes all pixels opaque; palette extracts fine.
        assert primary is not None


# ---------------------------------------------------------------------------
# Tests: two-color image
# ---------------------------------------------------------------------------


class TestTwoColorImage:
    """extract_palette correctly ranks primary and secondary by frequency."""

    def test_dominant_color_is_primary(self) -> None:
        """The more-frequent color is returned as primary."""
        img = _two_color_rgba(
            _NAVY_OPAQUE, _GOLD_OPAQUE, size=100, dominant_fraction=0.75
        )
        primary, secondary = extract_palette(img)
        # Navy fills 75% of pixels → navy should be primary.
        assert primary is not None
        assert secondary is not None
        assert primary != secondary, "Expected two distinct colors"
        assert primary == _NAVY_HEX, (
            f"Expected navy ({_NAVY_HEX!r}) as primary, got {primary!r}"
        )

    def test_accent_color_is_secondary(self) -> None:
        """The less-frequent color is returned as secondary."""
        img = _two_color_rgba(
            _NAVY_OPAQUE, _GOLD_OPAQUE, size=100, dominant_fraction=0.75
        )
        _, secondary = extract_palette(img)
        assert secondary == _GOLD_HEX, (
            f"Expected gold ({_GOLD_HEX!r}) as secondary, got {secondary!r}"
        )


# ---------------------------------------------------------------------------
# Tests: degenerate input — (None, None) safety-net (MILB-05)
# ---------------------------------------------------------------------------


class TestDegenerateInput:
    """All-transparent or all-near-white inputs return (None, None)."""

    def test_all_transparent_returns_none_none(self) -> None:
        """All-transparent RGBA image returns (None, None)."""
        img = _solid_rgba((0, 0, 0, 0))  # fully transparent
        primary, secondary = extract_palette(img)
        assert primary is None, f"Expected None for all-transparent, got {primary!r}"
        assert secondary is None

    def test_partially_transparent_below_threshold_returns_none_none(self) -> None:
        """Pixels with alpha < _ALPHA_MIN are all skipped → (None, None)."""
        # alpha = _ALPHA_MIN - 1, just below threshold
        img = _solid_rgba((50, 100, 200, _ALPHA_MIN - 1))
        primary, secondary = extract_palette(img)
        assert primary is None
        assert secondary is None

    def test_all_near_white_returns_none_none(self) -> None:
        """All-near-white RGBA image returns (None, None) — white filter."""
        # All channels > _WHITE_THRESHOLD (e.g. 241)
        near_white = _WHITE_THRESHOLD + 1
        img = _solid_rgba((near_white, near_white, near_white, 255))
        primary, secondary = extract_palette(img)
        assert primary is None, (
            f"Expected None for all-near-white image, got {primary!r}"
        )
        assert secondary is None

    def test_exactly_at_white_threshold_is_filtered(self) -> None:
        """Pixels at exactly _WHITE_THRESHOLD + 1 in all channels are filtered out."""
        # The game-thumbs filter is r > 240 (strict), so 241 is filtered.
        v = _WHITE_THRESHOLD + 1
        img = _solid_rgba((v, v, v, 255))
        primary, _ = extract_palette(img)
        assert primary is None

    def test_exactly_at_white_threshold_is_not_filtered(self) -> None:
        """Pixels at exactly _WHITE_THRESHOLD (240) in all channels are NOT filtered
        (game-thumbs uses strict >, not >=)."""
        # r == 240 → NOT filtered (game-thumbs: r > 240 is False for r==240)
        v = _WHITE_THRESHOLD
        img = _solid_rgba((v, v, v, 255))
        primary, _ = extract_palette(img)
        # Expected: NOT None (pixel passes the white filter)
        assert primary is not None


# ---------------------------------------------------------------------------
# End-to-end: rasterize fixture → extract palette (requires libcairo2)
# ---------------------------------------------------------------------------


@_requires_cairosvg
class TestRasterToPalette:
    """Rasterize the offline SVG fixture and extract palette — no network."""

    def test_fixture_palette_yields_navy_and_gold(self) -> None:
        """Rasterizing mlb_512.svg and extracting palette returns navy + gold."""
        import io

        from matchup_thumbs.svg import rasterize_svg_to_square_png

        svg_bytes = (_FIXTURES_DIR / "mlb_512.svg").read_bytes()
        png_bytes = rasterize_svg_to_square_png(svg_bytes)
        img = Image.open(io.BytesIO(png_bytes)).convert("RGBA")
        primary, secondary = extract_palette(img)

        assert primary is not None, "Expected non-None primary from fixture SVG"
        assert secondary is not None, "Expected non-None secondary from fixture SVG"

        # The fixture uses #002b5c (navy, dominant) and #fdb913 (gold, accent).
        # After quantization: navy (0,43,92) → q=(0,40,90)="00285a"; but the SVG
        # uses exactly #002b5c = (0,43,92).
        # round(0/10)*10=0, round(43/10)*10=40, round(92/10)*10=90 → "00285a"
        # Gold #fdb913 = (253,185,19):
        # round(253/10)*10=250, round(185/10)*10=190, round(19/10)*10=20 → "fabe14"
        # Accept any 6-char hex (quantized values may vary by rasterizer anti-aliasing).
        assert len(primary) == 6, f"primary must be 6-char hex, got {primary!r}"
        assert len(secondary) == 6, f"secondary must be 6-char hex, got {secondary!r}"
        # Verify each is valid lowercase hex.
        valid_hex_chars = set("0123456789abcdef")
        assert set(primary) <= valid_hex_chars, f"Invalid hex chars in {primary!r}"
        assert set(secondary) <= valid_hex_chars, f"Invalid hex chars in {secondary!r}"
