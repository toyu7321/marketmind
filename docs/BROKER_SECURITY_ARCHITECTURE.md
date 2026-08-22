# Broker execution security architecture

## Current state

MarketMind has **no activated broker execution path**. `ENABLE_LIVE_TRADING=false` and `ENABLE_REMOTE_PAPER_ORDERS=false` remain required. The order endpoint is retained only as a locked, audited compatibility boundary; it cannot submit an order to Alpaca.

## Required future topology

```text
AI / Strategy Engine (untrusted signal)
        |
        |  bounded TradeIntent only
        v
Intent validator + replay ledger
        v
Deterministic Risk Engine
        v
Execution Guard (live gate / kill switches / reconciliation)
        v
Isolated Broker Execution Service
        v
Broker
```

The strategy process must not import a broker SDK, read a broker secret, or invoke an execution URL. Broker credentials must exist only in a separately deployed executor identity with least privilege and an encrypted host-managed secret reference. The API/frontend/database never hold plaintext broker access or refresh tokens.

## Structured trade intent

`TradeIntent` is a strict Pydantic contract with a UUID, symbol, side, bounded asset type, strategy ID, `0..1` confidence, bounded horizon/risk score, signal timestamp, expiry (maximum 30 minutes after signal), and bounded reason-code identifiers. It deliberately has no free-form command, broker endpoint, account, notional, leverage, or credential field.

Before any future execution, the executor must independently obtain canonical broker portfolio state and market data. It must not trust portfolio exposure, cash, price, spread, or liquidity supplied by a browser or AI payload.

## Replay and idempotency

- A client supplies a high-entropy idempotency key; the database enforces uniqueness per user.
- A durable `trade_intents` ledger has a unique intent UUID, normalized payload digest, expiry, status, and deterministic `broker_client_order_id`.
- Reuse of an intent ID is rejected, including after a restart. Reuse of an idempotency key with different payload is rejected.
- The future broker call uses `broker_client_order_id` and treats timeout/ambiguous acknowledgement as unknown state, followed by reconciliation—not retry-as-new-order.
- Intent expiry, invalid signal timestamp, mismatched symbol/side, unknown asset, or missing intent are rejected.

## Reconciliation and fail-closed behavior

Before submitting an order, and after an ambiguous result or broker callback, the executor must reconcile:

1. broker positions against the local canonical portfolio snapshot;
2. open orders/client IDs against the intent ledger;
3. buying power, live-capital ceiling, and leverage;
4. fills, cancels, rejects, and unknown external positions.

Any mismatch, unknown position/order, stale broker snapshot, failed reconciliation, duplicate anomaly, or impossible balance immediately pauses trading and activates a durable execution hold. Recovery requires a named AAL2 administrator, a recorded reason, fresh reconciliation, and external investigation where appropriate. No worker may self-clear this hold.

## Future live enablement gate

`ENABLE_LIVE_TRADING=true` must never by itself activate anything. A separately reviewed release must require all of:

- production server flag and isolated executor allowlist;
- AAL2 administrator-approved security readiness state;
- valid, versioned risk policy and clear global/user/strategy circuit breakers;
- verified broker connection and a fresh reconciled account snapshot;
- explicit non-zero `live_capital_limit`, independent of broker account balance;
- approved data-health and market-status checks;
- durable idempotency/reconciliation stores and distributed rate limits;
- completed incident drill and independent security review.

The executor must allocate only within `live_capital_limit`; a $50,000 brokerage balance with a $5,000 live ceiling behaves as a $5,000 autonomous allocation pool. Do not build an override for this ceiling.
