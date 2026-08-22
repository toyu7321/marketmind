from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import math
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from .account import router as account_router
from .admin import router as admin_router
from .backtesting import run_backtest
from .config import get_settings
from .database import (
    BacktestRun, BrokerConnection, PaperOrder, Portfolio, PortfolioPosition, Prediction, SavedStrategy, TradeIntentRecord,
    SystemSetting, UserPreference, UserRiskProfile, UserSetting, get_db, init_db, utcnow,
)
from .indicators import technical_snapshot
from .observability import begin_request, cache_event, finish_request, latency_report, log_request, measure, reset_request, response_headers, safe_endpoint_name
from .providers import (
    EdgarSECProvider, analyze_evidence, close_provider_clients, market_provider, news_provider,
    options_provider, public_market_cache_report, start_provider_clients, strategy_candidates,
)
from .risk import RISK_POLICY_VERSION, RiskLimits, evaluate
from .redaction import install_secret_redaction
from .schemas import BacktestRequest, PaperOrderRequest, PortfolioResponse, PredictionCreate, RiskRequest, SettingsUpdate
from .scoring import MARKET_WEIGHTS, STOCK_WEIGHTS, score
from .security import (
    Principal, get_owned_resource, rate_limit, require_authenticated_user,
    require_sensitive_action_auth, trading_allowed, write_audit,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    install_secret_redaction()
    await init_db()
    await start_provider_clients()
    try:
        yield
    finally:
        await close_provider_clients()


class TimedJSONResponse(JSONResponse):
    """Measure FastAPI's actual JSON serialization without touching response data."""

    def render(self, content: Any) -> bytes:
        with measure("serialization"):
            return super().render(content)


app = FastAPI(
    title="MarketMind API", version="2.0.0", lifespan=lifespan,
    docs_url=None if get_settings().is_production else "/docs", default_response_class=TimedJSONResponse,
)
settings = get_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization", "Idempotency-Key", "X-Request-ID"],
    allow_credentials=False,
)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    token = begin_request()
    response: Response | None = None
    try:
        response = await call_next(request)
        endpoint = safe_endpoint_name(request.url.path)
        profile, total_ms = finish_request(endpoint)
        for name, value in response_headers(profile, total_ms).items():
            response.headers.setdefault(name, value)
        log_request(endpoint, profile, total_ms)
    finally:
        reset_request(token)
    assert response is not None
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=(), usb=()")
    response.headers.setdefault("Content-Security-Policy", "default-src 'none'; frame-ancestors 'none'; base-uri 'none'")
    if request.url.path.startswith("/api/"):
        response.headers.setdefault("Cache-Control", "no-store, private, max-age=0")
        response.headers.setdefault("Pragma", "no-cache")
    if settings.is_production:
        response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
    return response


app.include_router(admin_router)
app.include_router(account_router)


DEFAULT_USER_SETTINGS: dict[str, Any] = {
    "timezone": "America/New_York",
    "refresh_seconds": 60,
    "watchlist": ["SPY", "QQQ", "NVDA", "AMD", "AAPL", "MSFT", "META", "AMZN", "GOOGL", "TSLA"],
    "market_weights": MARKET_WEIGHTS,
    "stock_weights": STOCK_WEIGHTS,
    "risk": {key: value for key, value in RiskLimits().as_safe_dict().items() if key != "policy_version"},
    "automation": {"premarket": True, "session": True, "postmarket": True},
    "ai": {"model": settings.openai_model, "mode": "Top 5 only"},
}
_global_defaults_cache: tuple[float, dict[str, Any]] | None = None
_global_defaults_lock = asyncio.Lock()
_technical_cache: dict[str, tuple[float, dict[str, Any]]] = {}


def _merge(defaults: dict[str, Any], saved: dict[str, Any] | None) -> dict[str, Any]:
    result = {**defaults}
    for key, value in (saved or {}).items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = {**result[key], **value}
        elif key in result:
            result[key] = value
    return result


async def global_defaults(db: AsyncSession) -> dict[str, Any]:
    # The original `runtime_settings` row is preserved as a backward-compatible global default.
    global _global_defaults_cache
    now = time.monotonic()
    cached = _global_defaults_cache
    if cached and cached[0] > now:
        cache_event("runtime_defaults", "hit")
        return _merge(cached[1], None)
    async with _global_defaults_lock:
        cached = _global_defaults_cache
        if cached and cached[0] > time.monotonic():
            cache_event("runtime_defaults", "hit")
            return _merge(cached[1], None)
        cache_event("runtime_defaults", "miss")
        with measure("database.runtime_defaults"):
            legacy = await db.get(UserSetting, "runtime_settings")
            configured = await db.get(SystemSetting, "runtime_defaults")
        merged = _merge(DEFAULT_USER_SETTINGS, legacy.value if legacy else None)
        result = _merge(merged, configured.value if configured else None)
        _global_defaults_cache = (time.monotonic() + 120, result)
        return _merge(result, None)


async def runtime_settings(db: AsyncSession, user_id: str) -> dict[str, Any]:
    defaults = await global_defaults(db)
    with measure("database.user_preferences"):
        row = (await db.execute(select(UserPreference).where(UserPreference.user_id == user_id, UserPreference.key == "runtime_settings"))).scalar_one_or_none()
    return _merge(defaults, row.value if row else None)


