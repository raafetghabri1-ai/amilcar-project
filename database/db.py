import sqlite3
import os
from contextlib import contextmanager

# In production (Fly.io), DB lives on persistent /data volume
# In development, it stays in database/amilcar.db
_DATA_DIR = os.environ.get('DATA_DIR', os.path.join(os.path.dirname(os.path.abspath(__file__))))
DB_PATH = os.path.join(_DATA_DIR, 'amilcar.db')
os.makedirs(_DATA_DIR, exist_ok=True)

def connect():
    connection = sqlite3.connect(DB_PATH)
    return connection

@contextmanager
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute("PRAGMA cache_size = -8000")  # 8MB cache
    conn.execute("PRAGMA temp_store = MEMORY")
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
            username TEXT NOT NULL DEFAULT '',
            action TEXT NOT NULL DEFAULT '',
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            date TEXT NOT NULL,
            clock_in TEXT DEFAULT '',
            clock_out TEXT DEFAULT '',
            notes TEXT DEFAULT '',
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

    # ─── Phase 10: World-Class Operations Tables ───
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS rfm_segments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            customer_id INTEGER NOT NULL UNIQUE,
            recency_score INTEGER DEFAULT 0,
            frequency_score INTEGER DEFAULT 0,
            monetary_score INTEGER DEFAULT 0,
            rfm_score INTEGER DEFAULT 0,
            segment TEXT DEFAULT 'new',
            last_calculated TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (customer_id) REFERENCES customers (id)
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS marketing_campaigns (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            type TEXT DEFAULT 'manual',
            trigger_type TEXT DEFAULT '',
            trigger_value TEXT DEFAULT '',
            target_segment TEXT DEFAULT 'all',
            message_template TEXT DEFAULT '',
            channel TEXT DEFAULT 'sms',
            status TEXT DEFAULT 'active',
            sent_count INTEGER DEFAULT 0,
            last_run TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS campaign_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            campaign_id INTEGER NOT NULL,
            customer_id INTEGER NOT NULL,
            sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            status TEXT DEFAULT 'sent',
            FOREIGN KEY (campaign_id) REFERENCES marketing_campaigns (id),
            FOREIGN KEY (customer_id) REFERENCES customers (id)
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS service_bays (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            bay_type TEXT DEFAULT 'general',
            capacity INTEGER DEFAULT 1,
            active INTEGER DEFAULT 1
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS bay_bookings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            bay_id INTEGER NOT NULL,
            appointment_id INTEGER NOT NULL,
            start_time TEXT NOT NULL,
            end_time TEXT NOT NULL,
            date TEXT NOT NULL,
            FOREIGN KEY (bay_id) REFERENCES service_bays (id),
            FOREIGN KEY (appointment_id) REFERENCES appointments (id)
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS monthly_pnl (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            month TEXT NOT NULL,
            total_revenue REAL DEFAULT 0,
            total_expenses REAL DEFAULT 0,
            material_costs REAL DEFAULT 0,
            labor_costs REAL DEFAULT 0,
            other_costs REAL DEFAULT 0,
            net_profit REAL DEFAULT 0,
            calculated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(month)
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS employee_targets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            username TEXT NOT NULL,
            month TEXT NOT NULL,
            target_revenue REAL DEFAULT 0,
            actual_revenue REAL DEFAULT 0,
            target_jobs INTEGER DEFAULT 0,
            actual_jobs INTEGER DEFAULT 0,
            commission_rate REAL DEFAULT 0,
            commission_earned REAL DEFAULT 0,
            bonus REAL DEFAULT 0,
            FOREIGN KEY (user_id) REFERENCES users (id),
            UNIQUE(user_id, month)
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS digital_inspections (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            appointment_id INTEGER NOT NULL,
            car_id INTEGER NOT NULL,
            inspector TEXT DEFAULT '',
            items TEXT DEFAULT '[]',
            overall_status TEXT DEFAULT 'pending',
            customer_notified INTEGER DEFAULT 0,
            token TEXT DEFAULT '',
            notes TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (appointment_id) REFERENCES appointments (id),
            FOREIGN KEY (car_id) REFERENCES cars (id)
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS auto_purchase_orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            inventory_id INTEGER NOT NULL,
            supplier_id INTEGER,
            item_name TEXT NOT NULL,
            current_qty INTEGER DEFAULT 0,
            min_qty INTEGER DEFAULT 0,
            order_qty INTEGER DEFAULT 0,
            status TEXT DEFAULT 'suggested',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (inventory_id) REFERENCES inventory (id)
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS seasonal_campaigns (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            season TEXT DEFAULT 'summer',
            start_date TEXT NOT NULL,
            end_date TEXT NOT NULL,
            discount_percent REAL DEFAULT 0,
            target_services TEXT DEFAULT '',
            message TEXT DEFAULT '',
            status TEXT DEFAULT 'draft',
            sent_count INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS api_keys (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            api_key TEXT NOT NULL UNIQUE,
            permissions TEXT DEFAULT 'read',
            active INTEGER DEFAULT 1,
            last_used TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS webhooks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            url TEXT NOT NULL,
            events TEXT DEFAULT '',
            secret TEXT DEFAULT '',
            active INTEGER DEFAULT 1,
            last_triggered TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # ── Phase 11: Global Excellence ──

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS branches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            address TEXT DEFAULT '',
            phone TEXT DEFAULT '',
            manager TEXT DEFAULT '',
            active INTEGER DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS branch_transfers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            from_branch INTEGER NOT NULL,
            to_branch INTEGER NOT NULL,
            item_type TEXT DEFAULT 'inventory',
            item_id INTEGER NOT NULL,
            quantity INTEGER DEFAULT 1,
            notes TEXT DEFAULT '',
            status TEXT DEFAULT 'pending',
            created_by INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (from_branch) REFERENCES branches (id),
            FOREIGN KEY (to_branch) REFERENCES branches (id)
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS vin_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            car_id INTEGER NOT NULL,
            vin TEXT NOT NULL,
            decoded_make TEXT DEFAULT '',
            decoded_model TEXT DEFAULT '',
            decoded_year TEXT DEFAULT '',
            decoded_engine TEXT DEFAULT '',
            decoded_body TEXT DEFAULT '',
            decoded_fuel TEXT DEFAULT '',
            raw_data TEXT DEFAULT '{}',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (car_id) REFERENCES cars (id)
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS customer_timeline (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            customer_id INTEGER NOT NULL,
            event_type TEXT NOT NULL,
            event_title TEXT DEFAULT '',
            event_detail TEXT DEFAULT '',
            reference_id INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (customer_id) REFERENCES customers (id)
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS insurance_companies (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            contact_person TEXT DEFAULT '',
            phone TEXT DEFAULT '',
            email TEXT DEFAULT '',
            address TEXT DEFAULT '',
            contract_number TEXT DEFAULT '',
            discount_rate REAL DEFAULT 0,
            active INTEGER DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS insurance_claims (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            customer_id INTEGER NOT NULL,
            car_id INTEGER NOT NULL,
            insurance_id INTEGER NOT NULL,
            claim_number TEXT DEFAULT '',
            accident_date TEXT DEFAULT '',
            description TEXT DEFAULT '',
            estimated_cost REAL DEFAULT 0,
            approved_amount REAL DEFAULT 0,
            invoice_id INTEGER DEFAULT 0,
            status TEXT DEFAULT 'submitted',
            documents TEXT DEFAULT '[]',
            notes TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (customer_id) REFERENCES customers (id),
            FOREIGN KEY (car_id) REFERENCES cars (id),
            FOREIGN KEY (insurance_id) REFERENCES insurance_companies (id)
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS quality_checks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            appointment_id INTEGER NOT NULL,
            inspector_id INTEGER DEFAULT 0,
            checklist TEXT DEFAULT '[]',
            overall_score INTEGER DEFAULT 0,
            nps_score INTEGER DEFAULT 0,
            nps_comment TEXT DEFAULT '',
            customer_notified INTEGER DEFAULT 0,
            followup_needed INTEGER DEFAULT 0,
            followup_notes TEXT DEFAULT '',
            status TEXT DEFAULT 'pending',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (appointment_id) REFERENCES appointments (id)
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS vehicle_documents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            car_id INTEGER NOT NULL,
            doc_type TEXT NOT NULL,
            doc_name TEXT DEFAULT '',
            file_path TEXT DEFAULT '',
            expiry_date TEXT DEFAULT '',
            notes TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (car_id) REFERENCES cars (id)
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS cashflow_projections (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            month TEXT NOT NULL,
            projected_income REAL DEFAULT 0,
            projected_expenses REAL DEFAULT 0,
            actual_income REAL DEFAULT 0,
            actual_expenses REAL DEFAULT 0,
            notes TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(month)
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS vip_levels (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            min_spend REAL DEFAULT 0,
            discount_percent REAL DEFAULT 0,
            perks TEXT DEFAULT '',
            color TEXT DEFAULT '#D4AF37',
            icon TEXT DEFAULT '⭐',
            sort_order INTEGER DEFAULT 0
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS notifications_center (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER DEFAULT 0,
            title TEXT NOT NULL,
            message TEXT DEFAULT '',
            notif_type TEXT DEFAULT 'info',
            link TEXT DEFAULT '',
            is_read INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users (id)
        )
    ''')

    # ── Phase 12: Operational Intelligence ──

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS tickets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            customer_id INTEGER NOT NULL,
            subject TEXT NOT NULL,
            description TEXT DEFAULT '',
            category TEXT DEFAULT 'general',
            priority TEXT DEFAULT 'medium',
            sla_hours INTEGER DEFAULT 48,
            sla_deadline TEXT DEFAULT '',
            assigned_to INTEGER DEFAULT 0,
            status TEXT DEFAULT 'open',
            resolution TEXT DEFAULT '',
            satisfaction_score INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT '',
            closed_at TEXT DEFAULT '',
            FOREIGN KEY (customer_id) REFERENCES customers (id)
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS ticket_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticket_id INTEGER NOT NULL,
            sender_type TEXT DEFAULT 'staff',
            sender_id INTEGER DEFAULT 0,
            message TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (ticket_id) REFERENCES tickets (id)
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS churn_predictions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            customer_id INTEGER NOT NULL,
            risk_score REAL DEFAULT 0,
            risk_level TEXT DEFAULT 'low',
            days_since_last_visit INTEGER DEFAULT 0,
            avg_visit_interval REAL DEFAULT 0,
            predicted_churn_date TEXT DEFAULT '',
            action_taken TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (customer_id) REFERENCES customers (id)
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS accounting_entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            entry_date TEXT NOT NULL,
            account_code TEXT NOT NULL,
            account_name TEXT DEFAULT '',
            debit REAL DEFAULT 0,
            credit REAL DEFAULT 0,
            description TEXT DEFAULT '',
            reference_type TEXT DEFAULT '',
            reference_id INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS maintenance_contracts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            customer_id INTEGER NOT NULL,
            car_id INTEGER NOT NULL,
            contract_name TEXT DEFAULT '',
            start_date TEXT NOT NULL,
            end_date TEXT NOT NULL,
            total_visits INTEGER DEFAULT 4,
            used_visits INTEGER DEFAULT 0,
            included_services TEXT DEFAULT '',
            price REAL DEFAULT 0,
            paid REAL DEFAULT 0,
            status TEXT DEFAULT 'active',
            notes TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (customer_id) REFERENCES customers (id),
            FOREIGN KEY (car_id) REFERENCES cars (id)
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS capacity_planning (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            total_bays INTEGER DEFAULT 0,
            total_technicians INTEGER DEFAULT 0,
            available_hours REAL DEFAULT 0,
            booked_hours REAL DEFAULT 0,
            utilization_pct REAL DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(date)
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS team_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sender_id INTEGER NOT NULL,
            recipient_id INTEGER DEFAULT 0,
            channel TEXT DEFAULT 'general',
            message TEXT NOT NULL,
            is_read INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (sender_id) REFERENCES users (id)
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER DEFAULT 0,
            username TEXT DEFAULT '',
            action TEXT NOT NULL,
            entity_type TEXT DEFAULT '',
            entity_id INTEGER DEFAULT 0,
            old_value TEXT DEFAULT '',
            new_value TEXT DEFAULT '',
            ip_address TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
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
        ("ALTER TABLE customers ADD COLUMN rfm_segment TEXT DEFAULT ''", None),
        ("ALTER TABLE customers ADD COLUMN birthday TEXT DEFAULT ''", None),
        ("ALTER TABLE appointments ADD COLUMN bay_id INTEGER DEFAULT 0", None),
        ("ALTER TABLE users ADD COLUMN monthly_target REAL DEFAULT 0", None),
        ("ALTER TABLE users ADD COLUMN commission_rate REAL DEFAULT 0", None),
        ("ALTER TABLE invoices ADD COLUMN created_at TEXT DEFAULT ''", None),
        ("ALTER TABLE customers ADD COLUMN vip_level TEXT DEFAULT ''", None),
        ("ALTER TABLE customers ADD COLUMN total_spent REAL DEFAULT 0", None),
        ("ALTER TABLE customers ADD COLUMN insurance_id INTEGER DEFAULT 0", None),
        ("ALTER TABLE appointments ADD COLUMN branch_id INTEGER DEFAULT 0", None),
        ("ALTER TABLE invoices ADD COLUMN branch_id INTEGER DEFAULT 0", None),
        ("ALTER TABLE invoices ADD COLUMN insurance_claim_id INTEGER DEFAULT 0", None),
        ("ALTER TABLE users ADD COLUMN branch_id INTEGER DEFAULT 0", None),
        ("ALTER TABLE cars ADD COLUMN vin TEXT DEFAULT ''", None),
        ("ALTER TABLE inventory ADD COLUMN branch_id INTEGER DEFAULT 0", None),
        ("ALTER TABLE customers ADD COLUMN churn_risk TEXT DEFAULT ''", None),
        ("ALTER TABLE customers ADD COLUMN last_churn_check TEXT DEFAULT ''", None),
        ("ALTER TABLE appointments ADD COLUMN contract_id INTEGER DEFAULT 0", None),
        # Phase 13 migrations
        ("ALTER TABLE cars ADD COLUMN vehicle_type TEXT DEFAULT 'voiture'", None),
        ("ALTER TABLE cars ADD COLUMN color TEXT DEFAULT ''", None),
        ("ALTER TABLE cars ADD COLUMN year INTEGER DEFAULT 0", None),
        ("ALTER TABLE cars ADD COLUMN engine_size TEXT DEFAULT ''", None),
        # Phase 14 migrations
        ("ALTER TABLE cars ADD COLUMN qr_token TEXT DEFAULT ''", None),
        ("ALTER TABLE appointments ADD COLUMN upsell_suggestions TEXT DEFAULT ''", None),
        ("ALTER TABLE appointments ADD COLUMN upsell_accepted TEXT DEFAULT ''", None),
        # Phase 15 migrations
        ("ALTER TABLE customers ADD COLUMN wallet_balance REAL DEFAULT 0", None),
        ("ALTER TABLE customers ADD COLUMN birthday TEXT DEFAULT ''", None),
        ("ALTER TABLE customers ADD COLUMN nps_score INTEGER DEFAULT 0", None),
        ("ALTER TABLE customers ADD COLUMN loyalty_level TEXT DEFAULT 'bronze'", None),
        ("ALTER TABLE customers ADD COLUMN loyalty_points_total INTEGER DEFAULT 0", None),
        ("ALTER TABLE users ADD COLUMN commission_rate REAL DEFAULT 0", None),
        ("ALTER TABLE users ADD COLUMN points INTEGER DEFAULT 0", None),
        ("ALTER TABLE users ADD COLUMN badges TEXT DEFAULT ''", None),
        ("ALTER TABLE services ADD COLUMN estimated_minutes INTEGER DEFAULT 60", None),
        ("ALTER TABLE appointments ADD COLUMN actual_start TEXT DEFAULT ''", None),
        ("ALTER TABLE appointments ADD COLUMN actual_end TEXT DEFAULT ''", None),
        ("ALTER TABLE appointments ADD COLUMN assigned_employee_id INTEGER DEFAULT 0", None),
        # Phase 16 migrations
        ("ALTER TABLE flash_sales ADD COLUMN description TEXT DEFAULT ''", None),
        ("ALTER TABLE flash_sales ADD COLUMN banner_color TEXT DEFAULT '#ff6b35'", None),
        ("ALTER TABLE invoices ADD COLUMN commission_paid REAL DEFAULT 0", None),
        ("ALTER TABLE users ADD COLUMN specialties TEXT DEFAULT ''", None),
        ("ALTER TABLE users ADD COLUMN availability TEXT DEFAULT ''", None),
        # Phase 17 migrations
        ("ALTER TABLE appointments ADD COLUMN waitlist_notified INTEGER DEFAULT 0", None),
        ("ALTER TABLE suppliers ADD COLUMN rating REAL DEFAULT 0", None),
        ("ALTER TABLE suppliers ADD COLUMN delivery_score REAL DEFAULT 0", None),
        ("ALTER TABLE suppliers ADD COLUMN quality_score REAL DEFAULT 0", None),
        ("ALTER TABLE suppliers ADD COLUMN price_score REAL DEFAULT 0", None),
        ("ALTER TABLE suppliers ADD COLUMN total_orders INTEGER DEFAULT 0", None),
        ("ALTER TABLE services ADD COLUMN cost_products REAL DEFAULT 0", None),
        ("ALTER TABLE services ADD COLUMN cost_labor_minutes INTEGER DEFAULT 0", None),
        ("ALTER TABLE settings ADD COLUMN category TEXT DEFAULT 'general'", None),
        # Phase 25: Service Catalog Enhancement
        ("ALTER TABLE services ADD COLUMN category TEXT DEFAULT ''", None),
        ("ALTER TABLE services ADD COLUMN description TEXT DEFAULT ''", None),
        ("ALTER TABLE services ADD COLUMN icon TEXT DEFAULT ''", None),
        ("ALTER TABLE services ADD COLUMN sort_order INTEGER DEFAULT 0", None),
        ("ALTER TABLE services ADD COLUMN includes TEXT DEFAULT ''", None),
        ("ALTER TABLE services ADD COLUMN duration_label TEXT DEFAULT ''", None),
        ("ALTER TABLE services ADD COLUMN popular INTEGER DEFAULT 0", None),
        ("ALTER TABLE services ADD COLUMN image_url TEXT DEFAULT ''", None),
        # Phase 37: Soft Delete support
        ("ALTER TABLE customers ADD COLUMN is_deleted INTEGER DEFAULT 0", None),
        ("ALTER TABLE customers ADD COLUMN deleted_at TEXT DEFAULT ''", None),
        ("ALTER TABLE cars ADD COLUMN is_deleted INTEGER DEFAULT 0", None),
        ("ALTER TABLE cars ADD COLUMN deleted_at TEXT DEFAULT ''", None),
        ("ALTER TABLE appointments ADD COLUMN is_deleted INTEGER DEFAULT 0", None),
        ("ALTER TABLE appointments ADD COLUMN deleted_at TEXT DEFAULT ''", None),
        ("ALTER TABLE invoices ADD COLUMN is_deleted INTEGER DEFAULT 0", None),
        ("ALTER TABLE invoices ADD COLUMN deleted_at TEXT DEFAULT ''", None),
        ("ALTER TABLE expenses ADD COLUMN is_deleted INTEGER DEFAULT 0", None),
        ("ALTER TABLE expenses ADD COLUMN deleted_at TEXT DEFAULT ''", None),
        ("ALTER TABLE quotes ADD COLUMN is_deleted INTEGER DEFAULT 0", None),
        ("ALTER TABLE quotes ADD COLUMN deleted_at TEXT DEFAULT ''", None),
        # Phase 5: Password reset tokens
        ("ALTER TABLE users ADD COLUMN email TEXT DEFAULT ''", None),
        # Phase 8: time_tracking columns
        ("ALTER TABLE time_tracking ADD COLUMN clock_in TEXT DEFAULT ''", None),
        ("ALTER TABLE time_tracking ADD COLUMN clock_out TEXT DEFAULT ''", None),
        ("ALTER TABLE time_tracking ADD COLUMN notes TEXT DEFAULT ''", None),
    ]
    import logging
    _mig_log = logging.getLogger('amilcar.migrations')
    for sql, _ in migrations:
        try:
            cursor.execute(sql)
        except Exception as e:
            msg = str(e)
            if 'duplicate column' not in msg and 'already exists' not in msg:
                _mig_log.warning('Migration failed: %s — %s', sql[:60], msg)

    # ─── Password Reset Tokens ───
    cursor.execute('''CREATE TABLE IF NOT EXISTS password_reset_tokens (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        token TEXT NOT NULL UNIQUE,
        created_at TEXT NOT NULL,
        used INTEGER DEFAULT 0
    )''')

    # ─── Phase 13: Premium Car Care Intelligence Tables ───

    # Galerie Avant/Après
    cursor.execute('''CREATE TABLE IF NOT EXISTS vehicle_gallery (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        car_id INTEGER NOT NULL,
        appointment_id INTEGER DEFAULT 0,
        photo_type TEXT DEFAULT 'before',
        photo_path TEXT NOT NULL,
        caption TEXT DEFAULT '',
        is_portfolio INTEGER DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (car_id) REFERENCES cars(id)
    )''')

    # Suivi Traitements & Garanties Produits
    cursor.execute('''CREATE TABLE IF NOT EXISTS treatments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        car_id INTEGER NOT NULL,
        customer_id INTEGER NOT NULL,
        appointment_id INTEGER DEFAULT 0,
        treatment_type TEXT NOT NULL,
        product_used TEXT DEFAULT '',
        brand TEXT DEFAULT '',
        warranty_years REAL DEFAULT 0,
        warranty_expiry TEXT DEFAULT '',
        applied_date TEXT NOT NULL,
        next_renewal TEXT DEFAULT '',
        notes TEXT DEFAULT '',
        status TEXT DEFAULT 'active',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (car_id) REFERENCES cars(id),
        FOREIGN KEY (customer_id) REFERENCES customers(id)
    )''')

    # Fiche État Véhicule (Vehicle Condition Report)
    cursor.execute('''CREATE TABLE IF NOT EXISTS vehicle_conditions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        car_id INTEGER NOT NULL,
        appointment_id INTEGER NOT NULL,
        condition_type TEXT DEFAULT 'reception',
        exterior_state TEXT DEFAULT '',
        interior_state TEXT DEFAULT '',
        scratches TEXT DEFAULT '',
        dents TEXT DEFAULT '',
        paint_condition TEXT DEFAULT '',
        leather_condition TEXT DEFAULT '',
        dashboard_condition TEXT DEFAULT '',
        wheels_condition TEXT DEFAULT '',
        photos TEXT DEFAULT '',
        notes TEXT DEFAULT '',
        customer_signature INTEGER DEFAULT 0,
        created_by INTEGER DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (car_id) REFERENCES cars(id),
        FOREIGN KEY (appointment_id) REFERENCES appointments(id)
    )''')

    # Suivi Produits & Consommation
    cursor.execute('''CREATE TABLE IF NOT EXISTS product_usage (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        appointment_id INTEGER NOT NULL,
        product_id INTEGER DEFAULT 0,
        product_name TEXT NOT NULL,
        quantity_used REAL DEFAULT 0,
        unit TEXT DEFAULT 'ml',
        unit_cost REAL DEFAULT 0,
        total_cost REAL DEFAULT 0,
        vehicle_type TEXT DEFAULT 'voiture',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (appointment_id) REFERENCES appointments(id)
    )''')

    # Packs Detailing
    cursor.execute('''CREATE TABLE IF NOT EXISTS detailing_packs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        description TEXT DEFAULT '',
        vehicle_type TEXT DEFAULT 'all',
        included_services TEXT DEFAULT '',
        regular_price REAL DEFAULT 0,
        pack_price REAL DEFAULT 0,
        duration_minutes INTEGER DEFAULT 60,
        is_active INTEGER DEFAULT 1,
        photo_path TEXT DEFAULT '',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')

    # Abonnements Lavage (Wash Subscriptions)
    cursor.execute('''CREATE TABLE IF NOT EXISTS wash_subscriptions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        customer_id INTEGER NOT NULL,
        car_id INTEGER DEFAULT 0,
        plan_name TEXT NOT NULL,
        plan_type TEXT DEFAULT 'monthly',
        vehicle_type TEXT DEFAULT 'voiture',
        included_washes INTEGER DEFAULT 4,
        used_washes INTEGER DEFAULT 0,
        included_services TEXT DEFAULT '',
        price REAL DEFAULT 0,
        start_date TEXT NOT NULL,
        end_date TEXT NOT NULL,
        auto_renew INTEGER DEFAULT 0,
        status TEXT DEFAULT 'active',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (customer_id) REFERENCES customers(id)
    )''')

    # Avis Clients (Client Reviews with Photos)
    cursor.execute('''CREATE TABLE IF NOT EXISTS client_reviews (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        customer_id INTEGER NOT NULL,
        appointment_id INTEGER DEFAULT 0,
        car_id INTEGER DEFAULT 0,
        rating INTEGER DEFAULT 5,
        comment TEXT DEFAULT '',
        photos TEXT DEFAULT '',
        service_type TEXT DEFAULT '',
        is_public INTEGER DEFAULT 1,
        is_featured INTEGER DEFAULT 0,
        response TEXT DEFAULT '',
        response_date TEXT DEFAULT '',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (customer_id) REFERENCES customers(id)
    )''')

    # Suivi Temps Réel (Real-Time Vehicle Status)
    cursor.execute('''CREATE TABLE IF NOT EXISTS vehicle_status (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        appointment_id INTEGER NOT NULL,
        car_id INTEGER NOT NULL,
        current_step TEXT DEFAULT 'reception',
        progress_pct INTEGER DEFAULT 0,
        started_at TEXT DEFAULT '',
        estimated_end TEXT DEFAULT '',
        assigned_tech INTEGER DEFAULT 0,
        bay_number INTEGER DEFAULT 0,
        last_update TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (appointment_id) REFERENCES appointments(id),
        FOREIGN KEY (car_id) REFERENCES cars(id)
    )''')

    cursor.execute('''CREATE TABLE IF NOT EXISTS status_updates (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        appointment_id INTEGER NOT NULL,
        step_name TEXT NOT NULL,
        status TEXT DEFAULT 'pending',
        started_at TEXT DEFAULT '',
        completed_at TEXT DEFAULT '',
        tech_id INTEGER DEFAULT 0,
        notes TEXT DEFAULT '',
        photo_path TEXT DEFAULT '',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (appointment_id) REFERENCES appointments(id)
    )''')

    # ─── Phase 14: Smart Car Care Automation Tables ───

    # Upsell Rules Engine
    cursor.execute('''CREATE TABLE IF NOT EXISTS upsell_rules (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        trigger_type TEXT NOT NULL,
        trigger_value TEXT DEFAULT '',
        suggestion_text TEXT NOT NULL,
        discount_pct REAL DEFAULT 0,
        target_service TEXT DEFAULT '',
        vehicle_types TEXT DEFAULT 'all',
        is_active INTEGER DEFAULT 1,
        times_shown INTEGER DEFAULT 0,
        times_accepted INTEGER DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')

    # Online Booking Pro
    cursor.execute('''CREATE TABLE IF NOT EXISTS booking_slots (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        day_of_week INTEGER DEFAULT 0,
        start_time TEXT NOT NULL,
        end_time TEXT NOT NULL,
        max_bookings INTEGER DEFAULT 3,
        vehicle_types TEXT DEFAULT 'all',
        is_active INTEGER DEFAULT 1
    )''')

    # QR Vehicle History (qr_token on cars via migration)

    # Quality Checklists per Service
    cursor.execute('''CREATE TABLE IF NOT EXISTS service_checklists (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        service_name TEXT NOT NULL,
        vehicle_type TEXT DEFAULT 'all',
        checklist_items TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')

    cursor.execute('''CREATE TABLE IF NOT EXISTS checklist_results (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        appointment_id INTEGER NOT NULL,
        checklist_id INTEGER NOT NULL,
        results TEXT DEFAULT '',
        score INTEGER DEFAULT 0,
        total_items INTEGER DEFAULT 0,
        checked_by INTEGER DEFAULT 0,
        notes TEXT DEFAULT '',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (appointment_id) REFERENCES appointments(id)
    )''')

    # Smart Reminders
    cursor.execute('''CREATE TABLE IF NOT EXISTS smart_reminders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        customer_id INTEGER NOT NULL,
        car_id INTEGER DEFAULT 0,
        reminder_type TEXT NOT NULL,
        title TEXT NOT NULL,
        message TEXT DEFAULT '',
        due_date TEXT NOT NULL,
        status TEXT DEFAULT 'pending',
        sent_at TEXT DEFAULT '',
        reference_type TEXT DEFAULT '',
        reference_id INTEGER DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (customer_id) REFERENCES customers(id)
    )''')

    # Pack Configurator Selections (online)
    cursor.execute('''CREATE TABLE IF NOT EXISTS pack_configurations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        customer_name TEXT DEFAULT '',
        customer_phone TEXT DEFAULT '',
        customer_email TEXT DEFAULT '',
        vehicle_type TEXT DEFAULT 'voiture',
        selected_services TEXT NOT NULL,
        total_regular REAL DEFAULT 0,
        total_discounted REAL DEFAULT 0,
        discount_pct REAL DEFAULT 0,
        status TEXT DEFAULT 'pending',
        notes TEXT DEFAULT '',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')

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
        "CREATE INDEX IF NOT EXISTS idx_rfm_segments_customer ON rfm_segments(customer_id)",
        "CREATE INDEX IF NOT EXISTS idx_rfm_segments_segment ON rfm_segments(segment)",
        "CREATE INDEX IF NOT EXISTS idx_campaign_log_campaign ON campaign_log(campaign_id)",
        "CREATE INDEX IF NOT EXISTS idx_bay_bookings_date ON bay_bookings(date)",
        "CREATE INDEX IF NOT EXISTS idx_bay_bookings_bay ON bay_bookings(bay_id)",
        "CREATE INDEX IF NOT EXISTS idx_employee_targets_user ON employee_targets(user_id)",
        "CREATE INDEX IF NOT EXISTS idx_digital_inspections_appt ON digital_inspections(appointment_id)",
        "CREATE INDEX IF NOT EXISTS idx_api_keys_key ON api_keys(api_key)",
        "CREATE INDEX IF NOT EXISTS idx_branches_active ON branches(active)",
        "CREATE INDEX IF NOT EXISTS idx_branch_transfers_from ON branch_transfers(from_branch)",
        "CREATE INDEX IF NOT EXISTS idx_branch_transfers_to ON branch_transfers(to_branch)",
        "CREATE INDEX IF NOT EXISTS idx_vin_records_car ON vin_records(car_id)",
        "CREATE INDEX IF NOT EXISTS idx_vin_records_vin ON vin_records(vin)",
        "CREATE INDEX IF NOT EXISTS idx_customer_timeline_customer ON customer_timeline(customer_id)",
        "CREATE INDEX IF NOT EXISTS idx_customer_timeline_type ON customer_timeline(event_type)",
        "CREATE INDEX IF NOT EXISTS idx_insurance_claims_customer ON insurance_claims(customer_id)",
        "CREATE INDEX IF NOT EXISTS idx_insurance_claims_status ON insurance_claims(status)",
        "CREATE INDEX IF NOT EXISTS idx_quality_checks_appt ON quality_checks(appointment_id)",
        "CREATE INDEX IF NOT EXISTS idx_vehicle_documents_car ON vehicle_documents(car_id)",
        "CREATE INDEX IF NOT EXISTS idx_vehicle_documents_expiry ON vehicle_documents(expiry_date)",
        "CREATE INDEX IF NOT EXISTS idx_cashflow_month ON cashflow_projections(month)",
        "CREATE INDEX IF NOT EXISTS idx_notifications_user ON notifications_center(user_id)",
        "CREATE INDEX IF NOT EXISTS idx_notifications_read ON notifications_center(is_read)",
        "CREATE INDEX IF NOT EXISTS idx_tickets_customer ON tickets(customer_id)",
        "CREATE INDEX IF NOT EXISTS idx_tickets_status ON tickets(status)",
        "CREATE INDEX IF NOT EXISTS idx_tickets_priority ON tickets(priority)",
        "CREATE INDEX IF NOT EXISTS idx_ticket_messages_ticket ON ticket_messages(ticket_id)",
        "CREATE INDEX IF NOT EXISTS idx_churn_customer ON churn_predictions(customer_id)",
        "CREATE INDEX IF NOT EXISTS idx_churn_risk ON churn_predictions(risk_level)",
        "CREATE INDEX IF NOT EXISTS idx_accounting_date ON accounting_entries(entry_date)",
        "CREATE INDEX IF NOT EXISTS idx_accounting_account ON accounting_entries(account_code)",
        "CREATE INDEX IF NOT EXISTS idx_contracts_customer ON maintenance_contracts(customer_id)",
        "CREATE INDEX IF NOT EXISTS idx_contracts_status ON maintenance_contracts(status)",
        "CREATE INDEX IF NOT EXISTS idx_capacity_date ON capacity_planning(date)",
        "CREATE INDEX IF NOT EXISTS idx_team_messages_channel ON team_messages(channel)",
        "CREATE INDEX IF NOT EXISTS idx_team_messages_recipient ON team_messages(recipient_id)",
        "CREATE INDEX IF NOT EXISTS idx_audit_log_user ON audit_log(user_id)",
        "CREATE INDEX IF NOT EXISTS idx_audit_log_entity ON audit_log(entity_type, entity_id)",
        "CREATE INDEX IF NOT EXISTS idx_audit_log_date ON audit_log(created_at)",
        # Phase 13 indexes
        "CREATE INDEX IF NOT EXISTS idx_gallery_car ON vehicle_gallery(car_id)",
        "CREATE INDEX IF NOT EXISTS idx_gallery_appointment ON vehicle_gallery(appointment_id)",
        "CREATE INDEX IF NOT EXISTS idx_gallery_portfolio ON vehicle_gallery(is_portfolio)",
        "CREATE INDEX IF NOT EXISTS idx_treatments_car ON treatments(car_id)",
        "CREATE INDEX IF NOT EXISTS idx_treatments_customer ON treatments(customer_id)",
        "CREATE INDEX IF NOT EXISTS idx_treatments_status ON treatments(status)",
        "CREATE INDEX IF NOT EXISTS idx_treatments_expiry ON treatments(warranty_expiry)",
        "CREATE INDEX IF NOT EXISTS idx_conditions_car ON vehicle_conditions(car_id)",
        "CREATE INDEX IF NOT EXISTS idx_conditions_appt ON vehicle_conditions(appointment_id)",
        "CREATE INDEX IF NOT EXISTS idx_product_usage_appt ON product_usage(appointment_id)",
        "CREATE INDEX IF NOT EXISTS idx_packs_active ON detailing_packs(is_active)",
        "CREATE INDEX IF NOT EXISTS idx_packs_type ON detailing_packs(vehicle_type)",
        "CREATE INDEX IF NOT EXISTS idx_wash_subs_customer ON wash_subscriptions(customer_id)",
        "CREATE INDEX IF NOT EXISTS idx_wash_subs_status ON wash_subscriptions(status)",
        "CREATE INDEX IF NOT EXISTS idx_reviews_customer ON client_reviews(customer_id)",
        "CREATE INDEX IF NOT EXISTS idx_reviews_public ON client_reviews(is_public)",
        "CREATE INDEX IF NOT EXISTS idx_reviews_featured ON client_reviews(is_featured)",
        "CREATE INDEX IF NOT EXISTS idx_vehicle_status_appt ON vehicle_status(appointment_id)",
        "CREATE INDEX IF NOT EXISTS idx_status_updates_appt ON status_updates(appointment_id)",
        # Phase 14 indexes
        "CREATE INDEX IF NOT EXISTS idx_upsell_rules_active ON upsell_rules(is_active)",
        "CREATE INDEX IF NOT EXISTS idx_booking_slots_day ON booking_slots(day_of_week)",
        "CREATE INDEX IF NOT EXISTS idx_checklists_service ON service_checklists(service_name)",
        "CREATE INDEX IF NOT EXISTS idx_checklist_results_appt ON checklist_results(appointment_id)",
        "CREATE INDEX IF NOT EXISTS idx_smart_reminders_customer ON smart_reminders(customer_id)",
        "CREATE INDEX IF NOT EXISTS idx_smart_reminders_status ON smart_reminders(status)",
        "CREATE INDEX IF NOT EXISTS idx_smart_reminders_due ON smart_reminders(due_date)",
        "CREATE INDEX IF NOT EXISTS idx_pack_configs_status ON pack_configurations(status)",
        "CREATE INDEX IF NOT EXISTS idx_cars_qr ON cars(qr_token)",
        # Phase 15 indexes
        "CREATE INDEX IF NOT EXISTS idx_wallet_transactions_customer ON wallet_transactions(customer_id)",
        "CREATE INDEX IF NOT EXISTS idx_wallet_transactions_type ON wallet_transactions(transaction_type)",
        "CREATE INDEX IF NOT EXISTS idx_dynamic_pricing_service ON dynamic_pricing_rules(service_id)",
        "CREATE INDEX IF NOT EXISTS idx_dynamic_pricing_active ON dynamic_pricing_rules(is_active)",
        "CREATE INDEX IF NOT EXISTS idx_employee_scores_employee ON employee_gamification(employee_id)",
        "CREATE INDEX IF NOT EXISTS idx_employee_scores_month ON employee_gamification(month)",
        "CREATE INDEX IF NOT EXISTS idx_nps_surveys_customer ON nps_surveys(customer_id)",
        "CREATE INDEX IF NOT EXISTS idx_nps_surveys_date ON nps_surveys(created_at)",
        "CREATE INDEX IF NOT EXISTS idx_service_timer_appt ON service_timer(appointment_id)",
        "CREATE INDEX IF NOT EXISTS idx_stock_forecasts_product ON stock_forecasts(product_id)",
        "CREATE INDEX IF NOT EXISTS idx_monthly_goals_month ON monthly_goals(month)",
        "CREATE INDEX IF NOT EXISTS idx_whatsapp_logs_customer ON whatsapp_logs(customer_id)",
        "CREATE INDEX IF NOT EXISTS idx_loyalty_challenges_status ON loyalty_challenges(status)",
        # Phase 16 indexes
        "CREATE INDEX IF NOT EXISTS idx_commission_log_employee ON commission_log(employee_id)",
        "CREATE INDEX IF NOT EXISTS idx_commission_log_month ON commission_log(month)",
        "CREATE INDEX IF NOT EXISTS idx_revenue_daily_date ON revenue_daily(date)",
        "CREATE INDEX IF NOT EXISTS idx_channel_inbox_customer ON channel_inbox(customer_id)",
        "CREATE INDEX IF NOT EXISTS idx_channel_inbox_status ON channel_inbox(status)",
        "CREATE INDEX IF NOT EXISTS idx_import_history_type ON import_history(import_type)",
        "CREATE INDEX IF NOT EXISTS idx_health_score_date ON business_health_score(date)",
        "CREATE INDEX IF NOT EXISTS idx_report_builder_created ON report_builder(created_by)",
        "CREATE INDEX IF NOT EXISTS idx_flash_sales_active ON flash_sales(is_active)",
        "CREATE INDEX IF NOT EXISTS idx_flash_sales_dates ON flash_sales(start_datetime, end_datetime)",
        # Phase 17 indexes
        "CREATE INDEX IF NOT EXISTS idx_damage_claims_appointment ON damage_claims(appointment_id)",
        "CREATE INDEX IF NOT EXISTS idx_damage_claims_status ON damage_claims(status)",
        "CREATE INDEX IF NOT EXISTS idx_revenue_forecast_month ON revenue_forecast(month)",
        "CREATE INDEX IF NOT EXISTS idx_customer_segments_segment ON customer_segments(segment)",
        "CREATE INDEX IF NOT EXISTS idx_waitlist_date ON appointment_waitlist(preferred_date)",
        "CREATE INDEX IF NOT EXISTS idx_waitlist_status ON appointment_waitlist(status)",
        "CREATE INDEX IF NOT EXISTS idx_attendance_employee ON employee_attendance(employee_id)",
        "CREATE INDEX IF NOT EXISTS idx_attendance_date ON employee_attendance(date)",
        "CREATE INDEX IF NOT EXISTS idx_supplier_reviews_supplier ON supplier_reviews(supplier_id)",
        "CREATE INDEX IF NOT EXISTS idx_knowledge_base_category ON knowledge_base(category)",
        # Critical performance indexes
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_users_username ON users(username)",
        "CREATE INDEX IF NOT EXISTS idx_customers_name ON customers(name)",
        "CREATE INDEX IF NOT EXISTS idx_customers_phone ON customers(phone)",
        "CREATE INDEX IF NOT EXISTS idx_online_bookings_created ON online_bookings(created_at)",
        "CREATE INDEX IF NOT EXISTS idx_invoices_created ON invoices(created_at)",
        "CREATE INDEX IF NOT EXISTS idx_activity_log_created ON activity_log(created_at)",
        "CREATE INDEX IF NOT EXISTS idx_ratings_created ON ratings(created_at)",
        # Soft delete indexes
        "CREATE INDEX IF NOT EXISTS idx_customers_deleted ON customers(is_deleted)",
        "CREATE INDEX IF NOT EXISTS idx_cars_deleted ON cars(is_deleted)",
        "CREATE INDEX IF NOT EXISTS idx_appointments_deleted ON appointments(is_deleted)",
        "CREATE INDEX IF NOT EXISTS idx_invoices_deleted ON invoices(is_deleted)",
        "CREATE INDEX IF NOT EXISTS idx_expenses_deleted ON expenses(is_deleted)",
        "CREATE INDEX IF NOT EXISTS idx_quotes_deleted ON quotes(is_deleted)",
        # Phase 8: additional performance indexes
        "CREATE INDEX IF NOT EXISTS idx_appointments_assigned ON appointments(assigned_to)",
        "CREATE INDEX IF NOT EXISTS idx_appointments_time ON appointments(time)",
        "CREATE INDEX IF NOT EXISTS idx_appointments_date_status ON appointments(date, status)",
        "CREATE INDEX IF NOT EXISTS idx_invoices_status_amount ON invoices(status, amount)",
    ]
    for idx in indexes:
        try:
            cursor.execute(idx)
        except Exception as e:
            _mig_log.warning('Index failed: %s — %s', idx[:60], e)

    # ─── Phase 15: Revenue Intelligence & Client Excellence Tables ───

    # Wallet Transactions
    cursor.execute('''CREATE TABLE IF NOT EXISTS wallet_transactions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        customer_id INTEGER NOT NULL,
        transaction_type TEXT NOT NULL,
        amount REAL NOT NULL,
        balance_after REAL DEFAULT 0,
        description TEXT DEFAULT '',
        reference_type TEXT DEFAULT '',
        reference_id INTEGER DEFAULT 0,
        created_by TEXT DEFAULT 'system',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (customer_id) REFERENCES customers(id)
    )''')

    # Dynamic Pricing Rules
    cursor.execute('''CREATE TABLE IF NOT EXISTS dynamic_pricing_rules (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        service_id INTEGER DEFAULT 0,
        rule_name TEXT NOT NULL,
        rule_type TEXT NOT NULL,
        days_of_week TEXT DEFAULT '',
        hours_range TEXT DEFAULT '',
        season_start TEXT DEFAULT '',
        season_end TEXT DEFAULT '',
        price_modifier REAL DEFAULT 0,
        modifier_type TEXT DEFAULT 'percentage',
        min_price REAL DEFAULT 0,
        max_price REAL DEFAULT 0,
        is_active INTEGER DEFAULT 1,
        priority INTEGER DEFAULT 0,
        vehicle_types TEXT DEFAULT 'all',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')

    # Employee Gamification
    cursor.execute('''CREATE TABLE IF NOT EXISTS employee_gamification (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        employee_id INTEGER NOT NULL,
        month TEXT NOT NULL,
        services_completed INTEGER DEFAULT 0,
        revenue_generated REAL DEFAULT 0,
        avg_rating REAL DEFAULT 0,
        upsells_achieved INTEGER DEFAULT 0,
        on_time_pct REAL DEFAULT 0,
        total_points INTEGER DEFAULT 0,
        rank_position INTEGER DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (employee_id) REFERENCES users(id)
    )''')

    # NPS Surveys
    cursor.execute('''CREATE TABLE IF NOT EXISTS nps_surveys (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        customer_id INTEGER NOT NULL,
        appointment_id INTEGER DEFAULT 0,
        score INTEGER NOT NULL,
        category TEXT DEFAULT '',
        feedback TEXT DEFAULT '',
        follow_up_status TEXT DEFAULT 'none',
        follow_up_notes TEXT DEFAULT '',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (customer_id) REFERENCES customers(id)
    )''')

    # Service Timer
    cursor.execute('''CREATE TABLE IF NOT EXISTS service_timer (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        appointment_id INTEGER NOT NULL,
        employee_id INTEGER DEFAULT 0,
        service_name TEXT DEFAULT '',
        estimated_minutes INTEGER DEFAULT 60,
        started_at TEXT DEFAULT '',
        ended_at TEXT DEFAULT '',
        actual_minutes INTEGER DEFAULT 0,
        efficiency_pct REAL DEFAULT 0,
        notes TEXT DEFAULT '',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (appointment_id) REFERENCES appointments(id)
    )''')

    # Stock Forecasts
    cursor.execute('''CREATE TABLE IF NOT EXISTS stock_forecasts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        product_id INTEGER NOT NULL,
        product_name TEXT DEFAULT '',
        current_stock REAL DEFAULT 0,
        avg_daily_usage REAL DEFAULT 0,
        days_until_empty INTEGER DEFAULT 0,
        recommended_order REAL DEFAULT 0,
        forecast_date TEXT DEFAULT '',
        auto_order_threshold REAL DEFAULT 0,
        status TEXT DEFAULT 'ok',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')

    # Monthly Goals
    cursor.execute('''CREATE TABLE IF NOT EXISTS monthly_goals (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        month TEXT NOT NULL,
        goal_type TEXT NOT NULL,
        target_value REAL NOT NULL,
        current_value REAL DEFAULT 0,
        unit TEXT DEFAULT '',
        notes TEXT DEFAULT '',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')

    # WhatsApp Logs
    cursor.execute('''CREATE TABLE IF NOT EXISTS whatsapp_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        customer_id INTEGER NOT NULL,
        phone TEXT NOT NULL,
        message_type TEXT NOT NULL,
        message_text TEXT DEFAULT '',
        status TEXT DEFAULT 'pending',
        sent_at TEXT DEFAULT '',
        appointment_id INTEGER DEFAULT 0,
        template_name TEXT DEFAULT '',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (customer_id) REFERENCES customers(id)
    )''')

    # Loyalty Challenges
    cursor.execute('''CREATE TABLE IF NOT EXISTS loyalty_challenges (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT NOT NULL,
        description TEXT DEFAULT '',
        challenge_type TEXT NOT NULL,
        target_value REAL NOT NULL,
        reward_points INTEGER DEFAULT 0,
        reward_description TEXT DEFAULT '',
        start_date TEXT NOT NULL,
        end_date TEXT NOT NULL,
        vehicle_types TEXT DEFAULT 'all',
        status TEXT DEFAULT 'active',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')

    # Loyalty Challenge Progress
    cursor.execute('''CREATE TABLE IF NOT EXISTS loyalty_challenge_progress (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        challenge_id INTEGER NOT NULL,
        customer_id INTEGER NOT NULL,
        current_value REAL DEFAULT 0,
        completed INTEGER DEFAULT 0,
        completed_at TEXT DEFAULT '',
        reward_claimed INTEGER DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (challenge_id) REFERENCES loyalty_challenges(id),
        FOREIGN KEY (customer_id) REFERENCES customers(id)
    )''')

    # Flash Sales
    cursor.execute('''CREATE TABLE IF NOT EXISTS flash_sales (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        service_ids TEXT DEFAULT '',
        discount_pct REAL DEFAULT 0,
        start_datetime TEXT NOT NULL,
        end_datetime TEXT NOT NULL,
        max_bookings INTEGER DEFAULT 0,
        current_bookings INTEGER DEFAULT 0,
        is_active INTEGER DEFAULT 1,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')

    # ─── Phase 16: Operational Mastery & Smart Automation Tables ───

    # Commission Log
    cursor.execute('''CREATE TABLE IF NOT EXISTS commission_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        employee_id INTEGER NOT NULL,
        employee_name TEXT DEFAULT '',
        month TEXT NOT NULL,
        appointment_id INTEGER DEFAULT 0,
        invoice_id INTEGER DEFAULT 0,
        service_name TEXT DEFAULT '',
        invoice_total REAL DEFAULT 0,
        commission_rate REAL DEFAULT 0,
        commission_amount REAL DEFAULT 0,
        status TEXT DEFAULT 'pending',
        paid_at TEXT DEFAULT '',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (employee_id) REFERENCES users(id)
    )''')

    # Revenue Daily (pre-aggregated)
    cursor.execute('''CREATE TABLE IF NOT EXISTS revenue_daily (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        date TEXT NOT NULL UNIQUE,
        total_revenue REAL DEFAULT 0,
        total_appointments INTEGER DEFAULT 0,
        total_invoices INTEGER DEFAULT 0,
        avg_ticket REAL DEFAULT 0,
        new_customers INTEGER DEFAULT 0,
        services_breakdown TEXT DEFAULT '{}',
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')

    # Multi-Channel Inbox
    cursor.execute('''CREATE TABLE IF NOT EXISTS channel_inbox (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        customer_id INTEGER DEFAULT 0,
        customer_name TEXT DEFAULT '',
        channel TEXT DEFAULT 'whatsapp',
        direction TEXT DEFAULT 'outgoing',
        message TEXT DEFAULT '',
        status TEXT DEFAULT 'sent',
        reference_type TEXT DEFAULT '',
        reference_id INTEGER DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')

    # Business Health Score
    cursor.execute('''CREATE TABLE IF NOT EXISTS business_health_score (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        date TEXT NOT NULL,
        overall_score REAL DEFAULT 0,
        revenue_score REAL DEFAULT 0,
        satisfaction_score REAL DEFAULT 0,
        efficiency_score REAL DEFAULT 0,
        retention_score REAL DEFAULT 0,
        growth_score REAL DEFAULT 0,
        details TEXT DEFAULT '{}',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')

    # Import History
    cursor.execute('''CREATE TABLE IF NOT EXISTS import_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        import_type TEXT NOT NULL,
        filename TEXT DEFAULT '',
        total_rows INTEGER DEFAULT 0,
        imported_rows INTEGER DEFAULT 0,
        errors INTEGER DEFAULT 0,
        error_details TEXT DEFAULT '[]',
        imported_by TEXT DEFAULT 'admin',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')

    # Report Builder
    cursor.execute('''CREATE TABLE IF NOT EXISTS report_builder (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        report_type TEXT DEFAULT 'weekly',
        sections TEXT DEFAULT '[]',
        filters TEXT DEFAULT '{}',
        schedule TEXT DEFAULT '',
        last_generated TEXT DEFAULT '',
        created_by TEXT DEFAULT 'admin',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')

    # ─── Phase 17: Enterprise Intelligence & Business Growth Tables ───

    # Damage Claims
    cursor.execute('''CREATE TABLE IF NOT EXISTS damage_claims (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        appointment_id INTEGER DEFAULT 0,
        customer_id INTEGER DEFAULT 0,
        car_id INTEGER DEFAULT 0,
        employee_id INTEGER DEFAULT 0,
        damage_type TEXT DEFAULT '',
        description TEXT DEFAULT '',
        photos TEXT DEFAULT '[]',
        severity TEXT DEFAULT 'minor',
        compensation_amount REAL DEFAULT 0,
        compensation_type TEXT DEFAULT 'discount',
        status TEXT DEFAULT 'reported',
        resolution_notes TEXT DEFAULT '',
        reported_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        resolved_at TEXT DEFAULT '',
        FOREIGN KEY (appointment_id) REFERENCES appointments(id),
        FOREIGN KEY (customer_id) REFERENCES customers(id)
    )''')

    # Revenue Forecast
    cursor.execute('''CREATE TABLE IF NOT EXISTS revenue_forecast (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        month TEXT NOT NULL,
        predicted_revenue REAL DEFAULT 0,
        actual_revenue REAL DEFAULT 0,
        predicted_appointments INTEGER DEFAULT 0,
        actual_appointments INTEGER DEFAULT 0,
        confidence REAL DEFAULT 0,
        factors TEXT DEFAULT '{}',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')

    # Customer Segments
    cursor.execute('''CREATE TABLE IF NOT EXISTS customer_segments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        customer_id INTEGER NOT NULL,
        segment TEXT DEFAULT 'regular',
        score REAL DEFAULT 0,
        last_visit_days INTEGER DEFAULT 0,
        total_spent REAL DEFAULT 0,
        visit_count INTEGER DEFAULT 0,
        avg_ticket REAL DEFAULT 0,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (customer_id) REFERENCES customers(id)
    )''')

    # Appointment Waitlist
    cursor.execute('''CREATE TABLE IF NOT EXISTS appointment_waitlist (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        customer_id INTEGER NOT NULL,
        customer_name TEXT DEFAULT '',
        phone TEXT DEFAULT '',
        service_requested TEXT DEFAULT '',
        preferred_date TEXT DEFAULT '',
        preferred_time TEXT DEFAULT '',
        notes TEXT DEFAULT '',
        status TEXT DEFAULT 'waiting',
        notified_at TEXT DEFAULT '',
        assigned_appointment_id INTEGER DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (customer_id) REFERENCES customers(id)
    )''')

    # Employee Attendance
    cursor.execute('''CREATE TABLE IF NOT EXISTS employee_attendance (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        employee_id INTEGER NOT NULL,
        employee_name TEXT DEFAULT '',
        date TEXT NOT NULL,
        check_in TEXT DEFAULT '',
        check_out TEXT DEFAULT '',
        status TEXT DEFAULT 'present',
        late_minutes INTEGER DEFAULT 0,
        overtime_minutes INTEGER DEFAULT 0,
        notes TEXT DEFAULT '',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')

    # Supplier Reviews
    cursor.execute('''CREATE TABLE IF NOT EXISTS supplier_reviews (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        supplier_id INTEGER NOT NULL,
        purchase_order_id INTEGER DEFAULT 0,
        delivery_rating INTEGER DEFAULT 5,
        quality_rating INTEGER DEFAULT 5,
        price_rating INTEGER DEFAULT 5,
        overall_rating REAL DEFAULT 5,
        comment TEXT DEFAULT '',
        reviewed_by TEXT DEFAULT 'admin',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (supplier_id) REFERENCES suppliers(id)
    )''')

    # Currency Exchange Rates
    cursor.execute('''CREATE TABLE IF NOT EXISTS currency_rates (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        currency_code TEXT NOT NULL,
        currency_name TEXT DEFAULT '',
        rate_to_tnd REAL DEFAULT 1,
        is_active INTEGER DEFAULT 1,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')

    # Knowledge Base
    cursor.execute('''CREATE TABLE IF NOT EXISTS knowledge_base (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT NOT NULL,
        category TEXT DEFAULT 'general',
        content TEXT DEFAULT '',
        tags TEXT DEFAULT '',
        author TEXT DEFAULT 'admin',
        views INTEGER DEFAULT 0,
        is_pinned INTEGER DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')

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

    # ─── Phase 8: FTS5 Full-Text Search ───
    cursor.execute('''CREATE VIRTUAL TABLE IF NOT EXISTS fts_customers
        USING fts5(name, phone, email, content='customers', content_rowid='id')''')
    cursor.execute('''CREATE VIRTUAL TABLE IF NOT EXISTS fts_cars
        USING fts5(brand, model, plate, content='cars', content_rowid='id')''')

    # Client OTP for secure login
    cursor.execute('''CREATE TABLE IF NOT EXISTS client_otp (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        phone TEXT NOT NULL,
        otp_code TEXT NOT NULL,
        ip_address TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        expires_at TIMESTAMP NOT NULL,
        verified INTEGER DEFAULT 0
    )''')
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_client_otp_phone ON client_otp(phone, verified)")

    # Client login attempts for rate limiting
    cursor.execute('''CREATE TABLE IF NOT EXISTS client_login_attempts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        phone TEXT NOT NULL,
        ip_address TEXT NOT NULL,
        attempted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        success INTEGER DEFAULT 0
    )''')
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_client_login_ip ON client_login_attempts(ip_address, attempted_at)")

    # Push Notification Subscriptions
    cursor.execute('''CREATE TABLE IF NOT EXISTS push_subscriptions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        endpoint TEXT NOT NULL UNIQUE,
        p256dh TEXT NOT NULL,
        auth TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users(id)
    )''')
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_push_subscriptions_user ON push_subscriptions(user_id)")

    # Rebuild FTS indexes from source tables
    _rebuild_fts(cursor)

    conn.commit()
    conn.close()
    print("قاعدة البيانات جاهزة ✅")


def _rebuild_fts(cursor):
    """Rebuild FTS5 indexes from source tables (safe to run repeatedly)."""
    try:
        cursor.execute("DELETE FROM fts_customers")
        cursor.execute(
            "INSERT INTO fts_customers(rowid, name, phone, email) "
            "SELECT id, COALESCE(name,''), COALESCE(phone,''), COALESCE(email,'') "
            "FROM customers WHERE is_deleted=0")
        cursor.execute("DELETE FROM fts_cars")
        cursor.execute(
            "INSERT INTO fts_cars(rowid, brand, model, plate) "
            "SELECT id, COALESCE(brand,''), COALESCE(model,''), COALESCE(plate,'') "
            "FROM cars WHERE is_deleted=0")
    except Exception:
        pass


def rebuild_fts():
    """Public helper to rebuild FTS indexes (called after insert/update/delete)."""
    conn = connect()
    cursor = conn.cursor()
    _rebuild_fts(cursor)
    conn.commit()
    conn.close()


if __name__ == "__main__":
    create_tables()