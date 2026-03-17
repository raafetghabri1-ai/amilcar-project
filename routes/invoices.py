"""
AMILCAR — Invoices, Payments & Billing
Blueprint: invoices_bp
Routes: 42
"""
from flask import Blueprint, render_template, request, redirect, url_for, flash, make_response, jsonify, session, send_file, current_app
from helpers import login_required, admin_required, client_required, get_db, get_services, get_setting, get_all_settings
from helpers import allowed_file, safe_page, log_activity, build_wa_url, STATUS_MESSAGES, UPLOAD_FOLDER, MAX_FILE_SIZE, MAX_FILES, PER_PAGE, cache
from database.db import get_db
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, date, timedelta
import os, re, uuid, io
import time as time_module
import sqlite3

invoices_bp = Blueprint("invoices_bp", __name__)


@invoices_bp.route('/invoices')
@login_required
def invoices():
    page = safe_page(request.args.get('page', 1, type=int))
    status_filter = request.args.get('status', '')
    with get_db() as conn:
        base_q = ("FROM invoices i JOIN appointments a ON i.appointment_id = a.id "
                  "JOIN cars ca ON a.car_id = ca.id JOIN customers cu ON ca.customer_id = cu.id")
        where = ""
        params_filter = []
        if status_filter in ('paid', 'unpaid', 'partial'):
            where = " WHERE i.status = ?"
            params_filter = [status_filter]
        total = conn.execute(f"SELECT COUNT(*) {base_q}{where}", params_filter).fetchone()[0]
        all_invoices = conn.execute(
            f"SELECT i.id, a.id, i.amount, i.status, a.date, a.service, cu.name, i.payment_method, COALESCE(i.paid_amount, 0), "
            f"COALESCE(i.discount_type, ''), COALESCE(i.discount_value, 0) "
            f"{base_q}{where} ORDER BY i.id DESC LIMIT ? OFFSET ?",
            params_filter + [PER_PAGE, (page - 1) * PER_PAGE]
        ).fetchall()
    total_pages = (total + PER_PAGE - 1) // PER_PAGE
    return render_template('invoices.html', invoices=all_invoices,
                           page=page, total_pages=total_pages, status_filter=status_filter)
@invoices_bp.route('/add_customer', methods=['GET', 'POST'])
@login_required
def new_customer():
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        phone = request.form.get('phone', '').strip()
        email = request.form.get('email', '').strip()
        if not name or len(name) < 2:
            flash('Le nom doit contenir au moins 2 caractères', 'error')
            return render_template('add_customer.html')
        if not phone or not re.match(r'^[0-9+\s\-]{4,20}$', phone):
            flash('Entrez un numéro de téléphone valide (4-20 chiffres)', 'error')
            return render_template('add_customer.html')
        if email and not re.match(r'^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$', email):
            flash('Adresse email invalide', 'error')
            return render_template('add_customer.html')
        with get_db() as conn_dup:
            existing = conn_dup.execute("SELECT id, name FROM customers WHERE phone = ?", (phone,)).fetchone()
            if existing:
                flash(f'Ce numéro est déjà utilisé par le client : {existing[1]}', 'error')
                return render_template('add_customer.html')
        add_customer(name, phone, request.form.get('notes', '').strip())
        # Update email
        if email:
            with get_db() as conn_c:
                cust = conn_c.execute("SELECT id FROM customers WHERE phone = ? ORDER BY id DESC LIMIT 1", (phone,)).fetchone()
                if cust:
                    conn_c.execute("UPDATE customers SET email = ? WHERE id = ?", (email, cust[0]))
                    conn_c.commit()
        log_activity('Add Customer', f'Customer: {name}')
        flash('Client ajouté avec succès', 'success')
        return redirect(url_for('customers'))
    return render_template('add_customer.html')



@invoices_bp.route("/pay_invoice/<int:invoice_id>", methods=["POST"])
@login_required
def pay_invoice(invoice_id):
    payment_method = request.form.get("payment_method", "cash")
    pay_amount = request.form.get("pay_amount", "")
    with get_db() as conn:
        inv = conn.execute("SELECT amount, COALESCE(paid_amount, 0) FROM invoices WHERE id = ?", (invoice_id,)).fetchone()
        if inv:
            total = inv[0]
            already_paid = inv[1]
            if pay_amount:
                try:
                    new_pay = float(pay_amount)
                    if new_pay <= 0:
                        raise ValueError
                except ValueError:
                    new_pay = total - already_paid
            else:
                new_pay = total - already_paid
            new_total_paid = already_paid + new_pay
            if new_total_paid >= total:
                conn.execute("UPDATE invoices SET status = 'paid', payment_method = ?, paid_amount = ? WHERE id = ?",
                    (payment_method, total, invoice_id))
            else:
                conn.execute("UPDATE invoices SET status = 'partial', payment_method = ?, paid_amount = ? WHERE id = ?",
                    (payment_method, new_total_paid, invoice_id))
            conn.commit()
    log_activity('Pay Invoice', f'Invoice #{invoice_id} ({payment_method})')
    cache.invalidate_prefix('chart_')
    cache.invalidate_prefix('weekly_')
    cache.invalidate_prefix('monthly_')
    cache.invalidate_prefix('profit_')
    try:
        notify = current_app.config.get('notify_update')
        if notify:
            notify('invoice_paid', {'id': invoice_id})
    except Exception:
        pass
    return redirect("/invoices")



