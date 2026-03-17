"""
AMILCAR — Client Portal (PWA)
Blueprint: portal_bp
Routes: 16
"""
from flask import Blueprint, render_template, request, redirect, url_for, flash, make_response, jsonify, session, send_file
from helpers import login_required, admin_required, client_required, get_db, get_services, get_setting, get_all_settings
from helpers import allowed_file, safe_page, log_activity, build_wa_url, STATUS_MESSAGES, UPLOAD_FOLDER, MAX_FILE_SIZE, MAX_FILES, PER_PAGE
from database.db import get_db
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, date, timedelta
import os, re, uuid, io
import time as time_module
import sqlite3

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
    token = request.args.get("token", "")
    with get_db() as conn:
        shop = conn.execute("SELECT key, value FROM settings").fetchall()
        shop = {s['key']: s['value'] for s in shop}
    return render_template("client_app.html", shop=shop, token=token)



@portal_bp.route("/client_app/dashboard")
def client_app_dashboard():
    client_id = session.get('client_id')
    if not client_id:
        return redirect("/client_app")
    with get_db() as conn:
        customer = conn.execute("SELECT * FROM customers WHERE id=?", (client_id,)).fetchone()
        appointments = conn.execute("""SELECT a.*, ca.brand, ca.model, ca.plate
            FROM appointments a LEFT JOIN cars ca ON a.car_id=ca.id
            WHERE a.customer_id=? ORDER BY a.date DESC LIMIT 10""", (client_id,)).fetchall()
        cars = conn.execute("SELECT * FROM cars WHERE customer_id=?", (client_id,)).fetchall()
        wallet = conn.execute("""SELECT * FROM wallet_transactions WHERE customer_id=?
            ORDER BY created_at DESC LIMIT 10""", (client_id,)).fetchall()
        treatments = conn.execute("""SELECT t.*, ca.brand, ca.model FROM treatments t
            LEFT JOIN cars ca ON t.car_id=ca.id WHERE t.customer_id=?
            ORDER BY t.applied_date DESC LIMIT 5""", (client_id,)).fetchall()
        balance = customer['wallet_balance'] or 0 if customer['wallet_balance'] else 0
        loyalty = customer['loyalty_level'] or 'bronze' if customer['loyalty_level'] else 'bronze'
        points = customer['loyalty_points_total'] or 0 if customer['loyalty_points_total'] else 0
        shop = conn.execute("SELECT key, value FROM settings").fetchall()
        shop = {s['key']: s['value'] for s in shop}
    return render_template("client_app_dashboard.html", customer=customer, appointments=appointments,
        cars=cars, wallet=wallet, treatments=treatments, balance=balance,
        loyalty=loyalty, points=points, shop=shop)



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
    with get_db() as conn:
        customer = conn.execute("SELECT * FROM customers WHERE phone=?", (phone,)).fetchone()
        if not customer:
            flash("Numéro non trouvé. Contactez-nous pour créer votre compte.", "danger")
            return redirect("/espace-client")
        session['client_id'] = customer['id']
        session['client_name'] = customer['name']
        session['client_phone'] = customer['phone']
    return redirect("/espace-client/accueil")



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
        shop = dict(conn.execute("SELECT key, value FROM settings").fetchall() or [])
    return render_template("client_accueil.html", customer=customer, cars=cars,
        appointments=appointments, active_count=active_count, completed_count=completed_count,
        balance=balance, points=points, loyalty=loyalty, invoices_unpaid=invoices_unpaid, shop=shop)



@portal_bp.route("/espace-client/vehicules")
@client_required
def espace_client_vehicules():
    client_id = session['client_id']
    with get_db() as conn:
        cars = conn.execute("SELECT * FROM cars WHERE customer_id=?", (client_id,)).fetchall()
        car_data = []
        for car in cars:
            appts = conn.execute("""SELECT date, service, status
                FROM appointments WHERE car_id=? ORDER BY date DESC LIMIT 5""", (car['id'],)).fetchall()
            treatments = conn.execute("""SELECT treatment_type, applied_date, warranty_expiry
                FROM treatments WHERE car_id=? ORDER BY applied_date DESC LIMIT 3""", (car['id'],)).fetchall()
            car_data.append({'car': car, 'appointments': appts, 'treatments': treatments})
        shop = dict(conn.execute("SELECT key, value FROM settings").fetchall() or [])
    return render_template("client_vehicules.html", car_data=car_data, shop=shop)



@portal_bp.route("/espace-client/rendez-vous")
@client_required
def espace_client_rdv():
    client_id = session['client_id']
    with get_db() as conn:
        car_ids = [c['id'] for c in conn.execute("SELECT id FROM cars WHERE customer_id=?", (client_id,)).fetchall()]
        if car_ids:
            placeholders = ','.join('?' * len(car_ids))
            appointments = conn.execute(f"""SELECT a.*, ca.brand, ca.model, ca.plate
                FROM appointments a LEFT JOIN cars ca ON a.car_id=ca.id
                WHERE a.car_id IN ({placeholders}) ORDER BY a.date DESC""", car_ids).fetchall()
        else:
            appointments = []
        shop = dict(conn.execute("SELECT key, value FROM settings").fetchall() or [])
    return render_template("client_rdv.html", appointments=appointments, shop=shop)



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
    with get_db() as conn:
        car_ids = [c['id'] for c in conn.execute("SELECT id FROM cars WHERE customer_id=?", (client_id,)).fetchall()]
        if car_ids:
            placeholders = ','.join('?' * len(car_ids))
            invoices = conn.execute(f"""SELECT i.*, a.date as appt_date, a.service, ca.brand, ca.model, ca.plate
                FROM invoices i
                JOIN appointments a ON i.appointment_id=a.id
                LEFT JOIN cars ca ON a.car_id=ca.id
                WHERE a.car_id IN ({placeholders}) ORDER BY i.created_at DESC""", car_ids).fetchall()
        else:
            invoices = []
        shop = dict(conn.execute("SELECT key, value FROM settings").fetchall() or [])
    return render_template("client_factures.html", invoices=invoices, shop=shop)



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