from database.db import get_db

def total_revenue():
    with get_db() as conn:
        result = conn.execute('SELECT SUM(amount) FROM invoices').fetchone()[0]
        return result or 0

def total_customers():
    with get_db() as conn:
        return conn.execute('SELECT COUNT(*) FROM customers').fetchone()[0]

def total_appointments():
    with get_db() as conn:
        return conn.execute('SELECT COUNT(*) FROM appointments').fetchone()[0]