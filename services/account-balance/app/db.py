"""Postgres read model. Writes are out of scope for this ticket."""

from psycopg_pool import ConnectionPool

from app.logic import Account, Transaction


class PostgresRepository:
    def __init__(self, pool: ConnectionPool):
        self._pool = pool

    def ping(self) -> None:
        with self._pool.connection() as conn:
            conn.execute("SELECT 1").fetchone()

    def get_account(self, account_id: str) -> Account | None:
        with self._pool.connection() as conn:
            row = conn.execute(
                """
                SELECT account_id, currency, settled_balance_minor
                FROM accounts
                WHERE account_id = %s
                """,
                (account_id,),
            ).fetchone()
        if row is None:
            return None
        return Account(
            account_id=row[0],
            currency=row[1].strip(),
            settled_balance_minor=row[2],
        )

    def pending_for(self, account_id: str) -> list[Transaction]:
        with self._pool.connection() as conn:
            rows = conn.execute(
                """
                SELECT id, amount_minor, currency, description, status, created_at
                FROM transactions
                WHERE account_id = %s AND status = 'pending'
                ORDER BY created_at DESC
                """,
                (account_id,),
            ).fetchall()
        return [_row_to_transaction(row) for row in rows]

    def list_transactions(self, account_id: str, limit: int) -> list[Transaction]:
        with self._pool.connection() as conn:
            rows = conn.execute(
                """
                SELECT id, amount_minor, currency, description, status, created_at
                FROM transactions
                WHERE account_id = %s
                ORDER BY created_at DESC
                LIMIT %s
                """,
                (account_id, limit),
            ).fetchall()
        return [_row_to_transaction(row) for row in rows]


def _row_to_transaction(row) -> Transaction:
    created = row[5]
    created_at = created.isoformat() if hasattr(created, "isoformat") else str(created)
    return Transaction(
        id=row[0],
        amount_minor=row[1],
        currency=row[2].strip(),
        description=row[3],
        status=row[4],
        created_at=created_at,
    )
