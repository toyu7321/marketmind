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
from .observability import cache_event, measure
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
    return {"APCA-API-KEY-ID": settings.alpaca_market_data_api_key, "APCA-API-SECRET-KEY": settings.alpaca_market_data_secret_key}


def _number(value: Any, default: float | None = None) -> float | None:
    try:
        number = float(value)
        return number if math.isfinite(number) else default
    except (TypeError, ValueError):
        return default


def _timestamp(value: Any) -> str | None:
    """Normalize provider timestamps without accepting a local clock as market data."""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return str(value)
    return parsed.astimezone(timezone.utc).isoformat()


def _is_stale(timestamp: str | None, seconds: int) -> bool:
    if not timestamp:
        return True
    try:
        return datetime.now(timezone.utc) - datetime.fromisoformat(timestamp.replace("Z", "+00:00")) > timedelta(seconds=seconds)
    except ValueError:
        return True


def _quality_for_feed(feed: str, updated_at: str | None) -> str:
    settings = get_settings()
    if _is_stale(updated_at, settings.market_data_stale_seconds):
        return "STALE"
    return _feed_label(feed)


def _feed_label(feed: str) -> str:
    if feed == "delayed_sip":
        return "DELAYED"
    if feed == "iex":
        return "IEX"
    return "LIVE"


class _PublicTTLCache:
    """Small in-process cache for public market data only; private account data never enters it."""

    def __init__(self) -> None:
        self._entries: dict[str, tuple[float, float, Any]] = {}
        self._inflight: dict[str, asyncio.Task[Any]] = {}
        self._stats: dict[str, int] = {}
        self._lock = asyncio.Lock()

    def _group(self, key: str) -> str:
        if key.startswith("scanner-bars"):
            return "scanner_bars"
        return key.split(":", 1)[0]

    def _record(self, key: str, outcome: str) -> None:
        stat_key = f"{self._group(key)}:{outcome}"
        self._stats[stat_key] = self._stats.get(stat_key, 0) + 1
        cache_event(self._group(key), outcome)

    async def _load_and_store(self, key: str, ttl_seconds: int, stale_seconds: int, loader: Any) -> Any:
        try:
            value = await loader()
            expires_at = time.monotonic() + ttl_seconds
            async with self._lock:
                self._entries[key] = (expires_at, expires_at + stale_seconds, value)
            return value
        finally:
            async with self._lock:
                if self._inflight.get(key) is asyncio.current_task():
                    self._inflight.pop(key, None)

    async def get_or_load(self, key: str, ttl_seconds: int, loader: Any, *, stale_seconds: int = 0) -> Any:
        """Deduplicate misses and optionally refresh historical public data in background.

        Quotes remain strict-TTL.  Only explicitly opted-in historical resources
        can return a stale value while a single refresh is underway.
        """
        now = time.monotonic()
        async with self._lock:
            cached = self._entries.get(key)
            if cached and cached[0] > now:
                self._record(key, "hit")
                return cached[2]
            if cached and stale_seconds and cached[1] > now:
                self._record(key, "stale_refresh")
                if key not in self._inflight:
                    task = asyncio.create_task(self._load_and_store(key, ttl_seconds, stale_seconds, loader))
                    self._inflight[key] = task
                    # The response is intentionally ignored: a stale historical
                    # value is already returned and the next request sees fresh data.
                    task.add_done_callback(lambda completed: completed.exception() if not completed.cancelled() else None)
                return cached[2]
            task = self._inflight.get(key)
            if task is None:
                self._record(key, "miss")
                task = asyncio.create_task(self._load_and_store(key, ttl_seconds, stale_seconds, loader))
                self._inflight[key] = task
            else:
                self._record(key, "coalesced")
        return await task

    async def get(self, key: str) -> Any | None:
        async with self._lock:
            cached = self._entries.get(key)
            if cached and cached[0] > time.monotonic():
                self._record(key, "hit")
                return cached[2]
            return None

    async def put(self, key: str, ttl_seconds: int, value: Any) -> None:
        expires_at = time.monotonic() + ttl_seconds
        async with self._lock:
            self._entries[key] = (expires_at, expires_at, value)

    def report(self) -> dict[str, Any]:
        return {"entries": len(self._entries), "inflight": len(self._inflight), "outcomes": dict(sorted(self._stats.items()))}

    async def clear(self) -> None:
        async with self._lock:
            self._entries.clear()
            self._inflight.clear()
            self._stats.clear()


