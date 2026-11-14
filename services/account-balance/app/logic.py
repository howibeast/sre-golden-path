"""Balance rules with no I/O.

The product behaviour lives here so it can be tested without Postgres or the ledger.
Money is integer minor units. Display strings are derived and never parsed back.
"""

from dataclasses import dataclass


CONFIRMED = "confirmed"
UNAVAILABLE = "unavailable"
PENDING_NOT_APPLIED = "pending_not_applied"
LEDGER_INCOMPLETE = "ledger_incomplete"


@dataclass(frozen=True)
class Transaction:
    id: str
    amount_minor: int
    currency: str
    description: str
    status: str
    created_at: str


@dataclass(frozen=True)
class Account:
    account_id: str
    currency: str
    settled_balance_minor: int


@dataclass(frozen=True)
class BalanceView:
    settled_minor: int
    available_minor: int
    degraded: bool
    pending_confirmation: str
    warning: str | None
    pending_applied_minor: int


def format_minor(amount: int) -> str:
    sign = "-" if amount < 0 else ""
    whole, cents = divmod(abs(int(amount)), 100)
    return f"{sign}{whole}.{cents:02d}"


def available_balance(
    account: Account,
    pending: list[Transaction],
    ledger_states: dict[str, str] | None,
) -> BalanceView:
    """Work out the figure the customer can be shown.

    ledger_states is None when the ledger could not be consulted. We then return
    the settled balance and say so. We do not guess which holds are still open.

    When the ledger answers:
    - a pending debit still open (or already settled in the ledger, but not yet
      in our snapshot) reduces available balance
    - a pending credit increases available balance only once the ledger says it
      has settled; an unconfirmed deposit is not spendable
    - a rejected transaction is ignored
    If any pending id is missing from the reply, we treat the whole reply as
    unusable and degrade, rather than mix confirmed and guessed rows.
    """

    settled = account.settled_balance_minor
    if ledger_states is None:
        return BalanceView(
            settled_minor=settled,
            available_minor=settled,
            degraded=True,
            pending_confirmation=UNAVAILABLE,
            warning=PENDING_NOT_APPLIED,
            pending_applied_minor=0,
        )

    open_rows = [txn for txn in pending if txn.status == "pending"]
    missing = [txn.id for txn in open_rows if txn.id not in ledger_states]
    if missing:
        return BalanceView(
            settled_minor=settled,
            available_minor=settled,
            degraded=True,
            pending_confirmation=UNAVAILABLE,
            warning=LEDGER_INCOMPLETE,
            pending_applied_minor=0,
        )

    applied = 0
    for txn in open_rows:
        state = ledger_states[txn.id]
        if state not in ("pending", "settled"):
            continue
        if txn.amount_minor < 0:
            applied += txn.amount_minor
        elif state == "settled":
            applied += txn.amount_minor

    return BalanceView(
        settled_minor=settled,
        available_minor=settled + applied,
        degraded=False,
        pending_confirmation=CONFIRMED,
        warning=None,
        pending_applied_minor=applied,
    )
