"""
AMILCAR — WhatsApp, SMS, Email & Notifications
Blueprint: comms_bp
Routes: 34
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

comms_bp = Blueprint("comms_bp", __name__)


# ─── Email Invoice ───
@comms_bp.route("/email_invoice/<int:invoice_id>", methods=["POST"])
@login_required
def email_invoice(invoice_id):
    import smtplib
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText
    from email.mime.application import MIMEApplication
    from xhtml2pdf import pisa

    with get_db() as conn:
        inv = conn.execute(
            "SELECT i.id, i.appointment_id, i.amount, i.status, a.date, a.service, "
            "cu.name, cu.phone, ca.brand, ca.model, ca.plate, COALESCE(i.paid_amount, 0), i.payment_method, "
            "COALESCE(i.discount_type, ''), COALESCE(i.discount_value, 0), COALESCE(cu.email, '') "
            "FROM invoices i JOIN appointments a ON i.appointment_id = a.id "
            "JOIN cars ca ON a.car_id = ca.id JOIN customers cu ON ca.customer_id = cu.id "
            "WHERE i.id = ?", (invoice_id,)).fetchone()
    if not inv:
        flash("Facture introuvable", "error")
        return redirect("/invoices")
    customer_email = inv[15]
    if not customer_email:
        flash("Le client n'a pas d'adresse email", "error")
        return redirect("/invoices")
    settings = get_all_settings()
    smtp_host = settings.get('smtp_host', '')
    smtp_port = settings.get('smtp_port', '587')
    smtp_user = settings.get('smtp_user', '')
    smtp_pass = settings.get('smtp_pass', '')
    smtp_from = settings.get('smtp_from', smtp_user)
    if not smtp_host or not smtp_user:
        flash("Les paramètres SMTP ne sont pas configurés. Allez dans Paramètres pour configurer l'email.", "error")
        return redirect("/invoices")
    html = render_template("print_invoice.html", inv=inv, settings=settings)
    import base64
    logo_path = os.path.join(os.path.abspath('static'), 'logo.png')
    if os.path.exists(logo_path):
        with open(logo_path, 'rb') as f:
            logo_b64 = base64.b64encode(f.read()).decode()
        html = html.replace('/static/logo.png', f'data:image/png;base64,{logo_b64}')
    pdf_buffer = io.BytesIO()
    pisa.CreatePDF(io.StringIO(html), dest=pdf_buffer)
    pdf_buffer.seek(0)
    msg = MIMEMultipart()
    msg['From'] = smtp_from
    msg['To'] = customer_email
    shop_name = settings.get('shop_name', 'AMILCAR')
    msg['Subject'] = f'Facture #{invoice_id} — {shop_name}'
    body = f"Cher(e) {inv[6]},\n\nVeuillez trouver ci-joint votre facture n°{invoice_id}.\n\nMerci d'avoir choisi {shop_name}."
    msg.attach(MIMEText(body, 'plain'))
    pdf_part = MIMEApplication(pdf_buffer.read(), _subtype='pdf')
    pdf_part.add_header('Content-Disposition', 'attachment', filename=f'invoice_{invoice_id}.pdf')
    msg.attach(pdf_part)
    try:
        server = smtplib.SMTP(smtp_host, int(smtp_port), timeout=10)
        server.starttls()
        server.login(smtp_user, smtp_pass)
        server.send_message(msg)
        server.quit()
        flash(f"Facture envoyée à {customer_email}", "success")
        log_activity('Email Invoice', f'Invoice #{invoice_id} → {customer_email}')
    except Exception as e:
        flash(f"Erreur d'envoi email : {str(e)}", "error")
        log_activity('Email Error', f'Invoice #{invoice_id}: {str(e)}')
    return redirect("/invoices")



# ─── SMS Notifications ───
@comms_bp.route("/send_sms_reminders", methods=["POST"])
@admin_required
def send_sms_reminders():
    import requests as http_requests
    from datetime import date, timedelta
    settings = get_all_settings()
    api_url = settings.get('sms_api_url', '')
    api_key = settings.get('sms_api_key', '')
    sender = settings.get('sms_sender', 'AMILCAR')
    shop_name = settings.get('shop_name', 'AMILCAR')
    if not api_url or not api_key:
        flash("Configurez les paramètres SMS dans Paramètres (API URL + API Key)", "error")
        return redirect("/notifications")
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    with get_db() as conn:
        appts = conn.execute(
            "SELECT a.id, a.date, COALESCE(a.time, ''), a.service, cu.name, cu.phone "
            "FROM appointments a JOIN cars ca ON a.car_id = ca.id JOIN customers cu ON ca.customer_id = cu.id "
            "WHERE a.date = ? AND a.status = 'pending'", (tomorrow,)).fetchall()
    sent = 0
    errors = 0
    for a in appts:
        phone = a[5]
        if not phone:
            continue
        time_str = f" à {a[2]}" if a[2] else ""
        message = f"Bonjour {a[4]}, rappel de votre RDV chez {shop_name} demain{time_str} pour {a[3]}. À bientôt !"
        try:
            resp = http_requests.post(api_url, json={
                'api_key': api_key,
                'to': phone,
                'from': sender,
                'message': message
            }, timeout=10)
            if resp.status_code == 200:
                sent += 1
            else:
                errors += 1
        except Exception:
            errors += 1
    log_activity('SMS Reminders', f'{sent} envoyés, {errors} erreurs pour {tomorrow}')
    flash(f"SMS envoyés : {sent} succès, {errors} erreurs", "success" if errors == 0 else "warning")
    return redirect("/notifications")



# ─── Notifications ───
@comms_bp.route("/notifications")
@login_required
def notifications():
    from datetime import date, timedelta
    with get_db() as conn:
        today = date.today()
        tomorrow = today + timedelta(days=1)
        next_3 = today + timedelta(days=3)
        next_7 = today + timedelta(days=7)
        # Tomorrow's appointments
        tomorrow_appts = conn.execute(
            "SELECT a.id, a.date, a.time, a.service, a.status, ca.brand, ca.model, ca.plate, cu.name, cu.phone "
            "FROM appointments a JOIN cars ca ON a.car_id = ca.id JOIN customers cu ON ca.customer_id = cu.id "
            "WHERE a.date = ? ORDER BY a.time", (tomorrow.isoformat(),)).fetchall()
        # Next 3 days
        upcoming_3 = conn.execute(
            "SELECT a.id, a.date, a.time, a.service, a.status, ca.brand, ca.model, ca.plate, cu.name, cu.phone "
            "FROM appointments a JOIN cars ca ON a.car_id = ca.id JOIN customers cu ON ca.customer_id = cu.id "
            "WHERE a.date > ? AND a.date <= ? ORDER BY a.date, a.time", (tomorrow.isoformat(), next_3.isoformat())).fetchall()
        # Unpaid invoices
        unpaid = conn.execute(
            "SELECT i.id, i.amount, a.date, a.service, cu.name, cu.phone "
            "FROM invoices i JOIN appointments a ON i.appointment_id = a.id "
            "JOIN cars ca ON a.car_id = ca.id JOIN customers cu ON ca.customer_id = cu.id "
            "WHERE i.status IN ('unpaid', 'partial') ORDER BY a.date DESC LIMIT 10").fetchall()
        # Maintenance reminders due in next 7 days
        reminders = conn.execute(
            "SELECT ca.brand, ca.model, ca.plate, cu.name, cu.phone, a.service, a.date "
            "FROM appointments a JOIN cars ca ON a.car_id = ca.id JOIN customers cu ON ca.customer_id = cu.id "
            "WHERE a.status = 'completed' AND date(a.date, '+90 days') BETWEEN ? AND ? "
            "ORDER BY a.date", (today.isoformat(), next_7.isoformat())).fetchall()
    return render_template("notifications.html", tomorrow_appts=tomorrow_appts,
                           upcoming_3=upcoming_3, unpaid=unpaid, reminders=reminders,
                           tomorrow=tomorrow.isoformat())



# ─── Auto Email Reminders ───
@comms_bp.route("/send_email_reminders", methods=["POST"])
@admin_required
def send_email_reminders():
    import smtplib
    from email.mime.text import MIMEText
    from datetime import date, timedelta
    settings = get_all_settings()
    smtp_host = settings.get('smtp_host', '')
    smtp_port = settings.get('smtp_port', '587')
    smtp_user = settings.get('smtp_user', '')
    smtp_pass = settings.get('smtp_pass', '')
    smtp_from = settings.get('smtp_from', smtp_user)
    shop_name = settings.get('shop_name', 'AMILCAR')
    if not smtp_host or not smtp_user:
        flash("Configurez les paramètres SMTP dans Paramètres", "error")
        return redirect("/notifications")
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    with get_db() as conn:
        appts = conn.execute(
            "SELECT a.id, a.date, COALESCE(a.time, ''), a.service, cu.name, COALESCE(cu.email, '') "
            "FROM appointments a JOIN cars ca ON a.car_id = ca.id JOIN customers cu ON ca.customer_id = cu.id "
            "WHERE a.date = ? AND a.status = 'pending'", (tomorrow,)).fetchall()
    sent = 0
    errors = 0
    for a in appts:
        email = a[5]
        if not email:
            continue
        time_str = f" à {a[2]}" if a[2] else ""
        body = f"Bonjour {a[4]},\n\nRappel de votre rendez-vous chez {shop_name} demain{time_str} pour : {a[3]}.\n\nÀ bientôt !\n{shop_name}"
        msg = MIMEText(body, 'plain', 'utf-8')
        msg['From'] = smtp_from
        msg['To'] = email
        msg['Subject'] = f'Rappel RDV — {shop_name}'
        try:
            server = smtplib.SMTP(smtp_host, int(smtp_port), timeout=10)
            server.starttls()
            server.login(smtp_user, smtp_pass)
            server.send_message(msg)
            server.quit()
            sent += 1
        except Exception:
            errors += 1
    log_activity('Email Reminders', f'{sent} envoyés, {errors} erreurs pour {tomorrow}')
    flash(f"Emails envoyés : {sent} succès, {errors} erreurs", "success" if errors == 0 else "warning")
    return redirect("/notifications")



# ─── WhatsApp Integration ───
@comms_bp.route("/whatsapp_reminder/<int:appointment_id>")
@login_required
def whatsapp_reminder(appointment_id):
    with get_db() as conn:
        appt = conn.execute(
            "SELECT a.date, COALESCE(a.time, ''), a.service, cu.name, cu.phone, cu.id "
            "FROM appointments a JOIN cars ca ON a.car_id = ca.id JOIN customers cu ON ca.customer_id = cu.id "
            "WHERE a.id = ?", (appointment_id,)).fetchone()
    if not appt:
        flash("Rendez-vous introuvable", "error")
        return redirect("/appointments")
    phone = appt[4].strip().replace(' ', '').replace('-', '')
    if phone.startswith('0'):
        phone = '216' + phone[1:]
    elif not phone.startswith('+') and not phone.startswith('216'):
        phone = '216' + phone
    phone = phone.replace('+', '')
    settings = get_all_settings()
    shop_name = settings.get('shop_name', 'AMILCAR')
    time_str = f" à {appt[1]}" if appt[1] else ""
    message = f"Bonjour {appt[3]}, rappel de votre RDV chez {shop_name} le {appt[0]}{time_str} pour : {appt[2]}. À bientôt !"
    # Log communication
    with get_db() as conn:
        conn.execute("INSERT INTO communication_log (customer_id, type, subject, message, sent_by) VALUES (?,?,?,?,?)",
            (appt[5], 'whatsapp', f'Rappel RDV #{appointment_id}', message, session.get('username', '')))
        conn.commit()
    import urllib.parse
    wa_url = f"https://wa.me/{phone}?text={urllib.parse.quote(message)}"
    log_activity('WhatsApp', f'Reminder sent for appointment #{appointment_id}')
    return redirect(wa_url)



@comms_bp.route("/whatsapp_unpaid/<int:invoice_id>")
@login_required
def whatsapp_unpaid(invoice_id):
    with get_db() as conn:
        inv = conn.execute(
            "SELECT i.amount, COALESCE(i.paid_amount, 0), cu.name, cu.phone, cu.id "
            "FROM invoices i JOIN appointments a ON i.appointment_id = a.id "
            "JOIN cars ca ON a.car_id = ca.id JOIN customers cu ON ca.customer_id = cu.id "
            "WHERE i.id = ?", (invoice_id,)).fetchone()
    if not inv:
        flash("Facture introuvable", "error")
        return redirect("/invoices")
    phone = inv[3].strip().replace(' ', '').replace('-', '')
    if phone.startswith('0'):
        phone = '216' + phone[1:]
    elif not phone.startswith('+') and not phone.startswith('216'):
        phone = '216' + phone
    phone = phone.replace('+', '')
    settings = get_all_settings()
    shop_name = settings.get('shop_name', 'AMILCAR')
    remaining = inv[0] - inv[1]
    message = f"Bonjour {inv[2]}, nous vous rappelons qu'une facture de {remaining:.0f} DT est en attente chez {shop_name}. Merci de régulariser votre situation. Cordialement."
    with get_db() as conn:
        conn.execute("INSERT INTO communication_log (customer_id, type, subject, message, sent_by) VALUES (?,?,?,?,?)",
            (inv[4], 'whatsapp', f'Rappel facture #{invoice_id}', message, session.get('username', '')))
        conn.commit()
    import urllib.parse
    wa_url = f"https://wa.me/{phone}?text={urllib.parse.quote(message)}"
    log_activity('WhatsApp', f'Unpaid reminder for invoice #{invoice_id}')
    return redirect(wa_url)



@comms_bp.route("/whatsapp_bulk_reminders", methods=["POST"])
@login_required
def whatsapp_bulk_reminders():
    """Generate WhatsApp links for tomorrow's appointments"""
    from datetime import date, timedelta
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    with get_db() as conn:
        appts = conn.execute(
            "SELECT a.id, a.date, COALESCE(a.time, ''), a.service, cu.name, cu.phone "
            "FROM appointments a JOIN cars ca ON a.car_id = ca.id JOIN customers cu ON ca.customer_id = cu.id "
            "WHERE a.date = ? AND a.status = 'pending'", (tomorrow,)).fetchall()
    if not appts:
        flash("Aucun rendez-vous demain", "info")
        return redirect("/notifications")
    # Redirect to the first one; others shown as links in notifications page
    flash(f"{len(appts)} clients à contacter pour demain", "success")
    return redirect("/notifications")