_public_market_cache = _PublicTTLCache()
_alpaca_client: httpx.AsyncClient | None = None
_alpaca_client_lock = asyncio.Lock()


def _alpaca_timeout() -> httpx.Timeout:
    timeout = get_settings().market_data_request_timeout_seconds
    return httpx.Timeout(timeout, connect=min(float(timeout), 4.0))


async def start_provider_clients() -> None:
    """Start one keep-alive client per backend process for Alpaca REST requests."""
    global _alpaca_client
    async with _alpaca_client_lock:
        if _alpaca_client is None or _alpaca_client.is_closed:
            _alpaca_client = httpx.AsyncClient(
                timeout=_alpaca_timeout(),
                limits=httpx.Limits(max_connections=20, max_keepalive_connections=12, keepalive_expiry=45),
            )


async def close_provider_clients() -> None:
    global _alpaca_client
    async with _alpaca_client_lock:
        client, _alpaca_client = _alpaca_client, None
    if client is not None and not client.is_closed:
        await client.aclose()


async def alpaca_http_client() -> httpx.AsyncClient:
    await start_provider_clients()
    if _alpaca_client is None:
        raise RuntimeError("Alpaca HTTP client could not be initialized")
    return _alpaca_client


def public_market_cache_report() -> dict[str, Any]:
    return _public_market_cache.report()
_provider_activity: dict[str, dict[str, str | None]] = {
    "market": {"last_successful_request": None, "last_failure": None},
    "news": {"last_successful_request": None, "last_failure": None},
    "options": {"last_successful_request": None, "last_failure": None},
}


def _mark_provider_success(name: str) -> None:
    _provider_activity[name]["last_successful_request"] = datetime.now(timezone.utc).isoformat()
    _provider_activity[name]["last_failure"] = None


def _mark_provider_failure(name: str) -> None:
    _provider_activity[name]["last_failure"] = datetime.now(timezone.utc).isoformat()


def _provider_connection(name: str, configured: bool) -> str:
    if not configured:
        return "Demo fallback"
    activity = _provider_activity[name]
    if activity["last_failure"]:
        return "Degraded"
    return "Healthy" if activity["last_successful_request"] else "Configured"


def _company_name(symbol: str) -> str:
    return COMPANIES.get(symbol, symbol)


def _unavailable_quote(symbol: str, feed: str, reason: str = "Provider data unavailable") -> dict[str, Any]:
    return {
        "symbol": symbol, "company": _company_name(symbol), "company_metadata_source": "MarketMind symbol catalog",
        "price": None, "change": None, "change_percent": None, "volume": None, "sparkline": [], "trend": "Unavailable",
        "score": None, "freshness": "UNAVAILABLE", "data_quality": "UNAVAILABLE", "source": "Alpaca",
        "provider": "Alpaca", "feed": feed, "updated_at": None, "reason": reason,
    }


class MarketDataProvider(ABC):
    @abstractmethod
    async def get_quote(self, symbol: str) -> dict[str, Any]: ...

    @abstractmethod
    async def get_bars(self, symbol: str, days: int = 180) -> list[dict[str, Any]]: ...

    async def get_quotes(self, symbols: list[str]) -> list[dict[str, Any]]:
        return await asyncio.gather(*(self.get_quote(symbol) for symbol in symbols))

    async def get_bars_for_range(self, symbol: str, range_name: str) -> list[dict[str, Any]]:
        days = {"1D": 1, "5D": 5, "1M": 31, "3M": 93, "6M": 186, "1Y": 366}.get(range_name.upper(), 93)
        return await self.get_bars(symbol, days)

    async def get_bars_bulk(self, symbols: list[str], days: int = 120) -> dict[str, list[dict[str, Any]]]:
        rows = await asyncio.gather(*(self.get_bars(symbol, days) for symbol in symbols))
        return {symbol.upper(): values for symbol, values in zip(symbols, rows)}

    async def provider_status(self) -> dict[str, Any]: ...

    @abstractmethod
    async def get_market_status(self) -> dict[str, Any]: ...


