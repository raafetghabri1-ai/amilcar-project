from database.db import get_db

def add_customer(name, phone, notes=''):
    with get_db() as conn:
        conn.execute('INSERT INTO customers (name, phone, notes) VALUES (?, ?, ?)',
            (name, phone, notes))
        conn.commit()

def get_all_customers():
    with get_db() as conn:
        return conn.execute('SELECT * FROM customers').fetchall()