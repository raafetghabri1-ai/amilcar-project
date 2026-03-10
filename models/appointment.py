from database.db import connect

def add_appointment(car_id, date, service):
    conn = connect()
    cursor = conn.cursor()

    cursor.execute('''
        INSERT INTO appointments (car_id, date, service)
        VALUES (?, ?, ?)
    ''', (car_id, date, service))

    conn.commit()
    conn.close()
    print(f"تم حجز موعد بتاريخ {date} لخدمة {service} ✅")

def get_appointments():
    conn = connect()
    cursor = conn.cursor()

    cursor.execute('''
        SELECT appointments.id, customers.name, cars.brand, cars.model,
               appointments.date, appointments.service, appointments.status
        FROM appointments
        JOIN cars ON appointments.car_id = cars.id
        JOIN customers ON cars.customer_id = customers.id
    ''')

    results = cursor.fetchall()
    conn.close()
    return results