# Telemetry standard

This is the part a product team copies. The defaults are there to keep the observability bill boring. The platform charges on samples, spans, and log volume. A team that logs every request, or puts an account id on a metric, will cost more than the service costs to run.

## Bootstrap

Call `setup_telemetry()` once at process start, then instrument the HTTP framework. Point `OTEL_EXPORTER_OTLP_ENDPOINT` at the platform collector. Do not run your own Prometheus in production. Locally, the compose stack is that collector.

Resource attributes, and only these:

| Attribute | Example | Rule |
| --- | --- | --- |
| `service.name` | `account-balance` | One name per deployable. Same string as the SLO spec `service`. |
| `service.namespace` | `accounts` | The product area. |
| `deployment.environment` | `production` | `local`, `test`, or `production`. |

No account id, user id, email, or free-text description as a resource attribute. Those are not properties of the process.

## Metrics

Metrics are never sampled. An SLO built on a sampled counter is a guess.

The process sets `OTEL_SEMCONV_STABILITY_OPT_IN=http`. Without that, the Python instrumentation emits the old `http.server.duration` histogram in milliseconds. That histogram has no bucket at 200ms, and it labels `http.host`, which changes with the container address. The stable metric is `http.server.request.duration` in seconds, with the route template on `http.route`.

Name with the OpenTelemetry semantic conventions. The balance service uses:

- `http.server.request.duration` for the SLO (histogram, seconds, with a bucket boundary at `0.2`)
- `ledger.confirm.calls` for the dependency (counter, attribute `result`)
- `balance.read.degraded` for the mitigation (counter, attribute `reason`)

### Allowed labels

`service.name`, `deployment.environment`, `http.route` (the template, never the raw path), `http.request.method`, `http.response.status_code`, and the low-cardinality attributes above (`result`, `reason`).

`result` may be `ok`, `error`, `timeout`, `rejected`, `circuit_open`.
`reason` may be `pending_not_applied` or `ledger_incomplete`.

### Forbidden labels

Account id, transaction id, customer name, description, trace id, span id, raw URL, user agent, and anything a caller can set to a new value on each request. If a value can grow with traffic, it does not go on a metric.

The collector deletes `account_id`, `url.full`, `http.url`, and `user.id` if a team sets them anyway. That is a backstop, not permission. The policy gate rejects `account_id` inside `telemetry.py`.

Health checks (`/healthz`, `/readyz`) are not in the SLO and are not instrumented.

## Traces

The app sends every span. The collector decides what to keep:

- all error spans
- all spans slower than 200ms
- all spans with `balance.degraded=true`
- 10% of everything else

Do not also sample in the SDK. Two samplers in a row silently keep 1% and you will not have the incident. Do not set the span status to error on a degraded 200. The customer got a response. The attribute is how we find those traces without failing the request in every tool that counts span status.

Do not put the account id or the transaction description on a span. The route template is enough. The response carries `X-Trace-Id` so a caller can hand support the trace without us storing the account id in the trace backend.

Tail sampling means the collector still receives 100% of spans. The saving is storage, not collector CPU. At the Sunday peak that is about 1500 requests/s and a handful of spans each. Size the collector for the peak, and store the 10% plus the bad traces. A later sprint can split a sampling gateway from storage if the collector CPU becomes the bill.

## Logs

Default level is INFO. Successes are not logged. A degraded read or a read slower than 200ms logs one warning line. The line has a reason and a duration. It does not have an account id.

Log every request and the bill dominates the service. At 150 requests/s, a 500-byte access line is on the order of 200 GB a month before the Sunday spike. Warnings only stay in the single-digit gigabytes unless we are actually having an incident.

No payloads, no SQL, no tokens. Uvicorn access logs are off for the same reason.

## Retention

| Signal | Local | Production |
| --- | --- | --- |
| Metrics | 31 days, so the 30-day SLO window fits | 31 days raw, then the recording rules are what we keep |
| Traces | Jaeger local disk, a few hours is enough to demo | 3 days |
| Logs | Docker json-file, 10 MB × 3 files per container | 7 days |

Production retention is a platform setting, not a per-service knob. Teams do not get a longer trace window because a dashboard felt empty.

## What another team changes

The route, the service name, and any extra counter they need for their own dependency. They do not change the sampler, the label rules, or the log default. If they need a new label, they add it to the allowed list in this file in the same change. A label that is not on the list does not ship.
