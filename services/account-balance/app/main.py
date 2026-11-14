"""Account balance read API.

GET /accounts/{account_id}/balance
GET /accounts/{account_id}/transactions?limit=20

The balance read calls the ledger only when this account has pending rows.
If that call fails or is too slow, the handler still returns the settled
balance and marks the body degraded. That is the mitigation for a ledger
that is failing a fraction of calls.
"""

import logging
import os
import re
import time
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from psycopg_pool import ConnectionPool

from app.db import PostgresRepository
from app.ledger import CircuitBreaker, LedgerClient, LedgerUnavailable
from app.logic import Account, available_balance, format_minor
from app import telemetry

ACCOUNT_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")
STATIC_DIR = Path(__file__).resolve().parent / "static"
log = logging.getLogger("account-balance")


class Money(BaseModel):
    minor: int
    display: str


class BalanceResponse(BaseModel):
    account_id: str
    currency: str
    settled: Money
    available: Money
    degraded: bool
    pending_confirmation: str
    warning: str | None


class TransactionResponse(BaseModel):
    id: str
    amount: Money
    currency: str
    description: str
    status: str
    created_at: str


class TransactionListResponse(BaseModel):
    account_id: str
    limit: int
    transactions: list[TransactionResponse]


class DemoLedgerControl(BaseModel):
    mode: str
    error_rate: float = Field(default=0.1, ge=0.0, le=1.0)


LEDGER_MODES = {"ok", "error", "slow", "reject_auth", "intermittent"}


def configure_logging() -> None:
    level = os.environ.get("LOG_LEVEL", "INFO").upper()
    logging.basicConfig(level=level, format="%(levelname)s %(name)s %(message)s")


