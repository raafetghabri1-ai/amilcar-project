"""
AMILCAR — Appointments & Scheduling
Blueprint: appointments_bp
Routes: 32
"""
from flask import Blueprint, render_template, request, redirect, url_for, flash, make_response, jsonify, session, send_file
from helpers import login_required, admin_required, client_required, get_db, get_services, get_setting, get_all_settings
from helpers import allowed_file, safe_page, log_activity, build_wa_url, STATUS_MESSAGES, UPLOAD_FOLDER, MAX_FILE_SIZE, MAX_FILES, PER_PAGE, csrf, cache
from database.db import get_db
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, date, timedelta
import os, re, uuid, io
import time as time_module
import sqlite3

appointments_bp = Blueprint("appointments_bp", __name__)


@appointments_bp.route('/appointments')
@login_required
def appointments():
    page = safe_page(request.args.get('page', 1, type=int))
    status_filter = request.args.get('status', '')
    date_from = request.args.get('date_from', '')
    date_to = request.args.get('date_to', '')
    with get_db() as conn:
        base_q = ("FROM appointments a JOIN cars ca ON a.car_id = ca.id "
                  "JOIN customers cu ON ca.customer_id = cu.id")
        conditions = ["COALESCE(a.is_deleted,0)=0"]
        params = []
        if status_filter:
            conditions.append("a.status = ?")
            params.append(status_filter)
        if date_from:
            conditions.append("a.date >= ?")
            params.append(date_from)
        if date_to:
            conditions.append("a.date <= ?")
            params.append(date_to)
        where = " WHERE " + " AND ".join(conditions) if conditions else ""
        total = conn.execute(f"SELECT COUNT(*) {base_q}{where}", params).fetchone()[0]
        all_appointments = conn.execute(
            f"SELECT a.id, cu.name, ca.brand, ca.model, a.date, a.service, a.status, COALESCE(a.assigned_to, ''), COALESCE(a.time, '') "
            f"{base_q}{where} ORDER BY a.id DESC LIMIT ? OFFSET ?",
            params + [PER_PAGE, (page - 1) * PER_PAGE]
        ).fetchall()
    total_pages = (total + PER_PAGE - 1) // PER_PAGE
    return render_template('appointments.html', appointments=all_appointments,
                           page=page, total_pages=total_pages,
                           status_filter=status_filter, date_from=date_from, date_to=date_to)



@appointments_bp.route("/add_appointment", methods=["GET", "POST"])
@login_required
def new_appointment():
    if request.method == "POST":
        car_id = request.form["car_id"]
        date = request.form["date"]
        time_val = request.form.get("time", "").strip()
        service = request.form["service"]
        assigned_to = request.form.get("assigned_to", "").strip()
        repeat = request.form.get("repeat", "").strip()
        repeat_count = request.form.get("repeat_count", "1").strip()
        try:
            rcount = max(1, min(int(repeat_count), 52))
        except ValueError:
            rcount = 1
        from datetime import datetime, timedelta
        dates_to_create = [date]
        if repeat in ('weekly', 'biweekly', 'monthly') and rcount > 1:
            try:
                base_date = datetime.strptime(date, '%Y-%m-%d')
                for i in range(1, rcount):
                    if repeat == 'weekly':
                        next_date = base_date + timedelta(weeks=i)
                    elif repeat == 'biweekly':
                        next_date = base_date + timedelta(weeks=i*2)
                    else:  # monthly
                        m = base_date.month + i
                        y = base_date.year + (m - 1) // 12
                        m = (m - 1) % 12 + 1
                        d = min(base_date.day, 28)
                        next_date = base_date.replace(year=y, month=m, day=d)
                    dates_to_create.append(next_date.strftime('%Y-%m-%d'))
            except (ValueError, OverflowError):
                pass
        created = 0
        with get_db() as conn3:
            for appt_date in dates_to_create:
                # Check double booking
                if time_val:
                    existing = conn3.execute(
                        "SELECT id FROM appointments WHERE date = ? AND time = ? AND status != 'cancelled'",
                        (appt_date, time_val)).fetchone()
                    if existing:
                        continue
                cursor3 = conn3.execute("INSERT INTO appointments (car_id, date, service) VALUES (?, ?, ?)",
                    (car_id, appt_date, service))
                appt_id = cursor3.lastrowid
                if time_val:
                    conn3.execute("UPDATE appointments SET time = ? WHERE id = ?", (time_val, appt_id))
                if assigned_to:
                    conn3.execute("UPDATE appointments SET assigned_to = ? WHERE id = ?", (assigned_to, appt_id))
                created += 1
            # حفظ صور قبل (للموعد الأول فقط)
            photos_b = []
            for f in request.files.getlist('photos_before'):
                if f.filename and allowed_file(f.filename):
                    import uuid
                    fname = f'{uuid.uuid4().hex}_{secure_filename(f.filename)}'
                    f.save(os.path.join(UPLOAD_FOLDER, fname))
                    photos_b.append(fname)
            if photos_b:
                first_appt = conn3.execute("SELECT id FROM appointments WHERE car_id = ? AND date = ? ORDER BY id DESC LIMIT 1",
                    (car_id, dates_to_create[0])).fetchone()
                if first_appt:
                    conn3.execute("UPDATE appointments SET photos_before = ? WHERE id = ?", (','.join(photos_b), first_appt[0]))
            conn3.commit()
        if created > 1:
            flash(f'{created} rendez-vous créés avec succès', 'success')
        log_activity('Add Appointment', f'Service: {service} (x{created})')
        cache.clear()
        return redirect("/appointments")
    from models.customer import get_all_customers
    all_customers = get_all_customers()
    with get_db() as conn:
        all_cars = conn.execute("SELECT * FROM cars").fetchall()
        technicians = conn.execute("SELECT username, COALESCE(full_name, '') FROM users").fetchall()
    return render_template("add_appointment.html", customers=all_customers, cars=all_cars, services=get_services(), technicians=technicians)



