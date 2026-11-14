"""Drive the balance API while the fake ledger misbehaves.

Shows the mitigation: reads stay successful and say when pending holds were
not applied. Leave it running for about three minutes if you want the
dependency alert to fire in Prometheus.

  python scripts/demo_fault.py
  python scripts/demo_fault.py --mode slow
  python scripts/demo_fault.py --reset
"""

from __future__ import annotations

import argparse
import json
import time
import urllib.error
import urllib.request

API = "http://127.0.0.1:8080"
LEDGER = "http://127.0.0.1:8081"


def put_control(mode: str, error_rate: float) -> None:
    body = json.dumps(
        {"mode": mode, "error_rate": error_rate, "delay_ms": 800, "status_code": 500}
    ).encode()
    request = urllib.request.Request(
        LEDGER + "/control",
        data=body,
        method="PUT",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=5) as response:
        print("ledger control:", response.read().decode())


def once() -> tuple[int, float, dict]:
    request = urllib.request.Request(API + "/accounts/acc_1001/balance")
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=2) as response:
            raw = response.read()
            code = response.status
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        code = exc.code
    elapsed_ms = (time.perf_counter() - started) * 1000
    try:
        body = json.loads(raw)
    except json.JSONDecodeError:
        body = {}
    return code, elapsed_ms, body


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", default="intermittent", choices=["ok", "error", "slow", "reject_auth", "intermittent"])
    parser.add_argument("--error-rate", type=float, default=0.10)
    parser.add_argument("--seconds", type=int, default=150)
    parser.add_argument("--reset", action="store_true")
    args = parser.parse_args()

    if args.reset:
        put_control("ok", 0)
        print("ledger reset to ok")
        return 0

    put_control(args.mode, args.error_rate)
    deadline = time.time() + args.seconds
    codes: dict[int, int] = {}
    degraded = 0
    slow = 0
    total = 0
    while time.time() < deadline:
        code, elapsed_ms, body = once()
        total += 1
        codes[code] = codes.get(code, 0) + 1
        if elapsed_ms > 200:
            slow += 1
        if body.get("degraded"):
            degraded += 1
        if total % 25 == 0:
            print(
                f"n={total} codes={codes} degraded={degraded} "
                f"slower_than_200ms={slow} last_ms={elapsed_ms:.0f}"
            )
        time.sleep(0.1)

    print("---")
    print(f"requests={total} codes={codes} degraded={degraded} slower_than_200ms={slow}")
    print("Alert to look for: AccountBalanceReadLedgerConfirmErrorRateHigh")
    print("Prometheus: http://localhost:9090/alerts")
    print("Grafana:    http://localhost:3000/d/balance-read")
    print("Jaeger:     http://localhost:16686  (service: account-balance, tag balance.degraded=true)")
    print("Reset with: python scripts/demo_fault.py --reset")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
