"""Prometheus domain metrics for matchup-thumbs (D-11, OBS-01).

Metric objects are created at module import time and registered on the
default prometheus_client registry.  All route handlers and the asset
loader import from here.

Label cardinality is bounded (D-11):
- render_latency_seconds: ≤ 6 leagues × 3 kinds = 18 series
- render_cache_events_total: 4 tier values ("hit", "miss", "coalesced", "degraded")
- resolution_total / resolution_misses_total: 6 league values each
- espn_fetch_failures_total: no labels (unbounded counter — label would be
  unbounded by ESPN URL which violates D-11)

Never add a team/away/home label to any metric defined here.
"""

from prometheus_client import Counter, Histogram

#: Render pipeline latency, observed around render_pipeline() call (D-11).
#: Labels: league (≤ 6 values), kind (≤ 3 values) → ≤ 18 series.
render_latency_seconds: Histogram = Histogram(
    "render_latency_seconds",
    "Render pipeline latency in seconds",
    labelnames=["league", "kind"],
    buckets=(0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 5.0, 10.0),
)

#: Render cache events, incremented once per render_pipeline() call (D-09/D-11).
#: Label: tier ∈ {"hit", "miss", "coalesced", "degraded"} → 4 series.
render_cache_events_total: Counter = Counter(
    "render_cache_events_total",
    "Render cache events by tier",
    labelnames=["tier"],
)

#: ESPN logo re-fetch failures, incremented in assets/loader.py on failure (D-11).
#: No labels — team/URL labels would be unbounded (violates D-11).
espn_fetch_failures_total: Counter = Counter(
    "espn_fetch_failures_total",
    "Number of ESPN logo fetch failures",
)

#: Total team resolution attempts, incremented per resolve() call (D-11).
#: Label: league (≤ 6 values) → ≤ 6 series.
resolution_total: Counter = Counter(
    "resolution_total",
    "Total team resolution attempts",
    labelnames=["league"],
)

#: Total team resolution misses (resolve() returned None) (D-11).
#: Label: league (≤ 6 values) → ≤ 6 series.
#: Miss rate = rate(misses) / rate(total) in Prometheus queries.
resolution_misses_total: Counter = Counter(
    "resolution_misses_total",
    "Total team resolution misses (resolve() returned None)",
    labelnames=["league"],
)