class DemoMarketProvider(MarketDataProvider):
    """Deterministic synthetic data, so a zero-key install is usable and repeatable."""

    def _rng(self, symbol: str) -> random.Random:
        # Demo fixtures need reproducibility, never cryptographic randomness.
        return random.Random(sum(map(ord, symbol.upper())))  # nosec B311

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
                "volume": rng.randint(15_000_000, 75_000_000), "freshness": "DEMO", "data_quality": "DEMO",
                "provider": "Demo Market Engine", "feed": "demo",
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
            "symbol": symbol, "company": _company_name(symbol), "company_metadata_source": "MarketMind demo catalog", "price": price,
            "change": round(change, 2), "change_percent": round(change / previous * 100, 2),
            "score": 55 + self._rng(symbol).randint(-18, 32), "trend": "Bullish" if change > 0 else "Bearish",
            "sparkline": [item["close"] for item in bars[-16:]], "volume": bars[-1]["volume"], "freshness": "DEMO",
            "data_quality": "DEMO", "source": "Demo Market Engine", "provider": "Demo Market Engine", "feed": "demo",
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }

    async def get_market_status(self) -> dict[str, Any]:
        return {
            "is_open": False, "session": "After Hours", "provider": "Demo Market Engine", "freshness": "DEMO",
            "data_quality": "DEMO", "feed": "demo", "updated_at": datetime.now(timezone.utc).isoformat(),
        }

    async def provider_status(self) -> dict[str, Any]:
        return {"market_provider": "Demo Market Engine", "market_feed": "demo", "market_data_status": "Demo fallback", "connection": "Demo fallback", "last_successful_request": None}


