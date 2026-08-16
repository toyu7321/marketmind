"""Provider boundaries and conservative fallbacks for MarketMind services."""
from __future__ import annotations

import asyncio
import json
import logging
import math
import random
import re
import time
from abc import ABC, abstractmethod
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from .config import get_settings
from .schemas import AIAnalysis

logger = logging.getLogger(__name__)

COMPANIES = {
    "SPY": "SPDR S&P 500 ETF", "QQQ": "Invesco QQQ Trust", "DIA": "SPDR Dow Jones ETF",
    "IWM": "iShares Russell 2000", "VIX": "CBOE Volatility Index", "NVDA": "NVIDIA Corporation",
    "AMD": "Advanced Micro Devices", "AAPL": "Apple Inc.", "MSFT": "Microsoft Corporation",
    "META": "Meta Platforms", "AMZN": "Amazon.com", "GOOGL": "Alphabet Inc.",
    "TSLA": "Tesla Inc.", "JPM": "JPMorgan Chase", "XOM": "Exxon Mobil", "LLY": "Eli Lilly",
    "AVGO": "Broadcom", "NFLX": "Netflix", "COST": "Costco Wholesale",
}
BASE = {
    "SPY": 643.18, "QQQ": 572.44, "DIA": 452.11, "IWM": 229.63, "VIX": 14.72,
    "NVDA": 182.37, "AMD": 173.41, "AAPL": 231.59, "MSFT": 522.18, "META": 786.42,
    "AMZN": 223.67, "GOOGL": 201.93, "TSLA": 335.58, "JPM": 289.18, "XOM": 111.52,
    "LLY": 774.90, "AVGO": 301.42, "NFLX": 1210.31, "COST": 964.52,
}


def _headers() -> dict[str, str]:
    settings = get_settings()
    return {"APCA-API-KEY-ID": settings.alpaca_api_key, "APCA-API-SECRET-KEY": settings.alpaca_secret_key}


def _number(value: Any, default: float | None = None) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


class MarketDataProvider(ABC):
    @abstractmethod
    async def get_quote(self, symbol: str) -> dict[str, Any]: ...

    @abstractmethod
    async def get_bars(self, symbol: str, days: int = 180) -> list[dict[str, Any]]: ...

    @abstractmethod
    async def get_market_status(self) -> dict[str, Any]: ...


class DemoMarketProvider(MarketDataProvider):
    """Deterministic synthetic data, so a zero-key install is usable and repeatable."""

    def _rng(self, symbol: str) -> random.Random:
        return random.Random(sum(map(ord, symbol.upper())))

    async def get_bars(self, symbol: str, days: int = 180) -> list[dict[str, Any]]:
        symbol = symbol.upper()
        rng, base, values = self._rng(symbol), BASE.get(symbol, 100), []
        price, start = base * 0.78, datetime.now(timezone.utc) - timedelta(days=days)
        for index in range(days):
            drift = 0.0012 if symbol not in {"TSLA", "VIX"} else -0.0001
            price *= 1 + drift + rng.gauss(0, 0.013)
            values.append({
                "time": (start + timedelta(days=index)).date().isoformat(),
                "open": round(price * (1 + rng.gauss(0, 0.003)), 2), "high": round(price * 1.012, 2),
                "low": round(price * 0.989, 2), "close": round(price, 2),
                "volume": rng.randint(15_000_000, 75_000_000), "freshness": "DEMO",
            })
        scale = base / values[-1]["close"]
        for row in values:
            for key in ("open", "high", "low", "close"):
                row[key] = round(row[key] * scale, 2)
        return values

    async def get_quote(self, symbol: str) -> dict[str, Any]:
        symbol = symbol.upper()
        bars = await self.get_bars(symbol, 32)
        price, previous = bars[-1]["close"], bars[-2]["close"]
        change = price - previous
        return {
            "symbol": symbol, "company": COMPANIES.get(symbol, f"{symbol} Corporation"), "price": price,
            "change": round(change, 2), "change_percent": round(change / previous * 100, 2),
            "score": 55 + self._rng(symbol).randint(-18, 32), "trend": "Bullish" if change > 0 else "Bearish",
            "sparkline": [item["close"] for item in bars[-16:]], "freshness": "DEMO", "source": "Demo Market Engine",
        }

    async def get_market_status(self) -> dict[str, Any]:
        return {
            "is_open": False, "session": "After Hours", "provider": "Demo Market Engine", "freshness": "DEMO",
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }


