# Security readiness assessment

Assessment date: 2026-08-22. Automated tests and code controls reduce risk; they do not constitute a penetration test, compliance certification, or authorization to enable autonomous trading.

## Remediation status

The current release implements and tests server-authoritative risk context,
post-trade aggregation, instrument metadata, conservative executable prices,
strategy ledgers, durable loss/reconciliation holds, replay-safe intent/outbox
state, local bearer-session revocation checks, bounded bootstrap state, nested
redaction, and production OpenAPI disablement. Redis-backed rate limiting is
implemented when `REDIS_URL` is configured; the observable process-local
fallback is for single-instance read-only/research use only and blocks any
future remote execution.

This does **not** make autonomous paper or live trading safe. The executor is
non-operational, no broker credentials are present in the API runtime, and the
required broker reconciliation/mTLS/operational controls remain deployment and
re-audit gates.

## Safe for current authenticated market-data use

Subject to correct host configuration, the current release is suitable for private, read-oriented market intelligence: Supabase JWT/JWKS validation fails closed, per-user ownership is enforced and tested, admins require AAL2, private API/PWA responses are not cached, secrets are backend-only/redacted, and public-market provider failures are labelled rather than silently fabricated.

## Safe for current paper trading posture

Only in the present **preview-only** sense: paper order submission is disabled by default and the endpoint cannot call a broker. User confirmation, AAL2, idempotency, ownership, kill switches, typed-intent validation, and deterministic risk review form a locked safety boundary. This is not a claim that remote paper execution is ready.

## Blockers for autonomous paper trading

- Independent isolated executor with a reviewed encrypted token/secret manager;
- canonical server-side broker/portfolio/price/spread inputs, not client preview values;
- durable reconciliation worker, open-order/fill callback verification, distributed locks/idempotency, and a persistent execution hold;
- Redis/distributed rate limiting, job queue, monitoring/alerting, and tested notification/escalation;
- governed sector/theme/correlation classification and point-in-time historical data;
- paper trading soak tests, chaos/restart tests, incident drills, and independent security review.

## Blockers for live money

All autonomous-paper blockers plus broker contractual approval, legal/compliance review, staged non-zero live-capital limit, operational 24/7 ownership, external penetration test, secrets/key-management assessment, disaster recovery exercise, audited production change controls, and formal sign-off for the multi-condition live enablement gate. This code intentionally rejects production `ENABLE_LIVE_TRADING=true`.

## Requires external human security review

- Supabase tenant configuration (sign-up policy, email templates, MFA recovery, JWT key rotation);
- Render/Vercel/GitHub organization access, logs, environment permissions, branch protection, deployment identities, and source-map settings;
- database/network encryption, backup restoration, retention, and least-privilege roles;
- CSP/XSS review across the rendered application and dependency supply-chain review;
- broker OAuth/token custody, execution service isolation, reconciliation, and financial/regulatory obligations;
- an authenticated penetration test covering IDOR, session fixation/revocation, MFA enforcement, rate limits, and admin workflow.

## Operational non-negotiables

Keep `ENABLE_LIVE_TRADING=false` and `ENABLE_REMOTE_PAPER_ORDERS=false`. Use a secrets manager, rotate secrets on personnel/provider events, run CI security checks on every protected change, and treat an uncertain data/broker/policy state as no-trade.
