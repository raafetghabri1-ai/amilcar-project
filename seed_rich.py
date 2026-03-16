"""
AMILCAR — Rich Seed Data Generator
Fills the DB with realistic Tunisian auto-care data:
  - 50 customers
  - 65 cars
  - 200 appointments (spread over 12 months)
  - 160 invoices  
  - 15 inventory items
  - expenses, services, suppliers, loyalty points, rfm segments
"""

import sqlite3
import random
from datetime import date, timedelta, datetime

DB_PATH = "database/amilcar.db"

# ── Tunisian-realistic data ────────────────────────────────────────────────────

CUSTOMERS = [
    ("Jnina Chekir",        "20 345 678", "jnina.chekir@gmail.com",       "F", "1988-04-12"),
    ("Feryel Ghabri",       "55 912 340", "feryel.ghabri@yahoo.fr",       "F", "1992-07-23"),
    ("Kamel Chekir",        "20 876 543", "kamel.chekir@outlook.com",     "M", "1985-11-05"),
    ("Samira Loucieif",     "98 123 456", "samira.l@gmail.com",           "F", "1990-03-17"),
    ("Slim Ben Abdalah",    "22 654 321", "slim.benabdalah@hotmail.com",  "M", "1983-09-29"),
    ("Amine Batkalah",      "50 789 012", "amine.bat@gmail.com",          "M", "1995-01-08"),
    ("Wassef Djerbi",       "29 456 789", "wassef.dj@outlook.com",        "M", "1987-06-14"),
    ("Adem Garbaa",         "54 321 098", "adem.garbaa@gmail.com",        "M", "1993-12-30"),
    ("Mahmoud Bibi",        "97 654 210", "mahmoud.bibi@yahoo.fr",        "M", "1979-08-21"),
    ("Khaled Azaiez",       "23 987 654", "khaled.azaiez@gmail.com",      "M", "1991-05-03"),
    ("Nadia Mrad",          "71 234 567", "nadia.mrad@gmail.com",         "F", "1989-02-18"),
    ("Youssef Trabelsi",    "25 678 901", "youssef.t@gmail.com",          "M", "1986-10-11"),
    ("Sonia Ben Romdhane",  "99 012 345", "sonia.br@yahoo.fr",            "F", "1994-07-07"),
    ("Hamdi Gharbi",        "52 345 678", "hamdi.gh@gmail.com",           "M", "1982-04-25"),
    ("Ines Sfar",           "26 789 012", "ines.sfar@hotmail.com",        "F", "1997-01-14"),
    ("Mohamed Ali Sassi",   "74 012 345", "ma.sassi@gmail.com",           "M", "1980-09-09"),
    ("Rania Hammami",       "27 345 678", "rania.ham@gmail.com",          "F", "1996-06-22"),
    ("Farouk Ben Youssef",  "51 678 901", "farouk.by@yahoo.fr",           "M", "1977-03-16"),
    ("Leila Jendoubi",      "96 901 234", "leila.jen@gmail.com",          "F", "1991-11-28"),
    ("Tarek Belhaj",        "28 234 567", "tarek.bel@gmail.com",          "M", "1988-08-04"),
    ("Amira Khelifi",       "53 567 890", "amira.kh@hotmail.com",         "F", "1993-05-19"),
    ("Bilel Mansouri",      "72 890 123", "bilel.man@gmail.com",          "M", "1984-12-01"),
    ("Hajer Rezgui",        "29 123 456", "hajer.rez@yahoo.fr",           "F", "1998-04-11"),
    ("Oussama Tlili",       "55 456 789", "oussama.tl@gmail.com",         "M", "1990-02-27"),
    ("Meriem Chtourou",     "21 789 012", "meriem.ch@gmail.com",          "F", "1987-10-15"),
    ("Walid Ferchichi",     "75 012 345", "walid.fer@hotmail.com",        "M", "1981-07-08"),
    ("Sabrine Karoui",      "26 345 678", "sabrine.kar@gmail.com",        "F", "1995-03-23"),
    ("Mehdi Zouari",        "50 678 901", "mehdi.zou@gmail.com",          "M", "1992-09-17"),
    ("Dalila Ben Salah",    "98 901 234", "dalila.bs@yahoo.fr",           "F", "1986-01-06"),
    ("Chiheb Dridi",        "22 234 567", "chiheb.dri@gmail.com",         "M", "1994-06-30"),
    ("Yasmine Hadj Ali",    "54 567 890", "yasmine.ha@gmail.com",         "F", "1999-08-12"),
    ("Nizar Bouaziz",       "71 890 123", "nizar.bou@hotmail.com",        "M", "1983-11-24"),
    ("Olfa Chaouachi",      "27 123 456", "olfa.ch@gmail.com",            "F", "1990-04-18"),
    ("Riadh Ghorbel",       "52 456 789", "riadh.gh@yahoo.fr",            "M", "1978-02-09"),
    ("Cyrine Mzah",         "96 789 012", "cyrine.mz@gmail.com",          "F", "1997-07-14"),
    ("Lotfi Ben Hmida",     "28 012 345", "lotfi.bh@gmail.com",           "M", "1985-05-03"),
    ("Asma Chabbi",         "51 345 678", "asma.ch@hotmail.com",          "F", "1993-12-20"),
    ("Hichem Mabrouk",      "73 678 901", "hichem.mab@gmail.com",         "M", "1982-08-16"),
    ("Fatma Zahra Slama",   "25 901 234", "fz.slama@yahoo.fr",            "F", "1989-03-28"),
    ("Skander Jouini",      "55 234 567", "skander.jo@gmail.com",         "M", "1991-10-07"),
    ("Mouna Beji",          "21 567 890", "mouna.beji@gmail.com",         "F", "1996-01-31"),
    ("Amine Oueslati",      "75 890 123", "amine.ou@hotmail.com",         "M", "1987-06-11"),
    ("Dhouha Karray",       "27 123 456", "dhouha.kar@gmail.com",         "F", "1994-09-25"),
    ("Montassar Ben Ali",   "50 456 789", "mont.ba@gmail.com",            "M", "1980-12-13"),
    ("Sarra Baccouche",     "98 789 012", "sarra.bac@yahoo.fr",           "F", "1998-05-06"),
    ("Yassine Hamila",      "22 012 345", "yassine.ham@gmail.com",        "M", "1986-02-19"),
    ("Najet Abidi",         "54 345 678", "najet.abidi@gmail.com",        "F", "1983-07-22"),
    ("Anis Turki",          "72 678 901", "anis.tur@hotmail.com",         "M", "1992-04-14"),
    ("Emna Chahed",         "26 901 234", "emna.cha@gmail.com",           "F", "1995-11-08"),
    ("Zied Mellouli",       "51 234 567", "zied.mel@gmail.com",           "M", "1979-08-30"),
]

