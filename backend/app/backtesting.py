"""A small, deterministic next-session backtester for score-based research."""
from __future__ import annotations

import math
from collections import defaultdict
from datetime import date, timedelta
from typing import Iterable

import numpy as np


def _sample(values: np.ndarray, dates: list[str]) -> list[dict[str, float | str]]:
    step = max(1, len(values) // 120)
    indices = list(range(0, len(values), step))
    if indices[-1] != len(values) - 1:
        indices.append(len(values) - 1)
    return [{"date": dates[index], "value": round(float(values[index]), 2)} for index in indices]


def run_backtest(
    prices: Iterable[float],
    scores: Iterable[float],
    threshold: float = 65,
    cost_bps: float = 5,
    dates: list[str] | None = None,
    initial_equity: float = 100_000,
) -> dict:
    """Run a long/cash strategy using a prior-close score for the next session.

    A score at session i determines the position held over i to i+1. The final
    score is deliberately unused, avoiding direct close-to-close look-ahead.
    Costs are charged on each entry and exit turnover.
    """
    p, s = np.asarray(list(prices), dtype=float), np.asarray(list(scores), dtype=float)
    if len(p) < 3 or len(s) != len(p):
        raise ValueError("prices and scores must have equal length of at least three")
    if np.any(p <= 0):
        raise ValueError("prices must be positive")

    session_dates = dates or [(date.today() - timedelta(days=len(p) - index)).isoformat() for index in range(len(p))]
    if len(session_dates) != len(p):
        raise ValueError("dates must match prices")

    positions = (s[:-1] >= threshold).astype(float)
    raw_returns = np.diff(p) / p[:-1]
    turnover = np.abs(np.diff(np.r_[0.0, positions]))
    costs = turnover * cost_bps / 10_000
    strategy_returns = positions * raw_returns - costs
    equity = initial_equity * np.cumprod(1 + strategy_returns)
    benchmark = initial_equity * (p[1:] / p[0])
    peak = np.maximum.accumulate(equity)
    drawdown = equity / peak - 1

    trades, entry_index = [], None
    for index, position in enumerate(positions):
        prior_position = positions[index - 1] if index else 0
        if position and not prior_position:
            entry_index = index
        if prior_position and not position and entry_index is not None:
            exit_index = index
            gross_return = p[exit_index] / p[entry_index] - 1
            net_return = gross_return - 2 * cost_bps / 10_000
            trades.append({
                "entry_date": session_dates[entry_index], "exit_date": session_dates[exit_index],
                "entry_price": round(float(p[entry_index]), 2), "exit_price": round(float(p[exit_index]), 2),
                "return_percent": round(float(net_return * 100), 2), "status": "closed",
            })
            entry_index = None
    if entry_index is not None:
        gross_return = p[-1] / p[entry_index] - 1
        net_return = gross_return - cost_bps / 10_000
        trades.append({
            "entry_date": session_dates[entry_index], "exit_date": session_dates[-1],
            "entry_price": round(float(p[entry_index]), 2), "exit_price": round(float(p[-1]), 2),
            "return_percent": round(float(net_return * 100), 2), "status": "open",
        })

    active = strategy_returns[positions > 0]
    wins, losses = active[active > 0], active[active < 0]
    years = max(len(strategy_returns) / 252, 1 / 252)
    volatility = float(np.std(strategy_returns, ddof=0) * math.sqrt(252))
    downside = float(np.std(np.minimum(strategy_returns, 0), ddof=0) * math.sqrt(252))
    monthly: dict[str, list[float]] = defaultdict(list)
    for value, session_date in zip(strategy_returns, session_dates[1:]):
        monthly[session_date[:7]].append(float(value))

    cagr = float(((equity[-1] / initial_equity) ** (1 / years) - 1) * 100)
    return {
        "total_return": round(float((equity[-1] / initial_equity - 1) * 100), 2),
        "cagr": round(cagr, 2), "annualized_return": round(cagr, 2), "volatility": round(volatility * 100, 2),
        "sharpe": round(float(np.mean(strategy_returns) * 252 / volatility), 2) if volatility else 0,
        "sortino": round(float(np.mean(strategy_returns) * 252 / downside), 2) if downside else 0,
        "max_drawdown": round(float(drawdown.min() * 100), 2),
        "win_rate": round(float(len(wins) / len(active) * 100), 2) if len(active) else 0,
        "profit_factor": round(float(wins.sum() / abs(losses.sum())), 2) if len(losses) and losses.sum() else 0,
        "average_win": round(float(wins.mean() * 100), 2) if len(wins) else 0,
        "average_loss": round(float(losses.mean() * 100), 2) if len(losses) else 0,
        "trades": len(trades), "exposure": round(float(positions.mean() * 100), 2),
        "benchmark_return": round(float((p[-1] / p[0] - 1) * 100), 2),
        "equity_curve": _sample(equity, session_dates[1:]),
        "benchmark_curve": _sample(benchmark, session_dates[1:]),
        "drawdown": _sample(drawdown * 100, session_dates[1:]),
        "trade_history": trades,
        "monthly_returns": [{"month": month, "return_percent": round((np.prod(np.asarray(values) + 1) - 1) * 100, 2)} for month, values in sorted(monthly.items())],
        "assumptions": {"signal_execution": "next session", "transaction_cost_bps": cost_bps, "initial_equity": initial_equity},
    }