class AlpacaMarketProvider(MarketDataProvider):
    """REST-first Alpaca adapter. Configured provider failures are explicit, never synthetic."""

    async def _json(self, path: str, params: dict[str, Any], cache_key: str, ttl_seconds: int, *, stale_seconds: int = 0) -> dict[str, Any]:
        settings = get_settings()
        metric = "alpaca.quotes" if cache_key.startswith("quotes:") else "alpaca.bars" if "bars" in cache_key else "alpaca.market"

        async def load() -> dict[str, Any]:
            client = await alpaca_http_client()
            with measure(metric):
                response = await client.get(f"{settings.alpaca_data_url.rstrip('/')}{path}", headers=_headers(), params=params)
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict):
                raise ValueError("Alpaca response was not an object")
            return payload

        historical_stale_seconds = stale_seconds or (1_800 if "bars" in cache_key else 0)
        return await _public_market_cache.get_or_load(cache_key, ttl_seconds, load, stale_seconds=historical_stale_seconds)

    def _quote_from_snapshot(self, symbol: str, payload: dict[str, Any]) -> dict[str, Any]:
        settings = get_settings()
        latest = payload.get("latestTrade") or payload.get("latest_trade") or {}
        latest_quote = payload.get("latestQuote") or payload.get("latest_quote") or {}
        daily = payload.get("dailyBar") or payload.get("daily_bar") or {}
        previous = payload.get("prevDailyBar") or payload.get("previous_daily_bar") or {}
        price = _number(latest.get("p", latest.get("price"))) or _number(daily.get("c", daily.get("close")))
        previous_close = _number(previous.get("c", previous.get("close")))
        updated_at = _timestamp(latest.get("t", latest.get("timestamp")) or daily.get("t", daily.get("timestamp")))
        if price is None or price <= 0 or previous_close in {None, 0}:
            raise ValueError("Alpaca snapshot lacks a usable price and previous close")
        change = round(price - previous_close, 2)
        quality = _quality_for_feed(settings.alpaca_feed, updated_at)
        return {
            "symbol": symbol, "company": _company_name(symbol), "company_metadata_source": "MarketMind symbol catalog",
            "price": price, "change": change, "change_percent": round(change / previous_close * 100, 2),
            # Bid/ask are included only when the configured market-data feed
            # provides them. The canonical execution-risk builder fails closed
            # rather than substituting a browser estimate when either is absent.
            "bid": _number(latest_quote.get("bp", latest_quote.get("bid_price"))),
            "ask": _number(latest_quote.get("ap", latest_quote.get("ask_price"))),
            "volume": _number(daily.get("v", daily.get("volume"))), "sparkline": [], "trend": "Up" if change > 0 else "Down" if change < 0 else "Flat", "score": None,
            "freshness": quality, "data_quality": quality, "source": "Alpaca Market Data", "provider": "Alpaca", "feed": settings.alpaca_feed,
            "updated_at": updated_at, "reason": None,
        }

    def _bars_from_payload(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        settings = get_settings()
        output: list[dict[str, Any]] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            timestamp = _timestamp(row.get("t", row.get("timestamp")))
            close = _number(row.get("c", row.get("close")))
            high, low, opening = _number(row.get("h", row.get("high"))), _number(row.get("l", row.get("low"))), _number(row.get("o", row.get("open")))
            if close is None or high is None or low is None or opening is None:
                continue
            # Historical observations are intentionally old. Their feed label is
            # still useful, but age is not a stale-current-quote signal.
            quality = _feed_label(settings.alpaca_feed)
            output.append({
                "time": (timestamp or str(row.get("t", "")))[:10], "open": opening, "high": high, "low": low, "close": close,
                "volume": int(_number(row.get("v", row.get("volume")), 0) or 0), "freshness": quality, "data_quality": quality,
                "provider": "Alpaca", "feed": settings.alpaca_feed, "updated_at": timestamp,
            })
        return output

    async def get_quotes(self, symbols: list[str]) -> list[dict[str, Any]]:
        settings = get_settings()
        cleaned = list(dict.fromkeys(symbol.strip().upper() for symbol in symbols if symbol.strip()))
        if not cleaned:
            return []
        cached_quotes = {
            symbol: await _public_market_cache.get(f"quote:{settings.alpaca_feed}:{symbol}")
            for symbol in cleaned
        }
        missing = [symbol for symbol in cleaned if cached_quotes[symbol] is None]
        if not missing:
            return [cached_quotes[symbol] for symbol in cleaned]
        try:
            payload = await self._json("/v2/stocks/snapshots", {"symbols": ",".join(missing), "feed": settings.alpaca_feed}, f"quotes:{settings.alpaca_feed}:{','.join(missing)}", 15)
            snapshots = payload.get("snapshots", payload)
            if not isinstance(snapshots, dict):
                raise ValueError("Alpaca batch snapshots were malformed")
            result_by_symbol = {symbol: quote for symbol, quote in cached_quotes.items() if isinstance(quote, dict)}
            for symbol in missing:
                snapshot = snapshots.get(symbol)
                if not isinstance(snapshot, dict):
                    result_by_symbol[symbol] = _unavailable_quote(symbol, settings.alpaca_feed, "No snapshot returned for this symbol")
                    continue
                try:
                    quote = self._quote_from_snapshot(symbol, snapshot)
                    result_by_symbol[symbol] = quote
                    await _public_market_cache.put(f"quote:{settings.alpaca_feed}:{symbol}", 15, quote)
                except (TypeError, ValueError, KeyError):
                    result_by_symbol[symbol] = _unavailable_quote(symbol, settings.alpaca_feed, "Snapshot did not contain a usable price")
            _mark_provider_success("market")
            return [result_by_symbol[symbol] for symbol in cleaned]
        except (httpx.HTTPError, ValueError, TypeError, KeyError, AttributeError) as error:
            _mark_provider_failure("market")
            logger.warning("alpaca_quote_batch_failed", extra={"symbols": len(missing), "reason": type(error).__name__})
            return [cached_quotes[symbol] if isinstance(cached_quotes[symbol], dict) else _unavailable_quote(symbol, settings.alpaca_feed) for symbol in cleaned]

    async def get_quote(self, symbol: str) -> dict[str, Any]:
        quotes = await self.get_quotes([symbol])
        return quotes[0] if quotes else _unavailable_quote(symbol.upper(), get_settings().alpaca_feed)

    async def _bars(self, symbol: str, *, timeframe: str, start: datetime, limit: int, cache_ttl: int) -> list[dict[str, Any]]:
        settings, symbol = get_settings(), symbol.upper()
        try:
            payload = await self._json(
                f"/v2/stocks/{symbol}/bars",
                {"timeframe": timeframe, "start": start.isoformat(), "limit": limit, "feed": settings.alpaca_feed, "adjustment": "all"},
                f"bars:{symbol}:{settings.alpaca_feed}:{timeframe}:{start.date().isoformat()}:{limit}", cache_ttl,
            )
            rows = payload.get("bars", [])
            if not isinstance(rows, list):
                raise ValueError("Alpaca bars were malformed")
            output = self._bars_from_payload(rows)
            if not output:
                raise ValueError("Alpaca returned no usable bars")
            _mark_provider_success("market")
            return output
        except (httpx.HTTPError, ValueError, TypeError, KeyError, AttributeError) as error:
            _mark_provider_failure("market")
            logger.warning("alpaca_bars_failed", extra={"symbol": symbol, "reason": type(error).__name__})
            return []

    async def get_bars(self, symbol: str, days: int = 180) -> list[dict[str, Any]]:
        days = max(1, min(days, 1_000))
        return await self._bars(symbol, timeframe="1Day", start=datetime.now(timezone.utc) - timedelta(days=max(days * 2, 45)), limit=days, cache_ttl=900 if days < 31 else 3_600)

    async def get_bars_for_range(self, symbol: str, range_name: str) -> list[dict[str, Any]]:
        specs = {
            "1D": ("1Min", 2, 1_200, 300), "5D": ("15Min", 8, 1_000, 300), "1M": ("1Hour", 35, 1_000, 900),
            "3M": ("1Day", 100, 100, 3_600), "6M": ("1Day", 195, 195, 3_600), "1Y": ("1Day", 380, 380, 3_600),
        }
        timeframe, lookback, limit, ttl = specs.get(range_name.upper(), specs["3M"])
        return await self._bars(symbol, timeframe=timeframe, start=datetime.now(timezone.utc) - timedelta(days=lookback), limit=limit, cache_ttl=ttl)

    async def get_bars_bulk(self, symbols: list[str], days: int = 120) -> dict[str, list[dict[str, Any]]]:
        settings = get_settings()
        cleaned = list(dict.fromkeys(symbol.strip().upper() for symbol in symbols if symbol.strip()))
        if not cleaned:
            return {}
        days = max(30, min(days, 260))
        try:
            payload = await self._json(
                "/v2/stocks/bars",
                {"symbols": ",".join(cleaned), "timeframe": "1Day", "start": (datetime.now(timezone.utc) - timedelta(days=days * 2)).isoformat(), "limit": min(10_000, days * len(cleaned) + len(cleaned)), "feed": settings.alpaca_feed, "adjustment": "all"},
                f"scanner-bars:{settings.alpaca_feed}:{','.join(cleaned)}:{days}", 600,
            )
            grouped = payload.get("bars", {})
            if not isinstance(grouped, dict):
                raise ValueError("Alpaca grouped bars were malformed")
            output = {symbol: self._bars_from_payload(grouped.get(symbol, []))[-days:] for symbol in cleaned}
            _mark_provider_success("market")
            return output
        except (httpx.HTTPError, ValueError, TypeError, KeyError, AttributeError) as error:
            _mark_provider_failure("market")
            logger.warning("alpaca_scanner_bars_failed", extra={"symbols": len(cleaned), "reason": type(error).__name__})
            return {symbol: [] for symbol in cleaned}

    async def get_market_status(self) -> dict[str, Any]:
        settings = get_settings()
        try:
            async def load() -> dict[str, Any]:
                client = await alpaca_http_client()
                with measure("alpaca.clock"):
                    response = await client.get(f"{settings.alpaca_base_url.rstrip('/')}/v2/clock", headers=_headers())
                response.raise_for_status()
                payload = response.json()
                if not isinstance(payload, dict):
                    raise ValueError("Alpaca clock response was not an object")
                return payload

            clock = await _public_market_cache.get_or_load(f"clock:{settings.alpaca_feed}", 15, load)
            _mark_provider_success("market")
            return {"is_open": bool(clock.get("is_open")), "session": "Market Open" if clock.get("is_open") else "Market Closed", "provider": "Alpaca", "feed": settings.alpaca_feed, "freshness": _quality_for_feed(settings.alpaca_feed, datetime.now(timezone.utc).isoformat()), "data_quality": _quality_for_feed(settings.alpaca_feed, datetime.now(timezone.utc).isoformat()), "updated_at": datetime.now(timezone.utc).isoformat(), "next_open": clock.get("next_open"), "next_close": clock.get("next_close")}
        except (httpx.HTTPError, ValueError, TypeError) as error:
            logger.warning("alpaca_clock_failed", extra={"reason": type(error).__name__})
            return {"is_open": None, "session": "Market status unavailable", "provider": "Alpaca", "feed": settings.alpaca_feed, "freshness": "UNAVAILABLE", "data_quality": "UNAVAILABLE", "updated_at": None}

    async def provider_status(self) -> dict[str, Any]:
        return {"market_provider": "Alpaca", "market_feed": get_settings().alpaca_feed, "market_data_status": _provider_connection("market", True), "connection": _provider_connection("market", True), "last_successful_request": _provider_activity["market"]["last_successful_request"]}


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

    async def provider_status(self) -> dict[str, Any]: ...


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
            "category": "Market" if index < 3 else "Watchlist", "freshness": "DEMO", "provider": "Demo News Engine", "updated_at": None,
        } for index, (headline, affected, sentiment, importance) in enumerate(fixtures)]

    async def provider_status(self) -> dict[str, Any]:
        return {"news_provider": "Demo News Engine", "news_status": "Demo fallback", "last_successful_request": None}


