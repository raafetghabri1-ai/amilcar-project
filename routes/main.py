"""
AMILCAR — Main Pages
Blueprint: main_bp
Routes: 51
"""
from flask import Blueprint, render_template, request, redirect, url_for, flash, make_response, jsonify, session, send_file, current_app
from helpers import login_required, admin_required, client_required, get_db, get_services, get_setting, get_all_settings
from helpers import allowed_file, safe_page, log_activity, build_wa_url, STATUS_MESSAGES, UPLOAD_FOLDER, MAX_FILE_SIZE, MAX_FILES, PER_PAGE, check_booking_rate_limit, cache
from models.report import total_customers, total_appointments, total_revenue
from database.db import get_db
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, date, timedelta
import os, re, uuid, io
import time as time_module
import sqlite3

main_bp = Blueprint("main_bp", __name__)

EXPENSE_CATEGORIES = [
    'Pièces & Matériaux',
    'Main-d\'œuvre',
    'Loyer',
    'Services publics',
    'Équipement',
    'Marketing',
    'Assurance',
    'Autre',
]

AVAILABLE_WIDGETS = [
    {'type': 'today_revenue', 'name': "CA Aujourd'hui", 'icon': '💰'},
    {'type': 'today_appointments', 'name': "RDV Aujourd'hui", 'icon': '📅'},
    {'type': 'pending_invoices', 'name': 'Factures impayées', 'icon': '📄'},
    {'type': 'low_stock', 'name': 'Stock bas', 'icon': '📦'},
    {'type': 'queue_count', 'name': "File d'attente", 'icon': '⏳'},
    {'type': 'monthly_chart', 'name': 'Graphique mensuel', 'icon': '📈'},
    {'type': 'top_services', 'name': 'Top services', 'icon': '🏆'},
    {'type': 'recent_customers', 'name': 'Derniers clients', 'icon': '👥'},
    {'type': 'alerts', 'name': 'Alertes', 'icon': '🔔'},
    {'type': 'crm_pending', 'name': 'Suivis CRM', 'icon': '🔄'},
]



@main_bp.route('/')
@login_required
def index():
    from datetime import date, timedelta
    with get_db() as conn:
        pending_quotes = conn.execute("SELECT COUNT(*) FROM quotes WHERE status = 'pending'").fetchone()[0]
        total_expenses = conn.execute("SELECT COALESCE(SUM(amount),0) FROM expenses").fetchone()[0]
        pending_appointments = conn.execute(
            "SELECT a.id, cu.name, ca.brand, ca.model, a.date, a.service "
            "FROM appointments a JOIN cars ca ON a.car_id = ca.id "
            "JOIN customers cu ON ca.customer_id = cu.id "
            "WHERE a.status = 'pending' ORDER BY a.date"
        ).fetchall()
        tomorrow = str(date.today() + timedelta(days=1))
        tomorrow_appointments = conn.execute(
            "SELECT a.id, cu.name, ca.brand, ca.model, a.service "
            "FROM appointments a JOIN cars ca ON a.car_id = ca.id "
            "JOIN customers cu ON ca.customer_id = cu.id "
            "WHERE a.date = ? AND a.status = 'pending'", (tomorrow,)
        ).fetchall()
        today_str = str(date.today())
        today_revenue = conn.execute(
            "SELECT COALESCE(SUM(i.amount),0) FROM invoices i JOIN appointments a ON i.appointment_id = a.id "
            "WHERE a.date = ? AND i.status = 'paid'", (today_str,)).fetchone()[0]
        today_appointments = conn.execute(
            "SELECT COUNT(*) FROM appointments WHERE date = ?", (today_str,)).fetchone()[0]
        unpaid_total = conn.execute(
            "SELECT COALESCE(SUM(amount),0) FROM invoices WHERE status = 'unpaid'").fetchone()[0]
        # Rich dashboard stats
        top_customer = conn.execute(
            "SELECT cu.name, COALESCE(SUM(i.amount),0) as total "
            "FROM customers cu JOIN cars ca ON ca.customer_id = cu.id "
            "JOIN appointments a ON a.car_id = ca.id "
            "JOIN invoices i ON i.appointment_id = a.id AND i.status = 'paid' "
            "GROUP BY cu.id ORDER BY total DESC LIMIT 1"
        ).fetchone()
        most_visited_car = conn.execute(
            "SELECT ca.brand || ' ' || ca.model, COUNT(*) as cnt "
            "FROM cars ca JOIN appointments a ON a.car_id = ca.id "
            "GROUP BY ca.id ORDER BY cnt DESC LIMIT 1"
        ).fetchone()
        paid_count = conn.execute("SELECT COUNT(*) FROM invoices WHERE status = 'paid'").fetchone()[0]
        total_inv_count = conn.execute("SELECT COUNT(*) FROM invoices").fetchone()[0]
        pay_rate = round((paid_count / total_inv_count * 100) if total_inv_count > 0 else 0)
        # Online bookings stats
        pending_bookings = conn.execute(
            "SELECT COUNT(*) FROM online_bookings WHERE status='pending'").fetchone()[0]
        today_bookings = conn.execute(
            "SELECT COUNT(*) FROM online_bookings WHERE date(created_at)=?", (today_str,)).fetchone()[0]
        # This week revenue
        week_start = str(date.today() - timedelta(days=date.today().weekday()))
        week_revenue = conn.execute(
            "SELECT COALESCE(SUM(i.amount),0) FROM invoices i JOIN appointments a ON i.appointment_id=a.id "
            "WHERE a.date >= ? AND i.status='paid'", (week_start,)).fetchone()[0]
    stats = {
        'customers': total_customers(),
        'appointments': total_appointments(),
        'revenue': total_revenue(),
        'quotes': pending_quotes,
        'expenses': total_expenses,
        'profit': total_revenue() - total_expenses,
        'today_revenue': today_revenue,
        'today_appointments': today_appointments,
        'unpaid_total': unpaid_total,
        'top_customer': top_customer[0] if top_customer else '—',
        'top_customer_amount': top_customer[1] if top_customer else 0,
        'most_visited_car': most_visited_car[0] if most_visited_car else '—',
        'most_visited_count': most_visited_car[1] if most_visited_car else 0,
        'pay_rate': pay_rate,
        'pending_bookings': pending_bookings,
        'today_bookings': today_bookings,
        'week_revenue': week_revenue,
    }
    return render_template('index.html', stats=stats, pending_appointments=pending_appointments,
                           tomorrow_appointments=tomorrow_appointments)



@main_bp.route("/add_invoice", methods=["GET", "POST"])
@login_required
def new_invoice():
    if request.method == "POST":
        appointment_id = request.form.get("appointment_id")
        amount = request.form.get("amount", '').strip()
        discount_type = request.form.get("discount_type", "").strip()
        discount_value = request.form.get("discount_value", "0").strip()
        if not appointment_id:
            flash('Sélectionnez un rendez-vous', 'error')
        elif not amount:
            flash('Entrez un montant', 'error')
        else:
            try:
                amount_val = float(amount)
                if amount_val <= 0:
                    raise ValueError
                disc_val = float(discount_value) if discount_value else 0
                if disc_val < 0:
                    disc_val = 0
                if discount_type == 'percent' and disc_val > 100:
                    disc_val = 100
                if discount_type == 'fixed' and disc_val >= amount_val:
                    disc_val = amount_val - 0.01
                with get_db() as conn_inv:
                    cursor_inv = conn_inv.execute(
                        "INSERT INTO invoices (appointment_id, amount, discount_type, discount_value) VALUES (?,?,?,?)",
                        (appointment_id, amount_val, discount_type if discount_type in ('percent','fixed') else '', disc_val))
                    conn_inv.commit()
                log_activity('Add Invoice', f'Amount: {amount_val} DT')
                cache.clear()
                flash('Facture ajoutée avec succès', 'success')
                return redirect("/invoices")
            except ValueError:
                flash('Entrez un montant positif valide', 'error')
    from models.appointment import get_appointments
    all_appointments = get_appointments()
    return render_template("add_invoice.html", appointments=all_appointments)



