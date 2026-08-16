# Deploy MarketMind to the Internet

This guide deploys the existing shared Next.js/FastAPI codebase. It does **not** create a separate Windows or iPhone application. After deployment, install the same HTTPS PWA on Windows and iPhone.

The example uses Vercel for the frontend and Render for FastAPI/PostgreSQL because both support GitHub-connected deployments and generated HTTPS URLs. Railway, Fly.io, or another Docker-compatible host can substitute for Render; the backend has no vendor-specific runtime dependency.

## Before you start

You need accounts for:

1. GitHub — this repository is the source of truth.
2. A backend host with a managed PostgreSQL service (Render is the example below).
3. Vercel for the Next.js frontend.

You do **not** need a custom domain or market-data keys for the first deployment. Keep `ENABLE_LIVE_TRADING=false`.

> Security warning: MarketMind has no login system yet. Do not deploy broker credentials or turn on remote paper orders on a publicly reachable app. Leave `ENABLE_REMOTE_PAPER_ORDERS=false` until a real authentication layer is added.

## Part A — Deploy Backend

Complete Part B first so you have the database URL, then create the API service:

1. In Render, select **New +** then **Web Service** and connect `toyu7321/marketmind`.
2. Select branch `main`.
3. Set **Root Directory** to `backend`.
4. Choose the Docker runtime. Render builds `backend/Dockerfile`; other container hosts should do the same.
5. Set the health-check path to `/api/health`.
6. Add these environment variables:

| Key | First deployment value |
| --- | --- |
| `MARKETMIND_ENV` | `production` |
| `DATABASE_URL` | Your internal `postgresql+asyncpg://...` URL |
| `FRONTEND_ORIGIN` | A temporary HTTPS placeholder such as `https://marketmind-placeholder.vercel.app` |
| `CORS_ORIGINS` | The same temporary HTTPS placeholder |
| `ENABLE_PAPER_TRADING` | `true` |
| `ENABLE_REMOTE_PAPER_ORDERS` | `false` |
| `ENABLE_LIVE_TRADING` | `false` |
| `ENABLE_OPENAI_ANALYSIS` | `false` unless you intentionally add an OpenAI key |
| `SEC_USER_AGENT` | Leave the example until you are ready to configure a real SEC identity |

7. Do not add Alpaca or OpenAI values yet. The deployed API is fully usable in **DEMO** mode.
8. Deploy. The container runs `alembic upgrade head` and then starts Uvicorn on the host-provided `$PORT`.
9. Copy the generated backend URL, for example `https://marketmind-api-xxxx.onrender.com`.
10. Visit `https://YOUR_BACKEND/api/health`. It should return JSON with `status: "healthy"`, `database: "connected"`, and demo/provider statuses.

Render supports Docker web services, environment variables, and HTTP health checks. Its health checker accepts a `2xx` or `3xx` result, so the existing database-aware endpoint is suitable.

## Part B — Deploy PostgreSQL

1. In Render, create a PostgreSQL database in the same region you will use for the API.
2. Copy its **internal** database URL from the database connection page. Internal networking avoids exposing the database to the internet.
3. MarketMind uses SQLAlchemy async drivers, so change the copied scheme from `postgresql://` to `postgresql+asyncpg://` before setting `DATABASE_URL` in Part A.
4. Do not add this value to GitHub, Vercel, browser variables, or a frontend `.env` file.

PostgreSQL is persistent managed storage. Do not put a production SQLite database inside the API container filesystem.

## Part C — Deploy the Next.js PWA frontend on Vercel

1. In Vercel, select **Add New → Project** and import `toyu7321/marketmind`.
2. Before deploying, set **Root Directory** to `frontend`.
3. Confirm the framework is Next.js. `frontend/vercel.json` uses the committed pnpm lockfile for the install and build commands.
4. Add this environment variable for **Production** and **Preview**:

| Key | Value |
| --- | --- |
| `BACKEND_URL` | The HTTPS backend URL from Part A, without `/api` |

5. Leave `NEXT_PUBLIC_API_BASE_URL` blank for the recommended same-origin proxy setup. Browser requests stay at `/api/*` on the PWA origin and Vercel rewrites them server-side to `BACKEND_URL`.
6. Deploy and copy the generated HTTPS URL, for example `https://marketmind-xxxx.vercel.app`.

### Optional direct API mode

Instead of the proxy, set `NEXT_PUBLIC_API_BASE_URL=https://YOUR_BACKEND` in Vercel. This value is compiled into browser JavaScript and must be public. In this mode, CORS configuration in Part D is required for every frontend origin.

## Part D — Connect the frontend and backend safely

Return to the API service environment variables and replace both temporary values with the actual Vercel URL:

```text
FRONTEND_ORIGIN=https://marketmind-xxxx.vercel.app
CORS_ORIGINS=https://marketmind-xxxx.vercel.app
```

