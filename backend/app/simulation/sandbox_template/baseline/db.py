"""In-memory transaction store seeded with deterministic data for acceptance tests."""

import sqlite3


def make_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE transactions (id TEXT PRIMARY KEY, customer_id TEXT, amount_cents INTEGER,"
        " currency TEXT, created_at TEXT, description TEXT)"
    )
    rows = []
    for i in range(1, 24):  # 23 transactions for CUST-001, newest has the highest index
        rows.append((f"TX-A{i:03d}", "CUST-001", i * 100, "USD", f"2026-10-{i:02d}T10:00:00Z", f"Purchase {i}"))
    # one malformed legacy record (missing amount) that must never reach API consumers
    rows.append(("TX-A999", "CUST-001", None, "USD", "2026-09-30T10:00:00Z", "Legacy import"))
    for i in range(1, 6):
        rows.append((f"TX-B{i:03d}", "CUST-002", i * 250, "EUR", f"2026-10-{i:02d}T12:00:00Z", f"Transfer {i}"))
    conn.executemany("INSERT INTO transactions VALUES (?, ?, ?, ?, ?, ?)", rows)
    return conn
