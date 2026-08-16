import math

import pytest
from fastapi.testclient import TestClient

from app.backtesting import run_backtest
from app.config import Settings
from app.indicators import atr, bollinger, ema, macd, rate_of_change, rolling_volatility, rsi, sma, support_resistance
from app.main import app
from app.risk import evaluate
from app.schemas import AIAnalysis, PredictionCreate, RiskRequest
from app.scoring import MARKET_WEIGHTS, score


def test_indicators_are_deterministic():
    values = list(range(1, 101))
    assert sma(values, 5)[-1] == 98
    assert round(ema(values, 10)[-1], 2) == 95.5
    assert rsi(values)[-1] == 100
    assert macd(values)["histogram"][-1] > 0
    assert bollinger(values)["upper"][-1] > sma(values, 20)[-1]
    assert rolling_volatility(values)
    assert rate_of_change(values)[-1] > 0
    assert support_resistance(values) == {"support": 81.0, "resistance": 100.0}
    assert atr([value + 2 for value in values], [value - 2 for value in values], values)[-1] == 4


def test_explainable_scores_and_weight_validation():
    result = score({key: 80 for key in MARKET_WEIGHTS}, MARKET_WEIGHTS)
    assert result.score == 80
    assert result.label == "Bullish"
    assert len(result.components) == len(MARKET_WEIGHTS)
    with pytest.raises(ValueError):
        score({}, {"bad": 90})


def test_risk_engine_has_final_authority():
    request = RiskRequest(
        symbol="NVDA",
        proposed_value=20_000,
        portfolio_equity=100_000,
        current_exposure=70_000,
        sector_exposure=20_000,
        liquidity=2_000_000,
    )
    decision = evaluate(request)
    assert decision["decision"] == "REJECTED"
    assert len(decision["reasons"]) >= 2


def test_backtest_uses_prior_signal_and_calculates_metrics():
    prices = [100 + index * 0.2 + math.sin(index / 4) for index in range(300)]
    result = run_backtest(prices, [70] * len(prices))
    assert result["trades"] == 1
    assert "max_drawdown" in result
    assert len(result["equity_curve"]) > 20


def test_ai_schema_and_prediction_validation_are_strict():
    payload = {
        "bias": "Neutral", "confidence": 65, "summary": "x", "bull_case": "x", "base_case": "x",
        "bear_case": "x", "catalysts": [], "risks": [], "invalidation": "x", "preferred_action": "WAIT",
        "reasoning_summary": "x",
    }
    assert AIAnalysis.model_validate(payload).preferred_action == "WAIT"
    with pytest.raises(ValueError):
        PredictionCreate(
            ticker="NVDA", horizon=3, bias="Bullish", bull_probability=60, neutral_probability=25,
            bear_probability=10, confidence=70, starting_price=180, stock_score=80, market_score=72,
        )


def test_health_is_public_but_market_data_fails_closed_without_auth_configuration():
    with TestClient(app) as client:
        health = client.get("/api/health")
        assert health.status_code == 200
        assert health.json()["authentication"] == "not_configured"
        assert client.get("/api/dashboard").status_code == 503
        assert client.get("/api/settings").status_code == 503


def test_production_configuration_requires_https_supabase_and_audit_secret(monkeypatch):
    monkeypatch.setenv("MARKETMIND_ENV", "production")
    monkeypatch.setenv("CORS_ORIGINS", "https://marketmind.example")
    monkeypatch.setenv("FRONTEND_ORIGIN", "https://marketmind.example")
    monkeypatch.setenv("SUPABASE_URL", "https://project.supabase.co")
    monkeypatch.setenv("SUPABASE_PUBLISHABLE_KEY", "sb_publishable_example")
    monkeypatch.setenv("SUPABASE_SECRET_KEY", "sb_secret_example")
    monkeypatch.setenv("AUDIT_IP_HMAC_SECRET", "test-only-secret")
    production = Settings(_env_file=None)
    assert production.auth_ready
    assert production.cors_origin_list == ["https://marketmind.example"]
    assert not production.paper_order_submission_enabled
    monkeypatch.setenv("ENABLE_LIVE_TRADING", "true")
    with pytest.raises(ValueError, match="ENABLE_LIVE_TRADING"):
        Settings(_env_file=None)


def test_anonymous_authentication_modes_are_not_available(monkeypatch):
    monkeypatch.setenv("AUTH_MODE", "disabled")
    with pytest.raises(ValueError, match="AUTH_MODE"):
        Settings(_env_file=None)
