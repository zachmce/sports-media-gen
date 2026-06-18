"""Unit tests for src/matchup_thumbs/svg.py.

Coverage:
- PNG pass-through (rasterize_svg_if_needed is a no-op for PNG/JPEG/WebP bytes — D-22)
- SVG→PNG rasterization (bytes start with PNG magic header)
- SVG detection (leading-whitespace and <?xml …><svg> prefix forms)
- SVG-SSRF blocking (T-15-SVG-SSRF): a url_fetcher blocks any non-data: URL
- Render-bomb bound (T-15-SVG-BOMB): output width == _SVG_RASTER_SIZE

Tests that require the cairosvg runtime (i.e. libcairo2.so.2) are skipped
automatically when the library is not available locally.  All rasterize_*
tests are in this category.  The pass-through test and the url_fetcher
isolation test do NOT require cairosvg to be importable.
"""

from __future__ import annotations

import io
import pathlib
import sys
import types

import pytest

# ---------------------------------------------------------------------------
# Availability guard
# ---------------------------------------------------------------------------
# cairosvg raises OSError (not ImportError) when libcairo2.so.2 is absent.
# pytest.importorskip only catches ImportError, so we use a manual check.
# We use a module-level flag so individual tests can skip cleanly.

try:
    import cairosvg as _cairosvg_module  # type: ignore[import-untyped]  # noqa: F401

    _CAIROSVG_AVAILABLE = True
except OSError:
    _CAIROSVG_AVAILABLE = False

