import sqlite3

def connect():
    connection = sqlite3.connect("database/amilcar.db")
    return connection

def create_tables():
    conn = connect()
    cursor = conn.cursor()

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS customers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            phone TEXT NOT NULL
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS cars (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            customer_id INTEGER NOT NULL,
            brand TEXT NOT NULL,
            model TEXT NOT NULL,
            plate TEXT NOT NULL,
            FOREIGN KEY (customer_id) REFERENCES customers (id)
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS appointments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            car_id INTEGER NOT NULL,
            date TEXT NOT NULL,
            service TEXT NOT NULL,
            status TEXT DEFAULT 'pending',
            FOREIGN KEY (car_id) REFERENCES cars (id)
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS invoices (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            appointment_id INTEGER NOT NULL,
            amount REAL NOT NULL,
            status TEXT DEFAULT 'unpaid',
            FOREIGN KEY (appointment_id) REFERENCES appointments (id)
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS invoices (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            appointment_id INTEGER NOT NULL,
            amount REAL NOT NULL,
            status TEXT DEFAULT 'unpaid',
            FOREIGN KEY (appointment_id) REFERENCES appointments (id)
        )
    ''')

    conn.commit()
    conn.close()
    print("قاعدة البيانات جاهزة ✅")

if __name__ == "__main__":
    create_tables()