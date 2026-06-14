#!/usr/bin/env bash
# tests/integration/nginx_smoke.sh
# Smoke test for Phase 5 nginx caching proxy.
#
# Asserts all three Phase 5 success criteria (SC-1, SC-2, SC-3) via curl
# against the nginx front door.
#
# Requirements: docker compose stack running with nginx service.
# Usage: bash tests/integration/nginx_smoke.sh
#
# Environment variables (all have defaults):
#   NGINX_HOST_PORT   Host port nginx is published on (default: 8080)
#
# Exit codes:
#   0  All assertions passed (or stack not reachable — see SKIP below)
#   1  One or more assertions failed

set -euo pipefail

# ── Named constants (no magic numbers per CLAUDE.md) ─────────────────────────
# NGINX_HOST_PORT  8080  -- Host port for nginx front door (default; avoids 8000)
BASE="http://localhost:${NGINX_HOST_PORT:-8080}"

# SC-1 route: a valid image generation path used for cache HIT verification
SC1_ROUTE="/nba/lakers/celtics/thumb"

# SC-2 burst: send this many requests to trigger the burst limit (burst=20 + extras)
BURST_COUNT=25

# SC-2 probe: send this many consecutive probe requests to verify they are never 429
PROBE_COUNT=30

# SC-3 route: a deliberately invalid team name that forces an app 404
SC3_ROUTE="/nba/zzz_team_not_real/celtics/thumb"

# Pass/fail counters
PASS=0
FAIL=0

_pass() { echo "PASS: $1"; ((PASS++)); }
_fail() { echo "FAIL: $1"; ((FAIL++)); }

# ── Reachability pre-check ────────────────────────────────────────────────────
# If the nginx front door is not up yet (Wave 0: no nginx service in compose),
# skip with a clear message and exit 0. This keeps the harness non-failing
# until Wave 1 nginx config + compose service are in place.
if ! curl -s -o /dev/null --connect-timeout 3 "$BASE/healthz" 2>/dev/null; then
    echo "SKIP: nginx front door not reachable on $BASE"
    echo "      Bring up the stack first: docker compose up -d"
    echo "      (This is expected until plan 05-02 lands the nginx service.)"
    exit 0
fi

echo "nginx front door reachable at $BASE — running smoke assertions"
echo ""

# ── SC-1: Second image request must be a cache HIT (CACHE-06) ────────────────
# The first request fills the cache (MISS); the second must be served from it (HIT).
R1=$(curl -s -o /dev/null -w "%header{x-cache-status}" "$BASE$SC1_ROUTE")
R2=$(curl -s -o /dev/null -w "%header{x-cache-status}" "$BASE$SC1_ROUTE")
echo "SC-1 cache status: first=$R1 second=$R2"
if [ "$R2" = "HIT" ]; then
    _pass "SC-1 second request is a cache HIT"
else
    _fail "SC-1 expected HIT on second request, got '$R2' (first was '$R1')"
fi

echo ""

# ── SC-2 (part A): burst past rate limit must yield at least one 429 (OPS-04) ─
# Rate zone: 10r/s, burst=20 nodelay. Sending BURST_COUNT requests back-to-back
# should exhaust the burst allowance and trigger 429 for the overflow.
echo "SC-2 burst: sending $BURST_COUNT requests to $BASE$SC1_ROUTE ..."
STATUSES=""
for i in $(seq 1 "$BURST_COUNT"); do
    CODE=$(curl -s -o /dev/null -w "%{http_code}" "$BASE$SC1_ROUTE")
    STATUSES="$STATUSES $CODE"
done
echo "SC-2 burst status codes:$STATUSES"
if echo "$STATUSES" | grep -q "429"; then
    _pass "SC-2 burst returns at least one 429"
else
    _fail "SC-2 no 429 seen in $BURST_COUNT-request burst"
fi

echo ""

# ── SC-2 (part B): probe endpoints must never be rate-limited (OPS-04) ────────
# /healthz is an exact-match exempt location; it should return 200 every time.
echo "SC-2 probes: sending $PROBE_COUNT requests to $BASE/healthz ..."
HEALTHZ_CODES=$(for i in $(seq 1 "$PROBE_COUNT"); do
    curl -s -o /dev/null -w "%{http_code}\n" "$BASE/healthz"
done | sort -u)
if [ "$HEALTHZ_CODES" = "200" ]; then
    _pass "SC-2 /healthz never rate-limited ($PROBE_COUNT requests, all 200)"
