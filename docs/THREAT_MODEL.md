# MarketMind threat model

This is the security model for the current authenticated market-intelligence service and the planned isolated execution boundary. It is not a claim of certification or an authorization to trade autonomously.

## Assets and trust boundaries

| Boundary | Protected assets | Current controls | Residual risk |
| --- | --- | --- | --- |
| Next.js / Vercel browser boundary | Supabase session, private UI data | Same-origin BFF, middleware auth, CSP, no-store private responses | Browser/XSS or compromised dependency can act as the signed-in user |
| FastAPI / Render | authorization decisions, audit history, policy | JWKS signature/issuer/audience/expiry validation, ownership filters, strict schemas, rate limits | A compromised backend can access its configured provider credentials |
| PostgreSQL | user mappings, portfolios, audit and intent records | user IDs/FKs/owner queries, managed DB access, no plaintext OAuth token storage | DB operator compromise; encryption-at-rest is host responsibility |
| Supabase | identities, sessions, MFA, service role | asymmetric JWKS allowlist, AAL2 for admins, session cutoff | provider outage or administrator/key compromise |
| Market-data providers | pricing/news/options input | server-only keys, timeouts, health/freshness labelling, no-trade on bad trade data | bad/corrupted market data or provider compromise |
| Future broker executor | broker credential and account state | intentionally not implemented; documented isolated layer only | execution must not be enabled before external review |
| AI / strategy output | signal metadata | strict `TradeIntent`; no broker dependency; deterministic risk policy is final authority | prompt injection and model errors can still create rejected/no-trade signals |
| PWA / service worker | cached public static shell | static-only cache; navigations and `/api/*` excluded; versioned cache purge | previously installed client needs activation to discard prior caches |
| CI/CD / GitHub | source, build credentials, deployment integrity | proposed Actions/Dependabot/secret scanning; protected production environment is required | maintainer compromise or unreviewed workflow change |

## Threat actors and prioritized findings

| Actor / scenario | Severity | Risk | Mitigation and status |
| --- | --- | --- | --- |
| Unauthenticated attacker | HIGH | probes API/admin/bootstrap, malformed requests, credential stuffing | private API fails closed; bootstrap disabled by default; strict body schemas and rate limits implemented |
| Malicious authenticated user | HIGH | IDOR, mass assignment, abusive scans/backtests | per-user ownership queries/404s, role dependencies, bounded pagination/models, route limits implemented |
| Compromised user or stolen session | HIGH | private data or order-preview access | short-lived verified JWTs, server session cutoff, AAL2 for sensitive actions; revoke sessions during incident |
| Compromised administrator | CRITICAL | role/kill-switch/risk-policy abuse | AAL2 required; audit log and no self-demotion; maintain two admins; external monitoring still required |
| Leaked broker/Supabase/database secret | CRITICAL | provider/account takeover | backend-only env values, redaction, no cache/log/URL storage; rotate and pause immediately |
| Compromised frontend / XSS | HIGH | session action and private-page disclosure | CSP/frame denial/no-store; browser remains a high-value boundary, requiring dependency/CSP review |
| Compromised backend or database | CRITICAL | policy, audit, user data compromise | least privilege, host secret manager, backups and reconciliation; requires independent review before execution |
| Malicious dependency / CI action | HIGH | source/build compromise | lockfiles, Dependabot, dependency audit and secret scanning; pin actions and require reviewed changes |
| Market-data/provider compromise | HIGH | incorrect signal/order context | freshness/provider-health/data-conflict rejection and kill switch; independent price reconciliation still future work |
| Prompt injection / malicious news | HIGH | AI tries to issue arbitrary execution command | AI is untrusted input; only typed intent accepted and execution guard never receives AI text |
| Replay / worker restart | HIGH | duplicate order | durable intent UUID/idempotency/client-order-id design; execution remains disabled |
| Insider misuse | HIGH | secret/policy/audit tampering | least privilege, audited admin actions, host access controls and independent log retention required |
| Automated bots / denial of service | MEDIUM | resource exhaustion and provider cost | authenticated rate limits, upstream cache/timeouts; migrate to distributed Redis limits before horizontal scale |
| Service worker cache leakage | MEDIUM | stale private response after logout | no private/navigation/API caching; cache version bump deletes old shell caches |
| Error/source-map disclosure | LOW | implementation hints | production docs disabled, generic errors, no browser source maps by default; confirm Vercel production setting |

## Security properties that must remain true

1. A bearer token is accepted only after JWKS signature, allowed asymmetric algorithm, `kid`, issuer, audience, `iat`, `exp`, subject, user-active, and session-cutoff checks.
2. A frontend role indicator is never authorization. All privileged routes require server-side role checks, and admin routes additionally require AAL2 when configured.
3. Every user-owned query carries its authenticated owner. A foreign opaque ID receives generic `404`.
4. Secrets never cross the backend boundary, are not placed in `NEXT_PUBLIC_*`, URLs, audit values, cache entries, or error detail, and are redacted before logging/audit persistence.
5. An LLM cannot call a broker, select unrestricted notional, change policy, enable trading, or write risk/execution code.
6. Any uncertain data, configuration, broker state, or risk result resolves to **no trade**.

## Review cadence

Review this model after an auth/provider/CI change, before enabling remote paper orders, and before every new execution capability. Treat a change to a threat boundary, policy ceiling, secret store, or deployment identity as a security review trigger.
