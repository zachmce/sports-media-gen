"""nginx ↔ registry kind-drift guard (DEBT-01, D-05..D-08).

Covers:
- Exact set-equality in both directions between nginx's location-matcher
  alternation and the generator registry's registered kinds (D-07).
- Fails if a kind is removed from nginx's location regex OR if a kind is
  added to the registry without updating nginx — both directions caught.
- Pure file-parsing test: reads nginx.conf.template and imports the registry
  from the source tree; no running nginx, no container, no network (D-08).
"""

from __future__ import annotations

import re
from pathlib import Path

import matchup_thumbs.generators  # noqa: F401  — side-effect: populates _REGISTRY
from matchup_thumbs.generators.registry import registered_kinds


def test_nginx_kinds_match_generator_registry() -> None:
    """DEBT-01: nginx kind matcher must stay in sync with the generator registry.

    Fails if a kind is removed from nginx's location regex OR if a kind is
    added to the registry without updating nginx — both directions caught (D-07).
    """
    raw = (Path(__file__).parent.parent / "nginx" / "nginx.conf.template").read_text()
    # Strip comment lines so a commented-out/anchor `location` block (allowed by
    # D-06) can never shadow the live directive via a false-positive regex match.
    conf = "\n".join(
        line for line in raw.splitlines() if not line.lstrip().startswith("#")
    )
    m = re.search(r"location\s+~\s+\^/.*?/\(([a-z|]+)\)\$", conf)
    assert m is not None, "Could not find kind alternation in nginx.conf.template"
    nginx_kinds = frozenset(m.group(1).split("|"))

    registry_kinds = registered_kinds()

    assert nginx_kinds == registry_kinds, (
        f"nginx kinds {nginx_kinds!r} diverge from registry kinds {registry_kinds!r}.\n"
        f"nginx only: {nginx_kinds - registry_kinds!r}\n"
        f"registry only: {registry_kinds - nginx_kinds!r}"
    )


def test_nginx_does_not_ignore_cache_control() -> None:
    """D-05: nginx template has no proxy_ignore_headers directive (CACHE-10 premise).

    nginx honors upstream Cache-Control: no-store by default — no
    proxy_ignore_headers Cache-Control is needed.  This guard fails if someone
    adds such a directive, which would silently break the CACHE-10 kill-switch.
    """
    text = (Path(__file__).parent.parent / "nginx" / "nginx.conf.template").read_text()
    assert "proxy_ignore_headers" not in text, (
        "nginx.conf.template contains 'proxy_ignore_headers' — "
        "this would prevent Cache-Control: no-store from signaling nginx to skip "
        "its proxy_cache tier, breaking the CACHE-10 kill-switch (D-05)."
    )