# ─── Communication Log ───
@comms_bp.route("/communication_log/<int:customer_id>")
@login_required
def communication_log_view(customer_id):
    with get_db() as conn:
        customer = conn.execute("SELECT * FROM customers WHERE id = ?", (customer_id,)).fetchone()
        if not customer:
            flash("Client introuvable", "error")
            return redirect("/customers")
        logs = conn.execute(
            "SELECT * FROM communication_log WHERE customer_id = ? ORDER BY created_at DESC", (customer_id,)).fetchall()
    return render_template("communication_log.html", customer=customer, logs=logs)



@comms_bp.route("/send_rating_link/<int:appointment_id>")
@login_required
def send_rating_link(appointment_id):
    import hashlib
    with get_db() as conn:
        appt = conn.execute(
            "SELECT a.date, cu.phone, cu.name, a.service, cu.id "
            "FROM appointments a JOIN cars ca ON a.car_id = ca.id JOIN customers cu ON ca.customer_id = cu.id "
            "WHERE a.id = ? AND a.status = 'completed'", (appointment_id,)).fetchone()
    if not appt:
        flash("Rendez-vous introuvable ou non terminé", "error")
        return redirect("/appointments")
    token = hashlib.sha256(f"rate_{appointment_id}_{appt[0]}".encode()).hexdigest()[:24]
    rate_url = f"{request.host_url}rate/{token}"
    phone = appt[1].strip().replace(' ', '').replace('-', '')
    if phone.startswith('0'):
        phone = '216' + phone[1:]
    elif not phone.startswith('+') and not phone.startswith('216'):
        phone = '216' + phone
    phone = phone.replace('+', '')
    settings = get_all_settings()
    shop_name = settings.get('shop_name', 'AMILCAR')
    import urllib.parse
    message = f"Bonjour {appt[2]}, merci d'avoir choisi {shop_name} ! Nous aimerions votre avis sur le service ({appt[3]}). Évaluez-nous ici : {rate_url}"
    wa_url = f"https://wa.me/{phone}?text={urllib.parse.quote(message)}"
    # Log the communication
    with get_db() as conn:
        conn.execute("INSERT INTO communication_log (customer_id, type, subject, message, sent_by) VALUES (?,?,?,?,?)",
            (appt[4], 'whatsapp', f'Demande évaluation RDV #{appointment_id}', message, session.get('username', '')))
        conn.commit()
    log_activity('Rating Link', f'Sent for appointment #{appointment_id}')
    return redirect(wa_url)



