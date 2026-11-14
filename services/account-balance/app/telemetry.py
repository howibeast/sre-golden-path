"""OpenTelemetry bootstrap for this service.

Sampling of traces happens in the collector, not here. This process sends every
span so the collector can keep errors, slow calls, and degraded reads, and only
a fraction of the rest. Metrics are never sampled: the SLO is wrong if they are.
"""

import logging
import os

# Stable HTTP metrics: seconds, route template, status code.
# The library default is the old millisecond histogram, which has no 200ms
# bucket and labels the host. The SLO spec is written for the stable names.
os.environ.setdefault("OTEL_SEMCONV_STABILITY_OPT_IN", "http")

from opentelemetry import metrics, trace
from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.metrics.view import ExplicitBucketHistogramAggregation, View
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

log = logging.getLogger(__name__)

# 200ms is an explicit bucket so the SLO can count "fast enough" exactly.
LATENCY_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.2, 0.3, 0.5, 1.0, 2.5)

_started = False
_instrumented = False
_ledger_counter = None
_degraded_counter = None


def setup_telemetry(service_name: str) -> None:
    global _started
    if _started or os.environ.get("OTEL_DISABLED") == "1":
        return

    endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "localhost:4317")
    resource = Resource.create(
        {
            "service.name": service_name,
            "service.namespace": "accounts",
            "deployment.environment": os.environ.get("DEPLOYMENT_ENVIRONMENT", "local"),
        }
    )

    tracer_provider = TracerProvider(resource=resource)
    tracer_provider.add_span_processor(
        BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint, insecure=True))
    )
    trace.set_tracer_provider(tracer_provider)

    reader = PeriodicExportingMetricReader(
        OTLPMetricExporter(endpoint=endpoint, insecure=True),
        export_interval_millis=5000,
    )
    meter_provider = MeterProvider(
        resource=resource,
        metric_readers=[reader],
        views=[
            View(
                instrument_name="http.server.request.duration",
                aggregation=ExplicitBucketHistogramAggregation(LATENCY_BUCKETS),
            )
        ],
    )
    metrics.set_meter_provider(meter_provider)
    _started = True
    log.info("telemetry_enabled endpoint=%s", endpoint)


def instrument_app(app) -> None:
    global _instrumented
    if _instrumented or os.environ.get("OTEL_DISABLED") == "1":
        return
    # After the providers exist, so the HTTP histogram uses our buckets.
    FastAPIInstrumentor.instrument_app(app, excluded_urls="healthz,readyz")
    _instrumented = True


def record_ledger(result: str) -> None:
    _counter("ledger").add(1, {"result": result})


def record_degraded(reason: str) -> None:
    _counter("degraded").add(1, {"reason": reason})


def mark_degraded(reason: str) -> None:
    """Keep the incident traces without failing the HTTP span.

    The customer got a 200. Marking the span status as error would make some
    tools count a successful degraded read as a failure. The attribute is what
    the collector's tail sampler looks for.
    """

    span = trace.get_current_span()
    if not span.is_recording():
        return
    span.set_attribute("balance.degraded", "true")
    span.set_attribute("balance.degraded_reason", reason)


def trace_id() -> str | None:
    context = trace.get_current_span().get_span_context()
    if context is None or not context.is_valid:
        return None
    return format(context.trace_id, "032x")


def _counter(kind: str):
    global _ledger_counter, _degraded_counter
    meter = metrics.get_meter("account.balance")
    if kind == "ledger":
        if _ledger_counter is None:
            _ledger_counter = meter.create_counter(
                "ledger.confirm.calls",
                description="Outcomes of ledger confirm calls on the balance read path",
            )
        return _ledger_counter
    if _degraded_counter is None:
        _degraded_counter = meter.create_counter(
            "balance.read.degraded",
            description="Balance reads served without a confirmed pending set",
        )
    return _degraded_counter