@appointments_bp.route("/update_appointment/<int:appointment_id>/<status>", methods=["POST"])
@login_required
def update_appointment(appointment_id, status):
    if status not in ('completed', 'cancelled', 'in_progress'):
        return redirect("/appointments")
    with get_db() as conn:
        conn.execute("UPDATE appointments SET status = ? WHERE id = ?", (status, appointment_id))
        # Auto-deduct inventory when completed
        if status == 'completed':
            appt = conn.execute("SELECT service FROM appointments WHERE id = ?", (appointment_id,)).fetchone()
            if appt:
                service_name = appt[0].split(' - ')[0].strip()
                links = conn.execute(
                    "SELECT inventory_id, quantity_used FROM service_inventory WHERE service_name = ?",
                    (service_name,)).fetchall()
                for link in links:
                    conn.execute(
                        "UPDATE inventory SET quantity = MAX(0, quantity - ?), updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                        (link[1], link[0]))
        conn.commit()
        
        # Auto WhatsApp notification on status change
        if status in STATUS_MESSAGES:
            appt_data = conn.execute("""SELECT a.date, a.service, c.name, c.phone, car.brand, car.model
                FROM appointments a JOIN cars car ON a.car_id=car.id 
                JOIN customers c ON car.customer_id=c.id WHERE a.id=?""", (appointment_id,)).fetchone()
            if appt_data and appt_data['phone']:
                shop_name = get_setting('shop_name', 'AMILCAR')
                msg = STATUS_MESSAGES[status].format(
                    name=appt_data['name'], car=f"{appt_data['brand']} {appt_data['model']}",
                    shop=shop_name, service=appt_data['service'], date=appt_data['date'])
                wa_url = _build_wa_status_url(appt_data['phone'], msg)
                return redirect(wa_url)
    
    return redirect("/appointments")



@appointments_bp.route("/delete_appointment/<int:appointment_id>", methods=["POST"])
@login_required
def delete_appointment(appointment_id):
    from datetime import datetime
    with get_db() as conn:
        conn.execute("UPDATE appointments SET is_deleted=1, deleted_at=? WHERE id = ?", (datetime.now().isoformat(), appointment_id))
        conn.commit()
    log_activity('Delete Appointment', f'Appointment #{appointment_id} (soft)')
    flash('Rendez-vous supprim\u00e9 — r\u00e9cup\u00e9rable depuis la corbeille', 'success')
    return redirect("/appointments")



@appointments_bp.route("/edit_appointment/<int:appointment_id>", methods=["GET", "POST"])
@login_required
def edit_appointment(appointment_id):
    with get_db() as conn:
        if request.method == "POST":
            date_val = request.form.get("date", "").strip()
            time_val = request.form.get("time", "").strip()
            service = request.form.get("service", "").strip()
            if not date_val or not service:
                flash("La date et le service sont requis", "error")
                appt = conn.execute(
                    "SELECT a.*, cu.name, ca.brand, ca.model "
                    "FROM appointments a JOIN cars ca ON a.car_id = ca.id "
                    "JOIN customers cu ON ca.customer_id = cu.id WHERE a.id = ?", (appointment_id,)).fetchone()
                return render_template("edit_appointment.html", appt=appt, services=get_services())
            # Check double booking
            if time_val:
                existing = conn.execute(
                    "SELECT id FROM appointments WHERE date = ? AND time = ? AND status != 'cancelled' AND id != ?",
                    (date_val, time_val, appointment_id)).fetchone()
                if existing:
                    flash(f'Le créneau {time_val} du {date_val} est déjà réservé', 'error')
                    appt = conn.execute(
                        "SELECT a.*, cu.name, ca.brand, ca.model "
                        "FROM appointments a JOIN cars ca ON a.car_id = ca.id "
                        "JOIN customers cu ON ca.customer_id = cu.id WHERE a.id = ?", (appointment_id,)).fetchone()
                    return render_template("edit_appointment.html", appt=appt, services=get_services())
            assigned_to = request.form.get("assigned_to", "").strip()
            conn.execute("UPDATE appointments SET date = ?, time = ?, service = ?, assigned_to = ? WHERE id = ?",
                (date_val, time_val, service, assigned_to, appointment_id))
            conn.commit()
            flash("Rendez-vous mis à jour avec succès", "success")
            return redirect("/appointments")
        appt = conn.execute(
            "SELECT a.*, cu.name, ca.brand, ca.model "
            "FROM appointments a JOIN cars ca ON a.car_id = ca.id "
            "JOIN customers cu ON ca.customer_id = cu.id WHERE a.id = ?", (appointment_id,)).fetchone()
    if not appt:
        return redirect("/appointments")
    with get_db() as conn2:
        technicians = conn2.execute("SELECT username, COALESCE(full_name, '') FROM users").fetchall()
    return render_template("edit_appointment.html", appt=appt, services=get_services(), technicians=technicians)



@appointments_bp.route("/calendar")
@login_required
def calendar_view():
    return render_template("calendar.html")



# ─── Feature 5: Appointment Heatmap ───
@appointments_bp.route("/heatmap")
@login_required
def appointment_heatmap():
    return render_template("heatmap.html")



# ─── Phase 6 Feature 9: Smart Waiting Queue ───
@appointments_bp.route("/queue")
@login_required
def waiting_queue():
    with get_db() as conn:
        queue = conn.execute(
            "SELECT wq.id, cu.name, cu.phone, COALESCE(ca.brand||' '||ca.model,''), "
            "wq.service, wq.priority, wq.status, wq.estimated_wait, wq.notes, wq.created_at "
            "FROM waiting_queue wq JOIN customers cu ON wq.customer_id=cu.id "
            "LEFT JOIN cars ca ON wq.car_id=ca.id "
            "WHERE wq.status IN ('waiting','serving') ORDER BY wq.priority DESC, wq.created_at").fetchall()
        customers = conn.execute("SELECT id, name FROM customers ORDER BY name").fetchall()
        cars = conn.execute("SELECT id, brand||' '||model, customer_id FROM cars ORDER BY brand").fetchall()
        services_list = conn.execute("SELECT name FROM services ORDER BY name").fetchall()
    return render_template("waiting_queue.html", queue=queue, customers=customers,
                           cars=cars, services=[s[0] for s in services_list])



@appointments_bp.route("/queue/add", methods=["POST"])
@login_required
def add_to_queue():
    customer_id = request.form.get("customer_id")
    car_id = request.form.get("car_id") or None
    service = request.form.get("service", "").strip()
    priority = int(request.form.get("priority", "0"))
    notes = request.form.get("notes", "").strip()
    estimated_wait = int(request.form.get("estimated_wait", "30"))
    if not customer_id:
        flash("Client requis", "error")
        return redirect("/queue")
    with get_db() as conn:
        conn.execute("INSERT INTO waiting_queue (customer_id, car_id, service, priority, estimated_wait, notes) VALUES (?,?,?,?,?,?)",
            (customer_id, car_id, service, priority, estimated_wait, notes))
        conn.commit()
    flash("Client ajouté à la file d'attente", "success")
    return redirect("/queue")



@appointments_bp.route("/queue/update/<int:queue_id>", methods=["POST"])
@login_required
def update_queue_status(queue_id):
    new_status = request.form.get("status", "waiting")
    if new_status not in ('waiting', 'serving', 'done', 'cancelled'):
        new_status = 'waiting'
    with get_db() as conn:
        conn.execute("UPDATE waiting_queue SET status = ? WHERE id = ?", (new_status, queue_id))
        conn.commit()
    return redirect("/queue")



@appointments_bp.route("/queue/remove/<int:queue_id>", methods=["POST"])
@login_required
def remove_from_queue(queue_id):
    with get_db() as conn:
        conn.execute("DELETE FROM waiting_queue WHERE id = ?", (queue_id,))
        conn.commit()
    flash("Retiré de la file", "success")
    return redirect("/queue")



@appointments_bp.route("/bookings_admin")
@login_required
def bookings_admin():
    with get_db() as conn:
        bookings = conn.execute("SELECT * FROM online_bookings ORDER BY created_at DESC").fetchall()
    return render_template("bookings_admin.html", bookings=bookings)



@appointments_bp.route("/booking_confirm/<int:booking_id>", methods=["POST"])
@login_required
def booking_confirm(booking_id):
    with get_db() as conn:
        booking = conn.execute("SELECT * FROM online_bookings WHERE id=?", (booking_id,)).fetchone()
        if not booking:
            flash("Réservation introuvable", "danger")
            return redirect("/bookings_admin")
        # Create customer if not exists
        customer = conn.execute("SELECT id FROM customers WHERE phone=?", (booking[2],)).fetchone()
        if not customer:
            conn.execute("INSERT INTO customers (name, phone, email) VALUES (?,?,?)", (booking[1], booking[2], booking[3]))
            cid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        else:
            cid = customer[0]
        # Create car if plate given
        if booking[6]:
            car = conn.execute("SELECT id FROM cars WHERE plate=? AND customer_id=?", (booking[6], cid)).fetchone()
            if not car:
                conn.execute("INSERT INTO cars (customer_id, brand, model, plate) VALUES (?,?,?,?)",
                            (cid, booking[4] or 'N/A', booking[5] or 'N/A', booking[6]))
                car_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            else:
                car_id = car[0]
        else:
            car = conn.execute("SELECT id FROM cars WHERE customer_id=?", (cid,)).fetchone()
            if car:
                car_id = car[0]
            else:
                conn.execute("INSERT INTO cars (customer_id, brand, model, plate) VALUES (?,?,?,?)",
                            (cid, booking[4] or 'N/A', booking[5] or 'N/A', 'N/A'))
                car_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        # Create appointment
        conn.execute("INSERT INTO appointments (car_id, date, time, service, status) VALUES (?,?,?,?,?)",
                    (car_id, booking[8], booking[9] or '', booking[7], 'Confirmé'))
        conn.execute("UPDATE online_bookings SET status='confirmed' WHERE id=?", (booking_id,))
        conn.commit()
    flash("Réservation confirmée et rendez-vous créé !", "success")
    return redirect("/bookings_admin")



@appointments_bp.route("/booking_reject/<int:booking_id>", methods=["POST"])
@login_required
def booking_reject(booking_id):
    with get_db() as conn:
        conn.execute("UPDATE online_bookings SET status='rejected' WHERE id=?", (booking_id,))
        conn.commit()
    flash("Réservation refusée", "info")
    return redirect("/bookings_admin")



# ─── Phase 8 Feature 1: Automated Weekly/Monthly Reports ───
@appointments_bp.route("/scheduled_reports", methods=["GET", "POST"])
@login_required
def scheduled_reports():
    with get_db() as conn:
        if request.method == "POST":
            report_type = request.form.get('report_type', 'weekly')
            email_to = request.form.get('email_to', '')
            if email_to:
                conn.execute("INSERT INTO scheduled_reports (report_type, email_to) VALUES (?,?)", (report_type, email_to))
                conn.commit()
                flash("Rapport programmé ajouté !", "success")
            return redirect("/scheduled_reports")
        reports = conn.execute("SELECT * FROM scheduled_reports ORDER BY created_at DESC").fetchall()
    return render_template("scheduled_reports.html", reports=reports)



@appointments_bp.route("/scheduled_reports/delete/<int:rid>", methods=["POST"])
@login_required
def delete_scheduled_report(rid):
    with get_db() as conn:
        conn.execute("DELETE FROM scheduled_reports WHERE id=?", (rid,))
        conn.commit()
    flash("Rapport supprimé", "success")
    return redirect("/scheduled_reports")



@appointments_bp.route("/scheduled_reports/send_now/<int:rid>", methods=["POST"])
@login_required
def send_report_now(rid):
    with get_db() as conn:
        report = conn.execute("SELECT * FROM scheduled_reports WHERE id=?", (rid,)).fetchone()
        if not report:
            flash("Rapport introuvable", "danger")
            return redirect("/scheduled_reports")
        from datetime import date, timedelta
        today = date.today()
        if report[1] == 'weekly':
            start = (today - timedelta(days=7)).isoformat()
        else:
            start = today.replace(day=1).isoformat()
        rev = conn.execute("SELECT COALESCE(SUM(amount),0) FROM invoices WHERE date >= ? AND status='Payée'", (start,)).fetchone()[0]
        exp = conn.execute("SELECT COALESCE(SUM(amount),0) FROM expenses WHERE date >= ?", (start,)).fetchone()[0]
        appts = conn.execute("SELECT COUNT(*) FROM appointments WHERE date >= ?", (start,)).fetchone()[0]
        new_clients = conn.execute("SELECT COUNT(*) FROM customers WHERE created_at >= ?", (start,)).fetchone()[0]
        period = "Semaine" if report[1] == 'weekly' else "Mois"
        body = f"""<h2>AMILCAR — Rapport {period}</h2>
        <p>Période: {start} → {today.isoformat()}</p>
        <table style='border-collapse:collapse;width:100%'>
        <tr><td style='padding:8px;border:1px solid #ddd'><strong>Revenu</strong></td><td style='padding:8px;border:1px solid #ddd;color:green'>{rev:.0f} DH</td></tr>
        <tr><td style='padding:8px;border:1px solid #ddd'><strong>Dépenses</strong></td><td style='padding:8px;border:1px solid #ddd;color:red'>{exp:.0f} DH</td></tr>
        <tr><td style='padding:8px;border:1px solid #ddd'><strong>Bénéfice</strong></td><td style='padding:8px;border:1px solid #ddd;color:goldenrod'>{rev-exp:.0f} DH</td></tr>
        <tr><td style='padding:8px;border:1px solid #ddd'><strong>RDV</strong></td><td style='padding:8px;border:1px solid #ddd'>{appts}</td></tr>
        <tr><td style='padding:8px;border:1px solid #ddd'><strong>Nouveaux Clients</strong></td><td style='padding:8px;border:1px solid #ddd'>{new_clients}</td></tr>
        </table>"""
        # Try sending email
        smtp_server = conn.execute("SELECT value FROM settings WHERE key='smtp_server'").fetchone()
        smtp_email = conn.execute("SELECT value FROM settings WHERE key='smtp_email'").fetchone()
        smtp_pass = conn.execute("SELECT value FROM settings WHERE key='smtp_password'").fetchone()
        smtp_port = conn.execute("SELECT value FROM settings WHERE key='smtp_port'").fetchone()
        if smtp_server and smtp_email and smtp_pass:
            try:
                import smtplib
                from email.mime.text import MIMEText
                from email.mime.multipart import MIMEMultipart
                msg = MIMEMultipart()
                msg['From'] = f"AMILCAR <{smtp_email[0]}>"
                msg['To'] = report[2]
                msg['Subject'] = f"AMILCAR — Rapport {period} ({today.isoformat()})"
                msg.attach(MIMEText(body, 'html'))
                with smtplib.SMTP(smtp_server[0], int(smtp_port[0] if smtp_port else 587)) as server:
                    server.starttls()
                    server.login(smtp_email[0], smtp_pass[0])
                    server.send_message(msg)
                conn.execute("UPDATE scheduled_reports SET last_sent=? WHERE id=?", (today.isoformat(), rid))
                conn.execute("INSERT INTO email_log (to_email, subject, body, status) VALUES (?,?,?,?)",
                            (report[2], f"Rapport {period}", body, 'sent'))
                conn.commit()
                flash("Rapport envoyé par email !", "success")
            except Exception as e:
                flash(f"Erreur d'envoi: {str(e)}", "danger")
        else:
            conn.execute("UPDATE scheduled_reports SET last_sent=? WHERE id=?", (today.isoformat(), rid))
            conn.commit()
            flash("Rapport généré (SMTP non configuré — configurez dans Email Settings)", "warning")
    return redirect("/scheduled_reports")



# ─── 3. Bay / Resource Management ───
@appointments_bp.route("/bays")
@login_required
@admin_required
def bays():
    with get_db() as conn:
        bays_list = conn.execute("SELECT * FROM service_bays ORDER BY name").fetchall()
        today_date = request.args.get("date", "")
        if not today_date:
            from datetime import date
            today_date = date.today().isoformat()
        bookings = conn.execute("""SELECT bb.*, sb.name as bay_name, a.service, c.name as customer_name
            FROM bay_bookings bb JOIN service_bays sb ON bb.bay_id=sb.id
            JOIN appointments a ON bb.appointment_id=a.id
            JOIN cars ca ON a.car_id=ca.id JOIN customers c ON ca.customer_id=c.id
            WHERE bb.date=? ORDER BY bb.start_time""", (today_date,)).fetchall()
        appointments = conn.execute("""SELECT a.id, c.name, a.service, a.time
            FROM appointments a JOIN cars ca ON a.car_id=ca.id JOIN customers c ON ca.customer_id=c.id
            WHERE a.date=? AND a.status IN ('pending','in_progress')
            ORDER BY a.time""", (today_date,)).fetchall()
    return render_template("bays.html", bays=bays_list, bookings=bookings,
                          appointments=appointments, today_date=today_date)



@appointments_bp.route("/bay/add", methods=["POST"])
@login_required
@admin_required
def bay_add():
    name = request.form.get("name", "").strip()
    bay_type = request.form.get("bay_type", "general")
    if name:
        with get_db() as conn:
            conn.execute("INSERT INTO service_bays (name, bay_type) VALUES (?,?)", (name, bay_type))
            conn.commit()
        flash("Bay ajouté !", "success")
    return redirect("/bays")



@appointments_bp.route("/bay/book", methods=["POST"])
@login_required
def bay_book():
    bay_id = request.form.get("bay_id", type=int)
    appt_id = request.form.get("appointment_id", type=int)
    bdate = request.form.get("date", "")
    start = request.form.get("start_time", "")
    end = request.form.get("end_time", "")
    if bay_id and appt_id and bdate and start and end:
        with get_db() as conn:
            conflict = conn.execute("""SELECT id FROM bay_bookings WHERE bay_id=? AND date=?
                AND ((start_time < ? AND end_time > ?) OR (start_time < ? AND end_time > ?))""",
                (bay_id, bdate, end, start, end, start)).fetchone()
            if conflict:
                flash("Ce bay est déjà réservé pour ce créneau !", "error")
            else:
                conn.execute("INSERT INTO bay_bookings (bay_id, appointment_id, start_time, end_time, date) VALUES (?,?,?,?,?)",
                            (bay_id, appt_id, start, end, bdate))
                conn.execute("UPDATE appointments SET bay_id=? WHERE id=?", (bay_id, appt_id))
                conn.commit()
                flash("Bay réservé !", "success")
    return redirect(f"/bays?date={bdate}")



@appointments_bp.route("/bay/toggle/<int:bid>", methods=["POST"])
@login_required
@admin_required
def bay_toggle(bid):
    with get_db() as conn:
        b = conn.execute("SELECT active FROM service_bays WHERE id=?", (bid,)).fetchone()
        if b:
            conn.execute("UPDATE service_bays SET active=? WHERE id=?", (0 if b[0] else 1, bid))
            conn.commit()
    return redirect("/bays")



@appointments_bp.route("/digital_inspection/<int:appt_id>", methods=["GET", "POST"])
@login_required
def digital_inspection(appt_id):
    import json
    with get_db() as conn:
        appt = conn.execute("""SELECT a.*, ca.brand, ca.model, ca.plate, c.name, c.phone
            FROM appointments a JOIN cars ca ON a.car_id=ca.id JOIN customers c ON ca.customer_id=c.id
            WHERE a.id=?""", (appt_id,)).fetchone()
        if not appt:
            flash("RDV introuvable", "error")
            return redirect("/appointments")

        if request.method == "POST":
            items = []
            for cat, checks in INSPECTION_CATEGORIES.items():
                for check in checks:
                    key = f"item_{cat}_{check}".replace(' ', '_')
                    status = request.form.get(key, 'ok')
                    note = request.form.get(f"note_{key}", '')
                    items.append({'category': cat, 'item': check, 'status': status, 'note': note})

            overall = request.form.get("overall_status", "pass")
            notes = request.form.get("notes", "")
            token = uuid.uuid4().hex[:16]

            existing = conn.execute("SELECT id FROM digital_inspections WHERE appointment_id=?", (appt_id,)).fetchone()
            if existing:
                conn.execute("""UPDATE digital_inspections SET items=?, overall_status=?, notes=?, inspector=?, token=?
                    WHERE id=?""", (json.dumps(items), overall, notes, session.get('username', ''), token, existing[0]))
            else:
                conn.execute("""INSERT INTO digital_inspections (appointment_id, car_id, inspector, items, overall_status, notes, token)
                    VALUES (?,?,?,?,?,?,?)""",
                    (appt_id, appt[1], session.get('username', ''), json.dumps(items), overall, notes, token))
            conn.commit()
            flash("Inspection sauvegardée !", "success")
            return redirect(f"/digital_inspection/{appt_id}")

        inspection = conn.execute("SELECT * FROM digital_inspections WHERE appointment_id=?", (appt_id,)).fetchone()
        items_data = {}
        if inspection and inspection[4]:
            try:
                for item in json.loads(inspection[4]):
                    key = f"item_{item['category']}_{item['item']}".replace(' ', '_')
                    items_data[key] = item
            except (json.JSONDecodeError, KeyError):
                pass

    return render_template("digital_inspection.html", appt=appt, inspection=inspection,
                          categories=INSPECTION_CATEGORIES, items_data=items_data)



@appointments_bp.route("/digital_inspection/view/<token>")
@csrf.exempt
def digital_inspection_public(token):
    import json
    with get_db() as conn:
        insp = conn.execute("""SELECT di.*, ca.brand, ca.model, ca.plate, c.name
            FROM digital_inspections di JOIN cars ca ON di.car_id=ca.id
            JOIN appointments a ON di.appointment_id=a.id
            JOIN customers cust ON cust.id=ca.customer_id AS c
            WHERE di.token=?""", (token,)).fetchone()
        if not insp:
            # Try alternate query
            insp = conn.execute("""SELECT di.*, ca.brand, ca.model, ca.plate,
                (SELECT name FROM customers WHERE id=ca.customer_id) as cname
                FROM digital_inspections di JOIN cars ca ON di.car_id=ca.id
                WHERE di.token=?""", (token,)).fetchone()
        if not insp:
            return "Inspection introuvable", 404
        items = []
        try:
            items = json.loads(insp[4]) if insp[4] else []
        except (json.JSONDecodeError, TypeError):
            pass
    return render_template("digital_inspection_public.html", insp=insp, items=items)



@appointments_bp.route("/digital_inspection/notify/<int:appt_id>", methods=["POST"])
@login_required
def digital_inspection_notify(appt_id):
    with get_db() as conn:
        insp = conn.execute("SELECT token FROM digital_inspections WHERE appointment_id=?", (appt_id,)).fetchone()
        if insp:
            conn.execute("UPDATE digital_inspections SET customer_notified=1 WHERE appointment_id=?", (appt_id,))
            conn.commit()
            flash(f"Lien d'inspection: /digital_inspection/view/{insp[0]}", "success")
    return redirect(f"/digital_inspection/{appt_id}")



# ─── 7. Capacity Planning ───

@appointments_bp.route("/capacity")
@login_required
@admin_required
def capacity_planning():
    from datetime import date, timedelta
    with get_db() as conn:
        days = []
        for i in range(14):
            d = date.today() + timedelta(days=i)
            d_str = d.isoformat()
            total_bays = conn.execute("SELECT COUNT(*) FROM service_bays WHERE active=1").fetchone()[0] or 1
            total_techs = conn.execute("SELECT COUNT(*) FROM users WHERE role IN ('admin','employee')").fetchone()[0] or 1
            booked = conn.execute("SELECT COUNT(*) FROM appointments WHERE date=? AND status!='cancelled'", (d_str,)).fetchone()[0]
            booked_hours = booked * 1.5
            available_hours = min(total_bays, total_techs) * 8
            utilization = (booked_hours / available_hours * 100) if available_hours else 0
            # Save
            existing = conn.execute("SELECT id FROM capacity_planning WHERE date=?", (d_str,)).fetchone()
            if existing:
                conn.execute("""UPDATE capacity_planning SET total_bays=?, total_technicians=?,
                    available_hours=?, booked_hours=?, utilization_pct=? WHERE date=?""",
                    (total_bays, total_techs, available_hours, booked_hours, utilization, d_str))
            else:
                conn.execute("""INSERT INTO capacity_planning 
                    (date, total_bays, total_technicians, available_hours, booked_hours, utilization_pct)
                    VALUES (?,?,?,?,?,?)""",
                    (d_str, total_bays, total_techs, available_hours, booked_hours, utilization))
            days.append({
                'date': d_str, 'weekday': d.strftime("%A"), 'bays': total_bays,
                'techs': total_techs, 'available': available_hours, 'booked': booked_hours,
                'utilization': utilization, 'appointments': booked
            })
        conn.commit()
    return render_template("capacity.html", days=days)



# ─── 3. Réservation en Ligne Pro ───

@appointments_bp.route("/booking_online")
def booking_online():
    with get_db() as conn:
        services = conn.execute("SELECT id, name, price FROM services WHERE price > 0 ORDER BY name").fetchall()
        packs = conn.execute("SELECT * FROM detailing_packs WHERE is_active=1 ORDER BY name").fetchall()
        shop = {}
        for r in conn.execute("SELECT key, value FROM settings").fetchall():
            shop[r['key']] = r['value']
    return render_template("booking_online.html", services=services, packs=packs, shop=shop)



@appointments_bp.route("/booking_online/submit", methods=["POST"])
@csrf.exempt
def booking_online_submit():
    name = request.form.get("name", "").strip()
    phone = request.form.get("phone", "").strip()
    vehicle_type = request.form.get("vehicle_type", "voiture")
    brand = request.form.get("brand", "").strip()
    model = request.form.get("model", "").strip()
    plate = request.form.get("plate", "").strip()
    service = request.form.get("service", "")
    preferred_date = request.form.get("preferred_date", "")
    preferred_time = request.form.get("preferred_time", "")
    notes = request.form.get("notes", "")
    if name and phone and service and preferred_date:
        with get_db() as conn:
            conn.execute("""INSERT INTO online_bookings (customer_name, phone, vehicle_type, brand, model, plate,
                service, preferred_date, preferred_time, notes, status)
                VALUES (?,?,?,?,?,?,?,?,?,?,'pending')""",
                (name, phone, vehicle_type, brand, model, plate, service, preferred_date, preferred_time, notes))
            conn.commit()
    return render_template("booking_success.html", name=name)



@appointments_bp.route("/checklist/fill/<int:appointment_id>", methods=["GET", "POST"])
@login_required
def checklist_fill(appointment_id):
    import json
    with get_db() as conn:
        appt = conn.execute("""SELECT a.*, c.plate, c.brand, c.model, c.vehicle_type, cu.name as customer_name
            FROM appointments a JOIN cars c ON a.car_id=c.id JOIN customers cu ON c.customer_id=cu.id
            WHERE a.id=?""", (appointment_id,)).fetchone()
        if not appt:
            flash("RDV non trouvé", "danger")
            return redirect("/appointments")
        checklists = conn.execute("""SELECT * FROM service_checklists
            WHERE (service_name=? OR service_name LIKE ?) AND (vehicle_type='all' OR vehicle_type=?)""",
            (appt['service'], f"%{appt['service']}%", appt['vehicle_type'] or 'voiture')).fetchall()
        if request.method == "POST":
            checklist_id = request.form.get("checklist_id", 0, type=int)
            items_checked = request.form.getlist("items")
            total = request.form.get("total_items", 0, type=int)
            notes = request.form.get("notes", "")
            score = int((len(items_checked) / total * 100)) if total > 0 else 0
            conn.execute("""INSERT INTO checklist_results (appointment_id, checklist_id, results, score, total_items, checked_by, notes)
                VALUES (?,?,?,?,?,?,?)""", (appointment_id, checklist_id, json.dumps(items_checked), score, total, session.get('user_id', 0), notes))
            conn.commit()
            flash(f"Checklist validée — Score: {score}% ✅", "success")
            return redirect(f"/checklist/fill/{appointment_id}")
        results = conn.execute("SELECT cr.*, sc.service_name FROM checklist_results cr LEFT JOIN service_checklists sc ON cr.checklist_id=sc.id WHERE cr.appointment_id=?", (appointment_id,)).fetchall()
    return render_template("checklist_fill.html", appt=appt, checklists=checklists, results=results)



@appointments_bp.route('/waitlist/add', methods=['POST'])
@login_required
def waitlist_add():
    with get_db() as conn:
        cid = int(request.form.get('customer_id', 0))
        cust = conn.execute("SELECT name, phone FROM customers WHERE id=?", (cid,)).fetchone()
        conn.execute("""INSERT INTO appointment_waitlist
            (customer_id, customer_name, phone, service_requested, preferred_date, preferred_time, notes)
            VALUES (?,?,?,?,?,?,?)""",
            (cid, cust['name'] if cust else request.form.get('customer_name', ''),
             cust['phone'] if cust else request.form.get('phone', ''),
             request.form.get('service_requested', ''),
             request.form.get('preferred_date', ''),
             request.form.get('preferred_time', ''),
             request.form.get('notes', '')))
        conn.commit()
    flash("Ajouté à la liste d'attente", "success")
    return redirect("/appointment_waitlist")



@appointments_bp.route('/waitlist/notify/<int:wid>')
@login_required
def waitlist_notify(wid):
    with get_db() as conn:
        conn.execute("UPDATE appointment_waitlist SET status='notified', notified_at=? WHERE id=?",
                    (datetime.now().strftime('%Y-%m-%d %H:%M'), wid))
        conn.commit()
    flash("Client notifié", "success")
    return redirect("/appointment_waitlist")



@appointments_bp.route('/waitlist/convert/<int:wid>')
@login_required
def waitlist_convert(wid):
    with get_db() as conn:
        w = conn.execute("SELECT * FROM appointment_waitlist WHERE id=?", (wid,)).fetchone()
        if w:
            conn.execute("""INSERT INTO appointments (customer_id, date, time, service, status)
                VALUES (?,?,?,?,?)""",
                (w['customer_id'], w['preferred_date'], w['preferred_time'],
                 w['service_requested'], 'pending'))
            appt_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            conn.execute("UPDATE appointment_waitlist SET status='converted', assigned_appointment_id=? WHERE id=?",
                        (appt_id, wid))
            conn.commit()
    flash("Converti en rendez-vous", "success")
    return redirect("/appointment_waitlist")



@appointments_bp.route('/waitlist/remove/<int:wid>')
@login_required
def waitlist_remove(wid):
    with get_db() as conn:
        conn.execute("DELETE FROM appointment_waitlist WHERE id=?", (wid,))
        conn.commit()
    flash("Retiré de la liste d'attente", "success")
    return redirect("/appointment_waitlist")

# ── 7. Employee Attendance ──
@appointments_bp.route('/employee_attendance')
@login_required
def employee_attendance():
    month = request.args.get('month', datetime.now().strftime('%Y-%m'))
    with get_db() as conn:
        employees = conn.execute("SELECT id, full_name FROM users WHERE role != 'admin' ORDER BY full_name").fetchall()
        records = conn.execute("""
            SELECT * FROM employee_attendance
            WHERE date LIKE ? ORDER BY date DESC, employee_name
        """, (f"{month}%",)).fetchall()
        # Monthly stats per employee
        stats = conn.execute("""
            SELECT employee_id, employee_name,
                   COUNT(*) as total_days,
                   SUM(CASE WHEN status='present' THEN 1 ELSE 0 END) as present,
                   SUM(CASE WHEN status='absent' THEN 1 ELSE 0 END) as absent,
                   SUM(CASE WHEN status='late' THEN 1 ELSE 0 END) as late,
                   SUM(late_minutes) as total_late_min,
                   SUM(overtime_minutes) as total_overtime_min
            FROM employee_attendance WHERE date LIKE ?
            GROUP BY employee_id ORDER BY employee_name
        """, (f"{month}%",)).fetchall()
    today = datetime.now().strftime('%Y-%m-%d')
    return render_template('employee_attendance.html', employees=employees,
                          records=records, stats=stats, month=month, today=today)


