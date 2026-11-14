from generate import _alert_base, render


SPEC = {
    "service": "account-balance",
    "owner": "accounts-team",
    "environment": "production",
    "cost_centre": "accounts",
    "sli": {
        "name": "balance_read",
        "description": "fast successful reads",
        "route": "/accounts/{account_id}/balance",
    },
    "objective": {"target": 0.999, "window": "30d"},
    "metrics": {
        "histogram": "http_server_request_duration_seconds",
        "route_label": "http_route",
        "status_label": "http_response_status_code",
        "service_label": "service_name",
        "good_bucket": "0.2",
    },
    "alerting": {"min_request_rate": 0.2, "runbook": "services/account-balance/runbooks/ledger-degraded.md"},
    "dependency": {
        "name": "ledger_confirm",
        "metric": "ledger_confirm_calls_total",
        "result_label": "result",
        "bad_results": ["error", "timeout", "circuit_open"],
        "auth_result": "rejected",
        "error_ratio": 0.05,
        "window": "2m",
        "for": "1m",
        "min_rate": 0.1,
    },
}


def test_rules_encode_the_budget_and_exclude_client_errors():
    rules = render(SPEC, "services/account-balance/slo.yaml")
    assert 'le="0.2"' in rules
    assert 'http_response_status_code=~"2..|5.."' in rules
    assert "4.." not in rules
    # 14.4 x 0.1% budget = 1.44% error ratio, on both windows.
    assert "> 0.014400" in rules
    assert "good_ratio:1h" in rules
    assert "good_ratio:5m" in rules
    assert "> 0.006000" in rules
    assert "> 0.001000" in rules
    assert "circuit_open" in rules
    assert "AccountBalanceReadFastBurn" in rules
    assert "AccountBalanceReadLedgerConfirmErrorRateHigh" in rules
    assert "AccountBalanceReadTelemetryAbsent" in rules
    assert "43" in rules  # minutes of budget in 30 days at 0.1%


def test_alert_name_does_not_repeat_the_shared_word():
    assert _alert_base(SPEC) == "AccountBalanceRead"


def test_committed_rules_match_the_generator():
    from generate import generate_all

    rendered = generate_all()
    assert rendered, "expected at least one SLO spec"
    for path, body in rendered.items():
        assert path.exists(), f"missing {path}, run python golden-path/slo/generate.py"
        assert path.read_text(encoding="utf-8") == body
