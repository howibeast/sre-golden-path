from fastapi.testclient import TestClient

from app.ledger import LedgerUnavailable
from app.logic import Account, Transaction
from app.main import app


class Repo:
    def __init__(self, accounts, pending=None, recent=None, broken=False):
        self._accounts = accounts
        self._pending = pending or {}
        self._recent = recent or {}
        self.broken = broken
        self.pending_calls = 0

    def ping(self):
        if self.broken:
            raise RuntimeError("db down")

    def get_account(self, account_id):
        if self.broken:
            raise RuntimeError("db down")
        return self._accounts.get(account_id)

    def pending_for(self, account_id):
        self.pending_calls += 1
        if self.broken:
            raise RuntimeError("db down")
        return list(self._pending.get(account_id, []))

    def list_transactions(self, account_id, limit):
        if self.broken:
            raise RuntimeError("db down")
        return list(self._recent.get(account_id, []))[:limit]


class Ledger:
    def __init__(self, states=None, error=None):
        self.states = states or {}
        self.error = error
        self.calls = []

    def confirm(self, transaction_ids):
        self.calls.append(list(transaction_ids))
        if self.error:
            raise self.error
        return {tx_id: self.states.get(tx_id, "pending") for tx_id in transaction_ids}


def _install(repo, ledger):
    app.state.repo = repo
    app.state.ledger = ledger


def _account():
    return Account("acc_1001", "ZAR", 150_000)


def _pending_debit():
    return Transaction("tx_1001", -5_000, "ZAR", "Table game", "pending", "2026-11-14T00:00:00+00:00")


def test_balance_applies_a_confirmed_hold():
    ledger = Ledger({"tx_1001": "pending"})
    _install(Repo({"acc_1001": _account()}, {"acc_1001": [_pending_debit()]}), ledger)
    with TestClient(app) as client:
        response = client.get("/accounts/acc_1001/balance")
    assert response.status_code == 200
    body = response.json()
    assert body["available"]["minor"] == 145_000
    assert body["available"]["display"] == "1450.00"
    assert body["degraded"] is False
    assert body["warning"] is None
    assert ledger.calls == [["tx_1001"]]


def test_ledger_failure_stays_a_fast_degraded_success():
    ledger = Ledger(error=LedgerUnavailable("error"))
    _install(Repo({"acc_1001": _account()}, {"acc_1001": [_pending_debit()]}), ledger)
    with TestClient(app) as client:
        response = client.get("/accounts/acc_1001/balance")
    assert response.status_code == 200
    body = response.json()
    assert body["degraded"] is True
    assert body["warning"] == "pending_not_applied"
    assert body["available"]["minor"] == 150_000
    assert body["settled"]["minor"] == 150_000


def test_no_pending_transactions_does_not_call_the_ledger():
    ledger = Ledger()
    recent = Transaction("tx_9", -100, "ZAR", "Food", "settled", "2026-11-14T00:00:00+00:00")
    _install(Repo({"acc_1002": Account("acc_1002", "ZAR", 2_500)}, recent={"acc_1002": [recent]}), ledger)
    with TestClient(app) as client:
        response = client.get("/accounts/acc_1002/balance")
    assert response.status_code == 200
    assert response.json()["available"]["minor"] == 2_500
    assert ledger.calls == []


def test_unknown_account_is_a_client_error():
    _install(Repo({}), Ledger())
    with TestClient(app) as client:
        response = client.get("/accounts/acc_missing/balance")
    assert response.status_code == 404


def test_invalid_account_id_is_rejected():
    _install(Repo({}), Ledger())
    with TestClient(app) as client:
        response = client.get("/accounts/bad.id/balance")
    assert response.status_code == 400


def test_transactions_do_not_call_the_ledger_and_default_to_twenty():
    ledger = Ledger(error=LedgerUnavailable("error"))
    rows = [
        Transaction(f"tx_{i}", -100, "ZAR", "Play", "settled", "2026-11-14T00:00:00+00:00")
        for i in range(25)
    ]
    _install(Repo({"acc_1001": _account()}, recent={"acc_1001": rows}), ledger)
    with TestClient(app) as client:
        response = client.get("/accounts/acc_1001/transactions")
        rejected = client.get("/accounts/acc_1001/transactions?limit=500")
    assert response.status_code == 200
    assert len(response.json()["transactions"]) == 20
    assert rejected.status_code == 400
    assert ledger.calls == []


def test_demo_page_shows_the_degraded_warning():
    with TestClient(app) as client:
        index = client.get("/")
        assert index.json()["demo"] == "/demo"
        page = client.get("/demo")
        assert page.status_code == 200
        assert "text/html" in page.headers["content-type"]
        assert "Pending amounts were not applied" in page.text
        assert "Run 3 minute drill" in page.text
        assert "python scripts/demo_fault.py --seconds 180" in page.text
        assert "localhost:16686" in page.text
        logo = client.get("/static/sun-international.png")
        assert logo.status_code == 200
        assert logo.headers["content-type"].startswith("image/")


def test_demo_ledger_switch_rejects_an_unknown_mode():
    with TestClient(app) as client:
        response = client.put("/demo/ledger", json={"mode": "drop-database"})
    assert response.status_code == 400


def test_demo_ledger_switch_forwards_the_known_mode(monkeypatch):
    captured = {}

    class Reply:
        def raise_for_status(self):
            return None

        def json(self):
            return {"mode": "intermittent", "error_rate": 0.1}

    def fake_put(url, json, timeout):
        captured["url"] = url
        captured["json"] = json
        captured["timeout"] = timeout
        return Reply()

    monkeypatch.setattr("app.main.httpx.put", fake_put)
    with TestClient(app) as client:
        response = client.put("/demo/ledger", json={"mode": "intermittent", "error_rate": 0.1})
    assert response.status_code == 200
    assert response.json()["mode"] == "intermittent"
    assert captured["json"]["mode"] == "intermittent"
    assert captured["url"].endswith("/control")


def test_database_outage_fails_the_read():
    _install(Repo({}, broken=True), Ledger())
    with TestClient(app) as client:
        response = client.get("/accounts/acc_1001/balance")
    assert response.status_code == 503


def test_readyz_reports_database_health():
    _install(Repo({}), Ledger())
    with TestClient(app) as client:
        assert client.get("/readyz").status_code == 200
        assert client.get("/healthz").status_code == 200
    app.state.repo = Repo({}, broken=True)
    with TestClient(app) as client:
        assert client.get("/readyz").status_code == 503
