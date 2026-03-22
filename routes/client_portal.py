"""
AMILCAR — Client Portal (PWA)
Blueprint: portal_bp
Routes: 16
"""
from flask import Blueprint, render_template, request, redirect, url_for, flash, make_response, jsonify, session, send_file
from helpers import login_required, admin_required, client_required, get_db, get_services, get_setting, get_all_settings
from helpers import allowed_file, safe_page, log_activity, build_wa_url, check_booking_rate_limit, STATUS_MESSAGES, UPLOAD_FOLDER, MAX_FILE_SIZE, MAX_FILES, PER_PAGE, paginate_query
from database.db import get_db
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, date, timedelta
import os, re, uuid, io, secrets, hashlib
import time as time_module
import sqlite3

# ─── Client Login Rate Limiting ───
CLIENT_LOGIN_MAX_ATTEMPTS = 5
CLIENT_LOGIN_LOCKOUT_SECONDS = 900  # 15 minutes

def _check_client_rate_limit(phone, ip):
    """Returns (is_blocked, remaining_seconds). Checks DB-based rate limiting."""
    with get_db() as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM client_login_attempts WHERE (phone=? OR ip_address=?) AND attempted_at > datetime('now', ? || ' seconds') AND success=0",
            (phone, ip, str(-CLIENT_LOGIN_LOCKOUT_SECONDS))
        ).fetchone()[0]
        if count >= CLIENT_LOGIN_MAX_ATTEMPTS:
            return True, CLIENT_LOGIN_LOCKOUT_SECONDS
    return False, 0

def _record_login_attempt(phone, ip, success=False):
    """Record a client login attempt."""
    with get_db() as conn:
        conn.execute(
            "INSERT INTO client_login_attempts (phone, ip_address, attempted_at, success) VALUES (?,?,datetime('now'),?)",
            (phone, ip, 1 if success else 0)
        )
        conn.commit()
        # Cleanup old entries (older than 24h)
        conn.execute("DELETE FROM client_login_attempts WHERE attempted_at < datetime('now', '-1 day')")
        conn.commit()

def _generate_otp():
    """Generate a secure 4-digit OTP."""
    return f"{secrets.randbelow(10000):04d}"

def _hash_otp(otp):
    """Hash OTP for secure storage."""
    return hashlib.sha256(otp.encode()).hexdigest()

portal_bp = Blueprint("portal_bp", __name__)


@portal_bp.route("/client/dashboard")
def customer_dashboard():
    client_id = session.get('client_id')
    if not client_id:
        return redirect("/client")
    with get_db() as conn:
        customer = conn.execute("SELECT id, name, phone, email FROM customers WHERE id = ?", (client_id,)).fetchone()
        if not customer:
            session.pop('client_id', None)
            return redirect("/client")
        cars = conn.execute("SELECT id, brand, model, plate FROM cars WHERE customer_id = ?", (client_id,)).fetchall()
        car_ids = [c[0] for c in cars]
        appointments = []
        invoices_data = []
        if car_ids:
            appointments = conn.execute(
                "SELECT a.id, a.date, a.service, a.status, ca.brand||' '||ca.model, COALESCE(a.time,'') "
                "FROM appointments a JOIN cars ca ON a.car_id=ca.id "
                "WHERE ca.customer_id = ? ORDER BY a.date DESC LIMIT 20", (client_id,)).fetchall()
            invoices_data = conn.execute(
                "SELECT i.id, i.amount, i.status, a.date, a.service "
                "FROM invoices i JOIN appointments a ON i.appointment_id=a.id "
                "JOIN cars ca ON a.car_id=ca.id WHERE ca.customer_id = ? ORDER BY a.date DESC LIMIT 20", (client_id,)).fetchall()
        rewards = conn.execute(
            "SELECT points, total_earned, tier FROM reward_points WHERE customer_id = ?", (client_id,)).fetchone()
        total_spent = conn.execute(
            "SELECT COALESCE(SUM(i.amount),0) FROM invoices i JOIN appointments a ON i.appointment_id=a.id "
            "JOIN cars ca ON a.car_id=ca.id WHERE ca.customer_id=? AND i.status='paid'", (client_id,)).fetchone()[0]
    return render_template("customer_app.html",
        customer=customer, cars=cars, appointments=appointments,
        invoices=invoices_data, rewards=rewards, total_spent=total_spent)



