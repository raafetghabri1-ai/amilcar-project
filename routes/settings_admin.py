"""
AMILCAR — Settings & Administration
Blueprint: admin_bp
Routes: 26
"""
from flask import Blueprint, render_template, request, redirect, url_for, flash, make_response, jsonify, session, send_file
from helpers import login_required, admin_required, client_required, get_db, get_services, get_setting, get_all_settings
from helpers import allowed_file, safe_page, log_activity, build_wa_url, STATUS_MESSAGES, UPLOAD_FOLDER, MAX_FILE_SIZE, MAX_FILES, PER_PAGE, TRANSLATIONS
from database.db import get_db
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, date, timedelta
import os, re, uuid, io
import time as time_module
import sqlite3

admin_bp = Blueprint("admin_bp", __name__)


# ─── Global Search ───
@admin_bp.route("/search")
@login_required
def global_search():
    q = request.args.get('q', '').strip()
    if not q or len(q) < 2:
        return render_template("search_results.html", q=q, results={})
    with get_db() as conn:
        results = {
            'customers': conn.execute(
                "SELECT id, name, phone FROM customers WHERE name LIKE ? OR phone LIKE ? LIMIT 20",
                (f'%{q}%', f'%{q}%')).fetchall(),
            'cars': conn.execute(
                "SELECT ca.id, ca.brand, ca.model, ca.plate, cu.name FROM cars ca "
                "JOIN customers cu ON ca.customer_id = cu.id "
                "WHERE ca.brand LIKE ? OR ca.model LIKE ? OR ca.plate LIKE ? LIMIT 20",
                (f'%{q}%', f'%{q}%', f'%{q}%')).fetchall(),
            'appointments': conn.execute(
                "SELECT a.id, cu.name, ca.brand || ' ' || ca.model, a.date, a.service, a.status "
                "FROM appointments a JOIN cars ca ON a.car_id = ca.id "
                "JOIN customers cu ON ca.customer_id = cu.id "
                "WHERE cu.name LIKE ? OR a.service LIKE ? OR ca.brand LIKE ? OR ca.model LIKE ? LIMIT 20",
                (f'%{q}%', f'%{q}%', f'%{q}%', f'%{q}%')).fetchall(),
            'invoices': conn.execute(
                "SELECT i.id, cu.name, a.service, i.amount, i.status "
                "FROM invoices i JOIN appointments a ON i.appointment_id = a.id "
                "JOIN cars ca ON a.car_id = ca.id JOIN customers cu ON ca.customer_id = cu.id "
                "WHERE cu.name LIKE ? OR a.service LIKE ? LIMIT 20",
                (f'%{q}%', f'%{q}%')).fetchall(),
        }
    total = sum(len(v) for v in results.values())
    return render_template("search_results.html", q=q, results=results, total=total)



# ─── Activity Log ───
@admin_bp.route("/activity_log")
@admin_required
def activity_log():
    page = safe_page(request.args.get('page', 1, type=int))
    with get_db() as conn:
        total = conn.execute("SELECT COUNT(*) FROM activity_log").fetchone()[0]
        logs = conn.execute(
            "SELECT id, username, action, detail, created_at FROM activity_log "
            "ORDER BY id DESC LIMIT ? OFFSET ?", (PER_PAGE, (page - 1) * PER_PAGE)).fetchall()
    total_pages = (total + PER_PAGE - 1) // PER_PAGE
    return render_template("activity_log.html", logs=logs,
        page=page, total_pages=total_pages)



# ─── Services Management (Admin) ───
@admin_bp.route("/services")
@admin_required
def services_list():
    with get_db() as conn:
        all_services = conn.execute("SELECT * FROM services ORDER BY id").fetchall()
    return render_template("services.html", services=all_services)