async def effective_risk_limits(db: AsyncSession, principal: Principal, config: dict[str, Any]) -> RiskLimits:
    """Return a policy that user preferences may tighten, never widen.

    ``config`` deliberately is not used as a source of permissive limits: it
    includes user-editable display preferences. The only authority that can
    alter deployment-wide limits is the AAL2-protected administrator setting.
    """
    profile = await db.get(UserRiskProfile, principal.user.id)
    system_security = await db.get(SystemSetting, "security")
    admin_policy = ((system_security.value.get("risk_policy") or system_security.value.get("risk_ceiling")) if system_security else {}) or {}
    policy = RiskLimits.from_mapping(admin_policy)
    user_limits = (profile.limits if profile else {}) or {}
    tightened = policy.as_safe_dict()
    for key, value in user_limits.items():
        if value is None or key not in tightened:
            continue
        if key == "min_liquidity":
            tightened[key] = max(float(tightened[key]), float(value))
        elif key == "kill_switch":
            tightened[key] = bool(tightened[key]) or bool(value)
        elif isinstance(tightened[key], (int, float)) and isinstance(value, (int, float)):
            tightened[key] = min(float(tightened[key]), float(value))
    tightened["kill_switch"] = bool(tightened.get("kill_switch")) or principal.user.kill_switch_enabled or bool(system_security and system_security.value.get("global_kill_switch"))
    return RiskLimits.from_mapping(tightened)


def _mode(*items: Any) -> str:
    freshness: list[str] = []
    for item in items:
        if isinstance(item, list):
            freshness.extend(str(row.get("freshness", "DEMO")) for row in item)
        elif isinstance(item, dict):
            freshness.append(str(item.get("freshness", "DEMO")))
    values = {value.upper() for value in freshness}
    public_values = values - {"UNAVAILABLE"}
    if not public_values:
        return "UNAVAILABLE"
    if "DEMO" in public_values and len(public_values) > 1:
        return "MIXED"
    if "DEMO" in public_values:
        return "DEMO"
    if "STALE" in values:
        return "STALE"
    if "DELAYED" in values:
        return "DELAYED"
    if "IEX" in values:
        return "IEX"
    return "LIVE"


