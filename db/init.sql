-- Local seed for the account balance read API.
-- Money is integer minor units (cents). ZAR is an assumption, not a product decision.

CREATE TABLE accounts (
    account_id text PRIMARY KEY,
    currency char(3) NOT NULL,
    settled_balance_minor bigint NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE transactions (
    id text PRIMARY KEY,
    account_id text NOT NULL REFERENCES accounts (account_id),
    amount_minor bigint NOT NULL,
    currency char(3) NOT NULL,
    description text NOT NULL,
    status text NOT NULL CHECK (status IN ('settled', 'pending')),
    created_at timestamptz NOT NULL
);

CREATE INDEX transactions_account_created_idx
    ON transactions (account_id, created_at DESC);

INSERT INTO accounts (account_id, currency, settled_balance_minor) VALUES
    ('acc_1001', 'ZAR', 150000),
    ('acc_1002', 'ZAR', 2500),
    ('acc_1003', 'ZAR', 0);

INSERT INTO transactions (id, account_id, amount_minor, currency, description, status, created_at) VALUES
    ('tx_1001', 'acc_1001', -5000, 'ZAR', 'Table game', 'pending', now() - interval '2 minutes'),
    ('tx_1002', 'acc_1001', 20000, 'ZAR', 'Deposit', 'settled', now() - interval '1 day'),
    ('tx_1003', 'acc_1001', -1500, 'ZAR', 'Food and beverage', 'settled', now() - interval '3 hours'),
    ('tx_1004', 'acc_1002', -1000, 'ZAR', 'Slot play', 'settled', now() - interval '2 hours'),
    ('tx_1005', 'acc_1003', 10000, 'ZAR', 'Pending deposit', 'pending', now() - interval '10 minutes');