# ─── Phase 6 Feature 2: Bulk WhatsApp Messaging ───
@comms_bp.route("/bulk_message")
@login_required
def bulk_message():
    with get_db() as conn:
        customers = conn.execute("SELECT id, name, phone FROM customers ORDER BY name").fetchall()
        tiers = conn.execute("SELECT DISTINCT tier FROM reward_points").fetchall()
    return render_template("bulk_message.html", customers=customers, tiers=[t[0] for t in tiers])



@comms_bp.route("/bulk_message/send", methods=["POST"])
@login_required
def send_bulk_message():
    import urllib.parse
    message_template = request.form.get("message", "").strip()
    target = request.form.get("target", "all")
    tier_filter = request.form.get("tier", "")
    if not message_template:
        flash("Le message ne peut pas être vide", "error")
        return redirect("/bulk_message")
    with get_db() as conn:
        if target == 'tier' and tier_filter:
            customers = conn.execute(
                "SELECT cu.id, cu.name, cu.phone FROM customers cu "
                "JOIN reward_points rp ON rp.customer_id = cu.id WHERE rp.tier = ?",
                (tier_filter,)).fetchall()
        elif target == 'active':
            from datetime import date, timedelta
            d90 = (date.today() - timedelta(days=90)).isoformat()
            customers = conn.execute(
                "SELECT DISTINCT cu.id, cu.name, cu.phone FROM customers cu "
                "JOIN cars ca ON ca.customer_id=cu.id JOIN appointments a ON a.car_id=ca.id "
                "WHERE a.date >= ?", (d90,)).fetchall()
        elif target == 'inactive':
            from datetime import date, timedelta
            d90 = (date.today() - timedelta(days=90)).isoformat()
            customers = conn.execute(
                "SELECT cu.id, cu.name, cu.phone FROM customers cu "
                "WHERE cu.id NOT IN (SELECT DISTINCT ca.customer_id FROM cars ca "
                "JOIN appointments a ON a.car_id=ca.id WHERE a.date >= ?)", (d90,)).fetchall()
        else:
            customers = conn.execute("SELECT id, name, phone FROM customers").fetchall()
        # Log communications
        for c in customers:
            conn.execute("INSERT INTO communication_log (customer_id, type, subject, message, sent_by) VALUES (?,?,?,?,?)",
                (c[0], 'whatsapp_bulk', 'Message groupé', message_template.replace('{name}', c[1]), session.get('username','')))
        conn.commit()
    settings = get_all_settings()
    shop_name = settings.get('shop_name', 'AMILCAR')
    links = []
    for c in customers:
        phone = c[2].strip().replace(' ','').replace('-','')
        if phone.startswith('0'):
            phone = '216' + phone[1:]
        elif not phone.startswith('+') and not phone.startswith('216'):
            phone = '216' + phone
        phone = phone.replace('+','')
        msg = message_template.replace('{name}', c[1]).replace('{shop}', shop_name)
        links.append({'name': c[1], 'phone': c[2], 'url': f"https://wa.me/{phone}?text={urllib.parse.quote(msg)}"})
    log_activity('Bulk Message', f'Sent to {len(links)} customers ({target})')
    flash(f"Message préparé pour {len(links)} clients", "success")
    return render_template("bulk_message_results.html", links=links, count=len(links))



