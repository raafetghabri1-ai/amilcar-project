"""
AMILCAR — Database Migration System
Simple versioned migrations for SQLite.
Run: python -m database.migrations
"""
import sqlite3
import os
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'amilcar.db')

MIGRATIONS = [
    # (version, description, sql_up)
    (1, "Enable WAL mode and foreign keys", [
        "PRAGMA journal_mode = WAL",
    ]),
    (2, "Add indexes for common queries", [
        "CREATE INDEX IF NOT EXISTS idx_appointments_date ON appointments(date)",
        "CREATE INDEX IF NOT EXISTS idx_appointments_car_id ON appointments(car_id)",
        "CREATE INDEX IF NOT EXISTS idx_appointments_status ON appointments(status)",
        "CREATE INDEX IF NOT EXISTS idx_invoices_status ON invoices(status)",
        "CREATE INDEX IF NOT EXISTS idx_invoices_appointment_id ON invoices(appointment_id)",
        "CREATE INDEX IF NOT EXISTS idx_invoices_created_at ON invoices(created_at)",
        "CREATE INDEX IF NOT EXISTS idx_cars_customer_id ON cars(customer_id)",
        "CREATE INDEX IF NOT EXISTS idx_expenses_date ON expenses(date)",
        "CREATE INDEX IF NOT EXISTS idx_activity_log_user_id ON activity_log(user_id)",
    ]),
    (3, "Add compound indexes for report queries", [
        "CREATE INDEX IF NOT EXISTS idx_invoices_status_created ON invoices(status, created_at)",
        "CREATE INDEX IF NOT EXISTS idx_appointments_date_status ON appointments(date, status)",
    ]),
]


def get_current_version(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS schema_migrations (
        version INTEGER PRIMARY KEY,
        description TEXT,
        applied_at TEXT DEFAULT (datetime('now'))
    )""")
    row = conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()
    return row[0] or 0


def migrate(target_version=None):
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON")
    current = get_current_version(conn)

    if target_version is None:
        target_version = max(v for v, _, _ in MIGRATIONS)

    applied = 0
    for version, description, sqls in MIGRATIONS:
        if version <= current:
            continue
        if version > target_version:
            break
        print(f"  Applying migration {version}: {description}...")
        for sql in sqls:
            try:
                conn.execute(sql)
            except sqlite3.OperationalError as e:
                if "already exists" in str(e) or "duplicate column" in str(e):
                    pass
                else:
                    raise
        conn.execute("INSERT INTO schema_migrations (version, description) VALUES (?, ?)",
                     (version, description))
        conn.commit()
        applied += 1

    conn.close()
    return current, applied


def status():
    conn = sqlite3.connect(DB_PATH)
    current = get_current_version(conn)
    latest = max(v for v, _, _ in MIGRATIONS)
    rows = conn.execute("SELECT version, description, applied_at FROM schema_migrations ORDER BY version").fetchall()
    conn.close()
    return current, latest, rows


if __name__ == '__main__':
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == 'status':
        current, latest, rows = status()
        print(f"Current version: {current}/{latest}")
        for v, d, t in rows:
            print(f"  v{v}: {d} ({t})")
        pending = [v for v, _, _ in MIGRATIONS if v > current]
        if pending:
            print(f"  Pending: {len(pending)} migration(s)")
    else:
        print("Running migrations...")
        current, applied = migrate()
        if applied:
            print(f"  Applied {applied} migration(s) (was v{current})")
        else:
            print(f"  Already at latest version (v{current})")