CARS = [
    ("Peugeot",     "208",          "198 TU 7321", "2021", "Blanc"),
    ("Volkswagen",  "Golf 8",       "215 TU 4455", "2022", "Gris"),
    ("Renault",     "Clio 5",       "180 TU 9012", "2020", "Rouge"),
    ("Hyundai",     "Tucson",       "205 TU 3344", "2021", "Noir"),
    ("Toyota",      "Corolla",      "178 TU 6677", "2019", "Blanc"),
    ("Citroën",     "C3",           "220 TU 8899", "2022", "Bleu"),
    ("Ford",        "Focus",        "190 TU 1122", "2020", "Argent"),
    ("Kia",         "Sportage",     "210 TU 5566", "2022", "Gris"),
    ("Dacia",       "Duster",       "185 TU 7788", "2020", "Orange"),
    ("Mercedes",    "Classe A",     "225 TU 3311", "2023", "Noir"),
    ("BMW",         "Série 3",      "230 TU 4422", "2023", "Blanc"),
    ("Audi",        "A3",           "218 TU 9900", "2022", "Gris"),
    ("Toyota",      "Yaris",        "175 TU 2233", "2019", "Rouge"),
    ("Nissan",      "Juke",         "200 TU 6644", "2021", "Bleu"),
    ("Seat",        "Ibiza",        "195 TU 8811", "2020", "Blanc"),
    ("Skoda",       "Octavia",      "208 TU 1177", "2021", "Noir"),
    ("Opel",        "Astra",        "183 TU 5599", "2019", "Bleu"),
    ("Peugeot",     "308",          "212 TU 3388", "2022", "Gris"),
    ("Renault",     "Megane 4",     "197 TU 7766", "2020", "Rouge"),
    ("Hyundai",     "i20",          "222 TU 4400", "2022", "Blanc"),
    ("Honda",       "Civic",        "193 TU 9955", "2020", "Bleu"),
    ("Suzuki",      "Swift",        "182 TU 2266", "2019", "Vert"),
    ("Volkswagen",  "Polo",         "216 TU 6633", "2022", "Argent"),
    ("Ford",        "Puma",         "205 TU 1100", "2021", "Rouge"),
    ("Toyota",      "RAV4",         "232 TU 8877", "2023", "Noir"),
    ("Kia",         "Picanto",      "188 TU 4411", "2020", "Blanc"),
    ("Dacia",       "Logan",        "177 TU 7733", "2019", "Gris"),
    ("Citroën",     "C5 Aircross",  "215 TU 2255", "2022", "Bleu"),
    ("Peugeot",     "3008",         "228 TU 6688", "2023", "Gris"),
    ("Seat",        "Arona",        "203 TU 9944", "2021", "Rouge"),
    ("Renault",     "Arkana",       "220 TU 3377", "2022", "Noir"),
    ("BMW",         "X1",           "235 TU 5511", "2023", "Blanc"),
    ("Mercedes",    "GLA",          "229 TU 8822", "2023", "Argent"),
    ("Audi",        "Q3",           "224 TU 1133", "2022", "Gris"),
    ("Volkswagen",  "Tiguan",       "219 TU 4466", "2022", "Bleu"),
    ("Hyundai",     "Elantra",      "207 TU 7799", "2021", "Rouge"),
    ("Nissan",      "Qashqai",      "213 TU 2200", "2022", "Blanc"),
    ("Toyota",      "C-HR",         "209 TU 5544", "2021", "Gris"),
    ("Peugeot",     "2008",         "201 TU 8833", "2021", "Orange"),
    ("Kia",         "Stinger",      "227 TU 1166", "2023", "Noir"),
    ("Ford",        "Kuga",         "214 TU 6600", "2022", "Bleu"),
    ("Renault",     "Captur",       "196 TU 9977", "2020", "Rouge"),
    ("Citroën",     "C4",           "221 TU 3322", "2022", "Blanc"),
    ("Skoda",       "Karoq",        "217 TU 5588", "2022", "Gris"),
    ("Opel",        "Crossland",    "204 TU 8811", "2021", "Bleu"),
    ("Honda",       "HR-V",         "223 TU 1144", "2022", "Noir"),
    ("Suzuki",      "Vitara",       "210 TU 4477", "2022", "Gris"),
    ("Dacia",       "Sandero",      "186 TU 7700", "2019", "Blanc"),
    ("Seat",        "Ateca",        "211 TU 9933", "2022", "Bleu"),
    ("Hyundai",     "Kona",         "206 TU 2266", "2021", "Rouge"),
    ("Volkswagen",  "T-Roc",        "226 TU 5511", "2023", "Argent"),
    ("Kia",         "Niro",         "231 TU 8844", "2023", "Blanc"),
    ("Toyota",      "Land Cruiser", "234 TU 1177", "2023", "Noir"),
    ("BMW",         "X3",           "233 TU 4400", "2023", "Gris"),
    ("Mercedes",    "C 200",        "236 TU 7733", "2024", "Blanc"),
    ("Audi",        "A4",           "237 TU 2266", "2024", "Noir"),
    ("Peugeot",     "508",          "238 TU 5599", "2024", "Argent"),
    ("Renault",     "Austral",      "239 TU 8822", "2024", "Bleu"),
    ("Ford",        "Mustang Mach-E","240 TU 1155","2024", "Rouge"),
    ("Kia",         "EV6",          "241 TU 4488", "2024", "Blanc"),
    ("Hyundai",     "IONIQ 6",      "242 TU 7711", "2024", "Gris"),
    ("Tesla",       "Model 3",      "243 TU 2244", "2024", "Rouge"),
    ("Tesla",       "Model Y",      "244 TU 5577", "2024", "Noir"),
    ("Citroën",     "ë-C4",         "245 TU 8800", "2024", "Bleu"),
]

