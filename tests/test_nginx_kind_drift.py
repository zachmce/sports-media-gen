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
    conf = (Path(__file__).parent.parent / "nginx" / "nginx.conf.template").read_text()
    m = re.search(r"location\s+~\s+\^/.*?/\(([a-z|]+)\)\$", conf)
    assert m is not None, "Could not find kind alternation in nginx.conf.template"
    nginx_kinds = frozenset(m.group(1).split("|"))

    registry_kinds = registered_kinds()

    assert nginx_kinds == registry_kinds, (
        f"nginx kinds {nginx_kinds!r} diverge from registry kinds {registry_kinds!r}.\n"
        f"nginx only: {nginx_kinds - registry_kinds!r}\n"
        f"registry only: {registry_kinds - nginx_kinds!r}"
    )