@admin_bp.route("/add_service", methods=["POST"])
@admin_required
def add_service():
    name = request.form.get("name", "").strip()
    price = request.form.get("price", "0").strip()
    if not name:
        flash("Le nom du service est requis", "error")
        return redirect("/services")
    try:
        price_val = float(price)
    except ValueError:
        price_val = 0
    with get_db() as conn:
        conn.execute("INSERT INTO services (name, price) VALUES (?, ?)", (name, price_val))
        conn.commit()
    log_activity('Add Service', f'{name} — {price_val} DT')
    flash(f"Service '{name}' ajouté", "success")
    return redirect("/services")



@admin_bp.route("/delete_service/<int:service_id>", methods=["POST"])
@admin_required
def delete_service(service_id):
    with get_db() as conn:
        conn.execute("DELETE FROM services WHERE id = ?", (service_id,))
        conn.commit()
    log_activity('Delete Service', f'Service #{service_id}')
    flash("Service supprimé", "success")
    return redirect("/services")



# ─── Settings (Admin) ───
@admin_bp.route("/settings", methods=["GET", "POST"])
@admin_required
def settings_page():
    if request.method == "POST":
        with get_db() as conn:
            keys = ['shop_name', 'shop_tagline', 'shop_address', 'shop_phone', 'tax_rate',
                    'smtp_host', 'smtp_port', 'smtp_user', 'smtp_pass', 'smtp_from',
                    'sms_api_url', 'sms_api_key', 'sms_sender',
                    'wa_callmebot_phone', 'wa_callmebot_apikey', 'wa_notify_booking']
            for key in keys:
                val = request.form.get(key, "").strip()
                conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, val))
            conn.commit()
        log_activity('Update Settings', 'Shop settings updated')
        flash("Paramètres enregistrés avec succès", "success")
        return redirect("/settings")
    settings = get_all_settings()
    return render_template("settings.html", settings=settings)



# ─── Database Backup ───
@admin_bp.route("/backup")
@admin_required
def backup_database():
    import shutil
    from datetime import datetime
    db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'database', 'amilcar.db')
    if not os.path.exists(db_path):
        flash("Fichier de base de données introuvable", "error")
        return redirect("/settings")
    with open(db_path, 'rb') as f:
        data = f.read()
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    response = make_response(data)
    response.headers['Content-Type'] = 'application/octet-stream'
    response.headers['Content-Disposition'] = f'attachment; filename=amilcar_backup_{timestamp}.db'
    log_activity('Backup', 'Database backup downloaded')
    return response



# ─── Automatic Daily Backup ───
@admin_bp.route("/auto_backup_settings", methods=["POST"])
@admin_required
def auto_backup_settings():
    enabled = '1' if request.form.get("auto_backup") else '0'
    keep_days = request.form.get("backup_keep_days", "7").strip()
    try:
        kd = max(1, min(int(keep_days), 30))
    except ValueError:
        kd = 7
    with get_db() as conn:
        conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('auto_backup', ?)", (enabled,))
        conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('backup_keep_days', ?)", (str(kd),))
        conn.commit()
    flash("Paramètres de sauvegarde mis à jour", "success")
    return redirect("/settings")



@admin_bp.route("/run_backup", methods=["POST"])
@admin_required
def run_manual_backup():
    result = _perform_backup()
    if result:
        flash(f"Sauvegarde créée : {result}", "success")
    else:
        flash("Erreur lors de la sauvegarde", "error")
    return redirect("/settings")



@admin_bp.route("/download_backup/<filename>")
@admin_required
def download_backup(filename):
    # Sanitize filename
    safe_name = secure_filename(filename)
    backup_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'backups')
    filepath = os.path.join(backup_dir, safe_name)
    if not os.path.exists(filepath):
        flash("Fichier introuvable", "error")
        return redirect("/settings")
    with open(filepath, 'rb') as f:
        data = f.read()
    response = make_response(data)
    response.headers['Content-Type'] = 'application/octet-stream'
    response.headers['Content-Disposition'] = f'attachment; filename={safe_name}'
    return response



