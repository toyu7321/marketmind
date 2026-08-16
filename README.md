# MarketMind

**AI Market Intelligence Terminal** is a responsive, installable personal US-equity research workstation. It combines deterministic scores, technical analysis, market scanning, provider adapters, AI interpretation, portfolio analysis, backtesting, and a risk-controlled paper-trading workflow.

> Data calculates. Rules classify. AI interprets. Risk controls.

MarketMind is research software, not investment advice. It does not promise results or autonomous real-money trading.

## What is included

- Dashboard, markets, scanner, stock intelligence, news, options, predictions, backtesting, portfolio, paper trading, and settings.
- FastAPI provider boundaries with explicit Alpaca, OpenAI, SEC EDGAR, options, and broker fallbacks.
- An invite-only, fail-closed multi-user foundation with Supabase Auth, TOTP MFA, server-verified JWTs, per-user ownership, audit events, and security controls.
- Clearly labelled demo market data when optional market-data, news, AI, or SEC credentials are absent.
- Installable PWA metadata, self-hosted app icons, Apple metadata, offline application shell, update prompt, and standalone launch screen.
- Desktop, tablet, and phone layouts with a bottom navigation and iPhone safe-area support.
- Docker Compose, SQLite local fallback, PostgreSQL production support, Alembic migrations, health diagnostics, and validation scripts.

## Install MarketMind on Windows

After you deploy MarketMind, open its HTTPS URL in Microsoft Edge or Chrome.

1. Open the browser menu or address-bar install button and choose **Install MarketMind** / **Install app**.
2. Confirm the installation. MarketMind opens in its own application window.
3. Find **MarketMind** in the Windows Start Menu.
4. Right-click it and choose **Pin to taskbar** if desired.
5. If a desktop icon was not created, open the Start Menu, right-click MarketMind, choose **Open file location**, then create a shortcut from that location.

After installation, launch MarketMind from the icon instead of typing its URL. The installed app receives updated web assets when you accept the in-app refresh prompt.

## Install MarketMind on iPhone

An iPhone must use the deployed **HTTPS** URL in Safari. Local HTTP addresses do not provide the complete install experience.

1. Open the MarketMind URL in **Safari**.
2. Tap **Share**.
3. Choose **Add to Home Screen**.
4. If iOS offers **Open as Web App**, leave it enabled.
5. Tap **Add** and launch MarketMind from the Home Screen.

The manifest, Apple touch icon, standalone display mode, safe-area CSS, and startup screen are included in this repository. Launching from the Home Screen opens the app without normal Safari chrome.

## Local Mode (Windows)

### Recommended one-click Docker startup

1. Install and start Docker Desktop.
2. Open PowerShell in this repository.
3. Run:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
./scripts/start-marketmind.ps1
```

The helper creates a safe demo `.env` when needed, starts Docker Compose without deleting data, waits for the API health check, and opens `http://localhost:3000`.

Stop the local stack while preserving its PostgreSQL volume:

```powershell
./scripts/stop-marketmind.ps1
```

### Manual startup

```powershell
Copy-Item .env.example .env
docker compose up --build
```

Open `http://localhost:3000`. API diagnostics are at `http://localhost:8000/api/health` and API documentation is at `http://localhost:8000/docs`.

Market-data and AI keys are optional: empty provider credentials deliberately activate visible **DEMO DATA** after sign-in. Authentication is intentionally not optional for private application data. Configure a local Supabase project before using Dashboard, Scanner, Portfolio, or Settings.

## Cloud Mode

Deploy the frontend and backend as separate HTTPS services. The recommended first deployment is:

```text
Vercel (Next.js PWA)  ->  Render or Railway (FastAPI container)  ->  managed PostgreSQL
```

The computer running this repository does not need to remain powered on after cloud deployment. The complete beginner-oriented procedure is in [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md).

Use the provider-generated URLs first. A custom domain is optional and no code depends on one.

## Environment variables

Copy `.env.example` and keep secrets only in the backend host configuration.

| Variable | Where | Purpose |
| --- | --- | --- |
| `MARKETMIND_ENV` | backend | `development` locally; `production` in the cloud |
| `DATABASE_URL` | backend | SQLite locally or `postgresql+asyncpg://...` in production |
| `FRONTEND_ORIGIN`, `CORS_ORIGINS` | backend | Explicit HTTPS frontend origin(s) in production |
| `OPENAI_API_KEY`, `ALPACA_*`, `SEC_USER_AGENT` | backend | Optional provider credentials only |
| `SUPABASE_URL`, `SUPABASE_PUBLISHABLE_KEY` | backend | Identity provider URL and public key used to verify browser-issued access tokens |
| `SUPABASE_SECRET_KEY` | backend only | Server-only invitation capability; never expose it to Vercel/browser code |
| `AUDIT_IP_HMAC_SECRET` | backend | Required production secret for privacy-preserving audit IP hashing |
| `BOOTSTRAP_ADMIN_ENABLED`, `BOOTSTRAP_ADMIN_SECRET` | backend | Disabled-by-default, one-time remote first-admin setup only; remove the secret after use |
| `ENABLE_PAPER_TRADING` | backend | Enables the paper-broker architecture, not unreviewed execution |
| `ENABLE_REMOTE_PAPER_ORDERS` | backend | Defaults to `false`; current release stays preview-only even when configured |
| `ENABLE_LIVE_TRADING` | backend | Must remain `false`; production rejects `true` |
| `BACKEND_URL` | frontend host only | Server-side same-origin API proxy target, for example `https://marketmind-api.example-host.app` |
| `NEXT_PUBLIC_SUPABASE_URL`, `NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY` | frontend host | Public Supabase browser configuration; never place secrets under `NEXT_PUBLIC_` |

## Security status

MarketMind now fails closed: private routes require a provider-verified identity and an active MarketMind user record. The Admin Console is invite-only and requires TOTP MFA; every owned resource is scoped to the authenticated user and direct-object access attempts return a generic not-found response. The security page includes MFA setup, session visibility, and session revocation. Administrative actions, user/risk changes, broker connection setup, and blocked trade attempts are audited without logging secrets or raw addresses.

**Do not connect broker credentials to a public deployment until you have completed the setup and review checklist.** Remote paper execution is deliberately rejected by this release pending approved OAuth token storage. Live trading remains locked and production rejects `ENABLE_LIVE_TRADING=true`.

Do not commit `.env`, database files, token files, or provider keys. See [docs/SECURITY.md](docs/SECURITY.md) for the threat boundaries and [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) for exact Supabase, Vercel, Render, migration, and first-admin setup.

## Validation

```powershell
cd backend
pytest -q

cd ../frontend
npm run typecheck
npm run lint
npm run build
```

The production health endpoint checks API and database availability:

```text
GET /api/health
```

It reports backend, database, market provider, AI provider, SEC, broker, paper-order safety, and live-trading state without exposing credentials.