@invoices_bp.route("/quotes")
@login_required
def quotes():
    page = safe_page(request.args.get('page', 1, type=int))
    per_page = 15
    with get_db() as conn:
        total = conn.execute("SELECT COUNT(*) FROM quotes").fetchone()[0]
        total_pages = max(1, (total + per_page - 1) // per_page)
        all_quotes = conn.execute("SELECT * FROM quotes ORDER BY id DESC LIMIT ? OFFSET ?",
            (per_page, (page - 1) * per_page)).fetchall()
    return render_template("quotes.html", quotes=all_quotes, page=page, total_pages=total_pages)



@invoices_bp.route("/set_price/<int:quote_id>", methods=["POST"])
@login_required
def set_price(quote_id):
    price = request.form.get("price", "").strip()
    if not price:
        flash('Entrez un prix', 'error')
        return redirect("/quotes")
    try:
        price_val = float(price)
        if price_val < 0:
            raise ValueError
    except ValueError:
        flash('Entrez un prix valide', 'error')
        return redirect("/quotes")
    with get_db() as conn:
        conn.execute("UPDATE quotes SET price = ?, status = 'priced' WHERE id = ?", (price_val, quote_id))
        conn.commit()
    return redirect("/quotes")



@invoices_bp.route("/convert_quote/<int:quote_id>", methods=["GET", "POST"])
@login_required
def convert_quote(quote_id):
    from datetime import date
    with get_db() as conn:
        quote = conn.execute("SELECT * FROM quotes WHERE id = ?", (quote_id,)).fetchone()
        if not quote:
            return redirect("/quotes")

        if request.method == "POST":
            name = request.form.get("name", "").strip()
            phone = request.form.get("phone", "").strip()
            brand = request.form.get("brand", "").strip()
            model = request.form.get("model", "").strip()
            plate = request.form.get("plate", "").strip()
            service = request.form.get("service", "").strip()
            price = request.form.get("price", "0")

            if not name or not phone or not brand or not model or not plate:
                flash("Tous les champs sont requis", "error")
                return render_template("convert_quote.html", quote=quote)

            # البحث عن عميل موجود بنفس رقم الهاتف أو إنشاء جديد
            customer = conn.execute("SELECT id FROM customers WHERE phone = ?", (phone,)).fetchone()
            if customer:
                customer_id = customer[0]
            else:
                cursor = conn.execute("INSERT INTO customers (name, phone) VALUES (?, ?)", (name, phone))
                customer_id = cursor.lastrowid

            # البحث عن سيارة بنفس اللوحة أو إنشاء جديدة
            car = conn.execute("SELECT id FROM cars WHERE plate = ? AND customer_id = ?", (plate, customer_id)).fetchone()
            if car:
                car_id = car[0]
            else:
                cursor = conn.execute("INSERT INTO cars (customer_id, brand, model, plate) VALUES (?, ?, ?, ?)",
                    (customer_id, brand, model, plate))
                car_id = cursor.lastrowid

            # إنشاء الموعد
            try:
                price_val = float(price)
            except ValueError:
                price_val = 0
            service_text = f"{service} - {price_val} DT" if price_val else service
            conn.execute("INSERT INTO appointments (car_id, date, service) VALUES (?, ?, ?)",
                (car_id, str(date.today()), service_text))
            conn.execute("UPDATE quotes SET status = 'converted' WHERE id = ?", (quote_id,))
            conn.commit()
            flash("Devis converti en rendez-vous avec succès", "success")
            return redirect("/appointments")

    return render_template("convert_quote.html", quote=quote)



# ─── Bulk Invoice Operations ───
@invoices_bp.route("/bulk_pay_invoices", methods=["POST"])
@login_required
def bulk_pay_invoices():
    invoice_ids = request.form.getlist("invoice_ids")
    payment_method = request.form.get("payment_method", "cash")
    if not invoice_ids:
        flash("Aucune facture sélectionnée", "error")
        return redirect("/invoices")
    with get_db() as conn:
        count = 0
        for inv_id in invoice_ids:
            try:
                conn.execute("UPDATE invoices SET status = 'paid', payment_method = ? WHERE id = ? AND status = 'unpaid'",
                    (payment_method, int(inv_id)))
                count += 1
            except (ValueError, TypeError):
                pass
        conn.commit()
    log_activity('Bulk Pay Invoices', f'{count} invoices marked as paid ({payment_method})')
    flash(f"{count} factures marquées comme payées", "success")
    return redirect("/invoices")



@invoices_bp.route("/print_invoice/<int:invoice_id>")
@login_required
def print_invoice(invoice_id):
    with get_db() as conn:
        inv = conn.execute(
            "SELECT i.id, i.appointment_id, i.amount, i.status, a.date, a.service, "
            "cu.name, cu.phone, ca.brand, ca.model, ca.plate, COALESCE(i.paid_amount, 0), i.payment_method, "
            "COALESCE(i.discount_type, ''), COALESCE(i.discount_value, 0) "
            "FROM invoices i JOIN appointments a ON i.appointment_id = a.id "
            "JOIN cars ca ON a.car_id = ca.id JOIN customers cu ON ca.customer_id = cu.id "
            "WHERE i.id = ?", (invoice_id,)).fetchone()
    settings = get_all_settings()
    return render_template("print_invoice.html", inv=inv, settings=settings)



@invoices_bp.route("/download_invoice/<int:invoice_id>")
@login_required
def download_invoice(invoice_id):
    from xhtml2pdf import pisa
    with get_db() as conn:
        inv = conn.execute(
            "SELECT i.id, i.appointment_id, i.amount, i.status, a.date, a.service, "
            "cu.name, cu.phone, ca.brand, ca.model, ca.plate, COALESCE(i.paid_amount, 0), i.payment_method, "
            "COALESCE(i.discount_type, ''), COALESCE(i.discount_value, 0) "
            "FROM invoices i JOIN appointments a ON i.appointment_id = a.id "
            "JOIN cars ca ON a.car_id = ca.id JOIN customers cu ON ca.customer_id = cu.id "
            "WHERE i.id = ?", (invoice_id,)).fetchone()
    if not inv:
        return redirect("/invoices")
    settings = get_all_settings()
    html = render_template("print_invoice.html", inv=inv, settings=settings)
    # Embed logo as base64 for PDF compatibility
    import base64
    logo_path = os.path.join(os.path.abspath('static'), 'logo.png')
    if os.path.exists(logo_path):
        with open(logo_path, 'rb') as f:
            logo_b64 = base64.b64encode(f.read()).decode()
        html = html.replace('/static/logo.png', f'data:image/png;base64,{logo_b64}')
    try:
        pdf_buffer = io.BytesIO()
        pisa.CreatePDF(io.StringIO(html), dest=pdf_buffer)
        pdf_buffer.seek(0)
    except Exception as e:
        flash(f"Erreur de génération PDF : {str(e)}", "error")
        log_activity('PDF Error', f'Invoice #{invoice_id}: {str(e)}')
        return redirect("/invoices")
    response = make_response(pdf_buffer.read())
    response.headers['Content-Type'] = 'application/pdf'
    response.headers['Content-Disposition'] = f'attachment; filename=invoice_{invoice_id}.pdf'
    return response



@invoices_bp.route("/expenses")
@login_required
def expenses():
    page = safe_page(request.args.get('page', 1, type=int))
    month = request.args.get('month', '')
    with get_db() as conn:
        if month:
            year, mon = map(int, month.split("-"))
            ms = f"{year}-{mon:02d}-01"
            if mon == 12:
                me = f"{year+1}-01-01"
            else:
                me = f"{year}-{mon+1:02d}-01"
            total = conn.execute("SELECT COUNT(*) FROM expenses WHERE date >= ? AND date < ?", (ms, me)).fetchone()[0]
            all_expenses = conn.execute(
                "SELECT * FROM expenses WHERE date >= ? AND date < ? ORDER BY date DESC LIMIT ? OFFSET ?",
                (ms, me, PER_PAGE, (page - 1) * PER_PAGE)).fetchall()
            total_amount = conn.execute(
                "SELECT COALESCE(SUM(amount),0) FROM expenses WHERE date >= ? AND date < ?",
                (ms, me)).fetchone()[0]
        else:
            total = conn.execute("SELECT COUNT(*) FROM expenses").fetchone()[0]
            all_expenses = conn.execute(
                "SELECT * FROM expenses ORDER BY date DESC LIMIT ? OFFSET ?",
                (PER_PAGE, (page - 1) * PER_PAGE)).fetchall()
            total_amount = conn.execute("SELECT COALESCE(SUM(amount),0) FROM expenses").fetchone()[0]
    total_pages = (total + PER_PAGE - 1) // PER_PAGE
    return render_template("expenses.html", expenses=all_expenses,
        page=page, total_pages=total_pages, total_amount=total_amount, month=month)



@invoices_bp.route("/export/expenses_csv")
@login_required
def export_expenses_csv():
    import csv
    month = request.args.get("month", "")
    with get_db() as conn:
        if month:
            year, mon = map(int, month.split("-"))
            ms = f"{year}-{mon:02d}-01"
            if mon == 12:
                me = f"{year+1}-01-01"
            else:
                me = f"{year}-{mon+1:02d}-01"
            expenses = conn.execute(
                "SELECT id, date, category, description, amount FROM expenses "
                "WHERE date >= ? AND date < ? ORDER BY date", (ms, me)).fetchall()
        else:
            expenses = conn.execute("SELECT id, date, category, description, amount FROM expenses ORDER BY date").fetchall()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["ID", "Date", "Catégorie", "Description", "Montant (DT)"])
    for e in expenses:
        writer.writerow(e)
    response = make_response(output.getvalue())
    response.headers['Content-Type'] = 'text/csv; charset=utf-8'
    fname = f'expenses_{month}.csv' if month else 'expenses_all.csv'
    response.headers['Content-Disposition'] = f'attachment; filename={fname}'
    return response



