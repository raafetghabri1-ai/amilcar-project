from database.db import get_db

def add_appointment(car_id, date, service):
    with get_db() as conn:
        conn.execute('INSERT INTO appointments (car_id, date, service) VALUES (?, ?, ?)',
            (car_id, date, service))
        conn.commit()

def get_appointments():
    with get_db() as conn:
        return conn.execute(
            'SELECT appointments.id, customers.name, cars.brand, cars.model, '
            'appointments.date, appointments.service, appointments.status '
            'FROM appointments '
            'JOIN cars ON appointments.car_id = cars.id '
            'JOIN customers ON cars.customer_id = customers.id'
        ).fetchall()