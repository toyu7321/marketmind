from __future__ import annotations

from datetime import datetime, timedelta, timezone
import asyncio

import httpx
import pytest

from app.config import get_settings
from app import main as main_module
from app.indicators import technical_snapshot
import app.providers as providers_module
from app.providers import AlpacaMarketProvider, _PublicTTLCache


def _bar(index: int) -> dict[str, object]:
    return {
        "t": (datetime.now(timezone.utc) - timedelta(days=64 - index)).isoformat(),
        "o": 100 + index, "h": 102 + index, "l": 99 + index, "c": 101 + index, "v": 1_000_000 + index,
    }


def test_alpaca_quote_parser_returns_provider_only_fields():
    quote = AlpacaMarketProvider()._quote_from_snapshot("SPY", {
        "latestTrade": {"p": 512.34, "t": datetime.now(timezone.utc).isoformat()},
        "dailyBar": {"c": 511.98, "v": 4_200_000},
        "prevDailyBar": {"c": 509.10},
    })
    assert quote["price"] == 512.34
    assert quote["change_percent"] == round((512.34 - 509.10) / 509.10 * 100, 2)
    assert quote["provider"] == "Alpaca"
    assert quote["feed"] == "iex"
    assert quote["freshness"] == "IEX"
    assert quote["score"] is None


def test_historical_bars_parse_and_feed_indicators():
    bars = AlpacaMarketProvider()._bars_from_payload([_bar(index) for index in range(65)])
    assert len(bars) == 65
    assert bars[-1]["close"] == 165
    assert bars[-1]["freshness"] == "IEX"
    technical = technical_snapshot(
        [row["close"] for row in bars], [row["high"] for row in bars], [row["low"] for row in bars], [row["volume"] for row in bars],
    )
    assert technical["ema20"] > 0
    assert technical["rsi"] >= 0
    assert technical["support"] < technical["resistance"]


@pytest.mark.asyncio
async def test_provider_failure_is_unavailable_not_demo(monkeypatch):
    provider = AlpacaMarketProvider()

    async def fail(*_args, **_kwargs):
        raise httpx.ConnectError("offline")

    monkeypatch.setattr(provider, "_json", fail)
    quote = await provider.get_quote("NVDA")
    bars = await provider.get_bars("NVDA", 90)
    assert quote["freshness"] == "UNAVAILABLE"
    assert quote["price"] is None
    assert quote["source"] == "Alpaca"
    assert bars == []