class AlpacaNewsProvider(DemoNewsProvider):
    async def get_news(self, symbols: list[str]) -> tuple[str, list[dict[str, Any]]]:
        settings = get_settings()
        try:
            cleaned = list(dict.fromkeys(symbol.upper() for symbol in symbols if symbol))[:50]
            payload = await _public_market_cache.get_or_load(
                f"news:{','.join(cleaned)}",
                60,
                lambda: self._request_news(cleaned),
            )
            articles = payload.get("news", [])
            if not isinstance(articles, list):
                raise ValueError("Alpaca news response was malformed")
            seen, normalized = set(), []
            for article in articles:
                if not isinstance(article, dict):
                    continue
                headline = str(article.get("headline") or "Market update").strip()
                key = headline.lower()
                if key in seen:
                    continue
                seen.add(key)
                summary = str(article.get("summary") or "")
                lower = f"{headline} {summary}".lower()
                sentiment = "Positive" if any(word in lower for word in ("beats", "surge", "gain", "upgrade")) else "Negative" if any(word in lower for word in ("cut", "fall", "lawsuit", "miss")) else "Neutral"
                article_symbols = article.get("symbols") if isinstance(article.get("symbols"), list) else []
                normalized.append({
                    "id": article.get("id", len(normalized)), "time": str(article.get("created_at", ""))[11:16] or "—", "headline": headline,
                    "affected": ", ".join(str(value) for value in article_symbols) or "Market", "sentiment": sentiment,
                    "importance": "High" if len(article_symbols) >= 3 else "Medium", "why": summary[:280] or "Provider article; inspect the source before acting.",
                    "category": "Watchlist" if set(article_symbols) & set(symbols) else "Market", "url": article.get("url"), "freshness": "LIVE", "provider": "Alpaca News", "updated_at": _timestamp(article.get("created_at")),
                })
            _mark_provider_success("news")
            return "LIVE", normalized
        except (httpx.HTTPError, ValueError, TypeError, KeyError, AttributeError) as error:
            _mark_provider_failure("news")
            logger.warning("alpaca_news_failed", extra={"reason": type(error).__name__})
            return "UNAVAILABLE", []

    async def _request_news(self, symbols: list[str]) -> dict[str, Any]:
        settings = get_settings()
        client = await alpaca_http_client()
        with measure("alpaca.news"):
            response = await client.get(f"{settings.alpaca_data_url.rstrip('/')}/v1beta1/news", headers=_headers(), params={"symbols": ",".join(symbols), "limit": 20, "sort": "desc"})
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("Alpaca news response was not an object")
        return payload

    async def provider_status(self) -> dict[str, Any]:
        return {"news_provider": "Alpaca", "news_status": _provider_connection("news", True), "last_successful_request": _provider_activity["news"]["last_successful_request"]}


