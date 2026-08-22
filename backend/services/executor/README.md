# MarketMind executor boundary

This package is a separately deployable, **non-operational** execution worker.
It is intentionally not imported by `app.main`, the API, AI, or strategy code.

The FastAPI application may persist an immutable, canonical intent/outbox record.
An executor worker must receive a signed envelope over a private authenticated
channel, verify expiry/hash/nonce, claim the outbox transactionally, re-check
all durable holds and canonical account/market/instrument facts, reconcile by
`client_order_id`, and only then use an executor-runtime broker credential.

## Deployment identity

- API runtime: database access, market-data credentials, `EXECUTOR_INTENT_SIGNING_KEY` only.
- Executor runtime: private database/network access, `EXECUTOR_INTENT_SIGNING_KEY`, and future
  `EXECUTOR_BROKER_API_KEY` / `EXECUTOR_BROKER_SECRET_KEY` only.
- Browser/Vercel: none of the above.

The current `ExecutorService` always raises `ExecutionDisabled`; no broker SDK
or order endpoint is implemented here. Before any activation, deploy through a
private network with mTLS/service identity and complete a fresh independent
adversarial audit.
