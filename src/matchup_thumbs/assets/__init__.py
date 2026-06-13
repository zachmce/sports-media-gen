"""Bundled static assets for matchup-thumbs."""

from importlib.resources import files


def get_placeholder_logo() -> bytes:
    """Return the bundled neutral placeholder logo bytes.

    This is the terminal fallback in the D-10 / ESPN-02 logo fallback chain:
    primary ESPN CDN logo → alternate ESPN variant → this placeholder.
    Loaded from the package via importlib.resources so it ships correctly
    in both editable installs and built wheels.
    """
    return files("matchup_thumbs.assets").joinpath("placeholder_logo.png").read_bytes()
