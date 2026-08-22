from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from app.redaction import redact, redact_text
from app.risk import ABSOLUTE_SAFETY_CEILINGS, RiskLimits, evaluate
from app.schemas import RiskRequest, TradeIntent


def safe_request(**overrides) -> RiskRequest:
    payload = {
        "symbol": "JPM", "proposed_value": 1_000, "portfolio_equity": 100_000,
        "current_exposure": 10_000, "sector_exposure": 5_000, "liquidity": 10_000_000,
        "gross_exposure": 10_000, "theme_exposure": 5_000, "strategy_exposure": 0,
        "open_positions": 1, "atr_percent": 1, "stop_distance_pct": 5,
        "confidence": 0.8, "calibrated_confidence": 0.8, "conviction_tier": "STRONG",
    }
    payload.update(overrides)
    return RiskRequest(**payload)


def assert_rejected(**overrides):
    result = evaluate(safe_request(**overrides))
    assert result["decision"] == "REJECTED"
    assert result["fail_closed"] is True
    return result


def test_immutable_safety_ceilings_apply_even_to_an_admin_policy():
    limits = RiskLimits.from_mapping({"max_stock_pct": 100, "max_order_notional": 9_999_999, "min_liquidity": 1})
    assert limits.max_stock_pct == ABSOLUTE_SAFETY_CEILINGS["max_stock_pct"]
    assert limits.max_order_notional == ABSOLUTE_SAFETY_CEILINGS["max_order_notional"]
    assert limits.min_liquidity >= 500_000


@pytest.mark.parametrize("proposed_value", [50_000, 100_000])
def test_extreme_ai_confidence_cannot_bypass_stock_cap_or_dynamic_size(proposed_value):
    result = assert_rejected(proposed_value=proposed_value, confidence=1, calibrated_confidence=1, conviction_tier="EXCEPTIONAL")
    assert any("allocation" in reason.lower() or "dynamic" in reason.lower() for reason in result["reasons"])


def test_sector_and_theme_controls_catch_hidden_semiconductor_concentration():
    result = assert_rejected(symbol="AMD", proposed_value=8_000, sector_exposure=20_000, theme_exposure=25_000)
    assert any("sector" in reason.lower() for reason in result["reasons"])
    assert any("theme" in reason.lower() for reason in result["reasons"])


@pytest.mark.parametrize("overrides", [
    {"data_freshness": "STALE"}, {"data_freshness": "UNAVAILABLE"}, {"provider_healthy": False},
    {"data_conflict": True}, {"data_age_seconds": 121}, {"bid_ask_spread_pct": 0.51},
])
def test_data_quality_failures_are_no_trade(overrides):
    assert any("data" in reason.lower() or "spread" in reason.lower() for reason in assert_rejected(**overrides)["reasons"])


@pytest.mark.parametrize("overrides", [
    {"portfolio_equity": 90_000, "peak_equity": 100_000}, {"daily_pnl": -2_000}, {"weekly_pnl": -4_000},
    {"strategy_exposure": 20_000}, {"strategy_daily_pnl": -1_000}, {"strategy_drawdown_pct": 5},
])
def test_drawdown_and_strategy_circuit_breakers_reject(overrides):
    assert_rejected(**overrides)


def test_dynamic_sizing_uses_risk_budget_and_shrinks_for_volatility_and_drawdown():
    calm = evaluate(safe_request(proposed_value=1_000))
    volatile = evaluate(safe_request(proposed_value=1_000, atr_percent=10, peak_equity=95_000))
    assert calm["dynamic_sizing"]["risk_budget"] == 500
    assert volatile["dynamic_sizing"]["max_notional"] < calm["dynamic_sizing"]["max_notional"]


def test_canonical_execution_state_can_raise_automatic_hold_triggers():
    result = evaluate(safe_request(daily_pnl=-2_000, provider_healthy=False))
    assert set(result["automatic_halt_reasons"]) == {"DAILY_LOSS_LIMIT", "MARKET_DATA_INTEGRITY"}


def test_stale_or_malformed_trade_intent_is_rejected_by_schema():
    now = datetime.now(timezone.utc)
    with pytest.raises(ValueError, match="stale"):
        TradeIntent(intent_id=uuid4(), symbol="NVDA", side="BUY", strategy_id="momentum", confidence=.9, expected_horizon="SWING", proposed_risk_score=10, signal_timestamp=now - timedelta(minutes=10), expires_at=now - timedelta(minutes=1), reason_codes=["MOMENTUM"])
    with pytest.raises(ValueError, match="reason"):
        TradeIntent(symbol="NVDA", side="BUY", strategy_id="momentum", confidence=.9, expected_horizon="SWING", proposed_risk_score=10, signal_timestamp=now, expires_at=now + timedelta(minutes=5), reason_codes=["not a reason code!"])


def test_secret_redaction_removes_values_before_audit_or_logs():
    redacted = redact({"alpaca_secret_key": "abc", "nested": {"Authorization": "Bearer token-value"}, "normal": "token=visible"})
    assert redacted["alpaca_secret_key"] == "[REDACTED]"
    assert redacted["nested"]["Authorization"] == "[REDACTED]"
    assert "visible" not in redacted["normal"]
    assert "value" not in redact_text("Authorization: Bearer value")