SERVICES = [
    ("Lavage Extérieur",            25,  30),
    ("Lavage Complet",              55,  60),
    ("Polissage Carrosserie",       150, 180),
    ("Traitement Céramique",        350, 240),
    ("Nettoyage Intérieur",         45,  45),
    ("Lustrage",                    90,  120),
    ("Dégraissage Moteur",          40,  60),
    ("Traitement Jantes",           80,  90),
    ("Imperméabilisation Sièges",   120, 90),
    ("Désinfection Habitacle",      60,  45),
    ("Rénovation Phares",           70,  60),
    ("Teinture Vitres",             200, 120),
    ("Anti-rouille Soubassement",   180, 150),
    ("Pack Complet Premium",        450, 360),
    ("Entretien Rapide",            35,  30),
]

STATUSES_APPT = ["completed", "completed", "completed", "completed", "cancelled", "pending"]
PAYMENT_METHODS = ["cash", "cash", "cash", "card", "transfer"]


def random_date(start_days_ago, end_days_ago=0):
    """Return a date between start_days_ago and end_days_ago before today."""
    start = date.today() - timedelta(days=start_days_ago)
    end = date.today() - timedelta(days=end_days_ago)
    delta = (end - start).days
    if delta <= 0:
        return start
    return start + timedelta(days=random.randint(0, delta))