@main_bp.route("/request_quote", methods=["GET", "POST"])
def request_quote():
    if request.method == "POST":
        name = request.form.get("name", '').strip()
        phone = request.form.get("phone", '').strip()
        service = request.form.get("service", '')
        if not name or len(name) < 2:
            flash('Le nom doit contenir au moins 2 caractères', 'error')
            return render_template("request_quote.html", services=get_services())
        if not phone or len(phone) < 4:
            flash('Entrez un numéro de téléphone valide', 'error')
            return render_template("request_quote.html", services=get_services())
        files = request.files.getlist("photos")
        if len(files) > MAX_FILES:
            flash(f'Maximum {MAX_FILES} photos autorisées', 'error')
            return render_template("request_quote.html", services=get_services())
        saved = []
        for f in files:
            if f.filename and allowed_file(f.filename):
                f.seek(0, 2)
                size = f.tell()
                f.seek(0)
                if size > MAX_FILE_SIZE:
                    flash(f'Le fichier {f.filename} dépasse la limite de 5Mo', 'error')
                    return render_template("request_quote.html", services=get_services())
                fname = f'{uuid.uuid4().hex}_{secure_filename(f.filename)}'
                f.save(os.path.join(UPLOAD_FOLDER, fname))
                saved.append(fname)
        with get_db() as conn:
            conn.execute("INSERT INTO quotes (name, phone, service, photos) VALUES (?,?,?,?)",
                (name, phone, service, ",".join(saved)))
            conn.commit()
        return render_template("quote_success.html")
    return render_template("request_quote.html", services=get_services())



@main_bp.route("/add_car", methods=["GET", "POST"])
@login_required
def new_car():
    if request.method == "POST":
        customer_id = request.form["customer_id"]
        brand = request.form["brand"]
        model = request.form["model"]
        plate = request.form["plate"]
        year = request.form.get("year", "").strip()
        color = request.form.get("color", "").strip()
        from models.car import add_car
        add_car(customer_id, brand, model, plate)
        # Update year and color
        with get_db() as conn2:
            car_id = conn2.execute("SELECT id FROM cars WHERE customer_id = ? AND plate = ? ORDER BY id DESC LIMIT 1",
                (customer_id, plate)).fetchone()
            if car_id and (year or color):
                conn2.execute("UPDATE cars SET year = ?, color = ? WHERE id = ?", (year, color, car_id[0]))
                conn2.commit()
        log_activity('Add Car', f'{brand} {model} ({plate})')
        return redirect("/customers")
    from models.customer import get_all_customers
    all_customers = get_all_customers()
    return render_template("add_car.html", customers=all_customers)



@main_bp.route("/edit_customer/<int:customer_id>", methods=["GET", "POST"])
@login_required
def edit_customer(customer_id):
    with get_db() as conn:
        if request.method == "POST":
            name = request.form.get("name", '').strip()
            phone = request.form.get("phone", '').strip()
            notes = request.form.get("notes", '').strip()
            email = request.form.get("email", '').strip()
            if not name or len(name) < 2:
                flash('Le nom doit contenir au moins 2 caractères', 'error')
                customer = conn.execute("SELECT * FROM customers WHERE id = ?", (customer_id,)).fetchone()
                return render_template("edit_customer.html", customer=customer)
            if phone and not re.match(r'^[0-9+\s\-]{4,20}$', phone):
                flash('Entrez un numéro de téléphone valide', 'error')
                customer = conn.execute("SELECT * FROM customers WHERE id = ?", (customer_id,)).fetchone()
                return render_template("edit_customer.html", customer=customer)
            if email and not re.match(r'^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$', email):
                flash('Adresse email invalide', 'error')
                customer = conn.execute("SELECT * FROM customers WHERE id = ?", (customer_id,)).fetchone()
                return render_template("edit_customer.html", customer=customer)
            dup = conn.execute("SELECT id, name FROM customers WHERE phone = ? AND id != ?", (phone, customer_id)).fetchone()
            if dup:
                flash(f'Ce numéro est déjà utilisé par le client : {dup[1]}', 'error')
                customer = conn.execute("SELECT * FROM customers WHERE id = ?", (customer_id,)).fetchone()
                return render_template("edit_customer.html", customer=customer)
            conn.execute("UPDATE customers SET name = ?, phone = ?, notes = ?, email = ? WHERE id = ?", (name, phone, notes, email, customer_id))
            conn.commit()
            flash('Client mis à jour avec succès', 'success')
            return redirect(f"/customer/{customer_id}")
        customer = conn.execute("SELECT * FROM customers WHERE id = ?", (customer_id,)).fetchone()
        if not customer:
            flash('Client introuvable', 'error')
            return redirect("/customers")
    return render_template("edit_customer.html", customer=customer)



@main_bp.route("/edit_car/<int:car_id>", methods=["GET", "POST"])
@login_required
def edit_car(car_id):
    with get_db() as conn:
        if request.method == "POST":
            brand = request.form["brand"]
            model = request.form["model"]
            plate = request.form["plate"]
            year = request.form.get("year", "").strip()
            color = request.form.get("color", "").strip()
            conn.execute("UPDATE cars SET brand = ?, model = ?, plate = ?, year = ?, color = ? WHERE id = ?",
                (brand, model, plate, year, color, car_id))
            conn.commit()
            log_activity('Edit Car', f'{brand} {model} ({plate})')
            customer_id = conn.execute("SELECT customer_id FROM cars WHERE id = ?", (car_id,)).fetchone()[0]
            return redirect(f"/customer/{customer_id}")
        car = conn.execute("SELECT * FROM cars WHERE id = ?", (car_id,)).fetchone()
    return render_template("edit_car.html", car=car)



@main_bp.route("/delete_car/<int:car_id>", methods=["POST"])
@login_required
def delete_car(car_id):
    with get_db() as conn:
        car = conn.execute("SELECT customer_id FROM cars WHERE id = ?", (car_id,)).fetchone()
        if not car:
            return redirect("/customers")
        customer_id = car[0]
        # Cascade: delete invoices → appointments → car
        conn.execute("DELETE FROM invoices WHERE appointment_id IN (SELECT id FROM appointments WHERE car_id = ?)", (car_id,))
        conn.execute("DELETE FROM appointments WHERE car_id = ?", (car_id,))
        conn.execute("DELETE FROM cars WHERE id = ?", (car_id,))
        conn.commit()
    log_activity('Delete Car', f'Car #{car_id}')
    return redirect(f"/customer/{customer_id}")



@main_bp.route("/delete_customer/<int:customer_id>", methods=["POST"])
@login_required
def delete_customer(customer_id):
    with get_db() as conn:
        car_ids = [r[0] for r in conn.execute("SELECT id FROM cars WHERE customer_id = ?", (customer_id,)).fetchall()]
        if car_ids:
            placeholders = ','.join('?' * len(car_ids))
            conn.execute(f"DELETE FROM invoices WHERE appointment_id IN (SELECT id FROM appointments WHERE car_id IN ({placeholders}))", car_ids)
            conn.execute(f"DELETE FROM appointments WHERE car_id IN ({placeholders})", car_ids)
            conn.execute("DELETE FROM cars WHERE customer_id = ?", (customer_id,))
        conn.execute("DELETE FROM customers WHERE id = ?", (customer_id,))
        conn.commit()
    log_activity('Delete Customer', f'Customer #{customer_id}')
    return redirect("/customers")



@main_bp.route("/delete_invoice/<int:invoice_id>", methods=["POST"])
@login_required
def delete_invoice(invoice_id):
    with get_db() as conn:
        conn.execute("DELETE FROM invoices WHERE id = ?", (invoice_id,))
        conn.commit()
    log_activity('Delete Invoice', f'Invoice #{invoice_id}')
    return redirect("/invoices")



