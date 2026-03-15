"""
AMILCAR — Operations, Care & Services
Blueprint: ops_bp
Routes: 55
"""
from flask import Blueprint, render_template, request, redirect, url_for, flash, make_response, jsonify, session, send_file
from helpers import login_required, admin_required, client_required, get_db, get_services, get_setting, get_all_settings
from helpers import allowed_file, safe_page, log_activity, build_wa_url, STATUS_MESSAGES, UPLOAD_FOLDER, MAX_FILE_SIZE, MAX_FILES, PER_PAGE, csrf
from database.db import get_db
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, date, timedelta
import os, re, uuid, io
import time as time_module
import sqlite3

ops_bp = Blueprint("ops_bp", __name__)

CARE_STEPS = [
    ("reception", "Réception", "🚗"),
    ("inspection", "Inspection", "🔍"),
    ("lavage_ext", "Lavage extérieur", "🚿"),
    ("lavage_int", "Nettoyage intérieur", "🧹"),
    ("polish", "Polissage/Correction", "✨"),
    ("protection", "Protection/Traitement", "🛡️"),
    ("sechage", "Séchage/Curing", "☀️"),
    ("finition", "Finitions", "🎨"),
    ("controle", "Contrôle qualité", "✅"),
    ("pret", "Prêt à livrer", "🏁")
]


# ─── Maintenance Reminders ───
@ops_bp.route("/maintenance_reminders")
@login_required
def maintenance_reminders():
    from datetime import date, timedelta
    with get_db() as conn:
        cars_with_last_service = conn.execute(
            "SELECT ca.id, ca.brand, ca.model, ca.plate, cu.name, cu.phone, "
            "MAX(a.date) as last_date, a.service "
            "FROM cars ca JOIN customers cu ON ca.customer_id = cu.id "
            "LEFT JOIN appointments a ON a.car_id = ca.id AND a.status = 'completed' "
            "GROUP BY ca.id ORDER BY last_date ASC"
        ).fetchall()
    today = date.today()
    reminders = []
    for car in cars_with_last_service:
        last_date = car[6]
        if last_date:
            from datetime import datetime
            try:
                ld = datetime.strptime(last_date, '%Y-%m-%d').date()
                days_ago = (today - ld).days
            except ValueError:
                days_ago = 0
        else:
            days_ago = 999
        reminders.append({
            'car_id': car[0], 'brand': car[1], 'model': car[2], 'plate': car[3],
            'owner': car[4], 'phone': car[5],
            'last_date': last_date or 'Never', 'last_service': car[7] or '—',
            'days_ago': days_ago,
            'alert': days_ago > 90
        })
    return render_template("maintenance_reminders.html", reminders=reminders)