class AlpacaMarketProvider(DemoMarketProvider):
    """Alpaca market data adapter; every request has a safe demo fallback."""

    async def get_quote(self, symbol: str) -> dict[str, Any]:
        settings, symbol = get_settings(), symbol.upper()
        try:
            async with httpx.AsyncClient(timeout=6) as client:
                response = await client.get(
                    f"{settings.alpaca_data_url.rstrip('/')}/v2/stocks/{symbol}/snapshot",
                    headers=_headers(), params={"feed": settings.alpaca_feed},
                )
                response.raise_for_status()
                payload = response.json()
            latest = payload.get("latestTrade") or payload.get("dailyBar") or {}
            previous = payload.get("prevDailyBar") or {}
            price, previous_close = _number(latest.get("p", latest.get("c"))), _number(previous.get("c"))
            if not price or not previous_close:
                raise ValueError("Alpaca snapshot lacks a usable last and previous close")
            demo = await super().get_quote(symbol)
            return {
                **demo, "price": price, "change": round(price - previous_close, 2),
                "change_percent": round((price / previous_close - 1) * 100, 2), "freshness": "LIVE",
                "source": f"Alpaca ({settings.alpaca_feed})",
            }
        except (httpx.HTTPError, ValueError, KeyError) as error:
            logger.warning("market_quote_fallback", extra={"symbol": symbol, "reason": type(error).__name__})
            return await super().get_quote(symbol)

    async def get_bars(self, symbol: str, days: int = 180) -> list[dict[str, Any]]:
        settings, symbol = get_settings(), symbol.upper()
        start = (datetime.now(timezone.utc) - timedelta(days=max(days * 2, 45))).isoformat()
        try:
            async with httpx.AsyncClient(timeout=8) as client:
                response = await client.get(
                    f"{settings.alpaca_data_url.rstrip('/')}/v2/stocks/{symbol}/bars", headers=_headers(),
                    params={"timeframe": "1Day", "start": start, "limit": days, "feed": settings.alpaca_feed, "adjustment": "all"},
                )
                response.raise_for_status()
                rows = response.json().get("bars", [])
            normalized = [{
                "time": str(row["t"])[:10], "open": _number(row.get("o"), 0), "high": _number(row.get("h"), 0),
                "low": _number(row.get("l"), 0), "close": _number(row.get("c"), 0), "volume": int(row.get("v", 0)),
                "freshness": "LIVE",
            } for row in rows]
            if len(normalized) < 30:
                raise ValueError("Alpaca returned insufficient daily bars")
            return normalized[-days:]
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as error:
            logger.warning("market_bars_fallback", extra={"symbol": symbol, "reason": type(error).__name__})
            return await super().get_bars(symbol, days)

    async def get_market_status(self) -> dict[str, Any]:
        settings = get_settings()
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                response = await client.get(f"{settings.alpaca_base_url.rstrip('/')}/v2/clock", headers=_headers())
                response.raise_for_status()
                clock = response.json()
            return {
                "is_open": bool(clock.get("is_open")), "session": "Market Open" if clock.get("is_open") else "Market Closed",
                "provider": "Alpaca", "freshness": "LIVE", "updated_at": datetime.now(timezone.utc).isoformat(),
                "next_open": clock.get("next_open"), "next_close": clock.get("next_close"),
            }
        except httpx.HTTPError as error:
            logger.warning("market_clock_fallback", extra={"reason": type(error).__name__})
            return await super().get_market_status()


class AIProvider(ABC):
    @abstractmethod
    async def analyze(self, evidence: dict[str, Any]) -> dict[str, Any]: ...


