from __future__ import annotations

import asyncio
import hashlib
import json
import math
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from .account import router as account_router
from .admin import router as admin_router
from .backtesting import run_backtest
from .config import get_settings
from .database import (
    BacktestRun, BrokerConnection, PaperOrder, Portfolio, PortfolioPosition, Prediction, SavedStrategy,
    SystemSetting, UserPreference, UserRiskProfile, UserSetting, get_db, init_db, utcnow,
)
from .indicators import technical_snapshot
from .providers import EdgarSECProvider, analyze_evidence, market_provider, news_provider, options_provider, strategy_candidates
from .risk import RiskLimits, evaluate
from .schemas import BacktestRequest, PaperOrderRequest, PortfolioResponse, PredictionCreate, RiskRequest, SettingsUpdate
from .scoring import MARKET_WEIGHTS, STOCK_WEIGHTS, score
from .security import (
    Principal, get_owned_resource, rate_limit, require_authenticated_user,
    require_sensitive_action_auth, trading_allowed, write_audit,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    yield


app = FastAPI(title="MarketMind API", version="2.0.0", lifespan=lifespan, docs_url=None if get_settings().is_production else "/docs")
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
    response = await call_next(request)
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
    "risk": {
        "max_position_pct": 10, "max_exposure_pct": 80, "max_sector_pct": 30,
        "max_daily_loss_pct": 3, "min_liquidity": 1_000_000, "kill_switch": False,
    },
    "automation": {"premarket": True, "session": True, "postmarket": True},
    "ai": {"model": settings.openai_model, "mode": "Top 5 only"},
}


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
    legacy = await db.get(UserSetting, "runtime_settings")
    configured = await db.get(SystemSetting, "runtime_defaults")
    merged = _merge(DEFAULT_USER_SETTINGS, legacy.value if legacy else None)
    return _merge(merged, configured.value if configured else None)


async def runtime_settings(db: AsyncSession, user_id: str) -> dict[str, Any]:
    defaults = await global_defaults(db)
    row = (await db.execute(select(UserPreference).where(UserPreference.user_id == user_id, UserPreference.key == "runtime_settings"))).scalar_one_or_none()
    return _merge(defaults, row.value if row else None)


async def effective_risk_limits(db: AsyncSession, principal: Principal, config: dict[str, Any]) -> RiskLimits:
    profile = await db.get(UserRiskProfile, principal.user.id)
    limits = _merge(config["risk"], profile.limits if profile else None)
    system_security = await db.get(SystemSetting, "security")
    ceilings = (system_security.value.get("risk_ceiling") if system_security else {}) or {}
    for key, ceiling in ceilings.items():
        if ceiling is not None and key in limits and isinstance(limits[key], (int, float)):
            limits[key] = min(limits[key], ceiling)
    limits["kill_switch"] = bool(limits.get("kill_switch")) or principal.user.kill_switch_enabled or bool(system_security and system_security.value.get("global_kill_switch"))
    return RiskLimits(**limits)


def _mode(*items: Any) -> str:
    freshness: list[str] = []
    for item in items:
        if isinstance(item, list):
            freshness.extend(str(row.get("freshness", "DEMO")) for row in item)
        elif isinstance(item, dict):
            freshness.append(str(item.get("freshness", "DEMO")))
    live, demo = any(value == "LIVE" for value in freshness), any(value == "DEMO" for value in freshness)
    return "MIXED" if live and demo else "LIVE" if live else "DEMO"


