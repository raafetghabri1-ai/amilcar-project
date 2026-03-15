from database.db import get_db

def add_car(customer_id, brand, model, plate):
    with get_db() as conn:
        conn.execute('INSERT INTO cars (customer_id, brand, model, plate) VALUES (?, ?, ?, ?)',
            (customer_id, brand, model, plate))
        conn.commit()

def get_customer_cars(customer_id):
    with get_db() as conn:
        return conn.execute('SELECT * FROM cars WHERE customer_id = ?', (customer_id,)).fetchall()