class RulesAIProvider(AIProvider):
    async def analyze(self, evidence: dict[str, Any]) -> dict[str, Any]:
        score = evidence.get("score", evidence.get("stock_score", 72))
        bullish = score >= 65
        return {
            "bias": "Moderately Bullish" if bullish else "Neutral", "confidence": min(82, 50 + abs(score - 50)),
            "summary": "Structured evidence favors selective risk-taking; sizing and invalidation remain essential.",
            "bull_case": "Trend and breadth confirmation extend the current move.",
            "base_case": "Constructive consolidation with leadership remaining selective.",
            "bear_case": "Volatility expansion and lost trend support weaken the setup.",
            "catalysts": ["Technology leadership", "Improving momentum", "Stable volatility"],
            "risks": ["Macro event risk", "Breadth divergence"],
            "invalidation": "Loss of the 50-day trend with deteriorating breadth",
            "preferred_action": "WATCH" if bullish else "WAIT",
            "reasoning_summary": "Deterministic fallback interpretation; no external AI call was used.",
            "source": "RULES",
        }


class OpenAIProvider(AIProvider):
    """Bounded structured Responses API adapter. It receives compressed evidence only."""

    async def analyze(self, evidence: dict[str, Any]) -> dict[str, Any]:
        settings, fallback = get_settings(), RulesAIProvider()
        if not settings.openai_enabled:
            return await fallback.analyze(evidence)
        schema = {
            "type": "object", "additionalProperties": False,
            "required": ["bias", "confidence", "summary", "bull_case", "base_case", "bear_case", "catalysts", "risks", "invalidation", "preferred_action", "reasoning_summary"],
            "properties": {
                "bias": {"type": "string"}, "confidence": {"type": "integer", "minimum": 0, "maximum": 100},
                "summary": {"type": "string"}, "bull_case": {"type": "string"}, "base_case": {"type": "string"},
                "bear_case": {"type": "string"}, "catalysts": {"type": "array", "items": {"type": "string"}},
                "risks": {"type": "array", "items": {"type": "string"}}, "invalidation": {"type": "string"},
                "preferred_action": {"type": "string", "enum": ["WATCH", "WAIT", "NO TRADE", "BULLISH SETUP", "BEARISH SETUP", "HIGH RISK"]},
                "reasoning_summary": {"type": "string"},
            },
        }
        payload = {
            "model": settings.openai_model,
            "input": [
                {"role": "system", "content": "Interpret only supplied quantitative evidence. Express uncertainty; never promise outcomes."},
                {"role": "user", "content": json.dumps(evidence, separators=(",", ":"))},
            ],
            "text": {"format": {"type": "json_schema", "name": "market_analysis", "strict": True, "schema": schema}},
            "max_output_tokens": 700,
        }
        for delay in (0, 0.5, 1.5):
            try:
                if delay:
                    await asyncio.sleep(delay)
                async with httpx.AsyncClient(timeout=15) as client:
                    response = await client.post(
                        "https://api.openai.com/v1/responses", headers={"Authorization": f"Bearer {settings.openai_api_key}"}, json=payload,
                    )
                    response.raise_for_status()
                    body = response.json()
                text = next(
                    content.get("text") for output in body.get("output", []) for content in output.get("content", [])
                    if content.get("type") == "output_text" and content.get("text")
                )
                result = AIAnalysis.model_validate_json(text).model_dump()
                return {**result, "source": "OPENAI"}
            except (StopIteration, httpx.HTTPError, ValueError, TypeError) as error:
                logger.warning("ai_fallback", extra={"reason": type(error).__name__})
        return await fallback.analyze(evidence)


_ai_cache: dict[str, tuple[float, dict[str, Any]]] = {}


async def analyze_evidence(evidence: dict[str, Any]) -> dict[str, Any]:
    key, now = json.dumps(evidence, sort_keys=True, default=str, separators=(",", ":")), time.monotonic()
    cached = _ai_cache.get(key)
    if cached and now - cached[0] < 300:
        return {**cached[1], "cached": True}
    provider: AIProvider = OpenAIProvider() if get_settings().openai_enabled else RulesAIProvider()
    result = await provider.analyze(evidence)
    _ai_cache[key] = (now, result)
    return {**result, "cached": False}


class SECProvider(ABC):
    @abstractmethod
    async def filings_for_symbol(self, symbol: str) -> list[dict[str, Any]]: ...


