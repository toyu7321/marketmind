from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, EmailStr, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Quote(StrictModel):
    symbol: str
    company: str
    price: float
    change: float
    change_percent: float
    score: int
    trend: str
    sparkline: list[float]
    freshness: str = "DEMO"


class ScoreComponent(StrictModel):
    name: str
    weight: float
    normalized: float = Field(ge=0, le=100)
    contribution: float
    reason: str


class ScoreResult(StrictModel):
    score: int = Field(ge=0, le=100)
    label: str
    confidence: int
    components: list[ScoreComponent]
    timestamp: datetime


class AIAnalysis(StrictModel):
    bias: str
    confidence: int = Field(ge=0, le=100)
    summary: str
    bull_case: str
    base_case: str
    bear_case: str
    catalysts: list[str]
    risks: list[str]
    invalidation: str
    preferred_action: Literal["WATCH", "WAIT", "NO TRADE", "BULLISH SETUP", "BEARISH SETUP", "HIGH RISK"]
    reasoning_summary: str


class RiskRequest(StrictModel):
    symbol: str = Field(min_length=1, max_length=8, pattern=r"^[A-Za-z0-9]+$")
    proposed_value: float = Field(gt=0, le=10_000_000)
    portfolio_equity: float = Field(gt=0, le=100_000_000)
    current_exposure: float = Field(ge=0, le=100_000_000)
    sector_exposure: float = Field(ge=0, le=100_000_000)
    daily_pnl: float = Field(default=0, ge=-100_000_000, le=100_000_000)
    liquidity: float = Field(ge=0, le=10_000_000_000)
    event_risk: bool = False
    asset_type: Literal["STOCK", "ETF", "LEVERAGED_ETF", "OPTION"] = "STOCK"
    side: Literal["BUY", "SELL"] = "BUY"
    strategy_id: str = Field(default="manual-preview", min_length=3, max_length=80, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
    confidence: float = Field(default=0.5, ge=0, le=1)
    calibrated_confidence: float = Field(default=0.5, ge=0, le=1)
    conviction_tier: Literal["NORMAL", "STRONG", "HIGH_CONVICTION", "EXCEPTIONAL"] = "NORMAL"
    atr_percent: float = Field(default=5, gt=0, le=100)
    stop_distance_pct: float = Field(default=5, gt=0, le=100)
    bid_ask_spread_pct: float = Field(default=0, ge=0, le=100)
    data_freshness: Literal["LIVE", "IEX", "DELAYED", "DEMO", "STALE", "UNAVAILABLE"] = "LIVE"
    data_age_seconds: int = Field(default=0, ge=0, le=86_400)
    market_status: Literal["OPEN", "CLOSED", "UNKNOWN"] = "OPEN"
    provider_healthy: bool = True
    data_conflict: bool = False
    theme_exposure: float = Field(default=0, ge=0, le=100_000_000)
    gross_exposure: float = Field(default=0, ge=0, le=100_000_000)
    leverage: float = Field(default=1, ge=0, le=20)
    open_positions: int = Field(default=0, ge=0, le=10_000)
    weekly_pnl: float = Field(default=0, ge=-100_000_000, le=100_000_000)
    peak_equity: float | None = Field(default=None, gt=0, le=100_000_000)
    strategy_exposure: float = Field(default=0, ge=0, le=100_000_000)
    strategy_daily_pnl: float = Field(default=0, ge=-100_000_000, le=100_000_000)
    strategy_drawdown_pct: float = Field(default=0, ge=0, le=100)
    buying_power: float | None = Field(default=None, ge=0, le=100_000_000)


class TradeIntent(StrictModel):
    """A bounded signal contract, never a broker instruction or an AI command."""

    intent_id: UUID = Field(default_factory=uuid4)
    symbol: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9._-]+$")
    side: Literal["BUY", "SELL"]
    # Compatibility hint only. Authoritative classification always comes from
    # server-side InstrumentMetadata and this value is never used by execution
    # risk controls.
    asset_type: Literal["STOCK", "ETF", "LEVERAGED_ETF", "OPTION"] = "STOCK"
    strategy_id: str = Field(min_length=3, max_length=80, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
    confidence: float = Field(ge=0, le=1)
    expected_horizon: Literal["INTRADAY", "SWING", "POSITION"]
    proposed_risk_score: float = Field(ge=0, le=100)
    signal_timestamp: datetime
    expires_at: datetime
    reason_codes: list[str] = Field(min_length=1, max_length=12)

    @model_validator(mode="after")
    def bounded_and_fresh(self):
        now = datetime.now(timezone.utc)
        signal_time = self.signal_timestamp if self.signal_timestamp.tzinfo else self.signal_timestamp.replace(tzinfo=timezone.utc)
        expiry = self.expires_at if self.expires_at.tzinfo else self.expires_at.replace(tzinfo=timezone.utc)
        if expiry <= signal_time or expiry > signal_time + timedelta(minutes=30):
            raise ValueError("trade intent expiry must be after the signal and within 30 minutes")
        if expiry <= now or signal_time > now + timedelta(minutes=1):
            raise ValueError("trade intent is stale or has an invalid timestamp")
        if any(not code or len(code) > 64 or not code.replace("_", "").replace("-", "").isalnum() for code in self.reason_codes):
            raise ValueError("trade intent reason codes must be bounded identifiers")
        return self


class PredictionCreate(StrictModel):
    ticker: str = Field(min_length=1, max_length=8, pattern=r"^[A-Za-z0-9]+$")
    horizon: Literal[1, 3, 5]
    bias: Literal["Bullish", "Neutral", "Bearish"]
    bull_probability: float = Field(ge=0, le=100)
    neutral_probability: float = Field(ge=0, le=100)
    bear_probability: float = Field(ge=0, le=100)
    confidence: float = Field(ge=0, le=100)
    starting_price: float = Field(gt=0, le=10_000_000)
    stock_score: int = Field(ge=0, le=100)
    market_score: int = Field(ge=0, le=100)

    @model_validator(mode="after")
    def probabilities_total(self):
        if abs(self.bull_probability + self.neutral_probability + self.bear_probability - 100) > .01:
            raise ValueError("probabilities must total 100")
        return self


class BacktestRequest(StrictModel):
    ticker: str = Field(default="SPY", min_length=1, max_length=8, pattern=r"^[A-Za-z0-9]+$")
    days: int = Field(default=252, ge=30, le=2000)
    score_threshold: int = Field(default=65, ge=0, le=100)
    transaction_cost_bps: float = Field(default=5, ge=0, le=1000)


class PortfolioPositionResponse(StrictModel):
    symbol: str = Field(min_length=1, max_length=12)
    quantity: float
    average_cost: float
    price: float
    market_value: float
    unrealized_pl: float
    daily_pl: float
    weight: float = Field(ge=0, le=100)
    sector: str


class PortfolioAllocationResponse(StrictModel):
    symbol: str = Field(min_length=1, max_length=12)
    sector: str
    market_value: float
    weight: float = Field(ge=0, le=100)


class PortfolioResponse(StrictModel):
    """Stable per-user portfolio payload, including the valid zero-holdings state."""

    mode: Literal["USER"]
    source: str
    equity: float
    total_value: float
    cash: float
    buying_power: float
    exposure: float = Field(ge=0, le=100)
    day_change: float
    positions: list[PortfolioPositionResponse] = Field(default_factory=list)
    allocation: list[PortfolioAllocationResponse] = Field(default_factory=list)
    orders: list[dict[str, object]] = Field(default_factory=list)
    equity_curve: list[float] = Field(default_factory=list)


class RiskSettings(StrictModel):
    max_position_pct: float | None = Field(default=None, ge=0.1, le=100)
    max_exposure_pct: float | None = Field(default=None, ge=0.1, le=100)
    max_sector_pct: float | None = Field(default=None, ge=0.1, le=100)
    max_daily_loss_pct: float | None = Field(default=None, ge=0.1, le=100)
    min_liquidity: float | None = Field(default=None, ge=0, le=10_000_000_000)
    max_stock_pct: float | None = Field(default=None, ge=0.1, le=100)
    max_etf_pct: float | None = Field(default=None, ge=0.1, le=100)
    max_leveraged_etf_pct: float | None = Field(default=None, ge=0.1, le=100)
    max_options_pct: float | None = Field(default=None, ge=0.1, le=100)
    max_theme_pct: float | None = Field(default=None, ge=0.1, le=100)
    max_leverage: float | None = Field(default=None, ge=0.1, le=20)
    max_order_notional: float | None = Field(default=None, ge=1, le=10_000_000)
    max_open_positions: int | None = Field(default=None, ge=1, le=10_000)
    max_weekly_loss_pct: float | None = Field(default=None, ge=0.1, le=100)
    max_drawdown_pct: float | None = Field(default=None, ge=0.1, le=100)
    max_risk_per_trade_pct: float | None = Field(default=None, ge=0.01, le=100)
    max_bid_ask_spread_pct: float | None = Field(default=None, ge=0.01, le=100)
    max_data_age_seconds: int | None = Field(default=None, ge=1, le=86_400)
    strategy_max_allocation_pct: float | None = Field(default=None, ge=0.1, le=100)
    strategy_daily_loss_pct: float | None = Field(default=None, ge=0.1, le=100)
    strategy_drawdown_pct: float | None = Field(default=None, ge=0.1, le=100)
    moderate_drawdown_pct: float | None = Field(default=None, ge=0.1, le=100)
    severe_drawdown_pct: float | None = Field(default=None, ge=0.1, le=100)
    live_capital_limit: float | None = Field(default=None, ge=0, le=10_000_000)
    kill_switch: bool | None = None


class AutomationSettings(StrictModel):
    premarket: bool | None = None
    session: bool | None = None
    postmarket: bool | None = None


class AISettings(StrictModel):
    model: str | None = Field(default=None, max_length=120)
    mode: Literal["Manual", "Top 5 only", "Top 10 only", "Watchlist only", "Automatic"] | None = None


class SettingsUpdate(StrictModel):
    timezone: str | None = Field(default=None, max_length=80)
    refresh_seconds: int | None = Field(default=None, ge=15, le=3600)
    watchlist: list[str] | None = Field(default=None, max_length=50)
    market_weights: dict[str, float] | None = None
    stock_weights: dict[str, float] | None = None
    risk: RiskSettings | None = None
    automation: AutomationSettings | None = None
    ai: AISettings | None = None

    @model_validator(mode="after")
    def validate_settings(self):
        for name, weights in (("market", self.market_weights), ("stock", self.stock_weights)):
            if weights and (any(value < 0 or value > 100 for value in weights.values()) or abs(sum(weights.values()) - 100) > 0.01):
                raise ValueError(f"{name} weights must be percentages totaling 100")
        if self.watchlist is not None:
            normalized = [symbol.upper() for symbol in self.watchlist]
            if any(not symbol.isalnum() or len(symbol) > 8 for symbol in normalized):
                raise ValueError("watchlist symbols must be alphanumeric tickers")
            self.watchlist = list(dict.fromkeys(normalized))
        return self


class PaperOrderRequest(StrictModel):
    symbol: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9._-]+$")
    quantity: float = Field(gt=0, le=10_000_000)
    side: Literal["BUY", "SELL"]
    order_type: Literal["market", "limit"] = "limit"
    time_in_force: Literal["day", "gtc"] = "day"
    limit_price: float | None = Field(default=None, gt=0, le=10_000_000)
    # Legacy display/simulation fields. They remain parseable for the current
    # client release but are deliberately ignored by canonical risk decisions.
    estimated_price: float | None = Field(default=None, gt=0, le=10_000_000)
    current_exposure: float | None = Field(default=None, ge=0, le=100_000_000)
    sector_exposure: float | None = Field(default=None, ge=0, le=100_000_000)
    daily_pnl: float | None = Field(default=None, ge=-100_000_000, le=100_000_000)
    liquidity: float | None = Field(default=None, ge=0, le=10_000_000_000)
    event_risk: bool | None = None
    confirmed: bool = False
    intent: TradeIntent | None = None

    @model_validator(mode="after")
    def limit_order_has_price(self):
        if self.order_type == "limit" and self.limit_price is None:
            raise ValueError("limit_price is required for a limit order")
        return self


class InviteUserRequest(StrictModel):
    email: EmailStr
    display_name: str = Field(default="", max_length=120)
    role: Literal["ADMIN", "USER"] = "USER"


class BootstrapAdminRequest(StrictModel):
    """The temporary bootstrap contract intentionally contains no role or recovery switch."""

    auth_subject: UUID
    email: EmailStr
    display_name: str = Field(default="", max_length=120)


class UserAdminUpdate(StrictModel):
    role: Literal["ADMIN", "USER"] | None = None
    is_active: bool | None = None

    @model_validator(mode="after")
    def update_not_empty(self):
        if self.role is None and self.is_active is None:
            raise ValueError("at least one user attribute must be supplied")
        return self


class GlobalSecurityUpdate(StrictModel):
    global_kill_switch: bool | None = None
    risk_ceiling: RiskSettings | None = None
    risk_policy: RiskSettings | None = None


class SessionRevokeRequest(StrictModel):
    session_id: str | None = Field(default=None, max_length=128)
    all_other_sessions: bool = False

    @model_validator(mode="after")
    def revoke_target(self):
        if not self.session_id and not self.all_other_sessions:
            raise ValueError("choose a session or all_other_sessions")
        return self