# ─── Phase 7 Feature 2: Email Notifications ───
@comms_bp.route("/email_settings", methods=["GET", "POST"])
@login_required
def email_settings():
    with get_db() as conn:
        if request.method == "POST":
            for key in ['smtp_server', 'smtp_port', 'smtp_email', 'smtp_password', 'smtp_from_name']:
                val = request.form.get(key, '')
                conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, val))
            conn.commit()
            flash("Paramètres email enregistrés !", "success")
            return redirect("/email_settings")
        settings = {}
        for row in conn.execute("SELECT key, value FROM settings WHERE key LIKE 'smtp_%'").fetchall():
            settings[row[0]] = row[1]
        logs = conn.execute("SELECT * FROM email_log ORDER BY created_at DESC LIMIT 50").fetchall()
    return render_template("email_settings.html", settings=settings, logs=logs)



@comms_bp.route("/send_email/<int:customer_id>", methods=["POST"])
@login_required
def send_email_to_customer(customer_id):
    with get_db() as conn:
        customer = conn.execute("SELECT * FROM customers WHERE id=?", (customer_id,)).fetchone()
        if not customer:
            flash("Client introuvable", "danger")
            return redirect("/customers")
        email = customer[11] if len(customer) > 11 and customer[11] else ''
        if not email:
            flash("Ce client n'a pas d'adresse email", "warning")
            return redirect(f"/customer/{customer_id}")
        subject = request.form.get('subject', '')
        body = request.form.get('body', '')
        smtp_server = conn.execute("SELECT value FROM settings WHERE key='smtp_server'").fetchone()
        smtp_port = conn.execute("SELECT value FROM settings WHERE key='smtp_port'").fetchone()
        smtp_email = conn.execute("SELECT value FROM settings WHERE key='smtp_email'").fetchone()
        smtp_pass = conn.execute("SELECT value FROM settings WHERE key='smtp_password'").fetchone()
        smtp_name = conn.execute("SELECT value FROM settings WHERE key='smtp_from_name'").fetchone()
        status = 'failed'
        if smtp_server and smtp_email and smtp_pass:
            try:
                import smtplib
                from email.mime.text import MIMEText
                from email.mime.multipart import MIMEMultipart
                msg = MIMEMultipart()
                msg['From'] = f"{smtp_name[0] if smtp_name else 'AMILCAR'} <{smtp_email[0]}>"
                msg['To'] = email
                msg['Subject'] = subject
                msg.attach(MIMEText(body, 'html'))
                with smtplib.SMTP(smtp_server[0], int(smtp_port[0] if smtp_port else 587)) as server:
                    server.starttls()
                    server.login(smtp_email[0], smtp_pass[0])
                    server.send_message(msg)
                status = 'sent'
                flash("Email envoyé avec succès !", "success")
            except Exception as e:
                flash(f"Erreur d'envoi: {str(e)}", "danger")
        else:
            flash("Paramètres SMTP non configurés. Allez dans Email Settings.", "warning")
        conn.execute("INSERT INTO email_log (customer_id, to_email, subject, body, status) VALUES (?,?,?,?,?)",
                     (customer_id, email, subject, body, status))
        conn.commit()
    return redirect(f"/customer/{customer_id}")



# ─── Phase 7 Feature 4: SMS Notifications ───
@comms_bp.route("/sms_settings", methods=["GET", "POST"])
@login_required
def sms_settings():
    with get_db() as conn:
        if request.method == "POST":
            for key in ['sms_provider', 'sms_api_key', 'sms_sender_id']:
                val = request.form.get(key, '')
                conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, val))
            conn.commit()
            flash("Paramètres SMS enregistrés !", "success")
            return redirect("/sms_settings")
        settings = {}
        for row in conn.execute("SELECT key, value FROM settings WHERE key LIKE 'sms_%'").fetchall():
            settings[row[0]] = row[1]
    return render_template("sms_settings.html", settings=settings)



@comms_bp.route("/send_sms/<int:customer_id>", methods=["POST"])
@login_required
def send_sms(customer_id):
    with get_db() as conn:
        customer = conn.execute("SELECT * FROM customers WHERE id=?", (customer_id,)).fetchone()
        if not customer:
            flash("Client introuvable", "danger")
            return redirect("/customers")
        message = request.form.get('message', '')
        phone = customer[2]
        # Log & simulate (real API integration placeholder)
        conn.execute("INSERT INTO communication_log (customer_id, type, subject, message, sent_by) VALUES (?,?,?,?,?)",
                     (customer_id, 'SMS', 'SMS', message, session.get('username', '')))
        conn.commit()
        flash(f"SMS envoyé à {customer[1]} ({phone})", "success")
    return redirect(f"/customer/{customer_id}")



@comms_bp.route("/sms_reminder_batch", methods=["POST"])
@login_required
def sms_reminder_batch():
    with get_db() as conn:
        from datetime import date, timedelta
        tomorrow = (date.today() + timedelta(days=1)).isoformat()
        appts = conn.execute("""
            SELECT a.id, a.date, a.time, a.service, c.name, c.phone
            FROM appointments a
            JOIN cars cr ON a.car_id = cr.id
            JOIN customers c ON cr.customer_id = c.id
            WHERE a.date = ? AND a.status IN ('pending','Confirmé')
        """, (tomorrow,)).fetchall()
        count = 0
        for a in appts:
            msg = f"Rappel AMILCAR: Votre RDV demain {a[1]} à {a[2]} pour {a[3]}. À bientôt!"
            conn.execute("INSERT INTO communication_log (customer_id, type, subject, message, sent_by) VALUES ((SELECT customer_id FROM cars WHERE id=(SELECT car_id FROM appointments WHERE id=?)), 'SMS', 'Rappel RDV', ?, ?)",
                        (a[0], msg, session.get('username', '')))
            count += 1
        conn.commit()
    flash(f"{count} rappels SMS envoyés pour demain", "success")
    return redirect("/appointments")



