"""Authoritative, server-derived risk state.

This module deliberately accepts only a bounded order ticket.  It never reads
client-provided exposure, price, classification, P&L, liquidity, or provider
health fields.  Any missing authoritative fact produces ``NO_TRADE``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .database import (
    AuditEvent, ExecutionOutbox, InstrumentMetadata, Portfolio, PortfolioPosition,
    PortfolioRiskLedger, SavedStrategy, StrategyRiskLedger, TradingControlState,
    TradingHold, User, utcnow,
)
from .risk import RISK_POLICY_VERSION, RiskLimits


OPEN_OUTBOX_STATES = {"VALIDATED", "CLAIMED", "DISPATCHING", "ACKNOWLEDGED", "PARTIALLY_FILLED", "UNKNOWN", "RECONCILING"}
TRADING_HOLD_SCOPES = {"GLOBAL", "USER", "STRATEGY", "RECONCILIATION"}


class MarketFactsProvider(Protocol):
    async def get_quote(self, symbol: str) -> dict[str, Any]: ...
    async def get_bars(self, symbol: str, days: int = 180) -> list[dict[str, Any]]: ...
    async def get_market_status(self) -> dict[str, Any]: ...


@dataclass(frozen=True, slots=True)
class TrustedInstrument:
    symbol: str
    asset_type: str
    underlying_symbol: str
    sector: str
    theme: str
    leverage: float
    contract_multiplier: float
    option_strike: float | None = None


@dataclass(frozen=True, slots=True)
class CanonicalPosition:
    symbol: str
    underlying_symbol: str
    sector: str
    theme: str
    strategy_id: str | None
    signed_notional: float
    gross_notional: float


@dataclass(frozen=True, slots=True)
class CanonicalRiskContext:
    """Immutable facts used for a decision and copied into a signed intent."""

    user_id: str
    symbol: str
    side: str
    quantity: float
    order_type: str
    conservative_price: float
    order_notional: float
    instrument: TrustedInstrument
    account_equity: float
    cash: float
    buying_power: float
    positions: tuple[CanonicalPosition, ...]
    pending_orders: tuple[CanonicalPosition, ...]
    symbol_exposure: float
    underlying_exposure: float
    sector_exposure: float
    theme_exposure: float
    gross_exposure: float
    strategy_exposure: float
    daily_pnl: float
    weekly_pnl: float
    drawdown_pct: float
    strategy_daily_pnl: float
    strategy_drawdown_pct: float
    market_data_timestamp: str
    provider_status: str
    market_status: str
    spread_pct: float
    liquidity: float
    hold_generation: int
    active_hold_reasons: tuple[str, ...]
    strategy_id: str


# Curated server-side metadata is intentionally small. It is a fail-closed
# allowlist, not a browser-maintained instrument catalogue. Additional symbols
# require an administrator/reconciler-created InstrumentMetadata record.
_CURATED: dict[str, tuple[str, str, str, str, float]] = {
    "SPY": ("ETF", "Broad Market", "US_EQUITY_CORE", "SPY", 1.0),
    "QQQ": ("ETF", "Technology", "US_EQUITY_GROWTH", "QQQ", 1.0),
    "DIA": ("ETF", "Broad Market", "US_EQUITY_CORE", "DIA", 1.0),
    "IWM": ("ETF", "Small Cap", "US_EQUITY_SMALL_CAP", "IWM", 1.0),
    "SMH": ("ETF", "Semiconductors", "SEMICONDUCTORS", "SMH", 1.0),
    "SOXX": ("ETF", "Semiconductors", "SEMICONDUCTORS", "SOXX", 1.0),
    "NVDA": ("STOCK", "Semiconductors", "SEMICONDUCTORS", "NVDA", 1.0),
    "AMD": ("STOCK", "Semiconductors", "SEMICONDUCTORS", "AMD", 1.0),
    "AVGO": ("STOCK", "Semiconductors", "SEMICONDUCTORS", "AVGO", 1.0),
    "AAPL": ("STOCK", "Technology", "MEGA_CAP_TECH", "AAPL", 1.0),
    "MSFT": ("STOCK", "Technology", "MEGA_CAP_TECH", "MSFT", 1.0),
    "META": ("STOCK", "Communication", "MEGA_CAP_TECH", "META", 1.0),
    "AMZN": ("STOCK", "Consumer Discretionary", "MEGA_CAP_TECH", "AMZN", 1.0),
    "GOOGL": ("STOCK", "Communication", "MEGA_CAP_TECH", "GOOGL", 1.0),
    "TSLA": ("STOCK", "Consumer Discretionary", "EV_GROWTH", "TSLA", 1.0),
    "JPM": ("STOCK", "Financials", "US_FINANCIALS", "JPM", 1.0),
}


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _as_utc(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


async def resolve_instrument(db: AsyncSession, symbol: str) -> TrustedInstrument | None:
    symbol = symbol.strip().upper()
    stored = await db.get(InstrumentMetadata, symbol)
    if stored is not None:
        required = (stored.asset_type, stored.underlying_symbol, stored.sector, stored.theme, stored.source)
        if all(isinstance(value, str) and value.strip() for value in required) and stored.leverage > 0 and stored.contract_multiplier > 0:
            if stored.asset_type == "OPTION" and (stored.option_strike is None or stored.option_strike <= 0):
                return None
            return TrustedInstrument(
                symbol=symbol, asset_type=stored.asset_type, underlying_symbol=stored.underlying_symbol or symbol,
                sector=stored.sector or "", theme=stored.theme or "", leverage=stored.leverage,
                contract_multiplier=stored.contract_multiplier, option_strike=stored.option_strike,
            )
    curated = _CURATED.get(symbol)
    if curated is None:
        return None
    asset_type, sector, theme, underlying, leverage = curated
    return TrustedInstrument(symbol, asset_type, underlying, sector, theme, leverage, 1.0)


async def _personal_portfolio(db: AsyncSession, user_id: str) -> Portfolio:
    row = (await db.execute(select(Portfolio).where(Portfolio.user_id == user_id, Portfolio.name == "Personal portfolio"))).scalar_one_or_none()
    if row is None:
        row = Portfolio(user_id=user_id, name="Personal portfolio", cash=100_000.0)
        db.add(row)
        await db.flush()
    return row


def _quote_facts(quote: dict[str, Any], order_type: str, side: str, limit_price: float | None) -> tuple[float, float, str] | None:
    """Return a conservative executable price, spread and timestamp or fail."""
    freshness = str(quote.get("freshness") or quote.get("data_quality") or "UNAVAILABLE").upper()
    if freshness not in {"LIVE", "IEX", "DELAYED"}:
        return None
    bid, ask, last = _number(quote.get("bid")), _number(quote.get("ask")), _number(quote.get("price"))
    updated = _as_utc(quote.get("updated_at"))
    if bid is None or ask is None or ask < bid or updated is None:
        return None
    midpoint = (bid + ask) / 2
    if midpoint <= 0:
        return None
    spread_pct = (ask - bid) / midpoint * 100
    if order_type == "market":
        # A conservative half-spread plus 10 bps accounts for adverse movement
        # while the order is in flight. SELL positions are never allowed to
        # create a short in this currently non-operational executor.
        base = ask if side == "BUY" else bid
        price = base * (1.001 if side == "BUY" else 0.999)
    else:
        if limit_price is None or limit_price <= 0:
            return None
        price = max(limit_price, ask) if side == "BUY" else min(limit_price, bid)
    return price, spread_pct, updated.isoformat()


async def _position_from_row(db: AsyncSession, provider: MarketFactsProvider, row: PortfolioPosition) -> CanonicalPosition | None:
    instrument = await resolve_instrument(db, row.symbol)
    if instrument is None:
        return None
    quote = await provider.get_quote(instrument.symbol)
    facts = _quote_facts(quote, "market", "BUY", None)
    if facts is None:
        return None
    price, _, _ = facts
    premium_notional = abs(float(row.quantity)) * price * instrument.contract_multiplier * instrument.leverage
    underlying_notional = abs(float(row.quantity)) * float(instrument.option_strike or 0) * instrument.contract_multiplier if instrument.asset_type == "OPTION" else 0.0
    gross = max(premium_notional, underlying_notional)
    signed = gross if row.quantity >= 0 else -gross
    return CanonicalPosition(instrument.symbol, instrument.underlying_symbol, instrument.sector, instrument.theme, None, signed, gross)


async def _pending_positions(db: AsyncSession, user_id: str) -> tuple[CanonicalPosition, ...] | None:
    rows = (await db.execute(select(ExecutionOutbox).where(ExecutionOutbox.user_id == user_id, ExecutionOutbox.state.in_(OPEN_OUTBOX_STATES)))).scalars().all()
    result: list[CanonicalPosition] = []
    for row in rows:
        payload = row.payload if isinstance(row.payload, dict) else {}
        required = (payload.get("symbol"), payload.get("underlying_symbol"), payload.get("sector"), payload.get("theme"), payload.get("side"), payload.get("notional"))
        if not all(required):
            return None
        notional = float(payload["notional"])
        signed = notional if payload["side"] == "BUY" else -notional
        result.append(CanonicalPosition(str(payload["symbol"]), str(payload["underlying_symbol"]), str(payload["sector"]), str(payload["theme"]), str(payload.get("strategy_id") or ""), signed, abs(notional)))
    return tuple(result)


def _aggregate(lines: tuple[CanonicalPosition, ...], key: str) -> dict[str, float]:
    values: dict[str, float] = {}
    for line in lines:
        group = str(getattr(line, key))
        # Concentration measures exposure, not P&L. A sale can reduce an
        # existing long but cannot generate synthetic negative headroom.
        values[group] = values.get(group, 0.0) + line.signed_notional
    return {group: max(0.0, value) for group, value in values.items()}


async def build_canonical_risk_context(
    db: AsyncSession,
    provider: MarketFactsProvider,
    *,
    user: User,
    symbol: str,
    quantity: float,
    side: str,
    order_type: str,
    limit_price: float | None,
    strategy_id: str,
) -> CanonicalRiskContext | None:
    """Assemble all facts once. Any unknown fact returns ``None`` / no-trade."""
    instrument = await resolve_instrument(db, symbol)
    if instrument is None:
        return None
    strategy = (await db.execute(select(StrategyRiskLedger).where(
        StrategyRiskLedger.user_id == user.id, StrategyRiskLedger.strategy_id == strategy_id,
    ))).scalar_one_or_none()
    saved_strategy = await db.get(SavedStrategy, strategy_id)
    if strategy is None or not strategy.is_active or saved_strategy is None or saved_strategy.user_id != user.id:
        return None
    quote, status = await provider.get_quote(instrument.symbol), await provider.get_market_status()
    quote_facts = _quote_facts(quote, order_type, side, limit_price)
    if quote_facts is None or not bool(status.get("is_open")):
        return None
    price, spread_pct, quote_timestamp = quote_facts
    freshness = str(quote.get("freshness") or "UNAVAILABLE").upper()
    quote_time = _as_utc(quote_timestamp)
    if quote_time is None or (datetime.now(timezone.utc) - quote_time).total_seconds() > 300:
        return None
    bars = await provider.get_bars(instrument.symbol, 5)
    latest_bar = bars[-1] if bars else {}
    liquidity = (_number(latest_bar.get("volume")) or 0) * price
    if liquidity <= 0:
        return None
    account = await _personal_portfolio(db, user.id)
    positions_rows = (await db.execute(select(PortfolioPosition).where(PortfolioPosition.portfolio_id == account.id))).scalars().all()
    positions_list: list[CanonicalPosition] = []
    for row in positions_rows:
        position = await _position_from_row(db, provider, row)
        if position is None:
            return None
        positions_list.append(position)
    pending = await _pending_positions(db, user.id)
    if pending is None:
        return None
    positions = tuple(positions_list)
    combined = positions + pending
    current_symbols = _aggregate(combined, "symbol")
    # Selling more than the long position is unsupported (and fails closed).
    existing_symbol = current_symbols.get(instrument.symbol, 0.0)
    premium_notional = quantity * price * instrument.contract_multiplier * instrument.leverage
    # Premium and multiplier alone understate a derivative's correlated
    # underlying exposure. Use the larger conservative amount for every cap.
    underlying_notional = quantity * float(instrument.option_strike or 0) * instrument.contract_multiplier if instrument.asset_type == "OPTION" else 0.0
    order_notional = max(premium_notional, underlying_notional)
    if side == "SELL" and order_notional > existing_symbol:
        return None
    signed_new = order_notional if side == "BUY" else -order_notional
    post_lines = combined + (CanonicalPosition(instrument.symbol, instrument.underlying_symbol, instrument.sector, instrument.theme, strategy_id, signed_new, order_notional),)
    symbol_values = _aggregate(post_lines, "symbol")
    underlying_values = _aggregate(post_lines, "underlying_symbol")
    sector_values = _aggregate(post_lines, "sector")
    theme_values = _aggregate(post_lines, "theme")
    gross = sum(abs(line.signed_notional) for line in post_lines)
    equity = float(account.cash) + sum(line.signed_notional for line in positions)
    if equity <= 0:
        return None
    ledger = await db.get(PortfolioRiskLedger, user.id)
    if ledger is None:
        ledger = PortfolioRiskLedger(user_id=user.id, equity=equity, peak_equity=equity)
        db.add(ledger)
        await db.flush()
    else:
        ledger.equity = equity
        ledger.peak_equity = max(float(ledger.peak_equity), equity)
    drawdown = max(0.0, (float(ledger.peak_equity) - equity) / float(ledger.peak_equity) * 100) if ledger.peak_equity > 0 else 0.0
    controls = await db.get(TradingControlState, "global")
    generation = controls.hold_generation if controls else 0
    active_holds = (await db.execute(select(TradingHold).where(TradingHold.active.is_(True)))).scalars().all()
    hold_reason_set = {hold.reason_code for hold in active_holds if hold.scope == "GLOBAL" or hold.user_id == user.id or (hold.scope == "STRATEGY" and hold.strategy_id == strategy_id)}
    if controls and controls.global_hold_active:
        hold_reason_set.add("GLOBAL_KILL_SWITCH")
    hold_reasons = tuple(sorted(hold_reason_set))
    strategy_drawdown = max(0.0, (float(strategy.peak_value) - float(strategy.current_value)) / float(strategy.peak_value) * 100) if strategy.peak_value > 0 else 0.0
    return CanonicalRiskContext(
        user_id=user.id, symbol=instrument.symbol, side=side, quantity=quantity, order_type=order_type,
        conservative_price=price, order_notional=order_notional, instrument=instrument, account_equity=equity,
        cash=float(account.cash), buying_power=max(0.0, float(account.cash)), positions=positions, pending_orders=pending,
        symbol_exposure=symbol_values.get(instrument.symbol, 0.0), underlying_exposure=underlying_values.get(instrument.underlying_symbol, 0.0),
        sector_exposure=sector_values.get(instrument.sector, 0.0), theme_exposure=theme_values.get(instrument.theme, 0.0),
        gross_exposure=gross, strategy_exposure=float(strategy.allocation_value) + (order_notional if side == "BUY" else -order_notional),
        daily_pnl=float(ledger.daily_pnl), weekly_pnl=float(ledger.weekly_pnl), drawdown_pct=drawdown,
        strategy_daily_pnl=float(strategy.daily_pnl), strategy_drawdown_pct=strategy_drawdown,
        market_data_timestamp=quote_timestamp, provider_status=freshness, market_status="OPEN", spread_pct=spread_pct,
        liquidity=liquidity, hold_generation=generation, active_hold_reasons=hold_reasons, strategy_id=strategy_id,
    )


def canonical_decision(context: CanonicalRiskContext | None, limits: RiskLimits) -> dict[str, Any]:
    if context is None:
        return {"decision": "NO_TRADE", "reasons": ["Canonical account, instrument, market, or strategy facts are unavailable."], "policy_version": RISK_POLICY_VERSION, "fail_closed": True, "authoritative": True}
    reasons: list[str] = list(context.active_hold_reasons)
    equity = context.account_equity
    caps = {"STOCK": limits.max_stock_pct, "ETF": limits.max_etf_pct, "LEVERAGED_ETF": limits.max_leveraged_etf_pct, "OPTION": limits.max_options_pct}
    asset_cap = caps.get(context.instrument.asset_type)
    if asset_cap is None:
        reasons.append("UNSUPPORTED_INSTRUMENT")
    if context.provider_status not in {"LIVE", "IEX", "DELAYED"}:
        reasons.append("MARKET_DATA_INTEGRITY")
    if context.market_status != "OPEN":
        reasons.append("MARKET_CLOSED")
    if context.spread_pct > limits.max_bid_ask_spread_pct:
        reasons.append("SPREAD_LIMIT")
    if context.liquidity < limits.min_liquidity:
        reasons.append("LIQUIDITY_LIMIT")
    if context.order_notional > limits.max_order_notional or context.order_notional > context.buying_power:
        reasons.append("BUYING_POWER_OR_ORDER_LIMIT")
    if asset_cap is not None and context.symbol_exposure / equity * 100 > asset_cap:
        reasons.append("SYMBOL_POST_TRADE_LIMIT")
    # Options share the underlying's concentration bucket; related ETFs share
    # theme/sector buckets. No client-selected classification can escape this.
    if context.underlying_exposure / equity * 100 > limits.max_stock_pct:
        reasons.append("UNDERLYING_POST_TRADE_LIMIT")
    if context.sector_exposure / equity * 100 > limits.max_sector_pct:
        reasons.append("SECTOR_POST_TRADE_LIMIT")
    if context.theme_exposure / equity * 100 > limits.max_theme_pct:
        reasons.append("THEME_POST_TRADE_LIMIT")
    if context.gross_exposure / equity * 100 > limits.max_exposure_pct:
        reasons.append("GROSS_EXPOSURE_LIMIT")
    if context.strategy_exposure / equity * 100 > limits.strategy_max_allocation_pct:
        reasons.append("STRATEGY_ALLOCATION_LIMIT")
    if context.daily_pnl / equity * 100 <= -limits.max_daily_loss_pct:
        reasons.append("DAILY_LOSS_LIMIT")
    if context.weekly_pnl / equity * 100 <= -limits.max_weekly_loss_pct:
        reasons.append("WEEKLY_LOSS_LIMIT")
    if context.drawdown_pct >= limits.max_drawdown_pct:
        reasons.append("DRAWDOWN_LIMIT")
    if context.strategy_daily_pnl / equity * 100 <= -limits.strategy_daily_loss_pct:
        reasons.append("STRATEGY_DAILY_LOSS_LIMIT")
    if context.strategy_drawdown_pct >= limits.strategy_drawdown_pct:
        reasons.append("STRATEGY_DRAWDOWN_LIMIT")
    if limits.live_capital_limit > 0 and context.gross_exposure > limits.live_capital_limit:
        reasons.append("CAPITAL_CEILING_LIMIT")
    if limits.kill_switch:
        reasons.append("KILL_SWITCH")
    return {
        "decision": "REJECTED" if reasons else "APPROVED", "reasons": sorted(set(reasons)) or ["Canonical post-trade controls passed"],
        "policy_version": limits.policy_version, "fail_closed": bool(reasons), "authoritative": True,
        "post_trade": {
            "symbol_pct": round(context.symbol_exposure / equity * 100, 2), "underlying_pct": round(context.underlying_exposure / equity * 100, 2),
            "sector_pct": round(context.sector_exposure / equity * 100, 2), "theme_pct": round(context.theme_exposure / equity * 100, 2),
            "gross_pct": round(context.gross_exposure / equity * 100, 2), "strategy_pct": round(context.strategy_exposure / equity * 100, 2),
            "order_notional": round(context.order_notional, 2), "conservative_price": round(context.conservative_price, 4),
        },
    }


async def persist_circuit_breakers(db: AsyncSession, context: CanonicalRiskContext, decision: dict[str, Any]) -> list[str]:
    """Turn loss/data/reconciliation breakers into durable controls atomically."""
    trigger_codes = {"DAILY_LOSS_LIMIT", "WEEKLY_LOSS_LIMIT", "DRAWDOWN_LIMIT", "STRATEGY_DAILY_LOSS_LIMIT", "STRATEGY_DRAWDOWN_LIMIT", "MARKET_DATA_INTEGRITY"}
    reasons = set(decision.get("reasons") or ()) & trigger_codes
    if not reasons:
        return []
    control = await db.get(TradingControlState, "global", with_for_update=True)
    if control is None:
        control = TradingControlState(key="global")
        db.add(control)
        await db.flush()
    control.hold_generation += 1
    created: list[str] = []
    for reason in sorted(reasons):
        scope = "STRATEGY" if reason.startswith("STRATEGY_") else "USER"
        exists = (await db.execute(select(TradingHold).where(TradingHold.active.is_(True), TradingHold.scope == scope, TradingHold.user_id == context.user_id, TradingHold.strategy_id == (context.strategy_id if scope == "STRATEGY" else None), TradingHold.reason_code == reason))).scalar_one_or_none()
        if exists:
            continue
        db.add(TradingHold(scope=scope, user_id=context.user_id, strategy_id=context.strategy_id if scope == "STRATEGY" else None, reason_code=reason, hold_generation=control.hold_generation))
        db.add(AuditEvent(user_id=context.user_id, event_type="TRADING_HOLD_CREATED", resource="risk/circuit-breaker", result="DENIED", safe_metadata={"reason_code": reason, "hold_generation": control.hold_generation}))
        created.append(reason)
    return created