class EdgarSECProvider(SECProvider):
    _ticker_map: dict[str, int] | None = None

    async def _cik_for_symbol(self, symbol: str) -> int | None:
        if self._ticker_map is None:
            async with httpx.AsyncClient(timeout=10, headers={"User-Agent": get_settings().sec_user_agent}) as client:
                response = await client.get("https://www.sec.gov/files/company_tickers.json")
                response.raise_for_status()
                self._ticker_map = {row["ticker"].upper(): int(row["cik_str"]) for row in response.json().values()}
        return self._ticker_map.get(symbol.upper())

    async def filings_for_symbol(self, symbol: str) -> list[dict[str, Any]]:
        settings = get_settings()
        if not settings.sec_is_configured:
            return []
        try:
            cik = await self._cik_for_symbol(symbol)
            if not cik:
                return []
            async with httpx.AsyncClient(timeout=10, headers={"User-Agent": settings.sec_user_agent}) as client:
                response = await client.get(f"https://data.sec.gov/submissions/CIK{cik:010d}.json")
                response.raise_for_status()
                recent = response.json().get("filings", {}).get("recent", {})
            output = []
            for form, filed, accession, primary in zip(
                recent.get("form", []), recent.get("filingDate", []), recent.get("accessionNumber", []), recent.get("primaryDocument", []),
            ):
                if form not in {"10-K", "10-Q", "8-K", "4"}:
                    continue
                accession_id = accession.replace("-", "")
                output.append({
                    "form": "Form 4" if form == "4" else form, "filed": filed,
                    "title": {"10-K": "Annual report", "10-Q": "Quarterly report", "8-K": "Current report", "4": "Insider transaction"}[form],
                    "material": form in {"10-K", "10-Q", "8-K"},
                    "url": f"https://www.sec.gov/Archives/edgar/data/{cik}/{accession_id}/{primary}", "freshness": "LIVE",
                })
                if len(output) == 6:
                    break
            return output
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as error:
            logger.warning("sec_fallback", extra={"symbol": symbol, "reason": type(error).__name__})
            return []


class NewsProvider(ABC):
    @abstractmethod
    async def get_news(self, symbols: list[str]) -> tuple[str, list[dict[str, Any]]]: ...


class DemoNewsProvider(NewsProvider):
    async def get_news(self, symbols: list[str]) -> tuple[str, list[dict[str, Any]]]:
        fixtures = [
            ("Technology leadership lifts major indices", "NVDA, MSFT", "Positive", "High"),
            ("Fed officials emphasize data-dependent path", "SPY", "Neutral", "High"),
            ("Energy shares lag as crude consolidates", "XLE, XOM", "Negative", "Medium"),
            ("Semiconductor demand outlook remains constructive", "AMD, NVDA, AVGO", "Positive", "High"),
            ("Small-caps test key technical resistance", "IWM", "Neutral", "Medium"),
        ]
        return "DEMO", [{
            "id": index, "time": f"{14-index}:3{index}", "headline": headline, "affected": affected,
            "sentiment": sentiment, "importance": importance,
            "why": "Demo item: this type of event can change near-term positioning, liquidity, and relative strength.",
            "category": "Market" if index < 3 else "Watchlist", "freshness": "DEMO",
        } for index, (headline, affected, sentiment, importance) in enumerate(fixtures)]


class AlpacaNewsProvider(DemoNewsProvider):
    async def get_news(self, symbols: list[str]) -> tuple[str, list[dict[str, Any]]]:
        settings = get_settings()
        try:
            async with httpx.AsyncClient(timeout=8) as client:
                response = await client.get(
                    f"{settings.alpaca_data_url.rstrip('/')}/v1beta1/news", headers=_headers(),
                    params={"symbols": ",".join(symbols[:10]), "limit": 20, "sort": "desc"},
                )
                response.raise_for_status()
                articles = response.json().get("news", [])
            seen, normalized = set(), []
            for article in articles:
                headline = article.get("headline", "Market update").strip()
                key = headline.lower()
                if key in seen:
                    continue
                seen.add(key)
                summary = article.get("summary", "")
                lower = f"{headline} {summary}".lower()
                sentiment = "Positive" if any(word in lower for word in ("beats", "surge", "gain", "upgrade")) else "Negative" if any(word in lower for word in ("cut", "fall", "lawsuit", "miss")) else "Neutral"
                normalized.append({
                    "id": article.get("id", len(normalized)), "time": str(article.get("created_at", ""))[11:16] or "—", "headline": headline,
                    "affected": ", ".join(article.get("symbols", [])) or "Market", "sentiment": sentiment,
                    "importance": "High" if len(article.get("symbols", [])) >= 3 else "Medium", "why": summary[:280] or "Provider article; inspect the source before acting.",
                    "category": "Watchlist" if set(article.get("symbols", [])) & set(symbols) else "Market", "url": article.get("url"), "freshness": "LIVE",
                })
            return "LIVE", normalized
        except httpx.HTTPError as error:
            logger.warning("news_fallback", extra={"reason": type(error).__name__})
            return await super().get_news(symbols)