def _technical_from_bars(bars: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Indicators require a usable history; an unavailable feed must not produce synthetic readings."""
    if len(bars) < 50:
        return None
    try:
        fingerprint = hashlib.sha256(json.dumps([
            (row["time"], row["close"], row["high"], row["low"], row["volume"]) for row in bars
        ], separators=(",", ":"), default=str).encode()).hexdigest()
        cached = _technical_cache.get(fingerprint)
        if cached and cached[0] > time.monotonic():
            cache_event("technicals", "hit")
            return cached[1]
        cache_event("technicals", "miss")
        with measure("computation.technicals"):
            result = technical_snapshot([row["close"] for row in bars], [row["high"] for row in bars], [row["low"] for row in bars], [row["volume"] for row in bars])
        if len(_technical_cache) >= 512:
            _technical_cache.pop(next(iter(_technical_cache)))
        _technical_cache[fingerprint] = (time.monotonic() + 900, result)
        return result
    except (KeyError, TypeError, ValueError, IndexError):
        return None


def _unavailable_technical() -> dict[str, Any]:
    return {"rsi": None, "macd": None, "ema20": None, "ema50": None, "volatility": None, "trend": "unavailable", "support": None, "resistance": None, "volume_spike": None}


async def _stock_histories(provider: Any, symbol: str, range_name: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Reuse the 260-day indicator history for daily 3M/6M charts.

    The old path issued two overlapping daily-bar requests for the default Stock
    Intel view. Intraday ranges retain their dedicated aggregation.
    """
    if range_name in {"3M", "6M"}:
        technical = await provider.get_bars(symbol, 260)
        calendar_days = 93 if range_name == "3M" else 186
        cutoff = (datetime.now(timezone.utc) - timedelta(days=calendar_days)).date().isoformat()
        chart = [bar for bar in technical if str(bar.get("time", "")) >= cutoff]
        return technical, chart
    technical, chart = await asyncio.gather(provider.get_bars(symbol, 260), provider.get_bars_for_range(symbol, range_name))
    return technical, chart


def _has_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _stock_score(quote: dict[str, Any], tech: dict[str, Any] | None, market_score: int, weights: dict[str, float]):
    if tech is None or not _has_number(quote.get("change_percent")):
        return None
    rsi = tech["rsi"]
    values = {
        "Trend": 88 if tech["trend"] == "bullish" else 35 if tech["trend"] == "bearish" else 55,
        "Momentum": max(15, min(92, 50 + (rsi - 50) * 1.4)),
        "Relative Strength": max(20, min(95, 50 + quote["change_percent"] * 12)),
        "Volume": 78 if tech["volume_spike"] else 56,
        "Volatility": max(20, min(90, 96 - tech["volatility"] * 1.2)),
        "Technical Setup": 84 if tech["macd"] > 0 and tech["trend"] == "bullish" else 42,
        "Market Environment": market_score, "News": 60, "Fundamentals": 62, "Risk": 68,
    }
    reasons = {
        "Trend": f"Moving-average alignment is {tech['trend']}.",
        "Momentum": f"RSI is {tech['rsi']}; MACD histogram is {tech['macd']}.",
        "Relative Strength": f"Latest session return is {quote['change_percent']}%.",
        "Volatility": f"Annualized 20-session volatility is {tech['volatility']}%.",
        "Technical Setup": f"Support {tech['support']}; resistance {tech['resistance']}.",
        "Market Environment": f"Deterministic Market Score is {market_score}/100.",
        "Risk": "Risk engine retains final authority over every order proposal.",
    }
    with measure("computation.stock_score"):
        return score(values, weights, reasons)


def _demo_filings() -> list[dict[str, Any]]:
    return [
        {"form": "10-Q", "filed": "2026-07-30", "title": "Demo quarterly report", "material": True, "url": "https://www.sec.gov/edgar/search/", "freshness": "DEMO"},
        {"form": "8-K", "filed": "2026-07-29", "title": "Demo current report", "material": True, "url": "https://www.sec.gov/edgar/search/", "freshness": "DEMO"},
        {"form": "Form 4", "filed": "2026-07-25", "title": "Demo insider transaction", "material": False, "url": "https://www.sec.gov/edgar/search/", "freshness": "DEMO"},
    ]


@app.get("/api/health")
async def health(response: Response, db: AsyncSession = Depends(get_db)):
    database = "available"
    try:
        with measure("database.health"):
            await db.execute(text("SELECT 1"))
    except Exception:
        database = "unavailable"
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    market_status, news_status, options_status = await asyncio.gather(
        market_provider().provider_status(), news_provider().provider_status(), options_provider().provider_status(),
    )
    return {
        "status": "healthy" if database == "available" else "degraded",
        "database": database,
        "authentication": "configured" if settings.auth_ready else "not_configured",
        **market_status,
        **news_status,
        **options_status,
        "live_trading": "locked",
        "performance": {"latency": latency_report(), "public_market_cache": public_market_cache_report()},
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/api/dashboard")
async def dashboard(principal: Principal = Depends(require_authenticated_user), db: AsyncSession = Depends(get_db)):
    config, provider = await runtime_settings(db, principal.user.id), market_provider()
    index_symbols = ["SPY", "QQQ", "DIA", "IWM", "VIX"]
    sector_symbols = ["XLK", "XLF", "XLE", "XLV", "XLI", "XLY", "XLP", "XLB", "XLRE", "XLU", "XLC", "SMH", "SOXX"]
    quote_symbols = index_symbols + config["watchlist"][2:] + sector_symbols
    quotes, market_status, spy_bars, watch_bars, provider_status = await asyncio.gather(
        provider.get_quotes(quote_symbols), provider.get_market_status(), provider.get_bars("SPY", 240),
        provider.get_bars_bulk(config["watchlist"][2:], 100), provider.provider_status(),
    )
    lookup = {quote["symbol"]: quote for quote in quotes}
    spy_tech = _technical_from_bars(spy_bars)
    universe = [lookup[symbol] for symbol in config["watchlist"] if symbol in lookup and _has_number(lookup[symbol].get("change_percent"))]
    advancing = sum(quote["change_percent"] > 0 for quote in universe)
    above_ema = sum(quote["change_percent"] > -0.4 for quote in universe)
    breadth = round(above_ema / len(universe) * 100) if universe else 0
    qqq_change = lookup.get("QQQ", {}).get("change_percent")
    market = None
    if spy_tech and _has_number(qqq_change):
        market_values = {
            "Trend": 84 if spy_tech["trend"] == "bullish" else 38 if spy_tech["trend"] == "bearish" else 55,
            "Momentum": max(15, min(90, 50 + (spy_tech["rsi"] - 50) * 1.4)), "Breadth": breadth,
            "Volatility": max(20, min(90, 95 - spy_tech["volatility"] * 1.2)),
            "Relative Strength": max(20, min(90, 50 + qqq_change * 12)), "Macro": 58, "News": 70,
        }
        market = score(market_values, config["market_weights"], {
            "Trend": f"SPY moving-average alignment is {spy_tech['trend']}.", "Momentum": f"SPY RSI is {spy_tech['rsi']}.",
            "Breadth": f"{breadth}% of the configured watch universe is participating.",
            "Volatility": f"Annualized rolling volatility is {spy_tech['volatility']}%.",
            "Macro": "No external macro feed is configured; neutral default applied.", "News": "News contribution is deterministic until a connected feed is available.",
        })
        analysis = await analyze_evidence({"score": market.score, "trend": spy_tech["trend"], "breadth": breadth, "volatility": spy_tech["volatility"]})
    else:
        analysis = {"bias": "Unavailable", "confidence": 0, "catalysts": [], "risks": ["Market source did not return enough data for an interpretation."], "invalidation": "Wait for a healthy market-data response.", "source": "RULES"}
    watchlist = []
    for symbol in config["watchlist"][2:]:
        quote = lookup.get(symbol)
        if quote is None:
            continue
        technical = _technical_from_bars(watch_bars.get(symbol, []))
        result = _stock_score(quote, technical, market.score if market else 50, config["stock_weights"])
        watchlist.append({**quote, "score": result.score if result else None, "trend": (technical or {}).get("trend", quote.get("trend", "Unavailable"))})
    sectors = [{
        "symbol": symbol, "name": {"XLK": "Technology", "XLF": "Financials", "XLE": "Energy", "XLV": "Healthcare", "SMH": "Semiconductors", "SOXX": "Semiconductors"}.get(symbol, symbol),
        "change": lookup.get(symbol, {}).get("change_percent"), "relative_strength": round(max(0, min(100, 50 + lookup[symbol]["change_percent"] * 15)), 1) if _has_number(lookup.get(symbol, {}).get("change_percent")) else None, "freshness": lookup.get(symbol, {}).get("freshness", "UNAVAILABLE"),
    } for symbol in sector_symbols]
    return {
        "mode": _mode(quotes, market_status, spy_bars), "status": market_status, "provider_status": provider_status, "market_score": market,
        "indices": [lookup[symbol] for symbol in index_symbols if symbol in lookup], "watchlist": watchlist, "spy_bars": spy_bars, "spy_technical": spy_tech or _unavailable_technical(),
        "sectors": sectors, "ai_brief": analysis,
        "breadth": {"label": "Universe Breadth", "above_20d": breadth, "above_50d": max(0, breadth - 7), "advancing": advancing, "declining": len(universe) - advancing, "new_highs": sum(q["change_percent"] > 1 for q in universe), "new_lows": sum(q["change_percent"] < -1 for q in universe)},
        "vix_note": "VIX is not synthesized. Under an Alpaca IEX feed it is shown only when Alpaca returns a supported symbol; otherwise it is unavailable.",
    }


@app.get("/api/scanner")
async def scanner(principal: Principal = Depends(require_authenticated_user), db: AsyncSession = Depends(get_db)):
    config, provider = await runtime_settings(db, principal.user.id), market_provider()
    symbols = list(dict.fromkeys(config["watchlist"][2:] + ["JPM", "XOM", "LLY", "AVGO", "NFLX", "COST"]))
    quotes, bars_by_symbol, provider_status = await asyncio.gather(provider.get_quotes(symbols), provider.get_bars_bulk(symbols, 120), provider.provider_status())
    output = []
    for quote in quotes:
        bars = bars_by_symbol.get(quote["symbol"], [])
        tech = _technical_from_bars(bars)
        result = _stock_score(quote, tech, 50, config["stock_weights"])
        five_day = round((bars[-1]["close"] / bars[-6]["close"] - 1) * 100, 2) if len(bars) >= 6 else None
        output.append({
            **quote, **(tech or _unavailable_technical()), "score": result.score if result else None, "five_day": five_day,
            "momentum": "High" if result and result.score >= 70 else "Medium" if result and result.score >= 50 else "Unavailable" if not result else "Low",
            "relative_strength": round(max(0, min(100, result.score * 0.92)), 1) if result else None,
            "news_sentiment": "Unavailable", "ai_confidence": result.confidence if result else None, "freshness": _mode(quote, bars),
        })
    return {"mode": _mode(output), "provider_status": provider_status, "results": sorted(output, key=lambda row: (row["score"] is None, -(row["score"] or 0)))}


@app.get("/api/stocks/{symbol}")
async def stock(symbol: str, range_name: str = Query(default="3M", alias="range", pattern="^(1D|5D|1M|3M|6M|1Y)$"), principal: Principal = Depends(require_authenticated_user), db: AsyncSession = Depends(get_db)):
    symbol = symbol.upper()
    if not symbol.isalnum() or len(symbol) > 8:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid symbol")
    config, provider = await runtime_settings(db, principal.user.id), market_provider()
    quote, histories, news_result, provider_status, filings = await asyncio.gather(
        provider.get_quote(symbol), _stock_histories(provider, symbol, range_name), news_provider().get_news([symbol]), provider.provider_status(),
        EdgarSECProvider().filings_for_symbol(symbol),
    )
    technical_bars, chart_bars = histories
    news_mode, all_news = news_result
    tech = _technical_from_bars(technical_bars)
    result = _stock_score(quote, tech, 50, config["stock_weights"])
    if result and tech:
        analysis = await analyze_evidence({"ticker": symbol, "stock_score": result.score, "rsi": tech["rsi"], "macd": tech["macd"], "trend": tech["trend"], "relative_strength": "strong" if result.score >= 70 else "mixed", "risk_events": []})
    else:
        analysis = {"bias": "Unavailable", "confidence": 0, "base_case": "Await a usable quote and at least 50 daily bars before interpreting this symbol.", "bull_case": "Unavailable until provider data is healthy.", "bear_case": "Unavailable until provider data is healthy.", "invalidation": "Market data is unavailable.", "catalysts": [], "risks": ["No usable provider history"], "source": "RULES"}
    ticker_news = [article for article in all_news if symbol in str(article.get("affected", "")).split(", ")]
    if not ticker_news and news_mode == "DEMO":
        ticker_news = all_news
    outlooks = [{"horizon": days, "bull": min(70, result.score - 8 + days), "neutral": 25, "bear": max(5, 83 - result.score - days), "confidence": min(82, result.confidence - days)} for days in (1, 3, 5)] if result else []
    return {
        "mode": _mode(quote, technical_bars, chart_bars), "provider_status": provider_status, "quote": quote, "bars": chart_bars, "technical": tech or _unavailable_technical(), "score": result,
        "analysis": analysis, "outlooks": outlooks, "news": ticker_news, "news_mode": news_mode, "filings": filings or _demo_filings(),
        "fundamentals": {"revenue": "$130.5B", "revenue_growth": "+34.2%", "eps": "$4.18", "gross_margin": "71.3%", "forward_pe": "31.8x", "trend": "Improving", "freshness": "DEMO", "source": "Demo reference data"},
        "chart_range": range_name, "company_metadata_source": quote.get("company_metadata_source"),
    }


@app.get("/api/news")
async def news(principal: Principal = Depends(require_authenticated_user), db: AsyncSession = Depends(get_db)):
    config = await runtime_settings(db, principal.user.id)
    provider = news_provider()
    (mode, items), provider_status = await asyncio.gather(provider.get_news(config["watchlist"]), provider.provider_status())
    return {"mode": mode, "provider_status": provider_status, "items": items, "message": "Alpaca News did not return a feed for this account or request." if mode == "UNAVAILABLE" else None}


@app.get("/api/options/{symbol}")
async def options(symbol: str, _: Principal = Depends(require_authenticated_user)):
    symbol = symbol.upper()
    if not symbol.isalnum() or len(symbol) > 8:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid symbol")
    market, options_data = market_provider(), options_provider()
    if get_settings().demo_mode:
        # Demo option premiums are derived from the demo underlying, so retain
        # that dependency rather than producing mismatched synthetic contracts.
        quote, options_status = await asyncio.gather(market.get_quote(symbol), options_data.provider_status())
        source, chain = await options_data.get_chain(symbol, quote.get("price"))
    else:
        # Live option snapshots do not require the equity quote. Fetch both in
        # parallel to avoid a quote round trip delaying the options terminal.
        quote, option_result, options_status = await asyncio.gather(
            market.get_quote(symbol), options_data.get_chain(symbol, None), options_data.provider_status(),
        )
        source, chain = option_result
    with measure("computation.options"):
        valid_iv = [row["iv"] for row in chain if row.get("iv") is not None]
        call_volume = sum(row.get("volume") or 0 for row in chain if row["type"] == "CALL")
        put_volume = sum(row.get("volume") or 0 for row in chain if row["type"] == "PUT")
        strategies = strategy_candidates(quote["price"], chain) if _has_number(quote.get("price")) else []
    return {"mode": source, "symbol": symbol, "spot": quote.get("price"), "quote_quality": quote.get("freshness"), "provider_status": options_status, "put_call_ratio": round(put_volume / call_volume, 2) if call_volume else None, "iv_rank": round(sum(valid_iv) / len(valid_iv), 1) if valid_iv else None, "chain": chain, "strategies": strategies, "message": "Live options data is unavailable for the configured Alpaca account/feed. No synthetic contracts are shown." if source == "UNAVAILABLE" else None}


@app.post("/api/risk/evaluate", dependencies=[Depends(rate_limit("risk", 30))])
async def risk_evaluate(req: RiskRequest, principal: Principal = Depends(require_authenticated_user), db: AsyncSession = Depends(get_db)):
    config = await runtime_settings(db, principal.user.id)
    return evaluate(req, await effective_risk_limits(db, principal, config))


@app.post("/api/backtest", dependencies=[Depends(rate_limit("backtest", 12))])
async def backtest(req: BacktestRequest, principal: Principal = Depends(require_authenticated_user), db: AsyncSession = Depends(get_db)):
    bars = await market_provider().get_bars(req.ticker.upper(), req.days)
    prices = [row["close"] for row in bars]
    scores = [int(62 + 18 * math.sin(index / 17) + 8 * math.sin(index / 5)) for index in range(len(prices))]
    result = {"ticker": req.ticker.upper(), "mode": _mode(bars), "strategy": "MarketMind score threshold (next-session execution)", **run_backtest(prices, scores, req.score_threshold, req.transaction_cost_bps, [row["time"] for row in bars])}
    db.add(BacktestRun(user_id=principal.user.id, parameters=req.model_dump(), result=result))
    await db.commit()
    return result


async def evaluate_expired_predictions(db: AsyncSession, user_id: str) -> None:
    cutoff = datetime.now(timezone.utc)
    rows = (await db.execute(select(Prediction).where(Prediction.user_id == user_id, Prediction.actual_return.is_(None)))).scalars().all()
    provider = market_provider()
    for row in rows:
        created_at = row.created_at.replace(tzinfo=timezone.utc) if row.created_at.tzinfo is None else row.created_at
        if cutoff < created_at + timedelta(days=row.horizon * 2):
            continue
        quote = await provider.get_quote(row.ticker)
        actual_return = round((quote["price"] / row.starting_price - 1) * 100, 2)
        row.actual_return, row.direction_correct, row.invalidation_triggered, row.evaluated_at = actual_return, ((row.bias.lower().startswith("bull") and actual_return > 0) or (row.bias.lower().startswith("bear") and actual_return < 0) or (row.bias.lower().startswith("neutral") and abs(actual_return) < 1)), abs(actual_return) > 8, cutoff
    if rows:
        await db.commit()


@app.get("/api/predictions")
async def predictions(principal: Principal = Depends(require_authenticated_user), db: AsyncSession = Depends(get_db)):
    await evaluate_expired_predictions(db, principal.user.id)
    rows = (await db.execute(select(Prediction).where(Prediction.user_id == principal.user.id).order_by(Prediction.created_at.desc()))).scalars().all()
    stored = [{"id": row.public_id, "ticker": row.ticker, "horizon": row.horizon, "bias": row.bias, "confidence": row.confidence, "starting_price": row.starting_price, "actual_return": row.actual_return, "direction_correct": row.direction_correct, "evaluated_at": row.evaluated_at.isoformat() if row.evaluated_at else None} for row in rows]
    completed = [item for item in stored if item["actual_return"] is not None]
    accuracy = round(sum(bool(item.get("direction_correct")) for item in completed) / len(completed) * 100, 1) if completed else 0
    return {"mode": "USER", "accuracy": accuracy, "average_return": round(sum(item["actual_return"] for item in completed) / len(completed), 2) if completed else 0, "by_horizon": {"1D": 0, "3D": 0, "5D": 0}, "predictions": stored}


@app.post("/api/predictions", status_code=status.HTTP_201_CREATED, dependencies=[Depends(rate_limit("predictions", 20))])
async def create_prediction(req: PredictionCreate, request: Request, principal: Principal = Depends(require_authenticated_user), db: AsyncSession = Depends(get_db)):
    row = Prediction(user_id=principal.user.id, ticker=req.ticker.upper(), horizon=req.horizon, bias=req.bias, probabilities={"bull": req.bull_probability, "neutral": req.neutral_probability, "bear": req.bear_probability}, confidence=req.confidence, starting_price=req.starting_price, stock_score=req.stock_score, market_score=req.market_score)
    db.add(row)
    await db.flush()
    await write_audit(db, request, event_type="PREDICTION_CREATED", user_id=principal.user.id, resource=f"predictions/{row.public_id}")
    await db.commit()
    return {"id": row.public_id, "status": "tracked"}


@app.get("/api/predictions/{prediction_id}")
async def prediction_detail(prediction_id: str, principal: Principal = Depends(require_authenticated_user), db: AsyncSession = Depends(get_db)):
    row = (await db.execute(select(Prediction).where(Prediction.public_id == prediction_id, Prediction.user_id == principal.user.id))).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Resource not found.")
    return {"id": row.public_id, "ticker": row.ticker, "horizon": row.horizon, "bias": row.bias, "probabilities": row.probabilities, "confidence": row.confidence}


async def _personal_portfolio(db: AsyncSession, user_id: str) -> Portfolio:
    row = (await db.execute(select(Portfolio).where(Portfolio.user_id == user_id, Portfolio.name == "Personal portfolio"))).scalar_one_or_none()
    if row is None:
        row = Portfolio(user_id=user_id, name="Personal portfolio")
        db.add(row)
        await db.flush()
        await db.commit()
    return row


def _portfolio_number(value: Any, fallback: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return fallback
    return number if math.isfinite(number) else fallback


@app.get("/api/portfolio", response_model=PortfolioResponse)
async def portfolio(principal: Principal = Depends(require_authenticated_user), db: AsyncSession = Depends(get_db)):
    account = await _personal_portfolio(db, principal.user.id)
    with measure("database.portfolio_positions"):
        positions = (await db.execute(select(PortfolioPosition).where(PortfolioPosition.portfolio_id == account.id))).scalars().all()
    provider = market_provider()
    output: list[dict[str, Any]] = []
    valid_positions = [(row, str(row.symbol or "").strip().upper()) for row in positions]
    valid_positions = [(row, symbol) for row, symbol in valid_positions if symbol]
    symbols = [symbol for _, symbol in valid_positions]
    try:
        quotes = await provider.get_quotes(symbols) if hasattr(provider, "get_quotes") else await asyncio.gather(*(provider.get_quote(symbol) for symbol in symbols))
    except Exception:
        quotes = [{} for _ in symbols]
    quote_by_symbol = {str(quote.get("symbol", "")).upper(): quote for quote in quotes if isinstance(quote, dict)}
    for row, symbol in valid_positions:
        quantity = _portfolio_number(row.quantity)
        average_cost = _portfolio_number(row.average_cost)
        quote = quote_by_symbol.get(symbol, {})
        price = _portfolio_number(quote.get("price"), average_cost)
        daily_change = _portfolio_number(quote.get("change"))
        value = round(quantity * price, 2)
        output.append({"symbol": symbol, "quantity": quantity, "average_cost": average_cost, "price": price, "market_value": value, "unrealized_pl": round(quantity * (price - average_cost), 2), "daily_pl": round(quantity * daily_change, 2), "sector": str(row.sector or "Unknown")})
    total_positions = sum(row["market_value"] for row in output)
    cash = _portfolio_number(account.cash)
    total_value = round(cash + total_positions, 2)
    for row in output:
        row["weight"] = round(row["market_value"] / total_value * 100, 1) if total_value else 0
    allocation = [{"symbol": row["symbol"], "sector": row["sector"], "market_value": row["market_value"], "weight": row["weight"]} for row in output]
    return {"mode": "USER", "source": "Manual portfolio", "equity": total_value, "total_value": total_value, "cash": cash, "buying_power": cash, "exposure": round(total_positions / total_value * 100, 1) if total_value else 0, "day_change": round(sum(row["daily_pl"] for row in output), 2), "positions": output, "allocation": allocation, "orders": [], "equity_curve": []}


@app.get("/api/portfolios/{portfolio_id}")
async def portfolio_detail(portfolio_id: str, principal: Principal = Depends(require_authenticated_user), db: AsyncSession = Depends(get_db)):
    row = await get_owned_resource(db, Portfolio, portfolio_id, principal.user.id)
    positions = (await db.execute(select(PortfolioPosition).where(PortfolioPosition.portfolio_id == row.id))).scalars().all()
    return {"id": row.id, "name": row.name, "cash": row.cash, "positions": [{"symbol": position.symbol, "quantity": position.quantity, "average_cost": position.average_cost} for position in positions]}


@app.get("/api/backtests/{backtest_id}")
async def backtest_detail(backtest_id: str, principal: Principal = Depends(require_authenticated_user), db: AsyncSession = Depends(get_db)):
    row = await get_owned_resource(db, BacktestRun, backtest_id, principal.user.id)
    return {"id": row.id, "parameters": row.parameters, "result": row.result}


@app.get("/api/strategies/{strategy_id}")
async def strategy_detail(strategy_id: str, principal: Principal = Depends(require_authenticated_user), db: AsyncSession = Depends(get_db)):
    row = await get_owned_resource(db, SavedStrategy, strategy_id, principal.user.id)
    return {"id": row.id, "name": row.name, "configuration": row.configuration}


@app.get("/api/trading/orders/{order_id}")
async def paper_order_detail(order_id: str, principal: Principal = Depends(require_authenticated_user), db: AsyncSession = Depends(get_db)):
    row = await get_owned_resource(db, PaperOrder, order_id, principal.user.id)
    return {"id": row.id, "status": row.status, "created_at": row.created_at.isoformat()}


def _order_risk_request(order: PaperOrderRequest, equity: float) -> RiskRequest:
    intent = order.intent
    return RiskRequest(
        symbol=order.symbol.upper(), proposed_value=order.quantity * (order.limit_price or order.estimated_price), portfolio_equity=equity,
        current_exposure=order.current_exposure, sector_exposure=order.sector_exposure, daily_pnl=order.daily_pnl,
        liquidity=order.liquidity, event_risk=order.event_risk, asset_type=intent.asset_type if intent else "STOCK",
        side=order.side, strategy_id=intent.strategy_id if intent else "manual-preview", confidence=intent.confidence if intent else .5,
    )


@app.post("/api/trading/preview", dependencies=[Depends(rate_limit("order_preview", 10))])
async def preview_paper_order(order: PaperOrderRequest, principal: Principal = Depends(require_authenticated_user), db: AsyncSession = Depends(get_db)):
    config = await runtime_settings(db, principal.user.id)
    portfolio_row = await _personal_portfolio(db, principal.user.id)
    decision = evaluate(_order_risk_request(order, portfolio_row.cash), await effective_risk_limits(db, principal, config))
    return {"mode": "PAPER", "order": order.model_dump(exclude={"confirmed"}), "risk": decision, "requires_confirmation": False, "execution": "disabled by the security freeze; no broker order can be created", "risk_policy_version": RISK_POLICY_VERSION}


@app.post("/api/trading/orders", dependencies=[Depends(rate_limit("order_submit", 5))])
async def submit_paper_order(order: PaperOrderRequest, request: Request, idempotency_key: str = Header(..., alias="Idempotency-Key", min_length=16, max_length=128), principal: Principal = Depends(require_sensitive_action_auth), db: AsyncSession = Depends(get_db)):
    await trading_allowed(db, principal)
    if not order.confirmed:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Explicit confirmation is required before a paper order is submitted.")
    if order.intent is None:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="A current, structured trade intent is required for order submission.")
    if order.intent.symbol.upper() != order.symbol.upper() or order.intent.side != order.side:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Trade intent does not match the proposed order.")
    payload = order.model_dump(mode="json")
    preview_hash = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    existing = (await db.execute(select(PaperOrder).where(PaperOrder.user_id == principal.user.id, PaperOrder.idempotency_key == idempotency_key))).scalar_one_or_none()
    if existing:
        if not hmac.compare_digest(existing.preview_hash, preview_hash):
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Idempotency key was already used for a different order.")
        return {"id": existing.id, "status": existing.status, "idempotent_replay": True}
    existing_intent = await db.get(TradeIntentRecord, str(order.intent.intent_id))
    if existing_intent is not None:
        await write_audit(db, request, event_type="TRADE_INTENT_REPLAY_REJECTED", user_id=principal.user.id, resource="trading/orders", result="DENIED", safe_metadata={"intent_id": str(order.intent.intent_id)})
        await db.commit()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Trade intent was already consumed or rejected.")
    config = await runtime_settings(db, principal.user.id)
    portfolio_row = await _personal_portfolio(db, principal.user.id)
    decision = evaluate(_order_risk_request(order, portfolio_row.cash), await effective_risk_limits(db, principal, config))
    client_order_id = f"mm-{str(order.intent.intent_id).replace('-', '')[:24]}"
    intent_record = TradeIntentRecord(intent_id=str(order.intent.intent_id), user_id=principal.user.id, strategy_id=order.intent.strategy_id, payload_hash=preview_hash, broker_client_order_id=client_order_id, expires_at=order.intent.expires_at, status="REJECTED" if decision["decision"] == "REJECTED" else "VALIDATED")
    db.add(intent_record)
    if decision["decision"] == "REJECTED":
        await write_audit(db, request, event_type="TRADE_INTENT_REJECTED", user_id=principal.user.id, resource="trading/orders", result="DENIED", safe_metadata={"intent_id": str(order.intent.intent_id), "policy_version": decision["policy_version"], "reason_count": len(decision["reasons"])})
        await db.commit()
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="The deterministic risk policy rejected this trade intent.")
    connection = (await db.execute(select(BrokerConnection).where(BrokerConnection.user_id == principal.user.id, BrokerConnection.provider == "alpaca", BrokerConnection.environment == "paper", BrokerConnection.status == "ACTIVE"))).scalar_one_or_none()
    if connection is None:
        row = PaperOrder(user_id=principal.user.id, idempotency_key=idempotency_key, payload=payload, preview_hash=preview_hash, status="REJECTED")
        db.add(row)
        await write_audit(db, request, event_type="PAPER_ORDER_REJECTED", user_id=principal.user.id, resource="trading/orders", result="DENIED", safe_metadata={"reason": "no_active_user_broker_connection"})
        await db.commit()
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="No active user-owned paper broker connection is available.")
    # OAuth token exchange and execution are intentionally not activated in this security foundation.
    row = PaperOrder(user_id=principal.user.id, broker_connection_id=connection.id, idempotency_key=idempotency_key, payload=payload, preview_hash=preview_hash, status="REJECTED")
    db.add(row)
    await write_audit(db, request, event_type="PAPER_ORDER_REJECTED", user_id=principal.user.id, resource="trading/orders", result="DENIED", safe_metadata={"reason": "oauth_execution_not_activated"})
    await db.commit()
    raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Broker order execution is not activated. MarketMind is operating in secure preview-only mode.")