def _stock_score(quote: dict[str, Any], tech: dict[str, Any], market_score: int, weights: dict[str, float]):
    rsi = tech["rsi"]
    values = {
        "Trend": 88 if tech["trend"] == "bullish" else 35 if tech["trend"] == "bearish" else 55,
        "Momentum": max(15, min(92, 50 + (rsi - 50) * 1.4)),
        "Relative Strength": max(20, min(95, 50 + quote["change_percent"] * 12 + quote["score"] * 0.25)),
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
        await db.execute(text("SELECT 1"))
    except Exception:
        database = "unavailable"
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return {
        "status": "healthy" if database == "available" else "degraded",
        "database": database,
        "authentication": "configured" if settings.auth_ready else "not_configured",
        "live_trading": "locked",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/api/dashboard")
async def dashboard(principal: Principal = Depends(require_authenticated_user), db: AsyncSession = Depends(get_db)):
    config, provider = await runtime_settings(db, principal.user.id), market_provider()
    index_symbols = ["SPY", "QQQ", "DIA", "IWM", "VIX"]
    sector_symbols = ["XLK", "XLF", "XLE", "XLV", "XLI", "XLY", "XLP", "XLB", "XLRE", "XLU", "XLC", "SMH", "SOXX"]
    quote_symbols = index_symbols + config["watchlist"][2:] + sector_symbols
    quotes, market_status, spy_bars = await asyncio.gather(
        asyncio.gather(*(provider.get_quote(symbol) for symbol in quote_symbols)), provider.get_market_status(), provider.get_bars("SPY", 240)
    )
    lookup = {quote["symbol"]: quote for quote in quotes}
    spy_tech = technical_snapshot([row["close"] for row in spy_bars], [row["high"] for row in spy_bars], [row["low"] for row in spy_bars], [row["volume"] for row in spy_bars])
    universe = [lookup[symbol] for symbol in config["watchlist"] if symbol in lookup]
    advancing = sum(quote["change_percent"] > 0 for quote in universe)
    above_ema = sum(quote["change_percent"] > -0.4 for quote in universe)
    breadth = round(above_ema / len(universe) * 100) if universe else 0
    market_values = {
        "Trend": 84 if spy_tech["trend"] == "bullish" else 38 if spy_tech["trend"] == "bearish" else 55,
        "Momentum": max(15, min(90, 50 + (spy_tech["rsi"] - 50) * 1.4)), "Breadth": breadth,
        "Volatility": max(20, min(90, 95 - spy_tech["volatility"] * 1.2)),
        "Relative Strength": max(20, min(90, 50 + lookup["QQQ"]["change_percent"] * 12)), "Macro": 58, "News": 70,
    }
    market = score(market_values, config["market_weights"], {
        "Trend": f"SPY moving-average alignment is {spy_tech['trend']}.", "Momentum": f"SPY RSI is {spy_tech['rsi']}.",
        "Breadth": f"{breadth}% of the configured watch universe is participating.",
        "Volatility": f"Annualized rolling volatility is {spy_tech['volatility']}%.",
        "Macro": "No external macro feed is configured; neutral default applied.", "News": "News contribution is deterministic until a connected feed is available.",
    })
    analysis = await analyze_evidence({"score": market.score, "trend": spy_tech["trend"], "breadth": breadth, "volatility": spy_tech["volatility"]})
    sectors = [{
        "symbol": symbol, "name": {"XLK": "Technology", "XLF": "Financials", "XLE": "Energy", "XLV": "Healthcare", "SMH": "Semiconductors", "SOXX": "Semiconductors"}.get(symbol, symbol),
        "change": lookup[symbol]["change_percent"], "relative_strength": round(max(0, min(100, 50 + lookup[symbol]["change_percent"] * 15)), 1), "freshness": lookup[symbol]["freshness"],
    } for symbol in sector_symbols]
    return {
        "mode": _mode(quotes, market_status), "status": market_status, "market_score": market,
        "indices": [lookup[symbol] for symbol in index_symbols], "watchlist": [lookup[symbol] for symbol in config["watchlist"][2:] if symbol in lookup],
        "sectors": sectors, "ai_brief": analysis,
        "breadth": {"label": "Universe Breadth", "above_20d": breadth, "above_50d": max(0, breadth - 7), "advancing": advancing, "declining": len(universe) - advancing, "new_highs": sum(q["change_percent"] > 1 for q in universe), "new_lows": sum(q["change_percent"] < -1 for q in universe)},
    }


@app.get("/api/scanner")
async def scanner(principal: Principal = Depends(require_authenticated_user), db: AsyncSession = Depends(get_db)):
    config, provider = await runtime_settings(db, principal.user.id), market_provider()
    symbols = list(dict.fromkeys(config["watchlist"][2:] + ["JPM", "XOM", "LLY", "AVGO", "NFLX", "COST"]))
    quotes = await asyncio.gather(*(provider.get_quote(symbol) for symbol in symbols))
    output = []
    for quote in quotes:
        bars = await provider.get_bars(quote["symbol"], 90)
        tech = technical_snapshot([row["close"] for row in bars], [row["high"] for row in bars], [row["low"] for row in bars], [row["volume"] for row in bars])
        result = _stock_score(quote, tech, 70, config["stock_weights"])
        output.append({**quote, **tech, "score": result.score, "five_day": round((bars[-1]["close"] / bars[-6]["close"] - 1) * 100, 2), "momentum": "High" if result.score >= 70 else "Medium" if result.score >= 50 else "Low", "relative_strength": round(max(0, min(100, result.score * 0.92)), 1), "news_sentiment": "Positive" if quote["change_percent"] > 0 else "Neutral", "ai_confidence": min(88, result.confidence), "freshness": _mode(quote, bars)})
    return {"mode": _mode(output), "results": sorted(output, key=lambda row: row["score"], reverse=True)}


@app.get("/api/stocks/{symbol}")
async def stock(symbol: str, principal: Principal = Depends(require_authenticated_user), db: AsyncSession = Depends(get_db)):
    symbol = symbol.upper()
    if not symbol.isalnum() or len(symbol) > 8:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid symbol")
    config, provider = await runtime_settings(db, principal.user.id), market_provider()
    quote, bars = await asyncio.gather(provider.get_quote(symbol), provider.get_bars(symbol, 240))
    tech = technical_snapshot([row["close"] for row in bars], [row["high"] for row in bars], [row["low"] for row in bars], [row["volume"] for row in bars])
    result = _stock_score(quote, tech, 70, config["stock_weights"])
    analysis = await analyze_evidence({"ticker": symbol, "stock_score": result.score, "rsi": tech["rsi"], "macd": tech["macd"], "trend": tech["trend"], "relative_strength": "strong" if result.score >= 70 else "mixed", "risk_events": []})
    filings = await EdgarSECProvider().filings_for_symbol(symbol)
    news = [{"time": "14:32", "headline": f"{quote['company']} remains in focus as investors assess sector momentum", "tickers": [symbol], "sector": "Technology", "sentiment": "Positive", "importance": "High", "why": "Demo context is shown until a connected ticker-news feed returns related articles.", "freshness": "DEMO"}]
    return {"mode": _mode(quote, bars, filings), "quote": quote, "bars": bars, "technical": tech, "score": result, "analysis": analysis, "outlooks": [{"horizon": days, "bull": min(70, result.score - 8 + days), "neutral": 25, "bear": max(5, 83 - result.score - days), "confidence": min(82, result.confidence - days)} for days in (1, 3, 5)], "news": news, "filings": filings or _demo_filings(), "fundamentals": {"revenue": "$130.5B", "revenue_growth": "+34.2%", "eps": "$4.18", "gross_margin": "71.3%", "forward_pe": "31.8x", "trend": "Improving", "freshness": "DEMO"}}


@app.get("/api/news")
async def news(principal: Principal = Depends(require_authenticated_user), db: AsyncSession = Depends(get_db)):
    config = await runtime_settings(db, principal.user.id)
    mode, items = await news_provider().get_news(config["watchlist"])
    return {"mode": mode, "items": items}


@app.get("/api/options/{symbol}")
async def options(symbol: str, _: Principal = Depends(require_authenticated_user)):
    symbol = symbol.upper()
    if not symbol.isalnum() or len(symbol) > 8:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid symbol")
    quote = await market_provider().get_quote(symbol)
    source, chain = await options_provider().get_chain(symbol, quote["price"])
    valid_iv = [row["iv"] for row in chain if row.get("iv") is not None]
    call_volume = sum(row.get("volume") or 0 for row in chain if row["type"] == "CALL")
    put_volume = sum(row.get("volume") or 0 for row in chain if row["type"] == "PUT")
    return {"mode": source, "symbol": symbol, "spot": quote["price"], "put_call_ratio": round(put_volume / call_volume, 2) if call_volume else None, "iv_rank": round(sum(valid_iv) / len(valid_iv), 1) if valid_iv else None, "chain": chain, "strategies": strategy_candidates(quote["price"], chain)}


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
    positions = (await db.execute(select(PortfolioPosition).where(PortfolioPosition.portfolio_id == account.id))).scalars().all()
    provider = market_provider()
    output: list[dict[str, Any]] = []
    for row in positions:
        symbol = str(row.symbol or "").strip().upper()
        if not symbol:
            continue
        quantity = _portfolio_number(row.quantity)
        average_cost = _portfolio_number(row.average_cost)
        try:
            quote = await provider.get_quote(symbol)
        except Exception:
            quote = {}
        quote = quote if isinstance(quote, dict) else {}
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
    return RiskRequest(symbol=order.symbol.upper(), proposed_value=order.quantity * (order.limit_price or order.estimated_price), portfolio_equity=equity, current_exposure=order.current_exposure, sector_exposure=order.sector_exposure, daily_pnl=order.daily_pnl, liquidity=order.liquidity, event_risk=order.event_risk)


@app.post("/api/trading/preview", dependencies=[Depends(rate_limit("order_preview", 10))])
async def preview_paper_order(order: PaperOrderRequest, principal: Principal = Depends(require_authenticated_user), db: AsyncSession = Depends(get_db)):
    config = await runtime_settings(db, principal.user.id)
    portfolio_row = await _personal_portfolio(db, principal.user.id)
    decision = evaluate(_order_risk_request(order, portfolio_row.cash), await effective_risk_limits(db, principal, config))
    return {"mode": "PAPER", "order": order.model_dump(exclude={"confirmed"}), "risk": decision, "requires_confirmation": decision["decision"] == "APPROVED", "execution": "disabled until a user-owned OAuth broker connection is activated"}


@app.post("/api/trading/orders", dependencies=[Depends(rate_limit("order_submit", 5))])
async def submit_paper_order(order: PaperOrderRequest, request: Request, idempotency_key: str = Header(..., alias="Idempotency-Key", min_length=16, max_length=128), principal: Principal = Depends(require_sensitive_action_auth), db: AsyncSession = Depends(get_db)):
    await trading_allowed(db, principal)
    if not order.confirmed:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Explicit confirmation is required before a paper order is submitted.")
    existing = (await db.execute(select(PaperOrder).where(PaperOrder.user_id == principal.user.id, PaperOrder.idempotency_key == idempotency_key))).scalar_one_or_none()
    if existing:
        return {"id": existing.id, "status": existing.status, "idempotent_replay": True}
    connection = (await db.execute(select(BrokerConnection).where(BrokerConnection.user_id == principal.user.id, BrokerConnection.provider == "alpaca", BrokerConnection.environment == "paper", BrokerConnection.status == "ACTIVE"))).scalar_one_or_none()
    payload = order.model_dump()
    preview_hash = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
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
    return {**config, "providers": {"Market Data": "Demo" if settings.demo_mode else "Alpaca configured", "News": "Demo" if settings.demo_mode else "Alpaca configured", "OpenAI": "Connected" if settings.openai_enabled else "Offline (rules fallback)", "SEC": "Configured" if settings.sec_is_configured else "Available", "Broker": "Not connected", "Paper order submission": "Disabled until OAuth connection activation", "Live Trading": "Locked"}, "security": {"global_kill_switch": bool(security and security.value.get("global_kill_switch")), "user_kill_switch": principal.user.kill_switch_enabled, "mfa_level": principal.aal}}


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
    await write_audit(db, request, event_type="RISK_SETTING_CHANGED" if "risk" in update else "SETTINGS_CHANGED", user_id=principal.user.id, resource="settings", safe_metadata={"sections": sorted(update)})
    await db.commit()
    return {"status": "saved", "settings": merged}