def create_app() -> FastAPI:
    # Providers and middleware have to exist before the server starts.
    # Starlette refuses new middleware once the lifespan has begun.
    configure_logging()
    telemetry.setup_telemetry(os.environ.get("SERVICE_NAME", "account-balance"))

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        owns_dependencies = False
        database_url = os.environ.get("DATABASE_URL")
        if getattr(app.state, "repo", None) is None and database_url:
            pool = ConnectionPool(
                database_url,
                min_size=1,
                max_size=20,
                timeout=2,
                open=True,
            )
            app.state.pool = pool
            app.state.repo = PostgresRepository(pool)
            app.state.ledger = LedgerClient(
                base_url=os.environ.get("LEDGER_URL", "http://localhost:8081"),
                token=os.environ.get("LEDGER_TOKEN", "dev-ledger-token"),
                timeout_seconds=float(os.environ.get("LEDGER_TIMEOUT_SECONDS", "0.08")),
                breaker=CircuitBreaker(
                    failure_threshold=int(os.environ.get("LEDGER_BREAKER_THRESHOLD", "5")),
                    recovery_seconds=float(os.environ.get("LEDGER_BREAKER_RECOVERY_SECONDS", "30")),
                ),
            )
            owns_dependencies = True
            log.info("dependencies_ready")
        yield
        if owns_dependencies:
            app.state.ledger.close()
            app.state.pool.close()

    app = FastAPI(
        title="Account balance",
        version="1.0.0",
        summary="Read API for the current balance and recent transactions.",
        lifespan=lifespan,
    )

    @app.get("/healthz")
    def healthz():
        return {"status": "ok"}

    @app.get("/readyz")
    def readyz(request: Request):
        repo = getattr(request.app.state, "repo", None)
        if repo is None:
            raise HTTPException(status_code=503, detail="not_ready")
        try:
            repo.ping()
        except Exception:
            log.exception("readiness_failed")
            raise HTTPException(status_code=503, detail="database_unavailable") from None
        return {"status": "ready"}

    @app.get("/")
    def index():
        return {
            "service": "account-balance",
            "balance": "/accounts/{account_id}/balance",
            "transactions": "/accounts/{account_id}/transactions",
            "demo": "/demo",
        }

    @app.get("/demo", include_in_schema=False)
    def demo_page():
        return FileResponse(STATIC_DIR / "demo.html", media_type="text/html")

    @app.put("/demo/ledger", include_in_schema=False)
    def demo_ledger(body: DemoLedgerControl):
        # The browser cannot call the ledger directly. This forwards the
        # same switch demo_fault.py uses, and only the modes that switch allows.
        if body.mode not in LEDGER_MODES:
            raise HTTPException(status_code=400, detail="unknown mode")
        ledger_url = os.environ.get("LEDGER_URL", "http://localhost:8081").rstrip("/")
        try:
            reply = httpx.put(
                ledger_url + "/control",
                json={
                    "mode": body.mode,
                    "error_rate": body.error_rate,
                    "delay_ms": 800,
                    "status_code": 500,
                },
                timeout=2,
            )
            reply.raise_for_status()
        except httpx.HTTPError:
            raise HTTPException(status_code=503, detail="ledger_unreachable") from None
        return reply.json()

    @app.get("/accounts/{account_id}/balance", response_model=BalanceResponse)
    def read_balance(account_id: str, request: Request, response: Response):
        started = time.perf_counter()
        _check_account_id(account_id)
        repo = _require_repo(request)
        ledger = _require_ledger(request)
        account, pending = _load_account(repo, account_id)
        states, outcome = _confirm_pending(ledger, [txn.id for txn in pending])
        view = available_balance(account, pending, states)
        if view.degraded:
            telemetry.record_degraded(view.warning or "degraded")
            telemetry.mark_degraded(view.warning or "degraded")
            log.warning("balance_read_degraded reason=%s ledger=%s", view.warning, outcome)
        elapsed = time.perf_counter() - started
        if elapsed > 0.2:
            log.warning("balance_read_slow seconds=%.3f", elapsed)
        _set_trace_header(response)
        return BalanceResponse(
            account_id=account.account_id,
            currency=account.currency,
            settled=Money(minor=view.settled_minor, display=format_minor(view.settled_minor)),
            available=Money(minor=view.available_minor, display=format_minor(view.available_minor)),
            degraded=view.degraded,
            pending_confirmation=view.pending_confirmation,
            warning=view.warning,
        )

    @app.get(
        "/accounts/{account_id}/transactions",
        response_model=TransactionListResponse,
    )
    def read_transactions(account_id: str, request: Request, response: Response, limit: int = 20):
        # History is not in the balance SLO and does not call the ledger.
        # A ledger incident must not make "recent transactions" slow as well.
        _check_account_id(account_id)
        if limit < 1 or limit > 100:
            raise HTTPException(status_code=400, detail="limit must be between 1 and 100")
        repo = _require_repo(request)
        account, _pending = _load_account(repo, account_id)
        try:
            rows = repo.list_transactions(account_id, limit)
        except HTTPException:
            raise
        except Exception:
            log.exception("database_read_failed")
            raise HTTPException(status_code=503, detail="database_unavailable") from None
        _set_trace_header(response)
        return TransactionListResponse(
            account_id=account.account_id,
            limit=limit,
            transactions=[
                TransactionResponse(
                    id=row.id,
                    amount=Money(minor=row.amount_minor, display=format_minor(row.amount_minor)),
                    currency=row.currency,
                    description=row.description,
                    status=row.status,
                    created_at=row.created_at,
                )
                for row in rows
            ],
        )

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    telemetry.instrument_app(app)
    return app


def _check_account_id(account_id: str) -> None:
    if not ACCOUNT_ID.fullmatch(account_id):
        raise HTTPException(status_code=400, detail="invalid account id")


def _require_repo(request: Request):
    repo = getattr(request.app.state, "repo", None)
    if repo is None:
        raise HTTPException(status_code=503, detail="not_ready")
    return repo


def _require_ledger(request: Request):
    ledger = getattr(request.app.state, "ledger", None)
    if ledger is None:
        raise HTTPException(status_code=503, detail="not_ready")
    return ledger


def _load_account(repo, account_id: str) -> tuple[Account, list]:
    try:
        account = repo.get_account(account_id)
        if account is None:
            raise HTTPException(status_code=404, detail="account_not_found")
        pending = repo.pending_for(account_id)
    except HTTPException:
        raise
    except Exception:
        log.exception("database_read_failed")
        raise HTTPException(status_code=503, detail="database_unavailable") from None
    return account, pending


def _confirm_pending(ledger, transaction_ids: list[str]) -> tuple[dict[str, str] | None, str]:
    if not transaction_ids:
        return {}, "skipped"
    try:
        states = ledger.confirm(transaction_ids)
    except LedgerUnavailable as exc:
        telemetry.record_ledger(exc.result)
        return None, exc.result
    telemetry.record_ledger("ok")
    return states, "ok"


def _set_trace_header(response: Response) -> None:
    current = telemetry.trace_id()
    if current:
        response.headers["X-Trace-Id"] = current


app = create_app()
