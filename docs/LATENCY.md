# Latency profiling

MarketMind keeps public market caches in the backend process and private API
responses only in the authenticated browser session cache. It does not place
portfolio, broker, session, or authenticated API data in the service worker or
a shared HTTP cache.

## Warm-path diagnostics

Every FastAPI response includes a `Server-Timing` header. The key-free metrics
include authentication, database, Alpaca quote/bars/news/options calls,
indicator/strategy computation, JSON serialization, and total endpoint time.
The Vercel same-origin proxy appends `proxy_auth`, `proxy_backend`, and
`proxy_total` timings. It also forwards the backend's `X-MarketMind-Cache`
summary (`hit`, `miss`, `coalesced`, or `stale-refresh`).

`GET /api/health` exposes bounded rolling p50/p95 timing summaries and public
market-cache counters. It never returns credentials, JWTs, symbols, user IDs,
or account identifiers. Endpoint names are normalized before they enter this
report.

For a browser-session view, the bounded in-memory
`window.__marketMindLatency` array records navigation start, API start/response,
and post-paint render completion. Set `NEXT_PUBLIC_PERFORMANCE_DEBUG=true`
temporarily to also emit these entries to the browser console; do not enable it
as a long-term production default.

## What warm means

- **Warm backend:** an already-running Render instance, a reusable Alpaca HTTP
  connection, and a valid backend cache entry where applicable.
- **Cold Render start:** a free-tier process has to boot before it can execute
  application code. This delay is outside endpoint optimization and is shown
  separately by a large `proxy_backend` duration with no prior backend timing.
  MarketMind does not conceal it with synthetic live data.

Use four or more authenticated revisits to the same page and inspect the
median. Compare `proxy_auth`, `proxy_backend`, the nested FastAPI phases, and
the cache summary before changing timeouts or cache TTLs.

## Cache policy

- Alpaca quote snapshots: 15 seconds, with per-symbol reuse after a batch.
- Market clock: 15 seconds.
- Intraday bars: existing strict range TTLs.
- Daily/scanner bars: existing TTLs plus a bounded stale-while-refresh window;
  this is safe because they are historical observations, not current quotes.
- News: 60 seconds; options chains: 30 seconds.
- Technical snapshots: 15 minutes when the complete public OHLCV source
  fingerprint is unchanged.
- Browser SWR: session-scoped and rendered immediately while background refresh
  continues. Sidebar hover/focus warms likely small routes; options are not
  proactively prefetched.
