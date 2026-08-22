# Risk readiness: authoritative remediation v2

## Current status

**Implemented and tested:** the only order-risk path builds an immutable
`CanonicalRiskContext` on the server. Browser/AI payload values for price,
asset type, equity, buying power, exposures, P&L, liquidity, spread, provider
health, and market status are ignored. It resolves server-side instrument
metadata, uses bid/ask-conservative pricing, includes option multipliers and
underlying concentration, aggregates existing positions plus durable open
outbox records, and applies post-trade symbol/underlying/sector/theme/gross and
strategy caps. Unknown metadata, stale/degraded data, unregistered strategies,
unknown broker reconciliation, and any missing canonical fact are **NO TRADE**.

Loss/drawdown triggers create durable `trading_holds` with a monotonically
increasing control generation. Validation, executor claim, and the future
dispatch boundary must all compare that generation; a queued intent cannot
survive a new kill switch or reconciliation hold.

**Deployment-dependent:** live canonical broker balances, positions, fills,
and buying power must be supplied by the separately deployed executor's
reconciliation integration. The current application ledger is the trusted
source for this preview-only release.

**Planned / blocked:** no paper or live broker submission, automated recovery,
or autonomous trading is authorized. Keep `ENABLE_REMOTE_PAPER_ORDERS=false`
and `ENABLE_LIVE_TRADING=false` until a fresh independent adversarial re-audit.

The risk engine is deterministic, has no broker dependency, and is the final authority over a structured signal. It does not enable paper or live execution.

## Versioned policy and defaults

Policy version: `2026-08-security-freeze-v1`. The server has conservative defaults and immutable code-level ceilings. An AAL2 administrator may configure a stricter or bounded policy through the admin security route; a user may only tighten their own effective limits. Values are never accepted from AI output.

| Control | Default | Immutable maximum / minimum |
| --- | ---: | ---: |
| Single stock / ETF / leveraged ETF / option allocation | 8% / 15% / 5% / 2% | 15% / 30% / 10% / 5% |
| Sector / correlated theme / total gross exposure | 25% / 30% / 60% | 35% / 40% / 90% |
| Leverage | 1.0x | 1.0x |
| Order notional / positions | $10,000 / 15 | $50,000 / 50 |
| Daily / weekly loss / portfolio drawdown | 2% / 4% / 10% | 5% / 10% / 20% |
| Per-trade account risk | 0.5% | 2% |
| Minimum liquidity / max spread / data age | $1,000,000 / 0.50% / 120s | $500,000 floor / 2% / 300s |
| Strategy allocation / daily loss / drawdown | 20% / 1% / 5% | 35% / 3% / 10% |
| Moderate / severe drawdown scaling | 5% / 8% | 10% / 15% |

`live_capital_limit` is intentionally `0` until a reviewed future release sets an explicit bounded pool. Its immutable maximum is $50,000 for this code line. These ceilings are an immutable safety floor, not a business-performance setting.

## Dynamic sizing

The maximum proposed notional is the minimum of the dynamic allocation, stop-risk budget, order cap, remaining strategy allocation, and verified buying power:

```text
base asset cap
× conviction tier factor (0.55 / 0.70 / 0.85 / 1.00)
× bounded confidence factor
× volatility factor (target ATR% / actual ATR%, clamped)
× liquidity factor
× market-status factor
× sector/theme concentration headroom
× drawdown factor
```

The account-risk component is:

```text
risk_budget = equity × max_risk_per_trade_pct
stop_risk_notional = risk_budget / stop_distance_pct
```

Confidence is clamped and can never expand a position beyond an asset cap or any other hard limit. High ATR shrinks size; insufficient liquidity, closed/unknown market, severe drawdown, or exhausted concentration headroom produces no allowed new allocation.

## No-trade / circuit-breaker conditions

The evaluator rejects a proposal when the global/user kill switch is active; data is demo/stale/unavailable/over-age/degraded/conflicting; market status is uncertain; spread or liquidity fails; an event-risk flag exists; an asset/order/exposure/sector/theme/leverage/position cap fails; daily/weekly/drawdown breakers trip; or a strategy budget fails. It returns reasons plus sizing factors for auditability.

Severe drawdown sends the dynamic factor to zero; configured maximum drawdown is a hard circuit breaker. Future orchestration must persist the corresponding execution hold, audit it, notify an administrator, and require explicit AAL2 recovery after reconciliation. The current deployment cannot execute, so it cannot auto-flatten positions.

## Known limitations

- Preview inputs are user-supplied simulations; future execution must source all account, broker, spread, and price state server-side from reconciled providers.
- Correlated-theme exposure currently uses an authoritative supplied classification/value. A production autonomous system needs a governed symbol-to-sector/theme/correlation data service.
- The current rate limiter and policy cache are process-local. Distributed execution requires Redis/transactional coordination.
- No live trade, paper remote trade, broker reconciliation worker, notification channel, or auto-flatten logic is active.