@invoices_bp.route("/invoice_view/<token>")
def public_invoice_view(token):
    if not token or len(token) < 10:
        return render_template('404.html'), 404
    with get_db() as conn:
        inv = conn.execute(
            "SELECT i.id, i.amount, i.status, a.date, a.service, cu.name, cu.phone, "
            "ca.brand, ca.model, ca.plate, COALESCE(i.paid_amount,0), i.payment_method, "
            "COALESCE(i.discount_type,''), COALESCE(i.discount_value,0) "
            "FROM invoices i JOIN appointments a ON i.appointment_id = a.id "
            "JOIN cars ca ON a.car_id = ca.id JOIN customers cu ON ca.customer_id = cu.id "
            "WHERE i.qr_token = ?", (token,)).fetchone()
    if not inv:
        return render_template('404.html'), 404
    settings = get_all_settings()
    return render_template("public_invoice.html", inv=inv, settings=settings)



# ─── Phase 6 Feature 4: Promo Coupons System ───
@invoices_bp.route("/coupons")
@login_required
def coupons_page():
    with get_db() as conn:
        coupons = conn.execute("SELECT * FROM coupons ORDER BY created_at DESC").fetchall()
    return render_template("coupons.html", coupons=coupons)



@invoices_bp.route("/coupons/add", methods=["POST"])
@login_required
def add_coupon():
    code = request.form.get("code", "").strip().upper()
    discount_type = request.form.get("discount_type", "percent")
    discount_value = float(request.form.get("discount_value", 0))
    max_uses = int(request.form.get("max_uses", 1))
    expires_at = request.form.get("expires_at", "")
    min_amount = float(request.form.get("min_amount", 0))
    if not code or discount_value <= 0:
        flash("Code et valeur requis", "error")
        return redirect("/coupons")
    if discount_type not in ('percent', 'fixed'):
        discount_type = 'percent'
    with get_db() as conn:
        existing = conn.execute("SELECT id FROM coupons WHERE code = ?", (code,)).fetchone()
        if existing:
            flash("Ce code existe déjà", "error")
            return redirect("/coupons")
        conn.execute("INSERT INTO coupons (code, discount_type, discount_value, max_uses, expires_at, min_amount) VALUES (?,?,?,?,?,?)",
            (code, discount_type, discount_value, max_uses, expires_at, min_amount))
        conn.commit()
    log_activity('Coupon Created', f'{code} ({discount_type}: {discount_value})')
    flash(f"Coupon {code} créé", "success")
    return redirect("/coupons")



@invoices_bp.route("/coupons/toggle/<int:coupon_id>", methods=["POST"])
@login_required
def toggle_coupon(coupon_id):
    with get_db() as conn:
        conn.execute("UPDATE coupons SET active = CASE WHEN active = 1 THEN 0 ELSE 1 END WHERE id = ?", (coupon_id,))
        conn.commit()
    flash("Statut modifié", "success")
    return redirect("/coupons")



@invoices_bp.route("/coupons/delete/<int:coupon_id>", methods=["POST"])
@login_required
def delete_coupon(coupon_id):
    with get_db() as conn:
        conn.execute("DELETE FROM coupons WHERE id = ?", (coupon_id,))
        conn.commit()
    flash("Coupon supprimé", "success")
    return redirect("/coupons")



# ─── Phase 7 Feature 3: Advanced Quotes ───
@invoices_bp.route("/quotes_advanced")
@login_required
def quotes_advanced():
    with get_db() as conn:
        quotes = conn.execute("""
            SELECT q.*, CASE WHEN q.converted_invoice_id > 0 THEN 'Convertie'
            WHEN q.status='accepted' THEN 'Accepté'
            WHEN q.status='rejected' THEN 'Refusé'
            WHEN q.status='expired' THEN 'Expiré'
            ELSE 'En attente' END as display_status
            FROM quotes q ORDER BY q.id DESC
        """).fetchall()
    return render_template("quotes_advanced.html", quotes=quotes)



