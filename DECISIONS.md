# Decisions

## What I decided, and what I rejected

The balance read stays up when the ledger does not. If the ledger errors, times out, or the breaker is open, `GET /accounts/{id}/balance` returns 200 with the settled balance, `degraded: true`, and `warning: pending_not_applied`. Pending holds are not subtracted. I rejected the obvious version, which is to pass the ledger's 5xx through to the caller. At a 0.1% budget, a ledger that fails 10% of calls would spend the month's budget in well under an hour, on a dependency the customer cannot see. I also rejected hiding that fact. A fast wrong number with no warning is worse than a slow page. The warning is part of the mitigation, and the runbook says the app has to show it.

I rejected retries on this path. The budget is 200ms. A retry spends it twice and adds traffic to a ledger that is already failing. The client gets one attempt and an 80ms timeout, which leaves room for Postgres and the response. The local compose file sets that 80ms on purpose.

I rejected a cache. The ticket does not say whether this is a few hot accounts polled by every page, or a distinct account per customer. A cache is a large win in the first case and almost useless in the second, and it would also hide the fault I need the alert to see. The breaker and the timeout are the mitigation until we have the real mix.

I rejected putting the account id on a metric, a span, or a routine log. It is high cardinality, and it is an identifier. The collector strips it if it shows up anyway.

I rejected sampling metrics. Traces are sampled. Counters are not. An SLO on a sampled counter is fiction.

I opted the HTTP instrumentation into the stable semantic conventions. The Python default is still the old millisecond histogram. It has no bucket on 200ms, and it puts the host on the series. The SLO would not have been measurable on that metric. The stable one is seconds, with the route template and the status code.

I did not put this on Kubernetes for the local path. Compose is what the brief asked for, and it is enough to show the service, the ledger switch, the metrics, and the traces.

The optional kit piece I built is the pipeline. The edge and the Azure module are below, as designs. I rejected building all three. The pipeline is the piece that makes the SLO, the runbook, and the telemetry rules mandatory for the next team. The edge is one shared front door. The module is how we stamp out the runtime once the tag contract exists. The SLO spec already carries `owner`, `service`, `environment`, and `cost_centre`, which is the contract the module would enforce.

## How I kept the service small

Two reads. Postgres holds the settled balance and the transactions. The ledger is called only from the balance read, and only when that account has pending rows. The transaction list does not call the ledger, so a ledger incident cannot make history slow, and the list is not in the SLO.

Money is integer cents. Display strings are derived. I did not use floats.

The balance rules are in `services/account-balance/app/logic.py` and have no I/O:

- A pending debit that the ledger still calls pending, or that the ledger has settled but our snapshot has not caught up, reduces the available balance.
- A pending credit does not increase the available balance until the ledger says it has settled. I am assuming an unconfirmed deposit is not spendable. That is a product call and I would want it confirmed before this shipped.
- A rejected transaction is ignored.
- A partial ledger reply is thrown away. We do not mix confirmed rows and guessed rows.
- No pending rows means we do not call the ledger at all.

There is no write API, no reconciliation worker, no customer authentication, and no shared cache. Authentication belongs at the edge, in front of an internal-only service. I would not ship the read on a public network as it stands.

The breaker is in-process: five failures open it, one trial after 30 seconds. That is a shortcut once we run more than one replica. It is the right size for this service.

## Where AI helped, and what I changed

I used Cursor's agent (Grok) as a pair. I gave it the brief and asked for a small Python service, an SLO generator, local OpenTelemetry, and a pipeline gate, and I asked it to leave the edge and the Azure module as write-ups so the kit stayed explainable.

I kept the structure it drafted and I own these choices, including where the first draft was wrong or too big:

- The first instinct for a ledger 5xx is to fail the balance read. I changed that to the degraded 200. Failing the read protects the number and destroys the budget.
- I took retries out. They do not belong inside 200ms.
- I took a response cache out, for the reason above.
- I kept the degraded span attribute as a string (`true`) and I do not mark that span as an error. The HTTP call succeeded. Tools that count span status would otherwise call a successful degraded read a failure, and the tail sampler still keeps it via the attribute.
- I put `circuit_open` in the dependency alert's bad outcomes. Without that, the alert clears when the breaker opens, which is exactly when the ledger is still down.
- I did not take a service mesh, a second database, or a generated dashboard. The dashboard is hand-written for this one service. Generating dashboards can wait until a second team asks.

