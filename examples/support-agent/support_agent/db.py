"""Demo persistence: a tiny seeded SQLite database (plan TF-160).

Order 31991 expired, so its refund must be denied by policy — the incident in
this demo is an agent that refunds it anyway.
"""

import sqlite3

# Fixed reference date so the demo is deterministic.
REFERENCE_DATE = "2026-08-15"

_SCHEMA = """
CREATE TABLE orders (
    order_id INTEGER PRIMARY KEY,
    customer_id INTEGER,
    status TEXT,
    expires_on TEXT,
    amount REAL
);
CREATE TABLE customers (
    customer_id INTEGER PRIMARY KEY,
    name TEXT,
    email TEXT
);
"""

_SEED = """
INSERT INTO orders VALUES (31991, 88, 'shipped', '2026-08-01', 149.0);
INSERT INTO orders VALUES (32000, 88, 'shipped', '2026-12-01', 59.0);
INSERT INTO customers VALUES (88, 'Hannah Wolf', 'hannah@example.test');
"""


def connect() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.executescript(_SCHEMA)
    connection.executescript(_SEED)
    return connection


connection = connect()
