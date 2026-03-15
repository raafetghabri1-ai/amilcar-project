"""
AMILCAR — Vehicle Management & Gallery
Blueprint: vehicles_bp
Routes: 21
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

vehicles_bp = Blueprint("vehicles_bp", __name__)


@vehicles_bp.route("/car/<int:car_id>")
@login_required
def car_detail(car_id):
    with get_db() as conn:
        car = conn.execute("SELECT ca.*, cu.name, cu.phone FROM cars ca JOIN customers cu ON ca.customer_id = cu.id WHERE ca.id = ?", (car_id,)).fetchone()
        if not car:
            return redirect("/customers")
        appointments = conn.execute(
            "SELECT a.id, a.date, a.service, a.status FROM appointments a WHERE a.car_id = ? ORDER BY a.date DESC", (car_id,)).fetchall()
        invoices = conn.execute(
            "SELECT i.id, a.date, a.service, i.amount, i.status, i.payment_method "
            "FROM invoices i JOIN appointments a ON i.appointment_id = a.id "
            "WHERE a.car_id = ? ORDER BY a.date DESC", (car_id,)).fetchall()
        total_spent = conn.execute(
            "SELECT COALESCE(SUM(i.amount),0) FROM invoices i JOIN appointments a ON i.appointment_id = a.id "
            "WHERE a.car_id = ? AND i.status = 'paid'", (car_id,)).fetchone()[0]
    return render_template("car_detail.html", car=car, appointments=appointments,
                           invoices=invoices, total_spent=total_spent)



# ─── Appointment Photos ───
@vehicles_bp.route("/upload_photos/<int:appointment_id>", methods=["POST"])
@login_required
def upload_photos(appointment_id):
    photo_type = request.form.get("photo_type", "before")
    if photo_type not in ("before", "after"):
        photo_type = "before"
    import uuid
    with get_db() as conn:
        appt = conn.execute("SELECT photos_before, photos_after FROM appointments WHERE id = ?", (appointment_id,)).fetchone()
        if not appt:
            return redirect("/appointments")
        existing = appt[0] if photo_type == "before" else appt[1]
        existing_list = [p for p in (existing or '').split(',') if p]
        saved = []
        for f in request.files.getlist('photos'):
            if f.filename and allowed_file(f.filename):
                f.seek(0, 2)
                size = f.tell()
                f.seek(0)
                if size > MAX_FILE_SIZE:
                    continue
                fname = f'{uuid.uuid4().hex}_{secure_filename(f.filename)}'
                f.save(os.path.join(UPLOAD_FOLDER, fname))
                saved.append(fname)
        if saved:
            all_photos = existing_list + saved
            col = "photos_before" if photo_type == "before" else "photos_after"
            conn.execute(f"UPDATE appointments SET {col} = ? WHERE id = ?", (','.join(all_photos), appointment_id))
            conn.commit()
    log_activity('Upload Photos', f'Appointment #{appointment_id} ({photo_type})')
    return redirect(f"/gallery/{appointment_id}")



@vehicles_bp.route("/gallery/<int:appointment_id>")
@login_required
def gallery(appointment_id):
    with get_db() as conn:
        appt = conn.execute(
            "SELECT a.id, cu.name, ca.brand, ca.model, a.date, a.service, a.status, "
            "COALESCE(a.photos_before, ''), COALESCE(a.photos_after, '') "
            "FROM appointments a JOIN cars ca ON a.car_id = ca.id "
            "JOIN customers cu ON ca.customer_id = cu.id WHERE a.id = ?", (appointment_id,)).fetchone()
    if not appt:
        return redirect("/appointments")
    before = [p for p in appt[7].split(',') if p]
    after = [p for p in appt[8].split(',') if p]
    return render_template("gallery.html", appt=appt, before=before, after=after)



@vehicles_bp.route("/delete_photo/<int:appointment_id>", methods=["POST"])
@login_required
def delete_photo(appointment_id):
    photo = request.form.get("photo", "")
    photo_type = request.form.get("photo_type", "before")
    if photo_type not in ("before", "after"):
        return redirect(f"/gallery/{appointment_id}")
    with get_db() as conn:
        col = "photos_before" if photo_type == "before" else "photos_after"
        appt = conn.execute(f"SELECT {col} FROM appointments WHERE id = ?", (appointment_id,)).fetchone()
        if appt:
            photos = [p for p in (appt[0] or '').split(',') if p and p != photo]
            conn.execute(f"UPDATE appointments SET {col} = ? WHERE id = ?", (','.join(photos), appointment_id))
            conn.commit()
            # حذف الملف
            filepath = os.path.join(UPLOAD_FOLDER, photo)
            if os.path.exists(filepath):
                os.remove(filepath)
    return redirect(f"/gallery/{appointment_id}")



# ─── Phase 7 Feature 8: Photo Archive ───
@vehicles_bp.route("/car_photos/<int:car_id>")
@login_required
def car_photos(car_id):
    with get_db() as conn:
        car = conn.execute("SELECT cr.*, c.name FROM cars cr JOIN customers c ON cr.customer_id=c.id WHERE cr.id=?", (car_id,)).fetchone()
        if not car:
            flash("Véhicule introuvable", "danger")
            return redirect("/customers")
        photos = conn.execute("SELECT * FROM car_photos WHERE car_id=? ORDER BY uploaded_at DESC", (car_id,)).fetchall()
        appointments = conn.execute("SELECT id, date, service FROM appointments WHERE car_id=? ORDER BY date DESC", (car_id,)).fetchall()
    return render_template("car_photos.html", car=car, photos=photos, appointments=appointments)



@vehicles_bp.route("/car_photos/<int:car_id>/upload", methods=["POST"])
@login_required
def upload_car_photo(car_id):
    photo = request.files.get('photo')
    if not photo or photo.filename == '':
        flash("Aucune photo sélectionnée", "warning")
        return redirect(f"/car_photos/{car_id}")
    photo_type = request.form.get('photo_type', 'before')
    appointment_id = request.form.get('appointment_id') or None
    description = request.form.get('description', '')
    filename = secure_filename(f"{car_id}_{uuid.uuid4().hex[:8]}_{photo.filename}")
    upload_dir = os.path.join(app.root_path, 'static', 'uploads', 'cars')
    os.makedirs(upload_dir, exist_ok=True)
    photo.save(os.path.join(upload_dir, filename))
    with get_db() as conn:
        conn.execute("INSERT INTO car_photos (car_id, appointment_id, photo_type, filename, description) VALUES (?,?,?,?,?)",
                    (car_id, appointment_id, photo_type, filename, description))
        conn.commit()
    flash("Photo uploadée !", "success")
    return redirect(f"/car_photos/{car_id}")



@vehicles_bp.route("/car_photos/delete/<int:photo_id>", methods=["POST"])
@login_required
def delete_car_photo(photo_id):
    with get_db() as conn:
        photo = conn.execute("SELECT * FROM car_photos WHERE id=?", (photo_id,)).fetchone()
        if photo:
            car_id = photo[1]
            filepath = os.path.join(app.root_path, 'static', 'uploads', 'cars', photo[4])
            if os.path.exists(filepath):
                os.remove(filepath)
            conn.execute("DELETE FROM car_photos WHERE id=?", (photo_id,))
            conn.commit()
            flash("Photo supprimée", "success")
            return redirect(f"/car_photos/{car_id}")
    flash("Photo introuvable", "danger")
    return redirect("/customers")



# ─── 8. Vehicle History ───
@vehicles_bp.route("/vehicle_history")
@login_required
def vehicle_history():
    car_id = request.args.get("car_id", type=int)
    with get_db() as conn:
        cars = conn.execute("""SELECT ca.id, ca.brand, ca.model, ca.plate, c.name
            FROM cars ca JOIN customers c ON ca.customer_id=c.id ORDER BY c.name""").fetchall()
        history = []
        car = None
        if car_id:
            car = conn.execute("""SELECT ca.*, c.name, c.phone FROM cars ca
                JOIN customers c ON ca.customer_id=c.id WHERE ca.id=?""", (car_id,)).fetchone()
            history = conn.execute("""SELECT a.id, a.date, a.time, a.service, a.status, a.assigned_to,
                i.amount, i.status as inv_status
                FROM appointments a LEFT JOIN invoices i ON i.appointment_id=a.id
                WHERE a.car_id=? ORDER BY a.date DESC""", (car_id,)).fetchall()
    return render_template("vehicle_history.html", cars=cars, history=history, car=car, car_id=car_id)



@vehicles_bp.route("/vin_decode", methods=["GET", "POST"])
@login_required
def vin_decode():
    result = None
    car_id = request.args.get("car_id", 0, type=int)
    if request.method == "POST":
        vin = request.form.get("vin", "").strip().upper()
        car_id = request.form.get("car_id", 0, type=int)
        if len(vin) == 17:
            result = decode_vin_local(vin)
            if result and car_id:
                with get_db() as conn:
                    conn.execute("UPDATE cars SET vin=? WHERE id=?", (vin, car_id))
                    existing = conn.execute("SELECT id FROM vin_records WHERE car_id=?", (car_id,)).fetchone()
                    if existing:
                        conn.execute("""UPDATE vin_records SET vin=?, decoded_make=?, decoded_model=?, 
                            decoded_year=?, decoded_engine=?, decoded_body=?, decoded_fuel=? WHERE car_id=?""",
                            (vin, result['make'], '', result['year'], result['engine'], result['body'], result['fuel'], car_id))
                    else:
                        conn.execute("""INSERT INTO vin_records 
                            (car_id, vin, decoded_make, decoded_year, decoded_engine, decoded_body, decoded_fuel)
                            VALUES (?,?,?,?,?,?,?)""",
                            (car_id, vin, result['make'], result['year'], result['engine'], result['body'], result['fuel']))
                    conn.commit()
                flash("VIN décodé et sauvegardé !", "success")
        else:
            flash("VIN invalide — 17 caractères requis", "danger")
    with get_db() as conn:
        cars = conn.execute("""SELECT c.*, cu.name as customer_name FROM cars c 
            JOIN customers cu ON c.customer_id=cu.id ORDER BY c.id DESC""").fetchall()
        records = conn.execute("""SELECT vr.*, c.plate, cu.name as customer_name FROM vin_records vr 
            JOIN cars c ON vr.car_id=c.id 
            JOIN customers cu ON c.customer_id=cu.id 
            ORDER BY vr.created_at DESC LIMIT 50""").fetchall()
    return render_template("vin_decoder.html", cars=cars, result=result, car_id=car_id, records=records)



# ─── 6. Gestion Documentaire Véhicule ───

@vehicles_bp.route("/vehicle_docs/<int:car_id>")
@login_required
def vehicle_docs(car_id):
    with get_db() as conn:
        car = conn.execute("""SELECT c.*, cu.name as customer_name FROM cars c 
            JOIN customers cu ON c.customer_id=cu.id WHERE c.id=?""", (car_id,)).fetchone()
        if not car:
            flash("Véhicule introuvable", "danger")
            return redirect("/customers")
        docs = conn.execute("SELECT * FROM vehicle_documents WHERE car_id=? ORDER BY created_at DESC", (car_id,)).fetchall()
        from datetime import date
        today = date.today().isoformat()
        expiring = [d for d in docs if d['expiry_date'] and d['expiry_date'] <= today]
    return render_template("vehicle_docs.html", car=car, docs=docs, expiring=expiring)



@vehicles_bp.route("/vehicle_docs/add/<int:car_id>", methods=["POST"])
@login_required
def add_vehicle_doc(car_id):
    doc_type = request.form.get("doc_type", "").strip()
    doc_name = request.form.get("doc_name", "").strip()
    expiry = request.form.get("expiry_date", "")
    notes = request.form.get("notes", "").strip()
    file_path = ""
    if 'document' in request.files:
        f = request.files['document']
        if f and f.filename:
            ext = f.filename.rsplit('.', 1)[-1].lower() if '.' in f.filename else ''
            allowed = {'png', 'jpg', 'jpeg', 'gif', 'pdf', 'webp'}
            if ext in allowed:
                fname = secure_filename(f"{uuid.uuid4().hex[:12]}_{f.filename}")
                os.makedirs('static/uploads/docs', exist_ok=True)
                f.save(os.path.join('static/uploads/docs', fname))
                file_path = f"uploads/docs/{fname}"
    if doc_type:
        with get_db() as conn:
            conn.execute("""INSERT INTO vehicle_documents (car_id, doc_type, doc_name, file_path, expiry_date, notes)
                VALUES (?,?,?,?,?,?)""", (car_id, doc_type, doc_name, file_path, expiry, notes))
            conn.commit()
        flash("Document ajouté !", "success")
    return redirect(f"/vehicle_docs/{car_id}")



@vehicles_bp.route("/vehicle_docs/delete/<int:doc_id>/<int:car_id>", methods=["POST"])
@login_required
def delete_vehicle_doc(doc_id, car_id):
    with get_db() as conn:
        conn.execute("DELETE FROM vehicle_documents WHERE id=?", (doc_id,))
        conn.commit()
    flash("Document supprimé", "success")
    return redirect(f"/vehicle_docs/{car_id}")



# ─── 1. Galerie Avant/Après ───

@vehicles_bp.route("/gallery_global")
@login_required
def gallery_global():
    with get_db() as conn:
        photos = conn.execute("""SELECT g.*, c.plate, c.brand, c.model, c.vehicle_type
            FROM vehicle_gallery g JOIN cars c ON g.car_id = c.id
            ORDER BY g.created_at DESC LIMIT 100""").fetchall()
    return render_template("gallery_global.html", photos=photos)



@vehicles_bp.route("/gallery/upload", methods=["POST"])
@login_required
def gallery_upload():
    car_id = request.form.get("car_id", 0, type=int)
    appointment_id = request.form.get("appointment_id", 0, type=int)
    photo_type = request.form.get("photo_type", "before")
    caption = request.form.get("caption", "")
    is_portfolio = 1 if request.form.get("is_portfolio") else 0
    file = request.files.get("photo")
    if file and car_id:
        import os, uuid
        from werkzeug.utils import secure_filename
        fname = secure_filename(f"{uuid.uuid4().hex}_{file.filename}")
        upload_dir = os.path.join("static", "uploads", "gallery")
        os.makedirs(upload_dir, exist_ok=True)
        fpath = os.path.join(upload_dir, fname)
        file.save(fpath)
        with get_db() as conn:
            conn.execute("""INSERT INTO vehicle_gallery (car_id, appointment_id, photo_type, photo_path, caption, is_portfolio)
                VALUES (?,?,?,?,?,?)""", (car_id, appointment_id, photo_type, f"uploads/gallery/{fname}", caption, is_portfolio))
            conn.commit()
        flash("Photo ajoutée ✅", "success")
    return redirect("/gallery_global")



# ─── 3. Suivi Traitements & Garanties ───

@vehicles_bp.route("/treatments")
@login_required
def treatments():
    with get_db() as conn:
        treats = conn.execute("""SELECT t.*, c.plate, c.brand, c.model, c.vehicle_type, cu.name as customer_name
            FROM treatments t JOIN cars c ON t.car_id = c.id JOIN customers cu ON t.customer_id = cu.id
            ORDER BY t.created_at DESC""").fetchall()
        expiring = conn.execute("""SELECT t.*, c.plate, c.brand, c.model, cu.name as customer_name
            FROM treatments t JOIN cars c ON t.car_id = c.id JOIN customers cu ON t.customer_id = cu.id
            WHERE t.status='active' AND t.warranty_expiry != '' AND t.warranty_expiry <= date('now', '+30 days')
            ORDER BY t.warranty_expiry""").fetchall()
        stats = {
            'active': conn.execute("SELECT COUNT(*) FROM treatments WHERE status='active'").fetchone()[0],
            'expired': conn.execute("SELECT COUNT(*) FROM treatments WHERE status='expired'").fetchone()[0],
            'expiring_soon': len(expiring),
            'total': conn.execute("SELECT COUNT(*) FROM treatments").fetchone()[0]
        }
        customers = conn.execute("SELECT id, name FROM customers ORDER BY name").fetchall()
        cars = conn.execute("SELECT id, plate, brand, model, customer_id, vehicle_type FROM cars ORDER BY plate").fetchall()
    return render_template("treatments.html", treatments=treats, expiring=expiring, stats=stats, customers=customers, cars=cars)



@vehicles_bp.route("/treatment/add", methods=["POST"])
@login_required
def treatment_add():
    from datetime import datetime, timedelta
    car_id = request.form.get("car_id", 0, type=int)
    customer_id = request.form.get("customer_id", 0, type=int)
    treatment_type = request.form.get("treatment_type", "")
    product_used = request.form.get("product_used", "")
    brand = request.form.get("brand", "")
    warranty_years = request.form.get("warranty_years", 0, type=float)
    applied_date = request.form.get("applied_date", datetime.now().strftime("%Y-%m-%d"))
    notes = request.form.get("notes", "")
    warranty_expiry = ""
    next_renewal = ""
    if warranty_years > 0:
        expiry_date = datetime.strptime(applied_date, "%Y-%m-%d") + timedelta(days=int(warranty_years * 365))
        warranty_expiry = expiry_date.strftime("%Y-%m-%d")
        next_renewal = (expiry_date - timedelta(days=30)).strftime("%Y-%m-%d")
    with get_db() as conn:
        conn.execute("""INSERT INTO treatments (car_id, customer_id, treatment_type, product_used, brand,
            warranty_years, warranty_expiry, applied_date, next_renewal, notes)
            VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (car_id, customer_id, treatment_type, product_used, brand, warranty_years, warranty_expiry, applied_date, next_renewal, notes))
        conn.commit()
    flash("Traitement enregistré ✅", "success")
    return redirect("/treatments")