@portal_bp.route("/client/request_appointment", methods=["POST"])
def customer_request_appointment():
    client_id = session.get('client_id')
    if not client_id:
        return redirect("/client")
    if check_booking_rate_limit():
        flash("Trop de demandes. Réessayez dans quelques minutes.", "error")
        return redirect("/client/dashboard")
    car_id = request.form.get("car_id")
    date_val = request.form.get("date", "")
    service = request.form.get("service", "")
    time_val = request.form.get("time", "")
    if not car_id or not date_val or not service:
        flash("Tous les champs sont requis", "error")
        return redirect("/client/dashboard")
    # Verify car belongs to customer
    with get_db() as conn:
        car = conn.execute("SELECT id FROM cars WHERE id = ? AND customer_id = ?", (car_id, client_id)).fetchone()
        if not car:
            flash("Véhicule invalide", "error")
            return redirect("/client/dashboard")
        conn.execute("INSERT INTO appointments (car_id, date, service, status, time) VALUES (?,?,?,?,?)",
            (car_id, date_val, service, 'pending', time_val))
        conn.commit()
    flash("Demande de rendez-vous envoyée !", "success")
    return redirect("/client/dashboard")



# ─── 4. Portail Client 2.0 ───

@portal_bp.route("/client_portal/<token>")
def client_portal(token):
    with get_db() as conn:
        customer = conn.execute("SELECT * FROM customers WHERE portal_token=?", (token,)).fetchone()
        if not customer:
            return "Lien invalide", 404
        cars = conn.execute("SELECT * FROM cars WHERE customer_id=?", (customer['id'],)).fetchall()
        appointments = conn.execute("""SELECT a.*, c.plate, c.brand, c.model FROM appointments a 
            JOIN cars c ON a.car_id=c.id WHERE c.customer_id=? 
            ORDER BY a.date DESC LIMIT 20""", (customer['id'],)).fetchall()
        invoices = conn.execute("""SELECT i.*, a.service FROM invoices i 
            JOIN appointments a ON i.appointment_id=a.id 
            WHERE a.car_id IN (SELECT id FROM cars WHERE customer_id=?)
            ORDER BY i.date DESC LIMIT 20""", (customer['id'],)).fetchall()
        contracts = conn.execute("""SELECT * FROM maintenance_contracts 
            WHERE customer_id=? ORDER BY created_at DESC""", (customer['id'],)).fetchall()
        docs = conn.execute("""SELECT vd.*, c.plate FROM vehicle_documents vd 
            JOIN cars c ON vd.car_id=c.id WHERE c.customer_id=? 
            ORDER BY vd.created_at DESC""", (customer['id'],)).fetchall()
        vip = conn.execute("SELECT * FROM vip_levels WHERE name=?", (customer['vip_level'] or '',)).fetchone()
    return render_template("client_portal.html", customer=customer, cars=cars, 
                          appointments=appointments, invoices=invoices, contracts=contracts,
                          docs=docs, vip=vip, token=token)



@portal_bp.route("/client_portal/<token>/book", methods=["POST"])
def client_portal_book(token):
    with get_db() as conn:
        customer = conn.execute("SELECT * FROM customers WHERE portal_token=?", (token,)).fetchone()
        if not customer:
            return "Lien invalide", 404
        car_id = request.form.get("car_id", type=int)
        service = request.form.get("service", "").strip()
        date_val = request.form.get("date", "")
        time_val = request.form.get("time", "")
        notes = request.form.get("notes", "").strip()
        if car_id and service and date_val:
            conn.execute("""INSERT INTO appointments (car_id, service, date, time, status, notes)
                VALUES (?,?,?,?,?,?)""", (car_id, service, date_val, time_val, 'pending', notes))
            conn.commit()
            flash("Rendez-vous demandé avec succès !", "success")
    return redirect(f"/client_portal/{token}")



# ─── 9. Client PWA Espace ───

@portal_bp.route("/client_app")
def client_app():
    """Legacy client app — redirect to new espace-client."""
    return redirect("/espace-client", code=301)



@portal_bp.route("/client_app/dashboard")
def client_app_dashboard():
    """Legacy client app dashboard — redirect to new espace-client."""
    if session.get('client_id'):
        return redirect("/espace-client/accueil", code=301)
    return redirect("/espace-client", code=301)



@portal_bp.route("/espace-client")
def espace_client():
    if session.get('client_id'):
        return redirect('/espace-client/accueil')
    with get_db() as conn:
        shop = dict(conn.execute("SELECT key, value FROM settings").fetchall() or [])
    return render_template("client_login.html", shop=shop)