@invoices_bp.route("/quote_to_invoice/<int:quote_id>", methods=["POST"])
@login_required
def quote_to_invoice(quote_id):
    with get_db() as conn:
        quote = conn.execute("SELECT * FROM quotes WHERE id=?", (quote_id,)).fetchone()
        if not quote:
            flash("Devis introuvable", "danger")
            return redirect("/quotes_advanced")
        # Find or create customer
        customer = conn.execute("SELECT id FROM customers WHERE phone=?", (quote[2],)).fetchone()
        if not customer:
            conn.execute("INSERT INTO customers (name, phone) VALUES (?,?)", (quote[1], quote[2]))
            customer_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        else:
            customer_id = customer[0]
        # Create a car placeholder if needed
        car = conn.execute("SELECT id FROM cars WHERE customer_id=?", (customer_id,)).fetchone()
        if not car:
            conn.execute("INSERT INTO cars (customer_id, brand, model, plate) VALUES (?,?,?,?)",
                        (customer_id, 'N/A', 'N/A', 'N/A'))
            car_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        else:
            car_id = car[0]
        # Create appointment
        from datetime import date
        conn.execute("INSERT INTO appointments (car_id, date, service, status) VALUES (?,?,?,?)",
                    (car_id, date.today().isoformat(), quote[3] or 'Service', 'Confirmé'))
        appt_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        # Create invoice
        amount = quote[6] or 0
        conn.execute("INSERT INTO invoices (appointment_id, amount, status, date) VALUES (?,?,?,?)",
                    (appt_id, amount, 'Non payée', date.today().isoformat()))
        inv_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute("UPDATE quotes SET converted_invoice_id=?, status='accepted' WHERE id=?", (inv_id, quote_id))
        conn.commit()
        flash(f"Devis #{quote_id} converti en facture #{inv_id} !", "success")
    return redirect("/quotes_advanced")



@invoices_bp.route("/quote_status/<int:quote_id>/<status>", methods=["POST"])
@login_required
def update_quote_status(quote_id, status):
    if status not in ('accepted', 'rejected', 'expired', 'pending'):
        flash("Statut invalide", "danger")
        return redirect("/quotes_advanced")
    with get_db() as conn:
        conn.execute("UPDATE quotes SET status=? WHERE id=?", (status, quote_id))
        conn.commit()
    flash("Statut du devis mis à jour", "success")
    return redirect("/quotes_advanced")



@invoices_bp.route("/warranty/claim/<int:wid>", methods=["POST"])
@login_required
def warranty_claim(wid):
    with get_db() as conn:
        w = conn.execute("SELECT * FROM warranties WHERE id=?", (wid,)).fetchone()
        if not w:
            flash("Garantie introuvable", "danger")
            return redirect("/warranties")
        from datetime import date
        if w[7] < date.today().isoformat():
            flash("Garantie expirée !", "danger")
        else:
            conn.execute("UPDATE warranties SET status='claimed' WHERE id=?", (wid,))
            conn.commit()
            flash("Réclamation de garantie enregistrée", "success")
    return redirect("/warranties")



# ─── Phase 8 Feature 6: Invoice Terms & Conditions ───
@invoices_bp.route("/invoice_terms", methods=["GET", "POST"])
@login_required
def invoice_terms():
    with get_db() as conn:
        if request.method == "POST":
            terms = request.form.get('terms', '')
            warranty_text = request.form.get('warranty_text', '')
            conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('invoice_terms', ?)", (terms,))
            conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('invoice_warranty_text', ?)", (warranty_text,))
            conn.commit()
            flash("Conditions enregistrées !", "success")
            return redirect("/invoice_terms")
        terms = conn.execute("SELECT value FROM settings WHERE key='invoice_terms'").fetchone()
        warranty_text = conn.execute("SELECT value FROM settings WHERE key='invoice_warranty_text'").fetchone()
    return render_template("invoice_terms.html",
        terms=terms[0] if terms else '', warranty_text=warranty_text[0] if warranty_text else '')



# ─── Phase 8 Feature 7: Dynamic Pricing ───
@invoices_bp.route("/dynamic_pricing")
@login_required
def dynamic_pricing():
    with get_db() as conn:
        rules = conn.execute("SELECT * FROM dynamic_pricing WHERE active=1 ORDER BY service_name").fetchall()
        services = conn.execute("SELECT name, price FROM services WHERE active=1 ORDER BY name").fetchall()
    return render_template("dynamic_pricing.html", rules=rules, services=services)



@invoices_bp.route("/dynamic_pricing/add", methods=["POST"])
@login_required
def add_pricing_rule():
    service_name = request.form.get('service_name', '')
    car_category = request.form.get('car_category', 'sedan')
    season = request.form.get('season', 'normal')
    customer_tier = request.form.get('customer_tier', '')
    price_modifier = float(request.form.get('price_modifier', 1.0))
    fixed_price = float(request.form.get('fixed_price', 0))
    with get_db() as conn:
        conn.execute("INSERT INTO dynamic_pricing (service_name, car_category, season, customer_tier, price_modifier, fixed_price) VALUES (?,?,?,?,?,?)",
            (service_name, car_category, season, customer_tier, price_modifier, fixed_price))
        conn.commit()
    flash("Règle de tarification ajoutée !", "success")
    return redirect("/dynamic_pricing")



@invoices_bp.route("/dynamic_pricing/delete/<int:rid>", methods=["POST"])
@login_required
def delete_pricing_rule(rid):
    with get_db() as conn:
        conn.execute("UPDATE dynamic_pricing SET active=0 WHERE id=?", (rid,))
        conn.commit()
    flash("Règle supprimée", "success")
    return redirect("/dynamic_pricing")



# ─── 4. P&L Financial Dashboard ───
@invoices_bp.route("/pnl_dashboard")
@login_required
@admin_required
def pnl_dashboard():
    from datetime import date
    month = request.args.get("month", date.today().strftime("%Y-%m"))
    with get_db() as conn:
        # Revenue
        revenue = conn.execute("""SELECT COALESCE(SUM(i.amount),0) FROM invoices i
            JOIN appointments a ON i.appointment_id=a.id
            WHERE strftime('%%Y-%%m', a.date)=? AND i.status='paid'""", (month,)).fetchone()[0]
        # Expenses
        expenses = conn.execute("SELECT COALESCE(SUM(amount),0) FROM expenses WHERE strftime('%%Y-%%m', date)=?",
                               (month,)).fetchone()[0]
        # Material costs
        materials = conn.execute("""SELECT COALESCE(SUM(pi.quantity * pi.unit_price),0)
            FROM purchase_items pi JOIN purchase_orders po ON pi.order_id=po.id
            WHERE strftime('%%Y-%%m', po.order_date)=? AND po.status='received'""", (month,)).fetchone()[0]
        # Expense categories
        expense_cats = conn.execute("""SELECT category, SUM(amount) FROM expenses
            WHERE strftime('%%Y-%%m', date)=? GROUP BY category ORDER BY SUM(amount) DESC""", (month,)).fetchall()

        net_profit = revenue - expenses - materials
        # Save P&L
        conn.execute("""INSERT INTO monthly_pnl (month, total_revenue, total_expenses, material_costs, net_profit)
            VALUES (?,?,?,?,?) ON CONFLICT(month) DO UPDATE SET
            total_revenue=?, total_expenses=?, material_costs=?, net_profit=?, calculated_at=CURRENT_TIMESTAMP""",
            (month, revenue, expenses, materials, net_profit, revenue, expenses, materials, net_profit))
        conn.commit()

        # Historical P&L
        history_rows = conn.execute("SELECT * FROM monthly_pnl ORDER BY month DESC LIMIT 12").fetchall()
        history = [dict(r) for r in history_rows]
        expense_cats = list(expense_cats)
    return render_template("pnl_dashboard.html", month=month, revenue=revenue, expenses=expenses,
                          materials=materials, net_profit=net_profit, expense_cats=expense_cats, history=history)