Save and redeploy the API. Production startup rejects wildcard CORS origins and non-HTTPS origins by design. If you later add a custom domain, append it as a comma-separated explicit origin and redeploy:

```text
CORS_ORIGINS=https://marketmind-xxxx.vercel.app,https://marketmind.example.com
```

## Part E — Test the production deployment

1. Open the Vercel URL in a normal browser tab.
2. Confirm **DEMO DATA** appears when no provider keys are configured.
3. Open `https://YOUR_BACKEND/api/health` and confirm the database is connected.
4. Visit Dashboard, Scanner, Stock Intel, Options, Backtest, Portfolio, and Settings.
5. Turn airplane mode on after one successful visit, reopen the installed app, and confirm the offline shell truthfully states that live market services are unavailable.
6. Publish a harmless change to `main`; the GitHub-connected hosts should rebuild automatically. When the installed PWA sees a new worker, choose **Refresh** rather than being force-reloaded.

## Part F — Install the Windows app

1. Open the Vercel HTTPS URL in Microsoft Edge or Chrome.
2. Choose **Install MarketMind** from the address bar or browser menu.
3. Confirm. Windows places the installed PWA in the Start Menu.
4. Open it once, then right-click the Start Menu item to pin it to the taskbar. If required, use **Open file location** from the Start Menu item to create a desktop shortcut.

The installed PWA opens in its own window. Do not use a `localhost` address for this normal daily workflow after cloud deployment.

## Part G — Install the iPhone app

1. Open the Vercel HTTPS URL in **Safari**.
2. Tap **Share → Add to Home Screen**.
3. Keep **Open as Web App** enabled if offered.
4. Tap **Add**, then launch MarketMind from the Home Screen.

Safari uses the supplied Apple touch icon and mobile web-app metadata. The PWA layout includes notch/Dynamic Island and home-indicator safe-area spacing.

### Offline and update behavior

The service worker caches only the application shell, icons, manifest, and already visited same-origin static assets. Navigation is network-first and falls back to `/offline`; API responses are deliberately never cached or presented as live market data. A newly downloaded service worker waits until the user selects the **Refresh** prompt, so MarketMind does not reload during active work.

## Local Mode remains available

Cloud deployment does not replace local mode. On Windows with Docker Desktop:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
./scripts/start-marketmind.ps1
```

This starts local frontend and backend at `http://localhost:3000` and `http://localhost:8000`. Stop safely with `./scripts/stop-marketmind.ps1`; it does not delete the database volume.

## Environment-variable reference

### Backend-only secrets and configuration

```text
MARKETMIND_ENV=production
DATABASE_URL=postgresql+asyncpg://USER:PASSWORD@HOST:PORT/DATABASE
FRONTEND_ORIGIN=https://marketmind-xxxx.vercel.app
CORS_ORIGINS=https://marketmind-xxxx.vercel.app
OPENAI_API_KEY=
OPENAI_MODEL=gpt-5-mini
ENABLE_OPENAI_ANALYSIS=false
ALPACA_API_KEY=
ALPACA_SECRET_KEY=
ALPACA_BASE_URL=https://paper-api.alpaca.markets
ALPACA_DATA_URL=https://data.alpaca.markets
ALPACA_FEED=iex
SEC_USER_AGENT=YourApp your-email@example.com
ENABLE_PAPER_TRADING=true
ENABLE_REMOTE_PAPER_ORDERS=false
ENABLE_LIVE_TRADING=false
```

### Frontend host configuration

```text
BACKEND_URL=https://marketmind-api-xxxx.onrender.com
NEXT_PUBLIC_API_BASE_URL=
```

`NEXT_PUBLIC_API_BASE_URL` is the only frontend value and is intentionally public. Never prefix a secret with `NEXT_PUBLIC_`.

## Authentication and broker safety

Authentication is not yet implemented. The API has validation, controlled CORS, backend-only secrets, disabled live trading, and a production guard that disables remote paper-order submission by default, but it is **not** a replacement for real user authentication.

Before connecting any broker account, add a vetted authentication and authorization layer (for example, an identity provider with server-side session enforcement), restrict production access to the intended user, and add audit logging. Never treat a private GitHub repository as access control for a deployed URL.

## GitHub deployment workflow

```text
Codex or local change -> commit and push to main -> GitHub -> Vercel/Render rebuild -> installed PWA offers refresh
```

Vercel supports selecting a monorepo root directory for its project. Render can build the backend Dockerfile from its own root directory and uses `/api/health` for readiness. See the official [Vercel monorepo guide](https://vercel.com/docs/monorepos), [Vercel build configuration](https://vercel.com/docs/builds/configure-a-build), [Render Docker guide](https://render.com/docs/docker), and [Render health-check guide](https://render.com/docs/health-checks) for provider UI changes.