_requires_cairosvg = pytest.mark.skipif(
    not _CAIROSVG_AVAILABLE,
    reason=(
        "libcairo2 not installed — cairosvg unavailable; "
        "install libcairo2 to run rasterize tests"
    ),
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_FIXTURES_DIR = pathlib.Path(__file__).parent / "fixtures"


def _load_svg_fixture() -> bytes:
    """Load the offline MLB-style SVG fixture (no network)."""
    return (_FIXTURES_DIR / "mlb_512.svg").read_bytes()


# Minimal valid SVG for targeted tests.
_MINIMAL_SVG: bytes = (
    b'<svg xmlns="http://www.w3.org/2000/svg" width="50" height="50">'
    b'<rect width="50" height="50" fill="#002b5c"/>'
    b"</svg>"
)

# SVG that references an external http resource (used for SSRF test).
_SSRF_SVG: bytes = (
    b'<svg xmlns="http://www.w3.org/2000/svg"'
    b' xmlns:xlink="http://www.w3.org/1999/xlink"'
    b' width="50" height="50">'
    b'<image href="http://evil.example.com/logo.png"'
    b' x="0" y="0" width="50" height="50"/>'
    b"</svg>"
)

# PNG magic header (first 4 bytes).
_PNG_MAGIC = b"\x89PNG"

# Fake PNG bytes for pass-through tests (magic header only — not a real PNG).
_FAKE_PNG: bytes = b"\x89PNG\r\n\x1a\nfake"
# Fake JPEG bytes.
_FAKE_JPEG: bytes = b"\xff\xd8\xffFAKE"
# Fake WebP bytes.
_FAKE_WEBP: bytes = b"RIFFxxxxWEBPfake"


# ---------------------------------------------------------------------------
# Pass-through tests (no cairosvg required — svg.py is imported lazily inside
# each test to avoid failing at collection time when libcairo2 is absent)
# ---------------------------------------------------------------------------

# These tests only exercise the detection/bypass branch of rasterize_svg_if_needed
# (the `else: return raw` path), so they must NOT import svg.py at module level.
# We patch the cairosvg import inside svg.py with a sentinel to prove no rasterize
# call is made, and import svg.py only when cairosvg is available OR via monkeypatching.
#
# Strategy: when cairosvg is unavailable, we inject a stub module so that svg.py
# can be imported; the stub raises if svg2png is actually called (verifying that
# the pass-through truly does NOT call svg2png).


def _make_cairosvg_stub() -> types.ModuleType:
    """Return a minimal cairosvg stub that raises if svg2png is called."""

    def _svg2png_sentinel(**_kwargs: object) -> bytes:
        raise AssertionError("cairosvg.svg2png must NOT be called for non-SVG bytes")

    stub = types.ModuleType("cairosvg")
    stub.svg2png = _svg2png_sentinel  # type: ignore[attr-defined]
    return stub


def _import_svg_module_with_stub() -> types.ModuleType:
    """Import matchup_thumbs.svg, injecting a cairosvg stub if needed."""
    # Remove cached import so we can inject/re-use the stub.
    mod_name = "matchup_thumbs.svg"
    # Clear previously cached module (may already have real cairosvg or our stub).
    sys.modules.pop(mod_name, None)

    if not _CAIROSVG_AVAILABLE:
        # Inject stub so cairosvg can be "imported" inside svg.py.
        sys.modules["cairosvg"] = _make_cairosvg_stub()
    try:
        import importlib

        svg_mod = importlib.import_module(mod_name)
        return svg_mod
    finally:
        # After importing svg.py, remove the module cache entry so subsequent
        # tests import fresh (avoids cross-test pollution when running both
        # stubbed and real cairosvg).
        sys.modules.pop(mod_name, None)
        if not _CAIROSVG_AVAILABLE:
            sys.modules.pop("cairosvg", None)


class TestPassThrough:
    """rasterize_svg_if_needed must return non-SVG bytes unchanged (D-22)."""

    def test_png_bytes_returned_unchanged(self) -> None:
        """PNG magic-header bytes pass through without calling cairosvg."""
        svg_mod = _import_svg_module_with_stub()
        result = svg_mod.rasterize_svg_if_needed(_FAKE_PNG)
        assert result is _FAKE_PNG, "PNG bytes must be returned by identity (no copy)"

    def test_jpeg_bytes_returned_unchanged(self) -> None:
        """JPEG magic-header bytes pass through unchanged."""
        svg_mod = _import_svg_module_with_stub()
        result = svg_mod.rasterize_svg_if_needed(_FAKE_JPEG)
        assert result is _FAKE_JPEG

    def test_webp_bytes_returned_unchanged(self) -> None:
        """WebP magic-header bytes pass through unchanged."""
        svg_mod = _import_svg_module_with_stub()
        result = svg_mod.rasterize_svg_if_needed(_FAKE_WEBP)
        assert result is _FAKE_WEBP

    def test_leading_whitespace_png_passes_through(self) -> None:
        """PNG bytes with leading whitespace (rare but possible) pass through."""
        padded_png = b"  \n" + _FAKE_PNG
        svg_mod = _import_svg_module_with_stub()
        result = svg_mod.rasterize_svg_if_needed(padded_png)
        # After stripping, the first char is NOT '<' so pass-through.
        assert result is padded_png


# ---------------------------------------------------------------------------
# SVG detection tests (no cairosvg rasterization — stub verifies svg2png IS called)
# ---------------------------------------------------------------------------


class TestSVGDetection:
    """rasterize_svg_if_needed correctly identifies SVG input."""

    def test_svg_prefix_triggers_rasterize_call(self) -> None:
        """<svg bytes trigger svg2png (stub verifies detection branch is entered)."""
        call_log: list[bytes] = []

        def _stub_svg2png(
            bytestring: bytes | None = None,
            output_width: int | None = None,
            url_fetcher: object = None,
        ) -> bytes:
            assert bytestring is not None
            call_log.append(bytestring)
            # Return fake PNG bytes so the caller doesn't crash.
            return _PNG_MAGIC + b"\r\n\x1a\nFAKE"

        stub = types.ModuleType("cairosvg")
        stub.svg2png = _stub_svg2png  # type: ignore[attr-defined]
        sys.modules.pop("matchup_thumbs.svg", None)
        sys.modules["cairosvg"] = stub
        try:
            import importlib

            svg_mod = importlib.import_module("matchup_thumbs.svg")
            svg_mod.rasterize_svg_if_needed(_MINIMAL_SVG)
            assert len(call_log) == 1, "svg2png should be called exactly once"
        finally:
            sys.modules.pop("matchup_thumbs.svg", None)
            sys.modules.pop("cairosvg", None)

    def test_xml_declaration_prefix_triggers_rasterize(self) -> None:
        """<?xml …><svg bytes (starts with '<') trigger the rasterize branch."""
        xml_svg = (
            b'<?xml version="1.0" encoding="UTF-8"?>'
            b'<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10">'
            b'<rect width="10" height="10" fill="blue"/></svg>'
        )
        call_log: list[bytes] = []

        def _stub_svg2png(
            bytestring: bytes | None = None,
            output_width: int | None = None,
            url_fetcher: object = None,
        ) -> bytes:
            assert bytestring is not None
            call_log.append(bytestring)
            return _PNG_MAGIC + b"\r\n\x1a\nFAKE"

        stub = types.ModuleType("cairosvg")
        stub.svg2png = _stub_svg2png  # type: ignore[attr-defined]
        sys.modules.pop("matchup_thumbs.svg", None)
        sys.modules["cairosvg"] = stub
        try:
            import importlib

            svg_mod = importlib.import_module("matchup_thumbs.svg")
            svg_mod.rasterize_svg_if_needed(xml_svg)
            assert len(call_log) == 1
        finally:
            sys.modules.pop("matchup_thumbs.svg", None)
            sys.modules.pop("cairosvg", None)

    def test_leading_whitespace_svg_triggers_rasterize(self) -> None:
        """SVG bytes with leading whitespace still trigger rasterization."""
        padded_svg = b"  \n" + _MINIMAL_SVG
        call_log: list[bytes] = []

        def _stub_svg2png(
            bytestring: bytes | None = None,
            output_width: int | None = None,
            url_fetcher: object = None,
        ) -> bytes:
            assert bytestring is not None
            call_log.append(bytestring)
            return _PNG_MAGIC + b"\r\n\x1a\nFAKE"

        stub = types.ModuleType("cairosvg")
        stub.svg2png = _stub_svg2png  # type: ignore[attr-defined]
        sys.modules.pop("matchup_thumbs.svg", None)
        sys.modules["cairosvg"] = stub
        try:
            import importlib

            svg_mod = importlib.import_module("matchup_thumbs.svg")
            svg_mod.rasterize_svg_if_needed(padded_svg)
            assert len(call_log) == 1
        finally:
            sys.modules.pop("matchup_thumbs.svg", None)
            sys.modules.pop("cairosvg", None)


# ---------------------------------------------------------------------------
# SVG-SSRF blocking test (T-15-SVG-SSRF)
# Tests the _blocking_url_fetcher directly — no real cairosvg call needed.
# ---------------------------------------------------------------------------


class TestSSRFBlocking:
    """The _blocking_url_fetcher must reject non-data: URLs (T-15-SVG-SSRF)."""

    def _get_url_fetcher(self) -> object:
        """Import _blocking_url_fetcher, injecting a stub if cairosvg is absent."""
        sys.modules.pop("matchup_thumbs.svg", None)
        if not _CAIROSVG_AVAILABLE:
            sys.modules["cairosvg"] = _make_cairosvg_stub()
        try:
            import importlib

            svg_mod = importlib.import_module("matchup_thumbs.svg")
            fetcher = svg_mod._blocking_url_fetcher
            return fetcher
        finally:
            sys.modules.pop("matchup_thumbs.svg", None)
            if not _CAIROSVG_AVAILABLE:
                sys.modules.pop("cairosvg", None)

    def test_http_url_is_blocked(self) -> None:
        """An http:// URL raises ValueError — no network fetch."""
        fetcher = self._get_url_fetcher()
        with pytest.raises(ValueError, match="SVG-SSRF blocked"):
            fetcher("http://evil.example.com/logo.png")  # type: ignore[operator]

    def test_https_url_is_blocked(self) -> None:
        """An https:// URL raises ValueError."""
        fetcher = self._get_url_fetcher()
        with pytest.raises(ValueError, match="SVG-SSRF blocked"):
            fetcher("https://www.mlbstatic.com/evil.svg")  # type: ignore[operator]

    def test_file_url_is_blocked(self) -> None:
        """A file:// URL raises ValueError."""
        fetcher = self._get_url_fetcher()
        with pytest.raises(ValueError, match="SVG-SSRF blocked"):
            fetcher("file:///etc/passwd")  # type: ignore[operator]

    def test_data_uri_is_allowed(self) -> None:
        """A data: URI is allowed through (returns dict, not raises)."""
        fetcher = self._get_url_fetcher()
        result = fetcher("data:image/png;base64,abc123")  # type: ignore[operator]
        assert isinstance(result, dict)

    def test_url_fetcher_passed_to_svg2png(self) -> None:
        """rasterize_svg_if_needed passes the blocking url_fetcher to svg2png."""
        received_fetcher: list[object] = []

        def _stub_svg2png(
            bytestring: bytes | None = None,
            output_width: int | None = None,
            url_fetcher: object = None,
        ) -> bytes:
            received_fetcher.append(url_fetcher)
            return _PNG_MAGIC + b"\r\n\x1a\nFAKE"

        stub = types.ModuleType("cairosvg")
        stub.svg2png = _stub_svg2png  # type: ignore[attr-defined]
        sys.modules.pop("matchup_thumbs.svg", None)
        sys.modules["cairosvg"] = stub
        try:
            import importlib

            svg_mod = importlib.import_module("matchup_thumbs.svg")
            svg_mod.rasterize_svg_if_needed(_MINIMAL_SVG)
            assert len(received_fetcher) == 1
            assert received_fetcher[0] is svg_mod._blocking_url_fetcher
        finally:
            sys.modules.pop("matchup_thumbs.svg", None)
            sys.modules.pop("cairosvg", None)


# ---------------------------------------------------------------------------
# Rasterization tests (require real cairosvg + libcairo2)
# ---------------------------------------------------------------------------


@_requires_cairosvg
class TestRasterization:
    """Integration-level rasterize tests that require libcairo2 at runtime."""

    def test_svg_fixture_produces_png_bytes(self) -> None:
        """Rasterizing the offline fixture returns bytes with PNG magic header."""
        from matchup_thumbs.svg import rasterize_svg_if_needed

        svg_bytes = _load_svg_fixture()
        result = rasterize_svg_if_needed(svg_bytes)
        assert result[:4] == _PNG_MAGIC, "Expected PNG magic header in output"

    def test_rasterized_width_bounded_by_svg_raster_size(self) -> None:
        """Output width == _SVG_RASTER_SIZE regardless of SVG size (T-15-SVG-BOMB)."""
        from matchup_thumbs.svg import _SVG_RASTER_SIZE, rasterize_svg_if_needed

        svg_bytes = _load_svg_fixture()
        png_bytes = rasterize_svg_if_needed(svg_bytes)
        from PIL import Image

        img = Image.open(io.BytesIO(png_bytes))
        assert img.width == _SVG_RASTER_SIZE, (
            f"Expected width {_SVG_RASTER_SIZE}, got {img.width} — "
            "render-bomb bound not enforced"
        )

    def test_rasterize_svg_to_square_png_produces_square(self) -> None:
        """rasterize_svg_to_square_png produces a square image."""
        from matchup_thumbs.svg import rasterize_svg_to_square_png

        svg_bytes = _load_svg_fixture()
        png_bytes = rasterize_svg_to_square_png(svg_bytes)
        from PIL import Image

        img = Image.open(io.BytesIO(png_bytes))
        assert img.width == img.height, (
            f"Expected square output, got {img.width}x{img.height}"
        )

    def test_ssrf_svg_with_external_image_raises(self) -> None:
        """An SVG with <image href='http://…'> triggers the blocking url_fetcher."""
        from matchup_thumbs.svg import rasterize_svg_if_needed

        with pytest.raises(ValueError, match="SVG-SSRF blocked"):
            rasterize_svg_if_needed(_SSRF_SVG)
