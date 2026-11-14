"""Turn one SLO spec per service into Prometheus recording rules and burn-rate alerts.

The burn rates are the multi-window set from the Google SRE workbook, scaled to
this service's own budget (1 - target):

  burn 14.4 over 1h and 5m   pages  — about 2% of the 30-day budget in one hour
  burn 6    over 6h and 30m  pages  — about 5% of the budget in six hours
  burn 1    over 3d and 6h   tickets — about 10% of the budget in three days

Both windows have to be burning. The long window is the real spend. The short
window stops the page once the service has already recovered.

Adding a service is one spec file under services/<name>/slo.yaml and a re-run
of this script. Teams do not copy alert expressions around.
"""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = Path(__file__).resolve().parent / "generated"

BURNS = (
    ("FastBurn", "page", "1h", "5m", 14.4, "2% of the 30-day budget in one hour"),
    ("MediumBurn", "page", "6h", "30m", 6.0, "5% of the 30-day budget in six hours"),
    ("SlowBurn", "ticket", "3d", "6h", 1.0, "10% of the 30-day budget in three days"),
)
RATIO_WINDOWS = ("5m", "30m", "1h", "6h", "3d", "30d")

REQUIRED = (
    "service",
    "owner",
    "environment",
    "cost_centre",
    "sli",
    "objective",
    "metrics",
    "alerting",
    "dependency",
)


def generate_all(root: Path = ROOT) -> dict[Path, str]:
    specs = sorted(root.glob("services/*/slo.yaml"))
    if not specs:
        raise SystemExit("no services/*/slo.yaml specs found")
    rendered = {}
    for path in specs:
        spec = yaml.safe_load(path.read_text(encoding="utf-8"))
        _validate(spec, path)
        out = OUT_DIR / f"{spec['service']}.yml"
        rendered[out] = render(spec, path.relative_to(root).as_posix())
    return rendered


def write_all(check: bool) -> int:
    rendered = generate_all()
    stale = []
    for path, body in rendered.items():
        current = path.read_text(encoding="utf-8") if path.exists() else ""
        if current != body:
            stale.append(path)
        if not check:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(body, encoding="utf-8", newline="\n")
    if check and stale:
        for path in stale:
            print(f"SLO rules out of date: {path.relative_to(ROOT).as_posix()}")
        print("Run: python golden-path/slo/generate.py")
        return 1
    return 0


def render(spec: dict, source: str) -> str:
    service = spec["service"]
    sli = spec["sli"]["name"]
    prefix = _prefix(spec)
    budget = 1 - float(spec["objective"]["target"])
    lines = [
        f"# Generated from {source} by golden-path/slo/generate.py.",
        "# Do not edit. Change the spec and regenerate.",
        f"# SLO: {float(spec['objective']['target']) * 100:.1f}% of {sli} over {spec['objective']['window']}.",
        f"# Error budget: {budget:.4f} (about {_budget_minutes(spec)} minutes of total failure in that window).",
        "groups:",
        f"  - name: slo-{service}-{sli}".replace("_", "-"),
        "    rules:",
    ]

    for window in RATIO_WINDOWS:
        lines += [
            f"      - record: {prefix}:good_ratio:{window}",
            "        expr: |",
            *_indent(_good_ratio(spec, window), 10),
        ]

    lines += [
        f"      - record: slo:{_metric_prefix(spec)}:budget_remaining:{spec['objective']['window']}",
        "        expr: |",
        *_indent(
            f"1 - ((1 - {prefix}:good_ratio:{spec['objective']['window']}) / {budget:.6f})",
            10,
        ),
        f"      - record: sli:{_metric_prefix(spec)}:latency_p99:5m",
        "        expr: |",
        *_indent(_latency(spec), 10),
    ]

    runbook = spec["alerting"]["runbook"]
    min_rate = spec["alerting"]["min_request_rate"]
    for name, severity, long_window, short_window, factor, consumed in BURNS:
        threshold = factor * budget
        expr = " and ".join(
            [
                f"((1 - {prefix}:good_ratio:{long_window}) > {threshold:.6f})",
                f"((1 - {prefix}:good_ratio:{short_window}) > {threshold:.6f})",
                _traffic(spec, short_window, min_rate),
            ]
        )
        lines += [
            f"      - alert: {_alert_base(spec)}{name}",
            "        expr: |",
            *_indent(expr, 10),
            "        labels:",
            f"          severity: {severity}",
            f"          service: {service}",
            f"          slo: {sli}",
            f"          owner: {spec['owner']}",
            f"          burn: {name}",
            "        annotations:",
            f"          summary: {sli} on {service} is burning the error budget ({name}).",
            "          description: |",
            *_indent(
                (
                    f"Bad events are above {threshold:.2%} on both the {long_window} and "
                    f"{short_window} windows (burn {factor:g}x). That is {consumed}. "
                    f"Target {spec['objective']['target']} over {spec['objective']['window']}."
                ),
                12,
            ),
            f"          runbook: {runbook}",
        ]

    lines += _dependency_alerts(spec)
    lines += _absent_alert(spec)
    return "\n".join(lines) + "\n"