@admin_bp.route("/set_language/<lang>")
def set_language(lang):
    if lang in TRANSLATIONS:
        session['lang'] = lang
    return redirect(request.referrer or '/')



@admin_bp.route("/api_settings")
@login_required
@admin_required
def api_settings():
    with get_db() as conn:
        keys = conn.execute("SELECT * FROM api_keys ORDER BY created_at DESC").fetchall()
        hooks = conn.execute("SELECT * FROM webhooks ORDER BY created_at DESC").fetchall()
    return render_template("api_settings.html", keys=keys, hooks=hooks)



@admin_bp.route("/api_key/add", methods=["POST"])
@login_required
@admin_required
def api_key_add():
    name = request.form.get("name", "").strip()
    perms = request.form.get("permissions", "read")
    if name:
        key = f"amk_{uuid.uuid4().hex}"
        with get_db() as conn:
            conn.execute("INSERT INTO api_keys (name, api_key, permissions) VALUES (?,?,?)", (name, key, perms))
            conn.commit()
        flash(f"Clé API créée: {key}", "success")
    return redirect("/api_settings")



@admin_bp.route("/api_key/toggle/<int:kid>", methods=["POST"])
@login_required
@admin_required
def api_key_toggle(kid):
    with get_db() as conn:
        k = conn.execute("SELECT active FROM api_keys WHERE id=?", (kid,)).fetchone()
        if k:
            conn.execute("UPDATE api_keys SET active=? WHERE id=?", (0 if k[0] else 1, kid))
            conn.commit()
    return redirect("/api_settings")



@admin_bp.route("/api_key/delete/<int:kid>", methods=["POST"])
@login_required
@admin_required
def api_key_delete(kid):
    with get_db() as conn:
        conn.execute("DELETE FROM api_keys WHERE id=?", (kid,))
        conn.commit()
    flash("Clé supprimée", "success")
    return redirect("/api_settings")



# ─── 1. Multi-Succursale (Branches) ───

@admin_bp.route("/branches")
@login_required
@admin_required
def branches():
    with get_db() as conn:
        all_branches = conn.execute("SELECT * FROM branches ORDER BY name").fetchall()
        # Stats per branch
        stats = {}
        for b in all_branches:
            appts = conn.execute("SELECT COUNT(*) FROM appointments WHERE branch_id=?", (b['id'],)).fetchone()[0]
            revenue = conn.execute("SELECT COALESCE(SUM(amount),0) FROM invoices WHERE branch_id=?", (b['id'],)).fetchone()[0]
            staff = conn.execute("SELECT COUNT(*) FROM users WHERE branch_id=?", (b['id'],)).fetchone()[0]
            stats[b['id']] = {'appointments': appts, 'revenue': revenue, 'staff': staff}
        transfers = conn.execute("""
            SELECT bt.*, b1.name as from_name, b2.name as to_name 
            FROM branch_transfers bt 
            JOIN branches b1 ON bt.from_branch=b1.id 
            JOIN branches b2 ON bt.to_branch=b2.id 
            ORDER BY bt.created_at DESC LIMIT 20
        """).fetchall()
    return render_template("branches.html", branches=all_branches, stats=stats, transfers=transfers)



@admin_bp.route("/branch/add", methods=["POST"])
@login_required
@admin_required
def add_branch():
    name = request.form.get("name", "").strip()
    address = request.form.get("address", "").strip()
    phone = request.form.get("phone", "").strip()
    manager = request.form.get("manager", "").strip()
    if name:
        with get_db() as conn:
            conn.execute("INSERT INTO branches (name, address, phone, manager) VALUES (?,?,?,?)",
                        (name, address, phone, manager))
            conn.commit()
        flash("Succursale ajoutée !", "success")
    return redirect("/branches")



@admin_bp.route("/branch/toggle/<int:bid>", methods=["POST"])
@login_required
@admin_required
def toggle_branch(bid):
    with get_db() as conn:
        b = conn.execute("SELECT active FROM branches WHERE id=?", (bid,)).fetchone()
        if b:
            conn.execute("UPDATE branches SET active=? WHERE id=?", (0 if b[0] else 1, bid))
            conn.commit()
    return redirect("/branches")



