import os

import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"),
    reason="Set DATABASE_URL to run the Postgres test",
)


def test_seeded_account_is_readable():
    from psycopg_pool import ConnectionPool

    from app.db import PostgresRepository

    pool = ConnectionPool(os.environ["DATABASE_URL"], min_size=1, max_size=2, open=True)
    try:
        repo = PostgresRepository(pool)
        account = repo.get_account("acc_1001")
        assert account is not None
        assert account.settled_balance_minor == 150_000
        pending = repo.pending_for("acc_1001")
        assert [row.id for row in pending] == ["tx_1001"]
        recent = repo.list_transactions("acc_1001", limit=20)
        assert recent[0].id == "tx_1001"
        assert repo.get_account("missing") is None
    finally:
        pool.close()
