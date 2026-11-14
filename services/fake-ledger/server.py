"""Stand-in for the ledger the balance API calls.

The switch is PUT /control. Modes: ok, error, slow, reject_auth, intermittent.
It exists so we can show the alert and the mitigation without a real ledger.
"""

import os
import random
import threading
import time

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

MODES = {"ok", "error", "slow", "reject_auth", "intermittent"}

app = FastAPI(title="Fake ledger", version="1.0.0")
_lock = threading.Lock()
_control = {
    "mode": "ok",
    "error_rate": 0.10,
    "delay_ms": 800,
    "status_code": 500,
}


class ControlUpdate(BaseModel):
    mode: str
    error_rate: float | None = Field(default=None, ge=0.0, le=1.0)
    delay_ms: int | None = Field(default=None, ge=0, le=10_000)
    status_code: int | None = Field(default=None, ge=400, le=599)


class ConfirmRequest(BaseModel):
    transaction_ids: list[str]


def injection(mode: str, error_rate: float, roll: float) -> str | None:
    """What to do to this call. None means confirm it."""

    if mode == "reject_auth":
        return "reject_auth"
    if mode == "slow":
        return "slow"
    if mode == "error":
        return "error"
    if mode == "intermittent":
        return "error" if roll < error_rate else None
    if mode == "ok":
        return None
    return "error"


@app.get("/healthz")
def healthz():
    return {"status": "ok"}


@app.get("/control")
def read_control():
    with _lock:
        return dict(_control)


@app.put("/control")
def update_control(body: ControlUpdate):
    if body.mode not in MODES:
        raise HTTPException(status_code=400, detail="unknown mode")
    with _lock:
        _control["mode"] = body.mode
        if body.error_rate is not None:
            _control["error_rate"] = body.error_rate
        if body.delay_ms is not None:
            _control["delay_ms"] = body.delay_ms
        if body.status_code is not None:
            _control["status_code"] = body.status_code
        return dict(_control)


@app.post("/v1/transactions/confirm")
def confirm(
    body: ConfirmRequest,
    authorization: str | None = Header(default=None),
):
    token = os.environ.get("LEDGER_TOKEN", "dev-ledger-token")
    with _lock:
        snapshot = dict(_control)

    action = injection(snapshot["mode"], snapshot["error_rate"], random.random())
    if action == "reject_auth":
        raise HTTPException(status_code=401, detail="rejected")
    if authorization != f"Bearer {token}":
        raise HTTPException(status_code=401, detail="unauthorized")
    if action == "slow":
        time.sleep(snapshot["delay_ms"] / 1000)
    if action == "error":
        raise HTTPException(status_code=snapshot["status_code"], detail="injected error")

    return {
        "results": [{"id": tx_id, "state": "pending"} for tx_id in body.transaction_ids]
    }