# ─── Service Packages ───
@ops_bp.route("/packages")
@login_required
def packages_list():
    with get_db() as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS service_packages (
            id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL,
            description TEXT DEFAULT '', services TEXT NOT NULL,
            original_price REAL DEFAULT 0, package_price REAL DEFAULT 0,
            active INTEGER DEFAULT 1)""")
        packages = conn.execute("SELECT * FROM service_packages ORDER BY id").fetchall()
    return render_template("packages.html", packages=packages, services=get_services())



# ─── Feature 1: Live Workshop Board ───
@ops_bp.route("/live_board")
@login_required
def live_board():
    return render_template("live_board.html")



# ─── Phase 6 Feature 5: Maintenance Mileage Reminders ───
@ops_bp.route("/mileage_tracking")
@login_required
def mileage_tracking():
    with get_db() as conn:
        cars = conn.execute(
            "SELECT ca.id, cu.name, ca.brand, ca.model, ca.plate, "
            "COALESCE(ca.mileage,0), COALESCE(ca.last_oil_change,''), COALESCE(ca.next_service_date,'') "
            "FROM cars ca JOIN customers cu ON ca.customer_id = cu.id ORDER BY cu.name").fetchall()
    return render_template("mileage_tracking.html", cars=cars)



@ops_bp.route("/mileage_tracking/update/<int:car_id>", methods=["POST"])
@login_required
def update_mileage(car_id):
    mileage = request.form.get("mileage", "0")
    last_oil = request.form.get("last_oil_change", "")
    next_service = request.form.get("next_service_date", "")
    try:
        mileage_val = int(mileage)
    except ValueError:
        mileage_val = 0
    with get_db() as conn:
        conn.execute("UPDATE cars SET mileage = ?, last_oil_change = ?, next_service_date = ? WHERE id = ?",
            (mileage_val, last_oil, next_service, car_id))
        conn.commit()
    flash("Kilométrage mis à jour", "success")
    return redirect("/mileage_tracking")



# ─── Phase 7 Feature 5: Maintenance Plans ───
@ops_bp.route("/maintenance_plans")
@login_required
def maintenance_plans():
    with get_db() as conn:
        plans = conn.execute("""
            SELECT mp.*, c.name, cr.brand, cr.model, cr.plate, cr.mileage
            FROM maintenance_plans mp
            JOIN cars cr ON mp.car_id = cr.id
            JOIN customers c ON cr.customer_id = c.id
            WHERE mp.active = 1
            ORDER BY mp.next_due_date ASC
        """).fetchall()
        cars = conn.execute("SELECT cr.id, c.name || ' - ' || cr.brand || ' ' || cr.model || ' (' || cr.plate || ')' FROM cars cr JOIN customers c ON cr.customer_id = c.id ORDER BY c.name").fetchall()
        # Alerts: overdue plans
        from datetime import date
        today_str = date.today().isoformat()
        alerts = [p for p in plans if p[8] and p[8] <= today_str]
    return render_template("maintenance_plans.html", plans=plans, cars=cars, alerts=alerts, now_date=today_str)



@ops_bp.route("/maintenance_plans/add", methods=["POST"])
@login_required
def add_maintenance_plan():
    car_id = request.form.get('car_id')
    service_type = request.form.get('service_type', '')
    interval_km = int(request.form.get('interval_km', 0))
    interval_months = int(request.form.get('interval_months', 0))
    last_done_date = request.form.get('last_done_date', '')
    last_done_km = int(request.form.get('last_done_km', 0))
    from datetime import date, timedelta
    next_date = ''
    if last_done_date and interval_months > 0:
        from dateutil.relativedelta import relativedelta
        try:
            d = date.fromisoformat(last_done_date)
            next_date = (d + relativedelta(months=interval_months)).isoformat()
        except (ValueError, TypeError, AttributeError):
            pass
    next_km = last_done_km + interval_km if interval_km > 0 else 0
    with get_db() as conn:
        conn.execute("""INSERT INTO maintenance_plans
            (car_id, service_type, interval_km, interval_months, last_done_date, last_done_km, next_due_date, next_due_km)
            VALUES (?,?,?,?,?,?,?,?)""",
            (car_id, service_type, interval_km, interval_months, last_done_date, last_done_km, next_date, next_km))
        conn.commit()
    flash("Plan de maintenance ajouté !", "success")
    return redirect("/maintenance_plans")



@ops_bp.route("/maintenance_plans/done/<int:plan_id>", methods=["POST"])
@login_required
def mark_maintenance_done(plan_id):
    from datetime import date
    with get_db() as conn:
        plan = conn.execute("SELECT * FROM maintenance_plans WHERE id=?", (plan_id,)).fetchone()
        if plan:
            today_str = date.today().isoformat()
            car = conn.execute("SELECT mileage FROM cars WHERE id=?", (plan[1],)).fetchone()
            current_km = car[0] if car else 0
            next_date = ''
            if plan[4] > 0:
                try:
                    from dateutil.relativedelta import relativedelta
                    next_date = (date.today() + relativedelta(months=plan[4])).isoformat()
                except (ValueError, TypeError, AttributeError):
                    pass
            next_km = current_km + plan[3] if plan[3] > 0 else 0
            conn.execute("UPDATE maintenance_plans SET last_done_date=?, last_done_km=?, next_due_date=?, next_due_km=? WHERE id=?",
                        (today_str, current_km, next_date, next_km, plan_id))
            conn.commit()
    flash("Maintenance marquée comme effectuée !", "success")
    return redirect("/maintenance_plans")



@ops_bp.route("/maintenance_plans/delete/<int:plan_id>", methods=["POST"])
@login_required
def delete_maintenance_plan(plan_id):
    with get_db() as conn:
        conn.execute("UPDATE maintenance_plans SET active=0 WHERE id=?", (plan_id,))
        conn.commit()
    flash("Plan supprimé", "success")
    return redirect("/maintenance_plans")



# ─── Phase 8 Feature 2: Smart Alerts System ───
@ops_bp.route("/smart_alerts")
@login_required
def smart_alerts():
    with get_db() as conn:
        # Generate alerts
        from datetime import date, timedelta
        today = date.today()
        today_str = today.isoformat()
        # Low inventory alerts
        low_items = conn.execute("SELECT id, name, quantity, min_quantity FROM inventory WHERE quantity <= min_quantity").fetchall()
        for item in low_items:
            existing = conn.execute("SELECT id FROM smart_alerts WHERE alert_type='low_stock' AND related_id=? AND is_read=0", (item[0],)).fetchone()
            if not existing:
                conn.execute("INSERT INTO smart_alerts (alert_type, title, message, severity, related_id) VALUES (?,?,?,?,?)",
                    ('low_stock', f'Stock bas: {item[1]}', f'Quantité: {item[2]}/{item[3]}', 'warning', item[0]))
        # Unpaid invoices > 7 days
        old_unpaid = conn.execute("SELECT id, amount, date FROM invoices WHERE status IN ('unpaid','Non payée') AND date <= ?",
            ((today - timedelta(days=7)).isoformat(),)).fetchall()
        for inv in old_unpaid:
            existing = conn.execute("SELECT id FROM smart_alerts WHERE alert_type='overdue_invoice' AND related_id=? AND is_read=0", (inv[0],)).fetchone()
            if not existing:
                conn.execute("INSERT INTO smart_alerts (alert_type, title, message, severity, related_id) VALUES (?,?,?,?,?)",
                    ('overdue_invoice', f'Facture #{inv[0]} impayée', f'{inv[1]:.0f} DH depuis {inv[2]}', 'danger', inv[0]))
        # VIP customers not returning (60+ days)
        vip_gone = conn.execute("""
            SELECT c.id, c.name, MAX(a.date) as last_visit FROM customers c
            JOIN cars cr ON cr.customer_id = c.id
            JOIN appointments a ON a.car_id = cr.id
            GROUP BY c.id HAVING last_visit <= ?
        """, ((today - timedelta(days=60)).isoformat(),)).fetchall()
        for v in vip_gone[:10]:
            existing = conn.execute("SELECT id FROM smart_alerts WHERE alert_type='vip_churn' AND related_id=? AND is_read=0", (v[0],)).fetchone()
            if not existing:
                conn.execute("INSERT INTO smart_alerts (alert_type, title, message, severity, related_id) VALUES (?,?,?,?,?)",
                    ('vip_churn', f'Client absent: {v[1]}', f'Dernière visite: {v[2]}', 'info', v[0]))
        # Warranty expiring soon (7 days)
        exp_warranties = conn.execute("SELECT w.id, c.name, w.service, w.end_date FROM warranties w JOIN customers c ON w.customer_id=c.id WHERE w.status='active' AND w.end_date BETWEEN ? AND ?",
            (today_str, (today + timedelta(days=7)).isoformat())).fetchall()
        for w in exp_warranties:
            existing = conn.execute("SELECT id FROM smart_alerts WHERE alert_type='warranty_expiring' AND related_id=? AND is_read=0", (w[0],)).fetchone()
            if not existing:
                conn.execute("INSERT INTO smart_alerts (alert_type, title, message, severity, related_id) VALUES (?,?,?,?,?)",
                    ('warranty_expiring', f'Garantie expire: {w[1]}', f'{w[2]} — expire le {w[3]}', 'warning', w[0]))
        conn.commit()
        alerts = conn.execute("SELECT * FROM smart_alerts ORDER BY is_read ASC, created_at DESC LIMIT 100").fetchall()
        unread = conn.execute("SELECT COUNT(*) FROM smart_alerts WHERE is_read=0").fetchone()[0]
    return render_template("smart_alerts.html", alerts=alerts, unread=unread)



@ops_bp.route("/smart_alerts/read/<int:aid>", methods=["POST"])
@login_required
def mark_alert_read(aid):
    with get_db() as conn:
        conn.execute("UPDATE smart_alerts SET is_read=1 WHERE id=?", (aid,))
        conn.commit()
    return redirect("/smart_alerts")



@ops_bp.route("/smart_alerts/read_all", methods=["POST"])
@login_required
def mark_all_alerts_read():
    with get_db() as conn:
        conn.execute("UPDATE smart_alerts SET is_read=1")
        conn.commit()
    flash("Toutes les alertes marquées comme lues", "success")
    return redirect("/smart_alerts")



# ─── Phase 8 Feature 9: Subscriptions & Contracts ───
@ops_bp.route("/subscriptions")
@login_required
def subscriptions_list():
    with get_db() as conn:
        subs = conn.execute("""
            SELECT s.*, c.name, c.phone FROM subscriptions s
            JOIN customers c ON s.customer_id=c.id
            ORDER BY s.created_at DESC
        """).fetchall()
        customers = conn.execute("SELECT id, name FROM customers ORDER BY name").fetchall()
        from datetime import date
        today_str = date.today().isoformat()
        # Auto-expire
        for s in subs:
            if s[9] == 'active' and s[8] < today_str:
                conn.execute("UPDATE subscriptions SET status='expired' WHERE id=?", (s[0],))
        conn.commit()
        active_count = len([s for s in subs if s[9] == 'active' and s[8] >= today_str])
        total_revenue = sum(s[6] for s in subs if s[9] == 'active')
    return render_template("subscriptions.html", subs=subs, customers=customers,
                          active_count=active_count, total_revenue=total_revenue)



@ops_bp.route("/subscriptions/add", methods=["POST"])
@login_required
def add_subscription():
    customer_id = int(request.form.get('customer_id', 0))
    plan_name = request.form.get('plan_name', '')
    services_included = request.form.get('services_included', '')
    total_sessions = int(request.form.get('total_sessions', 12))
    price = float(request.form.get('price', 0))
    start_date = request.form.get('start_date', '')
    end_date = request.form.get('end_date', '')
    with get_db() as conn:
        conn.execute("""INSERT INTO subscriptions (customer_id, plan_name, services_included, total_sessions, price, start_date, end_date)
            VALUES (?,?,?,?,?,?,?)""",
            (customer_id, plan_name, services_included, total_sessions, price, start_date, end_date))
        conn.commit()
    flash("Abonnement créé !", "success")
    return redirect("/subscriptions")



@ops_bp.route("/subscriptions/use/<int:sid>", methods=["POST"])
@login_required
def use_subscription_session(sid):
    with get_db() as conn:
        sub = conn.execute("SELECT * FROM subscriptions WHERE id=?", (sid,)).fetchone()
        if sub and sub[5] < sub[4]:
            conn.execute("UPDATE subscriptions SET used_sessions=used_sessions+1 WHERE id=?", (sid,))
            if sub[5] + 1 >= sub[4]:
                conn.execute("UPDATE subscriptions SET status='completed' WHERE id=?", (sid,))
            conn.commit()
            flash(f"Séance utilisée ({sub[5]+1}/{sub[4]})", "success")
        else:
            flash("Toutes les séances ont été utilisées", "warning")
    return redirect("/subscriptions")



@ops_bp.route("/subscriptions/cancel/<int:sid>", methods=["POST"])
@login_required
def cancel_subscription(sid):
    with get_db() as conn:
        conn.execute("UPDATE subscriptions SET status='cancelled' WHERE id=?", (sid,))
        conn.commit()
    flash("Abonnement annulé", "info")
    return redirect("/subscriptions")



# ─── 5. Suivi Produits & Consommation ───

@ops_bp.route("/product_usage")
@login_required
def product_usage():
    month = request.args.get("month", "")
    from datetime import datetime
    if not month:
        month = datetime.now().strftime("%Y-%m")
    with get_db() as conn:
        usage = conn.execute("""SELECT pu.*, a.date, c.plate, c.brand, c.model, c.vehicle_type
            FROM product_usage pu JOIN appointments a ON pu.appointment_id = a.id
            JOIN cars c ON a.car_id = c.id
            WHERE strftime('%%Y-%%m', a.date) = ?
            ORDER BY a.date DESC""", (month,)).fetchall()
        # Product summary
        summary = conn.execute("""SELECT product_name, unit, SUM(quantity_used) as total_qty,
            SUM(total_cost) as total_cost, COUNT(*) as usage_count
            FROM product_usage pu JOIN appointments a ON pu.appointment_id = a.id
            WHERE strftime('%%Y-%%m', a.date) = ?
            GROUP BY product_name ORDER BY total_cost DESC""", (month,)).fetchall()
        total_cost = sum(r['total_cost'] for r in summary) if summary else 0
        # By vehicle type
        by_type = conn.execute("""SELECT vehicle_type, SUM(total_cost) as cost, COUNT(*) as cnt
            FROM product_usage WHERE strftime('%%Y-%%m', created_at) = ?
            GROUP BY vehicle_type""", (month,)).fetchall()
    return render_template("product_usage.html", usage=usage, summary=summary, total_cost=total_cost,
                          by_type=by_type, month=month)



@ops_bp.route("/product_usage/add", methods=["POST"])
@login_required
def product_usage_add():
    appointment_id = request.form.get("appointment_id", 0, type=int)
    product_name = request.form.get("product_name", "")
    quantity_used = request.form.get("quantity_used", 0, type=float)
    unit = request.form.get("unit", "ml")
    unit_cost = request.form.get("unit_cost", 0, type=float)
    vehicle_type = request.form.get("vehicle_type", "voiture")
    total_cost = quantity_used * unit_cost
    if appointment_id and product_name:
        with get_db() as conn:
            conn.execute("""INSERT INTO product_usage (appointment_id, product_name, quantity_used, unit, unit_cost, total_cost, vehicle_type)
                VALUES (?,?,?,?,?,?,?)""", (appointment_id, product_name, quantity_used, unit, unit_cost, total_cost, vehicle_type))
            conn.commit()
        flash("Consommation enregistrée ✅", "success")
    return redirect("/product_usage")



# ─── 6. Packs Detailing ───

@ops_bp.route("/detailing_packs")
@login_required
def detailing_packs():
    with get_db() as conn:
        packs = conn.execute("SELECT * FROM detailing_packs ORDER BY vehicle_type, name").fetchall()
        services = conn.execute("SELECT id, name, price FROM services ORDER BY name").fetchall()
    return render_template("detailing_packs.html", packs=packs, services=services)



@ops_bp.route("/detailing_pack/add", methods=["POST"])
@login_required
def detailing_pack_add():
    name = request.form.get("name", "").strip()
    description = request.form.get("description", "")
    vehicle_type = request.form.get("vehicle_type", "all")
    included_services = ",".join(request.form.getlist("services"))
    regular_price = request.form.get("regular_price", 0, type=float)
    pack_price = request.form.get("pack_price", 0, type=float)
    duration = request.form.get("duration_minutes", 60, type=int)
    if name:
        with get_db() as conn:
            conn.execute("""INSERT INTO detailing_packs (name, description, vehicle_type, included_services,
                regular_price, pack_price, duration_minutes)
                VALUES (?,?,?,?,?,?,?)""", (name, description, vehicle_type, included_services, regular_price, pack_price, duration))
            conn.commit()
        flash("Pack créé ✅", "success")
    return redirect("/detailing_packs")



@ops_bp.route("/detailing_pack/toggle/<int:pid>")
@login_required
def detailing_pack_toggle(pid):
    with get_db() as conn:
        conn.execute("UPDATE detailing_packs SET is_active = CASE WHEN is_active=1 THEN 0 ELSE 1 END WHERE id=?", (pid,))
        conn.commit()
    return redirect("/detailing_packs")



# ─── 7. Abonnements Lavage ───

@ops_bp.route("/wash_subscriptions")
@login_required
def wash_subscriptions():
    with get_db() as conn:
        subs = conn.execute("""SELECT ws.*, cu.name as customer_name, c.plate, c.brand, c.model, c.vehicle_type
            FROM wash_subscriptions ws JOIN customers cu ON ws.customer_id = cu.id
            LEFT JOIN cars c ON ws.car_id = c.id
            ORDER BY ws.created_at DESC""").fetchall()
        stats = {
            'active': conn.execute("SELECT COUNT(*) FROM wash_subscriptions WHERE status='active'").fetchone()[0],
            'total_revenue': conn.execute("SELECT COALESCE(SUM(price),0) FROM wash_subscriptions").fetchone()[0],
            'total_washes': conn.execute("SELECT COALESCE(SUM(used_washes),0) FROM wash_subscriptions").fetchone()[0],
            'expiring': conn.execute("SELECT COUNT(*) FROM wash_subscriptions WHERE status='active' AND end_date <= date('now', '+7 days')").fetchone()[0]
        }
        customers = conn.execute("SELECT id, name FROM customers ORDER BY name").fetchall()
        cars = conn.execute("SELECT id, plate, brand, model, customer_id, vehicle_type FROM cars ORDER BY plate").fetchall()
        services = conn.execute("SELECT id, name FROM services ORDER BY name").fetchall()
    return render_template("wash_subscriptions.html", subscriptions=subs, stats=stats,
                          customers=customers, cars=cars, services=services)



@ops_bp.route("/wash_subscription/add", methods=["POST"])
@login_required
def wash_subscription_add():
    customer_id = request.form.get("customer_id", 0, type=int)
    car_id = request.form.get("car_id", 0, type=int)
    plan_name = request.form.get("plan_name", "")
    plan_type = request.form.get("plan_type", "monthly")
    vehicle_type = request.form.get("vehicle_type", "voiture")
    included_washes = request.form.get("included_washes", 4, type=int)
    included_services = ",".join(request.form.getlist("services"))
    price = request.form.get("price", 0, type=float)
    start_date = request.form.get("start_date", "")
    end_date = request.form.get("end_date", "")
    auto_renew = 1 if request.form.get("auto_renew") else 0
    if customer_id and plan_name and start_date:
        with get_db() as conn:
            conn.execute("""INSERT INTO wash_subscriptions (customer_id, car_id, plan_name, plan_type, vehicle_type,
                included_washes, included_services, price, start_date, end_date, auto_renew)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (customer_id, car_id, plan_name, plan_type, vehicle_type, included_washes, included_services, price, start_date, end_date, auto_renew))
            conn.commit()
        flash("Abonnement créé ✅", "success")
    return redirect("/wash_subscriptions")