@portal_bp.route("/espace-client/connexion", methods=["POST"])
def espace_client_login():
    phone = request.form.get("phone", "").strip()
    if not phone:
        flash("Veuillez entrer votre numéro", "danger")
        return redirect("/espace-client")
    # Normalize phone
    phone = re.sub(r'[^\d+]', '', phone)
    ip = request.remote_addr
    # Rate limiting check
    blocked, remaining = _check_client_rate_limit(phone, ip)
    if blocked:
        mins = remaining // 60 + 1
        flash(f"Trop de tentatives. Réessayez dans {mins} minutes.", "danger")
        return redirect("/espace-client")
    with get_db() as conn:
        customer = conn.execute("SELECT * FROM customers WHERE phone=?", (phone,)).fetchone()
        if not customer:
            _record_login_attempt(phone, ip, success=False)
            flash("Numéro non trouvé. Contactez-nous pour créer votre compte.", "danger")
            return redirect("/espace-client")
        # Generate OTP
        otp = _generate_otp()
        otp_hash = _hash_otp(otp)
        expires = (datetime.now() + timedelta(minutes=5)).strftime('%Y-%m-%d %H:%M:%S')
        # Clear old OTPs for this phone
        conn.execute("DELETE FROM client_otp WHERE phone=?", (phone,))
        conn.execute(
            "INSERT INTO client_otp (phone, otp_code, ip_address, expires_at) VALUES (?,?,?,?)",
            (phone, otp_hash, ip, expires)
        )
        conn.commit()
    # Store phone in session for OTP verification
    session['otp_phone'] = phone
    session['otp_customer_name'] = customer['name']
    # In production, send OTP via SMS/WhatsApp. For now, flash it.
    # TODO: Integrate with CallMeBot WhatsApp API or SMS gateway
    flash(f"Code de vérification envoyé: {otp}", "info")
    return redirect("/espace-client/verify-otp")