@admin_bp.route("/branch/transfer", methods=["POST"])
@login_required
@admin_required
def branch_transfer():
    from_b = request.form.get("from_branch", type=int)
    to_b = request.form.get("to_branch", type=int)
    item_type = request.form.get("item_type", "inventory")
    item_id = request.form.get("item_id", type=int)
    qty = request.form.get("quantity", 1, type=int)
    notes = request.form.get("notes", "").strip()
    if from_b and to_b and item_id and from_b != to_b:
        with get_db() as conn:
            conn.execute("""INSERT INTO branch_transfers 
                (from_branch, to_branch, item_type, item_id, quantity, notes, status, created_by) 
                VALUES (?,?,?,?,?,?,?,?)""",
                (from_b, to_b, item_type, item_id, qty, notes, 'completed', session.get('user_id')))
            if item_type == 'inventory':
                conn.execute("UPDATE inventory SET quantity = quantity - ? WHERE id=? AND branch_id=?", (qty, item_id, from_b))
                existing = conn.execute("SELECT id FROM inventory WHERE id=? AND branch_id=?", (item_id, to_b)).fetchone()
                if existing:
                    conn.execute("UPDATE inventory SET quantity = quantity + ? WHERE id=? AND branch_id=?", (qty, item_id, to_b))
            conn.commit()
        flash("Transfert effectué !", "success")
    return redirect("/branches")



# ─── 4. Gestion Assurance & Tiers-Payant ───

@admin_bp.route("/insurance")
@login_required
def insurance():
    with get_db() as conn:
        companies = conn.execute("SELECT * FROM insurance_companies ORDER BY name").fetchall()
        claims = conn.execute("""SELECT ic.*, cu.name as customer_name, c.plate, ins.name as insurer_name
            FROM insurance_claims ic 
            JOIN customers cu ON ic.customer_id=cu.id 
            JOIN cars c ON ic.car_id=c.id 
            JOIN insurance_companies ins ON ic.insurance_id=ins.id 
            ORDER BY ic.created_at DESC""").fetchall()
        stats = {
            'total_claims': len(claims),
            'pending': sum(1 for c in claims if c['status'] in ('submitted', 'in_review')),
            'approved': sum(1 for c in claims if c['status'] == 'approved'),
            'total_amount': sum(c['estimated_cost'] for c in claims),
            'approved_amount': sum(c['approved_amount'] for c in claims if c['status'] == 'approved'),
        }
        customers = conn.execute("SELECT id, name FROM customers ORDER BY name").fetchall()
        cars = conn.execute("SELECT c.id, c.plate, cu.name FROM cars c JOIN customers cu ON c.customer_id=cu.id ORDER BY c.plate").fetchall()
    return render_template("insurance.html", companies=companies, claims=claims, stats=stats, customers=customers, cars=cars)



@admin_bp.route("/insurance/company/add", methods=["POST"])
@login_required
@admin_required
def add_insurance_company():
    name = request.form.get("name", "").strip()
    contact = request.form.get("contact_person", "").strip()
    phone = request.form.get("phone", "").strip()
    email = request.form.get("email", "").strip()
    contract = request.form.get("contract_number", "").strip()
    discount = request.form.get("discount_rate", 0, type=float)
    if name:
        with get_db() as conn:
            conn.execute("""INSERT INTO insurance_companies 
                (name, contact_person, phone, email, contract_number, discount_rate) 
                VALUES (?,?,?,?,?,?)""", (name, contact, phone, email, contract, discount))
            conn.commit()
        flash("Assureur ajouté !", "success")
    return redirect("/insurance")



