"""Deterministic, fail-closed risk policy.

This module intentionally has no broker imports and accepts no free-form AI
instructions. A strategy may provide a structured, bounded signal; this layer
sets the maximum permitted position and rejects any unsafe order.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from .schemas import RiskRequest


RISK_POLICY_VERSION = "2026-08-security-freeze-v1"

# Code-level ceilings/floors are deliberately independent of database policy.
# An administrator may make a deployment more conservative, never relax these
# production safety boundaries. Values are percentage points unless noted.
ABSOLUTE_SAFETY_CEILINGS: dict[str, float | int] = {
    "max_stock_pct": 15.0, "max_etf_pct": 30.0, "max_leveraged_etf_pct": 10.0,
    "max_options_pct": 5.0, "max_sector_pct": 35.0, "max_theme_pct": 40.0,
    "max_exposure_pct": 90.0, "max_leverage": 1.0, "max_order_notional": 50_000.0,
    "max_open_positions": 50, "max_daily_loss_pct": 5.0, "max_weekly_loss_pct": 10.0,
    "max_drawdown_pct": 20.0, "max_risk_per_trade_pct": 2.0,
    "max_bid_ask_spread_pct": 2.0, "max_data_age_seconds": 300,
    "strategy_max_allocation_pct": 35.0, "strategy_daily_loss_pct": 3.0,
    "strategy_drawdown_pct": 10.0, "moderate_drawdown_pct": 10.0,
    "severe_drawdown_pct": 15.0, "live_capital_limit": 50_000.0,
}
ABSOLUTE_SAFETY_FLOORS: dict[str, float] = {"min_liquidity": 500_000.0}


@dataclass(frozen=True, slots=True)
class RiskLimits:
    """Central risk configuration with conservative defaults.

    ``max_position_pct`` remains as a compatibility alias for the original UI;
    equity orders use ``max_stock_pct`` and every asset type has its own cap.
    """

    max_position_pct: float = 8.0
    max_stock_pct: float = 8.0
    max_etf_pct: float = 15.0
    max_leveraged_etf_pct: float = 5.0
    max_options_pct: float = 2.0
    max_exposure_pct: float = 60.0
    max_sector_pct: float = 25.0
    max_theme_pct: float = 30.0
    max_leverage: float = 1.0
    max_order_notional: float = 10_000.0
    max_open_positions: int = 15
    max_daily_loss_pct: float = 2.0
    max_weekly_loss_pct: float = 4.0
    max_drawdown_pct: float = 10.0
    max_risk_per_trade_pct: float = 0.5
    min_liquidity: float = 1_000_000.0
    max_bid_ask_spread_pct: float = 0.50
    max_data_age_seconds: int = 120
    strategy_max_allocation_pct: float = 20.0
    strategy_daily_loss_pct: float = 1.0
    strategy_drawdown_pct: float = 5.0
    moderate_drawdown_pct: float = 5.0
    severe_drawdown_pct: float = 8.0
    live_capital_limit: float = 0.0
    kill_switch: bool = False
    policy_version: str = RISK_POLICY_VERSION

    @classmethod
    def from_mapping(cls, values: dict[str, Any] | None = None) -> "RiskLimits":
        """Build a policy and enforce immutable code safety boundaries."""
        merged: dict[str, Any] = asdict(cls())
        for key, value in (values or {}).items():
            if value is not None and key in merged:
                merged[key] = value
        # The legacy setting maps to the stock cap when a newer field was not
        # provided. This keeps an existing user configuration conservative.
        if values and values.get("max_stock_pct") is None and values.get("max_position_pct") is not None:
            merged["max_stock_pct"] = values["max_position_pct"]
        for key, ceiling in ABSOLUTE_SAFETY_CEILINGS.items():
            try:
                merged[key] = min(float(merged[key]), ceiling)
            except (TypeError, ValueError):
                merged[key] = ceiling
        for key, floor in ABSOLUTE_SAFETY_FLOORS.items():
            try:
                merged[key] = max(float(merged[key]), floor)
            except (TypeError, ValueError):
                merged[key] = floor
        # A safety ceiling must not create a nonsensical drawdown scale.
        merged["moderate_drawdown_pct"] = min(float(merged["moderate_drawdown_pct"]), float(merged["max_drawdown_pct"]))
        merged["severe_drawdown_pct"] = min(float(merged["severe_drawdown_pct"]), float(merged["max_drawdown_pct"]))
        if merged["severe_drawdown_pct"] <= merged["moderate_drawdown_pct"]:
            merged["severe_drawdown_pct"] = min(float(merged["max_drawdown_pct"]), float(merged["moderate_drawdown_pct"]) + 0.1)
        merged["max_position_pct"] = min(float(merged["max_position_pct"]), float(merged["max_stock_pct"]))
        merged["max_open_positions"] = int(merged["max_open_positions"])
        merged["max_data_age_seconds"] = int(merged["max_data_age_seconds"])
        return cls(**merged)

    def as_safe_dict(self) -> dict[str, Any]:
        return asdict(self)


def _clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))


def _asset_cap(req: RiskRequest, limits: RiskLimits) -> float:
    return {
        "STOCK": limits.max_stock_pct,
        "ETF": limits.max_etf_pct,
        "LEVERAGED_ETF": limits.max_leveraged_etf_pct,
        "OPTION": limits.max_options_pct,
    }[req.asset_type]


def _drawdown_percent(req: RiskRequest) -> float:
    peak = req.peak_equity or req.portfolio_equity
    return max(0.0, (peak - req.portfolio_equity) / peak * 100)


def dynamic_position_limit(req: RiskRequest, limits: RiskLimits) -> dict[str, Any]:
    """Calculate a bounded maximum notional from account-risk, not conviction."""
    drawdown = _drawdown_percent(req)
    tier_factor = {"NORMAL": 0.55, "STRONG": 0.70, "HIGH_CONVICTION": 0.85, "EXCEPTIONAL": 1.0}[req.conviction_tier]
    confidence_factor = _clamp((req.confidence + req.calibrated_confidence) / 2, 0.25, 1.0)
    volatility_factor = _clamp(2.0 / req.atr_percent, 0.25, 1.0)
    liquidity_factor = _clamp(req.liquidity / max(limits.min_liquidity * 3, 1), 0.30, 1.0)
    regime_factor = 1.0 if req.market_status == "OPEN" else 0.0
    concentration_headroom = min(
        max(0.0, 1 - req.sector_exposure / req.portfolio_equity * 100 / limits.max_sector_pct),
        max(0.0, 1 - req.theme_exposure / req.portfolio_equity * 100 / limits.max_theme_pct),
    )
    concentration_factor = _clamp(concentration_headroom, 0.0, 1.0)
    if drawdown >= limits.severe_drawdown_pct:
        drawdown_factor = 0.0
    elif drawdown >= limits.moderate_drawdown_pct:
        drawdown_factor = 0.50
    else:
        drawdown_factor = 1.0
    base_allocation_pct = _asset_cap(req, limits)
    dynamic_allocation_pct = base_allocation_pct * tier_factor * confidence_factor * volatility_factor * liquidity_factor * regime_factor * concentration_factor * drawdown_factor
    stop_risk_notional = req.portfolio_equity * limits.max_risk_per_trade_pct / 100 / (req.stop_distance_pct / 100)
    strategy_remaining = max(0.0, req.portfolio_equity * limits.strategy_max_allocation_pct / 100 - req.strategy_exposure)
    buying_power = req.buying_power if req.buying_power is not None else req.portfolio_equity
    maximum_notional = min(req.portfolio_equity * dynamic_allocation_pct / 100, stop_risk_notional, limits.max_order_notional, strategy_remaining, buying_power)
    return {
        "policy_version": limits.policy_version,
        "base_allocation_pct": round(base_allocation_pct, 4),
        "dynamic_allocation_pct": round(max(0.0, dynamic_allocation_pct), 4),
        "max_notional": round(max(0.0, maximum_notional), 2),
        "risk_budget": round(req.portfolio_equity * limits.max_risk_per_trade_pct / 100, 2),
        "factors": {"conviction_tier": tier_factor, "confidence": round(confidence_factor, 4), "volatility": round(volatility_factor, 4), "liquidity": round(liquidity_factor, 4), "regime": regime_factor, "concentration": round(concentration_factor, 4), "drawdown": drawdown_factor},
        "drawdown_percent": round(drawdown, 4),
    }


def automatic_halt_reasons(req: RiskRequest, limits: RiskLimits) -> list[str]:
    """Return durable-hold triggers for a future trusted execution orchestrator.

    The preview endpoint deliberately does not persist these from browser input:
    an untrusted client must never be able to halt another account. A future
    executor calls this only with reconciled canonical account/provider state.
    """
    drawdown = _drawdown_percent(req)
    reasons: list[str] = []
    if req.daily_pnl / req.portfolio_equity * 100 <= -limits.max_daily_loss_pct:
        reasons.append("DAILY_LOSS_LIMIT")
    if drawdown >= limits.max_drawdown_pct:
        reasons.append("DRAWDOWN_LIMIT")
    if not req.provider_healthy or req.data_conflict or req.data_freshness in {"STALE", "UNAVAILABLE"}:
        reasons.append("MARKET_DATA_INTEGRITY")
    if req.leverage > limits.max_leverage:
        reasons.append("UNEXPECTED_LEVERAGE")
    return reasons


def evaluate(req: RiskRequest, limits: RiskLimits | None = None) -> dict[str, Any]:
    """Approve only if every deterministic safeguard passes; ambiguity rejects."""
    limits = limits or RiskLimits.from_mapping()
    reasons: list[str] = []
    position_percent = req.proposed_value / req.portfolio_equity * 100
    gross_after = (req.gross_exposure + req.proposed_value) / req.portfolio_equity * 100
    sector_after = (req.sector_exposure + req.proposed_value) / req.portfolio_equity * 100
    theme_after = (req.theme_exposure + req.proposed_value) / req.portfolio_equity * 100
    drawdown = _drawdown_percent(req)
    sizing = dynamic_position_limit(req, limits)
    halt_reasons = automatic_halt_reasons(req, limits)

    if limits.kill_switch:
        reasons.append("Trading kill switch is active")
    if req.data_freshness in {"DEMO", "STALE", "UNAVAILABLE"} or req.data_age_seconds > limits.max_data_age_seconds:
        reasons.append("Market data is stale, unavailable, or unsuitable for trading")
    if not req.provider_healthy or req.data_conflict:
        reasons.append("Market data provider health or price consistency cannot be confirmed")
    if req.market_status != "OPEN":
        reasons.append("Market status cannot support a new order")
    if req.bid_ask_spread_pct > limits.max_bid_ask_spread_pct:
        reasons.append("Maximum bid/ask spread exceeded")
    if req.liquidity < limits.min_liquidity:
        reasons.append("Minimum liquidity not met")
    if req.event_risk:
        reasons.append("Material event risk requires a no-trade decision")
    if position_percent > _asset_cap(req, limits):
        reasons.append("Asset-type maximum allocation exceeded")
    if req.proposed_value > limits.max_order_notional:
        reasons.append("Maximum order notional exceeded")
    if gross_after > limits.max_exposure_pct:
        reasons.append("Maximum portfolio gross exposure exceeded")
    if sector_after > limits.max_sector_pct:
        reasons.append("Maximum sector exposure exceeded")
    if theme_after > limits.max_theme_pct:
        reasons.append("Maximum correlated-theme exposure exceeded")
    if req.leverage > limits.max_leverage:
        reasons.append("Maximum leverage exceeded")
    if req.open_positions >= limits.max_open_positions and req.side == "BUY":
        reasons.append("Maximum open-position count reached")
    if req.daily_pnl / req.portfolio_equity * 100 <= -limits.max_daily_loss_pct:
        reasons.append("Daily loss circuit breaker is active")
    if req.weekly_pnl / req.portfolio_equity * 100 <= -limits.max_weekly_loss_pct:
        reasons.append("Weekly loss circuit breaker is active")
    if drawdown >= limits.max_drawdown_pct:
        reasons.append("Portfolio drawdown circuit breaker is active")
    if req.strategy_daily_pnl / req.portfolio_equity * 100 <= -limits.strategy_daily_loss_pct:
        reasons.append("Strategy daily-loss budget is exhausted")
    if req.strategy_drawdown_pct >= limits.strategy_drawdown_pct:
        reasons.append("Strategy drawdown budget is exhausted")
    if req.strategy_exposure + req.proposed_value > req.portfolio_equity * limits.strategy_max_allocation_pct / 100:
        reasons.append("Strategy allocation budget exceeded")
    if req.proposed_value > sizing["max_notional"]:
        reasons.append("Proposed notional exceeds the deterministic dynamic position limit")

    return {"decision": "REJECTED" if reasons else "APPROVED", "reasons": reasons or ["All deterministic hard limits and dynamic sizing controls passed"], "position_percent": round(position_percent, 2), "gross_exposure_after_pct": round(gross_after, 2), "sector_exposure_after_pct": round(sector_after, 2), "theme_exposure_after_pct": round(theme_after, 2), "dynamic_sizing": sizing, "automatic_halt_reasons": halt_reasons, "policy_version": limits.policy_version, "fail_closed": bool(reasons)}