@portal_bp.route("/espace-client/verify-otp", methods=["GET", "POST"])
def espace_client_verify_otp():
    phone = session.get('otp_phone')
    if not phone:
        return redirect("/espace-client")
    with get_db() as conn:
        shop = dict(conn.execute("SELECT key, value FROM settings").fetchall() or [])
    if request.method == "POST":
        otp_input = request.form.get("otp", "").strip()
        ip = request.remote_addr
        if not otp_input or len(otp_input) != 4:
            flash("Veuillez entrer un code à 4 chiffres", "danger")
            return render_template("client_otp.html", shop=shop, phone=phone)
        otp_hash = _hash_otp(otp_input)
        with get_db() as conn:
            record = conn.execute(
                "SELECT * FROM client_otp WHERE phone=? AND otp_code=? AND verified=0 AND expires_at>?",
                (phone, otp_hash, datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
            ).fetchone()
            if not record:
                _record_login_attempt(phone, ip, success=False)
                flash("Code incorrect ou expiré", "danger")
                return render_template("client_otp.html", shop=shop, phone=phone)
            # OTP valid — mark verified and log in
            conn.execute("UPDATE client_otp SET verified=1 WHERE id=?", (record['id'],))
            conn.commit()
            _record_login_attempt(phone, ip, success=True)
            customer = conn.execute("SELECT * FROM customers WHERE phone=?", (phone,)).fetchone()
            if not customer:
                flash("Erreur client", "danger")
                return redirect("/espace-client")
            session.pop('otp_phone', None)
            session.pop('otp_customer_name', None)
            session['client_id'] = customer['id']
            session['client_name'] = customer['name']
            session['client_phone'] = customer['phone']
        redirect_to = session.pop('otp_redirect', '/espace-client/accueil')
        return redirect(redirect_to)
    return render_template("client_otp.html", shop=shop, phone=phone)


@portal_bp.route("/espace-client/resend-otp", methods=["POST"])
def espace_client_resend_otp():
    phone = session.get('otp_phone')
    if not phone:
        return redirect("/espace-client")
    ip = request.remote_addr
    blocked, remaining = _check_client_rate_limit(phone, ip)
    if blocked:
        mins = remaining // 60 + 1
        flash(f"Trop de tentatives. Réessayez dans {mins} minutes.", "danger")
        return redirect("/espace-client/verify-otp")
    with get_db() as conn:
        customer = conn.execute("SELECT * FROM customers WHERE phone=?", (phone,)).fetchone()
        if not customer:
            return redirect("/espace-client")
        otp = _generate_otp()
        otp_hash = _hash_otp(otp)
        expires = (datetime.now() + timedelta(minutes=5)).strftime('%Y-%m-%d %H:%M:%S')
        conn.execute("DELETE FROM client_otp WHERE phone=?", (phone,))
        conn.execute(
            "INSERT INTO client_otp (phone, otp_code, ip_address, expires_at) VALUES (?,?,?,?)",
            (phone, otp_hash, ip, expires)
        )
        conn.commit()
    flash(f"Nouveau code envoyé: {otp}", "info")
    return redirect("/espace-client/verify-otp")



@portal_bp.route("/espace-client/deconnexion")
def espace_client_logout():
    session.pop('client_id', None)
    session.pop('client_name', None)
    session.pop('client_phone', None)
    return redirect("/espace-client")



@portal_bp.route("/espace-client/accueil")
@client_required
def espace_client_accueil():
    client_id = session['client_id']
    with get_db() as conn:
        customer = conn.execute("SELECT * FROM customers WHERE id=?", (client_id,)).fetchone()
        if not customer:
            session.pop('client_id', None)
            return redirect('/espace-client')
        cars = conn.execute("SELECT * FROM cars WHERE customer_id=?", (client_id,)).fetchall()
        car_ids = [c['id'] for c in cars]
        if car_ids:
            placeholders = ','.join('?' * len(car_ids))
            appointments = conn.execute(f"""SELECT a.*, ca.brand, ca.model, ca.plate
                FROM appointments a LEFT JOIN cars ca ON a.car_id=ca.id
                WHERE a.car_id IN ({placeholders}) ORDER BY a.date DESC LIMIT 10""", car_ids).fetchall()
            invoices_unpaid = conn.execute(f"""SELECT COUNT(*) FROM invoices i
                JOIN appointments a ON i.appointment_id=a.id
                WHERE a.car_id IN ({placeholders}) AND i.status IN ('unpaid','partial')""", car_ids).fetchone()[0]
        else:
            appointments = []
            invoices_unpaid = 0
        active_count = sum(1 for a in appointments if a['status'] in ('pending', 'confirmed', 'in_progress'))
        completed_count = sum(1 for a in appointments if a['status'] in ('Terminé', 'completed', 'done'))
        balance = customer['wallet_balance'] or 0 if customer['wallet_balance'] else 0
        points = customer['loyalty_points_total'] or 0 if customer['loyalty_points_total'] else 0
        loyalty = customer['loyalty_level'] or 'bronze' if customer['loyalty_level'] else 'bronze'
        notif_count = conn.execute("SELECT COUNT(*) FROM client_notifications WHERE customer_id=? AND is_read=0", (client_id,)).fetchone()[0]
        shop = dict(conn.execute("SELECT key, value FROM settings").fetchall() or [])
    return render_template("client_accueil.html", customer=customer, cars=cars,
        appointments=appointments, active_count=active_count, completed_count=completed_count,
        balance=balance, points=points, loyalty=loyalty, invoices_unpaid=invoices_unpaid,
        notif_count=notif_count, shop=shop)



@portal_bp.route("/espace-client/vehicules")
@client_required
def espace_client_vehicules():
    client_id = session['client_id']
    today_str = date.today().isoformat()
    with get_db() as conn:
        cars = conn.execute("SELECT * FROM cars WHERE customer_id=?", (client_id,)).fetchall()
        car_data = []
        for car in cars:
            appts = conn.execute("""SELECT date, service, status
                FROM appointments WHERE car_id=? ORDER BY date DESC LIMIT 5""", (car['id'],)).fetchall()
            treatments = conn.execute("""SELECT treatment_type, applied_date, warranty_expiry
                FROM treatments WHERE car_id=? ORDER BY applied_date DESC LIMIT 3""", (car['id'],)).fetchall()
            # Next scheduled maintenance
            next_maint = conn.execute("""SELECT date, service FROM appointments
                WHERE car_id=? AND date>=? AND status IN ('pending','confirmed')
                ORDER BY date ASC LIMIT 1""", (car['id'], today_str)).fetchone()
            car_data.append({
                'car': car, 'appointments': appts, 'treatments': treatments,
                'next_maintenance': next_maint
            })
        shop = dict(conn.execute("SELECT key, value FROM settings").fetchall() or [])
    return render_template("client_vehicules.html", car_data=car_data, shop=shop)



@portal_bp.route("/espace-client/vehicules/ajouter", methods=["POST"])
@client_required
def espace_client_add_car():
    client_id = session['client_id']
    vehicle_type = request.form.get("vehicle_type", "voiture").strip()
    brand = request.form.get("brand", "").strip()
    model = request.form.get("model", "").strip()
    plate = request.form.get("plate", "").strip().upper()
    year = request.form.get("year", "").strip()
    if not brand or not model or not plate:
        flash("Veuillez remplir tous les champs obligatoires", "danger")
        return redirect("/espace-client/vehicules")
    if len(plate) < 3:
        flash("Immatriculation invalide", "danger")
        return redirect("/espace-client/vehicules")
    with get_db() as conn:
        # Check duplicate plate for this customer
        existing = conn.execute("SELECT id FROM cars WHERE plate=? AND customer_id=?", (plate, client_id)).fetchone()
        if existing:
            flash("Ce véhicule est déjà enregistré", "danger")
            return redirect("/espace-client/vehicules")
        conn.execute(
            "INSERT INTO cars (customer_id, brand, model, plate, vehicle_type, year) VALUES (?,?,?,?,?,?)",
            (client_id, brand, model, plate, vehicle_type, year or None))
        conn.commit()
    flash(f"Véhicule {brand} {model} ajouté avec succès !", "success")
    return redirect("/espace-client/vehicules")



@portal_bp.route("/espace-client/rendez-vous")
@client_required
def espace_client_rdv():
    client_id = session['client_id']
    page = safe_page(request.args.get('page', 1))
    with get_db() as conn:
        car_ids = [c['id'] for c in conn.execute("SELECT id FROM cars WHERE customer_id=?", (client_id,)).fetchall()]
        if car_ids:
            placeholders = ','.join('?' * len(car_ids))
            query = f"""SELECT a.*, ca.brand, ca.model, ca.plate
                FROM appointments a LEFT JOIN cars ca ON a.car_id=ca.id
                WHERE a.car_id IN ({placeholders}) ORDER BY a.date DESC"""
            appointments, total, total_pages, page = paginate_query(conn, query, tuple(car_ids), page, 10)
            # Check which completed appointments are already rated
            rated_ids = set()
            completed_ids = [a['id'] for a in appointments if a['status'] in ('Terminé', 'completed', 'done')]
            if completed_ids:
                ph = ','.join('?' * len(completed_ids))
                rated_rows = conn.execute(f"SELECT appointment_id FROM ratings WHERE appointment_id IN ({ph})", completed_ids).fetchall()
                rated_ids = {r['appointment_id'] for r in rated_rows}
        else:
            appointments, total, total_pages = [], 0, 1
            rated_ids = set()
        shop = dict(conn.execute("SELECT key, value FROM settings").fetchall() or [])
    return render_template("client_rdv.html", appointments=appointments, shop=shop,
        page=page, total_pages=total_pages, total=total, rated_ids=rated_ids)



@portal_bp.route("/espace-client/reserver", methods=["GET", "POST"])
@client_required
def espace_client_reserver():
    client_id = session['client_id']
    with get_db() as conn:
        cars = conn.execute("SELECT * FROM cars WHERE customer_id=?", (client_id,)).fetchall()
        services = conn.execute("SELECT * FROM services WHERE active=1 ORDER BY name").fetchall()
        shop = dict(conn.execute("SELECT key, value FROM settings").fetchall() or [])
        if request.method == "POST":
            car_id = request.form.get("car_id", type=int)
            service_type = request.form.get("service_type", "").strip()
            date = request.form.get("date", "").strip()
            time_slot = request.form.get("time", "").strip()
            notes = request.form.get("notes", "").strip()[:500]
            if not all([car_id, service_type, date]):
                flash("Veuillez remplir tous les champs obligatoires", "danger")
            else:
                # Verify car belongs to client
                car_check = conn.execute("SELECT id FROM cars WHERE id=? AND customer_id=?", (car_id, client_id)).fetchone()
                if not car_check:
                    flash("Véhicule non trouvé", "danger")
                else:
                    customer = conn.execute("SELECT name, phone FROM customers WHERE id=?", (client_id,)).fetchone()
                    conn.execute("""INSERT INTO appointments (car_id, date, time, service,
                        status)
                        VALUES (?,?,?,?,'pending')""",
                        (car_id, date, time_slot, service_type))
                    conn.commit()
                    flash("Rendez-vous demandé avec succès! Nous vous confirmerons bientôt.", "success")
                    return redirect("/espace-client/rendez-vous")
    return render_template("client_reserver.html", cars=cars, services=services, shop=shop)



@portal_bp.route("/espace-client/factures")
@client_required
def espace_client_factures():
    client_id = session['client_id']
    page = safe_page(request.args.get('page', 1))
    with get_db() as conn:
        car_ids = [c['id'] for c in conn.execute("SELECT id FROM cars WHERE customer_id=?", (client_id,)).fetchall()]
        if car_ids:
            placeholders = ','.join('?' * len(car_ids))
            query = f"""SELECT i.*, a.date as appt_date, a.service, ca.brand, ca.model, ca.plate,
                COALESCE(i.payment_method, '') as payment_method
                FROM invoices i
                JOIN appointments a ON i.appointment_id=a.id
                LEFT JOIN cars ca ON a.car_id=ca.id
                WHERE a.car_id IN ({placeholders}) ORDER BY i.created_at DESC"""
            invoices, total, total_pages, page = paginate_query(conn, query, tuple(car_ids), page, 10)
        else:
            invoices, total, total_pages = [], 0, 1
        shop = dict(conn.execute("SELECT key, value FROM settings").fetchall() or [])
    return render_template("client_factures.html", invoices=invoices, shop=shop,
        page=page, total_pages=total_pages, total=total)



@portal_bp.route("/espace-client/facture/<int:invoice_id>/pdf")
@client_required
def espace_client_invoice_pdf(invoice_id):
    """Download invoice as PDF — client must own the vehicle."""
    from xhtml2pdf import pisa
    import base64
    client_id = session['client_id']
    with get_db() as conn:
        # Verify this invoice belongs to the client
        inv = conn.execute(
            "SELECT i.id, i.appointment_id, i.amount, i.status, a.date, a.service, "
            "cu.name, cu.phone, ca.brand, ca.model, ca.plate, COALESCE(i.paid_amount, 0), i.payment_method, "
            "COALESCE(i.discount_type, ''), COALESCE(i.discount_value, 0) "
            "FROM invoices i JOIN appointments a ON i.appointment_id = a.id "
            "JOIN cars ca ON a.car_id = ca.id JOIN customers cu ON ca.customer_id = cu.id "
            "WHERE i.id = ? AND cu.id = ?", (invoice_id, client_id)).fetchone()
        if not inv:
            flash("Facture non trouvée", "danger")
            return redirect("/espace-client/factures")
        settings = get_all_settings()
    now = datetime.now().strftime('%d/%m/%Y %H:%M')
    html = render_template("print_invoice.html", inv=inv, settings=settings, now=now)
    logo_path = os.path.join(os.path.abspath('static'), 'logo.png')
    if os.path.exists(logo_path):
        with open(logo_path, 'rb') as lf:
            logo_b64 = base64.b64encode(lf.read()).decode()
        html = html.replace('/static/logo.png', f'data:image/png;base64,{logo_b64}')
    try:
        pdf_buffer = io.BytesIO()
        pisa.CreatePDF(io.StringIO(html), dest=pdf_buffer)
        pdf_buffer.seek(0)
    except Exception:
        flash("Erreur de génération PDF", "danger")
        return redirect("/espace-client/factures")
    response = make_response(pdf_buffer.read())
    response.headers['Content-Type'] = 'application/pdf'
    response.headers['Content-Disposition'] = f'attachment; filename=facture_{invoice_id}.pdf'
    return response



@portal_bp.route("/espace-client/fidelite")
@client_required
def espace_client_fidelite():
    client_id = session['client_id']
    with get_db() as conn:
        customer = conn.execute("SELECT * FROM customers WHERE id=?", (client_id,)).fetchone()
        points = customer['loyalty_points_total'] or 0 if customer['loyalty_points_total'] else 0
        loyalty = customer['loyalty_level'] or 'bronze' if customer['loyalty_level'] else 'bronze'
        balance = customer['wallet_balance'] or 0 if customer['wallet_balance'] else 0
        wallet = conn.execute("""SELECT * FROM wallet_transactions WHERE customer_id=?
            ORDER BY created_at DESC LIMIT 15""", (client_id,)).fetchall()
        treatments = conn.execute("""SELECT t.*, ca.brand, ca.model FROM treatments t
            LEFT JOIN cars ca ON t.car_id=ca.id WHERE t.customer_id=?
            ORDER BY t.applied_date DESC LIMIT 10""", (client_id,)).fetchall()
        shop = dict(conn.execute("SELECT key, value FROM settings").fetchall() or [])
    return render_template("client_fidelite.html", customer=customer, points=points,
        loyalty=loyalty, balance=balance, wallet=wallet, treatments=treatments, shop=shop)



@portal_bp.route("/espace-client/suivi/<int:appointment_id>")
@client_required
def espace_client_suivi(appointment_id):
    client_id = session['client_id']
    with get_db() as conn:
        appt = conn.execute("""SELECT a.*, ca.brand, ca.model, ca.plate
            FROM appointments a LEFT JOIN cars ca ON a.car_id=ca.id
            WHERE a.id=? AND ca.customer_id=?""", (appointment_id, client_id)).fetchone()
        if not appt:
            flash("Rendez-vous non trouvé", "danger")
            return redirect("/espace-client/rendez-vous")
        photos = conn.execute("""SELECT * FROM vehicle_gallery WHERE appointment_id=?
            ORDER BY photo_type, uploaded_at""", (appointment_id,)).fetchall()
        shop = dict(conn.execute("SELECT key, value FROM settings").fetchall() or [])
    return render_template("client_suivi.html", appt=appt, photos=photos, shop=shop)


# ─── Client Rating ───
@portal_bp.route("/espace-client/noter/<int:appointment_id>", methods=["POST"])
@client_required
def espace_client_noter(appointment_id):
    """Submit a rating for a completed appointment."""
    client_id = session['client_id']
    rating = request.form.get("rating", 0, type=int)
    comment = request.form.get("comment", "").strip()[:500]
    if rating < 1 or rating > 5:
        flash("Note invalide", "danger")
        return redirect("/espace-client/rendez-vous")
    with get_db() as conn:
        # Verify appointment belongs to client and is completed
        appt = conn.execute("""SELECT a.id, a.status FROM appointments a
            JOIN cars ca ON a.car_id=ca.id
            WHERE a.id=? AND ca.customer_id=?""", (appointment_id, client_id)).fetchone()
        if not appt or appt['status'] not in ('Terminé', 'completed', 'done'):
            flash("Rendez-vous non trouvé ou non terminé", "danger")
            return redirect("/espace-client/rendez-vous")
        # Check if already rated
        existing = conn.execute("SELECT id FROM ratings WHERE appointment_id=?", (appointment_id,)).fetchone()
        if existing:
            flash("Vous avez déjà noté ce rendez-vous", "info")
            return redirect("/espace-client/rendez-vous")
        conn.execute("INSERT INTO ratings (appointment_id, customer_id, rating, comment) VALUES (?,?,?,?)",
            (appointment_id, client_id, rating, comment))
        conn.commit()
    flash("Merci pour votre avis ! ⭐", "success")
    return redirect("/espace-client/rendez-vous")


# ─── Client Notifications ───
@portal_bp.route("/espace-client/notifications")
@client_required
def espace_client_notifications():
    client_id = session['client_id']
    with get_db() as conn:
        notifs = conn.execute("""SELECT * FROM client_notifications
            WHERE customer_id=? ORDER BY created_at DESC LIMIT 30""", (client_id,)).fetchall()
        # Mark all as read
        conn.execute("UPDATE client_notifications SET is_read=1 WHERE customer_id=? AND is_read=0", (client_id,))
        conn.commit()
        shop = dict(conn.execute("SELECT key, value FROM settings").fetchall() or [])
    return render_template("client_notifications.html", notifs=notifs, shop=shop)


# ─── Online Payment ───
@portal_bp.route("/espace-client/payer/<int:invoice_id>", methods=["GET", "POST"])
@client_required
def espace_client_payer(invoice_id):
    """Initiate payment for an unpaid invoice."""
    client_id = session['client_id']
    with get_db() as conn:
        inv = conn.execute("""SELECT i.id, i.amount, i.status, i.paid_amount, a.service, a.date,
            ca.brand, ca.model, ca.plate, cu.name, cu.phone
            FROM invoices i JOIN appointments a ON i.appointment_id=a.id
            JOIN cars ca ON a.car_id=ca.id JOIN customers cu ON ca.customer_id=cu.id
            WHERE i.id=? AND cu.id=?""", (invoice_id, client_id)).fetchone()
        if not inv:
            flash("Facture non trouvée", "danger")
            return redirect("/espace-client/factures")
        if inv['status'] == 'paid':
            flash("Cette facture est déjà payée", "info")
            return redirect("/espace-client/factures")
        remaining = (inv['amount'] or 0) - (inv['paid_amount'] or 0)
        shop = dict(conn.execute("SELECT key, value FROM settings").fetchall() or [])
        flouci_key = shop.get('flouci_app_token', '')
        flouci_secret = shop.get('flouci_app_secret', '')
    if request.method == "POST":
        if not flouci_key or not flouci_secret:
            flash("Le paiement en ligne n'est pas encore configuré. Contactez-nous.", "danger")
            return redirect(f"/espace-client/payer/{invoice_id}")
        # Initiate Flouci payment
        import urllib.request, json as json_mod
        payload = json_mod.dumps({
            "app_token": flouci_key,
            "app_secret": flouci_secret,
            "amount": int(remaining * 1000),  # Flouci uses millimes
            "accept_url": request.host_url.rstrip('/') + f"/espace-client/paiement-ok/{invoice_id}",
            "cancel_url": request.host_url.rstrip('/') + f"/espace-client/payer/{invoice_id}",
            "decline_url": request.host_url.rstrip('/') + f"/espace-client/payer/{invoice_id}",
            "session_timeout_secs": 1200,
        }).encode()
        req = urllib.request.Request(
            "https://developers.flouci.com/api/generate_payment",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                result = json_mod.loads(resp.read())
            if result.get("result", {}).get("success"):
                payment_id = result["result"].get("payment_id", "")
                pay_url = result["result"].get("link", "")
                if pay_url:
                    # Store payment_id in session for verification
                    session[f'flouci_pay_{invoice_id}'] = payment_id
                    return redirect(pay_url)
            flash("Erreur de paiement. Réessayez.", "danger")
        except Exception:
            flash("Service de paiement indisponible. Réessayez plus tard.", "danger")
        return redirect(f"/espace-client/payer/{invoice_id}")
    return render_template("client_payer.html", inv=inv, remaining=remaining, shop=shop,
        payment_enabled=bool(flouci_key and flouci_secret))


@portal_bp.route("/espace-client/paiement-ok/<int:invoice_id>")
@client_required
def espace_client_payment_success(invoice_id):
    """Flouci payment success callback — verify and record payment."""
    client_id = session['client_id']
    payment_id = request.args.get("payment_id", "") or session.pop(f'flouci_pay_{invoice_id}', '')
    if not payment_id:
        flash("Paiement non vérifié", "danger")
        return redirect("/espace-client/factures")
    with get_db() as conn:
        inv = conn.execute("""SELECT i.id, i.amount, i.paid_amount, cu.id as cust_id
            FROM invoices i JOIN appointments a ON i.appointment_id=a.id
            JOIN cars ca ON a.car_id=ca.id JOIN customers cu ON ca.customer_id=cu.id
            WHERE i.id=? AND cu.id=?""", (invoice_id, client_id)).fetchone()
        if not inv:
            flash("Facture non trouvée", "danger")
            return redirect("/espace-client/factures")
        # Verify with Flouci API
        shop = dict(conn.execute("SELECT key, value FROM settings").fetchall() or [])
        flouci_key = shop.get('flouci_app_token', '')
        flouci_secret = shop.get('flouci_app_secret', '')
        verified = False
        if flouci_key and payment_id:
            import urllib.request, json as json_mod
            try:
                req = urllib.request.Request(
                    f"https://developers.flouci.com/api/verify_payment/{payment_id}",
                    headers={"apppublic": flouci_key, "appsecret": flouci_secret},
                )
                with urllib.request.urlopen(req, timeout=10) as resp:
                    result = json_mod.loads(resp.read())
                if result.get("result", {}).get("status") == "SUCCESS":
                    verified = True
            except Exception:
                pass
        if verified:
            remaining = (inv['amount'] or 0) - (inv['paid_amount'] or 0)
            new_paid = (inv['paid_amount'] or 0) + remaining
            new_status = 'paid' if new_paid >= (inv['amount'] or 0) else 'partial'
            conn.execute("UPDATE invoices SET paid_amount=?, status=?, payment_method='Flouci' WHERE id=?",
                (new_paid, new_status, invoice_id))
            conn.commit()
            flash("Paiement effectué avec succès ! ✅", "success")
        else:
            flash("Paiement en cours de vérification. Contactez-nous si nécessaire.", "info")
    return redirect("/espace-client/factures")


# ─── Public Appointment Tracking (no login required) ───
@portal_bp.route("/suivi")
def public_tracking():
    """Public page: track appointment status by ID + phone."""
    result = None
    error = None
    appt_id = request.args.get('id', '').strip()
    phone = request.args.get('phone', '').strip()
    if appt_id and phone:
        try:
            aid = int(appt_id)
        except ValueError:
            error = "Numéro de rendez-vous invalide"
            return render_template("public_tracking.html", result=result, error=error)
        with get_db() as conn:
            row = conn.execute("""
                SELECT a.id, a.date, COALESCE(a.time, ''), a.service, a.status,
                       ca.brand, ca.model, ca.plate, cu.name
                FROM appointments a JOIN cars ca ON a.car_id=ca.id
                JOIN customers cu ON ca.customer_id=cu.id
                WHERE a.id=? AND cu.phone=?
            """, (aid, phone)).fetchone()
            if row:
                # Get photos if any
                photos = conn.execute(
                    "SELECT photo_type, photo_url FROM vehicle_gallery WHERE appointment_id=? ORDER BY uploaded_at", (aid,)).fetchall()
                result = {
                    'id': row[0], 'date': row[1], 'time': row[2], 'service': row[3],
                    'status': row[4], 'brand': row[5], 'model': row[6], 'plate': row[7],
                    'name': row[8], 'photos': photos
                }
            else:
                error = "Aucun rendez-vous trouvé avec ces informations"
    return render_template("public_tracking.html", result=result, error=error)