def run():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = OFF")

    print("🌱 Démarrage du seed...")

    # ── 1. Services ──────────────────────────────────────────────────────────
    existing_services = conn.execute("SELECT COUNT(*) FROM services").fetchone()[0]
    if existing_services < 5:
        for name, price, minutes in SERVICES:
            conn.execute(
                "INSERT OR IGNORE INTO services (name, price, estimated_minutes) VALUES (?,?,?)",
                (name, price, minutes)
            )
        print(f"  ✓ {len(SERVICES)} services insérés")
    else:
        print(f"  ⏭ services déjà présents ({existing_services})")

    # ── 2. Customers ─────────────────────────────────────────────────────────
    today = date.today().isoformat()
    inserted_customer_ids = []
    for i, (name, phone, email, gender, born) in enumerate(CUSTOMERS):
        existing = conn.execute("SELECT id FROM customers WHERE phone=?", (phone,)).fetchone()
        if existing:
            inserted_customer_ids.append(existing[0])
            continue
        last_visit = random_date(400, 5).isoformat()
        total_visits = random.randint(1, 18)
        notes = random.choice([
            "Client fidèle, préfère le matin",
            "Très exigeant sur la propreté intérieure",
            "Chauffeur Uber, lavage fréquent",
            "Intéressé par la céramique",
            "Recommandé par un ami",
            "Préfère le weekend",
            "Possède plusieurs véhicules",
            "",
        ])
        row = conn.execute(
            "INSERT INTO customers (name, phone, email, notes, last_visit, total_visits) VALUES (?,?,?,?,?,?)",
            (name, phone, email, notes, last_visit, total_visits)
        )
        inserted_customer_ids.append(row.lastrowid)

    conn.commit()
    print(f"  ✓ {len(inserted_customer_ids)} clients traités")

    # ── 3. Cars ──────────────────────────────────────────────────────────────
    inserted_car_ids = []
    for i, (brand, model, plate, year, color) in enumerate(CARS):
        existing = conn.execute("SELECT id FROM cars WHERE plate=?", (plate,)).fetchone()
        if existing:
            inserted_car_ids.append(existing[0])
            continue
        # Assign car to customer (distribute evenly, some customers have 2 cars)
        customer_id = inserted_customer_ids[i % len(inserted_customer_ids)]
        row = conn.execute(
            "INSERT INTO cars (customer_id, brand, model, plate, year, color) VALUES (?,?,?,?,?,?)",
            (customer_id, brand, model, plate, year, color)
        )
        inserted_car_ids.append(row.lastrowid)

    conn.commit()
    print(f"  ✓ {len(inserted_car_ids)} véhicules traités")

    # ── 4. Appointments + Invoices ───────────────────────────────────────────
    services_db = conn.execute("SELECT id, name, price FROM services").fetchall()
    all_car_ids = conn.execute("SELECT id, customer_id FROM cars").fetchall()
    if not all_car_ids:
        print("  ✗ Aucune voiture trouvée")
        return

    appt_count = 0
    inv_count = 0
    times = ["08:00", "08:30", "09:00", "09:30", "10:00", "10:30", "11:00",
             "11:30", "14:00", "14:30", "15:00", "15:30", "16:00", "16:30", "17:00"]

    # Generate 200 appointments spread over last 12 months
    for _ in range(200):
        car_row = random.choice(all_car_ids)
        car_id, customer_id = car_row[0], car_row[1]
        svc = random.choice(services_db)
        svc_id, svc_name, svc_price = svc[0], svc[1], svc[2]
        appt_date = random_date(365, 1)
        appt_time = random.choice(times)
        status = random.choice(STATUSES_APPT)
        # slight price variation ±15%
        actual_price = round(svc_price * random.uniform(0.85, 1.15), 2)

        row = conn.execute(
            "INSERT INTO appointments (car_id, date, time, service, status) VALUES (?,?,?,?,?)",
            (car_id, appt_date.isoformat(), appt_time, svc_name, status)
        )
        appt_id = row.lastrowid
        appt_count += 1

        # Create invoice for completed appointments
        if status == "completed":
            pay_method = random.choice(PAYMENT_METHODS)
            inv_status = random.choice(["paid", "paid", "paid", "unpaid", "partial"])
            paid_amount = actual_price if inv_status == "paid" else (
                round(actual_price * random.uniform(0.3, 0.7), 2) if inv_status == "partial" else 0
            )
            inv_date = appt_date + timedelta(hours=2)
            conn.execute(
                """INSERT INTO invoices (appointment_id, amount, status, payment_method,
                   paid_amount, created_at)
                   VALUES (?,?,?,?,?,?)""",
                (appt_id, actual_price, inv_status, pay_method, paid_amount,
                 inv_date.isoformat())
            )
            inv_count += 1

    conn.commit()
    print(f"  ✓ {appt_count} rendez-vous insérés")
    print(f"  ✓ {inv_count} factures insérées")

    # ── 5. Inventory ─────────────────────────────────────────────────────────
    inventory_items = [
        ("Shampoing Auto Premium",      "shampoing",  45,  10, 5,  12.5),
        ("Cire Carnauba",               "cire",       12,  3,  2,  45.0),
        ("Dégraissant Jantes",          "produit",    28,  8,  5,  8.0),
        ("Vitres Net Crystal",          "vitres",     35,  10, 5,  6.5),
        ("Polish Finishing",            "polish",     8,   2,  3,  55.0),
        ("Imperméabilisant Tissu",      "traitement", 15,  5,  3,  22.0),
        ("Désodorisant Habitacle",      "parfum",     60,  20, 10, 3.5),
        ("Microfibre Premium 40x40",    "accessoire", 120, 30, 20, 2.8),
        ("Céramique Pro 9H",            "ceramique",  6,   1,  2,  180.0),
        ("Anti-rouille Undercoat",      "protection", 10,  3,  3,  35.0),
        ("Plastique Brillant",          "interieur",  22,  8,  5,  9.5),
        ("Cuir Renovateur",             "interieur",  18,  5,  4,  16.0),
        ("Brosse Détailing Jantes",     "outil",      25,  8,  5,  12.0),
        ("Aspirateur Portable 12V",     "equipement", 3,   1,  2,  85.0),
        ("Lance à Mousse Karcher",      "equipement", 2,   1,  1,  145.0),
    ]
    for name, cat, qty, min_qty, reorder, cost in inventory_items:
        existing = conn.execute("SELECT id FROM inventory WHERE name=?", (name,)).fetchone()
        if not existing:
            conn.execute(
                "INSERT INTO inventory (name, category, quantity, min_quantity, unit_price) VALUES (?,?,?,?,?)",
                (name, cat, qty, min_qty, cost)
            )
    conn.commit()
    print(f"  ✓ {len(inventory_items)} articles inventaire insérés")

    # ── 6. Expenses ──────────────────────────────────────────────────────────
    expense_cats = ["Loyer", "Électricité", "Eau", "Fournitures", "Salaires", "Matériel"]
    for i in range(24):  # 2 years of monthly expenses
        exp_date = date.today() - timedelta(days=30 * i)
        for cat in ["Loyer", "Électricité", "Salaires"]:
            amount = {
                "Loyer": 1200,
                "Électricité": random.randint(150, 350),
                "Salaires": random.randint(2500, 3200),
            }[cat]
            conn.execute(
                "INSERT INTO expenses (description, amount, category, date) VALUES (?,?,?,?)",
                (f"{cat} – {exp_date.strftime('%B %Y')}", amount, cat, exp_date.isoformat())
            )
    conn.commit()
    print("  ✓ Dépenses mensuelles insérées")

    # ── 7. Loyalty Points ────────────────────────────────────────────────────
    all_customers = conn.execute("SELECT id FROM customers").fetchall()
    for (cid,) in all_customers:
        existing = conn.execute("SELECT id FROM reward_points WHERE customer_id=?", (cid,)).fetchone()
        if not existing:
            points = random.randint(0, 800)
            tier = "Bronze" if points < 200 else ("Silver" if points < 500 else ("Gold" if points < 800 else "Platinum"))
            conn.execute(
                "INSERT INTO reward_points (customer_id, points, tier) VALUES (?,?,?)",
                (cid, points, tier)
            )
    conn.commit()
    print("  ✓ Points fidélité insérés")

    # ── 8. RFM Segments ──────────────────────────────────────────────────────
    segments = ["Champions", "Loyal Customers", "Potential Loyalist",
                "At Risk", "Need Attention", "Lost", "New Customers"]
    for (cid,) in all_customers:
        existing = conn.execute("SELECT id FROM rfm_segments WHERE customer_id=?", (cid,)).fetchone()
        if not existing:
            seg = random.choice(segments)
            r = random.randint(1, 5)
            f = random.randint(1, 5)
            m = random.randint(1, 5)
            conn.execute(
                "INSERT INTO rfm_segments (customer_id, segment, recency_score, frequency_score, monetary_score) VALUES (?,?,?,?,?)",
                (cid, seg, r, f, m)
            )
    conn.commit()
    print("  ✓ Segments RFM insérés")

    # ── 9. Smart Alerts ──────────────────────────────────────────────────────
    alert_types = [
        ("stock_low",       "Stock bas : Shampoing Auto Premium",   "medium"),
        ("stock_low",       "Stock bas : Céramique Pro 9H",         "high"),
        ("overdue_invoice", "Facture impayée > 30 jours",           "high"),
        ("maintenance_due", "Maintenance due : Toyota RAV4",        "medium"),
        ("birthday",        "Anniversaire client : Jnina Chekir",   "low"),
        ("stock_low",       "Stock critique : Cire Carnauba",       "high"),
        ("follow_up",       "Relance client inactif : 45 jours",    "medium"),
    ]
    existing_alerts = conn.execute("SELECT COUNT(*) FROM smart_alerts").fetchone()[0]
    if existing_alerts < 5:
        for atype, msg, priority in alert_types:
            conn.execute(
                "INSERT INTO smart_alerts (alert_type, title, message, severity, is_read) VALUES (?,?,?,?,0)",
                (atype, msg, msg, priority)
            )
        print("  ✓ Smart alerts insérés")

    # ── 10. Update customers total_visits ────────────────────────────────────
    conn.execute("""
        UPDATE customers SET total_visits = (
            SELECT COUNT(*) FROM appointments a
            JOIN cars ca ON a.car_id = ca.id
            WHERE ca.customer_id = customers.id AND a.status = 'completed'
        )
    """)

    # ── 11. Update customers last_visit ──────────────────────────────────────
    conn.execute("""
        UPDATE customers SET last_visit = (
            SELECT MAX(a.date) FROM appointments a
            JOIN cars ca ON a.car_id = ca.id
            WHERE ca.customer_id = customers.id AND a.status = 'completed'
        )
        WHERE EXISTS (
            SELECT 1 FROM appointments a
            JOIN cars ca ON a.car_id = ca.id
            WHERE ca.customer_id = customers.id
        )
    """)

    conn.commit()
    conn.execute("PRAGMA foreign_keys = ON")
    conn.close()

    print("\n✅ Seed terminé avec succès !")
    print(f"   Clients : {len(CUSTOMERS)}")
    print(f"   Voitures: {len(CARS)}")
    print(f"   RDV     : ~{appt_count}")
    print(f"   Factures: ~{inv_count}")


if __name__ == "__main__":
    run()
