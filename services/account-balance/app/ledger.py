"""Ledger client and the breaker that protects the balance read.

The read has a 200ms budget. This client does not retry. A retry would spend
the budget twice and would add load to a ledger that is already failing.
"""

import threading
import time

import httpx


class LedgerUnavailable(Exception):
    def __init__(self, result: str):
        super().__init__(result)
        self.result = result


class CircuitBreaker:
    """Per-process breaker. A shared one belongs in a later sprint."""

    def __init__(
        self,
        failure_threshold: int = 5,
        recovery_seconds: float = 30.0,
        clock=time.monotonic,
    ):
        self.failure_threshold = failure_threshold
        self.recovery_seconds = recovery_seconds
        self._clock = clock
        self._lock = threading.Lock()
        self.state = "closed"
        self.failures = 0
        self.opened_at = 0.0

    def allow(self) -> bool:
        with self._lock:
            if self.state == "closed":
                return True
            if self.state == "open":
                if (self._clock() - self.opened_at) >= self.recovery_seconds:
                    self.state = "half_open"
                    return True
                return False
            # A trial is already in flight.
            return False

    def record_success(self) -> None:
        with self._lock:
            self.failures = 0
            self.state = "closed"

    def record_failure(self) -> None:
        with self._lock:
            self.failures += 1
            if self.state == "half_open" or self.failures >= self.failure_threshold:
                self.state = "open"
                self.opened_at = self._clock()


class LedgerClient:
    def __init__(
        self,
        base_url: str,
        token: str,
        timeout_seconds: float = 0.08,
        breaker: CircuitBreaker | None = None,
        transport: httpx.BaseTransport | None = None,
    ):
        self._token = token
        self._breaker = breaker or CircuitBreaker()
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"),
            timeout=timeout_seconds,
            transport=transport,
        )

    @property
    def breaker(self) -> CircuitBreaker:
        return self._breaker

    def confirm(self, transaction_ids: list[str]) -> dict[str, str]:
        if not transaction_ids:
            return {}
        if not self._breaker.allow():
            raise LedgerUnavailable("circuit_open")
        try:
            response = self._client.post(
                "/v1/transactions/confirm",
                json={"transaction_ids": transaction_ids},
                headers={"Authorization": f"Bearer {self._token}"},
            )
        except httpx.TimeoutException:
            self._breaker.record_failure()
            raise LedgerUnavailable("timeout") from None
        except httpx.HTTPError:
            self._breaker.record_failure()
            raise LedgerUnavailable("error") from None

        if response.status_code == 401:
            self._breaker.record_failure()
            raise LedgerUnavailable("rejected")
        if response.status_code >= 400:
            self._breaker.record_failure()
            raise LedgerUnavailable("error")

        try:
            body = response.json()
            results = {item["id"]: item["state"] for item in body["results"]}
        except (ValueError, KeyError, TypeError):
            self._breaker.record_failure()
            raise LedgerUnavailable("error") from None

        self._breaker.record_success()
        return results

    def close(self) -> None:
        self._client.close()