@main_bp.route("/edit_invoice/<int:invoice_id>", methods=["GET", "POST"])
@login_required
def edit_invoice(invoice_id):
    with get_db() as conn:
        if request.method == "POST":
            amount = request.form.get("amount", "").strip()
            status = request.form.get("status", "unpaid")
            payment_method = request.form.get("payment_method", "")
            discount_type = request.form.get("discount_type", "").strip()
            discount_value = request.form.get("discount_value", "0").strip()
            if not amount:
                flash("Le montant est requis", "error")
                inv = conn.execute(
                    "SELECT i.*, a.date, a.service, cu.name "
                    "FROM invoices i JOIN appointments a ON i.appointment_id = a.id "
                    "JOIN cars ca ON a.car_id = ca.id JOIN customers cu ON ca.customer_id = cu.id "
                    "WHERE i.id = ?", (invoice_id,)).fetchone()
                return render_template("edit_invoice.html", inv=inv)
            try:
                amount_val = float(amount)
                if amount_val <= 0:
                    raise ValueError
            except ValueError:
                flash("Entrez un montant positif valide", "error")
                inv = conn.execute(
                    "SELECT i.*, a.date, a.service, cu.name "
                    "FROM invoices i JOIN appointments a ON i.appointment_id = a.id "
                    "JOIN cars ca ON a.car_id = ca.id JOIN customers cu ON ca.customer_id = cu.id "
                    "WHERE i.id = ?", (invoice_id,)).fetchone()
                return render_template("edit_invoice.html", inv=inv)
            disc_val = 0
            try:
                disc_val = float(discount_value) if discount_value else 0
                if disc_val < 0: disc_val = 0
                if discount_type == 'percent' and disc_val > 100: disc_val = 100
                if discount_type == 'fixed' and disc_val >= amount_val: disc_val = amount_val - 0.01
            except ValueError:
                disc_val = 0
            conn.execute("UPDATE invoices SET amount = ?, status = ?, payment_method = ?, discount_type = ?, discount_value = ? WHERE id = ?",
                (amount_val, status, payment_method, discount_type if discount_type in ('percent','fixed') else '', disc_val, invoice_id))
            conn.commit()
            flash("Facture mise à jour avec succès", "success")
            return redirect("/invoices")
        inv = conn.execute(
            "SELECT i.*, a.date, a.service, cu.name "
            "FROM invoices i JOIN appointments a ON i.appointment_id = a.id "
            "JOIN cars ca ON a.car_id = ca.id JOIN customers cu ON ca.customer_id = cu.id "
            "WHERE i.id = ?", (invoice_id,)).fetchone()
    if not inv:
        return redirect("/invoices")
    return render_template("edit_invoice.html", inv=inv)



@main_bp.route("/edit_expense/<int:expense_id>", methods=["GET", "POST"])
@login_required
def edit_expense(expense_id):
    with get_db() as conn:
        if request.method == "POST":
            date_val = request.form.get("date", "").strip()
            category = request.form.get("category", "").strip()
            description = request.form.get("description", "").strip()
            amount = request.form.get("amount", "").strip()
            if not date_val or not category or not amount:
                flash("La date, la catégorie et le montant sont requis", "error")
                expense = conn.execute("SELECT * FROM expenses WHERE id = ?", (expense_id,)).fetchone()
                return render_template("edit_expense.html", expense=expense, categories=EXPENSE_CATEGORIES)
            try:
                amount_val = float(amount)
                if amount_val <= 0:
                    raise ValueError
            except ValueError:
                flash("Entrez un montant positif valide", "error")
                expense = conn.execute("SELECT * FROM expenses WHERE id = ?", (expense_id,)).fetchone()
                return render_template("edit_expense.html", expense=expense, categories=EXPENSE_CATEGORIES)
            conn.execute("UPDATE expenses SET date = ?, category = ?, description = ?, amount = ? WHERE id = ?",
                (date_val, category, description, amount_val, expense_id))
            conn.commit()
            flash("Dépense mise à jour avec succès", "success")
            return redirect("/expenses")
        expense = conn.execute("SELECT * FROM expenses WHERE id = ?", (expense_id,)).fetchone()
    if not expense:
        return redirect("/expenses")
    return render_template("edit_expense.html", expense=expense, categories=EXPENSE_CATEGORIES)



@main_bp.route("/delete_quote/<int:quote_id>", methods=["POST"])
@login_required
def delete_quote(quote_id):
    with get_db() as conn:
        conn.execute("DELETE FROM quotes WHERE id = ?", (quote_id,))
        conn.commit()
    log_activity('Delete Quote', f'Quote #{quote_id}')
    return redirect("/quotes")



@main_bp.route("/add_expense", methods=["GET", "POST"])
@login_required
def add_expense():
    if request.method == "POST":
        date_val = request.form.get("date", "").strip()
        category = request.form.get("category", "").strip()
        description = request.form.get("description", "").strip()
        amount = request.form.get("amount", "").strip()
        if not date_val or not category or not amount:
            flash("La date, la catégorie et le montant sont requis", "error")
            return render_template("add_expense.html", categories=EXPENSE_CATEGORIES)
        try:
            amount_val = float(amount)
            if amount_val <= 0:
                raise ValueError
        except ValueError:
            flash("Entrez un montant positif valide", "error")
            return render_template("add_expense.html", categories=EXPENSE_CATEGORIES)
        with get_db() as conn:
            conn.execute("INSERT INTO expenses (date, category, description, amount) VALUES (?,?,?,?)",
                (date_val, category, description, amount_val))
            conn.commit()
        flash("Dépense ajoutée avec succès", "success")
        return redirect("/expenses")
    return render_template("add_expense.html", categories=EXPENSE_CATEGORIES)



@main_bp.route("/delete_expense/<int:expense_id>", methods=["POST"])
@login_required
def delete_expense(expense_id):
    with get_db() as conn:
        conn.execute("DELETE FROM expenses WHERE id = ?", (expense_id,))
        conn.commit()
    return redirect("/expenses")



@main_bp.route("/edit_service/<int:service_id>", methods=["POST"])
@admin_required
def edit_service(service_id):
    name = request.form.get("name", "").strip()
    price = request.form.get("price", "0").strip()
    active = 1 if request.form.get("active") else 0
    if not name:
        flash("Le nom du service est requis", "error")
        return redirect("/services")
    try:
        price_val = float(price)
    except ValueError:
        price_val = 0
    with get_db() as conn:
        conn.execute("UPDATE services SET name = ?, price = ?, active = ? WHERE id = ?",
            (name, price_val, active, service_id))
        conn.commit()
    log_activity('Edit Service', f'{name} — {price_val} DT')
    flash("Service mis à jour", "success")
    return redirect("/services")



# ─── Customer Ratings ───
@main_bp.route("/rate_appointment/<int:appointment_id>", methods=["POST"])
@login_required
def rate_appointment(appointment_id):
    rating = request.form.get("rating", "0")
    comment = request.form.get("comment", "").strip()[:500]
    try:
        rating_val = int(rating)
        if rating_val < 1 or rating_val > 5:
            raise ValueError
    except ValueError:
        flash("Note invalide (1-5)", "error")
        return redirect("/appointments")
    with get_db() as conn:
        appt = conn.execute(
            "SELECT a.id, ca.customer_id FROM appointments a JOIN cars ca ON a.car_id = ca.id WHERE a.id = ?",
            (appointment_id,)).fetchone()
        if not appt:
            flash("Rendez-vous introuvable", "error")
            return redirect("/appointments")
        existing = conn.execute("SELECT id FROM ratings WHERE appointment_id = ?", (appointment_id,)).fetchone()
        if existing:
            conn.execute("UPDATE ratings SET rating = ?, comment = ? WHERE appointment_id = ?",
                (rating_val, comment, appointment_id))
        else:
            conn.execute("INSERT INTO ratings (appointment_id, customer_id, rating, comment) VALUES (?,?,?,?)",
                (appointment_id, appt[1], rating_val, comment))
        conn.commit()
    flash("Évaluation enregistrée", "success")
    log_activity('Rate', f'Appointment #{appointment_id} → {rating_val}★')
    return redirect("/appointments")



@main_bp.route("/add_package", methods=["POST"])
@admin_required
def add_package():
    name = request.form.get("name", "").strip()
    description = request.form.get("description", "").strip()
    selected_services = request.form.getlist("services")
    package_price = request.form.get("package_price", "0").strip()
    if not name or not selected_services:
        flash("Le nom et au moins un service sont requis", "error")
        return redirect("/packages")
    try:
        price = float(package_price)
    except ValueError:
        price = 0
    services_list = get_services()
    services_dict = {s[0]: s[1] for s in services_list}
    original = sum(services_dict.get(s, 0) for s in selected_services)
    with get_db() as conn:
        conn.execute("INSERT INTO service_packages (name, description, services, original_price, package_price) VALUES (?,?,?,?,?)",
            (name, description, ','.join(selected_services), original, price))
        conn.commit()
    log_activity('Add Package', f'{name} — {price} DT')
    flash(f"Pack '{name}' créé", "success")
    return redirect("/packages")



