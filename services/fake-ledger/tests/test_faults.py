from fastapi.testclient import TestClient

from server import MODES, injection, app


def test_injection_matrix():
    assert injection("ok", 0.5, 0.0) is None
    assert injection("error", 0.0, 0.9) == "error"
    assert injection("slow", 0.0, 0.9) == "slow"
    assert injection("reject_auth", 0.0, 0.9) == "reject_auth"
    assert injection("intermittent", 0.10, 0.05) == "error"
    assert injection("intermittent", 0.10, 0.50) is None
    assert injection("nope", 0.0, 0.0) == "error"
    assert "intermittent" in MODES


def test_control_switch_and_confirm():
    with TestClient(app) as client:
        try:
            updated = client.put(
                "/control",
                json={"mode": "error", "error_rate": 0.2, "status_code": 503},
            )
            assert updated.status_code == 200
            assert updated.json()["mode"] == "error"
            denied = client.post(
                "/v1/transactions/confirm",
                json={"transaction_ids": ["tx_1"]},
                headers={"Authorization": "Bearer dev-ledger-token"},
            )
            assert denied.status_code == 503

            client.put("/control", json={"mode": "ok"})
            missing = client.post("/v1/transactions/confirm", json={"transaction_ids": ["tx_1"]})
            assert missing.status_code == 401
            allowed = client.post(
                "/v1/transactions/confirm",
                json={"transaction_ids": ["tx_1", "tx_2"]},
                headers={"Authorization": "Bearer dev-ledger-token"},
            )
            assert allowed.status_code == 200
            assert allowed.json()["results"] == [
                {"id": "tx_1", "state": "pending"},
                {"id": "tx_2", "state": "pending"},
            ]

            client.put("/control", json={"mode": "reject_auth"})
            rejected = client.post(
                "/v1/transactions/confirm",
                json={"transaction_ids": ["tx_1"]},
                headers={"Authorization": "Bearer dev-ledger-token"},
            )
            assert rejected.status_code == 401
        finally:
            client.put("/control", json={"mode": "ok"})