# ─── Phase 8 Feature 3: WhatsApp API Integration ───
@comms_bp.route("/whatsapp_settings", methods=["GET", "POST"])
@login_required
def whatsapp_settings():
    with get_db() as conn:
        if request.method == "POST":
            for key in ['wa_api_url', 'wa_api_token', 'wa_phone_id', 'wa_template_reminder', 'wa_template_ready', 'wa_template_invoice']:
                val = request.form.get(key, '')
                conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, val))
            conn.commit()
            flash("Paramètres WhatsApp enregistrés !", "success")
            return redirect("/whatsapp_settings")
        settings = {}
        for row in conn.execute("SELECT key, value FROM settings WHERE key LIKE 'wa_%'").fetchall():
            settings[row[0]] = row[1]
        logs = conn.execute("SELECT * FROM communication_log WHERE type='WhatsApp' ORDER BY created_at DESC LIMIT 50").fetchall()
    return render_template("whatsapp_settings.html", settings=settings, logs=logs)



@comms_bp.route("/whatsapp_send/<int:customer_id>", methods=["POST"])
@login_required
def whatsapp_send(customer_id):
    with get_db() as conn:
        customer = conn.execute("SELECT * FROM customers WHERE id=?", (customer_id,)).fetchone()
        if not customer:
            flash("Client introuvable", "danger")
            return redirect("/customers")
        message = request.form.get('message', '')
        template = request.form.get('template', '')
        phone = customer[2].replace(' ', '').replace('+', '')
        wa_url = conn.execute("SELECT value FROM settings WHERE key='wa_api_url'").fetchone()
        wa_token = conn.execute("SELECT value FROM settings WHERE key='wa_api_token'").fetchone()
        if wa_url and wa_token and wa_url[0] and wa_token[0]:
            try:
                import urllib.request, json
                payload = json.dumps({"messaging_product": "whatsapp", "to": phone, "type": "text", "text": {"body": message}}).encode()
                req = urllib.request.Request(wa_url[0], data=payload, headers={
                    'Authorization': f'Bearer {wa_token[0]}', 'Content-Type': 'application/json'})
                urllib.request.urlopen(req, timeout=10)
                status = 'sent'
                flash(f"WhatsApp envoyé à {customer[1]} !", "success")
            except Exception as e:
                status = 'failed'
                flash(f"Erreur WhatsApp: {str(e)}", "danger")
        else:
            status = 'manual'
            flash("API non configurée — utilisez le lien WhatsApp manuel", "warning")
        conn.execute("INSERT INTO communication_log (customer_id, type, subject, message, sent_by) VALUES (?,?,?,?,?)",
                     (customer_id, 'WhatsApp', template or 'Message', message, session.get('username', '')))
        conn.commit()
    return redirect(f"/customer/{customer_id}")



@comms_bp.route("/whatsapp_batch_remind", methods=["POST"])
@login_required
def whatsapp_batch_remind():
    with get_db() as conn:
        from datetime import date, timedelta
        tomorrow = (date.today() + timedelta(days=1)).isoformat()
        appts = conn.execute("""
            SELECT a.id, a.date, a.time, a.service, c.name, c.phone, c.id
            FROM appointments a JOIN cars cr ON a.car_id=cr.id JOIN customers c ON cr.customer_id=c.id
            WHERE a.date=? AND a.status IN ('pending','Confirmé')
        """, (tomorrow,)).fetchall()
        count = 0
        for a in appts:
            msg = f"Bonjour {a[4]} 👋\nRappel: votre RDV demain {a[1]} à {a[2]} pour *{a[3]}*.\nÀ bientôt chez AMILCAR! 🚗✨"
            conn.execute("INSERT INTO communication_log (customer_id, type, subject, message, sent_by) VALUES (?,?,?,?,?)",
                        (a[6], 'WhatsApp', 'Rappel RDV', msg, session.get('username', '')))
            count += 1
        conn.commit()
    flash(f"{count} rappels WhatsApp préparés pour demain", "success")
    return redirect("/appointments")



# ─── Phase 8 Feature 10: Enhanced PWA Push Notifications ───
@comms_bp.route("/push_settings", methods=["GET", "POST"])
@login_required
def push_settings():
    with get_db() as conn:
        if request.method == "POST":
            for key in ['vapid_public', 'vapid_private', 'vapid_email']:
                val = request.form.get(key, '')
                conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, val))
            conn.commit()
            flash("Paramètres Push sauvegardés !", "success")
            return redirect("/push_settings")
        settings = {}
        for row in conn.execute("SELECT key, value FROM settings WHERE key LIKE 'vapid_%'").fetchall():
            settings[row[0]] = row[1]
    return render_template("push_settings.html", settings=settings)



# ─── 2. Marketing Campaigns (Auto) ───
@comms_bp.route("/marketing_campaigns")
@login_required
@admin_required
def marketing_campaigns():
    with get_db() as conn:
        campaigns = conn.execute("SELECT * FROM marketing_campaigns ORDER BY created_at DESC").fetchall()
        segments = conn.execute("SELECT segment, COUNT(*) FROM rfm_segments GROUP BY segment").fetchall()
    return render_template("marketing_campaigns.html", campaigns=campaigns, segments=segments)



@comms_bp.route("/marketing_campaign/add", methods=["POST"])
@login_required
@admin_required
def marketing_campaign_add():
    name = request.form.get("name", "").strip()
    ctype = request.form.get("type", "manual")
    trigger_type = request.form.get("trigger_type", "")
    trigger_value = request.form.get("trigger_value", "")
    target = request.form.get("target_segment", "all")
    message = request.form.get("message_template", "").strip()
    channel = request.form.get("channel", "sms")
    if name and message:
        with get_db() as conn:
            conn.execute("""INSERT INTO marketing_campaigns (name, type, trigger_type, trigger_value,
                target_segment, message_template, channel) VALUES (?,?,?,?,?,?,?)""",
                (name, ctype, trigger_type, trigger_value, target, message, channel))
            conn.commit()
        flash("Campagne créée !", "success")
    return redirect("/marketing_campaigns")