@admin_bp.route("/insurance/claim/add", methods=["POST"])
@login_required
def add_insurance_claim():
    customer_id = request.form.get("customer_id", type=int)
    car_id = request.form.get("car_id", type=int)
    insurance_id = request.form.get("insurance_id", type=int)
    claim_number = request.form.get("claim_number", "").strip()
    accident_date = request.form.get("accident_date", "")
    description = request.form.get("description", "").strip()
    estimated_cost = request.form.get("estimated_cost", 0, type=float)
    if customer_id and car_id and insurance_id:
        with get_db() as conn:
            conn.execute("""INSERT INTO insurance_claims 
                (customer_id, car_id, insurance_id, claim_number, accident_date, description, estimated_cost)
                VALUES (?,?,?,?,?,?,?)""",
                (customer_id, car_id, insurance_id, claim_number, accident_date, description, estimated_cost))
            conn.commit()
        flash("Dossier créé !", "success")
    return redirect("/insurance")



@admin_bp.route("/insurance/claim/update/<int:cid>", methods=["POST"])
@login_required
def update_insurance_claim(cid):
    status = request.form.get("status", "")
    approved = request.form.get("approved_amount", 0, type=float)
    notes = request.form.get("notes", "").strip()
    with get_db() as conn:
        conn.execute("UPDATE insurance_claims SET status=?, approved_amount=?, notes=? WHERE id=?",
                    (status, approved, notes, cid))
        conn.commit()
    flash("Dossier mis à jour", "success")
    return redirect("/insurance")



# ─── 2. Benchmarking Inter-Succursales ───

@admin_bp.route("/branch_benchmark")
@login_required
@admin_required
def branch_benchmark():
    with get_db() as conn:
        branches = conn.execute("SELECT * FROM branches WHERE active=1 ORDER BY name").fetchall()
        data = []
        for b in branches:
            bid = b['id']
            revenue = conn.execute("SELECT COALESCE(SUM(amount),0) FROM invoices WHERE branch_id=?", (bid,)).fetchone()[0]
            appts = conn.execute("SELECT COUNT(*) FROM appointments WHERE branch_id=?", (bid,)).fetchone()[0]
            completed = conn.execute("SELECT COUNT(*) FROM appointments WHERE branch_id=? AND status='completed'", (bid,)).fetchone()[0]
            staff = conn.execute("SELECT COUNT(*) FROM users WHERE branch_id=?", (bid,)).fetchone()[0]
            avg_quality = conn.execute("""SELECT AVG(qc.overall_score) FROM quality_checks qc 
                JOIN appointments a ON qc.appointment_id=a.id WHERE a.branch_id=?""", (bid,)).fetchone()[0] or 0
            avg_nps = conn.execute("""SELECT AVG(qc.nps_score) FROM quality_checks qc 
                JOIN appointments a ON qc.appointment_id=a.id WHERE a.branch_id=? AND qc.nps_score>0""", (bid,)).fetchone()[0] or 0
            tickets_open = conn.execute("""SELECT COUNT(*) FROM tickets t 
                JOIN customers cu ON t.customer_id=cu.id WHERE t.status IN ('open','in_progress')""").fetchone()[0]
            rev_per_staff = revenue / staff if staff else 0
            data.append({
                'branch': b, 'revenue': revenue, 'appointments': appts, 'completed': completed,
                'completion_rate': (completed/appts*100) if appts else 0,
                'staff': staff, 'rev_per_staff': rev_per_staff,
                'avg_quality': avg_quality, 'avg_nps': avg_nps, 'tickets': tickets_open
            })
    return render_template("branch_benchmark.html", data=data)