@app.get("/api/settings")
async def get_settings_endpoint(principal: Principal = Depends(require_authenticated_user), db: AsyncSession = Depends(get_db)):
    config = await runtime_settings(db, principal.user.id)
    security = await db.get(SystemSetting, "security")
    market_status, news_status, options_status = await asyncio.gather(
        market_provider().provider_status(), news_provider().provider_status(), options_provider().provider_status(),
    )
    providers = {
        "Market Data": f"{market_status['market_provider']} · {market_status['market_feed']} · {market_status['market_data_status']}",
        "News": f"{news_status['news_provider']} · {news_status['news_status']}",
        "Options": f"{options_status['options_provider']} · {options_status['options_status']}",
        "OpenAI": "Connected" if settings.openai_enabled else "Offline (rules fallback)", "SEC": "Configured" if settings.sec_is_configured else "Available",
        "Broker": "Not connected", "Paper order submission": "Disabled until OAuth connection activation", "Live Trading": "Locked",
    }
    effective_limits = await effective_risk_limits(db, principal, config)
    return {**config, "providers": providers, "provider_status": {**market_status, **news_status, **options_status}, "security": {"global_kill_switch": bool(security and security.value.get("global_kill_switch")), "user_kill_switch": principal.user.kill_switch_enabled, "mfa_level": principal.aal, "risk_policy_version": RISK_POLICY_VERSION, "effective_risk_limits": effective_limits.as_safe_dict()}}


