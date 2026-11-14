import httpx

from app.ledger import CircuitBreaker, LedgerClient, LedgerUnavailable


def test_breaker_opens_after_the_threshold_and_allows_one_trial_later():
    now = {"t": 0.0}
    breaker = CircuitBreaker(failure_threshold=3, recovery_seconds=30, clock=lambda: now["t"])

    assert breaker.allow() is True
    breaker.record_failure()
    breaker.record_failure()
    assert breaker.state == "closed"
    breaker.record_failure()
    assert breaker.state == "open"
    assert breaker.allow() is False

    now["t"] = 30.0
    assert breaker.allow() is True
    assert breaker.state == "half_open"
    # One trial at a time.
    assert breaker.allow() is False
    breaker.record_success()
    assert breaker.state == "closed"
    assert breaker.allow() is True


def test_timeout_and_http_errors_do_not_retry_and_open_the_breaker():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        assert request.headers["Authorization"] == "Bearer secret"
        if calls["n"] == 1:
            raise httpx.ReadTimeout("slow", request=request)
        return httpx.Response(500, json={"error": "down"})

    client = LedgerClient(
        "http://ledger",
        "secret",
        timeout_seconds=0.08,
        breaker=CircuitBreaker(failure_threshold=2),
        transport=httpx.MockTransport(handler),
    )
    try:
        try:
            client.confirm(["tx_1"])
        except LedgerUnavailable as exc:
            assert exc.result == "timeout"
        try:
            client.confirm(["tx_1"])
        except LedgerUnavailable as exc:
            assert exc.result == "error"
        try:
            client.confirm(["tx_1"])
        except LedgerUnavailable as exc:
            assert exc.result == "circuit_open"
        else:
            raise AssertionError("breaker should be open")
        assert calls["n"] == 2
    finally:
        client.close()


def test_unauthorized_ledger_is_a_rejected_result():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"detail": "no"})

    client = LedgerClient(
        "http://ledger",
        "secret",
        transport=httpx.MockTransport(handler),
    )
    try:
        try:
            client.confirm(["tx_1"])
        except LedgerUnavailable as exc:
            assert exc.result == "rejected"
        else:
            raise AssertionError("expected rejected")
    finally:
        client.close()


def test_confirm_parses_states_and_skips_the_network_when_idle():
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("no pending ids means no call")

    client = LedgerClient("http://ledger", "secret", transport=httpx.MockTransport(handler))
    try:
        assert client.confirm([]) == {}
    finally:
        client.close()


def test_successful_confirm_returns_states():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"results": [{"id": "tx_1", "state": "pending"}, {"id": "tx_2", "state": "rejected"}]},
        )

    client = LedgerClient("http://ledger", "secret", transport=httpx.MockTransport(handler))
    try:
        assert client.confirm(["tx_1", "tx_2"]) == {"tx_1": "pending", "tx_2": "rejected"}
        assert client.breaker.state == "closed"
    finally:
        client.close()
