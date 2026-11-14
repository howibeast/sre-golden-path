# Runbook: {{alert}}

**Service:** {{service}}
**Owner:** {{owner}}
**SLO:** {{slo}}
**Alert:** {{alert}}

## Summary

What broke, in one sentence, and whether customers can still use the page.

## Impact

Who notices, and whether the number on screen is missing, slow, or wrong. Say if the response is explicitly degraded.

## Alert

Which alert fired, and why this signal rather than a different one. Link the dashboard.

## Mitigate

The steps that stop customer harm before the cause is fixed. Include what not to do (for example, do not raise timeouts on a dependency that is already failing).

## Fix

How to remove the cause. Name the other team if the cause is not in this service.

## Verify

What "recovered" looks like: alert state, one successful customer path, and the dashboard back inside the SLO.

## Escalate

Who to call, and after how long. Include the product owner when the customer-visible behaviour is a product decision.
