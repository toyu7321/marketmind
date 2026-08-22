from __future__ import annotations

from datetime import datetime, timedelta, timezone
import asyncio

import httpx
import pytest

from app.config import get_settings
from app import main as main_module
from app.indicators import technical_snapshot
from app.providers import AlpacaMarketProvider


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
    monkeypatch.setenv("ALPACA_API_KEY", "should-never-appear")
    monkeypatch.setenv("ALPACA_SECRET_KEY", "also-never-appear")
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