# ─── 9. Accounts Receivable Aging ───
@invoices_bp.route("/ar_aging")
@login_required
@admin_required
def ar_aging():
    from datetime import date, timedelta
    today = date.today()
    with get_db() as conn:
        unpaid = conn.execute("""SELECT i.id, i.amount, i.total_paid, i.status, a.date, a.service,
            c.name, c.phone, ca.plate
            FROM invoices i JOIN appointments a ON i.appointment_id=a.id
            JOIN cars ca ON a.car_id=ca.id JOIN customers c ON ca.customer_id=c.id
            WHERE i.status IN ('unpaid', 'partial')
            ORDER BY a.date""").fetchall()
    buckets = {'current': [], '30': [], '60': [], '90': [], 'over90': []}
    totals = {'current': 0, '30': 0, '60': 0, '90': 0, 'over90': 0}
    for inv in unpaid:
        remaining = (inv[1] or 0) - (inv[2] or 0)
        try:
            inv_date = date.fromisoformat(inv[4])
            days = (today - inv_date).days
        except (ValueError, TypeError):
            days = 999
        if days <= 30:
            bucket = 'current'
        elif days <= 60:
            bucket = '30'
        elif days <= 90:
            bucket = '60'
        elif days <= 120:
            bucket = '90'
        else:
            bucket = 'over90'
        buckets[bucket].append({'id': inv[0], 'amount': inv[1], 'paid': inv[2] or 0, 'remaining': remaining,
                                'date': inv[4], 'service': inv[5], 'customer': inv[6], 'phone': inv[7],
                                'plate': inv[8], 'days': days})
        totals[bucket] += remaining
    grand_total = sum(totals.values())
    return render_template("ar_aging.html", buckets=buckets, totals=totals, grand_total=grand_total)



# ─── 7. Prévision Cash Flow ───

@invoices_bp.route("/cashflow")
@login_required
@admin_required
def cashflow():
    from datetime import date, timedelta
    with get_db() as conn:
        today = date.today()
        months = []
        for i in range(12):
            m = today.month + i
            y = today.year + (m - 1) // 12
            m = ((m - 1) % 12) + 1
            month_str = f"{y}-{m:02d}"
            # Projected income: scheduled appointments
            proj_income = conn.execute("""SELECT COALESCE(SUM(s.price), 0) FROM appointments a 
                JOIN services s ON a.service = s.name 
                WHERE strftime('%%Y-%%m', a.date) = ? AND a.status != 'cancelled'""", (month_str,)).fetchone()[0]
            # Unpaid invoices due this month
            unpaid = conn.execute("""SELECT COALESCE(SUM(amount - COALESCE(paid_amount, 0)), 0) 
                FROM invoices WHERE status IN ('unpaid', 'partial') 
                AND strftime('%%Y-%%m', created_at) = ?""", (month_str,)).fetchone()[0]
            proj_income += unpaid
            # Actual income
            actual_income = conn.execute("""SELECT COALESCE(SUM(amount), 0) FROM invoices 
                WHERE status = 'paid' AND strftime('%%Y-%%m', created_at) = ?""", (month_str,)).fetchone()[0]
            # Projected expenses (average of past 3 months)
            avg_exp = conn.execute("""SELECT COALESCE(AVG(total), 0) FROM (
                SELECT strftime('%%Y-%%m', date) as m, SUM(amount) as total 
                FROM expenses GROUP BY m ORDER BY m DESC LIMIT 3)""").fetchone()[0]
            # Actual expenses
            actual_exp = conn.execute("""SELECT COALESCE(SUM(amount), 0) FROM expenses 
                WHERE strftime('%%Y-%%m', date) = ?""", (month_str,)).fetchone()[0]
            # Save/update projection
            existing = conn.execute("SELECT id FROM cashflow_projections WHERE month=?", (month_str,)).fetchone()
            if existing:
                conn.execute("""UPDATE cashflow_projections SET projected_income=?, projected_expenses=?,
                    actual_income=?, actual_expenses=? WHERE month=?""",
                    (proj_income, avg_exp, actual_income, actual_exp, month_str))
            else:
                conn.execute("""INSERT INTO cashflow_projections 
                    (month, projected_income, projected_expenses, actual_income, actual_expenses) 
                    VALUES (?,?,?,?,?)""", (month_str, proj_income, avg_exp, actual_income, actual_exp))
            months.append({
                'month': month_str, 'proj_income': proj_income, 'proj_expenses': avg_exp,
                'actual_income': actual_income, 'actual_expenses': actual_exp,
                'proj_net': proj_income - avg_exp, 'actual_net': actual_income - actual_exp
            })
        conn.commit()
        # Running balance
        balance = 0
        for m in months:
            if m['actual_income'] > 0:
                balance += m['actual_net']
            else:
                balance += m['proj_net']
            m['balance'] = balance
    return render_template("cashflow.html", months=months)



# ─── 5. Journal Comptable & TVA ───

