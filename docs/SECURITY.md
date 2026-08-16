# MarketMind security architecture

MarketMind is an invite-only, authenticated multi-user application. Its server-side security boundary is designed around this rule:

> Every private request has a verified identity, every owned record is scoped to that identity, and trading remains safety-gated.

This document describes the implemented controls and the deployment work required to enable them. It is not a substitute for a professional security review before handling production brokerage credentials.

## Identity and sessions

MarketMind uses [Supabase Auth](https://supabase.com/docs/guides/auth) for managed identity, email delivery, session lifecycle, and MFA. The Next.js app uses `@supabase/ssr` cookie sessions; the FastAPI backend validates bearer access tokens against the provider JWKS with an explicit issuer, audience, expiry, `kid`, and asymmetric-algorithm allowlist. Tokens are never blindly decoded.

- The frontend proxy calls `getUser()` before forwarding an access token to FastAPI. Browser code only calls same-origin `/api/*` routes.
- The backend refuses private requests with `401` when the token is missing/invalid and `503` when Auth is not configured. It never falls back to an anonymous demo account.
- A database user must be active and mapped to the verified provider subject. A valid external account alone is not enough to obtain MarketMind access.
- Sessions are recorded with an HMAC-derived IP value when `AUDIT_IP_HMAC_SECRET` is configured. Raw addresses and access tokens are not stored in the audit log.
- Revoking sessions writes a server-side cutoff timestamp so tokens issued before it are rejected. The Security page also exposes observed session metadata and a sign-out-all-other-sessions action.

The service worker never caches navigation responses, API responses, cookies, session payloads, or user data. It only caches static assets and shows an offline page when a network navigation cannot complete.

## Roles, MFA, and invite-only access

The `users` table has a random UUID primary key plus a unique provider `auth_subject`, email, active flag, and `ADMIN` or `USER` role. Roles are enforced by FastAPI dependencies, never trusted from browser UI state or user-editable metadata.

- Sign-up is disabled in the provider configuration. Administrators send invitations from the Admin Console using the Supabase server secret, which remains backend-only.
- Administrator endpoints require both `ADMIN` and Supabase `aal2` (TOTP step-up) by default.
- Broker connection setup, remote-paper submission, and revocation of other sessions require `aal2` for all roles.
- The Security page supports TOTP enrollment and verification. Passkeys/WebAuthn are deliberately a future additive factor rather than a substitute for the working TOTP path.
- Administrators cannot demote or deactivate themselves through the console; a separate active administrator should be maintained for recovery.

### One-time remote first-admin bootstrap

For hosts without an operator shell, `POST /api/admin/bootstrap` is a deliberately temporary first-admin mechanism. It is not an account-creation API: it returns `404` unless `BOOTSTRAP_ADMIN_ENABLED=true`, requires the server-only `X-Bootstrap-Secret` header using constant-time comparison, verifies the supplied Supabase user through the provider Admin API, and is rate-limited to three requests per source per hour by default.

The request can contain only `auth_subject`, `email`, and `display_name`; it cannot choose a role, bypass identity verification, enable recovery mode, or carry any provider credential. The first valid request creates/maps exactly one `ADMIN` and writes `ADMIN_BOOTSTRAPPED`. A retry with the exact same verified identity is a `200` no-op; a different request after an administrator exists is rejected with `409`. Disable the flag and delete the bootstrap secret immediately after the response is confirmed. The full Render procedure is in [DEPLOYMENT.md](DEPLOYMENT.md#4-bootstrap-the-first-administrator-without-a-shell).

## Tenant isolation and IDOR prevention

Every private resource uses an opaque UUID and a `user_id` ownership column. The API filters list requests by `user_id` and loads detail requests through a shared ownership helper that returns a generic `404` for both missing and foreign resources. This avoids confirming another account's records exist.

| Resource | Isolation rule |
| --- | --- |
| Runtime settings and risk preferences | `user_preferences.user_id` / `user_risk_profiles.user_id` |
| Predictions | `predictions.user_id`; legacy ownerless rows remain archival and are not returned |
| Portfolios and positions | portfolio owner, then position through that portfolio |
| Backtests and saved strategies | direct `user_id` ownership |
| Broker connections and paper orders | direct `user_id` ownership plus opaque IDs |
| Sessions | direct `user_id` ownership |
| System security policy | administrator-only `system_settings` |

Database foreign keys, owner indexes, and per-user uniqueness constraints complement (but do not replace) application-side filters. User settings are no longer keyed only by a global string. The prior `user_settings` row is retained solely as a migration-safe legacy default.

## API, browser, and deployment hardening

- Strict Pydantic request models reject extra fields and constrain tickers, numbers, horizons, and update payloads.
- Sensitive endpoints use an `Idempotency-Key`, bounded input, per-user sliding-window rate limits, and audited outcomes.
- CORS is explicit; production rejects wildcard and non-HTTPS origins. The same-origin Next.js proxy also requires the exact origin on state-changing requests.
- Frontend and backend attach CSP, frame denial, `nosniff`, referrer, permissions, no-store, and production HSTS headers. The Next.js CSP intentionally permits its framework-required inline bootstrap code; do not loosen it without review.
- Secrets are server-side environment values. Neither broker tokens nor Supabase service keys may have a `NEXT_PUBLIC_` prefix.
- Production disables FastAPI interactive docs and requires a non-empty audit-IP HMAC secret.

The bundled rate limiter is process-local and appropriate for a single Render/Railway instance. For multiple replicas, replace it with a shared Redis-backed limiter before horizontally scaling.

## Broker and trading safeguards

Broker connections are designed for OAuth. Only a per-user opaque token-storage reference is modeled; access/refresh tokens are never written to application tables, logs, or browser storage. An OAuth initiation record stores only a one-way state hash.

Remote paper order execution remains intentionally unavailable in this release. Even where paper trading is configured, execution requires: authenticated owner, MFA step-up, user confirmation, an idempotency key, a user-owned active paper connection, user risk approval, no user kill switch, no global kill switch, and server opt-in. The current code records a rejected/audited outcome instead of placing an order until an approved encrypted secret store and broker callback implementation are deployed.

Live trading is permanently locked by both settings validation and the execution guard. `ENABLE_LIVE_TRADING=true` fails production configuration. This implementation does not offer autonomous real-money trading.

## Audit events

Audit records include event type, UTC timestamp, actor user ID where known, resource, result, safe metadata, a privacy-preserving IP HMAC, and a truncated user agent. Security-relevant events include successful/failed login mapping, invitations, role changes, account enablement changes, session revocation, settings/risk changes, kill-switch actions, broker connection initiation, and paper-order denials.

Avoid putting secrets, broker account identifiers, raw IPs, or raw request bodies into `safe_metadata`.

## Migration and rollback

`20260816_02_security_multitenancy` adds the security tables and owner columns without deleting historical data. On an existing database, take a backup, run `alembic upgrade head`, and validate the schema before enabling authentication. It intentionally has no automatic downgrade because rolling back by deleting user, audit, session, and ownership records would be unsafe. Restore a verified backup if rollback is required.

## Operational checklist

1. Configure Supabase Auth and disable public signups.
2. Set production environment variables described in [DEPLOYMENT.md](DEPLOYMENT.md).
3. Run `alembic upgrade head` and bootstrap the first administrator through a trusted backend shell or the short-lived documented remote bootstrap mechanism.
4. Enroll MFA for every administrator before accessing the Admin Console.
5. Invite non-admin users from the console; do not use the public signup endpoint.
6. Keep remote paper orders and live trading disabled until an additional broker/OAuth/security review.
7. Review audit events, rotate service secrets according to provider policy, and keep database backups.