@main_bp.route("/delete_package/<int:pkg_id>", methods=["POST"])
@admin_required
def delete_package(pkg_id):
    with get_db() as conn:
        conn.execute("DELETE FROM service_packages WHERE id = ?", (pkg_id,))
        conn.commit()
    flash("Pack supprimé", "success")
    return redirect("/packages")



@main_bp.route("/add_communication/<int:customer_id>", methods=["POST"])
@login_required
def add_communication(customer_id):
    comm_type = request.form.get("type", "appel").strip()
    subject = request.form.get("subject", "").strip()
    message = request.form.get("message", "").strip()
    if comm_type not in ('appel', 'sms', 'email', 'whatsapp', 'visite', 'autre'):
        comm_type = 'autre'
    with get_db() as conn:
        conn.execute("INSERT INTO communication_log (customer_id, type, subject, message, sent_by) VALUES (?,?,?,?,?)",
            (customer_id, comm_type, subject, message, session.get('username', '')))
        conn.commit()
    flash("Communication enregistrée", "success")
    return redirect(f"/communication_log/{customer_id}")



# ─── Customer Portal ───
@main_bp.route("/portal/<token>")
def customer_portal(token):
    if not token or len(token) < 10:
        return render_template('404.html'), 404
    with get_db() as conn:
        customer = conn.execute("SELECT * FROM customers WHERE portal_token = ?", (token,)).fetchone()
        if not customer:
            return render_template('404.html'), 404
        customer_id = customer[0]
        cars = conn.execute("SELECT * FROM cars WHERE customer_id = ?", (customer_id,)).fetchall()
        appointments = conn.execute(
            "SELECT a.id, a.date, a.service, a.status, ca.brand, ca.model, COALESCE(a.time, '') "
            "FROM appointments a JOIN cars ca ON a.car_id = ca.id "
            "WHERE ca.customer_id = ? ORDER BY a.date DESC LIMIT 20", (customer_id,)).fetchall()
        invoices = conn.execute(
            "SELECT i.id, a.date, a.service, i.amount, i.status, COALESCE(i.payment_method, ''), ca.brand, ca.model "
            "FROM invoices i JOIN appointments a ON i.appointment_id = a.id "
            "JOIN cars ca ON a.car_id = ca.id WHERE ca.customer_id = ? ORDER BY a.date DESC LIMIT 20", (customer_id,)).fetchall()
        total_spent = conn.execute(
            "SELECT COALESCE(SUM(i.amount),0) FROM invoices i JOIN appointments a ON i.appointment_id = a.id "
            "JOIN cars ca ON a.car_id = ca.id WHERE ca.customer_id = ? AND i.status = 'paid'", (customer_id,)).fetchone()[0]
        total_unpaid = conn.execute(
            "SELECT COALESCE(SUM(i.amount),0) FROM invoices i JOIN appointments a ON i.appointment_id = a.id "
            "JOIN cars ca ON a.car_id = ca.id WHERE ca.customer_id = ? AND i.status IN ('unpaid','partial')", (customer_id,)).fetchone()[0]
    return render_template("customer_portal.html", customer=customer, cars=cars,
                           appointments=appointments, invoices=invoices,
                           total_spent=total_spent, total_unpaid=total_unpaid)



@main_bp.route("/generate_portal_link/<int:customer_id>", methods=["POST"])
@login_required
def generate_portal_link(customer_id):
    token = uuid.uuid4().hex + uuid.uuid4().hex[:8]
    with get_db() as conn:
        conn.execute("UPDATE customers SET portal_token = ? WHERE id = ?", (token, customer_id))
        conn.commit()
    portal_url = f"{request.host_url}portal/{token}"
    log_activity('Portal Link', f'Generated for customer #{customer_id}')
    flash(f"Lien portail généré : {portal_url}", "success")
    return redirect(f"/customer/{customer_id}")



@main_bp.route("/list_backups")
@admin_required
def list_backups():
    backup_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'backups')
    if not os.path.exists(backup_dir):
        return jsonify([])
    files = sorted(os.listdir(backup_dir), reverse=True)
    backups = []
    for f in files:
        if f.endswith('.db'):
            path = os.path.join(backup_dir, f)
            size = os.path.getsize(path)
            backups.append({'name': f, 'size': f"{size/1024:.0f} KB", 'path': f'/download_backup/{f}'})
    return jsonify(backups)



@main_bp.route("/generate_invoice_qr/<int:invoice_id>", methods=["POST"])
@login_required
def generate_invoice_qr(invoice_id):
    token = uuid.uuid4().hex
    with get_db() as conn:
        conn.execute("UPDATE invoices SET qr_token = ? WHERE id = ?", (token, invoice_id))
        conn.commit()
    url = f"{request.host_url}invoice_view/{token}"
    flash(f"QR Code généré. Lien : {url}", "success")
    return redirect("/invoices")



# ─── Feature 4: Auto-rating after Service ───
@main_bp.route("/rate/<token>")
def public_rating(token):
    if not token or len(token) < 10:
        return render_template('404.html'), 404
    with get_db() as conn:
        # Find appointment by a hash of id
        appts = conn.execute(
            "SELECT a.id, cu.name, a.service, a.date, ca.brand, ca.model "
            "FROM appointments a JOIN cars ca ON a.car_id = ca.id "
            "JOIN customers cu ON ca.customer_id = cu.id "
            "WHERE a.status = 'completed'").fetchall()
    target = None
    for a in appts:
        import hashlib
        h = hashlib.sha256(f"rate_{a[0]}_{a[3]}".encode()).hexdigest()[:24]
        if h == token:
            target = a
            break
    if not target:
        return render_template('404.html'), 404
    return render_template("public_rating.html", appt=target, token=token)



@main_bp.route("/rate/<token>", methods=["POST"])
def submit_public_rating(token):
    rating = request.form.get("rating", "0")
    comment = request.form.get("comment", "").strip()[:500]
    try:
        rating_val = int(rating)
        if rating_val < 1 or rating_val > 5:
            raise ValueError
    except ValueError:
        return "Évaluation invalide", 400
    with get_db() as conn:
        appts = conn.execute(
            "SELECT a.id, ca.customer_id FROM appointments a JOIN cars ca ON a.car_id = ca.id "
            "WHERE a.status = 'completed'").fetchall()
    target = None
    import hashlib
    for a in appts:
        h = hashlib.sha256(f"rate_{a[0]}_{a[1]}".encode()).hexdigest()[:24]
        if h == token:
            target = a
            break
    # fallback: try date-based hash
    if not target:
        with get_db() as conn:
            appts2 = conn.execute(
                "SELECT a.id, ca.customer_id, a.date FROM appointments a JOIN cars ca ON a.car_id = ca.id "
                "WHERE a.status = 'completed'").fetchall()
        for a in appts2:
            h = hashlib.sha256(f"rate_{a[0]}_{a[2]}".encode()).hexdigest()[:24]
            if h == token:
                target = (a[0], a[1])
                break
    if not target:
        return "Lien invalide", 404
    with get_db() as conn:
        existing = conn.execute("SELECT id FROM ratings WHERE appointment_id = ?", (target[0],)).fetchone()
        if existing:
            conn.execute("UPDATE ratings SET rating = ?, comment = ? WHERE appointment_id = ?",
                (rating_val, comment, target[0]))
        else:
            conn.execute("INSERT INTO ratings (appointment_id, customer_id, rating, comment) VALUES (?,?,?,?)",
                (target[0], target[1], rating_val, comment))
        conn.commit()
    return render_template("rating_thanks.html")



# ─── Feature 9: PWA Support ───
@main_bp.route("/offline")
def offline_page():
    return render_template("offline.html")

