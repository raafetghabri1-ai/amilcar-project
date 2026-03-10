from database.db import connect

def add_customer(name, phone):
    conn = connect()
    cursor = conn.cursor()

    cursor.execute('''
        INSERT INTO customers (name, phone)
        VALUES (?, ?)
    ''', (name, phone))

    conn.commit()
    conn.close()
    print(f"تم إضافة العميل {name} بنجاح ✅")

def get_all_customers():
    conn = connect()
    cursor = conn.cursor()

    cursor.execute('SELECT * FROM customers')
    customers = cursor.fetchall()

    conn.close()
    return customers