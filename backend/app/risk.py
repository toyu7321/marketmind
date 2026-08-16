from dataclasses import dataclass
from .schemas import RiskRequest

@dataclass
class RiskLimits:
    max_position_pct: float=10; max_exposure_pct: float=80; max_sector_pct: float=30
    max_daily_loss_pct: float=3; min_liquidity: float=1_000_000; kill_switch: bool=False

def evaluate(req: RiskRequest, limits=RiskLimits()):
    reasons=[]; position=req.proposed_value/req.portfolio_equity*100
    if limits.kill_switch: reasons.append("Trading kill switch is active")
    if position>limits.max_position_pct: reasons.append("Maximum position percentage exceeded")
    if (req.current_exposure+req.proposed_value)/req.portfolio_equity*100>limits.max_exposure_pct: reasons.append("Maximum portfolio exposure exceeded")
    if req.sector_exposure/req.portfolio_equity*100+position>limits.max_sector_pct: reasons.append("Maximum sector exposure exceeded")
    if req.daily_pnl/req.portfolio_equity*100 < -limits.max_daily_loss_pct: reasons.append("Daily loss limit reached")
    if req.liquidity<limits.min_liquidity: reasons.append("Minimum liquidity not met")
    if req.event_risk: reasons.append("Material event risk requires manual review")
    return {"decision":"REJECTED" if reasons else "APPROVED","reasons":reasons or ["All configured controls passed"],"position_percent":round(position,2)}