@ops_bp.route("/wash_subscription/use/<int:sid>", methods=["POST"])
@login_required
def wash_subscription_use(sid):
    with get_db() as conn:
        sub = conn.execute("SELECT * FROM wash_subscriptions WHERE id=?", (sid,)).fetchone()
        if sub and sub['used_washes'] < sub['included_washes']:
            new_used = sub['used_washes'] + 1
            status = 'completed' if new_used >= sub['included_washes'] else 'active'
            conn.execute("UPDATE wash_subscriptions SET used_washes=?, status=? WHERE id=?", (new_used, status, sid))
            conn.commit()
            flash("Lavage utilisé ✅", "success")
    return redirect("/wash_subscriptions")



# ─── 8. Portfolio Public ───

@ops_bp.route("/portfolio")
def portfolio_public():
    category = request.args.get("category", "")
    vtype = request.args.get("type", "")
    with get_db() as conn:
        where = "WHERE g.is_portfolio = 1"
        params = []
        if category:
            where += " AND g.caption LIKE ?"
            params.append(f"%{category}%")
        if vtype:
            where += " AND c.vehicle_type = ?"
            params.append(vtype)
        photos = conn.execute(f"""SELECT g.*, c.plate, c.brand, c.model, c.vehicle_type
            FROM vehicle_gallery g JOIN cars c ON g.car_id = c.id
            {where} ORDER BY g.created_at DESC LIMIT 50""", params).fetchall()
        reviews = conn.execute("""SELECT r.*, cu.name as customer_name
            FROM client_reviews r JOIN customers cu ON r.customer_id = cu.id
            WHERE r.is_public = 1 AND r.is_featured = 1
            ORDER BY r.created_at DESC LIMIT 6""").fetchall()
        stats = {
            'total_vehicles': conn.execute("SELECT COUNT(DISTINCT car_id) FROM vehicle_gallery WHERE is_portfolio=1").fetchone()[0],
            'total_photos': conn.execute("SELECT COUNT(*) FROM vehicle_gallery WHERE is_portfolio=1").fetchone()[0],
            'avg_rating': conn.execute("SELECT COALESCE(AVG(rating), 5) FROM client_reviews WHERE is_public=1").fetchone()[0]
        }
        shop = {}
        for r in conn.execute("SELECT key, value FROM settings").fetchall():
            shop[r['key']] = r['value']
    return render_template("portfolio.html", photos=photos, reviews=reviews, stats=stats, shop=shop,
                          category=category, vtype=vtype)