def test_stale_data_is_labelled_stale():
    quote = AlpacaMarketProvider()._quote_from_snapshot("QQQ", {
        "latestTrade": {"p": 400, "t": (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()},
        "prevDailyBar": {"c": 399},
    })
    assert quote["freshness"] == "STALE"


@pytest.mark.asyncio
async def test_partial_snapshot_failure_does_not_break_other_symbols(monkeypatch):
    provider = AlpacaMarketProvider()

    async def partial(*_args, **_kwargs):
        return {"snapshots": {"SPY": {"latestTrade": {"p": 500, "t": datetime.now(timezone.utc).isoformat()}, "prevDailyBar": {"c": 498}}}}

    monkeypatch.setattr(provider, "_json", partial)
    spy, missing = await provider.get_quotes(["SPY", "NOPE"])
    assert spy["freshness"] == "IEX"
    assert missing["symbol"] == "NOPE"
    assert missing["freshness"] == "UNAVAILABLE"


@pytest.mark.asyncio
async def test_scanner_bars_are_requested_in_one_batched_call(monkeypatch):
    provider = AlpacaMarketProvider()
    calls: list[dict[str, object]] = []

    async def grouped(_path, params, *_args):
        calls.append(params)
        return {"bars": {"SPY": [_bar(index) for index in range(60)], "QQQ": [_bar(index) for index in range(60)]}}

    monkeypatch.setattr(provider, "_json", grouped)
    bars = await provider.get_bars_bulk(["SPY", "QQQ"], 60)
    assert set(bars) == {"SPY", "QQQ"}
    assert len(calls) == 1
    assert calls[0]["symbols"] == "SPY,QQQ"


@pytest.mark.asyncio
async def test_provider_status_never_leaks_credentials(monkeypatch):
    monkeypatch.setenv("ALPACA_MARKET_DATA_API_KEY", "should-never-appear")
    monkeypatch.setenv("ALPACA_MARKET_DATA_SECRET_KEY", "also-never-appear")
    get_settings.cache_clear()
    try:
        payload = await AlpacaMarketProvider().provider_status()
        serialized = str(payload)
        assert "should-never-appear" not in serialized
        assert "also-never-appear" not in serialized
        assert set(payload) == {"market_provider", "market_feed", "market_data_status", "connection", "last_successful_request"}
    finally:
        get_settings.cache_clear()


@pytest.mark.asyncio
async def test_live_options_quote_chain_and_status_start_in_parallel(monkeypatch):
    started: list[str] = []
    gate = asyncio.Event()

    async def synchronize(name: str):
        started.append(name)
        if len(started) == 3:
            gate.set()
        await asyncio.wait_for(gate.wait(), timeout=0.15)

    class Market:
        async def get_quote(self, _symbol: str):
            await synchronize("quote")
            return {"price": 100.0, "freshness": "IEX"}

    class Options:
        async def get_chain(self, _symbol: str, spot: float | None):
            assert spot is None
            await synchronize("chain")
            return "LIVE", []

        async def provider_status(self):
            await synchronize("status")
            return {"options_status": "Healthy"}

    monkeypatch.setattr(main_module, "market_provider", lambda: Market())
    monkeypatch.setattr(main_module, "options_provider", lambda: Options())
    monkeypatch.setattr(main_module, "get_settings", lambda: type("Settings", (), {"demo_mode": False})())

    payload = await main_module.options("NVDA", None)

    assert set(started) == {"quote", "chain", "status"}
    assert payload["mode"] == "LIVE"


@pytest.mark.asyncio
async def test_public_market_cache_coalesces_duplicate_upstream_loads_and_reports_hits():
    cache = _PublicTTLCache()
    calls = 0
    started = asyncio.Event()

    async def loader():
        nonlocal calls
        calls += 1
        started.set()
        await asyncio.sleep(0.01)
        return {"value": 1}

    first = asyncio.create_task(cache.get_or_load("quotes:iex:SPY", 30, loader))
    await started.wait()
    second = asyncio.create_task(cache.get_or_load("quotes:iex:SPY", 30, loader))
    assert await first == await second == {"value": 1}
    assert calls == 1
    assert await cache.get_or_load("quotes:iex:SPY", 30, loader) == {"value": 1}
    report = cache.report()["outcomes"]
    assert report["quotes:miss"] == 1
    assert report["quotes:coalesced"] == 1
    assert report["quotes:hit"] == 1


@pytest.mark.asyncio
async def test_stock_intel_reuses_daily_indicator_history_for_default_chart():
    calls: list[tuple[str, int]] = []
    bars = [{"time": (datetime.now(timezone.utc) - timedelta(days=index)).date().isoformat(), "close": 100 + index} for index in range(260)]

    class Provider:
        async def get_bars(self, symbol: str, days: int):
            calls.append((symbol, days))
            return bars

        async def get_bars_for_range(self, *_args):
            raise AssertionError("3M should reuse daily indicator history")

    technical, chart = await main_module._stock_histories(Provider(), "NVDA", "3M")
    assert calls == [("NVDA", 260)]
    assert technical == bars
    assert chart


@pytest.mark.asyncio
async def test_alpaca_json_uses_the_shared_keep_alive_client_and_cache(monkeypatch):
    requests: list[str] = []

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"snapshots": {}}

    class Client:
        async def get(self, url, **_kwargs):
            requests.append(url)
            return Response()

    shared = Client()

    async def get_shared_client():
        return shared

    monkeypatch.setattr(providers_module, "alpaca_http_client", get_shared_client)
    provider = AlpacaMarketProvider()
    cache_key = f"quotes:iex:pool-{datetime.now(timezone.utc).timestamp()}"
    await provider._json("/v2/stocks/snapshots", {"symbols": "SPY"}, cache_key, 30)
    await provider._json("/v2/stocks/snapshots", {"symbols": "SPY"}, cache_key, 30)
    assert len(requests) == 1


@pytest.mark.asyncio
async def test_batch_quote_warms_single_symbol_stock_intel_request(monkeypatch):
    provider = AlpacaMarketProvider()
    calls = 0

    async def snapshots(_path, params, *_args):
        nonlocal calls
        calls += 1
        now = datetime.now(timezone.utc).isoformat()
        return {"snapshots": {
            symbol: {"latestTrade": {"p": 100, "t": now}, "prevDailyBar": {"c": 99}}
            for symbol in str(params["symbols"]).split(",")
        }}

    monkeypatch.setattr(provider, "_json", snapshots)
    initial = await provider.get_quotes(["LATENCY1", "LATENCY2"])
    repeated = await provider.get_quote("LATENCY1")
    assert calls == 1
    assert initial[0]["symbol"] == repeated["symbol"] == "LATENCY1"


def test_technical_snapshot_is_reused_when_public_bar_source_is_unchanged(monkeypatch):
    calls = 0
    main_module._technical_cache.clear()
    bars = [{"time": f"2026-01-{index + 1:02d}", "close": 100 + index, "high": 102 + index, "low": 99 + index, "volume": 1_000_000} for index in range(60)]

    def snapshot(*_args):
        nonlocal calls
        calls += 1
        return {"rsi": 55}

    monkeypatch.setattr(main_module, "technical_snapshot", snapshot)
    assert main_module._technical_from_bars(bars) == {"rsi": 55}
    assert main_module._technical_from_bars(bars) == {"rsi": 55}
    assert calls == 1