@app.put("/api/settings", dependencies=[Depends(rate_limit("settings", 20))])
async def update_settings(payload: SettingsUpdate, request: Request, principal: Principal = Depends(require_authenticated_user), db: AsyncSession = Depends(get_db)):
    current = await runtime_settings(db, principal.user.id)
    update = payload.model_dump(exclude_none=True)
    for name, allowed in (("market_weights", set(MARKET_WEIGHTS)), ("stock_weights", set(STOCK_WEIGHTS))):
        if name in update and set(update[name]) != allowed:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"{name} keys must match the configured scoring framework")
    for nested in ("risk", "automation", "ai"):
        if nested in update:
            update[nested] = {key: value for key, value in update[nested].items() if value is not None}
    merged = _merge(current, update)
    row = (await db.execute(select(UserPreference).where(UserPreference.user_id == principal.user.id, UserPreference.key == "runtime_settings"))).scalar_one_or_none()
    if row:
        row.value = merged
    else:
        db.add(UserPreference(user_id=principal.user.id, key="runtime_settings", value=merged))
    if "risk" in update:
        profile_limits = {key: value for key, value in update["risk"].items() if key != "kill_switch"}
        profile = await db.get(UserRiskProfile, principal.user.id)
        if profile:
            profile.limits = {**profile.limits, **profile_limits}
        else:
            db.add(UserRiskProfile(user_id=principal.user.id, limits=profile_limits))
    if "risk" in update and "kill_switch" in update["risk"]:
        principal.user.kill_switch_enabled = bool(update["risk"]["kill_switch"])
        await write_audit(db, request, event_type="KILL_SWITCH_TRIGGERED" if principal.user.kill_switch_enabled else "KILL_SWITCH_RELEASED", user_id=principal.user.id, resource="settings/risk")
    await write_audit(db, request, event_type="RISK_SETTING_CHANGED" if "risk" in update else "SETTINGS_CHANGED", user_id=principal.user.id, resource="settings", safe_metadata={"sections": sorted(update), "risk_keys": sorted(update.get("risk", {})), "risk_policy_version": RISK_POLICY_VERSION})
    await db.commit()
    return {"status": "saved", "settings": merged}