# ─── 9. Avis Clients avec Photos ───

@ops_bp.route("/client_reviews")
@login_required
def client_reviews():
    with get_db() as conn:
        reviews = conn.execute("""SELECT r.*, cu.name as customer_name, c.plate, c.brand, c.model, c.vehicle_type
            FROM client_reviews r JOIN customers cu ON r.customer_id = cu.id
            LEFT JOIN cars c ON r.car_id = c.id
            ORDER BY r.created_at DESC""").fetchall()
        stats = {
            'total': conn.execute("SELECT COUNT(*) FROM client_reviews").fetchone()[0],
            'avg_rating': conn.execute("SELECT COALESCE(AVG(rating), 0) FROM client_reviews").fetchone()[0],
            'five_star': conn.execute("SELECT COUNT(*) FROM client_reviews WHERE rating=5").fetchone()[0],
            'public': conn.execute("SELECT COUNT(*) FROM client_reviews WHERE is_public=1").fetchone()[0]
        }
        customers = conn.execute("SELECT id, name FROM customers ORDER BY name").fetchall()
        cars = conn.execute("SELECT id, plate, brand, model, customer_id FROM cars ORDER BY plate").fetchall()
    return render_template("client_reviews.html", reviews=reviews, stats=stats, customers=customers, cars=cars)



@ops_bp.route("/client_review/add", methods=["POST"])
@login_required
def client_review_add():
    import os, uuid, json
    from werkzeug.utils import secure_filename
    customer_id = request.form.get("customer_id", 0, type=int)
    car_id = request.form.get("car_id", 0, type=int)
    rating = request.form.get("rating", 5, type=int)
    comment = request.form.get("comment", "")
    service_type = request.form.get("service_type", "")
    is_public = 1 if request.form.get("is_public") else 0
    is_featured = 1 if request.form.get("is_featured") else 0
    photo_paths = []
    photos = request.files.getlist("photos")
    upload_dir = os.path.join("static", "uploads", "reviews")
    os.makedirs(upload_dir, exist_ok=True)
    for photo in photos:
        if photo and photo.filename:
            fname = secure_filename(f"{uuid.uuid4().hex}_{photo.filename}")
            photo.save(os.path.join(upload_dir, fname))
            photo_paths.append(f"uploads/reviews/{fname}")
    if customer_id:
        with get_db() as conn:
            conn.execute("""INSERT INTO client_reviews (customer_id, car_id, rating, comment, photos, service_type, is_public, is_featured)
                VALUES (?,?,?,?,?,?,?,?)""", (customer_id, car_id, rating, comment, json.dumps(photo_paths), service_type, is_public, is_featured))
            conn.commit()
        flash("Avis ajouté ✅", "success")
    return redirect("/client_reviews")



@ops_bp.route("/client_review/respond/<int:rid>", methods=["POST"])
@login_required
def client_review_respond(rid):
    from datetime import datetime
    response = request.form.get("response", "")
    with get_db() as conn:
        conn.execute("UPDATE client_reviews SET response=?, response_date=? WHERE id=?",
                    (response, datetime.now().strftime("%Y-%m-%d %H:%M"), rid))
        conn.commit()
    flash("Réponse enregistrée ✅", "success")
    return redirect("/client_reviews")



@ops_bp.route("/live_tracking")
@login_required
def live_tracking():
    with get_db() as conn:
        from datetime import date
        today = date.today().isoformat()
        vehicles = conn.execute("""SELECT vs.*, a.date, a.service, c.plate, c.brand, c.model, c.vehicle_type, c.color,
            cu.name as customer_name, cu.phone as customer_phone,
            u.username as tech_name
            FROM vehicle_status vs JOIN appointments a ON vs.appointment_id = a.id
            JOIN cars c ON vs.car_id = c.id JOIN customers cu ON c.customer_id = cu.id
            LEFT JOIN users u ON vs.assigned_tech = u.id
            WHERE a.date = ? ORDER BY vs.progress_pct DESC""", (today,)).fetchall()
        # Today's appointments without tracking
        untracked = conn.execute("""SELECT a.id, a.service, c.plate, c.brand, c.model, c.vehicle_type, cu.name
            FROM appointments a JOIN cars c ON a.car_id=c.id JOIN customers cu ON c.customer_id=cu.id
            WHERE a.date=? AND a.status != 'cancelled'
            AND a.id NOT IN (SELECT appointment_id FROM vehicle_status)""", (today,)).fetchall()
        techs = conn.execute("SELECT id, username FROM users WHERE role IN ('admin','tech') ORDER BY username").fetchall()
    return render_template("live_tracking.html", vehicles=vehicles, untracked=untracked,
                          techs=techs, steps=CARE_STEPS)



@ops_bp.route("/live_tracking/start/<int:appointment_id>", methods=["POST"])
@login_required
def live_tracking_start(appointment_id):
    from datetime import datetime
    tech_id = request.form.get("tech_id", 0, type=int)
    bay = request.form.get("bay_number", 0, type=int)
    with get_db() as conn:
        appt = conn.execute("SELECT car_id FROM appointments WHERE id=?", (appointment_id,)).fetchone()
        if appt:
            conn.execute("""INSERT INTO vehicle_status (appointment_id, car_id, current_step, progress_pct, started_at, assigned_tech, bay_number)
                VALUES (?,?,'reception',0,?,?,?)""", (appointment_id, appt['car_id'], datetime.now().strftime("%Y-%m-%d %H:%M"), tech_id, bay))
            conn.execute("""INSERT INTO status_updates (appointment_id, step_name, status, started_at)
                VALUES (?,?,'in_progress',?)""", (appointment_id, 'reception', datetime.now().strftime("%Y-%m-%d %H:%M")))
            conn.execute("UPDATE appointments SET status='in_progress' WHERE id=?", (appointment_id,))
            conn.commit()
    return redirect("/live_tracking")



@ops_bp.route("/live_tracking/update/<int:vs_id>", methods=["POST"])
@login_required
def live_tracking_update(vs_id):
    from datetime import datetime
    new_step = request.form.get("step", "")
    notes = request.form.get("notes", "")
    with get_db() as conn:
        vs = conn.execute("SELECT * FROM vehicle_status WHERE id=?", (vs_id,)).fetchone()
        if vs:
            step_names = [s[0] for s in CARE_STEPS]
            if new_step in step_names:
                idx = step_names.index(new_step)
                pct = int((idx / (len(step_names) - 1)) * 100)
                now = datetime.now().strftime("%Y-%m-%d %H:%M")
                # Complete previous step
                conn.execute("""UPDATE status_updates SET status='completed', completed_at=?
                    WHERE appointment_id=? AND step_name=? AND status='in_progress'""",
                    (now, vs['appointment_id'], vs['current_step']))
                # Start new step
                conn.execute("""INSERT INTO status_updates (appointment_id, step_name, status, started_at, notes)
                    VALUES (?,?,'in_progress',?,?)""", (vs['appointment_id'], new_step, now, notes))
                # Update vehicle_status
                conn.execute("UPDATE vehicle_status SET current_step=?, progress_pct=?, last_update=? WHERE id=?",
                            (new_step, pct, now, vs_id))
                if new_step == 'pret':
                    conn.execute("UPDATE appointments SET status='completed' WHERE id=?", (vs['appointment_id'],))
                conn.commit()
    return redirect("/live_tracking")



