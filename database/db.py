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

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS coupons (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT NOT NULL UNIQUE,
            discount_type TEXT NOT NULL DEFAULT 'percent',
            discount_value REAL NOT NULL DEFAULT 0,
            min_amount REAL DEFAULT 0,
            max_uses INTEGER DEFAULT 1,
            used_count INTEGER DEFAULT 0,
            expires_at TEXT DEFAULT '',
            active INTEGER DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS coupon_usage (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            coupon_id INTEGER NOT NULL,
            invoice_id INTEGER NOT NULL,
            customer_id INTEGER NOT NULL,
            discount_applied REAL DEFAULT 0,
            used_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (coupon_id) REFERENCES coupons (id),
            FOREIGN KEY (invoice_id) REFERENCES invoices (id),
            FOREIGN KEY (customer_id) REFERENCES customers (id)
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS suppliers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            phone TEXT DEFAULT '',
            email TEXT DEFAULT '',
            address TEXT DEFAULT '',
            notes TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS purchase_orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            supplier_id INTEGER NOT NULL,
            order_date TEXT NOT NULL,
            status TEXT DEFAULT 'pending',
            total_amount REAL DEFAULT 0,
            notes TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (supplier_id) REFERENCES suppliers (id)
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS purchase_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id INTEGER NOT NULL,
            inventory_id INTEGER,
            item_name TEXT NOT NULL,
            quantity REAL NOT NULL,
            unit_price REAL NOT NULL,
            FOREIGN KEY (order_id) REFERENCES purchase_orders (id),
            FOREIGN KEY (inventory_id) REFERENCES inventory (id)
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS waiting_queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            customer_id INTEGER NOT NULL,
            car_id INTEGER,
            service TEXT DEFAULT '',
            priority INTEGER DEFAULT 0,
            status TEXT DEFAULT 'waiting',
            estimated_wait INTEGER DEFAULT 30,
            notes TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (customer_id) REFERENCES customers (id)
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS user_permissions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            role TEXT NOT NULL,
            permission TEXT NOT NULL,
            UNIQUE(role, permission)
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS payments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            invoice_id INTEGER NOT NULL,
            amount REAL NOT NULL,
            method TEXT DEFAULT 'cash',
            reference TEXT DEFAULT '',
            notes TEXT DEFAULT '',
            paid_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (invoice_id) REFERENCES invoices (id)
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS surveys (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            appointment_id INTEGER NOT NULL,
            customer_id INTEGER NOT NULL,
            token TEXT NOT NULL UNIQUE,
            q_quality INTEGER DEFAULT 0,
            q_speed INTEGER DEFAULT 0,
            q_reception INTEGER DEFAULT 0,
            q_cleanliness INTEGER DEFAULT 0,
            q_value INTEGER DEFAULT 0,
            comment TEXT DEFAULT '',
            submitted INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            submitted_at TEXT DEFAULT '',
            FOREIGN KEY (appointment_id) REFERENCES appointments (id),
            FOREIGN KEY (customer_id) REFERENCES customers (id)
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS car_photos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            car_id INTEGER NOT NULL,
            appointment_id INTEGER,
            photo_type TEXT DEFAULT 'before',
            filename TEXT NOT NULL,
            description TEXT DEFAULT '',
            uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (car_id) REFERENCES cars (id)
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS online_bookings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            phone TEXT NOT NULL,
            email TEXT DEFAULT '',
            car_brand TEXT DEFAULT '',
            car_model TEXT DEFAULT '',
            car_plate TEXT DEFAULT '',
            service TEXT NOT NULL,
            preferred_date TEXT NOT NULL,
            preferred_time TEXT DEFAULT '',
            notes TEXT DEFAULT '',
            status TEXT DEFAULT 'pending',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS maintenance_plans (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            car_id INTEGER NOT NULL,
            service_type TEXT NOT NULL,
            interval_km INTEGER DEFAULT 0,
            interval_months INTEGER DEFAULT 0,
            last_done_date TEXT DEFAULT '',
            last_done_km INTEGER DEFAULT 0,
            next_due_date TEXT DEFAULT '',
            next_due_km INTEGER DEFAULT 0,
            active INTEGER DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (car_id) REFERENCES cars (id)
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS email_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            customer_id INTEGER,
            to_email TEXT NOT NULL,
            subject TEXT NOT NULL,
            body TEXT DEFAULT '',
            status TEXT DEFAULT 'sent',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS scheduled_reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            report_type TEXT NOT NULL DEFAULT 'weekly',
            email_to TEXT NOT NULL,
            last_sent TEXT DEFAULT '',
            active INTEGER DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS smart_alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            alert_type TEXT NOT NULL,
            title TEXT NOT NULL,
            message TEXT DEFAULT '',
            severity TEXT DEFAULT 'info',
            is_read INTEGER DEFAULT 0,
            related_id INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS warranties (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            invoice_id INTEGER NOT NULL,
            car_id INTEGER NOT NULL,
            customer_id INTEGER NOT NULL,
            service TEXT NOT NULL,
            warranty_days INTEGER DEFAULT 30,
            start_date TEXT NOT NULL,
            end_date TEXT NOT NULL,
            conditions TEXT DEFAULT '',
            status TEXT DEFAULT 'active',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (invoice_id) REFERENCES invoices (id),
            FOREIGN KEY (car_id) REFERENCES cars (id),
            FOREIGN KEY (customer_id) REFERENCES customers (id)
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS inspection_checklists (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            appointment_id INTEGER NOT NULL,
            car_id INTEGER NOT NULL,
            inspector TEXT DEFAULT '',
            checklist_data TEXT DEFAULT '{}',
            notes TEXT DEFAULT '',
            status TEXT DEFAULT 'pending',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (appointment_id) REFERENCES appointments (id),
            FOREIGN KEY (car_id) REFERENCES cars (id)
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS dynamic_pricing (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            service_name TEXT NOT NULL,
            car_category TEXT DEFAULT 'sedan',
            season TEXT DEFAULT 'normal',
            customer_tier TEXT DEFAULT '',
            price_modifier REAL DEFAULT 1.0,
            fixed_price REAL DEFAULT 0,
            active INTEGER DEFAULT 1
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS subscriptions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            customer_id INTEGER NOT NULL,
            plan_name TEXT NOT NULL,
            services_included TEXT DEFAULT '',
            total_sessions INTEGER DEFAULT 0,
            used_sessions INTEGER DEFAULT 0,
            price REAL DEFAULT 0,
            start_date TEXT NOT NULL,
            end_date TEXT NOT NULL,
            status TEXT DEFAULT 'active',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (customer_id) REFERENCES customers (id)
        )
    ''')

    # ─── Phase 9: Enterprise Grade Tables ───
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS crm_followups (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            customer_id INTEGER NOT NULL,
            type TEXT DEFAULT 'absence',
            reason TEXT DEFAULT '',
            scheduled_date TEXT NOT NULL,
            status TEXT DEFAULT 'pending',
            notes TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            completed_at TEXT DEFAULT '',
            FOREIGN KEY (customer_id) REFERENCES customers (id)
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS employee_shifts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            username TEXT NOT NULL,
            shift_date TEXT NOT NULL,
            start_time TEXT DEFAULT '08:00',
            end_time TEXT DEFAULT '17:00',
            shift_type TEXT DEFAULT 'normal',
            status TEXT DEFAULT 'scheduled',
            notes TEXT DEFAULT '',
            FOREIGN KEY (user_id) REFERENCES users (id)
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS employee_leaves (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            username TEXT NOT NULL,
            leave_type TEXT DEFAULT 'annual',
            start_date TEXT NOT NULL,
            end_date TEXT NOT NULL,
            status TEXT DEFAULT 'pending',
            reason TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users (id)
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS referrals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            referrer_id INTEGER NOT NULL,
            referred_name TEXT NOT NULL,
            referred_phone TEXT NOT NULL,
            reward_type TEXT DEFAULT 'free_wash',
            status TEXT DEFAULT 'pending',
            converted_customer_id INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (referrer_id) REFERENCES customers (id)
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS fleet_companies (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            contact_person TEXT DEFAULT '',
            phone TEXT DEFAULT '',
            email TEXT DEFAULT '',
            address TEXT DEFAULT '',
            contract_start TEXT DEFAULT '',
            contract_end TEXT DEFAULT '',
            discount_percent REAL DEFAULT 0,
            payment_terms TEXT DEFAULT 'monthly',
            notes TEXT DEFAULT '',
            active INTEGER DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS fleet_vehicles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            company_id INTEGER NOT NULL,
            car_id INTEGER NOT NULL,
            FOREIGN KEY (company_id) REFERENCES fleet_companies (id),
            FOREIGN KEY (car_id) REFERENCES cars (id),
            UNIQUE(company_id, car_id)
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS staff_notes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            entity_type TEXT NOT NULL,
            entity_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            username TEXT NOT NULL,
            note TEXT NOT NULL,
            priority TEXT DEFAULT 'normal',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS dashboard_widgets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            widget_type TEXT NOT NULL,
            position INTEGER DEFAULT 0,
            config TEXT DEFAULT '{}',
            visible INTEGER DEFAULT 1,
            FOREIGN KEY (user_id) REFERENCES users (id)
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
        ("ALTER TABLE cars ADD COLUMN mileage INTEGER DEFAULT 0", None),
        ("ALTER TABLE cars ADD COLUMN last_oil_change TEXT DEFAULT ''", None),
        ("ALTER TABLE cars ADD COLUMN next_service_date TEXT DEFAULT ''", None),
        ("ALTER TABLE users ADD COLUMN permissions TEXT DEFAULT ''", None),
        ("ALTER TABLE invoices ADD COLUMN coupon_id INTEGER DEFAULT 0", None),
        ("ALTER TABLE customers ADD COLUMN password_hash TEXT DEFAULT ''", None),
        ("ALTER TABLE invoices ADD COLUMN total_paid REAL DEFAULT 0", None),
        ("ALTER TABLE invoices ADD COLUMN remaining REAL DEFAULT 0", None),
        ("ALTER TABLE invoices ADD COLUMN installments INTEGER DEFAULT 0", None),
        ("ALTER TABLE quotes ADD COLUMN converted_invoice_id INTEGER DEFAULT 0", None),
        ("ALTER TABLE quotes ADD COLUMN valid_until TEXT DEFAULT ''", None),
        ("ALTER TABLE quotes ADD COLUMN items TEXT DEFAULT ''", None),
        ("ALTER TABLE customers ADD COLUMN preferred_lang TEXT DEFAULT 'fr'", None),
        ("ALTER TABLE settings ADD COLUMN value2 TEXT DEFAULT ''", None),
        ("ALTER TABLE invoices ADD COLUMN warranty_days INTEGER DEFAULT 0", None),
        ("ALTER TABLE invoices ADD COLUMN terms TEXT DEFAULT ''", None),
        ("ALTER TABLE services ADD COLUMN category_pricing TEXT DEFAULT ''", None),
        ("ALTER TABLE customers ADD COLUMN car_category TEXT DEFAULT 'sedan'", None),
        ("ALTER TABLE customers ADD COLUMN referred_by INTEGER DEFAULT 0", None),
        ("ALTER TABLE customers ADD COLUMN company_id INTEGER DEFAULT 0", None),
        ("ALTER TABLE customers ADD COLUMN last_visit TEXT DEFAULT ''", None),
        ("ALTER TABLE customers ADD COLUMN total_visits INTEGER DEFAULT 0", None),
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
        "CREATE INDEX IF NOT EXISTS idx_coupons_code ON coupons(code)",
        "CREATE INDEX IF NOT EXISTS idx_suppliers_name ON suppliers(name)",
        "CREATE INDEX IF NOT EXISTS idx_purchase_orders_supplier ON purchase_orders(supplier_id)",
        "CREATE INDEX IF NOT EXISTS idx_waiting_queue_status ON waiting_queue(status)",
        "CREATE INDEX IF NOT EXISTS idx_payments_invoice ON payments(invoice_id)",
        "CREATE INDEX IF NOT EXISTS idx_surveys_token ON surveys(token)",
        "CREATE INDEX IF NOT EXISTS idx_car_photos_car ON car_photos(car_id)",
        "CREATE INDEX IF NOT EXISTS idx_online_bookings_status ON online_bookings(status)",
        "CREATE INDEX IF NOT EXISTS idx_maintenance_plans_car ON maintenance_plans(car_id)",
        "CREATE INDEX IF NOT EXISTS idx_smart_alerts_read ON smart_alerts(is_read)",
        "CREATE INDEX IF NOT EXISTS idx_warranties_customer ON warranties(customer_id)",
        "CREATE INDEX IF NOT EXISTS idx_warranties_end ON warranties(end_date)",
        "CREATE INDEX IF NOT EXISTS idx_subscriptions_customer ON subscriptions(customer_id)",
        "CREATE INDEX IF NOT EXISTS idx_inspection_appointment ON inspection_checklists(appointment_id)",
        "CREATE INDEX IF NOT EXISTS idx_crm_followups_customer ON crm_followups(customer_id)",
        "CREATE INDEX IF NOT EXISTS idx_crm_followups_date ON crm_followups(scheduled_date)",
        "CREATE INDEX IF NOT EXISTS idx_employee_shifts_date ON employee_shifts(shift_date)",
        "CREATE INDEX IF NOT EXISTS idx_referrals_referrer ON referrals(referrer_id)",
        "CREATE INDEX IF NOT EXISTS idx_fleet_vehicles_company ON fleet_vehicles(company_id)",
        "CREATE INDEX IF NOT EXISTS idx_staff_notes_entity ON staff_notes(entity_type, entity_id)",
        "CREATE INDEX IF NOT EXISTS idx_dashboard_widgets_user ON dashboard_widgets(user_id)",
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
        ('max_daily_appointments', '10'),
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