@comms_bp.route("/marketing_campaign/run/<int:cid>")
@login_required
@admin_required
def marketing_campaign_run(cid):
    from datetime import date
    with get_db() as conn:
        camp = conn.execute("SELECT * FROM marketing_campaigns WHERE id=?", (cid,)).fetchone()
        if not camp:
            flash("Campagne introuvable", "error")
            return redirect("/marketing_campaigns")
        segment = camp[5]
        if segment == 'all':
            customers = conn.execute("SELECT id, name, phone FROM customers").fetchall()
        else:
            customers = conn.execute("""SELECT c.id, c.name, c.phone FROM customers c
                JOIN rfm_segments r ON r.customer_id=c.id WHERE r.segment=?""", (segment,)).fetchall()
        count = 0
        for c in customers:
            already = conn.execute("SELECT id FROM campaign_log WHERE campaign_id=? AND customer_id=? AND date(sent_at)=?",
                                  (cid, c[0], date.today().isoformat())).fetchone()
            if not already:
                conn.execute("INSERT INTO campaign_log (campaign_id, customer_id) VALUES (?,?)", (cid, c[0]))
                count += 1
        conn.execute("UPDATE marketing_campaigns SET sent_count=sent_count+?, last_run=? WHERE id=?",
                    (count, date.today().isoformat(), cid))
        conn.commit()
    flash(f"Campagne envoyée à {count} clients !", "success")
    return redirect("/marketing_campaigns")



@comms_bp.route("/marketing_campaign/toggle/<int:cid>")
@login_required
@admin_required
def marketing_campaign_toggle(cid):
    with get_db() as conn:
        camp = conn.execute("SELECT status FROM marketing_campaigns WHERE id=?", (cid,)).fetchone()
        if camp:
            new_status = 'paused' if camp[0] == 'active' else 'active'
            conn.execute("UPDATE marketing_campaigns SET status=? WHERE id=?", (new_status, cid))
            conn.commit()
    return redirect("/marketing_campaigns")



# ─── 8. Seasonal Campaigns ───
@comms_bp.route("/seasonal_campaigns")
@login_required
@admin_required
def seasonal_campaigns():
    with get_db() as conn:
        campaigns = conn.execute("SELECT * FROM seasonal_campaigns ORDER BY start_date DESC").fetchall()
    return render_template("seasonal_campaigns.html", campaigns=campaigns)



@comms_bp.route("/seasonal_campaign/add", methods=["POST"])
@login_required
@admin_required
def seasonal_campaign_add():
    name = request.form.get("name", "").strip()
    season = request.form.get("season", "summer")
    start = request.form.get("start_date", "")
    end = request.form.get("end_date", "")
    discount = request.form.get("discount_percent", 0, type=float)
    services = request.form.get("target_services", "")
    message = request.form.get("message", "").strip()
    if name and start and end:
        with get_db() as conn:
            conn.execute("""INSERT INTO seasonal_campaigns (name, season, start_date, end_date, discount_percent, target_services, message)
                VALUES (?,?,?,?,?,?,?)""", (name, season, start, end, discount, services, message))
            conn.commit()
        flash("Campagne saisonnière créée !", "success")
    return redirect("/seasonal_campaigns")



@comms_bp.route("/seasonal_campaign/launch/<int:cid>")
@login_required
@admin_required
def seasonal_campaign_launch(cid):
    with get_db() as conn:
        conn.execute("UPDATE seasonal_campaigns SET status='active' WHERE id=?", (cid,))
        # Count eligible customers
        count = conn.execute("SELECT COUNT(*) FROM customers").fetchone()[0]
        conn.execute("UPDATE seasonal_campaigns SET sent_count=? WHERE id=?", (count, cid))
        conn.commit()
    flash(f"Campagne lancée pour {count} clients !", "success")
    return redirect("/seasonal_campaigns")



# ─── 10. Centre de Notifications Intelligent ───

@comms_bp.route("/notifications")
@login_required
def notifications_center_view():
    user_id = session.get('user_id')
    with get_db() as conn:
        notifs = conn.execute("""SELECT * FROM notifications_center 
            WHERE user_id=? OR user_id=0 
            ORDER BY created_at DESC LIMIT 100""", (user_id,)).fetchall()
        unread = conn.execute("""SELECT COUNT(*) FROM notifications_center 
            WHERE (user_id=? OR user_id=0) AND is_read=0""", (user_id,)).fetchone()[0]
        # Auto-generate notifications
        from datetime import date, timedelta
        today = date.today().isoformat()
        tomorrow = (date.today() + timedelta(days=1)).isoformat()
        # Low stock alerts
        low_stock = conn.execute("SELECT COUNT(*) FROM inventory WHERE quantity <= min_quantity AND min_quantity > 0").fetchone()[0]
        if low_stock > 0:
            existing = conn.execute("""SELECT id FROM notifications_center 
                WHERE notif_type='stock' AND DATE(created_at)=? AND user_id=?""", (today, user_id)).fetchone()
            if not existing:
                conn.execute("""INSERT INTO notifications_center (user_id, title, message, notif_type, link) 
                    VALUES (?,?,?,?,?)""", (user_id, f"⚠️ {low_stock} article(s) en stock bas",
                    "Vérifiez l'inventaire", "stock", "/inventory"))
        # Tomorrow's appointments
        tmrw_count = conn.execute("SELECT COUNT(*) FROM appointments WHERE date=? AND status='pending'", (tomorrow,)).fetchone()[0]
        if tmrw_count > 0:
            existing = conn.execute("""SELECT id FROM notifications_center 
                WHERE notif_type='appointment' AND DATE(created_at)=? AND user_id=?""", (today, user_id)).fetchone()
            if not existing:
                conn.execute("""INSERT INTO notifications_center (user_id, title, message, notif_type, link) 
                    VALUES (?,?,?,?,?)""", (user_id, f"📅 {tmrw_count} RDV demain",
                    "Préparez les ressources", "appointment", "/appointments"))
        # Overdue invoices
        overdue = conn.execute("""SELECT COUNT(*) FROM invoices 
            WHERE status IN ('unpaid', 'partial') AND date < ?""", (today,)).fetchone()[0]
        if overdue > 0:
            existing = conn.execute("""SELECT id FROM notifications_center 
                WHERE notif_type='payment' AND DATE(created_at)=? AND user_id=?""", (today, user_id)).fetchone()
            if not existing:
                conn.execute("""INSERT INTO notifications_center (user_id, title, message, notif_type, link) 
                    VALUES (?,?,?,?,?)""", (user_id, f"💰 {overdue} facture(s) en retard",
                    "Relancez les paiements", "payment", "/ar_aging"))
        # Expiring documents
        week_later = (date.today() + timedelta(days=7)).isoformat()
        exp_docs = conn.execute("SELECT COUNT(*) FROM vehicle_documents WHERE expiry_date BETWEEN ? AND ?", (today, week_later)).fetchone()[0]
        if exp_docs > 0:
            existing = conn.execute("""SELECT id FROM notifications_center 
                WHERE notif_type='document' AND DATE(created_at)=? AND user_id=?""", (today, user_id)).fetchone()
            if not existing:
                conn.execute("""INSERT INTO notifications_center (user_id, title, message, notif_type, link) 
                    VALUES (?,?,?,?,?)""", (user_id, f"📄 {exp_docs} document(s) expire(nt) bientôt",
                    "Vérifiez les documents véhicules", "document", "/customers"))
        conn.commit()
    return render_template("notification_center.html", notifs=notifs, unread=unread)