@main_bp.route("/manifest.json")
def pwa_manifest():
    settings = get_all_settings()
    shop_name = settings.get('shop_name', 'AMILCAR')
    manifest = {
        "name": f"{shop_name} Auto Care",
        "short_name": shop_name,
        "description": "Gestion complète de garage automobile — rendez-vous, factures, clients, stock",
        "start_url": "/",
        "scope": "/",
        "display": "standalone",
        "background_color": "#09090b",
        "theme_color": "#D4AF37",
        "orientation": "portrait-primary",
        "categories": ["business", "productivity"],
        "lang": "fr",
        "dir": "ltr",
        "icons": [
            {"src": "/static/icons/icon-72x72.png", "sizes": "72x72", "type": "image/png"},
            {"src": "/static/icons/icon-96x96.png", "sizes": "96x96", "type": "image/png"},
            {"src": "/static/icons/icon-128x128.png", "sizes": "128x128", "type": "image/png"},
            {"src": "/static/icons/icon-144x144.png", "sizes": "144x144", "type": "image/png", "purpose": "any"},
            {"src": "/static/icons/icon-152x152.png", "sizes": "152x152", "type": "image/png"},
            {"src": "/static/icons/icon-192x192.png", "sizes": "192x192", "type": "image/png", "purpose": "any maskable"},
            {"src": "/static/icons/icon-384x384.png", "sizes": "384x384", "type": "image/png"},
            {"src": "/static/icons/icon-512x512.png", "sizes": "512x512", "type": "image/png", "purpose": "any maskable"}
        ],
        "shortcuts": [
            {"name": "Tableau de bord", "short_name": "Dashboard", "url": "/", "icons": [{"src": "/static/icons/icon-96x96.png", "sizes": "96x96"}]},
            {"name": "Nouveau RDV", "short_name": "RDV", "url": "/appointments/add", "icons": [{"src": "/static/icons/icon-96x96.png", "sizes": "96x96"}]},
            {"name": "Nouvelle facture", "short_name": "Facture", "url": "/invoices/add", "icons": [{"src": "/static/icons/icon-96x96.png", "sizes": "96x96"}]},
            {"name": "Clients", "short_name": "Clients", "url": "/customers", "icons": [{"src": "/static/icons/icon-96x96.png", "sizes": "96x96"}]}
        ]
    }
    response = make_response(jsonify(manifest))
    response.headers['Content-Type'] = 'application/manifest+json'
    return response



# Auto-add points when invoice is paid
@main_bp.after_app_request
def auto_reward_points(response):
    return response



# ─── Phase 6 Feature 3: Mobile Dashboard ───
@main_bp.route("/mobile")
@login_required
def mobile_dashboard():
    from datetime import date
    today = str(date.today())
    with get_db() as conn:
        today_appts = conn.execute(
            "SELECT COUNT(*) FROM appointments WHERE date = ?", (today,)).fetchone()[0]
        pending = conn.execute(
            "SELECT COUNT(*) FROM appointments WHERE date = ? AND status='pending'", (today,)).fetchone()[0]
        in_progress = conn.execute(
            "SELECT COUNT(*) FROM appointments WHERE date = ? AND status='in_progress'", (today,)).fetchone()[0]
        completed_today = conn.execute(
            "SELECT COUNT(*) FROM appointments WHERE date = ? AND status='completed'", (today,)).fetchone()[0]
        today_revenue = conn.execute(
            "SELECT COALESCE(SUM(amount),0) FROM invoices WHERE status='paid' AND DATE(created_at) = ?", (today,)).fetchone()[0]
        unpaid = conn.execute(
            "SELECT COUNT(*) FROM invoices WHERE status IN ('unpaid','partial')").fetchone()[0]
    return render_template("mobile_dashboard.html",
        today_appts=today_appts, pending=pending, in_progress=in_progress,
        completed_today=completed_today, today_revenue=today_revenue, unpaid=unpaid)



# ─── Phase 6 Feature 10: Customer Portal App ───
@main_bp.route("/client")
def customer_login_page():
    return render_template("customer_login.html")



# ─── Phase 7 Feature 6: Payment Tracking ───
@main_bp.route("/payments/<int:invoice_id>")
@login_required
def invoice_payments(invoice_id):
    with get_db() as conn:
        invoice = conn.execute("SELECT * FROM invoices WHERE id=?", (invoice_id,)).fetchone()
        if not invoice:
            flash("Facture introuvable", "danger")
            return redirect("/invoices")
        payments = conn.execute("SELECT * FROM payments WHERE invoice_id=? ORDER BY paid_at DESC", (invoice_id,)).fetchall()
        total_paid = sum(p[2] for p in payments)
        remaining = invoice[2] - total_paid
    return render_template("payments.html", invoice=invoice, payments=payments,
                          total_paid=total_paid, remaining=remaining)



@main_bp.route("/payments/<int:invoice_id>/add", methods=["POST"])
@login_required
def add_payment(invoice_id):
    amount = float(request.form.get('amount', 0))
    method = request.form.get('method', 'cash')
    reference = request.form.get('reference', '')
    notes = request.form.get('notes', '')
    with get_db() as conn:
        invoice = conn.execute("SELECT * FROM invoices WHERE id=?", (invoice_id,)).fetchone()
        if not invoice:
            flash("Facture introuvable", "danger")
            return redirect("/invoices")
        conn.execute("INSERT INTO payments (invoice_id, amount, method, reference, notes) VALUES (?,?,?,?,?)",
                    (invoice_id, amount, method, reference, notes))
        total_paid = conn.execute("SELECT COALESCE(SUM(amount),0) FROM payments WHERE invoice_id=?", (invoice_id,)).fetchone()[0]
        remaining = invoice[2] - total_paid
        if remaining <= 0:
            conn.execute("UPDATE invoices SET status='Payée', paid_amount=? WHERE id=?", (total_paid, invoice_id))
        else:
            conn.execute("UPDATE invoices SET status='partial', paid_amount=? WHERE id=?", (total_paid, invoice_id))
        try:
            conn.execute("UPDATE invoices SET total_paid=?, remaining=? WHERE id=?", (total_paid, max(0, remaining), invoice_id))
        except (ValueError, TypeError, AttributeError):
            pass
        conn.commit()
    flash(f"Paiement de {amount:.2f} DH enregistré", "success")
    return redirect(f"/payments/{invoice_id}")



# ─── Phase 25: Public Service Catalog ───
@main_bp.route("/nos-services")
def services_catalog():
    """Page publique du catalogue de services — visible sans login"""
    settings = get_all_settings()
    shop_name = settings.get('shop_name', 'AMILCAR Auto Care')
    with get_db() as conn:
        services = conn.execute(
            "SELECT * FROM services WHERE active=1 ORDER BY sort_order, name"
        ).fetchall()
    # Group by category
    categories = {}
    cat_labels = {
        'lavage': {'label': 'Lavage & Nettoyage', 'icon': '💧', 'desc': 'Du lavage express au lavage premium'},
        'interieur': {'label': 'Detailing Intérieur', 'icon': '🪑', 'desc': 'Nettoyage profond de l\'habitacle'},
        'exterieur': {'label': 'Detailing Extérieur', 'icon': '🔆', 'desc': 'Carrosserie parfaite et brillante'},
        'polissage': {'label': 'Polissage & Correction', 'icon': '💎', 'desc': 'Suppression rayures et swirl marks'},
        'protection': {'label': 'Protection & Céramique', 'icon': '🛡️', 'desc': 'Protection longue durée pour votre peinture'},
        'packs': {'label': 'Packs Complets', 'icon': '📦', 'desc': 'Nos formules tout-en-un les plus populaires'},
        'special': {'label': 'Services Spéciaux', 'icon': '⚙️', 'desc': 'Traitements ciblés et complémentaires'},
    }
    for svc in services:
        cat = svc['category'] or 'autre'
        if cat not in categories:
            info = cat_labels.get(cat, {'label': cat.title(), 'icon': '🔧', 'desc': ''})
            categories[cat] = {'info': info, 'services': []}
        categories[cat]['services'].append(svc)
    return render_template("services_catalog.html",
                           categories=categories, shop_name=shop_name,
                           settings=settings)


