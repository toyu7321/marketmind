from datetime import datetime
from typing import Any, Literal
from pydantic import BaseModel, Field, model_validator


class Quote(BaseModel):
    symbol: str; company: str; price: float; change: float; change_percent: float
    score: int; trend: str; sparkline: list[float]; freshness: str = "DEMO"


class ScoreComponent(BaseModel):
    name: str; weight: float; normalized: float = Field(ge=0, le=100); contribution: float; reason: str


class ScoreResult(BaseModel):
    score: int = Field(ge=0, le=100); label: str; confidence: int; components: list[ScoreComponent]; timestamp: datetime


class AIAnalysis(BaseModel):
    bias: str; confidence: int = Field(ge=0, le=100); summary: str
    bull_case: str; base_case: str; bear_case: str
    catalysts: list[str]; risks: list[str]; invalidation: str
    preferred_action: Literal["WATCH", "WAIT", "NO TRADE", "BULLISH SETUP", "BEARISH SETUP", "HIGH RISK"]
    reasoning_summary: str


class RiskRequest(BaseModel):
    symbol: str; proposed_value: float = Field(gt=0); portfolio_equity: float = Field(gt=0)
    current_exposure: float = Field(ge=0); sector_exposure: float = Field(ge=0)
    daily_pnl: float = 0; liquidity: float = Field(ge=0); event_risk: bool = False


class PredictionCreate(BaseModel):
    ticker: str; horizon: Literal[1, 3, 5]; bias: str
    bull_probability: float; neutral_probability: float; bear_probability: float
    confidence: float; starting_price: float; stock_score: int; market_score: int

    @model_validator(mode="after")
    def probabilities_total(self):
        if abs(self.bull_probability + self.neutral_probability + self.bear_probability - 100) > .01:
            raise ValueError("probabilities must total 100")
        return self


class BacktestRequest(BaseModel):
    ticker: str = "SPY"; days: int = Field(default=252, ge=30, le=2000)
    score_threshold: int = Field(default=65, ge=0, le=100); transaction_cost_bps: float = Field(default=5, ge=0)


class SettingsUpdate(BaseModel):
    timezone: str | None = Field(default=None, max_length=80)
    refresh_seconds: int | None = Field(default=None, ge=15, le=3600)
    watchlist: list[str] | None = None
    market_weights: dict[str, float] | None = None
    stock_weights: dict[str, float] | None = None
    risk: dict[str, float | bool] | None = None
    automation: dict[str, bool] | None = None
    ai: dict[str, str | int | bool] | None = None

    @model_validator(mode="after")
    def validate_weights(self):
        for name, weights in (("market", self.market_weights), ("stock", self.stock_weights)):
            if weights and abs(sum(weights.values()) - 100) > 0.01:
                raise ValueError(f"{name} weights must total 100")
        if self.watchlist:
            self.watchlist = [symbol.upper() for symbol in self.watchlist if symbol.isalnum()][:50]
        return self


class PaperOrderRequest(BaseModel):
    symbol: str = Field(min_length=1, max_length=8, pattern=r"^[A-Za-z0-9]+$")
    quantity: float = Field(gt=0)
    side: Literal["BUY", "SELL"]
    order_type: Literal["market", "limit"] = "limit"
    time_in_force: Literal["day", "gtc"] = "day"
    limit_price: float | None = Field(default=None, gt=0)
    estimated_price: float = Field(gt=0)
    current_exposure: float = Field(default=0, ge=0)
    sector_exposure: float = Field(default=0, ge=0)
    daily_pnl: float = 0
    liquidity: float = Field(default=10_000_000, ge=0)
    event_risk: bool = False
    confirmed: bool = False

    @model_validator(mode="after")
    def limit_order_has_price(self):
        if self.order_type == "limit" and self.limit_price is None:
            raise ValueError("limit_price is required for a limit order")
        return self