class OptionsDataProvider(ABC):
    @abstractmethod
    async def get_chain(self, symbol: str, spot: float | None) -> tuple[str, list[dict[str, Any]]]: ...

    async def provider_status(self) -> dict[str, Any]: ...


class DemoOptionsProvider(OptionsDataProvider):
    async def get_chain(self, symbol: str, spot: float | None) -> tuple[str, list[dict[str, Any]]]:
        spot = spot or BASE.get(symbol.upper(), 100)
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
                        "theta": round(-mid / expiry_days, 2), "vega": round(spot * 0.0008, 2), "freshness": "DEMO", "provider": "Demo Options Engine", "feed": "demo",
                    })
        return "DEMO", rows

    async def provider_status(self) -> dict[str, Any]:
        return {"options_provider": "Demo Options Engine", "options_status": "Demo fallback", "last_successful_request": None}


class AlpacaOptionsProvider(DemoOptionsProvider):
    async def get_chain(self, symbol: str, spot: float | None) -> tuple[str, list[dict[str, Any]]]:
        settings = get_settings()
        try:
            payload = await _public_market_cache.get_or_load(
                f"options:{symbol.upper()}:{settings.alpaca_options_feed}",
                30,
                lambda: self._request_chain(symbol.upper()),
            )
            snapshots = payload.get("snapshots", {})
            if not isinstance(snapshots, dict):
                raise ValueError("Alpaca options response was malformed")
            rows = []
            for contract, snapshot in snapshots.items():
                if not isinstance(snapshot, dict):
                    continue
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
                    "freshness": "LIVE", "data_quality": "LIVE", "provider": "Alpaca Options", "feed": settings.alpaca_options_feed, "contract": contract,
                })
            if not rows:
                raise ValueError("No option snapshots available")
            _mark_provider_success("options")
            return "LIVE", sorted(rows, key=lambda row: (row["expiration"], row["strike"], row["type"]))
        except (httpx.HTTPError, ValueError, TypeError, KeyError, AttributeError) as error:
            _mark_provider_failure("options")
            logger.warning("alpaca_options_unavailable", extra={"symbol": symbol, "reason": type(error).__name__})
            return "UNAVAILABLE", []

    async def _request_chain(self, symbol: str) -> dict[str, Any]:
        settings = get_settings()
        client = await alpaca_http_client()
        with measure("alpaca.options"):
            response = await client.get(f"{settings.alpaca_data_url.rstrip('/')}/v1beta1/options/snapshots/{symbol}", headers=_headers(), params={"feed": settings.alpaca_options_feed, "limit": 100})
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("Alpaca options response was not an object")
        return payload

    async def provider_status(self) -> dict[str, Any]:
        return {"options_provider": "Alpaca", "options_feed": get_settings().alpaca_options_feed, "options_status": _provider_connection("options", True), "last_successful_request": _provider_activity["options"]["last_successful_request"]}


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


def market_provider() -> MarketDataProvider:
    return DemoMarketProvider() if get_settings().demo_mode else AlpacaMarketProvider()
def news_provider() -> NewsProvider:
    return DemoNewsProvider() if get_settings().demo_mode else AlpacaNewsProvider()
def options_provider() -> OptionsDataProvider:
    return DemoOptionsProvider() if get_settings().demo_mode else AlpacaOptionsProvider()
