# Runbook: Ledger confirmation failing

**Service:** account-balance
**Owner:** accounts-team
**SLO:** 99.9% of balance reads succeed in under 200ms, rolling 30 days
**Alert:** AccountBalanceReadLedgerConfirmErrorRateHigh

## Summary

The ledger is failing or timing out on a share of confirm calls (drill: 5–20% for several minutes). Balance reads keep returning 200 with the settled balance and `warning: pending_not_applied`. Pending holds are not subtracted while this is true.

## Impact

The balance still loads, which is the point of the mitigation. The available figure can be higher than it should be, because unconfirmed holds are left off. The body says `degraded: true`. The app must show that warning. A customer who only sees the number, with no warning, is being shown a figure we could not confirm.

If `AccountBalanceReadFastBurn` is also firing, the mitigation is not holding: reads are failing or slower than 200ms. Treat that as the more serious page.

## Alert

Page on `AccountBalanceReadLedgerConfirmErrorRateHigh`. It measures ledger outcomes (`error`, `timeout`, and `circuit_open`) over 2 minutes, above 5% of calls.

This is the cause. The balance SLO is the customer symptom. While the degraded response stays fast, the SLO stays green and would not page you. `circuit_open` is included so the alert does not clear just because the breaker stopped calling the ledger.

`AccountBalanceReadLedgerConfirmAuthRejected` is a different fault: the token is wrong. Do not treat it as an outage.

Dashboard: Grafana → Golden path → Account balance. Traces: Jaeger, service `account-balance`, tag `balance.degraded=true`.

## Mitigate

1. Confirm the firing alert is the ledger one. If the fast-burn SLO alert is also firing, roll back the last balance-service change. A fast degraded 200 is better than a failed read.
2. Leave the breaker alone. Do not raise `LEDGER_TIMEOUT_SECONDS` during the incident. A longer timeout spends the 200ms budget on a ledger that is already sick, and it adds load.
3. Do not add retries on this path.
4. Tell the channel that balances may omit pending holds, using the product owner's wording. Do not quote internal error rates to customers.
5. If a single client is abusive, limit that client at the edge. Do not put a global cap under 1500 requests/s while a Sunday event is running. That cap would fail real customers. The production steady rate is about 150 requests/s and the event peak is about 1500.

Local drill only: `GET http://localhost:8081/control` shows the injected mode.

## Fix

1. If the auth alert is the one firing, rotate the ledger token with the ledger owner. A rejected credential is not fixed by scaling this service.
2. If this is the local double, set the mode back to `ok` (`python scripts/demo_fault.py --reset`) and confirm the ratio falls.
3. If this is production, the ledger owner fixes the ledger. This service waits. It does not reconcile pending rows on the read path.
4. Keep the incident open until the error ratio has stayed under 5% and the alert has cleared. Then read one account that has a pending transaction and check `degraded` is false.
5. Record how long we served degraded balances. Under the assumption in DECISIONS.md, that time does not spend the error budget, because the read met the SLI and said it was degraded. Confirm that with the product owner the first time it happens for real. If they disagree, the SLI has to change: a degraded read would count as a bad event.

## Verify

- `GET /accounts/acc_1001/balance` returns `degraded: false` and an available balance 50.00 below settled (the seeded hold).
- Ledger error ratio on the dashboard is back near zero.
- `AccountBalanceReadFastBurn` is not firing.

## Escalate

- Ledger on-call, immediately, when the fault is not a drill.
- Accounts product owner if degraded mode lasts longer than 15 minutes. The number on screen is then missing holds for long enough that the warning is not a short blip.
- Incident commander if the fast-burn alert fires as well, or if you cannot tell whether customers are seeing the warning.
