# MarketMind security incident response

This runbook prioritizes protecting customer accounts and preserving evidence. Do not attempt to diagnose an incident by enabling trading or weakening authentication.

## Durable hold and reconciliation response

For market-data integrity, loss/drawdown, broker timeout, duplicate-intent, or
position/order mismatch events, leave the generated `trading_holds` and
outbox/intent records intact. The hold generation invalidates queued work.
Capture the deterministic client-order ID, mark the outcome `UNKNOWN` or
`RECONCILING`, and query the broker before any future retry. Do not delete or
re-submit an ambiguous intent. A named AAL2 administrator must document a
release only after fresh reconciliation; the current release has no automated
hold release or dispatch worker.

## First 15 minutes: contain and preserve

1. Have an AAL2 administrator activate the global kill switch. Keep `ENABLE_REMOTE_PAPER_ORDERS=false` and `ENABLE_LIVE_TRADING=false`.
2. Disable the affected broker connection/executor deployment at the host and broker, not merely in the UI.
3. Preserve audit events, Render/Vercel/Supabase/GitHub logs, broker order/fill exports, request IDs, and relevant timestamps in an access-controlled incident folder. Do not copy secrets into tickets.
4. Identify incident commander, scope, suspected identities/secrets, and a timeline. Notify affected users through an approved channel when required.
5. Do not clear a kill switch or delete evidence until reconciliation and a documented recovery decision.

## Scenario playbooks

### Leaked broker, provider, database, Supabase, or bootstrap secret

- Revoke/rotate the credential at the owning provider, then remove it from Render/Vercel/GitHub and redeploy.
- Search logs, audit records, build artifacts, URLs, browser storage, and Git history for exposure; treat history exposure as permanent until rotated.
- Reconcile broker positions/orders and provider activity from the earliest possible exposure time.
- If the bootstrap secret was exposed, disable bootstrap, delete the variable, redeploy, and review all administrator mappings.

### Unauthorized or suspicious order

- Trigger kill switch and broker-side trading pause immediately.
- Reconcile open orders, fills, client order IDs, buying power, positions, and intent/idempotency ledger.
- Preserve broker confirmations and do not retry ambiguous requests. Investigate whether an intent/replay/authorization/risk policy failed.
- Escalate to the broker’s fraud/security channel and obtain legal/compliance guidance before corrective trading.

### Account/session or administrator compromise

- Disable/deactivate the affected local user where appropriate and revoke all sessions from the Admin Console/Supabase.
- For an administrator compromise, review role changes, invitations, system-security policy, kill-switch history, deployment configuration, and GitHub/Vercel/Render access. Rotate credentials reachable by that role.
- Require password/MFA reset and re-enrollment under the identity provider’s recovery process. Do not self-approve recovery with the compromised account.

### Database/backend/provider compromise or market-data corruption

- Pause trading, isolate the affected service, rotate its access secrets, and restore only from a verified backup when required.
- Compare provider snapshots with an independent source and broker state. Mark market data degraded/unavailable; never substitute synthetic values for execution.
- Validate audit continuity and data integrity. Treat a missing or altered intent/reconciliation record as a fail-closed event.

### Suspicious strategy / AI behavior

- Disable the strategy and preserve its model version, input evidence, typed intent(s), and rejection/decision reasons.
- Review prompt sources/news content for injection and check malformed-output rates. AI text is evidence only; it has no remediation authority.
- Re-enable only after deterministic test reproduction, approved policy review, and administrator sign-off.

## Recovery and post-incident

Recovery requires documented owner approval, fresh provider/broker reconciliation, valid authentication configuration, clean policy/kill-switch state, rotated credentials, and affected-user notification as appropriate. For any future executor, a separate AAL2 administrator must clear an execution hold only after these checks.

Within five business days, produce a blameless post-incident report with timeline, impact, root cause, corrective actions, retained evidence location, secret rotation confirmation, test gaps, and policy/documentation changes. An independent human security review is required before enabling a new execution tier.