class OptionsDataProvider(ABC):
    @abstractmethod
    async def get_chain(self, symbol: str, spot: float) -> tuple[str, list[dict[str, Any]]]: ...


class DemoOptionsProvider(OptionsDataProvider):
    async def get_chain(self, symbol: str, spot: float) -> tuple[str, list[dict[str, Any]]]:
        rows = []
        for expiry_days in (14, 30, 60):
            for offset in (-0.10, -0.05, 0, 0.05, 0.10):
                strike = round(spot * (1 + offset), 0)
                for kind in ("CALL", "PUT"):
                    intrinsic = max(0, spot - strike) if kind == "CALL" else max(0, strike - spot)
                    mid = intrinsic + spot * 0.025 * math.sqrt(expiry_days / 30)
                    rows.append({
                        "expiration": f"{expiry_days} DTE", "strike": strike, "type": kind, "bid": round(mid * 0.96, 2),
                        "ask": round(mid * 1.04, 2), "last": round(mid, 2), "volume": int(840 - abs(offset) * 3000),
                        "open_interest": int(4100 - abs(offset) * 9000), "iv": round(34 + abs(offset) * 80, 1),
                        "delta": round((0.5 - offset * 3) * (1 if kind == "CALL" else -1), 2), "gamma": 0.018,
                        "theta": round(-mid / expiry_days, 2), "vega": round(spot * 0.0008, 2), "freshness": "DEMO",
                    })
        return "DEMO", rows


class AlpacaOptionsProvider(DemoOptionsProvider):
    async def get_chain(self, symbol: str, spot: float) -> tuple[str, list[dict[str, Any]]]:
        settings = get_settings()
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.get(
                    f"{settings.alpaca_data_url.rstrip('/')}/v1beta1/options/snapshots/{symbol.upper()}", headers=_headers(),
                    params={"feed": "indicative", "limit": 100},
                )
                response.raise_for_status()
                snapshots = response.json().get("snapshots", {})
            rows = []
            for contract, snapshot in snapshots.items():
                match = re.match(r"^[A-Z]+(\d{6})([CP])(\d{8})$", contract)
                if not match:
                    continue
                date, kind, raw_strike = match.groups()
                quote, trade, greeks = snapshot.get("latestQuote") or {}, snapshot.get("latestTrade") or {}, snapshot.get("greeks") or {}
                rows.append({
                    "expiration": f"20{date[:2]}-{date[2:4]}-{date[4:]}", "strike": int(raw_strike) / 1000,
                    "type": "CALL" if kind == "C" else "PUT", "bid": _number(quote.get("bp", quote.get("bid_price"))),
                    "ask": _number(quote.get("ap", quote.get("ask_price"))), "last": _number(trade.get("p", trade.get("price"))),
                    "volume": _number((snapshot.get("dailyBar") or {}).get("v")), "open_interest": _number(snapshot.get("openInterest")),
                    "iv": _number(snapshot.get("impliedVolatility", greeks.get("impliedVolatility"))), "delta": _number(greeks.get("delta")),
                    "gamma": _number(greeks.get("gamma")), "theta": _number(greeks.get("theta")), "vega": _number(greeks.get("vega")),
                    "freshness": "LIVE", "contract": contract,
                })
            if not rows:
                raise ValueError("No option snapshots available")
            return "LIVE", sorted(rows, key=lambda row: (row["expiration"], row["strike"], row["type"]))
        except (httpx.HTTPError, ValueError) as error:
            logger.warning("options_fallback", extra={"symbol": symbol, "reason": type(error).__name__})
            return await super().get_chain(symbol, spot)