# ─── 2. Moteur Upsell Intelligent ───

@ops_bp.route("/upsell_rules")
@login_required
@admin_required
def upsell_rules():
    with get_db() as conn:
        rules = conn.execute("SELECT * FROM upsell_rules ORDER BY created_at DESC").fetchall()
        services = conn.execute("SELECT DISTINCT name FROM services ORDER BY name").fetchall()
    return render_template("upsell_rules.html", rules=rules, services=services)



@ops_bp.route("/upsell_rule/add", methods=["POST"])
@login_required
@admin_required
def upsell_rule_add():
    name = request.form.get("name", "").strip()
    trigger_type = request.form.get("trigger_type", "")
    trigger_value = request.form.get("trigger_value", "")
    suggestion_text = request.form.get("suggestion_text", "")
    discount_pct = request.form.get("discount_pct", 0, type=float)
    target_service = request.form.get("target_service", "")
    vehicle_types = request.form.get("vehicle_types", "all")
    if name and suggestion_text:
        with get_db() as conn:
            conn.execute("""INSERT INTO upsell_rules (name, trigger_type, trigger_value, suggestion_text,
                discount_pct, target_service, vehicle_types) VALUES (?,?,?,?,?,?,?)""",
                (name, trigger_type, trigger_value, suggestion_text, discount_pct, target_service, vehicle_types))
            conn.commit()
        flash("Règle upsell créée ✅", "success")
    return redirect("/upsell_rules")



@ops_bp.route("/upsell_rule/toggle/<int:rid>")
@login_required
@admin_required
def upsell_rule_toggle(rid):
    with get_db() as conn:
        conn.execute("UPDATE upsell_rules SET is_active = CASE WHEN is_active=1 THEN 0 ELSE 1 END WHERE id=?", (rid,))
        conn.commit()
    return redirect("/upsell_rules")



# ─── 7. Rappels Automatiques Smart ───

@ops_bp.route("/smart_reminders")
@login_required
def smart_reminders_view():
    with get_db() as conn:
        # Auto-generate reminders
        from datetime import date, timedelta
        today = date.today().isoformat()
        # Treatment expiry reminders
        expiring = conn.execute("""SELECT t.id, t.car_id, t.customer_id, t.treatment_type, t.warranty_expiry,
            cu.name, c.plate FROM treatments t JOIN customers cu ON t.customer_id=cu.id JOIN cars c ON t.car_id=c.id
            WHERE t.status='active' AND t.warranty_expiry != '' AND t.warranty_expiry <= date('now','+30 days')
            AND t.id NOT IN (SELECT reference_id FROM smart_reminders WHERE reminder_type='treatment_expiry' AND reference_id=t.id)
            """).fetchall()
        for t in expiring:
            conn.execute("""INSERT INTO smart_reminders (customer_id, car_id, reminder_type, title, message, due_date, reference_type, reference_id)
                VALUES (?,?,?,?,?,?,?,?)""",
                (t['customer_id'], t['car_id'], 'treatment_expiry',
                 f"Traitement {t['treatment_type']} expire bientôt",
                 f"{t['name']} — {t['plate']}: votre {t['treatment_type']} expire le {t['warranty_expiry']}",
                 t['warranty_expiry'], 'treatment', t['id']))
        # Unused wash subscriptions
        unused = conn.execute("""SELECT ws.id, ws.customer_id, ws.car_id, ws.plan_name, ws.used_washes, ws.included_washes,
            cu.name FROM wash_subscriptions ws JOIN customers cu ON ws.customer_id=cu.id
            WHERE ws.status='active' AND ws.used_washes < ws.included_washes AND ws.end_date <= date('now','+7 days')
            AND ws.id NOT IN (SELECT reference_id FROM smart_reminders WHERE reminder_type='subscription_expiring' AND reference_id=ws.id)
            """).fetchall()
        for u in unused:
            conn.execute("""INSERT INTO smart_reminders (customer_id, car_id, reminder_type, title, message, due_date, reference_type, reference_id)
                VALUES (?,?,?,?,?,?,?,?)""",
                (u['customer_id'], u['car_id'], 'subscription_expiring',
                 f"Abonnement {u['plan_name']} expire — lavages non utilisés",
                 f"{u['name']}: {u['included_washes'] - u['used_washes']} lavages restants sur votre abonnement",
                 date.today().isoformat(), 'subscription', u['id']))
        conn.commit()
        # Fetch all reminders
        status_filter = request.args.get("status", "")
        if status_filter and status_filter in ('pending', 'sent', 'dismissed'):
            where = "WHERE sr.status=?"
            where_params = [status_filter]
        else:
            where = ""
            where_params = []
            status_filter = ""
        reminders = conn.execute(f"""SELECT sr.*, cu.name as customer_name, cu.phone, c.plate, c.brand, c.model
            FROM smart_reminders sr JOIN customers cu ON sr.customer_id=cu.id LEFT JOIN cars c ON sr.car_id=c.id
            {where} ORDER BY sr.due_date ASC""", where_params).fetchall()
        stats = {
            'pending': conn.execute("SELECT COUNT(*) FROM smart_reminders WHERE status='pending'").fetchone()[0],
            'sent': conn.execute("SELECT COUNT(*) FROM smart_reminders WHERE status='sent'").fetchone()[0],
            'total': conn.execute("SELECT COUNT(*) FROM smart_reminders").fetchone()[0]
        }
    return render_template("smart_reminders_care.html", reminders=reminders, stats=stats, status_filter=status_filter)



@ops_bp.route("/smart_reminder/mark/<int:rid>/<action>")
@login_required
def smart_reminder_mark(rid, action):
    from datetime import datetime
    if action in ('sent', 'dismissed'):
        with get_db() as conn:
            conn.execute("UPDATE smart_reminders SET status=?, sent_at=? WHERE id=?",
                        (action, datetime.now().strftime("%Y-%m-%d %H:%M"), rid))
            conn.commit()
    return redirect("/smart_reminders")



# ─── 8. Configurateur Pack en Ligne ───

@ops_bp.route("/pack_configurator")
def pack_configurator():
    with get_db() as conn:
        services = conn.execute("SELECT id, name, price FROM services WHERE price > 0 ORDER BY name").fetchall()
        shop = {}
        for r in conn.execute("SELECT key, value FROM settings").fetchall():
            shop[r['key']] = r['value']
    return render_template("pack_configurator.html", services=services, shop=shop)



@ops_bp.route("/pack_configurator/submit", methods=["POST"])
@csrf.exempt
def pack_configurator_submit():
    import json
    name = request.form.get("name", "").strip()
    phone = request.form.get("phone", "").strip()
    email = request.form.get("email", "").strip()
    vehicle_type = request.form.get("vehicle_type", "voiture")
    selected = request.form.getlist("services")
    notes = request.form.get("notes", "")
    total_regular = request.form.get("total_regular", 0, type=float)
    total_discounted = request.form.get("total_discounted", 0, type=float)
    discount_pct = request.form.get("discount_pct", 0, type=float)
    if selected:
        with get_db() as conn:
            conn.execute("""INSERT INTO pack_configurations (customer_name, customer_phone, customer_email,
                vehicle_type, selected_services, total_regular, total_discounted, discount_pct, notes)
                VALUES (?,?,?,?,?,?,?,?,?)""",
                (name, phone, email, vehicle_type, json.dumps(selected), total_regular, total_discounted, discount_pct, notes))
            conn.commit()
    return render_template("pack_configurator_success.html", name=name, total=total_discounted, discount=discount_pct)



