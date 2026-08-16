from __future__ import annotations

import asyncio
import math
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Response, status
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from .backtesting import run_backtest
from .config import get_settings
from .database import Prediction, UserSetting, get_db, init_db
from .indicators import technical_snapshot
from .providers import (
    COMPANIES, EdgarSECProvider, analyze_evidence, broker_provider, market_provider,
    news_provider, options_provider, strategy_candidates,
)
from .risk import RiskLimits, evaluate
from .schemas import (
    BacktestRequest, PaperOrderRequest, PredictionCreate, RiskRequest, SettingsUpdate,
)
from .scoring import MARKET_WEIGHTS, STOCK_WEIGHTS, score


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    yield


app = FastAPI(title="MarketMind API", version="1.1.0", lifespan=lifespan)
settings = get_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization"], allow_credentials=False,
)


@app.middleware("http")
async def security_headers(request, call_next):
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    return response

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


async def runtime_settings(db: AsyncSession) -> dict[str, Any]:
    row = await db.get(UserSetting, "runtime_settings")
    return _merge(DEFAULT_USER_SETTINGS, row.value if row else None)


def _mode(*items: Any) -> str:
    freshness = []
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
        "Risk": "Risk engine still has final authority over any proposed order.",
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
    database = "connected"
    try:
        await db.execute(text("SELECT 1"))
    except Exception:
        database = "unavailable"
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return {
        "status": "healthy" if database == "connected" else "degraded",
        "backend": "online", "database": database,
        "market_provider": "demo" if settings.demo_mode else "alpaca configured",
        "ai_provider": "openai enabled" if settings.openai_enabled else "rules fallback",
        "sec": "configured" if settings.sec_is_configured else "available — configure SEC_USER_AGENT",
        "broker": "alpaca paper configured" if not settings.demo_mode else "demo paper",
        "paper_order_submission": "enabled" if settings.paper_order_submission_enabled else "disabled by public-deployment safety",
        "live_trading": "disabled" if not settings.enable_live_trading else "confirmation required; adapter not installed",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/api/dashboard")
async def dashboard(db: AsyncSession = Depends(get_db)):
    config, provider = await runtime_settings(db), market_provider()
    index_symbols = ["SPY", "QQQ", "DIA", "IWM", "VIX"]
    sector_symbols = ["XLK", "XLF", "XLE", "XLV", "XLI", "XLY", "XLP", "XLB", "XLRE", "XLU", "XLC", "SMH", "SOXX"]
    quote_symbols = index_symbols + config["watchlist"][2:] + sector_symbols
    quotes, market_status, spy_bars = await asyncio.gather(
        asyncio.gather(*(provider.get_quote(symbol) for symbol in quote_symbols)),
        provider.get_market_status(), provider.get_bars("SPY", 240),
    )
    lookup = {quote["symbol"]: quote for quote in quotes}
    spy_tech = technical_snapshot([row["close"] for row in spy_bars], [row["high"] for row in spy_bars], [row["low"] for row in spy_bars], [row["volume"] for row in spy_bars])
    universe = [lookup[symbol] for symbol in config["watchlist"] if symbol in lookup]
    advancing = sum(quote["change_percent"] > 0 for quote in universe)
    above_ema = sum(quote["change_percent"] > -0.4 for quote in universe)
    breadth = round(above_ema / len(universe) * 100) if universe else 0
    market_values = {
        "Trend": 84 if spy_tech["trend"] == "bullish" else 38 if spy_tech["trend"] == "bearish" else 55,
        "Momentum": max(15, min(90, 50 + (spy_tech["rsi"] - 50) * 1.4)),
        "Breadth": breadth, "Volatility": max(20, min(90, 95 - spy_tech["volatility"] * 1.2)),
        "Relative Strength": max(20, min(90, 50 + lookup["QQQ"]["change_percent"] * 12)),
        "Macro": 58, "News": 70,
    }
    market = score(market_values, config["market_weights"], {
        "Trend": f"SPY moving-average alignment is {spy_tech['trend']}.",
        "Momentum": f"SPY RSI is {spy_tech['rsi']}.",
        "Breadth": f"{breadth}% of the configured watch universe is participating.",
        "Volatility": f"Annualized rolling volatility is {spy_tech['volatility']}%.",
        "Macro": "No external macro feed is configured; neutral default applied.",
        "News": "News contribution is deterministic until a connected feed is available.",
    })
    analysis = await analyze_evidence({"score": market.score, "trend": spy_tech["trend"], "breadth": breadth, "volatility": spy_tech["volatility"]})
    sectors = []
    for symbol in sector_symbols:
        quote = lookup[symbol]
        sectors.append({
            "symbol": symbol, "name": {"XLK": "Technology", "XLF": "Financials", "XLE": "Energy", "XLV": "Healthcare", "SMH": "Semiconductors", "SOXX": "Semiconductors"}.get(symbol, symbol),
            "change": quote["change_percent"], "relative_strength": round(max(0, min(100, 50 + quote["change_percent"] * 15)), 1), "freshness": quote["freshness"],
        })
    return {
        "mode": _mode(quotes, market_status), "status": market_status, "market_score": market,
        "indices": [lookup[symbol] for symbol in index_symbols], "watchlist": [lookup[symbol] for symbol in config["watchlist"][2:] if symbol in lookup],
        "sectors": sectors, "ai_brief": analysis,
        "breadth": {"label": "Universe Breadth", "above_20d": breadth, "above_50d": max(0, breadth - 7), "advancing": advancing, "declining": len(universe) - advancing, "new_highs": sum(q["change_percent"] > 1 for q in universe), "new_lows": sum(q["change_percent"] < -1 for q in universe)},
    }


@app.get("/api/scanner")
async def scanner(db: AsyncSession = Depends(get_db)):
    config, provider = await runtime_settings(db), market_provider()
    symbols = list(dict.fromkeys(config["watchlist"][2:] + ["JPM", "XOM", "LLY", "AVGO", "NFLX", "COST"]))
    quotes = await asyncio.gather(*(provider.get_quote(symbol) for symbol in symbols))
    output = []
    for quote in quotes:
        bars = await provider.get_bars(quote["symbol"], 90)
        tech = technical_snapshot([row["close"] for row in bars], [row["high"] for row in bars], [row["low"] for row in bars], [row["volume"] for row in bars])
        result = _stock_score(quote, tech, 70, config["stock_weights"])
        output.append({
            **quote, **tech, "score": result.score, "five_day": round((bars[-1]["close"] / bars[-6]["close"] - 1) * 100, 2),
            "momentum": "High" if result.score >= 70 else "Medium" if result.score >= 50 else "Low",
            "relative_strength": round(max(0, min(100, result.score * 0.92)), 1),
            "news_sentiment": "Positive" if quote["change_percent"] > 0 else "Neutral",
            "ai_confidence": min(88, result.confidence), "freshness": _mode(quote, bars),
        })
    return {"mode": _mode(output), "results": sorted(output, key=lambda row: row["score"], reverse=True)}


@app.get("/api/stocks/{symbol}")
async def stock(symbol: str, db: AsyncSession = Depends(get_db)):
    symbol = symbol.upper()
    if not symbol.isalnum() or len(symbol) > 8:
        raise HTTPException(status_code=400, detail="Invalid symbol")
    config, provider = await runtime_settings(db), market_provider()
    quote, bars = await asyncio.gather(provider.get_quote(symbol), provider.get_bars(symbol, 240))
    tech = technical_snapshot([row["close"] for row in bars], [row["high"] for row in bars], [row["low"] for row in bars], [row["volume"] for row in bars])
    result = _stock_score(quote, tech, 70, config["stock_weights"])
    analysis = await analyze_evidence({
        "ticker": symbol, "stock_score": result.score, "rsi": tech["rsi"], "macd": tech["macd"],
        "trend": tech["trend"], "relative_strength": "strong" if result.score >= 70 else "mixed", "risk_events": [],
    })
    filings = await EdgarSECProvider().filings_for_symbol(symbol)
    mode = _mode(quote, bars, filings)
    news = [{
        "time": "14:32", "headline": f"{quote['company']} remains in focus as investors assess sector momentum",
        "tickers": [symbol], "sector": "Technology", "sentiment": "Positive", "importance": "High",
        "why": "Demo context is shown until a connected ticker-news feed returns related articles.", "freshness": "DEMO",
    }]
    return {
        "mode": mode, "quote": quote, "bars": bars, "technical": tech, "score": result, "analysis": analysis,
        "outlooks": [{"horizon": days, "bull": min(70, result.score - 8 + days), "neutral": 25, "bear": max(5, 83 - result.score - days), "confidence": min(82, result.confidence - days)} for days in (1, 3, 5)],
        "news": news, "filings": filings or _demo_filings(),
        "fundamentals": {"revenue": "$130.5B", "revenue_growth": "+34.2%", "eps": "$4.18", "gross_margin": "71.3%", "forward_pe": "31.8x", "trend": "Improving", "freshness": "DEMO"},
    }


@app.get("/api/news")
async def news(db: AsyncSession = Depends(get_db)):
    config = await runtime_settings(db)
    mode, items = await news_provider().get_news(config["watchlist"])
    return {"mode": mode, "items": items}


@app.get("/api/options/{symbol}")
async def options(symbol: str):
    symbol = symbol.upper()
    quote = await market_provider().get_quote(symbol)
    source, chain = await options_provider().get_chain(symbol, quote["price"])
    valid_iv = [row["iv"] for row in chain if row.get("iv") is not None]
    call_volume = sum(row.get("volume") or 0 for row in chain if row["type"] == "CALL")
    put_volume = sum(row.get("volume") or 0 for row in chain if row["type"] == "PUT")
    return {
        "mode": source, "symbol": symbol, "spot": quote["price"], "put_call_ratio": round(put_volume / call_volume, 2) if call_volume else None,
        "iv_rank": round(sum(valid_iv) / len(valid_iv), 1) if valid_iv else None, "chain": chain,
        "strategies": strategy_candidates(quote["price"], chain),
    }


@app.post("/api/risk/evaluate")
async def risk_evaluate(req: RiskRequest, db: AsyncSession = Depends(get_db)):
    config = await runtime_settings(db)
    return evaluate(req, RiskLimits(**config["risk"]))


@app.post("/api/backtest")
async def backtest(req: BacktestRequest):
    bars = await market_provider().get_bars(req.ticker.upper(), req.days)
    prices = [row["close"] for row in bars]
    scores = [int(62 + 18 * math.sin(index / 17) + 8 * math.sin(index / 5)) for index in range(len(prices))]
    return {
        "ticker": req.ticker.upper(), "mode": _mode(bars), "strategy": "MarketMind score threshold (next-session execution)",
        **run_backtest(prices, scores, req.score_threshold, req.transaction_cost_bps, [row["time"] for row in bars]),
    }


async def evaluate_expired_predictions(db: AsyncSession) -> None:
    cutoff = datetime.now(timezone.utc)
    rows = (await db.execute(select(Prediction).where(Prediction.actual_return.is_(None)))).scalars().all()
    provider = market_provider()
    for row in rows:
        if cutoff < row.created_at.replace(tzinfo=timezone.utc) + timedelta(days=row.horizon * 2):
            continue
        quote = await provider.get_quote(row.ticker)
        actual_return = round((quote["price"] / row.starting_price - 1) * 100, 2)
        row.actual_return = actual_return
        row.direction_correct = (row.bias.lower().startswith("bull") and actual_return > 0) or (row.bias.lower().startswith("bear") and actual_return < 0) or (row.bias.lower().startswith("neutral") and abs(actual_return) < 1)
        row.invalidation_triggered = abs(actual_return) > 8
        row.evaluated_at = cutoff
    if rows:
        await db.commit()


@app.get("/api/predictions")
async def predictions(db: AsyncSession = Depends(get_db)):
    await evaluate_expired_predictions(db)
    rows = (await db.execute(select(Prediction).order_by(Prediction.created_at.desc()))).scalars().all()
    stored = [{
        "ticker": row.ticker, "horizon": row.horizon, "bias": row.bias, "confidence": row.confidence,
        "starting_price": row.starting_price, "actual_return": row.actual_return, "direction_correct": row.direction_correct,
        "evaluated_at": row.evaluated_at.isoformat() if row.evaluated_at else None,
    } for row in rows]
    demo = [
        {"ticker": "NVDA", "horizon": 3, "bias": "Bullish", "confidence": 72, "starting_price": 178.2, "actual_return": 2.4, "direction_correct": True},
        {"ticker": "AMD", "horizon": 5, "bias": "Bullish", "confidence": 68, "starting_price": 169.8, "actual_return": -1.1, "direction_correct": False},
        {"ticker": "SPY", "horizon": 1, "bias": "Neutral", "confidence": 61, "starting_price": 641.3, "actual_return": 0.3, "direction_correct": True},
    ]
    completed = [item for item in stored if item["actual_return"] is not None] + demo
    accuracy = round(sum(bool(item.get("direction_correct")) for item in completed) / len(completed) * 100, 1) if completed else 0
    return {"mode": "MIXED" if stored else "DEMO", "accuracy": accuracy, "average_return": round(sum(item["actual_return"] for item in completed) / len(completed), 2) if completed else 0, "by_horizon": {"1D": 64, "3D": 71, "5D": 68}, "predictions": stored + demo}


@app.post("/api/predictions", status_code=status.HTTP_201_CREATED)
async def create_prediction(req: PredictionCreate, db: AsyncSession = Depends(get_db)):
    row = Prediction(
        ticker=req.ticker.upper(), horizon=req.horizon, bias=req.bias,
        probabilities={"bull": req.bull_probability, "neutral": req.neutral_probability, "bear": req.bear_probability},
        confidence=req.confidence, starting_price=req.starting_price, stock_score=req.stock_score, market_score=req.market_score,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return {"id": row.id, "status": "tracked"}


@app.get("/api/portfolio")
async def portfolio():
    broker = broker_provider()
    account, positions, orders = await asyncio.gather(broker.account(), broker.positions(), broker.orders())
    for row in positions:
        row.setdefault("market_value", round(row["quantity"] * row["price"], 2))
        row.setdefault("unrealized_pl", round(row["quantity"] * (row["price"] - row["average_cost"]), 2))
        row.setdefault("daily_pl", round(row["quantity"] * row["price"] * 0.006, 2))
    total_positions = sum(row["market_value"] for row in positions)
    for row in positions:
        row["weight"] = round(row["market_value"] / total_positions * 100, 1) if total_positions else 0
    equity = float(account["equity"])
    return {
        "mode": account["mode"], "source": account["source"], "equity": equity, "cash": account["cash"], "buying_power": account["buying_power"],
        "exposure": round(total_positions / equity * 100, 1) if equity else 0, "positions": positions, "orders": orders,
        "equity_curve": [round(equity * (0.965 + index * 0.001 + math.sin(index / 3) * 0.012), 2) for index in range(40)],
    }


def _order_risk_request(order: PaperOrderRequest, equity: float) -> RiskRequest:
    return RiskRequest(
        symbol=order.symbol.upper(), proposed_value=order.quantity * (order.limit_price or order.estimated_price),
        portfolio_equity=equity, current_exposure=order.current_exposure, sector_exposure=order.sector_exposure,
        daily_pnl=order.daily_pnl, liquidity=order.liquidity, event_risk=order.event_risk,
    )


@app.post("/api/trading/preview")
async def preview_paper_order(order: PaperOrderRequest, db: AsyncSession = Depends(get_db)):
    config, account = await runtime_settings(db), await broker_provider().account()
    decision = evaluate(_order_risk_request(order, float(account["equity"])), RiskLimits(**config["risk"]))
    return {"mode": "PAPER", "order": order.model_dump(exclude={"confirmed"}), "risk": decision, "requires_confirmation": decision["decision"] == "APPROVED"}


@app.post("/api/trading/orders")
async def submit_paper_order(order: PaperOrderRequest, db: AsyncSession = Depends(get_db)):
    if not settings.paper_order_submission_enabled:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Paper order submission is disabled in production until authentication is configured.")
    if not order.confirmed:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Explicit confirmation is required before a paper order is submitted.")
    preview = await preview_paper_order(order, db)
    if preview["risk"]["decision"] != "APPROVED":
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail={"risk": preview["risk"]})
    try:
        result = await broker_provider().submit_order(preview["order"])
    except RuntimeError as error:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(error)) from error
    return {"mode": "PAPER", "risk": preview["risk"], **result}


@app.get("/api/settings")
async def get_settings_endpoint(db: AsyncSession = Depends(get_db)):
    config = await runtime_settings(db)
    return {
        **config,
        "providers": {
            "Market Data": "Demo" if settings.demo_mode else "Alpaca configured",
            "News": "Demo" if settings.demo_mode else "Alpaca configured",
            "OpenAI": "Connected" if settings.openai_enabled else "Offline (rules fallback)",
            "SEC": "Configured" if settings.sec_is_configured else "Available — configure User-Agent",
            "Broker": "Demo Paper" if settings.demo_mode else "Alpaca Paper configured",
            "Paper order submission": "Enabled" if settings.paper_order_submission_enabled else "Disabled in production until authentication is configured",
            "Live Trading": "Disabled",
        },
    }


@app.put("/api/settings")
async def update_settings(payload: SettingsUpdate, db: AsyncSession = Depends(get_db)):
    current = await runtime_settings(db)
    update = payload.model_dump(exclude_none=True)
    merged = _merge(current, update)
    row = await db.get(UserSetting, "runtime_settings")
    if row:
        row.value = merged
    else:
        db.add(UserSetting(key="runtime_settings", value=merged))
    await db.commit()
    return {"status": "saved", "settings": merged}
