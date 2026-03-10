from database.db import connect

def add_invoice(appointment_id, amount):
    conn = connect()
    cursor = conn.cursor()

    cursor.execute('''
        INSERT INTO invoices (appointment_id, amount)
        VALUES (?, ?)
    ''', (appointment_id, amount))

    conn.commit()
    conn.close()
    print(f"تم إنشاء فاتورة بمبلغ {amount} ✅")

def get_all_invoices():
    conn = connect()
    cursor = conn.cursor()

    cursor.execute('''
        SELECT invoices.id, customers.name, cars.brand, cars.model,
               appointments.service, invoices.amount, invoices.status
        FROM invoices
        JOIN appointments ON invoices.appointment_id = appointments.id
        JOIN cars ON appointments.car_id = cars.id
        JOIN customers ON cars.customer_id = customers.id
    ''')

    results = cursor.fetchall()
    conn.close()
    return results
