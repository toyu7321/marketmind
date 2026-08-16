# MarketMind

**AI Market Intelligence Terminal** is a local-first, responsive US-equity workstation. It combines deterministic market calculations, explainable scores, structured AI interpretation, provider adapters, and a risk engine that has final authority over every proposed order.

> Data calculates. Rules classify. AI interprets. Risk controls.

MarketMind is research and paper-trading software, not investment advice. Scenarios, scores, and backtests do not guarantee an outcome.

## What is included

- A dark quantitative command center with a market regime score, index tape, universe breadth, sector leadership, watchlist ranking, and structured market brief.
- A scanner and individual stock workspace with local technical indicators, explainable stock scores, scenario outlooks, fundamentals, SEC filings, and linked intelligence.
- Real provider boundaries for Alpaca stocks, Alpaca news, Alpaca options chains, Alpaca paper brokerage, SEC EDGAR, and OpenAI Responses. Every provider has an explicit demo fallback.
- Deterministic SMA, EMA, RSI, MACD, ATR, Bollinger Bands, rolling volatility, rate of change, support/resistance, moving-average alignment, and volume-spike calculations.
- A next-session score backtester with transaction costs, CAGR, Sharpe, Sortino, drawdown, trade history, monthly returns, and benchmark comparison.
- A paper-only execution desk with a mandatory risk preview and a separate confirmation before an Alpaca paper order can be sent.
- Persisted settings and predictions, including prediction evaluation once an outlook reaches its horizon.
- Desktop, iPad, and phone layouts, a command palette (Ctrl or Command + K), PWA manifest, and kiosk mode.

## Architecture

~~~text
Browser: Next.js 15 + React 19 + TypeScript + Recharts
                         |
                     /api rewrite
                         |
FastAPI + Pydantic + deterministic engines + provider adapters
                         |
     SQLite for local development / PostgreSQL 16 in Docker Compose
~~~

Secrets remain in the FastAPI environment. No browser variable contains an API key.

## FIRST RUN ON YOUR COMPUTER

No provider keys are required. With an unchanged .env file, MarketMind starts in clearly labeled demo mode and every major screen remains usable.

### Windows with Docker Desktop

1. Install and start Docker Desktop. Make sure Docker Desktop shows that the engine is running.
2. Open PowerShell in the MarketMind folder.
3. Create your local configuration:

~~~powershell
Copy-Item .env.example .env
~~~

4. Build and start the complete application:

~~~powershell
docker compose up --build
~~~

5. When both services have started, open:

   - http://localhost:3000 for MarketMind
   - http://localhost:8000/docs for the API documentation
   - http://localhost:8000/api/health for diagnostics

6. In a second PowerShell window, verify the local stack:

~~~powershell
.scriptserify-local.ps1
~~~

Use Ctrl + C in the Compose window to stop MarketMind. Start it again later with docker compose up.

### macOS with Docker Desktop

1. Install and start Docker Desktop.
2. Open Terminal in the MarketMind folder.
3. Create configuration and start:

~~~bash
cp .env.example .env
docker compose up --build
~~~

4. Open http://localhost:3000 in a browser.
5. Verify from another Terminal window:

~~~bash
chmod +x scripts/verify-local.sh
./scripts/verify-local.sh
~~~

### Run without Docker

Requirements: Python 3.12 or later and Node.js 22 or later.

Windows PowerShell:

~~~powershell
Copy-Item .env.example .env
py -3.12 -m venv .venv
..venvScriptsActivate.ps1
pip install -r backendequirements.txt
Set-Location frontend
npm install
Set-Location ..
..venvScriptspython -m alembic -c backendalembic.ini upgrade head
~~~

Start the backend in its own PowerShell:

~~~powershell
..venvScriptspython -m uvicorn app.main:app --app-dir backend --reload --host 0.0.0.0 --port 8000
~~~

Start the frontend in another PowerShell:

~~~powershell
Set-Location frontend
npm run dev
~~~

macOS or Linux:

~~~bash
cp .env.example .env
python3 -m venv .venv
source .venv/bin/activate
pip install -r backend/requirements.txt
(cd frontend && npm install)
(cd backend && alembic upgrade head)
~~~

Then use separate terminals:

~~~bash
source .venv/bin/activate
uvicorn app.main:app --app-dir backend --reload --host 0.0.0.0 --port 8000
~~~

~~~bash
cd frontend
npm run dev
~~~

The frontend and backend bind to 0.0.0.0. They are available at ports 3000 and 8000 respectively.