@comms_bp.route("/notifications/read/<int:nid>")
@login_required
def mark_notification_read(nid):
    with get_db() as conn:
        conn.execute("UPDATE notifications_center SET is_read=1 WHERE id=?", (nid,))
        conn.commit()
    link = request.args.get("redirect", "/notifications")
    return redirect(link)



@comms_bp.route("/notifications/read_all")
@login_required
def mark_all_notifications_read():
    user_id = session.get('user_id')
    with get_db() as conn:
        conn.execute("UPDATE notifications_center SET is_read=1 WHERE user_id=? OR user_id=0", (user_id,))
        conn.commit()
    return redirect("/notifications")



# ─── 1. WhatsApp Business Hub ───

@comms_bp.route("/whatsapp_hub")
@login_required
def whatsapp_hub():
    with get_db() as conn:
        logs = conn.execute("""SELECT w.*, c.name as customer_name
            FROM whatsapp_logs w LEFT JOIN customers c ON w.customer_id=c.id
            ORDER BY w.created_at DESC LIMIT 200""").fetchall()
        stats = {
            'total': conn.execute("SELECT COUNT(*) FROM whatsapp_logs").fetchone()[0],
            'sent': conn.execute("SELECT COUNT(*) FROM whatsapp_logs WHERE status='sent'").fetchone()[0],
            'pending': conn.execute("SELECT COUNT(*) FROM whatsapp_logs WHERE status='pending'").fetchone()[0],
            'today': conn.execute("SELECT COUNT(*) FROM whatsapp_logs WHERE DATE(created_at)=DATE('now')").fetchone()[0],
        }
        templates = [
            {'name': 'rdv_confirmation', 'label': 'Confirmation RDV', 'icon': '✅'},
            {'name': 'rdv_reminder', 'label': 'Rappel 24h', 'icon': '⏰'},
            {'name': 'service_ready', 'label': 'Véhicule prêt', 'icon': '🚗'},
            {'name': 'review_request', 'label': 'Demande avis', 'icon': '⭐'},
            {'name': 'birthday', 'label': 'Anniversaire', 'icon': '🎂'},
            {'name': 'promotion', 'label': 'Promotion', 'icon': '🎁'},
            {'name': 'treatment_expiry', 'label': 'Traitement expire', 'icon': '🛡️'},
        ]
    return render_template("whatsapp_hub.html", logs=logs, stats=stats, templates=templates)



@comms_bp.route("/whatsapp_hub_send", methods=["POST"])
@login_required
def whatsapp_hub_send():
    with get_db() as conn:
        customer_id = request.form.get("customer_id", 0, type=int)
        template = request.form.get("template_name", "")
        custom_msg = request.form.get("custom_message", "")
        customer = conn.execute("SELECT * FROM customers WHERE id=?", (customer_id,)).fetchone()
        if not customer:
            flash("Client non trouvé", "danger")
            return redirect("/whatsapp_hub")
        phone = customer['phone'] or ''
        messages = {
            'rdv_confirmation': f"✅ Bonjour {customer['name']}, votre RDV chez AMILCAR est confirmé !",
            'rdv_reminder': f"⏰ Rappel : votre RDV chez AMILCAR est demain. On vous attend !",
            'service_ready': f"🚗 {customer['name']}, votre véhicule est prêt ! Venez le récupérer.",
            'review_request': f"⭐ {customer['name']}, comment était votre expérience chez AMILCAR ? Donnez-nous votre avis !",
            'birthday': f"🎂 Joyeux anniversaire {customer['name']} ! -20% sur votre prochain soin.",
            'promotion': f"🎁 {customer['name']}, offre spéciale AMILCAR ! Profitez-en maintenant.",
            'treatment_expiry': f"🛡️ {customer['name']}, votre traitement arrive à expiration. Renouvelez-le !",
        }
        msg_text = custom_msg if custom_msg else messages.get(template, f"Message AMILCAR pour {customer['name']}")
        conn.execute("""INSERT INTO whatsapp_logs (customer_id, phone, message_type, message_text, status, template_name)
            VALUES (?,?,?,?,?,?)""", (customer_id, phone, template or 'custom', msg_text, 'pending', template))
        conn.commit()
    flash(f"Message WhatsApp préparé pour {customer['name']}", "success")
    return redirect("/whatsapp_hub")



@comms_bp.route("/whatsapp_bulk", methods=["POST"])
@login_required
def whatsapp_bulk():
    template = request.form.get("template_name", "")
    target = request.form.get("target", "all")
    with get_db() as conn:
        if target == "tomorrow_rdv":
            from datetime import date, timedelta
            tomorrow = (date.today() + timedelta(days=1)).isoformat()
            customers = conn.execute("""SELECT DISTINCT c.id, c.name, c.phone FROM customers c
                JOIN appointments a ON a.customer_id=c.id WHERE a.date=? AND a.status='pending'""", (tomorrow,)).fetchall()
        elif target == "birthday_month":
            from datetime import date
            month = date.today().strftime('%m')
            customers = conn.execute("SELECT id, name, phone FROM customers WHERE substr(birthday,6,2)=?", (month,)).fetchall()
        else:
            customers = conn.execute("SELECT id, name, phone FROM customers WHERE phone != ''").fetchall()
        count = 0
        for c in customers:
            if c['phone']:
                msg = f"Bonjour {c['name']}, message AMILCAR"
                conn.execute("""INSERT INTO whatsapp_logs (customer_id, phone, message_type, message_text, status, template_name)
                    VALUES (?,?,?,?,?,?)""", (c['id'], c['phone'], template or 'bulk', msg, 'pending', template))
                count += 1
        conn.commit()
    flash(f"{count} messages WhatsApp préparés", "success")
    return redirect("/whatsapp_hub")