@invoices_bp.route("/accounting")
@login_required
@admin_required
def accounting():
    from datetime import date
    month = request.args.get("month", date.today().strftime("%Y-%m"))
    with get_db() as conn:
        # Auto-generate entries from invoices
        invoices = conn.execute("""SELECT i.*, a.service, a.date as appt_date FROM invoices i 
            JOIN appointments a ON i.appointment_id=a.id 
            WHERE strftime('%%Y-%%m', i.created_at) = ? AND i.status='paid'""", (month,)).fetchall()
        for inv in invoices:
            existing = conn.execute("SELECT id FROM accounting_entries WHERE reference_type='invoice' AND reference_id=?",
                                  (inv['id'],)).fetchone()
            if not existing:
                conn.execute("""INSERT INTO accounting_entries 
                    (entry_date, account_code, account_name, debit, credit, description, reference_type, reference_id)
                    VALUES (?,?,?,?,?,?,?,?)""",
                    (inv['appt_date'] or inv['created_at'], '701', 'Ventes de services', 0, inv['amount'],
                     f"Facture #{inv['id']} — {inv['service']}", 'invoice', inv['id']))
                conn.execute("""INSERT INTO accounting_entries 
                    (entry_date, account_code, account_name, debit, credit, description, reference_type, reference_id)
                    VALUES (?,?,?,?,?,?,?,?)""",
                    (inv['appt_date'] or inv['created_at'], '411', 'Clients', inv['amount'], 0,
                     f"Facture #{inv['id']} — {inv['service']}", 'invoice', inv['id']))
        # Auto-generate from expenses
        expenses = conn.execute("SELECT * FROM expenses WHERE strftime('%%Y-%%m', date) = ?", (month,)).fetchall()
        for exp in expenses:
            existing = conn.execute("SELECT id FROM accounting_entries WHERE reference_type='expense' AND reference_id=?",
                                  (exp['id'],)).fetchone()
            if not existing:
                conn.execute("""INSERT INTO accounting_entries 
                    (entry_date, account_code, account_name, debit, credit, description, reference_type, reference_id)
                    VALUES (?,?,?,?,?,?,?,?)""",
                    (exp['date'], '6', 'Charges', exp['amount'], 0,
                     f"Dépense: {exp.get('description','')}", 'expense', exp['id']))
                conn.execute("""INSERT INTO accounting_entries 
                    (entry_date, account_code, account_name, debit, credit, description, reference_type, reference_id)
                    VALUES (?,?,?,?,?,?,?,?)""",
                    (exp['date'], '512', 'Banque', 0, exp['amount'],
                     f"Dépense: {exp.get('description','')}", 'expense', exp['id']))
        conn.commit()
        entries = conn.execute("""SELECT * FROM accounting_entries 
            WHERE strftime('%%Y-%%m', entry_date) = ? ORDER BY entry_date, id""", (month,)).fetchall()
        total_debit = sum(e['debit'] for e in entries)
        total_credit = sum(e['credit'] for e in entries)
        # TVA calculation
        settings = conn.execute("SELECT value FROM settings WHERE key='tax_rate'").fetchone()
        tax_rate = float(settings['value']) if settings and settings['value'] else 0
        total_revenue = conn.execute("""SELECT COALESCE(SUM(amount),0) FROM invoices 
            WHERE strftime('%%Y-%%m', created_at)=? AND status='paid'""", (month,)).fetchone()[0]
        tva_collected = total_revenue * tax_rate / 100 if tax_rate else 0
        total_expenses_month = conn.execute("""SELECT COALESCE(SUM(amount),0) FROM expenses 
            WHERE strftime('%%Y-%%m', date)=?""", (month,)).fetchone()[0]
        tva_deductible = total_expenses_month * tax_rate / 100 if tax_rate else 0
        tva_due = tva_collected - tva_deductible
    return render_template("accounting.html", entries=entries, month=month,
                          total_debit=total_debit, total_credit=total_credit,
                          tax_rate=tax_rate, tva_collected=tva_collected,
                          tva_deductible=tva_deductible, tva_due=tva_due,
                          total_revenue=total_revenue, total_expenses=total_expenses_month)



# ─── 6. Contrats de Maintenance ───

@invoices_bp.route("/contracts")
@login_required
def contracts():
    with get_db() as conn:
        all_contracts = conn.execute("""SELECT mc.*, cu.name as customer_name, c.plate, c.brand, c.model
            FROM maintenance_contracts mc 
            JOIN customers cu ON mc.customer_id=cu.id 
            JOIN cars c ON mc.car_id=c.id 
            ORDER BY mc.created_at DESC""").fetchall()
        stats = {
            'active': sum(1 for c in all_contracts if c['status'] == 'active'),
            'total_value': sum(c['price'] for c in all_contracts if c['status'] == 'active'),
            'total_paid': sum(c['paid'] for c in all_contracts),
            'visits_remaining': sum(c['total_visits'] - c['used_visits'] for c in all_contracts if c['status'] == 'active'),
        }
        customers = conn.execute("SELECT id, name FROM customers ORDER BY name").fetchall()
        cars = conn.execute("SELECT c.id, c.plate, c.brand, c.model, cu.name FROM cars c JOIN customers cu ON c.customer_id=cu.id ORDER BY c.plate").fetchall()
        services = conn.execute("SELECT name FROM services ORDER BY name").fetchall()
    return render_template("contracts.html", contracts=all_contracts, stats=stats,
                          customers=customers, cars=cars, services=services)



@invoices_bp.route("/contract/add", methods=["POST"])
@login_required
def add_contract():
    customer_id = request.form.get("customer_id", type=int)
    car_id = request.form.get("car_id", type=int)
    contract_name = request.form.get("contract_name", "").strip()
    start_date = request.form.get("start_date", "")
    end_date = request.form.get("end_date", "")
    total_visits = request.form.get("total_visits", 4, type=int)
    services = request.form.get("included_services", "").strip()
    price = request.form.get("price", 0, type=float)
    notes = request.form.get("notes", "").strip()
    if customer_id and car_id and start_date and end_date:
        with get_db() as conn:
            conn.execute("""INSERT INTO maintenance_contracts 
                (customer_id, car_id, contract_name, start_date, end_date, total_visits, included_services, price, notes)
                VALUES (?,?,?,?,?,?,?,?,?)""",
                (customer_id, car_id, contract_name, start_date, end_date, total_visits, services, price, notes))
            conn.commit()
        flash("Contrat créé !", "success")
    return redirect("/contracts")



@invoices_bp.route("/contract/use/<int:cid>", methods=["POST"])
@login_required
def use_contract_visit(cid):
    with get_db() as conn:
        contract = conn.execute("SELECT * FROM maintenance_contracts WHERE id=?", (cid,)).fetchone()
        if contract and contract['used_visits'] < contract['total_visits']:
            conn.execute("UPDATE maintenance_contracts SET used_visits = used_visits + 1 WHERE id=?", (cid,))
            if contract['used_visits'] + 1 >= contract['total_visits']:
                conn.execute("UPDATE maintenance_contracts SET status='completed' WHERE id=?", (cid,))
            conn.commit()
            flash("Visite utilisée !", "success")
    return redirect("/contracts")



# ─── 2. Tarification Dynamique Pro ───

@invoices_bp.route("/dynamic_pricing_pro")
@login_required
def dynamic_pricing_pro():
    with get_db() as conn:
        rules = conn.execute("""SELECT dp.*, s.name as service_name FROM dynamic_pricing_rules dp
            LEFT JOIN services s ON dp.service_id=s.id ORDER BY dp.priority DESC, dp.created_at DESC""").fetchall()
        services = conn.execute("SELECT * FROM services ORDER BY name").fetchall()
        flash_sales = conn.execute("SELECT * FROM flash_sales ORDER BY created_at DESC LIMIT 20").fetchall()
    return render_template("dynamic_pricing.html", rules=rules, services=services, flash_sales=flash_sales)



