# Deploy secure MarketMind

MarketMind is deployed as three HTTPS services:

```text
Vercel (Next.js PWA + same-origin API proxy)
    -> Render / Railway (FastAPI)
    -> managed PostgreSQL
           ^
Supabase Auth (identity, invitation email, sessions, TOTP MFA)
```

The frontend is a private application, not a public demo site. Configure identity before enabling a production backend; production startup fails closed if Supabase Auth, HTTPS CORS, or audit hashing are missing.

## 1. Create and configure Supabase Auth

1. Create a Supabase project and save its project URL, **publishable/anon key**, and **secret/service-role key**. The secret key belongs only on the FastAPI host.
2. In **Authentication → Providers**, keep Email enabled and configure a real transactional email sender before inviting external users.
3. In **Authentication → Settings**, disable public sign-ups. MarketMind creates invited users through its administrator API only.
4. In **URL Configuration**, set the Site URL to your Vercel URL and add each callback URL, for example:

   ```text
   https://marketmind-xxxx.vercel.app/auth/callback
   https://marketmind.example.com/auth/callback
   ```

5. In **Multi-Factor Authentication**, enable TOTP. Administrators must enroll it before any administrator operation is accepted.
6. In **JWT signing keys**, use an asymmetric ECC/RSA signing key compatible with the backend’s `ES256,RS256` allowlist. Do not add an `HS256` shared secret as a compatibility shortcut.
7. Invite or create the very first administrator in the Supabase dashboard. Copy that user’s UUID from the Auth users page. Do not create a public password-signup flow.

