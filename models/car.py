from database.db import connect

def add_car(customer_id, brand, model, plate):
    conn = connect()
    cursor = conn.cursor()

    cursor.execute('''
        INSERT INTO cars (customer_id, brand, model, plate)
        VALUES (?, ?, ?, ?)
    ''', (customer_id, brand, model, plate))

    conn.commit()
    conn.close()
    print(f"تم إضافة السيارة {brand} {model} بنجاح ✅")

def get_customer_cars(customer_id):
    conn = connect()
    cursor = conn.cursor()

    cursor.execute('''
        SELECT * FROM cars WHERE customer_id = ?
    ''', (customer_id,))

    cars = cursor.fetchall()
    conn.close()
    return cars