@invoices_bp.route("/dynamic_pricing_pro/add", methods=["POST"])
@login_required
def dynamic_pricing_pro_add():
    with get_db() as conn:
        conn.execute("""INSERT INTO dynamic_pricing_rules
            (service_id, rule_name, rule_type, days_of_week, hours_range, season_start, season_end,
             price_modifier, modifier_type, min_price, max_price, vehicle_types, priority)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (request.form.get("service_id", 0, type=int), request.form["rule_name"],
             request.form["rule_type"], request.form.get("days_of_week", ""),
             request.form.get("hours_range", ""), request.form.get("season_start", ""),
             request.form.get("season_end", ""), request.form.get("price_modifier", 0, type=float),
             request.form.get("modifier_type", "percentage"), request.form.get("min_price", 0, type=float),
             request.form.get("max_price", 0, type=float), request.form.get("vehicle_types", "all"),
             request.form.get("priority", 0, type=int)))
        conn.commit()
    flash("Règle de tarification ajoutée", "success")
    return redirect("/dynamic_pricing_pro")



@invoices_bp.route("/flash_sale/add", methods=["POST"])
@login_required
def flash_sale_add():
    with get_db() as conn:
        conn.execute("""INSERT INTO flash_sales (name, service_ids, discount_pct, start_datetime, end_datetime, max_bookings)
            VALUES (?,?,?,?,?,?)""",
            (request.form["name"], request.form.get("service_ids", ""),
             request.form.get("discount_pct", 0, type=float),
             request.form["start_datetime"], request.form["end_datetime"],
             request.form.get("max_bookings", 0, type=int)))
        conn.commit()
    flash("Vente flash créée !", "success")
    return redirect("/dynamic_pricing")



# ─── Phase 16: Operational Mastery & Smart Automation ───

# ── 1. Flash Sales Manager ──
@invoices_bp.route('/flash_sales_manager')
@login_required
def flash_sales_manager():
    with get_db() as conn:
        sales = conn.execute("SELECT * FROM flash_sales ORDER BY created_at DESC").fetchall()
        services = conn.execute("SELECT id, name FROM services ORDER BY name").fetchall()
    now = datetime.now().strftime('%Y-%m-%dT%H:%M')
    return render_template('flash_sales_manager.html', sales=sales, services=services, now=now)



@invoices_bp.route('/flash_sale/edit/<int:sale_id>', methods=['POST'])
@login_required
def flash_sale_edit(sale_id):
    with get_db() as conn:
        conn.execute("""UPDATE flash_sales SET name=?, service_ids=?, discount_pct=?,
            start_datetime=?, end_datetime=?, max_bookings=?, description=?, banner_color=?
            WHERE id=?""",
            (request.form['name'], request.form.get('service_ids', ''),
             float(request.form.get('discount_pct', 0)),
             request.form['start_datetime'], request.form['end_datetime'],
             int(request.form.get('max_bookings', 0)),
             request.form.get('description', ''), request.form.get('banner_color', '#ff6b35'),
             sale_id))
        conn.commit()
    flash("Vente flash mise à jour", "success")
    return redirect("/flash_sales_manager")



@invoices_bp.route('/flash_sale/toggle/<int:sale_id>', methods=["POST"])
@login_required
def flash_sale_toggle(sale_id):
    with get_db() as conn:
        sale = conn.execute("SELECT is_active FROM flash_sales WHERE id=?", (sale_id,)).fetchone()
        if sale:
            conn.execute("UPDATE flash_sales SET is_active=? WHERE id=?", (1 - sale['is_active'], sale_id))
            conn.commit()
    flash("Statut mis à jour", "success")
    return redirect("/flash_sales_manager")



@invoices_bp.route('/flash_sale/delete/<int:sale_id>', methods=["POST"])
@login_required
def flash_sale_delete(sale_id):
    with get_db() as conn:
        conn.execute("DELETE FROM flash_sales WHERE id=?", (sale_id,))
        conn.commit()
    flash("Vente flash supprimée", "success")
    return redirect("/flash_sales_manager")

# ── 2. Revenue Heatmap ──
@invoices_bp.route('/revenue_heatmap')
@login_required
def revenue_heatmap():
    year = request.args.get('year', datetime.now().year, type=int)
    with get_db() as conn:
        daily_data = conn.execute("""
            SELECT created_at as date, SUM(amount) as revenue, COUNT(*) as count
            FROM invoices WHERE created_at LIKE ? AND status != 'cancelled'
            GROUP BY created_at ORDER BY created_at
        """, (f"{year}%",)).fetchall()
        monthly_summary = conn.execute("""
            SELECT strftime('%m', created_at) as month, SUM(amount) as revenue,
                   COUNT(*) as invoices, AVG(amount) as avg_ticket
            FROM invoices WHERE strftime('%Y', created_at) = ? AND status != 'cancelled'
            GROUP BY strftime('%m', created_at) ORDER BY month
        """, (str(year),)).fetchall()
        best_day = conn.execute("""
            SELECT created_at as date, SUM(amount) as revenue FROM invoices
            WHERE strftime('%Y', created_at) = ? AND status != 'cancelled'
            GROUP BY created_at ORDER BY revenue DESC LIMIT 1
        """, (str(year),)).fetchone()
        total_year = conn.execute("""
            SELECT COALESCE(SUM(amount), 0) FROM invoices
            WHERE strftime('%Y', created_at) = ? AND status != 'cancelled'
        """, (str(year),)).fetchone()[0]
    heatmap_data = {}
    for d in daily_data:
        heatmap_data[d['date']] = {'revenue': d['revenue'], 'count': d['count']}
    return render_template('revenue_heatmap.html', year=year, heatmap_data=heatmap_data,
                          monthly_summary=monthly_summary, best_day=best_day,
                          total_year=total_year)

# ── 3. Commission Tracker ──
@invoices_bp.route('/commission_tracker')
@login_required
def commission_tracker():
    month = request.args.get('month', datetime.now().strftime('%Y-%m'))
    with get_db() as conn:
        employees = conn.execute("SELECT id, full_name, commission_rate FROM users WHERE role != 'admin' ORDER BY full_name").fetchall()
        commissions = conn.execute("""
            SELECT cl.*, u.full_name as emp_name FROM commission_log cl
            LEFT JOIN users u ON cl.employee_id = u.id
            WHERE cl.month = ? ORDER BY cl.created_at DESC
        """, (month,)).fetchall()
        summary = conn.execute("""
            SELECT employee_id, employee_name,
                   SUM(invoice_total) as total_revenue,
                   SUM(commission_amount) as total_commission,
                   COUNT(*) as services_count,
                   SUM(CASE WHEN status='paid' THEN commission_amount ELSE 0 END) as paid,
                   SUM(CASE WHEN status='pending' THEN commission_amount ELSE 0 END) as pending
            FROM commission_log WHERE month = ?
            GROUP BY employee_id ORDER BY total_commission DESC
        """, (month,)).fetchall()
    return render_template('commission_tracker.html', employees=employees, commissions=commissions,
                          summary=summary, month=month)




# ─── 18.3 Quick POS Mode ────────────────────────────────────────────────────

@invoices_bp.route("/pos")
@login_required
def pos_view():
    with get_db() as conn:
        services = conn.execute("SELECT id, name, price FROM services WHERE active=1 ORDER BY name").fetchall()
        customers = conn.execute("SELECT id, name, phone FROM customers ORDER BY name").fetchall()
        cars = conn.execute("SELECT id, plate, brand, model, customer_id FROM cars ORDER BY plate").fetchall()
    return render_template("pos.html", services=services, customers=customers, cars=cars)



@invoices_bp.route("/pos/checkout", methods=["POST"])
@login_required
def pos_checkout():
    car_id = request.form.get("car_id", 0, type=int)
    service_name = request.form.get("service", "")
    amount = request.form.get("amount", 0, type=float)
    payment_method = request.form.get("payment_method", "cash")
    payment_method2 = request.form.get("payment_method2", "")
    amount1 = request.form.get("amount1", 0, type=float)
    amount2 = request.form.get("amount2", 0, type=float)
    
    if not car_id or not service_name or amount <= 0:
        flash("Données manquantes", "danger")
        return redirect("/pos")
    
    today = str(date.today())
    now = datetime.now().strftime("%H:%M")
    
    with get_db() as conn:
        cursor = conn.execute("""INSERT INTO appointments (car_id, date, time, service, status)
            VALUES (?,?,?,?,'completed')""", (car_id, today, now, service_name))
        appt_id = cursor.lastrowid
        
        if payment_method2 and amount1 > 0 and amount2 > 0:
            pm = f"{payment_method}/{payment_method2}"
            notes = f"Split: {amount1} DT ({payment_method}) + {amount2} DT ({payment_method2})"
            conn.execute("""INSERT INTO invoices (appointment_id, amount, status, payment_method, paid_amount, created_at)
                VALUES (?,?,'paid',?,?,?)""", (appt_id, amount, pm, amount, today))
        else:
            conn.execute("""INSERT INTO invoices (appointment_id, amount, status, payment_method, paid_amount, created_at)
                VALUES (?,?,'paid',?,?,?)""", (appt_id, amount, payment_method, amount, today))
        
        appt_data = conn.execute("""SELECT a.service, car.brand, car.model FROM appointments a
            JOIN cars car ON a.car_id=car.id WHERE a.id=?""", (appt_id,)).fetchone()
        if appt_data:
            svc_name = appt_data['service'].split(' - ')[0].strip()
            links = conn.execute("SELECT inventory_id, quantity_used FROM service_inventory WHERE service_name=?",
                (svc_name,)).fetchall()
            for link in links:
                conn.execute("UPDATE inventory SET quantity=MAX(0,quantity-?), updated_at=CURRENT_TIMESTAMP WHERE id=?",
                    (link[1], link[0]))
        conn.commit()
    
    log_activity('POS Sale', f'{service_name} - {amount} DT ({payment_method})')
    flash(f"✅ Vente enregistrée : {amount} DT", "success")
    return redirect("/pos")





# ─── 18.5 Split Payment Support ─────────────────────────────────────────────
# (Integrated into POS above + modify pay_invoice to support split)

@invoices_bp.route("/pay_invoice_split/<int:invoice_id>", methods=["POST"])
@login_required
def pay_invoice_split(invoice_id):
    method1 = request.form.get("payment_method1", "cash")
    method2 = request.form.get("payment_method2", "card")
    amount1 = request.form.get("amount1", 0, type=float)
    amount2 = request.form.get("amount2", 0, type=float)
    
    with get_db() as conn:
        inv = conn.execute("SELECT amount, COALESCE(paid_amount,0) as paid FROM invoices WHERE id=?", (invoice_id,)).fetchone()
        if not inv:
            flash("Facture introuvable", "danger")
            return redirect("/invoices")
        
        total_pay = amount1 + amount2
        already_paid = inv['paid']
        new_paid = already_paid + total_pay
        pm = f"{method1}/{method2}"
        
        if new_paid >= inv['amount']:
            conn.execute("UPDATE invoices SET status='paid', payment_method=?, paid_amount=? WHERE id=?",
                (pm, inv['amount'], invoice_id))
        else:
            conn.execute("UPDATE invoices SET status='partial', payment_method=?, paid_amount=? WHERE id=?",
                (pm, new_paid, invoice_id))
        conn.commit()
    
    log_activity('Split Payment', f'Invoice #{invoice_id}: {amount1} ({method1}) + {amount2} ({method2})')
    flash(f"Paiement divisé enregistré ✅", "success")
    return redirect("/invoices")





# ─── 18.9 Professional PDF Quote ────────────────────────────────────────────

@invoices_bp.route("/quote_pdf/<int:quote_id>")
@login_required
def quote_pdf(quote_id):
    with get_db() as conn:
        quote = conn.execute("SELECT * FROM quotes WHERE id=?", (quote_id,)).fetchone()
        if not quote:
            flash("Devis introuvable", "danger")
            return redirect("/quotes")
        shop = get_all_settings()
    return render_template("quote_pdf.html", quote=quote, shop=shop)



@invoices_bp.route("/quote_whatsapp/<int:quote_id>")
@login_required
def quote_whatsapp(quote_id):
    with get_db() as conn:
        quote = conn.execute("SELECT * FROM quotes WHERE id=?", (quote_id,)).fetchone()
    if not quote:
        flash("Devis introuvable", "danger")
        return redirect("/quotes")
    
    shop = get_all_settings()
    shop_name = shop.get('shop_name', 'AMILCAR')
    price_text = f" - Montant: {quote['price']} DT" if quote['price'] else ""
    msg = f"Bonjour {quote['name']},\n\nVoici votre devis chez {shop_name} :\n📋 Service : {quote['service']}{price_text}\n\nPour confirmer, répondez à ce message ou appelez-nous.\nMerci ! 🙏"
    wa_url = _build_wa_status_url(quote['phone'], msg)
    return redirect(wa_url)