def _dependency_alerts(spec: dict) -> list[str]:
    dep = spec["dependency"]
    service = spec["service"]
    metric = dep["metric"]
    result = dep["result_label"]
    service_label = spec["metrics"]["service_label"]
    bad = "|".join(dep["bad_results"])
    base = f'{service_label}="{service}"'
    ratio = (
        f'(sum(rate({metric}{{{base},{result}=~"{bad}"}}[{dep["window"]}]))'
        f' / sum(rate({metric}{{{base}}}[{dep["window"]}]))) > {float(dep["error_ratio"]):.4f}'
        f' and sum(rate({metric}{{{base}}}[{dep["window"]}])) > {float(dep["min_rate"])}'
    )
    auth = (
        f'sum(rate({metric}{{{base},{result}="{dep["auth_result"]}"}}[5m])) > 0'
    )
    return [
        f"      - alert: {_alert_base(spec)}LedgerConfirmErrorRateHigh",
        "        expr: |",
        *_indent(ratio, 10),
        f"        for: {dep['for']}",
        "        labels:",
        "          severity: page",
        f"          service: {service}",
        f"          slo: {spec['sli']['name']}",
        f"          owner: {spec['owner']}",
        "          signal: dependency",
        "        annotations:",
        "          summary: Ledger confirm calls are failing on the balance read path.",
        "          description: |",
        *_indent(
            "This is the cause signal. The balance read should stay fast and successful "
            "by serving a degraded settled balance. Page on this alert, not on the SLO, "
            "while that mitigation is holding. circuit_open counts as bad so the alert "
            "does not clear just because the breaker stopped calling the ledger.",
            12,
        ),
        f"          runbook: {spec['alerting']['runbook']}",
        f"      - alert: {_alert_base(spec)}LedgerConfirmAuthRejected",
        "        expr: |",
        *_indent(auth, 10),
        "        for: 2m",
        "        labels:",
        "          severity: page",
        f"          service: {service}",
        f"          owner: {spec['owner']}",
        "          signal: auth",
        "        annotations:",
        "          summary: The ledger is rejecting the balance service credentials.",
        "          description: |",
        *_indent(
            "This is a configuration failure, not capacity. Check the ledger token. "
            "Do not raise timeouts and do not retry.",
            12,
        ),
        f"          runbook: {spec['alerting']['runbook']}",
    ]


def _absent_alert(spec: dict) -> list[str]:
    metric = spec["metrics"]["histogram"] + "_count"
    selector = _common(spec)
    return [
        f"      - alert: {_alert_base(spec)}TelemetryAbsent",
        "        expr: |",
        *_indent(f"absent({metric}{{{selector}}})", 10),
        "        for: 10m",
        "        labels:",
        "          severity: ticket",
        f"          service: {spec['service']}",
        f"          owner: {spec['owner']}",
        "          signal: telemetry",
        "        annotations:",
        "          summary: Balance-read metrics have been missing for 10 minutes.",
        "          description: |",
        *_indent(
            "The SLO is blind without this series. This API is on every page, so "
            "silence is more likely a broken pipeline than an idle service.",
            12,
        ),
        f"          runbook: {spec['alerting']['runbook']}",
    ]


def _good_ratio(spec: dict, window: str) -> str:
    histogram = spec["metrics"]["histogram"]
    status = spec["metrics"]["status_label"]
    bucket = spec["metrics"]["good_bucket"]
    common = _common(spec)
    good = f'sum(rate({histogram}_bucket{{{common},{status}=~"2..",le="{bucket}"}}[{window}]))'
    total = f'sum(rate({histogram}_count{{{common},{status}=~"2..|5.."}}[{window}]))'
    return f"({good}) / ({total})"


def _traffic(spec: dict, window: str, min_rate: float) -> str:
    histogram = spec["metrics"]["histogram"]
    status = spec["metrics"]["status_label"]
    return (
        f'(sum(rate({histogram}_count{{{_common(spec)},{status}=~"2..|5.."}}[{window}]))'
        f" > {min_rate})"
    )


def _latency(spec: dict) -> str:
    histogram = spec["metrics"]["histogram"]
    return (
        f"histogram_quantile(0.99, sum by (le) (rate({histogram}_bucket{{{_common(spec)}}}[5m])))"
    )


def _common(spec: dict) -> str:
    metrics = spec["metrics"]
    return (
        f'{metrics["service_label"]}="{spec["service"]}",'
        f'{metrics["route_label"]}="{spec["sli"]["route"]}"'
    )


def _prefix(spec: dict) -> str:
    return f"sli:{_metric_prefix(spec)}"


def _metric_prefix(spec: dict) -> str:
    service = spec["service"].replace("-", "_")
    return f"{service}:{spec['sli']['name']}"


def _alert_base(spec: dict) -> str:
    """Pascal-case service + SLI, without saying the shared word twice.

    account-balance + balance_read -> AccountBalanceRead.
    """

    service_parts = spec["service"].split("-")
    sli_parts = spec["sli"]["name"].split("_")
    if sli_parts and service_parts and sli_parts[0] == service_parts[-1]:
        sli_parts = sli_parts[1:]
    parts = service_parts + sli_parts
    return "".join(part[:1].upper() + part[1:] for part in parts)


def _budget_minutes(spec: dict) -> int:
    window = spec["objective"]["window"]
    if not window.endswith("d"):
        return 0
    days = int(window[:-1])
    return round(days * 24 * 60 * (1 - float(spec["objective"]["target"])))


def _validate(spec: dict, path: Path) -> None:
    missing = [key for key in REQUIRED if key not in spec]
    if missing:
        raise SystemExit(f"{path}: missing {', '.join(missing)}")
    target = float(spec["objective"]["target"])
    if not 0 < target < 1:
        raise SystemExit(f"{path}: objective.target must be between 0 and 1")
    for key in ("owner", "environment", "cost_centre", "service"):
        if not str(spec[key]).strip():
            raise SystemExit(f"{path}: {key} is empty")


def _indent(text: str, spaces: int) -> list[str]:
    pad = " " * spaces
    return [pad + line if line else line for line in text.split("\n")]


def main(argv: list[str]) -> int:
    return write_all(check="--check" in argv)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
