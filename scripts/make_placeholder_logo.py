"""One-time script to generate the neutral placeholder logo PNG.

Produces a 512x512 RGBA image with a neutral gray circle on a transparent
background.  Run once to regenerate:

    uv run python scripts/make_placeholder_logo.py

The resulting PNG is committed to src/matchup_thumbs/assets/placeholder_logo.png
and bundled in the package wheel (hatchling includes non-Python files by default).
This is the terminal fallback of the D-10 / ESPN-02 logo fallback chain.
"""

from __future__ import annotations

import pathlib

from PIL import Image, ImageDraw

OUTPUT = (
    pathlib.Path(__file__).parent.parent
    / "src"
    / "matchup_thumbs"
    / "assets"
    / "placeholder_logo.png"
)

SIZE = 512
CIRCLE_COLOR = (160, 160, 160, 230)  # neutral gray, slightly transparent


def make_placeholder_logo() -> None:
    """Draw a neutral gray circle and save as PNG."""
    img = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Draw a filled circle centered in the image with a small inset margin
    margin = SIZE // 16  # 32px inset at 512
    draw.ellipse(
        [margin, margin, SIZE - margin, SIZE - margin],
        fill=CIRCLE_COLOR,
    )

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    img.save(OUTPUT, format="PNG")
    print(f"Saved {OUTPUT} ({OUTPUT.stat().st_size} bytes)")


if __name__ == "__main__":
    make_placeholder_logo()
