from datetime import datetime, timezone
from .schemas import ScoreComponent, ScoreResult

MARKET_WEIGHTS={"Trend":25,"Momentum":15,"Breadth":15,"Volatility":15,"Relative Strength":10,"Macro":10,"News":10}
STOCK_WEIGHTS={"Trend":20,"Momentum":15,"Relative Strength":13,"Volume":8,"Volatility":7,"Technical Setup":12,"Market Environment":10,"News":5,"Fundamentals":7,"Risk":3}

def score(values: dict[str,float], weights: dict[str,float], reasons: dict[str,str]|None=None) -> ScoreResult:
    if not weights or abs(sum(weights.values())-100)>.01: raise ValueError("weights must total 100")
    components=[]; total=0
    for name,weight in weights.items():
        normalized=max(0,min(100,float(values.get(name,50)))); contribution=normalized*weight/100; total+=contribution
        components.append(ScoreComponent(name=name,weight=weight,normalized=normalized,contribution=round(contribution,2),reason=(reasons or {}).get(name,"Rules-based normalization")))
    result=round(total); label="Strong Bullish" if result>=85 else "Bullish" if result>=65 else "Neutral" if result>=45 else "Bearish" if result>=25 else "Strong Bearish"
    return ScoreResult(score=result,label=label,confidence=min(92,55+abs(result-50)),components=components,timestamp=datetime.now(timezone.utc))
