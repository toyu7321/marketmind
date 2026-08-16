import math
from fastapi.testclient import TestClient
from app.indicators import sma,ema,rsi,macd,atr,bollinger,rolling_volatility,rate_of_change,support_resistance
from app.scoring import score,MARKET_WEIGHTS,STOCK_WEIGHTS
from app.schemas import RiskRequest,AIAnalysis
from app.risk import evaluate
from app.backtesting import run_backtest
from app.providers import DemoMarketProvider,RulesAIProvider
from app.main import app

def test_indicators_are_deterministic():
    values=list(range(1,101)); assert sma(values,5)[-1]==98; assert round(ema(values,10)[-1],2)==95.5
    assert rsi(values)[-1]==100; assert macd(values)["histogram"][-1]>0
    assert bollinger(values)["upper"][-1]>sma(values,20)[-1]
    assert rolling_volatility(values); assert rate_of_change(values)[-1]>0
    assert support_resistance(values)=={"support":81.,"resistance":100.}
    assert atr([x+2 for x in values],[x-2 for x in values],values)[-1]==4

def test_explainable_scores_and_weight_validation():
    result=score({k:80 for k in MARKET_WEIGHTS},MARKET_WEIGHTS)
    assert result.score==80 and result.label=="Bullish" and len(result.components)==7
    try: score({}, {"bad":90}); assert False
    except ValueError: pass

def test_risk_engine_has_final_authority():
    request=RiskRequest(symbol="NVDA",proposed_value=20_000,portfolio_equity=100_000,current_exposure=70_000,sector_exposure=20_000,liquidity=2_000_000)
    decision=evaluate(request); assert decision["decision"]=="REJECTED"; assert len(decision["reasons"])>=2

def test_backtest_uses_prior_signal_and_calculates_metrics():
    prices=[100+i*.2+math.sin(i/4) for i in range(300)]; scores=[70]*300
    result=run_backtest(prices,scores); assert result["trades"]==1; assert "max_drawdown" in result; assert len(result["equity_curve"])>20

def test_ai_schema_validation():
    payload={"bias":"Neutral","confidence":65,"summary":"x","bull_case":"x","base_case":"x","bear_case":"x","catalysts":[],"risks":[],"invalidation":"x","preferred_action":"WAIT","reasoning_summary":"x"}
    assert AIAnalysis.model_validate(payload).preferred_action=="WAIT"

def test_health_and_demo_pipeline():
    with TestClient(app) as client:
        health=client.get("/api/health"); assert health.status_code==200 and health.json()["status"]=="healthy"
        dashboard=client.get("/api/dashboard"); assert dashboard.status_code==200 and len(dashboard.json()["indices"])==5
        scanner=client.get("/api/scanner"); assert scanner.status_code==200 and len(scanner.json()["results"])>=10
        prediction=client.post("/api/predictions",json={"ticker":"NVDA","horizon":3,"bias":"Bullish","bull_probability":60,"neutral_probability":25,"bear_probability":15,"confidence":70,"starting_price":180,"stock_score":80,"market_score":72}); assert prediction.status_code==201


def test_settings_validation_and_paper_order_confirmation_gate():
    order = {"symbol":"NVDA","quantity":1,"side":"BUY","order_type":"limit","limit_price":100,"estimated_price":100}
    with TestClient(app) as client:
        invalid_weights = client.put("/api/settings", json={"market_weights":{"trend":99}})
        assert invalid_weights.status_code == 422
        preview = client.post("/api/trading/preview", json=order)
        assert preview.status_code == 200 and preview.json()["risk"]["decision"] == "APPROVED"
        submission = client.post("/api/trading/orders", json=order)
        assert submission.status_code == 409
