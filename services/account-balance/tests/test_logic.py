from app.logic import Account, Transaction, available_balance, format_minor


def account(settled=150_000):
    return Account("acc_1001", "ZAR", settled)


def txn(tx_id="tx_1001", amount=-5_000, status="pending"):
    return Transaction(tx_id, amount, "ZAR", "Table game", status, "2026-11-14T00:00:00+00:00")


def test_format_minor_signs_and_cents():
    assert format_minor(150_000) == "1500.00"
    assert format_minor(-5_000) == "-50.00"
    assert format_minor(5) == "0.05"
    assert format_minor(-5) == "-0.05"
    assert format_minor(0) == "0.00"


def test_pending_debit_reduces_available_balance():
    view = available_balance(account(), [txn()], {"tx_1001": "pending"})
    assert view.degraded is False
    assert view.available_minor == 145_000
    assert view.pending_applied_minor == -5_000


def test_pending_credit_is_not_spendable_until_the_ledger_settles_it():
    credit = txn("tx_dep", amount=10_000)
    held = available_balance(account(0), [credit], {"tx_dep": "pending"})
    cleared = available_balance(account(0), [credit], {"tx_dep": "settled"})
    assert held.available_minor == 0
    assert cleared.available_minor == 10_000


def test_rejected_hold_is_released():
    view = available_balance(account(), [txn()], {"tx_1001": "rejected"})
    assert view.available_minor == 150_000
    assert view.degraded is False


def test_settled_rows_in_the_snapshot_are_not_applied_again():
    # Settled transactions are already inside settled_balance_minor.
    view = available_balance(account(), [txn(status="settled")], {"tx_1001": "settled"})
    assert view.available_minor == 150_000
    assert view.pending_applied_minor == 0


def test_ledger_outage_returns_settled_balance_and_says_so():
    view = available_balance(account(), [txn()], None)
    assert view.degraded is True
    assert view.available_minor == 150_000
    assert view.pending_confirmation == "unavailable"
    assert view.warning == "pending_not_applied"


def test_incomplete_ledger_reply_is_not_partially_applied():
    view = available_balance(account(), [txn(), txn("tx_other", amount=-100)], {"tx_1001": "pending"})
    assert view.degraded is True
    assert view.warning == "ledger_incomplete"
    assert view.available_minor == 150_000


def test_no_pending_rows_needs_no_ledger_answer():
    view = available_balance(account(), [], {})
    assert view.degraded is False
    assert view.available_minor == 150_000