@ops_bp.route("/pack_configurations")
@login_required
def pack_configurations():
    with get_db() as conn:
        configs = conn.execute("SELECT * FROM pack_configurations ORDER BY created_at DESC").fetchall()
    return render_template("pack_configurations.html", configs=configs)



# ─── 9. Rapport Tendances Produits ───

@ops_bp.route("/product_trends")
@login_required
@admin_required
def product_trends():
    from datetime import date
    with get_db() as conn:
        # Monthly product usage last 6 months
        months = []
        for i in range(5, -1, -1):
            m = date.today().month - i
            y = date.today().year
            while m <= 0:
                m += 12; y -= 1
            ms = f"{y}-{m:02d}"
            total = conn.execute("SELECT COALESCE(SUM(total_cost),0) FROM product_usage WHERE strftime('%%Y-%%m',created_at)=?", (ms,)).fetchone()[0]
            qty = conn.execute("SELECT COALESCE(SUM(quantity_used),0) FROM product_usage WHERE strftime('%%Y-%%m',created_at)=?", (ms,)).fetchone()[0]
            months.append({'month': ms, 'cost': total, 'quantity': qty})
        # Top products all time
        top = conn.execute("""SELECT product_name, unit, SUM(quantity_used) as total_qty, SUM(total_cost) as total_cost,
            COUNT(*) as usage_count, AVG(unit_cost) as avg_cost
            FROM product_usage GROUP BY product_name ORDER BY total_cost DESC LIMIT 15""").fetchall()
        # Low stock warning (products used a lot but maybe running low)
        high_usage = conn.execute("""SELECT product_name, SUM(quantity_used) as monthly_usage
            FROM product_usage WHERE created_at >= date('now','-30 days')
            GROUP BY product_name ORDER BY monthly_usage DESC LIMIT 10""").fetchall()
        # By vehicle type
        by_type = conn.execute("""SELECT vehicle_type, SUM(total_cost) as cost, COUNT(*) as cnt
            FROM product_usage GROUP BY vehicle_type ORDER BY cost DESC""").fetchall()
    return render_template("product_trends.html", months=months, top=top, high_usage=high_usage, by_type=by_type)



# ─── 6. Chronomètre de Service ───

@ops_bp.route("/service_timer/<int:appointment_id>")
@login_required
def service_timer_view(appointment_id):
    with get_db() as conn:
        appt = conn.execute("""SELECT a.*, c.name as customer_name, ca.brand, ca.model, ca.plate
            FROM appointments a
            LEFT JOIN cars ca ON a.car_id=ca.id
            LEFT JOIN customers c ON ca.customer_id=c.id
            WHERE a.id=?""", (appointment_id,)).fetchone()
        if not appt:
            flash("RDV non trouvé", "danger")
            return redirect("/appointments")
        timers = conn.execute("""SELECT st.*, u.full_name as emp_name FROM service_timer st
            LEFT JOIN users u ON st.employee_id=u.id
            WHERE st.appointment_id=? ORDER BY st.created_at""", (appointment_id,)).fetchall()
        employees = conn.execute("SELECT id, full_name as name FROM users WHERE role IN ('employee','admin') ORDER BY full_name").fetchall()
        services = conn.execute("SELECT * FROM services ORDER BY name").fetchall()
    return render_template("service_timer.html", appt=appt, timers=timers, employees=employees, services=services)