@admin_bp.route("/audit_trail")
@login_required
@admin_required
def audit_trail():
    page = request.args.get("page", 1, type=int)
    entity_filter = request.args.get("entity", "")
    user_filter = request.args.get("user", "", type=str)
    with get_db() as conn:
        where_clauses = []
        params = []
        if entity_filter:
            where_clauses.append("entity_type=?")
            params.append(entity_filter)
        if user_filter:
            where_clauses.append("username=?")
            params.append(user_filter)
        where = "WHERE " + " AND ".join(where_clauses) if where_clauses else ""
        total = conn.execute(f"SELECT COUNT(*) FROM audit_log {where}", params).fetchone()[0]
        logs = conn.execute(f"""SELECT * FROM audit_log {where} 
            ORDER BY created_at DESC LIMIT ? OFFSET ?""", params + [50, (page-1)*50]).fetchall()
        entity_types = conn.execute("SELECT DISTINCT entity_type FROM audit_log WHERE entity_type!='' ORDER BY entity_type").fetchall()
        usernames = conn.execute("SELECT DISTINCT username FROM audit_log WHERE username!='' ORDER BY username").fetchall()
    total_pages = (total + 49) // 50
    return render_template("audit_trail.html", logs=logs, page=page, total_pages=total_pages,
                          total=total, entity_types=entity_types, usernames=usernames,
                          entity_filter=entity_filter, user_filter=user_filter)



@admin_bp.route('/import_center/upload', methods=['POST'])
@login_required
def import_center_upload():
    import csv
    import io
    import_type = request.form['import_type']
    file = request.files.get('file')
    if not file or not file.filename.endswith('.csv'):
        flash("Veuillez fournir un fichier CSV valide", "danger")
        return redirect("/import_center")

    content = file.read().decode('utf-8-sig')
    reader = csv.DictReader(io.StringIO(content), delimiter=';')
    rows = list(reader)
    if not rows:
        flash("Fichier vide", "warning")
        return redirect("/import_center")

    imported = 0
    errors = 0
    error_details = []

    with get_db() as conn:
        for i, row in enumerate(rows):
            try:
                if import_type == 'services':
                    name = row.get('name', row.get('nom', '')).strip()
                    price = float(row.get('price', row.get('prix', 0)))
                    if name:
                        conn.execute("INSERT INTO services (name, price) VALUES (?,?)", (name, price))
                        imported += 1
                elif import_type == 'inventory':
                    name = row.get('name', row.get('nom', '')).strip()
                    qty = int(row.get('quantity', row.get('quantite', 0)))
                    price = float(row.get('price', row.get('prix', 0)))
                    if name:
                        conn.execute("INSERT INTO inventory (name, quantity, price) VALUES (?,?,?)",
                                   (name, qty, price))
                        imported += 1
                elif import_type == 'customers':
                    name = row.get('name', row.get('nom', '')).strip()
                    phone = row.get('phone', row.get('telephone', '')).strip()
                    if name:
                        conn.execute("INSERT INTO customers (name, phone) VALUES (?,?)", (name, phone))
                        imported += 1
                elif import_type == 'cars':
                    brand = row.get('brand', row.get('marque', '')).strip()
                    model = row.get('model', row.get('modele', '')).strip()
                    plate = row.get('plate', row.get('matricule', '')).strip()
                    cid = int(row.get('customer_id', row.get('client_id', 0)))
                    if brand and plate:
                        conn.execute("INSERT INTO cars (brand, model, plate, customer_id) VALUES (?,?,?,?)",
                                   (brand, model, plate, cid))
                        imported += 1
            except Exception as e:
                errors += 1
                error_details.append(f"Ligne {i+2}: {str(e)[:80]}")

        conn.execute("""INSERT INTO import_history
            (import_type, filename, total_rows, imported_rows, errors, error_details)
            VALUES (?,?,?,?,?,?)""",
            (import_type, file.filename, len(rows), imported, errors, str(error_details)))
        conn.commit()

    flash(f"Import terminé: {imported} importés, {errors} erreurs", "success" if errors == 0 else "warning")
    return redirect("/import_center")

# ── 9. PDF Report Builder ──
@admin_bp.route('/report_builder')
@login_required
def report_builder():
    with get_db() as conn:
        reports = conn.execute("SELECT * FROM report_builder ORDER BY created_at DESC").fetchall()
    return render_template('report_builder.html', reports=reports)