I can walk through the generator, the breaker, and the balance rules line by line. Those are the parts worth the review time.

## Questions I would ask, and the assumptions I shipped with

I did not get these answered by the product team, so the code matches the assumptions and the runbook says where a different answer would change the SLI.

1. When the ledger cannot confirm, do we show the settled balance with a warning, or do we fail the read? I assumed we show it and say so.
2. Does that degraded minute spend the error budget? I assumed no, because the SLI is success and latency, and the body is explicitly degraded. If product says a missing hold is a failed read, degraded responses become bad events and the burn alerts start paging on this incident. I would rather they decide that in the spec than discover it during the first real outage.
3. How long is the Sunday 16:30 peak, and is it one property or the group? I assumed four hours, once a week, at 1500 requests/s, for the cost note and the capacity note. The rate itself is from the ticket.
4. Who is allowed to call this API? I assumed the app, through an internal edge, and that this service does not authenticate the customer. That is not good enough for a public origin.
5. Are pending deposits spendable? I assumed no.
6. What happens when the budget is gone? I assumed a release freeze on this service: only reliability changes ship until the 30-day window recovers, unless the product owner writes down an override. The pipeline's production environment is where that person approves. The gate does not yet read the live budget. A human does, which is the honest version for a first cut.
7. Is the money contract integer cents in ZAR? I assumed yes.

4xx is excluded from the SLI. A 404 is the service doing its job. 5xx counts. So does a success that took longer than 200ms.

Silence is treated as a fault. `AccountBalanceReadTelemetryAbsent` tickets if the balance-read series is missing for 10 minutes. That is only valid because this API is on every page. An API that is idle at night would need a different rule.

## What it costs

Planning numbers for Azure South Africa North, not a quote. I have not run them through the pricing calculator. I used rough public list rates (about $0.000024 per vCPU-second and $0.000003 per GiB-second for Container Apps, traces around $0.40 per million stored, logs around $3 per GB) and added a little for the region. The shape of the bill matters more than the rand total.

Traffic I used: 150 requests/s steady is about 390 million balance reads in 30 days. A 4-hour spike at 1500 requests/s, four Sundays, adds about 80 million. Call it 470 million reads a month.

| Piece | Assumption | About |
| --- | --- | --- |
| Container Apps | 2 small replicas steady. Scale up for the event. Concurrency, not the raw 1500, is what we size: 1500 requests/s at 30ms is about 45 in flight. At the 80ms ledger cap it is higher, and the breaker stops that growing without limit. | $150 |
| PostgreSQL Flexible Server | Zone-redundant General Purpose, 2 vCPU, small disk and backups. I rejected Burstable for a spike that is on the customer path. Non-HA would be closer to half. | $400 |
| Metrics | A few dozen series at a 15s scrape is a few million samples a month. | under $5 |
| Traces | 3 spans per read, 10% kept, plus errors and degraded reads. Around 140 million spans. | $60 |
| Logs | Warnings only. A quiet month is about a gigabyte. | $5 |
| Registry and egress | Small. | $30 |

**About $650 a month**, and most of it is the HA database. Telemetry stays well under $100 only while the defaults hold.

The comparison that justifies the log rule: an access line of 500 bytes on every read is on the order of 230 GB a month, several hundred dollars, before anyone is having an incident. The same comparison justifies the label rule. One `account_id` label turns "a few dozen series" into one series per customer, and the metrics line stops being a rounding error.

Front Door is not in this total. It is in the edge note below. I would expect a small base fee plus the request charge, and I would price it when that design is the one we build.

## Edge and security (not built)

I would put Azure Front Door Premium in front of this service and give the Container App no public ingress. The origin is reachable from the Front Door identity only. TLS is a Front Door managed certificate, issued and renewed by the platform, with an alert when the certificate has less than 21 days left. That alert is on the certificate resource, not something each team invents. WAF starts on the managed rule set in detection for a week, then block, so we do not discover a false positive during the Sunday peak. The rate limit is per client, for abuse. It is not how we survive 1500 requests/s. A global cap under that number would fail real customers at 16:30. Capacity is replicas. The limit is for the client that is not a customer.