else
    _fail "SC-2 /healthz got non-200 codes: $HEALTHZ_CODES"
fi

# /metrics is an exact-match exempt location.
METRICS_CODE=$(curl -s -o /dev/null -w "%{http_code}" "$BASE/metrics")
if [ "$METRICS_CODE" = "200" ]; then
    _pass "SC-2 /metrics returned 200 (probe exempt)"
else
    _fail "SC-2 /metrics returned $METRICS_CODE (expected 200, probe should be exempt)"
fi

# /leagues is an exact-match exempt location.
LEAGUES_CODE=$(curl -s -o /dev/null -w "%{http_code}" "$BASE/leagues")
if [ "$LEAGUES_CODE" = "200" ]; then
    _pass "SC-2 /leagues returned 200 (probe exempt)"
else
    _fail "SC-2 /leagues returned $LEAGUES_CODE (expected 200, probe should be exempt)"
fi

# /{league}/teams is a regex-match exempt location (no rate-limit, not cached).
TEAMS_CODE=$(curl -s -o /dev/null -w "%{http_code}" "$BASE/nba/teams")
if [ "$TEAMS_CODE" = "200" ]; then
    _pass "SC-2 /nba/teams returned 200 (listing exempt)"
else
    _fail "SC-2 /nba/teams returned $TEAMS_CODE (expected 200, listing should be exempt)"
fi

echo ""

# ── SC-3 (part A): app 404 responses must NOT be cached (OPS-04) ─────────────
# The nginx config uses proxy_cache_valid 200 30d — 404 is never stored.
# Both the first and second request to a non-existent team must be non-HIT.
S1=$(curl -s -o /dev/null -w "%header{x-cache-status}" "$BASE$SC3_ROUTE")
S2=$(curl -s -o /dev/null -w "%header{x-cache-status}" "$BASE$SC3_ROUTE")
echo "SC-3 404 cache status: first=$S1 second=$S2"
if [ "$S1" != "HIT" ] && [ "$S2" != "HIT" ]; then
    _pass "SC-3 404 response not cached (neither request returned HIT)"
else
    _fail "SC-3 404 was cached (s1='$S1' s2='$S2' — expected neither to be HIT)"
fi

echo ""

# ── SC-3 (part B): HTTP-only — port 443 must NOT be listening (OPS-04, D-13) ─
# nginx must listen only on port 80. Port 443 (TLS) must be absent; TLS is
# terminated upstream. Try ss first (BusyBox version in alpine); fall back to
# /proc/net/tcp hex-address scan if ss is unavailable.
#
# Port 80 in /proc/net/tcp hex: 0050 (big-endian decimal 80)
# Port 443 in /proc/net/tcp hex: 01BB (big-endian decimal 443)
if docker compose exec -T nginx ss -tlnp 2>/dev/null | grep -q ".*"; then
    # ss is available
    HAS_443=$(docker compose exec -T nginx ss -tlnp 2>/dev/null | grep ":443" || true)
    HAS_80=$(docker compose exec -T nginx ss -tlnp 2>/dev/null | grep ":80" || true)
else
    # Fallback: read /proc/net/tcp hex columns; port is column 2 after the colon
    HAS_443=$(docker compose exec -T nginx sh -c \
        "grep -i '01BB' /proc/net/tcp /proc/net/tcp6 2>/dev/null || true")
    HAS_80=$(docker compose exec -T nginx sh -c \
        "grep -i '0050' /proc/net/tcp /proc/net/tcp6 2>/dev/null || true")
fi

if [ -n "$HAS_80" ]; then
    _pass "SC-3 nginx is listening on port 80 (HTTP)"
else
    _fail "SC-3 port 80 not found in nginx listening sockets — nginx may not be serving"
fi

if [ -z "$HAS_443" ]; then
    _pass "SC-3 port 443 is NOT listening in nginx (HTTP-only, TLS is upstream)"
else
    _fail "SC-3 port 443 is open in nginx — TLS must be terminated upstream, not here"
fi

# ── Results ───────────────────────────────────────────────────────────────────
echo ""
echo "Results: $PASS passed, $FAIL failed"

if [ "$FAIL" -gt 0 ]; then
    exit 1
fi
