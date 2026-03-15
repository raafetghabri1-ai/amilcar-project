from database.db import get_db

def add_invoice(appointment_id, amount):
    with get_db() as conn:
        conn.execute('INSERT INTO invoices (appointment_id, amount) VALUES (?, ?)',
            (appointment_id, amount))
        conn.commit()

def get_all_invoices():
    with get_db() as conn:
        return conn.execute(
            'SELECT invoices.id, customers.name, cars.brand, cars.model, '
            'appointments.service, invoices.amount, invoices.status '
            'FROM invoices '
            'JOIN appointments ON invoices.appointment_id = appointments.id '
            'JOIN cars ON appointments.car_id = cars.id '
            'JOIN customers ON cars.customer_id = customers.id'
        ).fetchall()