# ─── 4. Fiche État Véhicule ───

@vehicles_bp.route("/vehicle_condition/<int:appointment_id>", methods=["GET", "POST"])
@login_required
def vehicle_condition(appointment_id):
    with get_db() as conn:
        appt = conn.execute("""SELECT a.*, c.plate, c.brand, c.model, c.vehicle_type, cu.name as customer_name
            FROM appointments a JOIN cars c ON a.car_id = c.id JOIN customers cu ON c.customer_id = cu.id
            WHERE a.id=?""", (appointment_id,)).fetchone()
        if not appt:
            flash("RDV non trouvé", "danger")
            return redirect("/appointments")
        if request.method == "POST":
            exterior = request.form.get("exterior_state", "")
            interior = request.form.get("interior_state", "")
            scratches = request.form.get("scratches", "")
            dents = request.form.get("dents", "")
            paint = request.form.get("paint_condition", "")
            leather = request.form.get("leather_condition", "")
            dashboard = request.form.get("dashboard_condition", "")
            wheels = request.form.get("wheels_condition", "")
            notes = request.form.get("notes", "")
            ctype = request.form.get("condition_type", "reception")
            # Handle photos
            import os, uuid, json
            from werkzeug.utils import secure_filename
            photo_paths = []
            photos = request.files.getlist("photos")
            upload_dir = os.path.join("static", "uploads", "conditions")
            os.makedirs(upload_dir, exist_ok=True)
            for photo in photos:
                if photo and photo.filename:
                    fname = secure_filename(f"{uuid.uuid4().hex}_{photo.filename}")
                    photo.save(os.path.join(upload_dir, fname))
                    photo_paths.append(f"uploads/conditions/{fname}")
            conn.execute("""INSERT INTO vehicle_conditions (car_id, appointment_id, condition_type, exterior_state,
                interior_state, scratches, dents, paint_condition, leather_condition, dashboard_condition,
                wheels_condition, photos, notes, created_by)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (appt['car_id'], appointment_id, ctype, exterior, interior, scratches, dents, paint, leather,
                 dashboard, wheels, json.dumps(photo_paths), notes, session.get('user_id', 0)))
            conn.commit()
            flash("Fiche état enregistrée ✅", "success")
            return redirect(f"/vehicle_condition/{appointment_id}")
        conditions = conn.execute("SELECT * FROM vehicle_conditions WHERE appointment_id=? ORDER BY created_at", (appointment_id,)).fetchall()
    return render_template("vehicle_condition.html", appt=appt, conditions=conditions)



# ─── 1. Dashboard Car Care ───

@vehicles_bp.route("/care_dashboard")
@login_required
@admin_required
def care_dashboard():
    from datetime import date, timedelta
    today = date.today().isoformat()
    with get_db() as conn:
        # Active treatments
        active_treatments = conn.execute("SELECT COUNT(*) FROM treatments WHERE status='active'").fetchone()[0]
        expiring_treatments = conn.execute(
            "SELECT COUNT(*) FROM treatments WHERE status='active' AND warranty_expiry <= date('now','+30 days') AND warranty_expiry != ''").fetchone()[0]
        # Top products
        top_products = conn.execute("""SELECT product_name, SUM(total_cost) as cost, SUM(quantity_used) as qty, COUNT(*) as cnt
            FROM product_usage GROUP BY product_name ORDER BY cost DESC LIMIT 5""").fetchall()
        # Subscriptions
        active_subs = conn.execute("SELECT COUNT(*) FROM wash_subscriptions WHERE status='active'").fetchone()[0]
        sub_revenue = conn.execute("SELECT COALESCE(SUM(price),0) FROM wash_subscriptions WHERE status='active'").fetchone()[0]
        # Reviews
        avg_rating = conn.execute("SELECT COALESCE(AVG(rating),0) FROM client_reviews").fetchone()[0]
        total_reviews = conn.execute("SELECT COUNT(*) FROM client_reviews").fetchone()[0]
        # Today's live tracking
        in_progress = conn.execute("SELECT COUNT(*) FROM vehicle_status vs JOIN appointments a ON vs.appointment_id=a.id WHERE a.date=?", (today,)).fetchone()[0]
        # Revenue by vehicle type (this month)
        month = date.today().strftime("%Y-%m")
        rev_by_type = conn.execute("""SELECT c.vehicle_type, COUNT(*) as cnt, COALESCE(SUM(i.amount),0) as rev
            FROM invoices i JOIN appointments a ON i.appointment_id=a.id JOIN cars c ON a.car_id=c.id
            WHERE strftime('%%Y-%%m',a.date)=? AND i.status='paid'
            GROUP BY c.vehicle_type""", (month,)).fetchall()
        # Gallery count
        gallery_count = conn.execute("SELECT COUNT(*) FROM vehicle_gallery").fetchone()[0]
        portfolio_count = conn.execute("SELECT COUNT(*) FROM vehicle_gallery WHERE is_portfolio=1").fetchone()[0]
        # Packs
        active_packs = conn.execute("SELECT COUNT(*) FROM detailing_packs WHERE is_active=1").fetchone()[0]
        # Treatments by type
        treat_by_type = conn.execute("""SELECT treatment_type, COUNT(*) as cnt
            FROM treatments WHERE status='active' GROUP BY treatment_type ORDER BY cnt DESC LIMIT 6""").fetchall()
        # Expiring treatments list
        expiring_list = conn.execute("""SELECT t.*, c.plate, c.brand, c.model, cu.name as customer_name, cu.phone
            FROM treatments t JOIN cars c ON t.car_id=c.id JOIN customers cu ON t.customer_id=cu.id
            WHERE t.status='active' AND t.warranty_expiry != '' AND t.warranty_expiry <= date('now','+30 days')
            ORDER BY t.warranty_expiry LIMIT 10""").fetchall()
    return render_template("care_dashboard.html", active_treatments=active_treatments,
        expiring_treatments=expiring_treatments, top_products=top_products, active_subs=active_subs,
        sub_revenue=sub_revenue, avg_rating=avg_rating, total_reviews=total_reviews,
        in_progress=in_progress, rev_by_type=rev_by_type, gallery_count=gallery_count,
        portfolio_count=portfolio_count, active_packs=active_packs, treat_by_type=treat_by_type,
        expiring_list=expiring_list)



# ─── 4. QR Code Véhicule ───

@vehicles_bp.route("/vehicle_qr/<int:car_id>")
@login_required
def vehicle_qr(car_id):
    import uuid
    with get_db() as conn:
        car = conn.execute("""SELECT c.*, cu.name as customer_name, cu.phone
            FROM cars c JOIN customers cu ON c.customer_id=cu.id WHERE c.id=?""", (car_id,)).fetchone()
        if not car:
            flash("Véhicule non trouvé", "danger")
            return redirect("/customers")
        # Generate token if not exists
        if not car['qr_token']:
            token = uuid.uuid4().hex[:12]
            conn.execute("UPDATE cars SET qr_token=? WHERE id=?", (token, car_id))
            conn.commit()
        else:
            token = car['qr_token']
    return render_template("vehicle_qr.html", car=car, token=token)



@vehicles_bp.route("/vehicle_history_public/<token>")
def vehicle_history_public(token):
    with get_db() as conn:
        car = conn.execute("""SELECT c.*, cu.name as customer_name
            FROM cars c JOIN customers cu ON c.customer_id=cu.id WHERE c.qr_token=?""", (token,)).fetchone()
        if not car:
            return "Véhicule non trouvé", 404
        appointments = conn.execute("""SELECT a.date, a.service, a.status FROM appointments a
            WHERE a.car_id=? ORDER BY a.date DESC""", (car['id'],)).fetchall()
        treatments = conn.execute("""SELECT * FROM treatments WHERE car_id=? ORDER BY applied_date DESC""",
            (car['id'],)).fetchone() and conn.execute("""SELECT * FROM treatments WHERE car_id=? ORDER BY applied_date DESC""",
            (car['id'],)).fetchall() or []
        gallery = conn.execute("SELECT * FROM vehicle_gallery WHERE car_id=? ORDER BY created_at DESC LIMIT 10",
            (car['id'],)).fetchall()
        shop = {}
        for r in conn.execute("SELECT key, value FROM settings").fetchall():
            shop[r['key']] = r['value']
    return render_template("vehicle_history_public.html", car=car, appointments=appointments,
        treatments=treatments, gallery=gallery, shop=shop)



# ─── 10. Historique Complet Véhicule ───

@vehicles_bp.route("/vehicle_full_history/<int:car_id>")
@login_required
def vehicle_full_history(car_id):
    with get_db() as conn:
        car = conn.execute("""SELECT c.*, cu.name as customer_name, cu.phone, cu.email
            FROM cars c JOIN customers cu ON c.customer_id=cu.id WHERE c.id=?""", (car_id,)).fetchone()
        if not car:
            flash("Véhicule non trouvé", "danger")
            return redirect("/customers")
        appointments = conn.execute("""SELECT a.*, COALESCE(i.amount,0) as invoice_amount, i.status as invoice_status
            FROM appointments a LEFT JOIN invoices i ON i.appointment_id=a.id
            WHERE a.car_id=? ORDER BY a.date DESC""", (car_id,)).fetchall()
        treatments = conn.execute("SELECT * FROM treatments WHERE car_id=? ORDER BY applied_date DESC", (car_id,)).fetchall()
        gallery = conn.execute("SELECT * FROM vehicle_gallery WHERE car_id=? ORDER BY created_at DESC", (car_id,)).fetchall()
        conditions = conn.execute("""SELECT vc.*, a.date as appt_date FROM vehicle_conditions vc
            JOIN appointments a ON vc.appointment_id=a.id WHERE vc.car_id=? ORDER BY vc.created_at DESC""", (car_id,)).fetchall()
        subscriptions = conn.execute("""SELECT * FROM wash_subscriptions WHERE car_id=? ORDER BY created_at DESC""", (car_id,)).fetchall()
        product_costs = conn.execute("""SELECT COALESCE(SUM(pu.total_cost),0) FROM product_usage pu
            JOIN appointments a ON pu.appointment_id=a.id WHERE a.car_id=?""", (car_id,)).fetchone()[0]
        total_revenue = conn.execute("""SELECT COALESCE(SUM(i.amount),0) FROM invoices i
            JOIN appointments a ON i.appointment_id=a.id WHERE a.car_id=? AND i.status='paid'""", (car_id,)).fetchone()[0]
        total_visits = len(appointments)
    return render_template("vehicle_full_history.html", car=car, appointments=appointments,
        treatments=treatments, gallery=gallery, conditions=conditions, subscriptions=subscriptions,
        product_costs=product_costs, total_revenue=total_revenue, total_visits=total_visits)