@ops_bp.route("/service_timer/start", methods=["POST"])
@login_required
def service_timer_start():
    from datetime import datetime
    appt_id = request.form.get("appointment_id", 0, type=int)
    with get_db() as conn:
        svc = conn.execute("SELECT estimated_minutes FROM services WHERE name=?",
            (request.form.get("service_name", ""),)).fetchone()
        est = svc['estimated_minutes'] if svc and svc['estimated_minutes'] else 60
        conn.execute("""INSERT INTO service_timer (appointment_id, employee_id, service_name, estimated_minutes, started_at)
            VALUES (?,?,?,?,?)""", (appt_id, request.form.get("employee_id", 0, type=int),
            request.form.get("service_name", ""), est, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
        conn.execute("UPDATE appointments SET actual_start=COALESCE(NULLIF(actual_start,''), ?) WHERE id=?",
            (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), appt_id))
        conn.commit()
    flash("Chronomètre démarré", "success")
    return redirect(f"/service_timer/{appt_id}")



@ops_bp.route("/service_timer/stop/<int:timer_id>", methods=["POST"])
@login_required
def service_timer_stop(timer_id):
    from datetime import datetime
    with get_db() as conn:
        timer = conn.execute("SELECT * FROM service_timer WHERE id=?", (timer_id,)).fetchone()
        if timer and timer['started_at'] and not timer['ended_at']:
            now = datetime.now()
            started = datetime.strptime(timer['started_at'], "%Y-%m-%d %H:%M:%S")
            actual_min = int((now - started).total_seconds() / 60)
            eff = round((timer['estimated_minutes'] / max(actual_min, 1)) * 100, 1) if timer['estimated_minutes'] else 0
            conn.execute("""UPDATE service_timer SET ended_at=?, actual_minutes=?, efficiency_pct=? WHERE id=?""",
                (now.strftime("%Y-%m-%d %H:%M:%S"), actual_min, eff, timer_id))
            conn.execute("UPDATE appointments SET actual_end=? WHERE id=?",
                (now.strftime("%Y-%m-%d %H:%M:%S"), timer['appointment_id']))
            conn.commit()
            flash(f"Chronométrage arrêté — {actual_min} min (efficacité {eff}%)", "success")
            return redirect(f"/service_timer/{timer['appointment_id']}")
    flash("Timer non trouvé", "danger")
    return redirect("/appointments")



# ─── Phase 17: Enterprise Intelligence & Business Growth ───

# ── 1. Damage Claim Tracker ──
@ops_bp.route('/damage_claims')
@login_required
def damage_claims():
    with get_db() as conn:
        claims = conn.execute("""
            SELECT dc.*, c.name as customer_name, car.brand, car.model, car.plate
            FROM damage_claims dc
            LEFT JOIN customers c ON dc.customer_id = c.id
            LEFT JOIN cars car ON dc.car_id = car.id
            ORDER BY dc.reported_at DESC
        """).fetchall()
        stats = {
            'total': len(claims),
            'open': sum(1 for c in claims if c['status'] in ('reported', 'investigating')),
            'resolved': sum(1 for c in claims if c['status'] == 'resolved'),
            'total_compensation': sum(c['compensation_amount'] for c in claims if c['status'] == 'resolved')
        }
    return render_template('damage_claims.html', claims=claims, stats=stats)



@ops_bp.route('/damage_claim/add', methods=['POST'])
@login_required
def damage_claim_add():
    with get_db() as conn:
        conn.execute("""INSERT INTO damage_claims
            (appointment_id, customer_id, car_id, employee_id, damage_type, description, severity)
            VALUES (?,?,?,?,?,?,?)""",
            (int(request.form.get('appointment_id', 0)),
             int(request.form.get('customer_id', 0)),
             int(request.form.get('car_id', 0)),
             int(request.form.get('employee_id', 0)),
             request.form.get('damage_type', ''),
             request.form.get('description', ''),
             request.form.get('severity', 'minor')))
        conn.commit()
    flash("Réclamation enregistrée", "success")
    return redirect("/damage_claims")



@ops_bp.route('/damage_claim/update/<int:claim_id>', methods=['POST'])
@login_required
def damage_claim_update(claim_id):
    status = request.form['status']
    with get_db() as conn:
        resolved_at = datetime.now().strftime('%Y-%m-%d %H:%M') if status == 'resolved' else ''
        conn.execute("""UPDATE damage_claims SET status=?, compensation_amount=?,
            compensation_type=?, resolution_notes=?, resolved_at=? WHERE id=?""",
            (status, float(request.form.get('compensation_amount', 0)),
             request.form.get('compensation_type', 'discount'),
             request.form.get('resolution_notes', ''), resolved_at, claim_id))
        conn.commit()
    flash("Réclamation mise à jour", "success")
    return redirect("/damage_claims")

# ── 2. Before/After Comparison ──
@ops_bp.route('/before_after/<int:appointment_id>')
@login_required
def before_after(appointment_id):
    with get_db() as conn:
        appointment = conn.execute("""
            SELECT a.*, c.name as customer_name, car.brand, car.model, car.plate
            FROM appointments a
            LEFT JOIN customers c ON a.customer_id = c.id
            LEFT JOIN cars car ON a.car_id = car.id
            WHERE a.id = ?
        """, (appointment_id,)).fetchone()
        if not appointment:
            flash("Rendez-vous non trouvé", "danger")
            return redirect("/appointments")
        gallery = conn.execute("""
            SELECT * FROM vehicle_gallery
            WHERE appointment_id = ? ORDER BY photo_type, uploaded_at
        """, (appointment_id,)).fetchall()
        before_photos = [g for g in gallery if g['photo_type'] == 'before']
        after_photos = [g for g in gallery if g['photo_type'] == 'after']
    return render_template('before_after.html', appointment=appointment,
                          before_photos=before_photos, after_photos=after_photos)

# ── 3. Revenue Forecast ──
@ops_bp.route('/revenue_forecast')
@login_required
def revenue_forecast():
    with get_db() as conn:
        # Historical monthly data (last 12 months)
        historical = conn.execute("""
            SELECT strftime('%Y-%m', created_at) as month,
                   SUM(amount) as revenue, COUNT(*) as invoices,
                   AVG(amount) as avg_ticket
            FROM invoices WHERE status != 'cancelled'
            GROUP BY strftime('%Y-%m', created_at)
            ORDER BY month DESC LIMIT 12
        """).fetchall()

        # Calculate forecast for next 3 months
        if len(historical) >= 3:
            revenues = [h['revenue'] for h in historical[:6]]
            avg_revenue = sum(revenues) / len(revenues)
            trend = (revenues[0] - revenues[-1]) / len(revenues) if len(revenues) > 1 else 0
        else:
            avg_revenue = historical[0]['revenue'] if historical else 0
            trend = 0

        forecasts = []
        for i in range(1, 4):
            future_month = (datetime.now() + timedelta(days=30 * i)).strftime('%Y-%m')
            predicted = max(0, avg_revenue + (trend * i))
            # Seasonal adjustment (summer +15%, winter -10%)
            month_num = int(future_month.split('-')[1])
            if month_num in (6, 7, 8):
                predicted *= 1.15
            elif month_num in (12, 1, 2):
                predicted *= 0.9
            confidence = max(50, 95 - (i * 10) - (5 if len(historical) < 6 else 0))

            existing = conn.execute("SELECT * FROM revenue_forecast WHERE month=?", (future_month,)).fetchone()
            if not existing:
                conn.execute("""INSERT INTO revenue_forecast
                    (month, predicted_revenue, predicted_appointments, confidence)
                    VALUES (?,?,?,?)""",
                    (future_month, predicted,
                     int(predicted / max(avg_revenue / max(sum(h['invoices'] for h in historical[:6]) / min(len(historical), 6), 1), 1)) if avg_revenue > 0 else 0,
                     confidence))
            forecasts.append({
                'month': future_month, 'predicted': predicted,
                'confidence': confidence
            })
        conn.commit()

        # Load saved forecasts with actuals
        saved = conn.execute("SELECT * FROM revenue_forecast ORDER BY month DESC LIMIT 12").fetchall()

    return render_template('revenue_forecast.html', historical=historical,
                          forecasts=forecasts, saved=saved, avg_revenue=avg_revenue, trend=trend)

# ── 4. Customer Segments ──
@ops_bp.route('/customer_segments')
@login_required
def customer_segments():
    with get_db() as conn:
        # Recalculate segments
        customers = conn.execute("""
            SELECT c.id, c.name, c.phone, c.total_spent, c.total_visits, c.last_visit,
                   c.loyalty_level,
                   COALESCE(SUM(i.amount), 0) as real_spent,
                   COUNT(DISTINCT a.id) as real_visits,
                   MAX(a.date) as last_visit_date
            FROM customers c
            LEFT JOIN cars ca ON c.id = ca.customer_id
            LEFT JOIN appointments a ON ca.id = a.car_id
            LEFT JOIN invoices i ON a.id = i.appointment_id AND i.status != 'cancelled'
            GROUP BY c.id ORDER BY real_spent DESC
        """).fetchall()

        segments = {'vip': [], 'frequent': [], 'seasonal': [], 'new': [], 'at_risk': [], 'lost': []}
        today = datetime.now()

        for cust in customers:
            spent = cust['real_spent'] or 0
            visits = cust['real_visits'] or 0
            last = cust['last_visit_date']
            days_since = (today - datetime.strptime(last, '%Y-%m-%d')).days if last else 999
            avg_ticket = spent / max(visits, 1)

            # Segment logic
            if spent >= 2000 and visits >= 10:
                segment = 'vip'
                score = 95
            elif visits >= 5 and days_since < 60:
                segment = 'frequent'
                score = 80
            elif visits >= 2 and days_since > 60 and days_since < 180:
                segment = 'seasonal'
                score = 55
            elif days_since < 30 and visits <= 2:
                segment = 'new'
                score = 60
            elif days_since > 180:
                segment = 'lost'
                score = 15
            elif days_since > 90:
                segment = 'at_risk'
                score = 30
            else:
                segment = 'frequent'
                score = 65

            segments[segment].append({
                'id': cust['id'], 'name': cust['name'], 'phone': cust['phone'],
                'spent': spent, 'visits': visits, 'last_visit': last,
                'days_since': days_since, 'avg_ticket': avg_ticket, 'score': score
            })

            # Update DB
            conn.execute("""INSERT OR REPLACE INTO customer_segments
                (customer_id, segment, score, last_visit_days, total_spent, visit_count, avg_ticket, updated_at)
                VALUES (?,?,?,?,?,?,?,?)""",
                (cust['id'], segment, score, days_since, spent, visits, avg_ticket,
                 datetime.now().strftime('%Y-%m-%d %H:%M')))

        conn.commit()

    return render_template('customer_segments.html', segments=segments,
                          total=sum(len(v) for v in segments.values()))

# ── 5. Service Cost Calculator ──
@ops_bp.route('/service_cost_calculator')
@login_required
def service_cost_calculator():
    with get_db() as conn:
        services = conn.execute("""
            SELECT s.*, COUNT(a.id) as usage_count,
                   AVG(st.actual_minutes) as avg_time
            FROM services s
            LEFT JOIN appointments a ON a.service = s.name AND a.status = 'completed'
            LEFT JOIN service_timer st ON st.service_name = s.name
            GROUP BY s.id ORDER BY s.name
        """).fetchall()
        # Get hourly labor rate from settings
        hourly_rate = 15  # DT/hour default
        try:
            rate_setting = conn.execute("SELECT value FROM settings WHERE key='hourly_labor_rate'").fetchone()
            if rate_setting:
                hourly_rate = float(rate_setting['value'])
        except (ValueError, TypeError, AttributeError):
            pass
    return render_template('service_cost_calculator.html', services=services,
                          hourly_rate=hourly_rate)



@ops_bp.route('/knowledge_base/add', methods=['POST'])
@login_required
def knowledge_base_add():
    with get_db() as conn:
        conn.execute("""INSERT INTO knowledge_base (title, category, content, tags, is_pinned)
            VALUES (?,?,?,?,?)""",
            (request.form['title'], request.form.get('category', 'general'),
             request.form['content'], request.form.get('tags', ''),
             1 if request.form.get('is_pinned') else 0))
        conn.commit()
    flash("Article ajouté", "success")
    return redirect("/knowledge_base")



@ops_bp.route('/knowledge_base/view/<int:article_id>')
@login_required
def knowledge_base_view(article_id):
    with get_db() as conn:
        article = conn.execute("SELECT * FROM knowledge_base WHERE id=?", (article_id,)).fetchone()
        if not article:
            flash("Article non trouvé", "danger")
            return redirect("/knowledge_base")
        conn.execute("UPDATE knowledge_base SET views = views + 1 WHERE id=?", (article_id,))
        conn.commit()
    return render_template('knowledge_base_view.html', article=article)



@ops_bp.route('/knowledge_base/edit/<int:article_id>', methods=['POST'])
@login_required
def knowledge_base_edit(article_id):
    with get_db() as conn:
        conn.execute("""UPDATE knowledge_base SET title=?, category=?, content=?,
            tags=?, is_pinned=?, updated_at=? WHERE id=?""",
            (request.form['title'], request.form.get('category', 'general'),
             request.form['content'], request.form.get('tags', ''),
             1 if request.form.get('is_pinned') else 0,
             datetime.now().strftime('%Y-%m-%d %H:%M'), article_id))
        conn.commit()
    flash("Article mis à jour", "success")
    return redirect(f"/knowledge_base/view/{article_id}")



@ops_bp.route('/knowledge_base/delete/<int:article_id>')
@login_required
def knowledge_base_delete(article_id):
    with get_db() as conn:
        conn.execute("DELETE FROM knowledge_base WHERE id=?", (article_id,))
        conn.commit()
    flash("Article supprimé", "success")
    return redirect("/knowledge_base")




# ─── 18.4 Protection Renewal Reminders ──────────────────────────────────────

@ops_bp.route("/protection_renewals")
@login_required
def protection_renewals():
    with get_db() as conn:
        renewals = conn.execute("""
            SELECT a.id as appt_id, a.date, a.service, c.id as customer_id, c.name, c.phone,
                   car.brand, car.model, car.plate,
                   CASE 
                       WHEN LOWER(a.service) LIKE '%ppf%' THEN date(a.date, '+5 years')
                       WHEN LOWER(a.service) LIKE '%céramique%' OR LOWER(a.service) LIKE '%ceramique%' OR LOWER(a.service) LIKE '%ceramic%' THEN date(a.date, '+2 years')
                       WHEN LOWER(a.service) LIKE '%nano%' THEN date(a.date, '+1 year')
                       ELSE date(a.date, '+1 year')
                   END as renewal_date,
                   CASE
                       WHEN LOWER(a.service) LIKE '%ppf%' THEN 'PPF'
                       WHEN LOWER(a.service) LIKE '%céramique%' OR LOWER(a.service) LIKE '%ceramique%' OR LOWER(a.service) LIKE '%ceramic%' THEN 'Céramique'
                       WHEN LOWER(a.service) LIKE '%nano%' THEN 'Nano'
                       ELSE 'Protection'
                   END as protection_type
            FROM appointments a
            JOIN cars car ON a.car_id=car.id
            JOIN customers c ON car.customer_id=c.id
            WHERE a.status='completed'
              AND (LOWER(a.service) LIKE '%ppf%' OR LOWER(a.service) LIKE '%céramique%' 
                   OR LOWER(a.service) LIKE '%ceramique%' OR LOWER(a.service) LIKE '%ceramic%'
                   OR LOWER(a.service) LIKE '%nano%')
            ORDER BY renewal_date ASC
        """).fetchall()
        
        today = str(date.today())
        upcoming = [r for r in renewals if r['renewal_date'] and r['renewal_date'] >= today 
                    and r['renewal_date'] <= str(date.today() + timedelta(days=90))]
        overdue = [r for r in renewals if r['renewal_date'] and r['renewal_date'] < today]
        
        shop = get_all_settings()
    return render_template("protection_renewals.html", upcoming=upcoming, overdue=overdue,
                          all_renewals=renewals, shop=shop, day=str(date.today()))



@ops_bp.route("/protection_renewal/remind/<int:appt_id>")
@login_required
def protection_renewal_remind(appt_id):
    with get_db() as conn:
        data = conn.execute("""SELECT c.name, c.phone, car.brand, car.model, a.service, a.date
            FROM appointments a JOIN cars car ON a.car_id=car.id JOIN customers c ON car.customer_id=c.id
            WHERE a.id=?""", (appt_id,)).fetchone()
    if not data:
        flash("Données introuvables", "danger")
        return redirect("/protection_renewals")
    
    shop = get_all_settings()
    shop_name = shop.get('shop_name', 'AMILCAR')
    msg = f"Bonjour {data['name']}, votre traitement {data['service']} appliqué sur votre {data['brand']} {data['model']} le {data['date']} arrive à échéance. Prenez RDV chez {shop_name} pour renouveler votre protection ! 🛡️"
    wa_url = _build_wa_status_url(data['phone'], msg)
    return redirect(wa_url)





# ─── 18.7 Material Cost Calculator ──────────────────────────────────────────

@ops_bp.route("/cost_calculator")
@login_required 
def cost_calculator():
    with get_db() as conn:
        services = conn.execute("""SELECT s.id, s.name, s.price,
            GROUP_CONCAT(si.service_name || ':' || si.quantity_used || ':' || COALESCE(inv.unit_price,0), '|') as materials
            FROM services s
            LEFT JOIN service_inventory si ON s.name=si.service_name
            LEFT JOIN inventory inv ON si.inventory_id=inv.id
            WHERE s.active=1
            GROUP BY s.id ORDER BY s.name""").fetchall()
        
        results = []
        for svc in services:
            material_cost = 0
            materials = []
            if svc['materials']:
                for m in svc['materials'].split('|'):
                    parts = m.split(':')
                    if len(parts) >= 3:
                        qty = float(parts[1]) if parts[1] else 0
                        cost = float(parts[2]) if parts[2] else 0
                        material_cost += qty * cost
                        materials.append({'name': parts[0], 'qty': qty, 'unit_cost': cost, 'total': qty * cost})
            
            margin = svc['price'] - material_cost if svc['price'] else 0
            margin_pct = (margin / svc['price'] * 100) if svc['price'] and svc['price'] > 0 else 0
            
            results.append({
                'id': svc['id'],
                'name': svc['name'],
                'price': svc['price'],
                'material_cost': round(material_cost, 2),
                'margin': round(margin, 2),
                'margin_pct': round(margin_pct, 1),
                'materials': materials,
            })
    return render_template("cost_calculator.html", services=results)





# ─── 18.8 Visual Subscription Counter ───────────────────────────────────────

@ops_bp.route("/subscription_cards")
@login_required
def subscription_cards():
    with get_db() as conn:
        subs = conn.execute("""SELECT ws.*, c.name as customer_name, c.phone,
            car.brand, car.model, car.plate
            FROM wash_subscriptions ws
            JOIN customers c ON ws.customer_id=c.id
            LEFT JOIN cars car ON ws.car_id=car.id
            WHERE ws.status='active'
            ORDER BY ws.end_date ASC""").fetchall()
    return render_template("subscription_cards.html", subscriptions=subs)





# ─── 18.10 Waiting Room TV Display ──────────────────────────────────────────

@ops_bp.route("/tv_display")
def tv_display():
    """Public route - no login needed. Displays in-progress work on a TV screen."""
    with get_db() as conn:
        today = str(date.today())
        in_progress = conn.execute("""SELECT a.service, a.time, car.brand, car.model, 
            COALESCE(car.plate,'') as plate, c.name,
            CASE a.status 
                WHEN 'in_progress' THEN 'En cours'
                WHEN 'pending' THEN 'En attente'
                WHEN 'completed' THEN 'Terminé'
                ELSE a.status 
            END as status_label,
            a.status
            FROM appointments a
            LEFT JOIN cars car ON a.car_id=car.id
            LEFT JOIN customers c ON car.customer_id=c.id
            WHERE a.date=? AND a.status IN ('pending','in_progress','completed')
            ORDER BY 
                CASE a.status WHEN 'in_progress' THEN 1 WHEN 'pending' THEN 2 WHEN 'completed' THEN 3 END,
                a.time""", (today,)).fetchall()
        
        stats = {
            'in_progress': sum(1 for a in in_progress if a['status'] == 'in_progress'),
            'pending': sum(1 for a in in_progress if a['status'] == 'pending'),
            'completed': sum(1 for a in in_progress if a['status'] == 'completed'),
        }
        shop = get_all_settings()
    return render_template("tv_display.html", appointments=in_progress, stats=stats, 
                          shop=shop, today=today)