def _premium(row: dict[str, Any], side: str = "buy") -> float:
    return float(row.get("ask") if side == "buy" else row.get("bid") or row.get("last") or 0)


def strategy_candidates(spot: float, chain: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Deterministic payoff summaries. These are educational candidates, never recommendations."""
    calls = sorted((row for row in chain if row["type"] == "CALL" and row.get("ask") is not None), key=lambda row: abs(row["strike"] - spot))
    puts = sorted((row for row in chain if row["type"] == "PUT" and row.get("ask") is not None), key=lambda row: abs(row["strike"] - spot))
    if not calls or not puts:
        return []
    call, put = calls[0], puts[0]
    higher_call = next((row for row in sorted(calls, key=lambda row: row["strike"]) if row["strike"] > call["strike"]), call)
    lower_put = next((row for row in sorted(puts, key=lambda row: row["strike"], reverse=True) if row["strike"] < put["strike"]), put)
    call_debit, put_debit = _premium(call), _premium(put)
    spread_width, bull_debit = max(higher_call["strike"] - call["strike"], 0), max(call_debit - _premium(higher_call, "sell"), 0)
    bear_debit = max(put_debit - _premium(lower_put, "sell"), 0)
    return [
        {"name": "Long Call", "debit": call_debit, "max_profit": None, "max_loss": call_debit, "breakeven": call["strike"] + call_debit, "risk": "Premium can be lost in full."},
        {"name": "Long Put", "debit": put_debit, "max_profit": max(put["strike"] - put_debit, 0), "max_loss": put_debit, "breakeven": put["strike"] - put_debit, "risk": "Premium can be lost in full."},
        {"name": "Covered Call", "credit": _premium(call, "sell"), "max_profit": max(call["strike"] - spot, 0) + _premium(call, "sell"), "max_loss": spot - _premium(call, "sell"), "breakeven": spot - _premium(call, "sell"), "risk": "Requires shares; downside remains substantial."},
        {"name": "Cash-Secured Put", "credit": _premium(put, "sell"), "max_profit": _premium(put, "sell"), "max_loss": max(put["strike"] - _premium(put, "sell"), 0), "breakeven": put["strike"] - _premium(put, "sell"), "risk": "Assignment creates material downside exposure."},
        {"name": "Bull Call Spread", "debit": bull_debit, "max_profit": max(spread_width - bull_debit, 0), "max_loss": bull_debit, "breakeven": call["strike"] + bull_debit, "risk": "Defined loss, capped upside."},
        {"name": "Bear Put Spread", "debit": bear_debit, "max_profit": max(put["strike"] - lower_put["strike"] - bear_debit, 0), "max_loss": bear_debit, "breakeven": put["strike"] - bear_debit, "risk": "Defined loss, capped downside participation."},
    ]


class BrokerProvider(ABC):
    @abstractmethod
    async def account(self) -> dict[str, Any]: ...
    @abstractmethod
    async def positions(self) -> list[dict[str, Any]]: ...
    @abstractmethod
    async def orders(self) -> list[dict[str, Any]]: ...
    @abstractmethod
    async def submit_order(self, order: dict[str, Any]) -> dict[str, Any]: ...


class DemoPaperBroker(BrokerProvider):
    async def account(self) -> dict[str, Any]:
        return {"mode": "PAPER", "source": "DEMO", "equity": 100000, "cash": 42500, "buying_power": 85000}
    async def positions(self) -> list[dict[str, Any]]:
        return [
            {"symbol": "NVDA", "quantity": 35, "average_cost": 151.2, "price": 182.37, "sector": "Semiconductors"},
            {"symbol": "MSFT", "quantity": 18, "average_cost": 474.1, "price": 522.18, "sector": "Technology"},
            {"symbol": "SPY", "quantity": 22, "average_cost": 608.4, "price": 643.18, "sector": "Broad Market"},
        ]
    async def orders(self) -> list[dict[str, Any]]:
        return [{"symbol": "AMD", "side": "BUY", "quantity": 10, "status": "Filled", "price": 171.82}, {"symbol": "NVDA", "side": "SELL", "quantity": 5, "status": "Filled", "price": 180.44}]
    async def submit_order(self, order: dict[str, Any]) -> dict[str, Any]:
        return {"status": "paper accepted", "source": "DEMO", "order": order}


class AlpacaPaperBroker(DemoPaperBroker):
    async def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        settings = get_settings()
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.request(method, f"{settings.alpaca_base_url.rstrip('/')}{path}", headers=_headers(), **kwargs)
            response.raise_for_status()
            return response.json()
    async def account(self) -> dict[str, Any]:
        try:
            account = await self._request("GET", "/v2/account")
            return {"mode": "PAPER", "source": "ALPACA PAPER", "equity": _number(account.get("equity"), 0), "cash": _number(account.get("cash"), 0), "buying_power": _number(account.get("buying_power"), 0)}
        except httpx.HTTPError as error:
            logger.warning("broker_account_fallback", extra={"reason": type(error).__name__})
            return await super().account()
    async def positions(self) -> list[dict[str, Any]]:
        try:
            rows = await self._request("GET", "/v2/positions")
            return [{"symbol": row.get("symbol"), "quantity": _number(row.get("qty"), 0), "average_cost": _number(row.get("avg_entry_price"), 0), "price": _number(row.get("current_price"), 0), "sector": "Unclassified", "market_value": _number(row.get("market_value"), 0), "unrealized_pl": _number(row.get("unrealized_pl"), 0), "daily_pl": _number(row.get("unrealized_intraday_pl"), 0)} for row in rows]
        except httpx.HTTPError as error:
            logger.warning("broker_positions_fallback", extra={"reason": type(error).__name__})
            return await super().positions()
    async def orders(self) -> list[dict[str, Any]]:
        try:
            rows = await self._request("GET", "/v2/orders", params={"status": "all", "limit": 20, "direction": "desc"})
            return [{"symbol": row.get("symbol"), "side": str(row.get("side", "")).upper(), "quantity": _number(row.get("qty"), 0), "status": row.get("status"), "price": _number(row.get("filled_avg_price") or row.get("limit_price"), 0)} for row in rows]
        except httpx.HTTPError as error:
            logger.warning("broker_orders_fallback", extra={"reason": type(error).__name__})
            return await super().orders()
    async def submit_order(self, order: dict[str, Any]) -> dict[str, Any]:
        payload = {"symbol": order["symbol"], "qty": str(order["quantity"]), "side": order["side"].lower(), "type": order["order_type"], "time_in_force": order["time_in_force"].lower()}
        if order.get("limit_price") is not None:
            payload["limit_price"] = str(order["limit_price"])
        try:
            return {"source": "ALPACA PAPER", "order": await self._request("POST", "/v2/orders", json=payload)}
        except httpx.HTTPError as error:
            logger.warning("broker_order_error", extra={"symbol": order["symbol"], "reason": type(error).__name__})
            raise RuntimeError("Alpaca paper order could not be submitted") from error


class LiveBrokerGuard(BrokerProvider):
    """Live execution is intentionally unavailable in this local-first build."""
    async def account(self) -> dict[str, Any]: return {"mode": "LIVE", "status": "disabled"}
    async def positions(self) -> list[dict[str, Any]]: return []
    async def orders(self) -> list[dict[str, Any]]: return []
    async def submit_order(self, order: dict[str, Any]) -> dict[str, Any]:
        raise PermissionError("Live trading is disabled. MarketMind cannot autonomously submit real-money orders.")


def market_provider() -> MarketDataProvider:
    return DemoMarketProvider() if get_settings().demo_mode else AlpacaMarketProvider()
def news_provider() -> NewsProvider:
    return DemoNewsProvider() if get_settings().demo_mode else AlpacaNewsProvider()
def options_provider() -> OptionsDataProvider:
    return DemoOptionsProvider() if get_settings().demo_mode else AlpacaOptionsProvider()
def broker_provider() -> BrokerProvider:
    settings = get_settings()
    return AlpacaPaperBroker() if settings.enable_paper_trading and not settings.demo_mode else DemoPaperBroker()