# ─── Phase 7 Feature 9: Online Booking ───
@main_bp.route("/book", methods=["GET", "POST"])
def online_booking():
    from datetime import date as dt_date, timedelta
    settings = get_all_settings()
    shop_name = settings.get('shop_name', 'AMILCAR Auto Care')

    if request.method == "POST":
        if check_booking_rate_limit():
            flash("Trop de réservations. Veuillez réessayer dans quelques minutes.", "danger")
            return redirect("/book")
        name     = request.form.get('name', '').strip()
        phone    = request.form.get('phone', '').strip()
        email    = request.form.get('email', '').strip()
        car_brand  = request.form.get('car_brand', '').strip()
        car_model  = request.form.get('car_model', '').strip()
        car_plate  = request.form.get('car_plate', '').strip()
        service    = request.form.get('service', '')
        pref_date  = request.form.get('preferred_date', '')
        pref_time  = request.form.get('preferred_time', '')
        notes      = request.form.get('notes', '')

        if not name or not phone or not service or not pref_date:
            flash("Veuillez remplir tous les champs obligatoires (*)", "danger")
            with get_db() as conn:
                services = conn.execute("SELECT name, price, estimated_minutes FROM services ORDER BY name").fetchall()
            return render_template("online_booking.html", services=services,
                                   settings=settings, shop_name=shop_name)

        with get_db() as conn:
            row = conn.execute("""INSERT INTO online_bookings
                (name, phone, email, car_brand, car_model, car_plate,
                 service, preferred_date, preferred_time, notes)
                VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (name, phone, email, car_brand, car_model, car_plate,
                 service, pref_date, pref_time, notes))
            booking_id = row.lastrowid
            # Auto-notify: add to notifications_center for admin (user_id=1)
            conn.execute("""INSERT INTO notifications_center
                (user_id, title, message, notif_type, link, is_read)
                VALUES (1, ?, ?, 'booking', '/bookings', 0)""",
                (f"📅 Nouvelle réservation — {name}",
                 f"{name} ({phone}) souhaite {service} le {pref_date} à {pref_time or 'heure flexible'}"))
            conn.commit()

        # Real-time dashboard notification
        try:
            notify = current_app.config.get('notify_update')
            if notify:
                notify('new_booking', {'name': name, 'service': service, 'date': pref_date})
        except Exception:
            pass

        # ── CallMeBot WhatsApp notification to admin ──
        wa_admin_phone = settings.get('wa_callmebot_phone', '')
        wa_admin_key = settings.get('wa_callmebot_apikey', '')
        wa_notify = settings.get('wa_notify_booking', '1')
        if wa_admin_phone and wa_admin_key and wa_notify == '1':
            try:
                import requests as _req
                import urllib.parse as _up
                notif_msg = (f"📅 *Nouvelle réservation*\n"
                             f"👤 {name} ({phone})\n"
                             f"🔧 {service}\n"
                             f"📅 {pref_date} {pref_time or 'flexible'}\n"
                             f"🚗 {car_brand} {car_model} {car_plate}".strip())
                _req.get(
                    f"https://api.callmebot.com/whatsapp.php"
                    f"?phone={_up.quote(wa_admin_phone)}"
                    f"&text={_up.quote(notif_msg)}"
                    f"&apikey={_up.quote(wa_admin_key)}",
                    timeout=10
                )
            except Exception:
                pass  # Don't block booking on notification failure

        # WhatsApp confirmation link for customer
        import urllib.parse
        wa_msg = (f"✅ Bonjour {name},\n\n"
                  f"Votre demande de RDV chez {shop_name} a bien été reçue !\n"
                  f"📅 Date souhaitée : {pref_date}\n"
                  f"🔧 Service : {service}\n\n"
                  f"Nous vous confirmerons très bientôt. Merci ! 🚗✨")
        wa_phone = phone.strip().replace(' ', '').replace('-', '')
        if wa_phone.startswith('0'):
            wa_phone = '216' + wa_phone[1:]
        elif not wa_phone.startswith('216') and not wa_phone.startswith('+'):
            wa_phone = '216' + wa_phone
        wa_phone = wa_phone.replace('+', '')
        wa_url = f"https://wa.me/{wa_phone}?text={urllib.parse.quote(wa_msg)}"

        return render_template("booking_success.html",
                               name=name, service=service,
                               date=pref_date, time=pref_time,
                               booking_id=booking_id, wa_url=wa_url,
                               shop_name=shop_name)

    # GET — build available slots (exclude already-booked)
    today = dt_date.today()
    # Next 30 days available dates (exclude past)
    min_date = (today + timedelta(days=1)).isoformat()
    max_date = (today + timedelta(days=30)).isoformat()

    with get_db() as conn:
        services = conn.execute(
            "SELECT name, price, estimated_minutes, category, icon, description, duration_label "
            "FROM services WHERE active=1 ORDER BY sort_order, name").fetchall()
        # Booked slots per date
        booked = conn.execute(
            "SELECT preferred_date, preferred_time, COUNT(*) as cnt "
            "FROM online_bookings WHERE status != 'rejected' "
            "GROUP BY preferred_date, preferred_time").fetchall()

    booked_slots = {}
    for b in booked:
        key = b['preferred_date']
        if key not in booked_slots:
            booked_slots[key] = []
        booked_slots[key].append(b['preferred_time'])

    time_slots = ["08:00","08:30","09:00","09:30","10:00","10:30","11:00","11:30",
                  "14:00","14:30","15:00","15:30","16:00","16:30","17:00","17:30"]

    preselect = request.args.get('service', '')

    return render_template("online_booking.html",
                           services=services, settings=settings,
                           shop_name=shop_name, min_date=min_date,
                           max_date=max_date, time_slots=time_slots,
                           booked_slots=booked_slots,
                           preselect=preselect)


@main_bp.route("/book/qr")
def booking_qr():
    """Génère un QR code PNG pointant vers la page de réservation"""
    import qrcode, io
    settings = get_all_settings()
    # Use request.host_url so it works on any network
    book_url = request.host_url.rstrip('/') + '/book'
    qr = qrcode.QRCode(version=2, box_size=10, border=3,
                       error_correction=qrcode.constants.ERROR_CORRECT_M)
    qr.add_data(book_url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="#1A1A2E", back_color="white")
    buf = io.BytesIO()
    img.save(buf, format='PNG')
    buf.seek(0)
    from flask import send_file as sf
    return sf(buf, mimetype='image/png',
               as_attachment=False,
               download_name='amilcar_booking_qr.png')


@main_bp.route("/book/qr_page")
def booking_qr_page():
    """Page avec QR code + lien de partage (pour affichage en boutique)"""
    settings = get_all_settings()
    shop_name = settings.get('shop_name', 'AMILCAR Auto Care')
    book_url = request.host_url.rstrip('/') + '/book'
    return render_template("booking_qr_page.html",
                           book_url=book_url, shop_name=shop_name,
                           qr_url='/book/qr')







# ─── Phase 8 Feature 4: Warranty Tracking ───
@main_bp.route("/warranties")
@login_required
def warranties_list():
    with get_db() as conn:
        from datetime import date
        today_str = date.today().isoformat()
        warranties = conn.execute("""
            SELECT w.*, c.name, cr.brand, cr.model, cr.plate
            FROM warranties w
            JOIN customers c ON w.customer_id=c.id
            JOIN cars cr ON w.car_id=cr.id
            ORDER BY w.end_date ASC
        """).fetchall()
        active = [w for w in warranties if w[9] == 'active' and w[7] >= today_str]
        expiring = [w for w in warranties if w[9] == 'active' and w[7] < today_str]
        # Auto-expire
        for w in expiring:
            conn.execute("UPDATE warranties SET status='expired' WHERE id=?", (w[0],))
        conn.commit()
    return render_template("warranties.html", warranties=warranties, active_count=len(active),
                          expired_count=len(expiring), today=today_str)



@main_bp.route("/warranties/add", methods=["POST"])
@login_required
def add_warranty():
    invoice_id = int(request.form.get('invoice_id', 0))
    car_id = int(request.form.get('car_id', 0))
    customer_id = int(request.form.get('customer_id', 0))
    service = request.form.get('service', '')
    warranty_days = int(request.form.get('warranty_days', 30))
    conditions = request.form.get('conditions', '')
    from datetime import date, timedelta
    start = date.today()
    end = start + timedelta(days=warranty_days)
    with get_db() as conn:
        conn.execute("""INSERT INTO warranties (invoice_id, car_id, customer_id, service, warranty_days, start_date, end_date, conditions)
            VALUES (?,?,?,?,?,?,?,?)""",
            (invoice_id, car_id, customer_id, service, warranty_days, start.isoformat(), end.isoformat(), conditions))
        conn.commit()
    flash(f"Garantie {warranty_days}j ajoutée pour {service}", "success")
    return redirect("/warranties")



@main_bp.route("/inspection/<int:appointment_id>", methods=["GET", "POST"])
@login_required
def inspection_checklist(appointment_id):
    import json
    with get_db() as conn:
        appt = conn.execute("SELECT a.*, cr.id as car_id, c.name FROM appointments a JOIN cars cr ON a.car_id=cr.id JOIN customers c ON cr.customer_id=c.id WHERE a.id=?", (appointment_id,)).fetchone()
        if not appt:
            flash("RDV introuvable", "danger")
            return redirect("/appointments")
        existing = conn.execute("SELECT * FROM inspection_checklists WHERE appointment_id=?", (appointment_id,)).fetchone()
        if request.method == "POST":
            checklist_data = {}
            for category, items in INSPECTION_ITEMS:
                for item in items:
                    key = f"{category}_{item}".replace(' ', '_').replace('.', '')
                    checklist_data[key] = {
                        'status': request.form.get(f'status_{key}', 'ok'),
                        'note': request.form.get(f'note_{key}', '')
                    }
            notes = request.form.get('notes', '')
            inspector = session.get('username', '')
            if existing:
                conn.execute("UPDATE inspection_checklists SET checklist_data=?, notes=?, inspector=?, status='completed' WHERE id=?",
                    (json.dumps(checklist_data), notes, inspector, existing[0]))
            else:
                conn.execute("INSERT INTO inspection_checklists (appointment_id, car_id, inspector, checklist_data, notes, status) VALUES (?,?,?,?,?,?)",
                    (appointment_id, appt[-2], inspector, json.dumps(checklist_data), notes, 'completed'))
            conn.commit()
            flash("Checklist de contrôle enregistrée !", "success")
            return redirect(f"/inspection/{appointment_id}")
        checklist = json.loads(existing[4]) if existing and existing[4] else {}
    return render_template("inspection_checklist.html", appt=appt, existing=existing,
                          checklist=checklist, items=INSPECTION_ITEMS, notes=existing[5] if existing else '')



# ─── 5. Service Profitability Report (Enhanced) ───
@main_bp.route("/service_profitability")
@login_required
def service_profitability_report():
    with get_db() as conn:
        # Revenue per service
        services = conn.execute("""SELECT a.service,
            COUNT(*) as cnt,
            SUM(i.amount) as revenue,
            SUM(CASE WHEN i.status='paid' THEN i.amount ELSE 0 END) as collected
            FROM appointments a JOIN invoices i ON i.appointment_id=a.id
            GROUP BY a.service ORDER BY revenue DESC""").fetchall()
        # Material costs per service (from service_inventory)
        material_costs = {}
        for si in conn.execute("""SELECT si.service_name, SUM(si.quantity_used * inv.unit_price) as cost
            FROM service_inventory si JOIN inventory inv ON si.inventory_id=inv.id
            GROUP BY si.service_name""").fetchall():
            material_costs[si[0]] = si[1]
    report = []
    for s in services:
        name, cnt, revenue, collected = s[0], s[1], s[2] or 0, s[3] or 0
        mat_cost = material_costs.get(name, 0) * cnt
        profit = revenue - mat_cost
        margin = (profit / revenue * 100) if revenue > 0 else 0
        report.append({'name': name, 'count': cnt, 'revenue': revenue, 'collected': collected,
                       'material_cost': mat_cost, 'profit': profit, 'margin': margin})
    return render_template("service_profitability.html", report=report)



@main_bp.route("/custom_dashboard")
@login_required
def custom_dashboard():
    from datetime import date, timedelta
    uid = session.get('user_id', 0)
    with get_db() as conn:
        widgets = conn.execute("SELECT * FROM dashboard_widgets WHERE user_id=? AND visible=1 ORDER BY position",
                              (uid,)).fetchall()
        if not widgets:
            # Auto-create default widgets
            for i, w in enumerate(AVAILABLE_WIDGETS[:6]):
                conn.execute("INSERT INTO dashboard_widgets (user_id, widget_type, position, visible) VALUES (?,?,?,1)",
                            (uid, w['type'], i))
            conn.commit()
            widgets = conn.execute("SELECT * FROM dashboard_widgets WHERE user_id=? AND visible=1 ORDER BY position",
                                  (uid,)).fetchall()
        # Build widget data
        today = date.today().isoformat()
        data = {}
        for w in widgets:
            wtype = w[2]
            if wtype == 'today_revenue':
                val = conn.execute("SELECT COALESCE(SUM(i.amount),0) FROM invoices i JOIN appointments a ON i.appointment_id=a.id WHERE a.date=?", (today,)).fetchone()[0]
                data[wtype] = f"{val:.0f} DT"
            elif wtype == 'today_appointments':
                val = conn.execute("SELECT COUNT(*) FROM appointments WHERE date=?", (today,)).fetchone()[0]
                data[wtype] = str(val)
            elif wtype == 'pending_invoices':
                val = conn.execute("SELECT COUNT(*) FROM invoices WHERE status IN ('unpaid','partial')").fetchone()[0]
                data[wtype] = str(val)
            elif wtype == 'low_stock':
                val = conn.execute("SELECT COUNT(*) FROM inventory WHERE quantity <= min_quantity").fetchone()[0]
                data[wtype] = str(val)
            elif wtype == 'queue_count':
                val = conn.execute("SELECT COUNT(*) FROM waiting_queue WHERE status='waiting'").fetchone()[0]
                data[wtype] = str(val)
            elif wtype == 'top_services':
                rows = conn.execute("SELECT service, COUNT(*) as cnt FROM appointments GROUP BY service ORDER BY cnt DESC LIMIT 5").fetchall()
                data[wtype] = [tuple(r) for r in rows]
            elif wtype == 'recent_customers':
                rows = conn.execute("SELECT name, phone FROM customers ORDER BY id DESC LIMIT 5").fetchall()
                data[wtype] = [tuple(r) for r in rows]
            elif wtype == 'alerts':
                val = conn.execute("SELECT COUNT(*) FROM smart_alerts WHERE is_read=0").fetchone()[0]
                data[wtype] = str(val)
            elif wtype == 'crm_pending':
                val = conn.execute("SELECT COUNT(*) FROM crm_followups WHERE status='pending'").fetchone()[0]
                data[wtype] = str(val)
            elif wtype == 'monthly_chart':
                rows = conn.execute("""SELECT strftime('%Y-%m', a.date) as m, SUM(i.amount)
                    FROM invoices i JOIN appointments a ON i.appointment_id=a.id
                    WHERE a.date >= date('now', '-6 months')
                    GROUP BY m ORDER BY m""").fetchall()
                data[wtype] = [tuple(r) for r in rows]
    return render_template("custom_dashboard.html", widgets=widgets, data=data,
                          available=AVAILABLE_WIDGETS)



@main_bp.route("/custom_dashboard/toggle", methods=["POST"])
@login_required
def custom_dashboard_toggle():
    uid = session.get('user_id', 0)
    wtype = request.form.get("widget_type", "")
    action = request.form.get("action", "add")
    with get_db() as conn:
        if action == 'add':
            pos = conn.execute("SELECT COALESCE(MAX(position),0)+1 FROM dashboard_widgets WHERE user_id=?", (uid,)).fetchone()[0]
            conn.execute("INSERT INTO dashboard_widgets (user_id, widget_type, position, visible) VALUES (?,?,?,1)",
                        (uid, wtype, pos))
        elif action == 'remove':
            conn.execute("DELETE FROM dashboard_widgets WHERE user_id=? AND widget_type=?", (uid, wtype))
        conn.commit()
    return redirect("/custom_dashboard")



@main_bp.route("/webhook/add", methods=["POST"])
@login_required
@admin_required
def webhook_add():
    name = request.form.get("name", "").strip()
    url_val = request.form.get("url", "").strip()
    events = request.form.get("events", "")
    if name and url_val:
        secret = uuid.uuid4().hex[:24]
        with get_db() as conn:
            conn.execute("INSERT INTO webhooks (name, url, events, secret) VALUES (?,?,?,?)",
                        (name, url_val, events, secret))
            conn.commit()
        flash("Webhook ajouté !", "success")
    return redirect("/api_settings")



@main_bp.route("/webhook/toggle/<int:wid>", methods=["POST"])
@login_required
@admin_required
def webhook_toggle(wid):
    with get_db() as conn:
        w = conn.execute("SELECT active FROM webhooks WHERE id=?", (wid,)).fetchone()
        if w:
            conn.execute("UPDATE webhooks SET active=? WHERE id=?", (0 if w[0] else 1, wid))
            conn.commit()
    return redirect("/api_settings")



# ─── 2. Support Motos — vehicle_type already in migration, update add_car ───

@main_bp.route("/add_car_vehicle", methods=["POST"])
@login_required
def add_car_vehicle():
    """Enhanced car/moto add with vehicle_type"""
    customer_id = request.form.get("customer_id", 0, type=int)
    brand = request.form.get("brand", "").strip()
    model = request.form.get("model", "").strip()
    plate = request.form.get("plate", "").strip()
    vehicle_type = request.form.get("vehicle_type", "voiture")
    color = request.form.get("color", "").strip()
    year = request.form.get("year", 0, type=int)
    if customer_id and brand and model and plate:
        with get_db() as conn:
            conn.execute("""INSERT INTO cars (customer_id, brand, model, plate, vehicle_type, color, year)
                VALUES (?,?,?,?,?,?,?)""", (customer_id, brand, model, plate, vehicle_type, color, year))
            conn.commit()
        flash("Véhicule ajouté ✅", "success")
    return redirect(f"/customer/{customer_id}")



# ─── 5. Checklist Qualité par Service ───

@main_bp.route("/service_checklists")
@login_required
@admin_required
def service_checklists_view():
    with get_db() as conn:
        checklists = conn.execute("SELECT * FROM service_checklists ORDER BY service_name").fetchall()
        services = conn.execute("SELECT DISTINCT name FROM services ORDER BY name").fetchall()
    return render_template("service_checklists.html", checklists=checklists, services=services)



@main_bp.route("/service_checklist/add", methods=["POST"])
@login_required
@admin_required
def service_checklist_add():
    service_name = request.form.get("service_name", "")
    vehicle_type = request.form.get("vehicle_type", "all")
    items = request.form.get("checklist_items", "")
    if service_name and items:
        with get_db() as conn:
            conn.execute("INSERT INTO service_checklists (service_name, vehicle_type, checklist_items) VALUES (?,?,?)",
                        (service_name, vehicle_type, items))
            conn.commit()
        flash("Checklist créée ✅", "success")
    return redirect("/service_checklists")



@main_bp.route('/service_cost/update', methods=['POST'])
@login_required
def service_cost_update():
    service_id = int(request.form['service_id'])
    with get_db() as conn:
        conn.execute("""UPDATE services SET cost_products=?, cost_labor_minutes=? WHERE id=?""",
            (float(request.form.get('cost_products', 0)),
             int(request.form.get('cost_labor_minutes', 0)), service_id))
        conn.commit()
    flash("Coûts mis à jour", "success")
    return redirect("/service_cost_calculator")

# ── 6. Appointment Waitlist ──
@main_bp.route('/appointment_waitlist')
@login_required
def appointment_waitlist():
    with get_db() as conn:
        waitlist = conn.execute("""
            SELECT w.*, c.name as cname, c.phone as cphone
            FROM appointment_waitlist w
            LEFT JOIN customers c ON w.customer_id = c.id
            ORDER BY CASE w.status WHEN 'waiting' THEN 0 WHEN 'notified' THEN 1 ELSE 2 END,
            w.created_at DESC
        """).fetchall()
        customers = conn.execute("SELECT id, name, phone FROM customers ORDER BY name").fetchall()
        # Check for available slots today
        today = datetime.now().strftime('%Y-%m-%d')
        today_count = conn.execute(
            "SELECT COUNT(*) FROM appointments WHERE date=? AND status != 'cancelled'",
            (today,)).fetchone()[0]
        max_daily = 10
        try:
            ms = conn.execute("SELECT value FROM settings WHERE key='max_daily_appointments'").fetchone()
            if ms:
                max_daily = int(ms['value'])
        except (ValueError, TypeError, AttributeError):
            pass
        available_slots = max(0, max_daily - today_count)
    return render_template('appointment_waitlist.html', waitlist=waitlist,
                          customers=customers, available_slots=available_slots, today=today)



@main_bp.route('/attendance/record', methods=['POST'])
@login_required
def attendance_record():
    emp_id = int(request.form['employee_id'])
    with get_db() as conn:
        emp = conn.execute("SELECT full_name FROM users WHERE id=?", (emp_id,)).fetchone()
        date = request.form['date']
        check_in = request.form.get('check_in', '')
        check_out = request.form.get('check_out', '')
        status = request.form.get('status', 'present')
        # Calculate late minutes (assume start is 08:00)
        late_min = 0
        if check_in and status == 'present':
            try:
                ci = datetime.strptime(check_in, '%H:%M')
                start = datetime.strptime('08:00', '%H:%M')
                if ci > start:
                    late_min = int((ci - start).total_seconds() / 60)
                    if late_min > 15:
                        status = 'late'
            except (ValueError, TypeError, AttributeError):
                pass
        # Calculate overtime
        overtime = 0
        if check_out:
            try:
                co = datetime.strptime(check_out, '%H:%M')
                end = datetime.strptime('17:00', '%H:%M')
                if co > end:
                    overtime = int((co - end).total_seconds() / 60)
            except (ValueError, TypeError, AttributeError):
                pass

        existing = conn.execute("SELECT id FROM employee_attendance WHERE employee_id=? AND date=?",
                               (emp_id, date)).fetchone()
        if existing:
            conn.execute("""UPDATE employee_attendance SET check_in=?, check_out=?, status=?,
                late_minutes=?, overtime_minutes=?, notes=? WHERE id=?""",
                (check_in, check_out, status, late_min, overtime,
                 request.form.get('notes', ''), existing['id']))
        else:
            conn.execute("""INSERT INTO employee_attendance
                (employee_id, employee_name, date, check_in, check_out, status, late_minutes, overtime_minutes, notes)
                VALUES (?,?,?,?,?,?,?,?,?)""",
                (emp_id, emp['full_name'] if emp else '', date, check_in, check_out,
                 status, late_min, overtime, request.form.get('notes', '')))
        conn.commit()
    flash("Présence enregistrée", "success")
    return redirect(f"/employee_attendance?month={date[:7]}")

# ── 8. Supplier Performance ──
@main_bp.route('/supplier_performance')
@login_required
def supplier_performance():
    with get_db() as conn:
        suppliers = conn.execute("""
            SELECT s.*, COUNT(sr.id) as review_count,
                   AVG(sr.delivery_rating) as avg_delivery,
                   AVG(sr.quality_rating) as avg_quality,
                   AVG(sr.price_rating) as avg_price,
                   AVG(sr.overall_rating) as avg_overall
            FROM suppliers s
            LEFT JOIN supplier_reviews sr ON s.id = sr.supplier_id
            GROUP BY s.id ORDER BY avg_overall DESC
        """).fetchall()
        recent_reviews = conn.execute("""
            SELECT sr.*, s.name as supplier_name
            FROM supplier_reviews sr
            LEFT JOIN suppliers s ON sr.supplier_id = s.id
            ORDER BY sr.created_at DESC LIMIT 20
        """).fetchall()
    return render_template('supplier_performance.html', suppliers=suppliers,
                          recent_reviews=recent_reviews)



@main_bp.route('/currency/update', methods=['POST'])
@login_required
def currency_update():
    with get_db() as conn:
        rate_id = int(request.form['rate_id'])
        conn.execute("UPDATE currency_rates SET rate_to_tnd=?, updated_at=? WHERE id=?",
                    (float(request.form['rate_to_tnd']),
                     datetime.now().strftime('%Y-%m-%d %H:%M'), rate_id))
        conn.commit()
    flash("Taux mis à jour", "success")
    return redirect("/multi_currency")



@main_bp.route('/currency/add', methods=['POST'])
@login_required
def currency_add():
    with get_db() as conn:
        conn.execute("INSERT INTO currency_rates (currency_code, currency_name, rate_to_tnd) VALUES (?,?,?)",
                    (request.form['currency_code'].upper(),
                     request.form['currency_name'],
                     float(request.form.get('rate_to_tnd', 1))))
        conn.commit()
    flash("Devise ajoutée", "success")
    return redirect("/multi_currency")