@comms_bp.route('/channel_inbox/send', methods=['POST'])
@login_required
def channel_inbox_send():
    with get_db() as conn:
        conn.execute("""INSERT INTO channel_inbox
            (customer_id, customer_name, channel, direction, message, status)
            VALUES (?,?,?,?,?,?)""",
            (int(request.form.get('customer_id', 0)), request.form.get('customer_name', ''),
             request.form['channel'], 'outgoing', request.form['message'], 'sent'))
        conn.commit()
    flash("Message envoyé", "success")
    return redirect("/channel_inbox")

# ── 6. Business Health Score ──
@comms_bp.route('/business_health')
@login_required
def business_health():
    with get_db() as conn:
        today = datetime.now().strftime('%Y-%m-%d')
        month_start = datetime.now().strftime('%Y-%m-01')
        last_month_start = (datetime.now().replace(day=1) - timedelta(days=1)).strftime('%Y-%m-01')
        last_month_end = (datetime.now().replace(day=1) - timedelta(days=1)).strftime('%Y-%m-%d')

        # Revenue score (0-100)
        current_revenue = conn.execute(
            "SELECT COALESCE(SUM(amount), 0) FROM invoices WHERE created_at >= ? AND status != 'cancelled'",
            (month_start,)).fetchone()[0]
        last_revenue = conn.execute(
            "SELECT COALESCE(SUM(amount), 0) FROM invoices WHERE created_at >= ? AND created_at <= ? AND status != 'cancelled'",
            (last_month_start, last_month_end)).fetchone()[0]
        revenue_score = min(100, (current_revenue / max(last_revenue, 1)) * 100)

        # Satisfaction score (NPS-based)
        nps_data = conn.execute("SELECT AVG(score) as avg_nps FROM nps_surveys WHERE created_at >= ?", (month_start,)).fetchone()
        satisfaction_score = min(100, ((nps_data['avg_nps'] or 7) / 10) * 100)

        # Efficiency score
        timers = conn.execute("SELECT AVG(efficiency_pct) FROM service_timer WHERE started_at >= ?", (month_start,)).fetchone()[0]
        efficiency_score = min(100, timers or 75)

        # Retention score
        total_customers = conn.execute("SELECT COUNT(*) FROM customers").fetchone()[0]
        returning = conn.execute("SELECT COUNT(*) FROM customers WHERE total_visits > 1").fetchone()[0]
        retention_score = (returning / max(total_customers, 1)) * 100

        # Growth score
        new_this_month = conn.execute(
            "SELECT COUNT(*) FROM customers WHERE last_visit >= ?", (month_start,)).fetchone()[0]
        new_last_month = conn.execute(
            "SELECT COUNT(*) FROM customers WHERE last_visit >= ? AND last_visit < ?",
            (last_month_start, month_start)).fetchone()[0]
        growth_score = min(100, (new_this_month / max(new_last_month, 1)) * 100)

        overall = (revenue_score * 0.3 + satisfaction_score * 0.2 + efficiency_score * 0.2 +
                   retention_score * 0.15 + growth_score * 0.15)

        # Save to history
        conn.execute("""INSERT OR REPLACE INTO business_health_score
            (date, overall_score, revenue_score, satisfaction_score,
             efficiency_score, retention_score, growth_score)
            VALUES (?,?,?,?,?,?,?)""",
            (today, overall, revenue_score, satisfaction_score,
             efficiency_score, retention_score, growth_score))
        conn.commit()

        # History for chart
        history = conn.execute(
            "SELECT * FROM business_health_score ORDER BY date DESC LIMIT 30").fetchall()

        # Top metrics
        appointments_today = conn.execute(
            "SELECT COUNT(*) FROM appointments WHERE date = ?", (today,)).fetchone()[0]
        revenue_today = conn.execute(
            "SELECT COALESCE(SUM(amount), 0) FROM invoices WHERE created_at LIKE ? AND status != 'cancelled'",
            (today + '%',)).fetchone()[0]

    return render_template('business_health.html', overall=overall,
                          revenue_score=revenue_score, satisfaction_score=satisfaction_score,
                          efficiency_score=efficiency_score, retention_score=retention_score,
                          growth_score=growth_score, history=history,
                          current_revenue=current_revenue, last_revenue=last_revenue,
                          appointments_today=appointments_today, revenue_today=revenue_today)

# ── 7. Smart Scheduling ──
@comms_bp.route('/smart_scheduling')
@login_required
def smart_scheduling():
    date = request.args.get('date', datetime.now().strftime('%Y-%m-%d'))
    with get_db() as conn:
        # Get all bays
        bays = conn.execute("SELECT * FROM service_bays WHERE is_active=1 ORDER BY name").fetchall()
        # Get appointments for date
        appointments = conn.execute("""
            SELECT a.*, c.name as customer_name, car.brand, car.model
            FROM appointments a
            LEFT JOIN cars car ON a.car_id = car.id
            LEFT JOIN customers c ON car.customer_id = c.id
            WHERE a.date = ? ORDER BY a.time
        """, (date,)).fetchall()
        # Get employees and their shifts
        employees = conn.execute("""
            SELECT u.id, u.full_name, u.specialties,
                   es.shift_start, es.shift_end
            FROM users u
            LEFT JOIN employee_shifts es ON u.id = es.employee_id AND es.date = ?
            WHERE u.role != 'admin' ORDER BY u.full_name
        """, (date,)).fetchall()
        # Get historical load pattern for this weekday
        weekday = datetime.strptime(date, '%Y-%m-%d').strftime('%A')
        hourly_pattern = conn.execute("""
            SELECT time, COUNT(*) as count FROM appointments
            WHERE strftime('%w', date) = strftime('%w', ?)
            GROUP BY time ORDER BY time
        """, (date,)).fetchall()
        # Available services
        services = conn.execute("SELECT id, name, estimated_minutes FROM services ORDER BY name").fetchall()
    return render_template('smart_scheduling.html', date=date, bays=bays,
                          appointments=appointments, employees=employees,
                          hourly_pattern=hourly_pattern, services=services, weekday=weekday)