Supabase publishes its [Auth overview](https://supabase.com/docs/guides/auth), [server-side Next.js guidance](https://supabase.com/docs/guides/auth/server-side/creating-a-client), and [MFA/TOTP setup](https://supabase.com/docs/guides/auth/auth-mfa).

## 2. Provision PostgreSQL and run the migration

Create managed PostgreSQL in the same region as the FastAPI host. Copy its internal connection string and change the driver scheme to `postgresql+asyncpg://` for SQLAlchemy.

Back up any existing MarketMind database before upgrading. In a trusted backend shell, run:

```bash
cd backend
alembic upgrade head
```

The security migration retains legacy settings and ownerless historical predictions but does not expose them as new users’ private data. It deliberately has no destructive automatic downgrade; restore a backup to roll back.

## 3. Deploy FastAPI on Render or Railway

Create a Docker web service from this repository with **Root Directory** `backend`, then configure its health check as `/api/health`. Set these server-only environment variables:

```text
MARKETMIND_ENV=production
DATABASE_URL=postgresql+asyncpg://USER:PASSWORD@HOST:PORT/DATABASE
FRONTEND_ORIGIN=https://marketmind-xxxx.vercel.app
CORS_ORIGINS=https://marketmind-xxxx.vercel.app

AUTH_MODE=required
SUPABASE_URL=https://YOUR_PROJECT.supabase.co
SUPABASE_PUBLISHABLE_KEY=sb_publishable_...
SUPABASE_SECRET_KEY=sb_secret_...
AUDIT_IP_HMAC_SECRET=<generate-a-long-random-secret>
ADMIN_MFA_REQUIRED=true
BOOTSTRAP_ADMIN_ENABLED=false
BOOTSTRAP_ADMIN_SECRET=
BOOTSTRAP_ADMIN_RATE_LIMIT_PER_HOUR=3
AUTO_CREATE_SCHEMA=false

ENABLE_PAPER_TRADING=true
ENABLE_REMOTE_PAPER_ORDERS=false
ENABLE_LIVE_TRADING=false

OPENAI_API_KEY=
ENABLE_OPENAI_ANALYSIS=false
ALPACA_MARKET_DATA_API_KEY=
ALPACA_MARKET_DATA_SECRET_KEY=
ALPACA_DATA_URL=https://data.alpaca.markets
ALPACA_FEED=iex
ALPACA_OPTIONS_FEED=indicative
MARKET_DATA_REQUEST_TIMEOUT_SECONDS=8
MARKET_DATA_STALE_SECONDS=900
ALPACA_OAUTH_CLIENT_ID=
ALPACA_OAUTH_CLIENT_SECRET=
ALPACA_OAUTH_REDIRECT_URI=
SEC_USER_AGENT=MarketMind security@example.com
```

Use the host’s encrypted environment-variable facility. Never put the secret key, audit HMAC secret, database URL, broker credentials, OAuth client secret, or token-store credentials in Vercel, Git, browser storage, `NEXT_PUBLIC_*`, or logs. Use only market-data-scoped Alpaca credentials in the API service. Future `EXECUTOR_BROKER_*` credentials belong exclusively to a separately deployed private executor and must not be configured for this release. The container runs Alembic during startup; a managed release command that runs `alembic upgrade head` before deployment is preferred for controlled production changes.

At this stage, visit `https://YOUR_API/api/health`. It should return `healthy`, `authentication: configured`, and `live_trading: locked`, without secret values. The market-specific fields are intentionally safe: `market_provider`, `market_feed`, `market_data_status`, `news_provider`, and `options_provider`. The first successful public request changes a configured Alpaca connection to `Healthy`; a feed failure becomes `Degraded` and never produces synthetic live-looking values.

For Live Market Data V1, keep `ALPACA_DATA_URL=https://data.alpaca.markets` and start with `ALPACA_FEED=iex`. IEX is visibly labelled as such. Only select `sip` after confirming the account is entitled to that feed; `delayed_sip` is visibly delayed. Options defaults to `indicative` and will show an unavailable capability panel if the account/feed does not return a chain. Do not set any of these values in Vercel or under a `NEXT_PUBLIC_` name.

## 4. Bootstrap the first administrator without a shell

This temporary procedure is for Render Free and other hosts where an operator shell is unavailable. It does **not** create a permanent public administrator endpoint.

1. In Render, set `BOOTSTRAP_ADMIN_ENABLED=true`.
2. Generate a high-entropy, unique secret of at least 32 characters in a password manager. Set it as `BOOTSTRAP_ADMIN_SECRET` in Render, and set `BOOTSTRAP_ADMIN_EXPIRES_AT` to a near UTC ISO-8601 expiry (for example `2026-08-22T14:00:00Z`). Do not put either in Git, Vercel, a `NEXT_PUBLIC_*` value, or the request body.
3. Keep `ENABLE_LIVE_TRADING=false` and `ENABLE_REMOTE_PAPER_ORDERS=false`, then redeploy the backend.
4. After the deployment is healthy, call the backend URL exactly once. Store the secret in a shell variable so it is sent only in the dedicated header:

   ```bash
   export BOOTSTRAP_ADMIN_SECRET='paste-the-strong-render-secret-here'
   curl --fail-with-body --request POST "https://YOUR_API_HOST/api/admin/bootstrap" \
     --header "Content-Type: application/json" \
     --header "X-Bootstrap-Secret: ${BOOTSTRAP_ADMIN_SECRET}" \
     --data '{
       "auth_subject": "cd384bcf-b533-4a71-909d-43552daa0abd",
       "email": "yu788317@gmail.com",
       "display_name": "Yu"
     }'
   unset BOOTSTRAP_ADMIN_SECRET
   ```

   The endpoint verifies that this subject exists in Supabase and that its provider email matches the request. It returns a redacted user view with `status: "bootstrapped"`; it never returns the bootstrap secret, provider key, JWT, or database connection string.

5. Confirm the response shows `role: "ADMIN"`, sign in at the Vercel URL, and enroll TOTP in **Security**. An exact retry returns `200` with `already_bootstrapped` and creates no duplicate; a different second candidate is rejected.
6. Immediately set `BOOTSTRAP_ADMIN_ENABLED=false` in Render, delete `BOOTSTRAP_ADMIN_SECRET` from Render, and redeploy again. A disabled endpoint returns `404` and there is no user-facing UI for it.
7. Use **Admin Console** for all future invitations. Keep at least two active MFA-enrolled administrators.

For a non-Render recovery process, the separately documented `backend/scripts/bootstrap_admin.py` remains available only from a trusted operator environment after verifying the intended Supabase subject.

## 5. Deploy the Next.js PWA on Vercel

1. Import the repository into Vercel and choose branch `main`.
2. Set **Root Directory** to `frontend`.
3. Leave the committed commands in place: `pnpm install --frozen-lockfile` and `pnpm run build`.
4. Set these Vercel environment variables for Production and Preview:

   ```text
   BACKEND_URL=https://marketmind-api-xxxx.onrender.com
   NEXT_PUBLIC_SUPABASE_URL=https://YOUR_PROJECT.supabase.co
   NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY=sb_publishable_...
   ```

   The two `NEXT_PUBLIC_` values are designed to be public. `BACKEND_URL` is server-only. Do not set `NEXT_PUBLIC_API_BASE_URL`: browser API calls remain same-origin, and the route handler verifies the Supabase session before forwarding a bearer token to FastAPI.

5. Deploy and copy the generated Vercel HTTPS URL.
6. Return to the backend configuration and replace temporary frontend origins with the exact Vercel URL. Add custom domains as explicit comma-separated HTTPS origins.

The frontend will intentionally send an unauthenticated visitor to `/login`. A missing Supabase configuration sends a configuration warning rather than showing private market data.

## 6. Production acceptance checklist

1. Open the Vercel URL in a private browser session and confirm it redirects to Login.
2. Sign in with the bootstrap account. Confirm dashboard data loads only after authentication.
3. Visit **Security**, enroll and verify a TOTP authenticator, then confirm `aal2` access is shown.
4. Visit **Admin Console** and confirm that non-admin or non-MFA sessions receive no user/audit data.
5. Invite a standard user and confirm the user can see only their own settings, predictions, portfolio, broker connections, backtests, strategies, orders, and sessions.
6. Enable the global kill switch as an MFA-enabled administrator. Confirm every paper order attempt is rejected. Release it only after deliberate confirmation.
7. Confirm `/api/health` exposes no token, database URL, provider secret, or raw audit data.
8. Use browser developer tools to verify `/api/*` responses have `Cache-Control: no-store`; go offline and confirm the PWA does not display stale account/market API content.
9. Keep `ENABLE_REMOTE_PAPER_ORDERS=false` and `ENABLE_LIVE_TRADING=false`. The present release is secure preview-only for trading.

## 7. Local development

For the zero-credential demo shell, Docker remains available:

```powershell
Copy-Item .env.example .env
docker compose up --build
```

The demo shell intentionally does not bypass authentication for private data. Configure a local Supabase project and set the public/secret keys when testing the signed-in workflow. Use an HTTPS tunnel or a provider-supported local callback URL for OAuth/MFA experiments; do not expose a development database or `.env` publicly.

## Operational notes

- The service worker only caches static assets. It does not cache navigations or `/api/*` responses.
- The bundled rate limiter is per backend process. Introduce a shared Redis-backed rate limiter before running multiple API replicas.
- OAuth broker tokens are not stored by the application. Do not enable a broker callback or remote paper execution until a reviewed encrypted secret manager and token-rotation design are in place.
- Live trading is not supported by this release. `ENABLE_LIVE_TRADING=true` is rejected in production.
- Rotate Supabase, audit HMAC, broker, and database credentials through their host/provider controls and review the MarketMind audit log after a suspected incident.

For the detailed controls and threat boundaries, read [SECURITY.md](SECURITY.md).
