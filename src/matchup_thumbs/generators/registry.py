"""Typed (kind, style) generator registry for matchup-thumbs.

Provides a decorator-based registry that maps ``(kind: str, style: int)``
pairs to callable generator functions.  Generator modules register themselves
at import time via the ``@register`` decorator so that importing
``matchup_thumbs.generators`` is sufficient to populate the registry.

Usage::

    from matchup_thumbs.generators.registry import register, get_generator

    @register("thumb", 0)
    def generate_thumb_style0(away, home, assets):
        ...

    fn = get_generator("thumb", 0)  # returns generate_thumb_style0
    fn = get_generator("bogus", 0)  # returns None → 400 path (GEN-07)
"""

from __future__ import annotations

from collections.abc import Callable

from PIL import Image

from .types import DecodedAssets, TeamDict

# Python 3.14+ type alias (AGENTS.md: use modern syntax freely).
# A GeneratorFn is a pure function: inputs in, PIL.Image out, no I/O (GEN-04).
type GeneratorFn = Callable[[TeamDict, TeamDict, DecodedAssets], Image.Image]

# Module-level registry keyed by (kind, style).
# Populated at import time via the @register decorator.
_REGISTRY: dict[tuple[str, int], GeneratorFn] = {}


def register(kind: str, style: int) -> Callable[[GeneratorFn], GeneratorFn]:
    """Return a decorator that registers a generator function under (kind, style).

    The decorated function is stored unchanged in ``_REGISTRY`` and returned,
    so it can still be imported and called directly.

    Example::

        @register("thumb", 0)
        def generate_thumb_style0(away, home, assets):
            ...
    """

    def decorator(fn: GeneratorFn) -> GeneratorFn:
        _REGISTRY[(kind, style)] = fn
        return fn

    return decorator


def get_generator(kind: str, style: int) -> GeneratorFn | None:
    """Return the registered generator for (kind, style), or None if unknown.

    Returns ``None`` for any unregistered (kind, style) combination.
    The caller (render pipeline / Phase 4 route handler) should translate
    a ``None`` return into a 400 response (GEN-07).
    """
    return _REGISTRY.get((kind, style))


def registered_kinds() -> frozenset[str]:
    """Return the set of distinct image kinds registered in _REGISTRY.

    Derived from the first element of every (kind, style) key.  A kind
    is included once regardless of how many styles are registered for it.

    Used by the nginx/registry drift guard (DEBT-01).
    """
    return frozenset(kind for (kind, _style) in _REGISTRY)