What I would have shown locally, if I had spent the build here, is Caddy or NGINX on the compose network: TLS to the proxy, a per-IP limit, and the API container unpublished. The origin-only-through-the-edge rule is the one I care about. A limit that the client can bypass by talking to the container directly is not a control.

I did not build it because the failure mode in this ticket is the ledger, not the edge, and because one platform team should own the front door. Every product team owning a slightly different WAF is how rules drift. The pipeline is what each team has to reference. The edge is a module they attach to, with the service name and the routes, and nothing else.

The balance service as written trusts whoever can reach it. That is acceptable on the compose network and not acceptable on a public IP. Shipping the Azure module below without the private ingress would be the wrong order.

## Azure module (not built)

One Terraform module, `service_runtime`, in the platform repo. A product team passes the image, the replica bounds, the SKU, and the four tags. The tags are `owner`, `service`, `environment`, and `cost_centre`, the same four fields the SLO spec already requires, so the gate and the module agree. `validation` blocks reject an empty tag and an environment outside `test` and `production`. That feedback is fast and local. The real enforcement is an Azure Policy that denies untagged resources, because a team can always click past a module.

The module creates the Container App (or attaches to the shared environment), the Postgres Flexible Server, a private endpoint, a user-assigned identity, a Key Vault reference for the ledger token, and diagnostic settings into the platform workspace. The token is not in tfvars and not in git. State is remote, in a locked storage account. `terraform fmt` and `terraform validate` run in the pipeline on every change. `plan` runs in a sandbox subscription and is not a scoring requirement. Nobody applies this from a laptop against production.

I did not build the module in this repo. An unapplied module looks finished and is easy to get subtly wrong, and `validate` without the provider schema for a half-written resource is a weak test. The next sprint is a small module that passes `fmt` and `validate`, applied once in a sandbox, with the policy gate already requiring the four fields so the module is not the first place they appear.

Cost tags without a budget alert are only half the control. I would add a monthly budget on the resource group at the number above, with a mail to the owner at 80% and again at 100%. The tag says who to send it to.

## Gaps and shortcuts

- The breaker is per process. Two replicas can keep calling a dead ledger until each one opens.
- The API does not authenticate callers.
- Postgres in compose is a single container. The cost estimate assumes zone-redundant HA that is not running here.
- Tail sampling is correct on one collector. It is wrong if we scale collectors without making sure a whole trace lands on one of them. I have not built that gateway.
- The 3-day and 30-day windows need the retention to match. Prometheus is set to 31 days. A fresh demo will not have three days of history, so those alerts are proven with `promtool`, not by waiting.
- I have not driven this at 1500 requests/s. The k6 script can. The SLO does not depend on me having done it on a laptop.
- Python is there because the rules are easy to read in a review. If the production profile needed a smaller process, I would keep the kit and replace the runtime. The spec does not mention the language.
- Grafana in compose is anonymous admin. That is a local convenience. It is not the production auth model.
- There is no reconciliation worker. A pending row stays pending until some other system updates Postgres. The read path confirms; it does not repair.
- The pipeline does not yet query the remaining budget. A person approving production is the stand-in.

## One more sprint

I would spend it on the decisions I had to assume, and on proving the alert with a clock.

Run the intermittent ledger under the load script and write down how long until `AccountBalanceReadLedgerConfirmErrorRateHigh` is firing and that the fast-burn alert stayed quiet. That is the game day.

Confirm with Accounts whether a degraded read spends the budget, and whether a pending deposit is spendable. Change `logic.py` and the spec if the answer is different. Those are one-file changes.

Add a pre-scale at 16:00 on Sunday, and an autoscaler on in-flight requests, using the concurrency note above rather than CPU. CPU will look bored while threads wait on the ledger.

Put `terraform fmt` and `terraform validate` for `service_runtime` into the pipeline, in a sandbox, and attach a budget alert to the resource group.

Only then would I look at a shared breaker and a cache, and only if the account mix says the cache would hit.