## iPhone and iPad access on your home network

1. Keep MarketMind running and connect the phone or tablet to the same Wi-Fi as the computer.
2. On Windows, run ipconfig and find the IPv4 address for the active Wi-Fi adapter. On macOS, run ipconfig getifaddr en0.
3. On the device, open http://YOUR_COMPUTER_IP:3000. Example: http://192.168.1.42:3000.
4. If it does not open, allow incoming TCP port 3000 through the computer firewall and confirm the Wi-Fi is not a guest or isolated network.
5. In Safari choose Share, then Add to Home Screen. The supplied web manifest enables standalone app presentation.

The tablet layout preserves a compact two-column terminal at 768 to 1279 pixels. Phones use a single-column priority layout, bottom navigation, and scrollable market tables rather than squeezing the desktop layout into a narrow viewport. Use the top-right panel icon for kiosk mode on a dedicated iPad display.

## Optional data providers

| Environment variable | Purpose |
| --- | --- |
| ALPACA_API_KEY and ALPACA_SECRET_KEY | Alpaca stocks, news, options data, and paper account |
| ALPACA_BASE_URL | Paper brokerage URL; defaults to Alpaca paper |
| ALPACA_DATA_URL | Market-data URL; defaults to Alpaca data |
| ALPACA_FEED | Market-data feed; defaults to iex |
| OPENAI_API_KEY | Optional structured AI interpretation |
| OPENAI_MODEL | Model used only by the server-side OpenAI adapter |
| ENABLE_OPENAI_ANALYSIS | Must be true before MarketMind sends any OpenAI request |
| SEC_USER_AGENT | Descriptive identity required before live EDGAR fetching |
| DATABASE_URL | SQLite local URL or PostgreSQL async URL |
| ENABLE_PAPER_TRADING | Enables paper-broker integration |
| ENABLE_LIVE_TRADING | Defaults to false; no live adapter is installed |
| CORS_ORIGINS | Comma-separated direct API origins |

To enable EDGAR, replace the example SEC user agent with a descriptive application name and a real contact email. This avoids sending the placeholder identity to SEC systems.

### Provider and data labels

- LIVE means a request was supplied by a configured provider.
- DEMO means deterministic synthetic data.
- MIXED means the view combines provider and fallback results.
- Offline rules AI is a deterministic, no-key fallback; OpenAI is used only when a key is configured.
- Alpaca options may return delayed indicative data depending on the account feed. Missing values are shown as a dash, never fabricated as live data.
- Paper broker state is visibly distinct from live. MarketMind has no autonomous real-money execution path.

## Diagnostics, migrations, and verification

The API health endpoint reports backend, database, market adapter, AI adapter, SEC configuration, and broker mode:

http://localhost:8000/api/health

Docker runs Alembic before FastAPI starts. Local migration:

~~~bash
cd backend
alembic upgrade head
~~~

The first migration handles a fresh database and adds the prediction-evaluation fields to an older local schema. For an obsolete disposable demo database, stopping the stack and removing its local data is also safe; never remove a database that contains work you need.

Useful commands:

~~~bash
make test
make lint
make build
make migrate
~~~

Windows users can run the underlying commands in the non-Docker section instead of installing make.

The post-start verification scripts check required files, .env, Compose syntax, backend health, database connectivity reported by the API, and frontend availability:

~~~powershell
.scriptserify-local.ps1
~~~

~~~bash
./scripts/verify-local.sh
~~~

## Testing

Backend tests cover indicators, scoring, risk authority, no-look-ahead backtest calculations, AI schema validation, health diagnostics, demo scanning, and prediction persistence.

~~~bash
cd backend
pytest -q
~~~

Frontend checks:

~~~bash
cd frontend
npm run lint
npm run typecheck
npm run build
~~~

Run these on the local computer after installation. A static check alone is not a successful runtime test.

## Important limits

- Full NYSE/Nasdaq breadth requires a licensed exchange-wide feed. MarketMind labels the available calculation Universe Breadth.
- Demo fundamentals, options, news, filings, historical bars, and paper fills are synthetic and visibly labeled.
- Backtests are research tools. They do not model every corporate action, tax, partial fill, data-revision, liquidity, or survivorship effect.
- The options Strategy Lab calculates payoff summaries from available chain values. It is not a suitability check or a trade recommendation.
- Production use should add authentication, encrypted secret management, rate-limited job execution, observation tooling, and licensed point-in-time datasets.
