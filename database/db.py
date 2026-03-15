import sqlite3
from contextlib import contextmanager

def connect():
    connection = sqlite3.connect("database/amilcar.db")
    return connection

@contextmanager
def get_db():
    conn = sqlite3.connect("database/amilcar.db")
    try:
        yield conn
    finally:
        conn.close()

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
        CREATE TABLE IF NOT EXISTS quotes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            phone TEXT NOT NULL,
            service TEXT,
            photos TEXT,
            status TEXT DEFAULT 'pending',
            price REAL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS expenses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            category TEXT NOT NULL,
            description TEXT,
            amount REAL NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS activity_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            username TEXT,
            action TEXT NOT NULL,
            detail TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS services (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            price REAL DEFAULT 0,
            active INTEGER DEFAULT 1
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS loyalty (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            customer_id INTEGER NOT NULL,
            service_type TEXT NOT NULL,
            wash_count INTEGER DEFAULT 0,
            free_washes_used INTEGER DEFAULT 0,
            FOREIGN KEY (customer_id) REFERENCES customers (id),
            UNIQUE(customer_id, service_type)
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS inventory (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            category TEXT DEFAULT '',
            quantity INTEGER DEFAULT 0,
            min_quantity INTEGER DEFAULT 5,
            unit_price REAL DEFAULT 0,
            supplier TEXT DEFAULT '',
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS ratings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            appointment_id INTEGER NOT NULL UNIQUE,
            customer_id INTEGER NOT NULL,
            rating INTEGER NOT NULL CHECK(rating BETWEEN 1 AND 5),
            comment TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (appointment_id) REFERENCES appointments (id),
            FOREIGN KEY (customer_id) REFERENCES customers (id)
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS service_packages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            description TEXT DEFAULT '',
            services TEXT NOT NULL,
            original_price REAL DEFAULT 0,
            package_price REAL DEFAULT 0,
            active INTEGER DEFAULT 1
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS service_inventory (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            service_name TEXT NOT NULL,
            inventory_id INTEGER NOT NULL,
            quantity_used REAL DEFAULT 1,
            FOREIGN KEY (inventory_id) REFERENCES inventory (id)
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS communication_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            customer_id INTEGER NOT NULL,
            type TEXT NOT NULL,
            subject TEXT DEFAULT '',
            message TEXT DEFAULT '',
            sent_by TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (customer_id) REFERENCES customers (id)
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS time_tracking (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            username TEXT NOT NULL,
            action TEXT NOT NULL,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            date TEXT NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users (id)
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS reward_points (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            customer_id INTEGER NOT NULL UNIQUE,
            points INTEGER DEFAULT 0,
            total_earned INTEGER DEFAULT 0,
            total_spent INTEGER DEFAULT 0,
            tier TEXT DEFAULT 'BRONZE',
            FOREIGN KEY (customer_id) REFERENCES customers (id)
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS reward_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            customer_id INTEGER NOT NULL,
            points INTEGER NOT NULL,
            type TEXT NOT NULL,
            description TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (customer_id) REFERENCES customers (id)
        )
    ''')

    # إضافة أعمدة جديدة إذا لم تكن موجودة
    migrations = [
        ("ALTER TABLE customers ADD COLUMN notes TEXT DEFAULT ''", None),
        ("ALTER TABLE invoices ADD COLUMN payment_method TEXT DEFAULT ''", None),
        ("ALTER TABLE users ADD COLUMN role TEXT DEFAULT 'employee'", None),
        ("ALTER TABLE cars ADD COLUMN year TEXT DEFAULT ''", None),
        ("ALTER TABLE cars ADD COLUMN color TEXT DEFAULT ''", None),
        ("ALTER TABLE appointments ADD COLUMN assigned_to TEXT DEFAULT ''", None),
        ("ALTER TABLE invoices ADD COLUMN paid_amount REAL DEFAULT 0", None),
        ("ALTER TABLE users ADD COLUMN full_name TEXT DEFAULT ''", None),
        ("ALTER TABLE appointments ADD COLUMN photos_before TEXT DEFAULT ''", None),
        ("ALTER TABLE appointments ADD COLUMN photos_after TEXT DEFAULT ''", None),
        ("ALTER TABLE customers ADD COLUMN email TEXT DEFAULT ''", None),
        ("ALTER TABLE appointments ADD COLUMN time TEXT DEFAULT ''", None),
        ("ALTER TABLE invoices ADD COLUMN discount_type TEXT DEFAULT ''", None),
        ("ALTER TABLE invoices ADD COLUMN discount_value REAL DEFAULT 0", None),
        ("ALTER TABLE customers ADD COLUMN portal_token TEXT DEFAULT ''", None),
        ("ALTER TABLE appointments ADD COLUMN estimated_duration INTEGER DEFAULT 60", None),
        ("ALTER TABLE invoices ADD COLUMN qr_token TEXT DEFAULT ''", None),
    ]
    for sql, _ in migrations:
        try:
            cursor.execute(sql)
        except:
            pass

    # إنشاء الفهارس
    indexes = [
        "CREATE INDEX IF NOT EXISTS idx_appointments_date ON appointments(date)",
        "CREATE INDEX IF NOT EXISTS idx_appointments_status ON appointments(status)",
        "CREATE INDEX IF NOT EXISTS idx_appointments_car_id ON appointments(car_id)",
        "CREATE INDEX IF NOT EXISTS idx_invoices_status ON invoices(status)",
        "CREATE INDEX IF NOT EXISTS idx_invoices_appointment_id ON invoices(appointment_id)",
        "CREATE INDEX IF NOT EXISTS idx_cars_customer_id ON cars(customer_id)",
        "CREATE INDEX IF NOT EXISTS idx_cars_plate ON cars(plate)",
        "CREATE INDEX IF NOT EXISTS idx_expenses_date ON expenses(date)",
        "CREATE INDEX IF NOT EXISTS idx_comm_log_customer ON communication_log(customer_id)",
        "CREATE INDEX IF NOT EXISTS idx_time_tracking_user ON time_tracking(user_id)",
        "CREATE INDEX IF NOT EXISTS idx_time_tracking_date ON time_tracking(date)",
        "CREATE INDEX IF NOT EXISTS idx_reward_points_customer ON reward_points(customer_id)",
    ]
    for idx in indexes:
        try:
            cursor.execute(idx)
        except:
            pass

    # إدراج الخدمات الافتراضية إذا كان الجدول فارغاً
    existing = cursor.execute("SELECT COUNT(*) FROM services").fetchone()[0]
    if existing == 0:
        default_services = [
            ('Lavage Normal', 35), ('Céramique Spray', 55),
            ('Detailing Intérieur', 100), ('Detailing Extérieur', 120),
            ('Detailing Complet', 200), ('Polissage', 350),
            ('Nano Céramique', 0), ('Correction Peinture', 0),
            ('Protection PPF', 0), ('Autre', 0),
        ]
        for name, price in default_services:
            cursor.execute("INSERT INTO services (name, price) VALUES (?, ?)", (name, price))

    # إدراج الإعدادات الافتراضية
    defaults = [
        ('shop_name', 'AMILCAR'),
        ('shop_tagline', 'WHERE CARS BECOME ART'),
        ('shop_address', 'MAHRES, SFAX, TUNISIA'),
        ('shop_phone', ''),
        ('tax_rate', '0'),
    ]
    for key, val in defaults:
        try:
            cursor.execute("INSERT INTO settings (key, value) VALUES (?, ?)", (key, val))
        except:
            pass

    conn.commit()
    conn.close()
    print("قاعدة البيانات جاهزة ✅")

if __name__ == "__main__":
    create_tables()