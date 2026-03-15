from flask import Flask, render_template, request, redirect, url_for, flash, make_response, jsonify, session
from flask_wtf.csrf import CSRFProtect
from models.customer import get_all_customers, add_customer
from models.report import total_customers, total_appointments, total_revenue
from models.appointment import get_appointments
from models.invoice import get_all_invoices
from database.db import create_tables, get_db
import os
import io
import uuid
import time as time_module
import re
from functools import wraps
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)

# ─── Persistent Secret Key ───
def _get_secret_key():
    key_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.secret_key')
    env_key = os.environ.get('SECRET_KEY')
    if env_key:
        return env_key
    if os.path.exists(key_file):
        with open(key_file, 'rb') as f:
            return f.read()
    key = os.urandom(32)
    with open(key_file, 'wb') as f:
        f.write(key)
    os.chmod(key_file, 0o600)
    return key

app.secret_key = _get_secret_key()
csrf = CSRFProtect(app)
create_tables()

# ─── Rate Limiting (Login) ───
_login_attempts = {}  # ip -> (count, first_attempt_time)
LOGIN_MAX_ATTEMPTS = 5
LOGIN_LOCKOUT_SECONDS = 300  # 5 minutes

UPLOAD_FOLDER = 'static/uploads'
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp'}
MAX_FILE_SIZE = 5 * 1024 * 1024  # 5 MB
MAX_FILES = 5
PER_PAGE = 15

SERVICES_FALLBACK = [
    ('Lavage Normal', 35),
    ('Céramique Spray', 55),
    ('Detailing Intérieur', 100),
    ('Detailing Extérieur', 120),
    ('Detailing Complet', 200),
    ('Polissage', 350),
    ('Nano Céramique', 0),
    ('Correction Peinture', 0),
    ('Protection PPF', 0),
    ('Autre', 0),
]

def get_services():
    try:
        with get_db() as conn:
            rows = conn.execute("SELECT name, price FROM services WHERE active = 1 ORDER BY id").fetchall()
        return [(r[0], r[1]) for r in rows] if rows else SERVICES_FALLBACK
    except Exception:
        return SERVICES_FALLBACK

def get_setting(key, default=''):
    try:
        with get_db() as conn:
            row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row[0] if row else default
    except Exception:
        return default

def get_all_settings():
    try:
        with get_db() as conn:
            rows = conn.execute("SELECT key, value FROM settings").fetchall()
        return {r[0]: r[1] for r in rows}
    except Exception:
        return {}

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def safe_page(page):
    return max(1, min(page, 10000))

# ─── Authentication ───
def init_admin():
    with get_db() as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY, username TEXT UNIQUE, password TEXT, role TEXT DEFAULT 'employee')")
        admin = conn.execute("SELECT id FROM users WHERE username = 'admin'").fetchone()
        if not admin:
            conn.execute("INSERT INTO users (username, password, role) VALUES (?, ?, ?)",
                ('admin', generate_password_hash('admin123'), 'admin'))
            conn.commit()
        else:
            conn.execute("UPDATE users SET role = 'admin' WHERE username = 'admin' AND (role IS NULL OR role = 'employee')")
            conn.commit()

init_admin()

# ─── Security Headers ───
@app.after_request
def set_security_headers(response):
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'SAMEORIGIN'
    response.headers['X-XSS-Protection'] = '1; mode=block'
    response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
    return response

# ─── Session Configuration ───
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['PERMANENT_SESSION_LIFETIME'] = 86400  # 24h
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max upload

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('user_id'):
            return redirect('/login')
        return f(*args, **kwargs)
    return decorated

def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('user_id'):
            return redirect('/login')
        if session.get('role') != 'admin':
            flash('Accès administrateur requis', 'error')
            return redirect('/')
        return f(*args, **kwargs)
    return decorated

def log_activity(action, detail=''):
    with get_db() as conn:
        conn.execute("INSERT INTO activity_log (user_id, username, action, detail) VALUES (?,?,?,?)",
            (session.get('user_id'), session.get('username', ''), action, detail))
        conn.commit()

@app.route('/login', methods=['GET', 'POST'])
def login():
    if session.get('user_id'):
        return redirect('/')
    if request.method == 'POST':
        ip = request.remote_addr
        now = time_module.time()
        # Rate limiting check
        if ip in _login_attempts:
            attempts, first_time = _login_attempts[ip]
            if now - first_time > LOGIN_LOCKOUT_SECONDS:
                _login_attempts.pop(ip, None)
            elif attempts >= LOGIN_MAX_ATTEMPTS:
                remaining = int(LOGIN_LOCKOUT_SECONDS - (now - first_time))
                flash(f'Trop de tentatives. Réessayez dans {remaining}s', 'error')
                return render_template('login.html')
        username = request.form.get('username', '').strip().lower()
        password = request.form.get('password', '')
        with get_db() as conn:
            user = conn.execute("SELECT * FROM users WHERE LOWER(username) = ?", (username,)).fetchone()
        if user and check_password_hash(user[2], password):
            _login_attempts.pop(ip, None)
            session['user_id'] = user[0]
            session['username'] = user[1]
            session['role'] = user[3] if len(user) > 3 and user[3] else 'employee'
            return redirect('/')
        # Track failed attempt
        if ip in _login_attempts:
            _login_attempts[ip] = (_login_attempts[ip][0] + 1, _login_attempts[ip][1])
        else:
            _login_attempts[ip] = (1, now)
        flash('Nom d\'utilisateur ou mot de passe invalide', 'error')
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect('/login')

@app.route('/change_password', methods=['GET', 'POST'])
@login_required
def change_password():
    if request.method == 'POST':
        current = request.form.get('current_password', '')
        new_pass = request.form.get('new_password', '')
        confirm = request.form.get('confirm_password', '')
        with get_db() as conn:
            user = conn.execute("SELECT * FROM users WHERE id = ?", (session['user_id'],)).fetchone()
            if not check_password_hash(user[2], current):
                flash('Mot de passe actuel incorrect', 'error')
            elif len(new_pass) < 6:
                flash('Le nouveau mot de passe doit contenir au moins 6 caractères', 'error')
            elif new_pass != confirm:
                flash('Les mots de passe ne correspondent pas', 'error')
            else:
                conn.execute("UPDATE users SET password = ? WHERE id = ?",
                    (generate_password_hash(new_pass), session['user_id']))
                conn.commit()
                flash('Mot de passe modifié avec succès', 'success')
    return render_template('change_password.html')

@app.errorhandler(404)
def page_not_found(e):
    return render_template('404.html'), 404

@app.errorhandler(500)
def internal_error(e):
    return render_template('500.html'), 500

@app.route('/')
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
    }
    return render_template('index.html', stats=stats, pending_appointments=pending_appointments,
                           tomorrow_appointments=tomorrow_appointments)

@app.route('/customers')
@login_required
def customers():
    search = request.args.get('q', '').strip()
    page = safe_page(request.args.get('page', 1, type=int))
    with get_db() as conn:
        if search:
            total = conn.execute(
                "SELECT COUNT(*) FROM customers WHERE name LIKE ? OR phone LIKE ?",
                (f'%{search}%', f'%{search}%')
            ).fetchone()[0]
            all_customers = conn.execute(
                "SELECT * FROM customers WHERE name LIKE ? OR phone LIKE ? LIMIT ? OFFSET ?",
                (f'%{search}%', f'%{search}%', PER_PAGE, (page - 1) * PER_PAGE)
            ).fetchall()
        else:
            total = conn.execute("SELECT COUNT(*) FROM customers").fetchone()[0]
            all_customers = conn.execute(
                "SELECT * FROM customers LIMIT ? OFFSET ?",
                (PER_PAGE, (page - 1) * PER_PAGE)
            ).fetchall()
    total_pages = (total + PER_PAGE - 1) // PER_PAGE
    return render_template('customers.html', customers=all_customers, search=search,
                           page=page, total_pages=total_pages)

@app.route('/appointments')
@login_required
def appointments():
    page = safe_page(request.args.get('page', 1, type=int))
    status_filter = request.args.get('status', '')
    date_from = request.args.get('date_from', '')
    date_to = request.args.get('date_to', '')
    with get_db() as conn:
        base_q = ("FROM appointments a JOIN cars ca ON a.car_id = ca.id "
                  "JOIN customers cu ON ca.customer_id = cu.id")
        conditions = []
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

@app.route('/invoices')
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
@app.route('/add_customer', methods=['GET', 'POST'])
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

@app.route("/add_appointment", methods=["GET", "POST"])
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
        return redirect("/appointments")
    from models.customer import get_all_customers
    all_customers = get_all_customers()
    with get_db() as conn:
        all_cars = conn.execute("SELECT * FROM cars").fetchall()
        technicians = conn.execute("SELECT username, COALESCE(full_name, '') FROM users").fetchall()
    return render_template("add_appointment.html", customers=all_customers, cars=all_cars, services=get_services(), technicians=technicians)

@app.route("/add_invoice", methods=["GET", "POST"])
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
                flash('Facture ajoutée avec succès', 'success')
                return redirect("/invoices")
            except ValueError:
                flash('Entrez un montant positif valide', 'error')
    from models.appointment import get_appointments
    all_appointments = get_appointments()
    return render_template("add_invoice.html", appointments=all_appointments)

@app.route("/pay_invoice/<int:invoice_id>", methods=["POST"])
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
    return redirect("/invoices")

@app.route("/request_quote", methods=["GET", "POST"])
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

@app.route("/quotes")
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

@app.route("/add_car", methods=["GET", "POST"])
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

@app.route("/set_price/<int:quote_id>", methods=["POST"])
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

@app.route("/convert_quote/<int:quote_id>", methods=["GET", "POST"])
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

@app.route("/update_appointment/<int:appointment_id>/<status>", methods=["POST"])
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
    return redirect("/appointments")

@app.route("/customer/<int:customer_id>")
@login_required
def customer_detail(customer_id):
    with get_db() as conn:
        customer = conn.execute("SELECT * FROM customers WHERE id = ?", (customer_id,)).fetchone()
        if not customer:
            flash('Client introuvable', 'error')
            return redirect("/customers")
        cars = conn.execute("SELECT * FROM cars WHERE customer_id = ?", (customer_id,)).fetchall()
        appointments = conn.execute("SELECT a.* FROM appointments a JOIN cars c ON a.car_id = c.id WHERE c.customer_id = ? ORDER BY a.id DESC", (customer_id,)).fetchall()
        # CLV calculation
        total_spent = conn.execute(
            "SELECT COALESCE(SUM(i.amount),0) FROM invoices i JOIN appointments a ON i.appointment_id = a.id "
            "JOIN cars ca ON a.car_id = ca.id WHERE ca.customer_id = ? AND i.status = 'paid'",
            (customer_id,)).fetchone()[0]
        visit_count = conn.execute(
            "SELECT COUNT(*) FROM appointments a JOIN cars ca ON a.car_id = ca.id "
            "WHERE ca.customer_id = ? AND a.status = 'completed'", (customer_id,)).fetchone()[0]
        first_visit = conn.execute(
            "SELECT MIN(a.date) FROM appointments a JOIN cars ca ON a.car_id = ca.id "
            "WHERE ca.customer_id = ?", (customer_id,)).fetchone()[0]
        # Average rating
        avg_rating = conn.execute(
            "SELECT AVG(rating), COUNT(*) FROM ratings WHERE customer_id = ?",
            (customer_id,)).fetchone()
        # CLV tier
        if total_spent >= 1000:
            tier = 'OR'
        elif total_spent >= 500:
            tier = 'ARGENT'
        elif total_spent >= 200:
            tier = 'BRONZE'
        else:
            tier = '—'
        clv = {
            'total_spent': total_spent, 'visits': visit_count,
            'first_visit': first_visit or '—',
            'avg_rating': round(avg_rating[0], 1) if avg_rating[0] else 0,
            'rating_count': avg_rating[1],
            'tier': tier
        }
    return render_template("customer_detail.html", customer=customer, cars=cars,
                           appointments=appointments, clv=clv)

@app.route("/edit_customer/<int:customer_id>", methods=["GET", "POST"])
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

@app.route("/edit_car/<int:car_id>", methods=["GET", "POST"])
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

@app.route("/car/<int:car_id>")
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

@app.route("/delete_car/<int:car_id>", methods=["POST"])
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

@app.route("/delete_customer/<int:customer_id>", methods=["POST"])
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

@app.route("/delete_appointment/<int:appointment_id>", methods=["POST"])
@login_required
def delete_appointment(appointment_id):
    with get_db() as conn:
        conn.execute("DELETE FROM appointments WHERE id = ?", (appointment_id,))
        conn.commit()
    log_activity('Delete Appointment', f'Appointment #{appointment_id}')
    return redirect("/appointments")

@app.route("/delete_invoice/<int:invoice_id>", methods=["POST"])
@login_required
def delete_invoice(invoice_id):
    with get_db() as conn:
        conn.execute("DELETE FROM invoices WHERE id = ?", (invoice_id,))
        conn.commit()
    log_activity('Delete Invoice', f'Invoice #{invoice_id}')
    return redirect("/invoices")

@app.route("/edit_appointment/<int:appointment_id>", methods=["GET", "POST"])
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

@app.route("/edit_invoice/<int:invoice_id>", methods=["GET", "POST"])
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

@app.route("/edit_expense/<int:expense_id>", methods=["GET", "POST"])
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

@app.route("/delete_quote/<int:quote_id>", methods=["POST"])
@login_required
def delete_quote(quote_id):
    with get_db() as conn:
        conn.execute("DELETE FROM quotes WHERE id = ?", (quote_id,))
        conn.commit()
    log_activity('Delete Quote', f'Quote #{quote_id}')
    return redirect("/quotes")

# ─── Global Search ───
@app.route("/search")
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

# ─── User Management (Admin) ───
@app.route("/users")
@admin_required
def users_list():
    with get_db() as conn:
        users = conn.execute("SELECT id, username, role, COALESCE(full_name, '') FROM users ORDER BY id").fetchall()
    return render_template("users.html", users=users)

@app.route("/add_user", methods=["GET", "POST"])
@admin_required
def add_user():
    if request.method == "POST":
        username = request.form.get("username", "").strip().lower()
        password = request.form.get("password", "").strip()
        role = request.form.get("role", "employee")
        if not username or len(username) < 3:
            flash("Le nom d'utilisateur doit contenir au moins 3 caractères", "error")
            return render_template("add_user.html")
        if not password or len(password) < 6:
            flash("Le mot de passe doit contenir au moins 6 caractères", "error")
            return render_template("add_user.html")
        if role not in ('admin', 'employee'):
            role = 'employee'
        full_name = request.form.get("full_name", "").strip()
        with get_db() as conn:
            exists = conn.execute("SELECT id FROM users WHERE LOWER(username) = ?", (username,)).fetchone()
            if exists:
                flash("Ce nom d'utilisateur existe déjà", "error")
                return render_template("add_user.html")
            conn.execute("INSERT INTO users (username, password, role, full_name) VALUES (?,?,?,?)",
                (username, generate_password_hash(password), role, full_name))
            conn.commit()
        log_activity('Add User', f'User: {username} ({role})')
        flash(f"Utilisateur {username} ajouté avec succès", "success")
        return redirect("/users")
    return render_template("add_user.html")

@app.route("/delete_user/<int:user_id>", methods=["POST"])
@admin_required
def delete_user(user_id):
    if user_id == session.get('user_id'):
        flash("Impossible de supprimer votre propre compte", "error")
        return redirect("/users")
    with get_db() as conn:
        user = conn.execute("SELECT username FROM users WHERE id = ?", (user_id,)).fetchone()
        conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
        conn.commit()
    log_activity('Delete User', f'User: {user[0] if user else user_id}')
    flash("Utilisateur supprimé", "success")
    return redirect("/users")

# ─── Bulk Invoice Operations ───
@app.route("/bulk_pay_invoices", methods=["POST"])
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

# ─── Activity Log ───
@app.route("/activity_log")
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

@app.route("/daily")
@login_required
def daily():
    from datetime import date
    today = str(date.today())
    with get_db() as conn:
        appointments = conn.execute("SELECT a.id, cu.name, ca.brand, ca.model, a.date, a.service, a.status, COALESCE(a.time, '') FROM appointments a JOIN cars ca ON a.car_id = ca.id JOIN customers cu ON ca.customer_id = cu.id WHERE a.date = ?", (today,)).fetchall()
        revenue = conn.execute("SELECT SUM(amount) FROM invoices i JOIN appointments a ON i.appointment_id = a.id WHERE a.date = ? AND i.status = 'paid'", (today,)).fetchone()[0] or 0
        expenses_today = conn.execute("SELECT COALESCE(SUM(amount),0) FROM expenses WHERE date = ?", (today,)).fetchone()[0]
    return render_template("daily.html", appointments=appointments, revenue=revenue, today=today, expenses_today=expenses_today)

@app.route("/print_invoice/<int:invoice_id>")
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

@app.route("/calendar")
@login_required
def calendar_view():
    return render_template("calendar.html")

@app.route("/api/appointments_calendar")
@login_required
def appointments_calendar():
    with get_db() as conn:
        appointments = conn.execute(
            "SELECT a.id, cu.name, ca.brand || ' ' || ca.model, a.date, a.service, a.status, COALESCE(a.time, '') "
            "FROM appointments a JOIN cars ca ON a.car_id = ca.id "
            "JOIN customers cu ON ca.customer_id = cu.id"
        ).fetchall()
    events = []
    colors = {'pending': '#D4AF37', 'completed': '#2d6a4f', 'cancelled': '#555', 'in_progress': '#1B6B93'}
    for a in appointments:
        start = a[3]
        if a[6]:
            start = f"{a[3]}T{a[6]}"
        events.append({
            'id': a[0],
            'title': f"{a[1]} — {a[4]}",
            'start': start,
            'color': colors.get(a[5], '#D4AF37'),
            'extendedProps': {'car': a[2], 'status': a[5]}
        })
    return jsonify(events)

# ─── Calendar Drag-Drop Reschedule ───
@app.route("/api/reschedule_appointment", methods=["POST"])
@login_required
def reschedule_appointment():
    data = request.get_json()
    if not data or 'id' not in data or 'date' not in data:
        return jsonify({'error': 'Données manquantes'}), 400
    appt_id = data['id']
    new_date = data['date']
    new_time = data.get('time', '')
    if not re.match(r'^\d{4}-\d{2}-\d{2}$', new_date):
        return jsonify({'error': 'Format de date invalide'}), 400
    # Validate date is real
    from datetime import datetime
    try:
        datetime.strptime(new_date, '%Y-%m-%d')
    except ValueError:
        return jsonify({'error': 'Date invalide'}), 400
    with get_db() as conn:
        appt = conn.execute("SELECT id, status FROM appointments WHERE id = ?", (appt_id,)).fetchone()
        if not appt:
            return jsonify({'error': 'Rendez-vous introuvable'}), 404
        if appt[1] in ('completed', 'cancelled'):
            return jsonify({'error': 'Impossible de déplacer un rendez-vous terminé ou annulé'}), 400
        if new_time:
            conflict = conn.execute(
                "SELECT id FROM appointments WHERE date = ? AND time = ? AND id != ? AND status != 'cancelled'",
                (new_date, new_time, appt_id)).fetchone()
            if conflict:
                return jsonify({'error': f'Le créneau {new_time} du {new_date} est déjà réservé'}), 409
            conn.execute("UPDATE appointments SET date = ?, time = ? WHERE id = ?", (new_date, new_time, appt_id))
        else:
            conn.execute("UPDATE appointments SET date = ? WHERE id = ?", (new_date, appt_id))
        conn.commit()
    log_activity('Reschedule', f'Appointment #{appt_id} → {new_date} {new_time}')
    return jsonify({'success': True})

# ─── AJAX Search API ───
@app.route("/api/search")
@login_required
def api_search():
    q = request.args.get('q', '').strip()
    if not q or len(q) < 2:
        return jsonify([])
    with get_db() as conn:
        customers = conn.execute(
            "SELECT 'client' as type, id, name, phone FROM customers WHERE name LIKE ? OR phone LIKE ? LIMIT 5",
            (f'%{q}%', f'%{q}%')).fetchall()
        cars = conn.execute(
            "SELECT 'voiture' as type, ca.id, ca.brand || ' ' || ca.model, ca.plate "
            "FROM cars ca WHERE ca.brand LIKE ? OR ca.model LIKE ? OR ca.plate LIKE ? LIMIT 5",
            (f'%{q}%', f'%{q}%', f'%{q}%')).fetchall()
    results = []
    for c in customers:
        results.append({'type': 'client', 'id': c[1], 'label': c[2], 'sub': c[3], 'url': f'/customer/{c[1]}'})
    for c in cars:
        results.append({'type': 'voiture', 'id': c[1], 'label': c[2], 'sub': c[3], 'url': f'/car/{c[1]}'})
    return jsonify(results)

@app.route("/monthly")
@login_required
def monthly():
    from datetime import date, timedelta
    month_param = request.args.get("month")
    if month_param:
        year, mon = map(int, month_param.split("-"))
    else:
        today = date.today()
        year, mon = today.year, today.month
    month_start = f"{year}-{mon:02d}-01"
    if mon == 12:
        next_y, next_m = year + 1, 1
    else:
        next_y, next_m = year, mon + 1
    month_end = f"{next_y}-{next_m:02d}-01"
    if mon == 1:
        prev_y, prev_m = year - 1, 12
    else:
        prev_y, prev_m = year, mon - 1
    months_ar = ["JAN","FEB","MAR","APR","MAY","JUN","JUL","AUG","SEP","OCT","NOV","DEC"]
    month_label = f"{months_ar[mon-1]} {year}"
    prev_label = f"{months_ar[prev_m-1]} {prev_y}"
    next_label = f"{months_ar[next_m-1]} {next_y}"
    with get_db() as conn:
        appointments = conn.execute(
            "SELECT a.id, cu.name, ca.brand, ca.model, a.date, a.service, a.status "
            "FROM appointments a JOIN cars ca ON a.car_id = ca.id "
            "JOIN customers cu ON ca.customer_id = cu.id "
            "WHERE a.date >= ? AND a.date < ? ORDER BY a.date",
            (month_start, month_end)).fetchall()
        invoices = conn.execute(
            "SELECT i.id, cu.name, a.service, i.amount, i.status "
            "FROM invoices i JOIN appointments a ON i.appointment_id = a.id "
            "JOIN cars ca ON a.car_id = ca.id JOIN customers cu ON ca.customer_id = cu.id "
            "WHERE a.date >= ? AND a.date < ? ORDER BY i.id",
            (month_start, month_end)).fetchall()
        revenue = conn.execute(
            "SELECT COALESCE(SUM(i.amount),0) FROM invoices i JOIN appointments a ON i.appointment_id = a.id "
            "WHERE a.date >= ? AND a.date < ? AND i.status = 'paid'",
            (month_start, month_end)).fetchone()[0]
        unpaid = conn.execute(
            "SELECT COALESCE(SUM(i.amount),0) FROM invoices i JOIN appointments a ON i.appointment_id = a.id "
            "WHERE a.date >= ? AND a.date < ? AND i.status = 'unpaid'",
            (month_start, month_end)).fetchone()[0]
        completed = sum(1 for a in appointments if a[6] == 'completed')
        month_expenses = conn.execute(
            "SELECT COALESCE(SUM(amount),0) FROM expenses WHERE date >= ? AND date < ?",
            (month_start, month_end)).fetchone()[0]
    stats = {
        'appointments': len(appointments),
        'completed': completed,
        'revenue': revenue,
        'unpaid': unpaid,
        'expenses': month_expenses,
        'profit': revenue - month_expenses
    }
    return render_template("monthly.html",
        stats=stats, appointments=appointments, invoices=invoices,
        month_label=month_label,
        current_month=f"{year}-{mon:02d}",
        prev_month=f"{prev_y}-{prev_m:02d}", prev_label=prev_label,
        next_month=f"{next_y}-{next_m:02d}", next_label=next_label)

@app.route("/download_invoice/<int:invoice_id>")
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

@app.route("/api/chart_data")
@login_required
def chart_data():
    from datetime import date
    with get_db() as conn:
        today = date.today()
        months = []
        revenue_data = []
        expenses_data = []
        appointments_data = []
        for i in range(5, -1, -1):
            m = today.month - i
            y = today.year
            while m <= 0:
                m += 12
                y -= 1
            ms = f"{y}-{m:02d}-01"
            if m == 12:
                me = f"{y+1}-01-01"
            else:
                me = f"{y}-{m+1:02d}-01"
            rev = conn.execute(
                "SELECT COALESCE(SUM(i.amount),0) FROM invoices i JOIN appointments a ON i.appointment_id = a.id "
                "WHERE a.date >= ? AND a.date < ? AND i.status = 'paid'", (ms, me)).fetchone()[0]
            exp = conn.execute(
                "SELECT COALESCE(SUM(amount),0) FROM expenses WHERE date >= ? AND date < ?", (ms, me)).fetchone()[0]
            apt = conn.execute(
                "SELECT COUNT(*) FROM appointments a JOIN cars ca ON a.car_id = ca.id "
                "JOIN customers cu ON ca.customer_id = cu.id "
                "WHERE a.date >= ? AND a.date < ?", (ms, me)).fetchone()[0]
            month_names = ["JAN","FEB","MAR","APR","MAY","JUN","JUL","AUG","SEP","OCT","NOV","DEC"]
            months.append(month_names[m-1])
            revenue_data.append(float(rev))
            expenses_data.append(float(exp))
            appointments_data.append(apt)
        # Service distribution
        services = conn.execute(
            "SELECT a.service, COUNT(*) FROM appointments a "
            "JOIN cars ca ON a.car_id = ca.id JOIN customers cu ON ca.customer_id = cu.id "
            "GROUP BY a.service ORDER BY COUNT(*) DESC LIMIT 6"
        ).fetchall()
        # Expense categories distribution
        expense_cats = conn.execute(
            "SELECT category, COALESCE(SUM(amount),0) FROM expenses GROUP BY category ORDER BY SUM(amount) DESC LIMIT 6"
        ).fetchall()
    return jsonify({
        'months': months,
        'revenue': revenue_data,
        'expenses': expenses_data,
        'appointments': appointments_data,
        'services': {'labels': [s[0][:20] for s in services], 'data': [s[1] for s in services]},
        'expense_categories': {'labels': [e[0] for e in expense_cats], 'data': [float(e[1]) for e in expense_cats]}
    })

# ─── Expenses ───
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

@app.route("/expenses")
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

@app.route("/add_expense", methods=["GET", "POST"])
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

@app.route("/delete_expense/<int:expense_id>", methods=["POST"])
@login_required
def delete_expense(expense_id):
    with get_db() as conn:
        conn.execute("DELETE FROM expenses WHERE id = ?", (expense_id,))
        conn.commit()
    return redirect("/expenses")

# ─── CSV Export ───
@app.route("/export/monthly")
@login_required
def export_monthly_csv():
    import csv
    month = request.args.get("month", "")
    if not month:
        from datetime import date
        today = date.today()
        month = f"{today.year}-{today.month:02d}"
    year, mon = map(int, month.split("-"))
    ms = f"{year}-{mon:02d}-01"
    if mon == 12:
        me = f"{year+1}-01-01"
    else:
        me = f"{year}-{mon+1:02d}-01"
    with get_db() as conn:
        appointments = conn.execute(
            "SELECT a.id, cu.name, ca.brand || ' ' || ca.model, a.date, a.service, a.status "
            "FROM appointments a JOIN cars ca ON a.car_id = ca.id "
            "JOIN customers cu ON ca.customer_id = cu.id "
            "WHERE a.date >= ? AND a.date < ? ORDER BY a.date", (ms, me)).fetchall()
        invoices = conn.execute(
            "SELECT i.id, cu.name, a.service, i.amount, i.status "
            "FROM invoices i JOIN appointments a ON i.appointment_id = a.id "
            "JOIN cars ca ON a.car_id = ca.id JOIN customers cu ON ca.customer_id = cu.id "
            "WHERE a.date >= ? AND a.date < ?", (ms, me)).fetchall()
        expenses = conn.execute(
            "SELECT id, date, category, description, amount FROM expenses "
            "WHERE date >= ? AND date < ?", (ms, me)).fetchall()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["=== RENDEZ-VOUS ==="])
    writer.writerow(["ID", "Client", "Voiture", "Date", "Service", "Statut"])
    for a in appointments:
        writer.writerow(a)
    writer.writerow([])
    writer.writerow(["=== FACTURES ==="])
    writer.writerow(["ID", "Client", "Service", "Montant (DT)", "Statut"])
    for inv in invoices:
        writer.writerow(inv)
    writer.writerow([])
    writer.writerow(["=== DÉPENSES ==="])
    writer.writerow(["ID", "Date", "Catégorie", "Description", "Montant (DT)"])
    for e in expenses:
        writer.writerow(e)
    response = make_response(output.getvalue())
    response.headers['Content-Type'] = 'text/csv; charset=utf-8'
    response.headers['Content-Disposition'] = f'attachment; filename=monthly_report_{month}.csv'
    return response

@app.route("/export/customers")
@login_required
def export_customers_csv():
    import csv
    with get_db() as conn:
        customers = conn.execute("SELECT id, name, phone, notes FROM customers ORDER BY id").fetchall()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["ID", "Nom", "Téléphone", "Notes"])
    for c in customers:
        writer.writerow(c)
    response = make_response(output.getvalue())
    response.headers['Content-Type'] = 'text/csv; charset=utf-8'
    response.headers['Content-Disposition'] = 'attachment; filename=customers.csv'
    return response

@app.route("/export/expenses")
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

# ─── Daily CSV Export ───
@app.route("/export/daily")
@login_required
def export_daily_csv():
    import csv
    from datetime import date
    day = request.args.get("date", str(date.today()))
    with get_db() as conn:
        appointments = conn.execute(
            "SELECT a.id, cu.name, ca.brand || ' ' || ca.model, a.date, a.service, a.status "
            "FROM appointments a JOIN cars ca ON a.car_id = ca.id "
            "JOIN customers cu ON ca.customer_id = cu.id WHERE a.date = ? ORDER BY a.id", (day,)).fetchall()
        revenue = conn.execute(
            "SELECT COALESCE(SUM(amount),0) FROM invoices i JOIN appointments a ON i.appointment_id = a.id "
            "WHERE a.date = ? AND i.status = 'paid'", (day,)).fetchone()[0]
        expenses = conn.execute(
            "SELECT id, date, category, description, amount FROM expenses WHERE date = ?", (day,)).fetchall()
        exp_total = conn.execute(
            "SELECT COALESCE(SUM(amount),0) FROM expenses WHERE date = ?", (day,)).fetchone()[0]
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([f"Rapport Journalier — {day}"])
    writer.writerow([f"Revenus: {revenue} DT", f"Dépenses: {exp_total} DT", f"Bénéfice: {revenue - exp_total} DT"])
    writer.writerow([])
    writer.writerow(["=== RENDEZ-VOUS ==="])
    writer.writerow(["ID", "Client", "Voiture", "Date", "Service", "Statut"])
    for a in appointments:
        writer.writerow(a)
    writer.writerow([])
    writer.writerow(["=== DÉPENSES ==="])
    writer.writerow(["ID", "Date", "Catégorie", "Description", "Montant (DT)"])
    for e in expenses:
        writer.writerow(e)
    response = make_response(output.getvalue())
    response.headers['Content-Type'] = 'text/csv; charset=utf-8'
    response.headers['Content-Disposition'] = f'attachment; filename=daily_report_{day}.csv'
    return response

# ─── Services Management (Admin) ───
@app.route("/services")
@admin_required
def services_list():
    with get_db() as conn:
        all_services = conn.execute("SELECT * FROM services ORDER BY id").fetchall()
    return render_template("services.html", services=all_services)

@app.route("/add_service", methods=["POST"])
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

@app.route("/edit_service/<int:service_id>", methods=["POST"])
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

@app.route("/delete_service/<int:service_id>", methods=["POST"])
@admin_required
def delete_service(service_id):
    with get_db() as conn:
        conn.execute("DELETE FROM services WHERE id = ?", (service_id,))
        conn.commit()
    log_activity('Delete Service', f'Service #{service_id}')
    flash("Service supprimé", "success")
    return redirect("/services")

# ─── Settings (Admin) ───
@app.route("/settings", methods=["GET", "POST"])
@admin_required
def settings_page():
    if request.method == "POST":
        with get_db() as conn:
            keys = ['shop_name', 'shop_tagline', 'shop_address', 'shop_phone', 'tax_rate',
                    'smtp_host', 'smtp_port', 'smtp_user', 'smtp_pass', 'smtp_from',
                    'sms_api_url', 'sms_api_key', 'sms_sender']
            for key in keys:
                val = request.form.get(key, "").strip()
                conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, val))
            conn.commit()
        log_activity('Update Settings', 'Shop settings updated')
        flash("Paramètres enregistrés avec succès", "success")
        return redirect("/settings")
    settings = get_all_settings()
    return render_template("settings.html", settings=settings)

# ─── Maintenance Reminders ───
@app.route("/maintenance_reminders")
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

# ─── Technician Performance ───
@app.route("/technician_performance")
@admin_required
def technician_performance():
    with get_db() as conn:
        users = conn.execute("SELECT id, username, COALESCE(full_name, '') FROM users ORDER BY id").fetchall()
        performance = []
        for u in users:
            username = u[1]
            full_name = u[2]
            total_jobs = conn.execute(
                "SELECT COUNT(*) FROM appointments WHERE assigned_to = ?", (username,)).fetchone()[0]
            completed_jobs = conn.execute(
                "SELECT COUNT(*) FROM appointments WHERE assigned_to = ? AND status = 'completed'", (username,)).fetchone()[0]
            in_progress = conn.execute(
                "SELECT COUNT(*) FROM appointments WHERE assigned_to = ? AND status = 'in_progress'", (username,)).fetchone()[0]
            revenue = conn.execute(
                "SELECT COALESCE(SUM(i.amount), 0) FROM invoices i JOIN appointments a ON i.appointment_id = a.id "
                "WHERE a.assigned_to = ? AND i.status = 'paid'", (username,)).fetchone()[0]
            performance.append({
                'username': username, 'full_name': full_name,
                'total': total_jobs, 'completed': completed_jobs,
                'in_progress': in_progress, 'revenue': revenue
            })
    return render_template("technician_performance.html", performance=performance)

# ─── Appointment Photos ───
@app.route("/upload_photos/<int:appointment_id>", methods=["POST"])
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

@app.route("/gallery/<int:appointment_id>")
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

@app.route("/delete_photo/<int:appointment_id>", methods=["POST"])
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

# ─── Loyalty System ───
LOYALTY_SERVICES = ['Lavage Normal', 'Détailing Intérieur', 'Détailing Extérieur',
                    'Detailing Intérieur', 'Detailing Extérieur', 'Lavage']
LOYALTY_THRESHOLD = 5  # كل 5 غسلات → السادسة مجانية

@app.route("/loyalty")
@login_required
def loyalty_page():
    with get_db() as conn:
        loyalty_data = conn.execute(
            "SELECT l.id, cu.id, cu.name, cu.phone, l.service_type, l.wash_count, l.free_washes_used "
            "FROM loyalty l JOIN customers cu ON l.customer_id = cu.id "
            "ORDER BY l.wash_count DESC"
        ).fetchall()
        all_customers = conn.execute("SELECT id, name, phone FROM customers ORDER BY name").fetchall()
    entries = []
    for row in loyalty_data:
        washes = row[5]
        free_used = row[6]
        free_earned = washes // LOYALTY_THRESHOLD
        free_available = free_earned - free_used
        entries.append({
            'id': row[0], 'customer_id': row[1], 'name': row[2], 'phone': row[3],
            'service_type': row[4], 'wash_count': washes,
            'free_earned': free_earned, 'free_used': free_used,
            'free_available': max(0, free_available),
            'progress': washes % LOYALTY_THRESHOLD
        })
    return render_template("loyalty.html", entries=entries, threshold=LOYALTY_THRESHOLD, customers=all_customers)

@app.route("/loyalty/add_wash", methods=["POST"])
@login_required
def loyalty_add_wash():
    customer_id = request.form.get("customer_id", "")
    service_type = request.form.get("service_type", "Lavage Normal")
    if not customer_id:
        flash("Sélectionnez un client", "error")
        return redirect("/loyalty")
    with get_db() as conn:
        existing = conn.execute(
            "SELECT id, wash_count FROM loyalty WHERE customer_id = ? AND service_type = ?",
            (customer_id, service_type)).fetchone()
        if existing:
            new_count = existing[1] + 1
            conn.execute("UPDATE loyalty SET wash_count = ? WHERE id = ?", (new_count, existing[0]))
        else:
            conn.execute("INSERT INTO loyalty (customer_id, service_type, wash_count) VALUES (?, ?, 1)",
                (customer_id, service_type))
            new_count = 1
        conn.commit()
    if new_count % LOYALTY_THRESHOLD == 0:
        flash(f"🎉 Le client a gagné un lavage GRATUIT ! (lavage #{new_count})", "success")
    else:
        remaining = LOYALTY_THRESHOLD - (new_count % LOYALTY_THRESHOLD)
        flash(f"Lavage #{new_count} enregistré. Encore {remaining} pour un lavage gratuit !", "success")
    log_activity('Loyalty Wash', f'Customer #{customer_id} — {service_type} (#{new_count})')
    return redirect("/loyalty")

@app.route("/loyalty/use_free", methods=["POST"])
@login_required
def loyalty_use_free():
    loyalty_id = request.form.get("loyalty_id", "")
    with get_db() as conn:
        row = conn.execute("SELECT wash_count, free_washes_used FROM loyalty WHERE id = ?", (loyalty_id,)).fetchone()
        if row:
            free_earned = row[0] // LOYALTY_THRESHOLD
            if row[1] < free_earned:
                conn.execute("UPDATE loyalty SET free_washes_used = free_washes_used + 1 WHERE id = ?", (loyalty_id,))
                conn.commit()
                flash("Lavage gratuit utilisé avec succès !", "success")
            else:
                flash("Aucun lavage gratuit disponible", "error")
    return redirect("/loyalty")

# ─── Email Invoice ───
@app.route("/email_invoice/<int:invoice_id>", methods=["POST"])
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

# ─── Database Backup ───
@app.route("/backup")
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

# ─── KPI Dashboard ───
@app.route("/kpi")
@login_required
def kpi_dashboard():
    from datetime import date, timedelta
    with get_db() as conn:
        today = date.today()
        # Current month boundaries
        ms = f"{today.year}-{today.month:02d}-01"
        if today.month == 12:
            me = f"{today.year+1}-01-01"
        else:
            me = f"{today.year}-{today.month+1:02d}-01"
        # Previous month boundaries
        if today.month == 1:
            pms = f"{today.year-1}-12-01"
            pme = ms
        else:
            pms = f"{today.year}-{today.month-1:02d}-01"
            pme = ms
        # Current month stats
        curr_revenue = conn.execute(
            "SELECT COALESCE(SUM(i.amount),0) FROM invoices i JOIN appointments a ON i.appointment_id = a.id "
            "WHERE a.date >= ? AND a.date < ? AND i.status = 'paid'", (ms, me)).fetchone()[0]
        curr_appts = conn.execute("SELECT COUNT(*) FROM appointments WHERE date >= ? AND date < ?", (ms, me)).fetchone()[0]
        curr_completed = conn.execute("SELECT COUNT(*) FROM appointments WHERE date >= ? AND date < ? AND status = 'completed'", (ms, me)).fetchone()[0]
        curr_new_customers = conn.execute(
            "SELECT COUNT(DISTINCT ca.customer_id) FROM appointments a JOIN cars ca ON a.car_id = ca.id "
            "WHERE a.date >= ? AND a.date < ? AND ca.customer_id NOT IN "
            "(SELECT DISTINCT ca2.customer_id FROM appointments a2 JOIN cars ca2 ON a2.car_id = ca2.id WHERE a2.date < ?)",
            (ms, me, ms)).fetchone()[0]
        # Previous month stats
        prev_revenue = conn.execute(
            "SELECT COALESCE(SUM(i.amount),0) FROM invoices i JOIN appointments a ON i.appointment_id = a.id "
            "WHERE a.date >= ? AND a.date < ? AND i.status = 'paid'", (pms, pme)).fetchone()[0]
        prev_appts = conn.execute("SELECT COUNT(*) FROM appointments WHERE date >= ? AND date < ?", (pms, pme)).fetchone()[0]
        # Completion rate
        completion_rate = round(curr_completed / curr_appts * 100) if curr_appts > 0 else 0
        # Average revenue per day (this month)
        days_elapsed = max(1, (today - date(today.year, today.month, 1)).days + 1)
        avg_daily = round(curr_revenue / days_elapsed, 1)
        # Average rating this month
        avg_rating = conn.execute(
            "SELECT AVG(r.rating), COUNT(*) FROM ratings r WHERE r.created_at >= ?", (ms,)).fetchone()
        # Returning customers rate
        total_customers_visited = conn.execute(
            "SELECT COUNT(DISTINCT ca.customer_id) FROM appointments a JOIN cars ca ON a.car_id = ca.id "
            "WHERE a.date >= ? AND a.date < ?", (ms, me)).fetchone()[0]
        returning = total_customers_visited - curr_new_customers if total_customers_visited > curr_new_customers else 0
        return_rate = round(returning / total_customers_visited * 100) if total_customers_visited > 0 else 0
        # Revenue growth
        revenue_growth = round((curr_revenue - prev_revenue) / prev_revenue * 100) if prev_revenue > 0 else 0
        # Top technicians this month
        top_techs = conn.execute(
            "SELECT a.assigned_to, COUNT(*) as cnt, COALESCE(SUM(i.amount),0) "
            "FROM appointments a LEFT JOIN invoices i ON i.appointment_id = a.id AND i.status = 'paid' "
            "WHERE a.date >= ? AND a.date < ? AND a.assigned_to != '' "
            "GROUP BY a.assigned_to ORDER BY cnt DESC LIMIT 5", (ms, me)).fetchall()
    kpi = {
        'revenue': curr_revenue, 'prev_revenue': prev_revenue, 'revenue_growth': revenue_growth,
        'appointments': curr_appts, 'prev_appointments': prev_appts,
        'completion_rate': completion_rate, 'avg_daily': avg_daily,
        'avg_rating': round(avg_rating[0], 1) if avg_rating[0] else 0,
        'rating_count': avg_rating[1],
        'new_customers': curr_new_customers, 'return_rate': return_rate,
        'top_techs': top_techs
    }
    month_names = ["Janvier","Février","Mars","Avril","Mai","Juin","Juillet","Août","Septembre","Octobre","Novembre","Décembre"]
    return render_template("kpi.html", kpi=kpi, month_label=f"{month_names[today.month-1]} {today.year}")

# ─── Advanced Financial Reports ───
@app.route("/reports")
@login_required
def reports():
    from datetime import date
    with get_db() as conn:
        today = date.today()
        # Monthly comparison - last 6 months
        months_data = []
        for i in range(5, -1, -1):
            m = today.month - i
            y = today.year
            while m <= 0:
                m += 12
                y -= 1
            ms = f"{y}-{m:02d}-01"
            if m == 12:
                me = f"{y+1}-01-01"
            else:
                me = f"{y}-{m+1:02d}-01"
            rev = conn.execute(
                "SELECT COALESCE(SUM(i.amount),0) FROM invoices i JOIN appointments a ON i.appointment_id = a.id "
                "WHERE a.date >= ? AND a.date < ? AND i.status = 'paid'", (ms, me)).fetchone()[0]
            exp = conn.execute(
                "SELECT COALESCE(SUM(amount),0) FROM expenses WHERE date >= ? AND date < ?", (ms, me)).fetchone()[0]
            appts = conn.execute(
                "SELECT COUNT(*) FROM appointments WHERE date >= ? AND date < ?", (ms, me)).fetchone()[0]
            month_names = ["JAN","FEB","MAR","APR","MAY","JUN","JUL","AUG","SEP","OCT","NOV","DEC"]
            months_data.append({
                'label': f"{month_names[m-1]} {y}", 'revenue': rev,
                'expenses': exp, 'profit': rev - exp, 'appointments': appts
            })
        # Top 5 most profitable services
        top_services = conn.execute(
            "SELECT a.service, COUNT(*) as cnt, COALESCE(SUM(i.amount),0) as total "
            "FROM appointments a LEFT JOIN invoices i ON i.appointment_id = a.id AND i.status = 'paid' "
            "GROUP BY a.service ORDER BY total DESC LIMIT 5"
        ).fetchall()
        # Top 5 spending customers
        top_customers = conn.execute(
            "SELECT cu.id, cu.name, COALESCE(SUM(i.amount),0) as total, COUNT(DISTINCT a.id) as visits "
            "FROM customers cu JOIN cars ca ON ca.customer_id = cu.id "
            "JOIN appointments a ON a.car_id = ca.id "
            "LEFT JOIN invoices i ON i.appointment_id = a.id AND i.status = 'paid' "
            "GROUP BY cu.id ORDER BY total DESC LIMIT 5"
        ).fetchall()
        # Payment method breakdown
        payment_methods = conn.execute(
            "SELECT COALESCE(payment_method, 'N/A'), COUNT(*), COALESCE(SUM(amount),0) "
            "FROM invoices WHERE status = 'paid' GROUP BY payment_method ORDER BY SUM(amount) DESC"
        ).fetchall()
        # Invoice stats
        total_paid = conn.execute("SELECT COALESCE(SUM(amount),0) FROM invoices WHERE status = 'paid'").fetchone()[0]
        total_unpaid = conn.execute("SELECT COALESCE(SUM(amount),0) FROM invoices WHERE status = 'unpaid'").fetchone()[0]
        total_partial = conn.execute("SELECT COALESCE(SUM(amount - COALESCE(paid_amount,0)),0) FROM invoices WHERE status = 'partial'").fetchone()[0]
    return render_template("reports.html", months_data=months_data, top_services=top_services,
                           top_customers=top_customers, payment_methods=payment_methods,
                           total_paid=total_paid, total_unpaid=total_unpaid, total_partial=total_partial)

# ─── Customer Report Export ───
@app.route("/customer_report/<int:customer_id>")
@login_required
def customer_report(customer_id):
    import csv
    with get_db() as conn:
        customer = conn.execute("SELECT * FROM customers WHERE id = ?", (customer_id,)).fetchone()
        if not customer:
            flash('Client introuvable', 'error')
            return redirect("/customers")
        cars = conn.execute("SELECT * FROM cars WHERE customer_id = ?", (customer_id,)).fetchall()
        appointments = conn.execute(
            "SELECT a.id, a.date, a.service, a.status, ca.brand, ca.model "
            "FROM appointments a JOIN cars ca ON a.car_id = ca.id "
            "WHERE ca.customer_id = ? ORDER BY a.date DESC", (customer_id,)).fetchall()
        invoices = conn.execute(
            "SELECT i.id, a.date, a.service, i.amount, i.status, i.payment_method "
            "FROM invoices i JOIN appointments a ON i.appointment_id = a.id "
            "JOIN cars ca ON a.car_id = ca.id WHERE ca.customer_id = ? ORDER BY a.date DESC", (customer_id,)).fetchall()
        total_spent = conn.execute(
            "SELECT COALESCE(SUM(i.amount),0) FROM invoices i JOIN appointments a ON i.appointment_id = a.id "
            "JOIN cars ca ON a.car_id = ca.id WHERE ca.customer_id = ? AND i.status = 'paid'", (customer_id,)).fetchone()[0]
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([f"Rapport Client — {customer[1]}"])
    writer.writerow([f"Téléphone: {customer[2]}", f"Email: {customer[4] if len(customer) > 4 else ''}"])
    writer.writerow([f"Total Dépensé: {total_spent} DT"])
    writer.writerow([])
    writer.writerow(["=== VOITURES ==="])
    writer.writerow(["Marque", "Modèle", "Plaque", "Année", "Couleur"])
    for c in cars:
        writer.writerow([c[2], c[3], c[4], c[5] if len(c) > 5 else '', c[6] if len(c) > 6 else ''])
    writer.writerow([])
    writer.writerow(["=== RENDEZ-VOUS ==="])
    writer.writerow(["ID", "Date", "Service", "Statut", "Voiture"])
    for a in appointments:
        writer.writerow([a[0], a[1], a[2], a[3], f"{a[4]} {a[5]}"])
    writer.writerow([])
    writer.writerow(["=== FACTURES ==="])
    writer.writerow(["ID", "Date", "Service", "Montant (DT)", "Statut", "Paiement"])
    for inv in invoices:
        writer.writerow(inv)
    response = make_response(output.getvalue())
    response.headers['Content-Type'] = 'text/csv; charset=utf-8'
    safe_name = customer[1].replace(' ', '_')
    response.headers['Content-Disposition'] = f'attachment; filename=customer_report_{safe_name}.csv'
    return response

# ─── SMS Notifications ───
@app.route("/send_sms_reminders", methods=["POST"])
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
@app.route("/notifications")
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

# ─── Monthly Report PDF Export ───
@app.route("/export/monthly_pdf")
@login_required
def export_monthly_pdf():
    from xhtml2pdf import pisa
    from datetime import date
    month = request.args.get("month", "")
    if not month:
        today = date.today()
        month = f"{today.year}-{today.month:02d}"
    year, mon = map(int, month.split("-"))
    ms = f"{year}-{mon:02d}-01"
    if mon == 12:
        me = f"{year+1}-01-01"
    else:
        me = f"{year}-{mon+1:02d}-01"
    month_names_fr = ["Janvier","Février","Mars","Avril","Mai","Juin","Juillet","Août","Septembre","Octobre","Novembre","Décembre"]
    month_label = f"{month_names_fr[mon-1]} {year}"
    with get_db() as conn:
        revenue = conn.execute(
            "SELECT COALESCE(SUM(i.amount),0) FROM invoices i JOIN appointments a ON i.appointment_id = a.id "
            "WHERE a.date >= ? AND a.date < ? AND i.status = 'paid'", (ms, me)).fetchone()[0]
        expenses_total = conn.execute(
            "SELECT COALESCE(SUM(amount),0) FROM expenses WHERE date >= ? AND date < ?", (ms, me)).fetchone()[0]
        appt_count = conn.execute(
            "SELECT COUNT(*) FROM appointments WHERE date >= ? AND date < ?", (ms, me)).fetchone()[0]
        completed_count = conn.execute(
            "SELECT COUNT(*) FROM appointments WHERE date >= ? AND date < ? AND status = 'completed'", (ms, me)).fetchone()[0]
        unpaid_total = conn.execute(
            "SELECT COALESCE(SUM(i.amount),0) FROM invoices i JOIN appointments a ON i.appointment_id = a.id "
            "WHERE a.date >= ? AND a.date < ? AND i.status = 'unpaid'", (ms, me)).fetchone()[0]
        top_services = conn.execute(
            "SELECT a.service, COUNT(*) as cnt FROM appointments a WHERE a.date >= ? AND a.date < ? "
            "GROUP BY a.service ORDER BY cnt DESC LIMIT 5", (ms, me)).fetchall()
    settings = get_all_settings()
    shop_name = settings.get('shop_name', 'AMILCAR')
    profit = revenue - expenses_total
    services_html = ""
    for s in top_services:
        services_html += f"<tr><td>{s[0]}</td><td style='text-align:right'>{s[1]}</td></tr>"
    profit_color = '#2d6a4f' if profit >= 0 else '#C41E3A'
    html = f"""<!DOCTYPE html><html><head><meta charset="utf-8">
    <style>
    body {{ font-family: Helvetica, Arial, sans-serif; color: #222; padding: 30px; }}
    h1 {{ color: #C41E3A; text-align: center; font-size: 24px; letter-spacing: 3px; }}
    h2 {{ color: #D4AF37; font-size: 16px; letter-spacing: 2px; border-bottom: 1px solid #ddd; padding-bottom: 5px; }}
    .subtitle {{ text-align: center; color: #888; font-size: 12px; letter-spacing: 2px; margin-bottom: 30px; }}
    table {{ width: 100%; border-collapse: collapse; margin: 10px 0 20px; }}
    td, th {{ padding: 8px 12px; border-bottom: 1px solid #eee; font-size: 12px; }}
    th {{ background: #f5f5f5; text-align: left; color: #D4AF37; font-weight: bold; }}
    .stat-box {{ display: inline-block; width: 30%; text-align: center; padding: 15px; border: 1px solid #eee; border-radius: 8px; margin: 5px 1%; }}
    .stat-val {{ font-size: 22px; font-weight: bold; color: #D4AF37; }}
    .stat-lbl {{ font-size: 10px; color: #888; letter-spacing: 1px; margin-top: 5px; }}
    </style></head><body>
    <h1>{shop_name}</h1>
    <p class="subtitle">RAPPORT MENSUEL &mdash; {month_label.upper()}</p>
    <div style="text-align:center;margin-bottom:25px;">
        <div class="stat-box"><div class="stat-val">{revenue:.0f} DT</div><div class="stat-lbl">REVENUS</div></div>
        <div class="stat-box"><div class="stat-val" style="color:#C41E3A">{expenses_total:.0f} DT</div><div class="stat-lbl">D&Eacute;PENSES</div></div>
        <div class="stat-box"><div class="stat-val" style="color:{profit_color}">{profit:.0f} DT</div><div class="stat-lbl">B&Eacute;N&Eacute;FICE NET</div></div>
    </div>
    <table><tr><th>Indicateur</th><th style="text-align:right">Valeur</th></tr>
    <tr><td>Rendez-vous</td><td style="text-align:right">{appt_count}</td></tr>
    <tr><td>Termin&eacute;s</td><td style="text-align:right">{completed_count}</td></tr>
    <tr><td>Factures impay&eacute;es</td><td style="text-align:right">{unpaid_total:.0f} DT</td></tr>
    </table>
    <h2>TOP SERVICES</h2>
    <table><tr><th>Service</th><th style="text-align:right">Nombre</th></tr>{services_html}</table>
    <p style="text-align:center;color:#888;font-size:10px;margin-top:40px">G&eacute;n&eacute;r&eacute; automatiquement par {shop_name}</p>
    </body></html>"""
    try:
        pdf_buffer = io.BytesIO()
        pisa.CreatePDF(io.StringIO(html), dest=pdf_buffer)
        pdf_buffer.seek(0)
    except Exception as e:
        flash(f"Erreur de génération PDF : {str(e)}", "error")
        return redirect("/monthly")
    response = make_response(pdf_buffer.read())
    response.headers['Content-Type'] = 'application/pdf'
    response.headers['Content-Disposition'] = f'attachment; filename=rapport_mensuel_{month}.pdf'
    log_activity('Export PDF', f'Monthly report {month}')
    return response

# ─── Customer Ratings ───
@app.route("/rate_appointment/<int:appointment_id>", methods=["POST"])
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

@app.route("/api/ratings/<int:customer_id>")
@login_required
def api_customer_ratings(customer_id):
    with get_db() as conn:
        ratings = conn.execute(
            "SELECT r.rating, r.comment, r.created_at, a.service, a.date "
            "FROM ratings r JOIN appointments a ON r.appointment_id = a.id "
            "WHERE r.customer_id = ? ORDER BY r.created_at DESC", (customer_id,)).fetchall()
        avg_rating = conn.execute(
            "SELECT AVG(rating), COUNT(*) FROM ratings WHERE customer_id = ?",
            (customer_id,)).fetchone()
    return jsonify({
        'average': round(avg_rating[0], 1) if avg_rating[0] else 0,
        'count': avg_rating[1],
        'ratings': [{'stars': r[0], 'comment': r[1], 'date': r[2], 'service': r[3], 'appt_date': r[4]} for r in ratings]
    })

# ─── Service Packages ───
@app.route("/packages")
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

@app.route("/add_package", methods=["POST"])
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

@app.route("/delete_package/<int:pkg_id>", methods=["POST"])
@admin_required
def delete_package(pkg_id):
    with get_db() as conn:
        conn.execute("DELETE FROM service_packages WHERE id = ?", (pkg_id,))
        conn.commit()
    flash("Pack supprimé", "success")
    return redirect("/packages")

# ─── Service-Inventory Linking ───
@app.route("/service_inventory")
@admin_required
def service_inventory():
    with get_db() as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS service_inventory (
            id INTEGER PRIMARY KEY AUTOINCREMENT, service_name TEXT NOT NULL,
            inventory_id INTEGER NOT NULL, quantity_used REAL DEFAULT 1,
            FOREIGN KEY (inventory_id) REFERENCES inventory (id))""")
        links = conn.execute(
            "SELECT si.id, si.service_name, i.name, si.quantity_used "
            "FROM service_inventory si JOIN inventory i ON si.inventory_id = i.id "
            "ORDER BY si.service_name").fetchall()
        items = conn.execute("SELECT id, name FROM inventory ORDER BY name").fetchall()
    return render_template("service_inventory.html", links=links, services=get_services(), items=items)

@app.route("/add_service_inventory", methods=["POST"])
@admin_required
def add_service_inventory():
    service_name = request.form.get("service_name", "").strip()
    inventory_id = request.form.get("inventory_id", "")
    quantity_used = request.form.get("quantity_used", "1")
    if not service_name or not inventory_id:
        flash("Service et produit requis", "error")
        return redirect("/service_inventory")
    try:
        qty = float(quantity_used)
        if qty <= 0: qty = 1
    except ValueError:
        qty = 1
    with get_db() as conn:
        conn.execute("INSERT INTO service_inventory (service_name, inventory_id, quantity_used) VALUES (?,?,?)",
            (service_name, int(inventory_id), qty))
        conn.commit()
    flash("Liaison ajoutée", "success")
    return redirect("/service_inventory")

@app.route("/delete_service_inventory/<int:link_id>", methods=["POST"])
@admin_required
def delete_service_inventory(link_id):
    with get_db() as conn:
        conn.execute("DELETE FROM service_inventory WHERE id = ?", (link_id,))
        conn.commit()
    flash("Liaison supprimée", "success")
    return redirect("/service_inventory")

# ─── Auto Email Reminders ───
@app.route("/send_email_reminders", methods=["POST"])
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

# ─── Inventory Management ───
INVENTORY_CATEGORIES = [
    'Produits de lavage',
    'Polisseuses & Outils',
    'Céramique & Protection',
    'Chiffons & Éponges',
    'Consommables',
    'Autre',
]

@app.route("/inventory")
@login_required
def inventory_list():
    with get_db() as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS inventory (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL, category TEXT DEFAULT '',
            quantity INTEGER DEFAULT 0, min_quantity INTEGER DEFAULT 5,
            unit_price REAL DEFAULT 0, supplier TEXT DEFAULT '',
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""")
        items = conn.execute("SELECT * FROM inventory ORDER BY category, name").fetchall()
    low_stock = [i for i in items if i[3] <= i[4]]
    return render_template("inventory.html", items=items, low_stock=low_stock,
                           categories=INVENTORY_CATEGORIES)

@app.route("/add_inventory", methods=["POST"])
@login_required
def add_inventory():
    name = request.form.get("name", "").strip()
    category = request.form.get("category", "").strip()
    quantity = request.form.get("quantity", "0")
    min_quantity = request.form.get("min_quantity", "5")
    unit_price = request.form.get("unit_price", "0")
    supplier = request.form.get("supplier", "").strip()
    if not name:
        flash("Le nom du produit est requis", "error")
        return redirect("/inventory")
    try:
        qty = int(quantity)
        min_qty = int(min_quantity)
        price = float(unit_price)
    except ValueError:
        qty, min_qty, price = 0, 5, 0
    with get_db() as conn:
        conn.execute("INSERT INTO inventory (name, category, quantity, min_quantity, unit_price, supplier) VALUES (?,?,?,?,?,?)",
            (name, category, qty, min_qty, price, supplier))
        conn.commit()
    log_activity('Add Inventory', f'{name} (x{qty})')
    flash(f"Produit '{name}' ajouté au stock", "success")
    return redirect("/inventory")

@app.route("/update_inventory/<int:item_id>", methods=["POST"])
@login_required
def update_inventory(item_id):
    quantity = request.form.get("quantity", "0")
    min_quantity = request.form.get("min_quantity", "5")
    unit_price = request.form.get("unit_price", "0")
    supplier = request.form.get("supplier", "").strip()
    try:
        qty = int(quantity)
        min_qty = int(min_quantity)
        price = float(unit_price)
    except ValueError:
        flash("Valeurs invalides", "error")
        return redirect("/inventory")
    with get_db() as conn:
        conn.execute("UPDATE inventory SET quantity = ?, min_quantity = ?, unit_price = ?, supplier = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (qty, min_qty, price, supplier, item_id))
        conn.commit()
    log_activity('Update Inventory', f'Item #{item_id} → qty={qty}')
    flash("Stock mis à jour", "success")
    return redirect("/inventory")

@app.route("/delete_inventory/<int:item_id>", methods=["POST"])
@login_required
def delete_inventory(item_id):
    with get_db() as conn:
        conn.execute("DELETE FROM inventory WHERE id = ?", (item_id,))
        conn.commit()
    log_activity('Delete Inventory', f'Item #{item_id}')
    flash("Produit supprimé", "success")
    return redirect("/inventory")

# ─── WhatsApp Integration ───
@app.route("/whatsapp_reminder/<int:appointment_id>")
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

@app.route("/whatsapp_unpaid/<int:invoice_id>")
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

@app.route("/whatsapp_bulk_reminders", methods=["POST"])
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
@app.route("/communication_log/<int:customer_id>")
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

@app.route("/add_communication/<int:customer_id>", methods=["POST"])
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

# ─── Excel Import/Export ───
@app.route("/export/customers_excel")
@login_required
def export_customers_excel():
    import csv
    with get_db() as conn:
        customers = conn.execute(
            "SELECT c.id, c.name, c.phone, COALESCE(c.email,''), COALESCE(c.notes,''), "
            "(SELECT COUNT(*) FROM cars WHERE customer_id = c.id), "
            "(SELECT COALESCE(SUM(i.amount),0) FROM invoices i JOIN appointments a ON i.appointment_id = a.id "
            "JOIN cars ca ON a.car_id = ca.id WHERE ca.customer_id = c.id AND i.status = 'paid') "
            "FROM customers c ORDER BY c.name").fetchall()
    output = io.StringIO()
    writer = csv.writer(output, delimiter=';')
    writer.writerow(["ID", "Nom", "Téléphone", "Email", "Notes", "Nb Voitures", "Total Payé (DT)"])
    for c in customers:
        writer.writerow(c)
    response = make_response('\ufeff' + output.getvalue())
    response.headers['Content-Type'] = 'text/csv; charset=utf-8-sig'
    response.headers['Content-Disposition'] = 'attachment; filename=clients_amilcar.csv'
    return response

@app.route("/import/customers", methods=["POST"])
@admin_required
def import_customers():
    import csv
    file = request.files.get("file")
    if not file or not file.filename:
        flash("Sélectionnez un fichier", "error")
        return redirect("/customers")
    if not file.filename.lower().endswith(('.csv', '.txt')):
        flash("Format non supporté. Utilisez CSV", "error")
        return redirect("/customers")
    try:
        content = file.read().decode('utf-8-sig')
        reader = csv.reader(io.StringIO(content), delimiter=';')
        header = next(reader, None)
        if not header:
            flash("Fichier vide", "error")
            return redirect("/customers")
        imported = 0
        skipped = 0
        with get_db() as conn:
            for row in reader:
                if len(row) < 2:
                    skipped += 1
                    continue
                name = row[0].strip() if not row[0].strip().isdigit() else (row[1].strip() if len(row) > 1 else '')
                phone = row[1].strip() if not row[0].strip().isdigit() else (row[2].strip() if len(row) > 2 else '')
                # If first column is ID (number), shift
                if row[0].strip().isdigit() and len(row) >= 3:
                    name = row[1].strip()
                    phone = row[2].strip()
                if not name or not phone:
                    skipped += 1
                    continue
                existing = conn.execute("SELECT id FROM customers WHERE phone = ?", (phone,)).fetchone()
                if existing:
                    skipped += 1
                    continue
                email = ''
                notes = ''
                if len(row) > 3:
                    email = row[3].strip()
                if len(row) > 4:
                    notes = row[4].strip()
                conn.execute("INSERT INTO customers (name, phone, email, notes) VALUES (?,?,?,?)",
                    (name, phone, email, notes))
                imported += 1
            conn.commit()
        log_activity('Import', f'{imported} clients importés, {skipped} ignorés')
        flash(f"{imported} clients importés, {skipped} ignorés (doublons/invalides)", "success")
    except Exception as e:
        flash(f"Erreur d'import : {str(e)}", "error")
    return redirect("/customers")

@app.route("/export/full_report_excel")
@login_required
def export_full_report_excel():
    """Full business report as CSV (Excel-compatible)"""
    import csv
    from datetime import date
    today = date.today()
    month = request.args.get("month", f"{today.year}-{today.month:02d}")
    year, mon = map(int, month.split("-"))
    ms = f"{year}-{mon:02d}-01"
    me = f"{year+1}-01-01" if mon == 12 else f"{year}-{mon+1:02d}-01"
    with get_db() as conn:
        appointments = conn.execute(
            "SELECT a.id, cu.name, ca.brand || ' ' || ca.model, ca.plate, a.date, a.service, a.status, COALESCE(a.assigned_to,'') "
            "FROM appointments a JOIN cars ca ON a.car_id = ca.id JOIN customers cu ON ca.customer_id = cu.id "
            "WHERE a.date >= ? AND a.date < ? ORDER BY a.date", (ms, me)).fetchall()
        invoices = conn.execute(
            "SELECT i.id, cu.name, a.service, i.amount, i.status, COALESCE(i.payment_method,''), a.date "
            "FROM invoices i JOIN appointments a ON i.appointment_id = a.id "
            "JOIN cars ca ON a.car_id = ca.id JOIN customers cu ON ca.customer_id = cu.id "
            "WHERE a.date >= ? AND a.date < ? ORDER BY a.date", (ms, me)).fetchall()
        expenses = conn.execute(
            "SELECT id, date, category, description, amount FROM expenses WHERE date >= ? AND date < ? ORDER BY date", (ms, me)).fetchall()
        revenue = conn.execute(
            "SELECT COALESCE(SUM(i.amount),0) FROM invoices i JOIN appointments a ON i.appointment_id = a.id "
            "WHERE a.date >= ? AND a.date < ? AND i.status = 'paid'", (ms, me)).fetchone()[0]
        exp_total = conn.execute(
            "SELECT COALESCE(SUM(amount),0) FROM expenses WHERE date >= ? AND date < ?", (ms, me)).fetchone()[0]
    output = io.StringIO()
    writer = csv.writer(output, delimiter=';')
    month_names = ["Janvier","Février","Mars","Avril","Mai","Juin","Juillet","Août","Septembre","Octobre","Novembre","Décembre"]
    writer.writerow([f"RAPPORT COMPLET — {month_names[mon-1]} {year}"])
    writer.writerow([f"Revenus: {revenue:.0f} DT", f"Dépenses: {exp_total:.0f} DT", f"Bénéfice: {revenue-exp_total:.0f} DT"])
    writer.writerow([])
    writer.writerow(["=== RENDEZ-VOUS ==="])
    writer.writerow(["ID", "Client", "Voiture", "Plaque", "Date", "Service", "Statut", "Technicien"])
    for a in appointments:
        writer.writerow(a)
    writer.writerow([])
    writer.writerow(["=== FACTURES ==="])
    writer.writerow(["ID", "Client", "Service", "Montant (DT)", "Statut", "Paiement", "Date"])
    for inv in invoices:
        writer.writerow(inv)
    writer.writerow([])
    writer.writerow(["=== DÉPENSES ==="])
    writer.writerow(["ID", "Date", "Catégorie", "Description", "Montant (DT)"])
    for e in expenses:
        writer.writerow(e)
    response = make_response('\ufeff' + output.getvalue())
    response.headers['Content-Type'] = 'text/csv; charset=utf-8-sig'
    response.headers['Content-Disposition'] = f'attachment; filename=rapport_complet_{month}.csv'
    log_activity('Export', f'Full report {month}')
    return response

# ─── Enhanced Dashboard API ───
@app.route("/api/weekly_revenue")
@login_required
def weekly_revenue():
    from datetime import date, timedelta
    today = date.today()
    data = []
    day_names = ["Lun", "Mar", "Mer", "Jeu", "Ven", "Sam", "Dim"]
    start_of_week = today - timedelta(days=today.weekday())
    with get_db() as conn:
        for i in range(7):
            day = start_of_week + timedelta(days=i)
            ds = day.isoformat()
            rev = conn.execute(
                "SELECT COALESCE(SUM(i.amount),0) FROM invoices i JOIN appointments a ON i.appointment_id = a.id "
                "WHERE a.date = ? AND i.status = 'paid'", (ds,)).fetchone()[0]
            appts = conn.execute("SELECT COUNT(*) FROM appointments WHERE date = ?", (ds,)).fetchone()[0]
            data.append({'day': day_names[i], 'date': ds, 'revenue': float(rev), 'appointments': appts})
    return jsonify(data)

@app.route("/api/monthly_comparison")
@login_required
def monthly_comparison():
    from datetime import date
    today = date.today()
    results = []
    with get_db() as conn:
        for i in range(11, -1, -1):
            m = today.month - i
            y = today.year
            while m <= 0:
                m += 12
                y -= 1
            ms = f"{y}-{m:02d}-01"
            me = f"{y+1}-01-01" if m == 12 else f"{y}-{m+1:02d}-01"
            rev = conn.execute(
                "SELECT COALESCE(SUM(i.amount),0) FROM invoices i JOIN appointments a ON i.appointment_id = a.id "
                "WHERE a.date >= ? AND a.date < ? AND i.status = 'paid'", (ms, me)).fetchone()[0]
            exp = conn.execute(
                "SELECT COALESCE(SUM(amount),0) FROM expenses WHERE date >= ? AND date < ?", (ms, me)).fetchone()[0]
            month_names = ["J","F","M","A","M","J","J","A","S","O","N","D"]
            results.append({
                'label': f"{month_names[m-1]}", 'month': f"{y}-{m:02d}",
                'revenue': float(rev), 'expenses': float(exp), 'profit': float(rev - exp)
            })
    return jsonify(results)

@app.route("/api/profit_forecast")
@login_required
def profit_forecast():
    from datetime import date
    today = date.today()
    with get_db() as conn:
        # Average daily revenue last 30 days
        from datetime import timedelta
        d30 = (today - timedelta(days=30)).isoformat()
        avg_rev = conn.execute(
            "SELECT COALESCE(SUM(i.amount),0)/30.0 FROM invoices i JOIN appointments a ON i.appointment_id = a.id "
            "WHERE a.date >= ? AND i.status = 'paid'", (d30,)).fetchone()[0]
        avg_exp = conn.execute(
            "SELECT COALESCE(SUM(amount),0)/30.0 FROM expenses WHERE date >= ?", (d30,)).fetchone()[0]
        # Days remaining in month
        if today.month == 12:
            last_day = date(today.year + 1, 1, 1) - timedelta(days=1)
        else:
            last_day = date(today.year, today.month + 1, 1) - timedelta(days=1)
        days_remaining = (last_day - today).days
        # Current month actual
        ms = f"{today.year}-{today.month:02d}-01"
        me = f"{today.year+1}-01-01" if today.month == 12 else f"{today.year}-{today.month+1:02d}-01"
        curr_rev = conn.execute(
            "SELECT COALESCE(SUM(i.amount),0) FROM invoices i JOIN appointments a ON i.appointment_id = a.id "
            "WHERE a.date >= ? AND a.date < ? AND i.status = 'paid'", (ms, me)).fetchone()[0]
        curr_exp = conn.execute(
            "SELECT COALESCE(SUM(amount),0) FROM expenses WHERE date >= ? AND date < ?", (ms, me)).fetchone()[0]
    forecast_rev = float(curr_rev) + float(avg_rev) * days_remaining
    forecast_exp = float(curr_exp) + float(avg_exp) * days_remaining
    return jsonify({
        'current_revenue': float(curr_rev),
        'current_expenses': float(curr_exp),
        'forecast_revenue': round(forecast_rev),
        'forecast_expenses': round(forecast_exp),
        'forecast_profit': round(forecast_rev - forecast_exp),
        'avg_daily_revenue': round(float(avg_rev), 1),
        'days_remaining': days_remaining
    })

# ─── Professional PDF Report ───
@app.route("/export/professional_pdf")
@login_required
def export_professional_pdf():
    from xhtml2pdf import pisa
    from datetime import date
    month = request.args.get("month", "")
    if not month:
        today = date.today()
        month = f"{today.year}-{today.month:02d}"
    year, mon = map(int, month.split("-"))
    ms = f"{year}-{mon:02d}-01"
    me = f"{year+1}-01-01" if mon == 12 else f"{year}-{mon+1:02d}-01"
    month_names_fr = ["Janvier","Février","Mars","Avril","Mai","Juin","Juillet","Août","Septembre","Octobre","Novembre","Décembre"]
    month_label = f"{month_names_fr[mon-1]} {year}"
    settings = get_all_settings()
    shop_name = settings.get('shop_name', 'AMILCAR')
    shop_address = settings.get('shop_address', 'Mahres, Sfax')
    shop_phone = settings.get('shop_phone', '')
    with get_db() as conn:
        revenue = conn.execute(
            "SELECT COALESCE(SUM(i.amount),0) FROM invoices i JOIN appointments a ON i.appointment_id = a.id "
            "WHERE a.date >= ? AND a.date < ? AND i.status = 'paid'", (ms, me)).fetchone()[0]
        expenses_total = conn.execute(
            "SELECT COALESCE(SUM(amount),0) FROM expenses WHERE date >= ? AND date < ?", (ms, me)).fetchone()[0]
        appt_count = conn.execute("SELECT COUNT(*) FROM appointments WHERE date >= ? AND date < ?", (ms, me)).fetchone()[0]
        completed_count = conn.execute("SELECT COUNT(*) FROM appointments WHERE date >= ? AND date < ? AND status = 'completed'", (ms, me)).fetchone()[0]
        cancelled_count = conn.execute("SELECT COUNT(*) FROM appointments WHERE date >= ? AND date < ? AND status = 'cancelled'", (ms, me)).fetchone()[0]
        unpaid_total = conn.execute(
            "SELECT COALESCE(SUM(i.amount),0) FROM invoices i JOIN appointments a ON i.appointment_id = a.id "
            "WHERE a.date >= ? AND a.date < ? AND i.status = 'unpaid'", (ms, me)).fetchone()[0]
        new_customers = conn.execute(
            "SELECT COUNT(DISTINCT ca.customer_id) FROM appointments a JOIN cars ca ON a.car_id = ca.id "
            "WHERE a.date >= ? AND a.date < ? AND ca.customer_id NOT IN "
            "(SELECT DISTINCT ca2.customer_id FROM appointments a2 JOIN cars ca2 ON a2.car_id = ca2.id WHERE a2.date < ?)",
            (ms, me, ms)).fetchone()[0]
        top_services = conn.execute(
            "SELECT a.service, COUNT(*) as cnt, COALESCE(SUM(i.amount),0) FROM appointments a "
            "LEFT JOIN invoices i ON i.appointment_id = a.id AND i.status = 'paid' "
            "WHERE a.date >= ? AND a.date < ? GROUP BY a.service ORDER BY cnt DESC LIMIT 8", (ms, me)).fetchall()
        top_customers = conn.execute(
            "SELECT cu.name, COALESCE(SUM(i.amount),0) as total, COUNT(DISTINCT a.id) "
            "FROM customers cu JOIN cars ca ON ca.customer_id = cu.id "
            "JOIN appointments a ON a.car_id = ca.id "
            "LEFT JOIN invoices i ON i.appointment_id = a.id AND i.status = 'paid' "
            "WHERE a.date >= ? AND a.date < ? GROUP BY cu.id ORDER BY total DESC LIMIT 5", (ms, me)).fetchall()
        exp_by_cat = conn.execute(
            "SELECT category, COALESCE(SUM(amount),0) FROM expenses "
            "WHERE date >= ? AND date < ? GROUP BY category ORDER BY SUM(amount) DESC", (ms, me)).fetchall()
        # Daily revenue for mini chart
        daily_rev = conn.execute(
            "SELECT a.date, COALESCE(SUM(i.amount),0) FROM invoices i JOIN appointments a ON i.appointment_id = a.id "
            "WHERE a.date >= ? AND a.date < ? AND i.status = 'paid' GROUP BY a.date ORDER BY a.date", (ms, me)).fetchall()
    profit = revenue - expenses_total
    completion_rate = round(completed_count / appt_count * 100) if appt_count > 0 else 0
    services_rows = "".join(f"<tr><td>{s[0]}</td><td style='text-align:center'>{s[1]}</td><td style='text-align:right'>{s[2]:.0f} DT</td></tr>" for s in top_services)
    customers_rows = "".join(f"<tr><td>{c[0]}</td><td style='text-align:center'>{c[2]}</td><td style='text-align:right'>{c[1]:.0f} DT</td></tr>" for c in top_customers)
    expenses_rows = "".join(f"<tr><td>{e[0]}</td><td style='text-align:right'>{e[1]:.0f} DT</td></tr>" for e in exp_by_cat)
    profit_color = '#2d6a4f' if profit >= 0 else '#C41E3A'
    html = f"""<!DOCTYPE html><html><head><meta charset="utf-8">
    <style>
    @page {{ size: A4; margin: 20mm; }}
    body {{ font-family: Helvetica, Arial, sans-serif; color: #222; font-size: 11px; line-height: 1.4; }}
    .header {{ text-align: center; border-bottom: 3px solid #C41E3A; padding-bottom: 15px; margin-bottom: 20px; }}
    .header h1 {{ color: #C41E3A; font-size: 28px; letter-spacing: 5px; margin: 0; }}
    .header p {{ color: #888; font-size: 11px; letter-spacing: 2px; margin: 3px 0; }}
    .header .month {{ color: #D4AF37; font-size: 16px; font-weight: bold; letter-spacing: 3px; margin-top: 10px; }}
    .stats-grid {{ width: 100%; margin: 15px 0; }}
    .stats-grid td {{ width: 33.33%; text-align: center; padding: 12px 8px; border: 1px solid #eee; }}
    .stat-val {{ font-size: 20px; font-weight: bold; color: #D4AF37; }}
    .stat-lbl {{ font-size: 9px; color: #888; letter-spacing: 1.5px; margin-top: 3px; }}
    h2 {{ color: #C41E3A; font-size: 13px; letter-spacing: 2px; border-bottom: 1px solid #ddd; padding-bottom: 4px; margin: 20px 0 8px; }}
    table.data {{ width: 100%; border-collapse: collapse; margin: 5px 0 15px; }}
    table.data th {{ background: #f8f6f0; color: #D4AF37; font-size: 10px; font-weight: bold; letter-spacing: 1px; padding: 6px 10px; text-align: left; border-bottom: 2px solid #D4AF37; }}
    table.data td {{ padding: 5px 10px; border-bottom: 1px solid #f0f0f0; font-size: 10px; }}
    table.data tr:nth-child(even) {{ background: #fafafa; }}
    .footer {{ text-align: center; color: #aaa; font-size: 9px; margin-top: 30px; border-top: 1px solid #eee; padding-top: 10px; }}
    .two-col {{ width: 100%; }}
    .two-col > tbody > tr > td {{ width: 50%; vertical-align: top; padding: 0 8px; }}
    </style></head><body>
    <div class="header">
        <h1>{shop_name}</h1>
        <p>{shop_address} {('| ' + shop_phone) if shop_phone else ''}</p>
        <div class="month">RAPPORT MENSUEL &mdash; {month_label.upper()}</div>
    </div>
    <table class="stats-grid"><tr>
        <td><div class="stat-val">{revenue:.0f} DT</div><div class="stat-lbl">REVENUS</div></td>
        <td><div class="stat-val" style="color:#C41E3A">{expenses_total:.0f} DT</div><div class="stat-lbl">D&Eacute;PENSES</div></td>
        <td><div class="stat-val" style="color:{profit_color}">{profit:.0f} DT</div><div class="stat-lbl">B&Eacute;N&Eacute;FICE NET</div></td>
    </tr><tr>
        <td><div class="stat-val" style="font-size:16px">{appt_count}</div><div class="stat-lbl">RENDEZ-VOUS</div></td>
        <td><div class="stat-val" style="font-size:16px">{completion_rate}%</div><div class="stat-lbl">TAUX COMPL&Eacute;TION</div></td>
        <td><div class="stat-val" style="font-size:16px">{new_customers}</div><div class="stat-lbl">NOUVEAUX CLIENTS</div></td>
    </tr></table>
    <table class="two-col"><tbody><tr><td>
        <h2>TOP SERVICES</h2>
        <table class="data"><tr><th>Service</th><th style="text-align:center">Nb</th><th style="text-align:right">Revenus</th></tr>{services_rows}</table>
    </td><td>
        <h2>TOP CLIENTS</h2>
        <table class="data"><tr><th>Client</th><th style="text-align:center">Visites</th><th style="text-align:right">Total</th></tr>{customers_rows}</table>
    </td></tr></tbody></table>
    <h2>D&Eacute;PENSES PAR CAT&Eacute;GORIE</h2>
    <table class="data"><tr><th>Cat&eacute;gorie</th><th style="text-align:right">Montant</th></tr>{expenses_rows}</table>
    <table class="stats-grid" style="margin-top:10px"><tr>
        <td><div class="stat-val" style="font-size:14px;color:#C41E3A">{unpaid_total:.0f} DT</div><div class="stat-lbl">IMPAY&Eacute;S</div></td>
        <td><div class="stat-val" style="font-size:14px">{completed_count}</div><div class="stat-lbl">TERMIN&Eacute;S</div></td>
        <td><div class="stat-val" style="font-size:14px;color:#888">{cancelled_count}</div><div class="stat-lbl">ANNUL&Eacute;S</div></td>
    </tr></table>
    <div class="footer">G&eacute;n&eacute;r&eacute; automatiquement par {shop_name} le {date.today().isoformat()} &mdash; Rapport confidentiel</div>
    </body></html>"""
    try:
        pdf_buffer = io.BytesIO()
        pisa.CreatePDF(io.StringIO(html), dest=pdf_buffer)
        pdf_buffer.seek(0)
    except Exception as e:
        flash(f"Erreur de génération PDF : {str(e)}", "error")
        return redirect("/reports")
    response = make_response(pdf_buffer.read())
    response.headers['Content-Type'] = 'application/pdf'
    response.headers['Content-Disposition'] = f'attachment; filename=rapport_professionnel_{month}.pdf'
    log_activity('Export PDF', f'Professional report {month}')
    return response

# ─── Customer Portal ───
@app.route("/portal/<token>")
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

@app.route("/generate_portal_link/<int:customer_id>", methods=["POST"])
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

# ─── Automatic Daily Backup ───
@app.route("/auto_backup_settings", methods=["POST"])
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

@app.route("/run_backup", methods=["POST"])
@admin_required
def run_manual_backup():
    result = _perform_backup()
    if result:
        flash(f"Sauvegarde créée : {result}", "success")
    else:
        flash("Erreur lors de la sauvegarde", "error")
    return redirect("/settings")

@app.route("/list_backups")
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

@app.route("/download_backup/<filename>")
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

def _perform_backup():
    """Perform a database backup and cleanup old backups"""
    import shutil
    from datetime import datetime
    db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'database', 'amilcar.db')
    backup_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'backups')
    os.makedirs(backup_dir, exist_ok=True)
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    backup_name = f'amilcar_backup_{timestamp}.db'
    backup_path = os.path.join(backup_dir, backup_name)
    try:
        shutil.copy2(db_path, backup_path)
    except Exception:
        return None
    # Cleanup old backups
    try:
        keep_days = int(get_setting('backup_keep_days', '7'))
    except ValueError:
        keep_days = 7
    cutoff = datetime.now().timestamp() - (keep_days * 86400)
    for f in os.listdir(backup_dir):
        fp = os.path.join(backup_dir, f)
        if f.endswith('.db') and os.path.getmtime(fp) < cutoff:
            os.remove(fp)
    return backup_name

# Auto-backup on startup
def _auto_backup_check():
    from datetime import datetime
    if get_setting('auto_backup', '0') != '1':
        return
    backup_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'backups')
    if os.path.exists(backup_dir):
        today = datetime.now().strftime('%Y%m%d')
        existing = [f for f in os.listdir(backup_dir) if today in f]
        if existing:
            return
    _perform_backup()

try:
    _auto_backup_check()
except Exception:
    pass

# ─── Feature 1: Live Workshop Board ───
@app.route("/live_board")
@login_required
def live_board():
    return render_template("live_board.html")

@app.route("/api/live_board")
@login_required
def api_live_board():
    from datetime import date
    today = str(date.today())
    with get_db() as conn:
        appointments = conn.execute(
            "SELECT a.id, cu.name, ca.brand || ' ' || ca.model, ca.plate, a.service, a.status, "
            "COALESCE(a.time,''), COALESCE(a.assigned_to,''), COALESCE(a.estimated_duration, 60) "
            "FROM appointments a JOIN cars ca ON a.car_id = ca.id "
            "JOIN customers cu ON ca.customer_id = cu.id "
            "WHERE a.date = ? ORDER BY a.time, a.id", (today,)).fetchall()
    columns = {'pending': [], 'in_progress': [], 'completed': []}
    for a in appointments:
        item = {'id': a[0], 'customer': a[1], 'car': a[2], 'plate': a[3],
                'service': a[4], 'status': a[5], 'time': a[6], 'tech': a[7], 'duration': a[8]}
        if a[5] in columns:
            columns[a[5]].append(item)
        elif a[5] == 'cancelled':
            pass
        else:
            columns['pending'].append(item)
    return jsonify(columns)

@app.route("/api/update_board_status", methods=["POST"])
@login_required
def update_board_status():
    data = request.get_json()
    if not data or 'id' not in data or 'status' not in data:
        return jsonify({'error': 'Données manquantes'}), 400
    new_status = data['status']
    if new_status not in ('pending', 'in_progress', 'completed'):
        return jsonify({'error': 'Statut invalide'}), 400
    appt_id = data['id']
    with get_db() as conn:
        conn.execute("UPDATE appointments SET status = ? WHERE id = ?", (new_status, appt_id))
        # Auto-deduct inventory when completed
        if new_status == 'completed':
            appt = conn.execute("SELECT service FROM appointments WHERE id = ?", (appt_id,)).fetchone()
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
    log_activity('Board Update', f'Appointment #{appt_id} → {new_status}')
    return jsonify({'success': True})

# ─── Feature 2: QR Code for Invoices ───
@app.route("/api/invoice_qr/<int:invoice_id>")
@login_required
def invoice_qr(invoice_id):
    """Generate QR code as SVG for an invoice"""
    with get_db() as conn:
        inv = conn.execute("SELECT qr_token FROM invoices WHERE id = ?", (invoice_id,)).fetchone()
        if not inv:
            return "Not found", 404
        token = inv[0]
        if not token:
            token = uuid.uuid4().hex
            conn.execute("UPDATE invoices SET qr_token = ? WHERE id = ?", (token, invoice_id))
            conn.commit()
    # Generate QR as simple SVG using manual encoding
    url = f"{request.host_url}invoice_view/{token}"
    # Use a simple QR code generation via HTML/JS approach
    return jsonify({'url': url, 'token': token})

@app.route("/invoice_view/<token>")
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

@app.route("/generate_invoice_qr/<int:invoice_id>", methods=["POST"])
@login_required
def generate_invoice_qr(invoice_id):
    token = uuid.uuid4().hex
    with get_db() as conn:
        conn.execute("UPDATE invoices SET qr_token = ? WHERE id = ?", (token, invoice_id))
        conn.commit()
    url = f"{request.host_url}invoice_view/{token}"
    flash(f"QR Code généré. Lien : {url}", "success")
    return redirect("/invoices")

# ─── Feature 3: Smart Scheduling ───
@app.route("/api/available_slots")
@login_required
def available_slots():
    date_val = request.args.get('date', '')
    if not date_val:
        return jsonify([])
    max_daily = int(get_setting('max_daily_appointments', '10'))
    with get_db() as conn:
        booked = conn.execute(
            "SELECT COALESCE(time,''), COUNT(*) FROM appointments WHERE date = ? AND status != 'cancelled' GROUP BY time",
            (date_val,)).fetchall()
        total_booked = conn.execute(
            "SELECT COUNT(*) FROM appointments WHERE date = ? AND status != 'cancelled'",
            (date_val,)).fetchone()[0]
    booked_times = {b[0] for b in booked if b[0]}
    slots = []
    all_times = ['08:00', '08:30', '09:00', '09:30', '10:00', '10:30', '11:00', '11:30',
                 '12:00', '13:00', '13:30', '14:00', '14:30', '15:00', '15:30', '16:00', '16:30', '17:00']
    for t in all_times:
        slots.append({'time': t, 'available': t not in booked_times})
    return jsonify({
        'slots': slots,
        'total_booked': total_booked,
        'max_daily': max_daily,
        'full': total_booked >= max_daily
    })

# ─── Feature 4: Auto-rating after Service ───
@app.route("/rate/<token>")
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

@app.route("/rate/<token>", methods=["POST"])
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

@app.route("/send_rating_link/<int:appointment_id>")
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

# ─── Feature 5: Appointment Heatmap ───
@app.route("/heatmap")
@login_required
def appointment_heatmap():
    return render_template("heatmap.html")

@app.route("/api/heatmap_data")
@login_required
def api_heatmap_data():
    with get_db() as conn:
        # Day of week analysis
        day_data = conn.execute(
            "SELECT CASE CAST(strftime('%w', date) AS INTEGER) "
            "WHEN 0 THEN 'Dim' WHEN 1 THEN 'Lun' WHEN 2 THEN 'Mar' WHEN 3 THEN 'Mer' "
            "WHEN 4 THEN 'Jeu' WHEN 5 THEN 'Ven' WHEN 6 THEN 'Sam' END as day_name, "
            "COUNT(*) FROM appointments WHERE status != 'cancelled' GROUP BY strftime('%w', date) "
            "ORDER BY CAST(strftime('%w', date) AS INTEGER)").fetchall()
        # Hour analysis
        hour_data = conn.execute(
            "SELECT COALESCE(time, ''), COUNT(*) FROM appointments "
            "WHERE time != '' AND status != 'cancelled' GROUP BY time ORDER BY time").fetchall()
        # Day x Hour matrix
        matrix = conn.execute(
            "SELECT strftime('%w', date) as dow, time, COUNT(*) "
            "FROM appointments WHERE time != '' AND status != 'cancelled' "
            "GROUP BY dow, time ORDER BY dow, time").fetchall()
        # Monthly trend
        monthly = conn.execute(
            "SELECT strftime('%Y-%m', date) as month, COUNT(*) "
            "FROM appointments WHERE status != 'cancelled' "
            "GROUP BY month ORDER BY month DESC LIMIT 12").fetchall()
        # Peak analysis
        busiest_day = conn.execute(
            "SELECT date, COUNT(*) as cnt FROM appointments WHERE status != 'cancelled' "
            "GROUP BY date ORDER BY cnt DESC LIMIT 5").fetchall()
    day_names = ['Dim', 'Lun', 'Mar', 'Mer', 'Jeu', 'Ven', 'Sam']
    matrix_data = []
    for m in matrix:
        dow = int(m[0])
        hour = m[1][:2] if m[1] else '00'
        matrix_data.append({'day': dow, 'day_name': day_names[dow], 'hour': hour, 'time': m[1], 'count': m[2]})
    return jsonify({
        'by_day': [{'day': d[0], 'count': d[1]} for d in day_data],
        'by_hour': [{'time': h[0], 'count': h[1]} for h in hour_data],
        'matrix': matrix_data,
        'monthly': [{'month': m[0], 'count': m[1]} for m in reversed(monthly)],
        'busiest_days': [{'date': b[0], 'count': b[1]} for b in busiest_day]
    })

# ─── Feature 6: Employee Time Tracking ───
@app.route("/time_tracking")
@login_required
def time_tracking():
    from datetime import date
    today = str(date.today())
    with get_db() as conn:
        users = conn.execute("SELECT id, username, COALESCE(full_name, '') FROM users ORDER BY id").fetchall()
        today_logs = conn.execute(
            "SELECT t.id, t.username, t.action, t.timestamp, t.date "
            "FROM time_tracking t WHERE t.date = ? ORDER BY t.timestamp DESC", (today,)).fetchall()
        # Current status for each user
        user_status = {}
        for u in users:
            last = conn.execute(
                "SELECT action, timestamp FROM time_tracking WHERE user_id = ? AND date = ? ORDER BY timestamp DESC LIMIT 1",
                (u[0], today)).fetchone()
            if last:
                user_status[u[0]] = {'action': last[0], 'time': last[1]}
            else:
                user_status[u[0]] = {'action': 'out', 'time': None}
    return render_template("time_tracking.html", users=users, today_logs=today_logs,
                           user_status=user_status, today=today)

@app.route("/clock_in_out", methods=["POST"])
@login_required
def clock_in_out():
    from datetime import date, datetime
    user_id = request.form.get("user_id", session.get('user_id'))
    action = request.form.get("action", "clock_in")
    if action not in ('clock_in', 'clock_out', 'break_start', 'break_end'):
        action = 'clock_in'
    today = str(date.today())
    with get_db() as conn:
        username = conn.execute("SELECT username FROM users WHERE id = ?", (user_id,)).fetchone()
        if username:
            conn.execute("INSERT INTO time_tracking (user_id, username, action, date) VALUES (?,?,?,?)",
                (user_id, username[0], action, today))
            conn.commit()
    action_labels = {'clock_in': 'Entrée', 'clock_out': 'Sortie', 'break_start': 'Pause début', 'break_end': 'Pause fin'}
    flash(f"{action_labels.get(action, action)} enregistré(e)", "success")
    return redirect("/time_tracking")

@app.route("/api/time_report")
@login_required
def api_time_report():
    from datetime import date, timedelta, datetime
    period = request.args.get('period', 'week')
    today = date.today()
    if period == 'week':
        start = (today - timedelta(days=today.weekday())).isoformat()
    elif period == 'month':
        start = f"{today.year}-{today.month:02d}-01"
    else:
        start = (today - timedelta(days=30)).isoformat()
    with get_db() as conn:
        users = conn.execute("SELECT id, username, COALESCE(full_name, '') FROM users ORDER BY id").fetchall()
        results = []
        for u in users:
            logs = conn.execute(
                "SELECT action, timestamp FROM time_tracking WHERE user_id = ? AND date >= ? ORDER BY timestamp",
                (u[0], start)).fetchall()
            total_hours = 0
            clock_in_time = None
            for log in logs:
                if log[0] == 'clock_in':
                    try:
                        clock_in_time = datetime.fromisoformat(log[1])
                    except (ValueError, TypeError):
                        clock_in_time = None
                elif log[0] == 'clock_out' and clock_in_time:
                    try:
                        clock_out_time = datetime.fromisoformat(log[1])
                        total_hours += (clock_out_time - clock_in_time).total_seconds() / 3600
                        clock_in_time = None
                    except (ValueError, TypeError):
                        pass
            results.append({
                'username': u[1], 'full_name': u[2],
                'total_hours': round(total_hours, 1),
                'log_count': len(logs)
            })
    return jsonify(results)

# ─── Feature 7: Service Profitability Analysis ───
@app.route("/profitability")
@login_required
def service_profitability():
    with get_db() as conn:
        # Get services with revenue and material costs
        services = conn.execute(
            "SELECT a.service, COUNT(*) as cnt, COALESCE(SUM(i.amount),0) as revenue "
            "FROM appointments a LEFT JOIN invoices i ON i.appointment_id = a.id AND i.status = 'paid' "
            "WHERE a.status = 'completed' GROUP BY a.service ORDER BY revenue DESC").fetchall()
        # Material cost per service from service_inventory
        cost_data = {}
        links = conn.execute(
            "SELECT si.service_name, SUM(si.quantity_used * inv.unit_price) as cost "
            "FROM service_inventory si JOIN inventory inv ON si.inventory_id = inv.id "
            "GROUP BY si.service_name").fetchall()
        for l in links:
            cost_data[l[0]] = l[1]
    results = []
    for s in services:
        service_name = s[0].split(' - ')[0].strip()
        material_cost = cost_data.get(service_name, 0) * s[1]
        profit = s[2] - material_cost
        margin = round(profit / s[2] * 100) if s[2] > 0 else 0
        results.append({
            'service': s[0], 'count': s[1], 'revenue': s[2],
            'material_cost': round(material_cost, 1),
            'profit': round(profit, 1), 'margin': margin
        })
    return render_template("profitability.html", services=results)

# ─── Feature 8: Advanced Inventory Monitoring ───
@app.route("/api/inventory_trends")
@login_required
def inventory_trends():
    with get_db() as conn:
        items = conn.execute("SELECT id, name, quantity, min_quantity, unit_price, category FROM inventory ORDER BY name").fetchall()
        # Consumption rate from service_inventory usage
        consumption = {}
        from datetime import date, timedelta
        d30 = (date.today() - timedelta(days=30)).isoformat()
        for item in items:
            used = conn.execute(
                "SELECT COALESCE(SUM(si.quantity_used),0) "
                "FROM service_inventory si JOIN appointments a ON si.service_name = a.service "
                "JOIN inventory inv ON si.inventory_id = inv.id "
                "WHERE inv.id = ? AND a.status = 'completed' AND a.date >= ?",
                (item[0], d30)).fetchone()[0]
            consumption[item[0]] = used
    results = []
    for item in items:
        usage_30d = consumption.get(item[0], 0)
        days_until_empty = round(item[2] / (usage_30d / 30)) if usage_30d > 0 else 999
        reorder_needed = item[2] <= item[3]
        results.append({
            'id': item[0], 'name': item[1], 'quantity': item[2], 'min_quantity': item[3],
            'unit_price': item[4], 'category': item[5],
            'usage_30d': round(usage_30d, 1), 'days_until_empty': min(days_until_empty, 999),
            'reorder': reorder_needed,
            'stock_value': round(item[2] * item[4], 1)
        })
    return jsonify(results)

@app.route("/inventory_dashboard")
@login_required
def inventory_dashboard():
    return render_template("inventory_dashboard.html")

# ─── Feature 9: PWA Support ───
@app.route("/manifest.json")
def pwa_manifest():
    settings = get_all_settings()
    shop_name = settings.get('shop_name', 'AMILCAR')
    manifest = {
        "name": f"{shop_name} Auto Care",
        "short_name": shop_name,
        "start_url": "/",
        "display": "standalone",
        "background_color": "#0a0a0a",
        "theme_color": "#D4AF37",
        "orientation": "portrait-primary",
        "icons": [
            {"src": "/static/logo.png", "sizes": "192x192", "type": "image/png"},
            {"src": "/static/logo.png", "sizes": "512x512", "type": "image/png"}
        ]
    }
    response = make_response(jsonify(manifest))
    response.headers['Content-Type'] = 'application/manifest+json'
    return response

@app.route("/sw.js")
def service_worker():
    sw_content = """
const CACHE_NAME = 'amilcar-v5';
const urlsToCache = ['/', '/static/style.css', '/static/logo.png'];
self.addEventListener('install', e => {
    e.waitUntil(caches.open(CACHE_NAME).then(c => c.addAll(urlsToCache)));
});
self.addEventListener('fetch', e => {
    e.respondWith(
        caches.match(e.request).then(r => r || fetch(e.request))
    );
});
self.addEventListener('activate', e => {
    e.waitUntil(
        caches.keys().then(keys => Promise.all(
            keys.filter(k => k !== CACHE_NAME).map(k => caches.delete(k))
        ))
    );
});
"""
    response = make_response(sw_content)
    response.headers['Content-Type'] = 'application/javascript'
    response.headers['Service-Worker-Allowed'] = '/'
    return response

# ─── Feature 10: Advanced Points & Rewards System ───
POINTS_PER_DINAR = 1  # 1 point per DT spent
TIER_THRESHOLDS = {'BRONZE': 0, 'ARGENT': 500, 'OR': 1000, 'PLATINE': 2000}

@app.route("/rewards")
@login_required
def rewards_page():
    with get_db() as conn:
        rewards = conn.execute(
            "SELECT rp.id, cu.id, cu.name, cu.phone, rp.points, rp.total_earned, rp.total_spent, rp.tier "
            "FROM reward_points rp JOIN customers cu ON rp.customer_id = cu.id "
            "ORDER BY rp.points DESC").fetchall()
        customers = conn.execute("SELECT id, name, phone FROM customers ORDER BY name").fetchall()
    return render_template("rewards.html", rewards=rewards, customers=customers,
                           tiers=TIER_THRESHOLDS)

@app.route("/rewards/add_points", methods=["POST"])
@login_required
def add_reward_points():
    customer_id = request.form.get("customer_id", "")
    points = request.form.get("points", "0")
    description = request.form.get("description", "").strip()
    if not customer_id:
        flash("Sélectionnez un client", "error")
        return redirect("/rewards")
    try:
        pts = int(points)
        if pts <= 0:
            raise ValueError
    except ValueError:
        flash("Nombre de points invalide", "error")
        return redirect("/rewards")
    with get_db() as conn:
        existing = conn.execute("SELECT id, points, total_earned FROM reward_points WHERE customer_id = ?",
            (customer_id,)).fetchone()
        if existing:
            new_points = existing[1] + pts
            new_total = existing[2] + pts
            tier = _calculate_tier(new_total)
            conn.execute("UPDATE reward_points SET points = ?, total_earned = ?, tier = ? WHERE id = ?",
                (new_points, new_total, tier, existing[0]))
        else:
            tier = _calculate_tier(pts)
            conn.execute("INSERT INTO reward_points (customer_id, points, total_earned, tier) VALUES (?,?,?,?)",
                (customer_id, pts, pts, tier))
        conn.execute("INSERT INTO reward_history (customer_id, points, type, description) VALUES (?,?,?,?)",
            (customer_id, pts, 'earn', description or f'+{pts} points'))
        conn.commit()
    flash(f"{pts} points ajoutés", "success")
    return redirect("/rewards")

@app.route("/rewards/redeem", methods=["POST"])
@login_required
def redeem_reward_points():
    customer_id = request.form.get("customer_id", "")
    points = request.form.get("points", "0")
    reward_desc = request.form.get("reward", "").strip()
    if not customer_id:
        flash("Client requis", "error")
        return redirect("/rewards")
    try:
        pts = int(points)
        if pts <= 0:
            raise ValueError
    except ValueError:
        flash("Nombre de points invalide", "error")
        return redirect("/rewards")
    with get_db() as conn:
        existing = conn.execute("SELECT id, points, total_spent FROM reward_points WHERE customer_id = ?",
            (customer_id,)).fetchone()
        if not existing or existing[1] < pts:
            flash("Points insuffisants", "error")
            return redirect("/rewards")
        conn.execute("UPDATE reward_points SET points = points - ?, total_spent = total_spent + ? WHERE id = ?",
            (pts, pts, existing[0]))
        conn.execute("INSERT INTO reward_history (customer_id, points, type, description) VALUES (?,?,?,?)",
            (customer_id, -pts, 'redeem', reward_desc or f'Échange {pts} points'))
        conn.commit()
    flash(f"{pts} points échangés", "success")
    return redirect("/rewards")

@app.route("/api/reward_history/<int:customer_id>")
@login_required
def api_reward_history(customer_id):
    with get_db() as conn:
        history = conn.execute(
            "SELECT points, type, description, created_at FROM reward_history "
            "WHERE customer_id = ? ORDER BY created_at DESC LIMIT 50",
            (customer_id,)).fetchall()
        info = conn.execute(
            "SELECT points, total_earned, total_spent, tier FROM reward_points WHERE customer_id = ?",
            (customer_id,)).fetchone()
    return jsonify({
        'info': {'points': info[0], 'earned': info[1], 'spent': info[2], 'tier': info[3]} if info else None,
        'history': [{'points': h[0], 'type': h[1], 'desc': h[2], 'date': h[3]} for h in history]
    })

def _calculate_tier(total_earned):
    if total_earned >= TIER_THRESHOLDS['PLATINE']:
        return 'PLATINE'
    elif total_earned >= TIER_THRESHOLDS['OR']:
        return 'OR'
    elif total_earned >= TIER_THRESHOLDS['ARGENT']:
        return 'ARGENT'
    return 'BRONZE'

# Auto-add points when invoice is paid
@app.after_request
def auto_reward_points(response):
    return response

# ─── Phase 6 Feature 1: Advanced PDF Reports ───
@app.route("/advanced_report")
@login_required
def advanced_report():
    from datetime import date, timedelta
    period = request.args.get('period', 'month')
    today = date.today()
    if period == 'year':
        start = f"{today.year}-01-01"
        title = f"Rapport Annuel {today.year}"
    else:
        start = f"{today.year}-{today.month:02d}-01"
        title = f"Rapport Mensuel {today.strftime('%B %Y')}"
    end = today.isoformat()
    with get_db() as conn:
        revenue = conn.execute("SELECT COALESCE(SUM(amount),0) FROM invoices WHERE status='paid' AND date_created BETWEEN ? AND ?", (start, end)).fetchone()[0]
        expenses = conn.execute("SELECT COALESCE(SUM(amount),0) FROM expenses WHERE date BETWEEN ? AND ?", (start, end)).fetchone()[0]
        appt_count = conn.execute("SELECT COUNT(*) FROM appointments WHERE date BETWEEN ? AND ?", (start, end)).fetchone()[0]
        completed = conn.execute("SELECT COUNT(*) FROM appointments WHERE date BETWEEN ? AND ? AND status='completed'", (start, end)).fetchone()[0]
        new_customers = conn.execute("SELECT COUNT(*) FROM customers WHERE id IN (SELECT DISTINCT ca.customer_id FROM cars ca JOIN appointments a ON a.car_id=ca.id WHERE a.date BETWEEN ? AND ?)", (start, end)).fetchone()[0]
        top_services = conn.execute("SELECT service, COUNT(*) as cnt, COALESCE(SUM(i.amount),0) FROM appointments a LEFT JOIN invoices i ON i.appointment_id=a.id AND i.status='paid' WHERE a.date BETWEEN ? AND ? GROUP BY a.service ORDER BY cnt DESC LIMIT 10", (start, end)).fetchall()
        top_customers = conn.execute("SELECT cu.name, COUNT(*) as cnt, COALESCE(SUM(i.amount),0) FROM appointments a JOIN cars ca ON a.car_id=ca.id JOIN customers cu ON ca.customer_id=cu.id LEFT JOIN invoices i ON i.appointment_id=a.id AND i.status='paid' WHERE a.date BETWEEN ? AND ? GROUP BY cu.id ORDER BY cnt DESC LIMIT 10", (start, end)).fetchall()
        monthly_rev = conn.execute("SELECT strftime('%Y-%m', date_created) as m, SUM(amount) FROM invoices WHERE status='paid' AND date_created >= date(?, '-12 months') GROUP BY m ORDER BY m", (end,)).fetchall()
    data = {
        'title': title, 'period': period, 'start': start, 'end': end,
        'revenue': revenue, 'expenses': expenses, 'profit': revenue - expenses,
        'appt_count': appt_count, 'completed': completed, 'new_customers': new_customers,
        'completion_rate': round(completed/appt_count*100) if appt_count else 0,
        'top_services': top_services, 'top_customers': top_customers, 'monthly_rev': monthly_rev
    }
    fmt = request.args.get('format', 'html')
    if fmt == 'pdf':
        from xhtml2pdf import pisa
        html = render_template("advanced_report.html", data=data, pdf_mode=True)
        result = io.BytesIO()
        pisa.CreatePDF(io.BytesIO(html.encode('utf-8')), dest=result)
        result.seek(0)
        response = make_response(result.read())
        response.headers['Content-Type'] = 'application/pdf'
        response.headers['Content-Disposition'] = f'attachment; filename=rapport_{period}_{end}.pdf'
        return response
    return render_template("advanced_report.html", data=data, pdf_mode=False)

# ─── Phase 6 Feature 2: Bulk WhatsApp Messaging ───
@app.route("/bulk_message")
@login_required
def bulk_message():
    with get_db() as conn:
        customers = conn.execute("SELECT id, name, phone FROM customers ORDER BY name").fetchall()
        tiers = conn.execute("SELECT DISTINCT tier FROM reward_points").fetchall()
    return render_template("bulk_message.html", customers=customers, tiers=[t[0] for t in tiers])

@app.route("/bulk_message/send", methods=["POST"])
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

# ─── Phase 6 Feature 3: Mobile Dashboard ───
@app.route("/mobile")
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
            "SELECT COALESCE(SUM(amount),0) FROM invoices WHERE status='paid' AND date_created = ?", (today,)).fetchone()[0]
        unpaid = conn.execute(
            "SELECT COUNT(*) FROM invoices WHERE status IN ('unpaid','partial')").fetchone()[0]
    return render_template("mobile_dashboard.html",
        today_appts=today_appts, pending=pending, in_progress=in_progress,
        completed_today=completed_today, today_revenue=today_revenue, unpaid=unpaid)

# ─── Phase 6 Feature 4: Promo Coupons System ───
@app.route("/coupons")
@login_required
def coupons_page():
    with get_db() as conn:
        coupons = conn.execute("SELECT * FROM coupons ORDER BY created_at DESC").fetchall()
    return render_template("coupons.html", coupons=coupons)

@app.route("/coupons/add", methods=["POST"])
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

@app.route("/coupons/toggle/<int:coupon_id>", methods=["POST"])
@login_required
def toggle_coupon(coupon_id):
    with get_db() as conn:
        conn.execute("UPDATE coupons SET active = CASE WHEN active = 1 THEN 0 ELSE 1 END WHERE id = ?", (coupon_id,))
        conn.commit()
    flash("Statut modifié", "success")
    return redirect("/coupons")

@app.route("/coupons/delete/<int:coupon_id>", methods=["POST"])
@login_required
def delete_coupon(coupon_id):
    with get_db() as conn:
        conn.execute("DELETE FROM coupons WHERE id = ?", (coupon_id,))
        conn.commit()
    flash("Coupon supprimé", "success")
    return redirect("/coupons")

@app.route("/api/validate_coupon")
@login_required
def validate_coupon():
    from datetime import date
    code = request.args.get('code', '').strip().upper()
    amount = float(request.args.get('amount', 0))
    with get_db() as conn:
        coupon = conn.execute("SELECT id, discount_type, discount_value, max_uses, used_count, expires_at, active, min_amount FROM coupons WHERE code = ?", (code,)).fetchone()
    if not coupon:
        return jsonify({'valid': False, 'error': 'Code invalide'})
    if not coupon[6]:
        return jsonify({'valid': False, 'error': 'Coupon désactivé'})
    if coupon[4] >= coupon[3]:
        return jsonify({'valid': False, 'error': 'Coupon épuisé'})
    if coupon[5] and coupon[5] < date.today().isoformat():
        return jsonify({'valid': False, 'error': 'Coupon expiré'})
    if amount < coupon[7]:
        return jsonify({'valid': False, 'error': f'Montant minimum: {coupon[7]} DT'})
    if coupon[1] == 'percent':
        discount = round(amount * coupon[2] / 100, 2)
    else:
        discount = min(coupon[2], amount)
    return jsonify({'valid': True, 'discount': discount, 'type': coupon[1], 'value': coupon[2]})

# ─── Phase 6 Feature 5: Maintenance Mileage Reminders ───
@app.route("/mileage_tracking")
@login_required
def mileage_tracking():
    with get_db() as conn:
        cars = conn.execute(
            "SELECT ca.id, cu.name, ca.brand, ca.model, ca.plate, "
            "COALESCE(ca.mileage,0), COALESCE(ca.last_oil_change,''), COALESCE(ca.next_service_date,'') "
            "FROM cars ca JOIN customers cu ON ca.customer_id = cu.id ORDER BY cu.name").fetchall()
    return render_template("mileage_tracking.html", cars=cars)

@app.route("/mileage_tracking/update/<int:car_id>", methods=["POST"])
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

@app.route("/api/mileage_alerts")
@login_required
def mileage_alerts():
    from datetime import date, timedelta
    soon = (date.today() + timedelta(days=14)).isoformat()
    today = date.today().isoformat()
    with get_db() as conn:
        due = conn.execute(
            "SELECT ca.id, cu.name, cu.phone, ca.brand, ca.model, ca.plate, ca.next_service_date, ca.mileage "
            "FROM cars ca JOIN customers cu ON ca.customer_id = cu.id "
            "WHERE ca.next_service_date != '' AND ca.next_service_date <= ? ORDER BY ca.next_service_date",
            (soon,)).fetchall()
    alerts = []
    for d in due:
        overdue = d[6] < today
        alerts.append({'car_id': d[0], 'customer': d[1], 'phone': d[2], 'car': f"{d[3]} {d[4]}",
                       'plate': d[5], 'due_date': d[6], 'mileage': d[7], 'overdue': overdue})
    return jsonify(alerts)

# ─── Phase 6 Feature 6: Supplier Management ───
@app.route("/suppliers")
@login_required
def suppliers_page():
    with get_db() as conn:
        suppliers = conn.execute("SELECT * FROM suppliers ORDER BY name").fetchall()
    return render_template("suppliers.html", suppliers=suppliers)

@app.route("/suppliers/add", methods=["POST"])
@login_required
def add_supplier():
    name = request.form.get("name", "").strip()
    phone = request.form.get("phone", "").strip()
    email = request.form.get("email", "").strip()
    address = request.form.get("address", "").strip()
    notes = request.form.get("notes", "").strip()
    if not name:
        flash("Nom requis", "error")
        return redirect("/suppliers")
    with get_db() as conn:
        conn.execute("INSERT INTO suppliers (name, phone, email, address, notes) VALUES (?,?,?,?,?)",
            (name, phone, email, address, notes))
        conn.commit()
    log_activity('Supplier Added', name)
    flash(f"Fournisseur {name} ajouté", "success")
    return redirect("/suppliers")

@app.route("/suppliers/delete/<int:sid>", methods=["POST"])
@login_required
def delete_supplier(sid):
    with get_db() as conn:
        conn.execute("DELETE FROM suppliers WHERE id = ?", (sid,))
        conn.commit()
    flash("Fournisseur supprimé", "success")
    return redirect("/suppliers")

@app.route("/purchase_orders")
@login_required
def purchase_orders():
    with get_db() as conn:
        orders = conn.execute(
            "SELECT po.id, s.name, po.order_date, po.status, po.total_amount, po.notes "
            "FROM purchase_orders po JOIN suppliers s ON po.supplier_id = s.id "
            "ORDER BY po.order_date DESC").fetchall()
        suppliers = conn.execute("SELECT id, name FROM suppliers ORDER BY name").fetchall()
        inventory = conn.execute("SELECT id, name, unit_price FROM inventory ORDER BY name").fetchall()
    return render_template("purchase_orders.html", orders=orders, suppliers=suppliers, inventory=inventory)

@app.route("/purchase_orders/add", methods=["POST"])
@login_required
def add_purchase_order():
    supplier_id = request.form.get("supplier_id")
    order_date = request.form.get("order_date", "")
    notes = request.form.get("notes", "").strip()
    items_json = request.form.get("items", "[]")
    import json
    try:
        items = json.loads(items_json)
    except (json.JSONDecodeError, TypeError):
        items = []
    if not supplier_id or not items:
        flash("Fournisseur et articles requis", "error")
        return redirect("/purchase_orders")
    total = sum(float(i.get('quantity', 0)) * float(i.get('unit_price', 0)) for i in items)
    with get_db() as conn:
        cursor = conn.execute("INSERT INTO purchase_orders (supplier_id, order_date, total_amount, notes) VALUES (?,?,?,?)",
            (supplier_id, order_date, total, notes))
        order_id = cursor.lastrowid
        for item in items:
            inv_id = item.get('inventory_id') or None
            conn.execute("INSERT INTO purchase_items (order_id, inventory_id, item_name, quantity, unit_price) VALUES (?,?,?,?,?)",
                (order_id, inv_id, item.get('name', ''), float(item.get('quantity', 0)), float(item.get('unit_price', 0))))
        conn.commit()
    log_activity('Purchase Order', f'Order #{order_id} total: {total} DT')
    flash(f"Commande #{order_id} créée ({total} DT)", "success")
    return redirect("/purchase_orders")

@app.route("/purchase_orders/receive/<int:order_id>", methods=["POST"])
@login_required
def receive_purchase_order(order_id):
    with get_db() as conn:
        items = conn.execute("SELECT inventory_id, quantity FROM purchase_items WHERE order_id = ? AND inventory_id IS NOT NULL", (order_id,)).fetchall()
        for item in items:
            conn.execute("UPDATE inventory SET quantity = quantity + ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (item[1], item[0]))
        conn.execute("UPDATE purchase_orders SET status = 'received' WHERE id = ?", (order_id,))
        conn.commit()
    log_activity('Order Received', f'Order #{order_id} stock updated')
    flash(f"Commande #{order_id} reçue — stock mis à jour", "success")
    return redirect("/purchase_orders")

# ─── Phase 6 Feature 7: Customer Analytics ───
@app.route("/customer_analytics/<int:customer_id>")
@login_required
def customer_analytics(customer_id):
    with get_db() as conn:
        customer = conn.execute("SELECT * FROM customers WHERE id = ?", (customer_id,)).fetchone()
        if not customer:
            flash("Client introuvable", "error")
            return redirect("/customers")
        cars = conn.execute("SELECT * FROM cars WHERE customer_id = ?", (customer_id,)).fetchall()
        car_ids = [c[0] for c in cars]
        if car_ids:
            placeholders = ','.join(['?' for _ in car_ids])
            appointments = conn.execute(
                f"SELECT a.id, a.date, a.service, a.status, ca.brand, ca.model, ca.plate "
                f"FROM appointments a JOIN cars ca ON a.car_id=ca.id WHERE ca.customer_id=? ORDER BY a.date DESC", (customer_id,)).fetchall()
            total_spent = conn.execute(
                f"SELECT COALESCE(SUM(i.amount),0) FROM invoices i JOIN appointments a ON i.appointment_id=a.id "
                f"JOIN cars ca ON a.car_id=ca.id WHERE ca.customer_id=? AND i.status='paid'", (customer_id,)).fetchone()[0]
            visit_count = conn.execute(
                f"SELECT COUNT(*) FROM appointments a JOIN cars ca ON a.car_id=ca.id WHERE ca.customer_id=?", (customer_id,)).fetchone()[0]
            services_used = conn.execute(
                f"SELECT a.service, COUNT(*) as cnt FROM appointments a JOIN cars ca ON a.car_id=ca.id "
                f"WHERE ca.customer_id=? GROUP BY a.service ORDER BY cnt DESC", (customer_id,)).fetchall()
            monthly_spending = conn.execute(
                f"SELECT strftime('%Y-%m', a.date) as m, COALESCE(SUM(i.amount),0) "
                f"FROM appointments a JOIN cars ca ON a.car_id=ca.id LEFT JOIN invoices i ON i.appointment_id=a.id AND i.status='paid' "
                f"WHERE ca.customer_id=? GROUP BY m ORDER BY m DESC LIMIT 12", (customer_id,)).fetchall()
            first_visit = conn.execute(
                f"SELECT MIN(a.date) FROM appointments a JOIN cars ca ON a.car_id=ca.id WHERE ca.customer_id=?", (customer_id,)).fetchone()[0]
            last_visit = conn.execute(
                f"SELECT MAX(a.date) FROM appointments a JOIN cars ca ON a.car_id=ca.id WHERE ca.customer_id=?", (customer_id,)).fetchone()[0]
        else:
            appointments, total_spent, visit_count, services_used, monthly_spending = [], 0, 0, [], []
            first_visit, last_visit = None, None
        # Rewards info
        rewards = conn.execute("SELECT points, total_earned, tier FROM reward_points WHERE customer_id=?", (customer_id,)).fetchone()
        # Ratings
        avg_rating = conn.execute(
            "SELECT AVG(r.rating) FROM ratings r WHERE r.customer_id=?", (customer_id,)).fetchone()[0]
    # Predict next visit
    from datetime import date, timedelta
    predicted_next = None
    if visit_count >= 2 and last_visit and first_visit:
        try:
            d_first = date.fromisoformat(first_visit)
            d_last = date.fromisoformat(last_visit)
            avg_gap = (d_last - d_first).days / max(visit_count - 1, 1)
            predicted_next = (d_last + timedelta(days=int(avg_gap))).isoformat()
        except (ValueError, TypeError):
            pass
    return render_template("customer_analytics.html",
        customer=customer, cars=cars, appointments=appointments[:20],
        total_spent=total_spent, visit_count=visit_count,
        services_used=services_used, monthly_spending=list(reversed(monthly_spending)),
        first_visit=first_visit, last_visit=last_visit,
        predicted_next=predicted_next, rewards=rewards,
        avg_rating=round(avg_rating, 1) if avg_rating else None)

# ─── Phase 6 Feature 8: Advanced Role Permissions ───
PERMISSIONS = {
    'admin': ['all'],
    'manager': ['customers', 'appointments', 'invoices', 'reports', 'inventory', 'services', 'expenses'],
    'receptionist': ['customers', 'appointments', 'invoices', 'calendar'],
    'technician': ['appointments', 'live_board', 'time_tracking', 'gallery'],
}

def has_permission(permission):
    role = session.get('role', 'employee')
    if role == 'admin':
        return True
    allowed = PERMISSIONS.get(role, [])
    return permission in allowed or 'all' in allowed

@app.route("/manage_roles")
@login_required
def manage_roles():
    if session.get('role') != 'admin':
        flash("Accès refusé", "error")
        return redirect("/")
    with get_db() as conn:
        users = conn.execute("SELECT id, username, role, COALESCE(full_name,'') FROM users ORDER BY id").fetchall()
    return render_template("manage_roles.html", users=users, roles=PERMISSIONS)

@app.route("/manage_roles/update/<int:user_id>", methods=["POST"])
@login_required
def update_user_role(user_id):
    if session.get('role') != 'admin':
        flash("Accès refusé", "error")
        return redirect("/")
    new_role = request.form.get("role", "employee")
    if new_role not in PERMISSIONS and new_role != 'employee':
        new_role = 'employee'
    with get_db() as conn:
        conn.execute("UPDATE users SET role = ? WHERE id = ?", (new_role, user_id))
        conn.commit()
    log_activity('Role Updated', f'User #{user_id} → {new_role}')
    flash(f"Rôle mis à jour: {new_role}", "success")
    return redirect("/manage_roles")

# ─── Phase 6 Feature 9: Smart Waiting Queue ───
@app.route("/queue")
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

@app.route("/queue/add", methods=["POST"])
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

@app.route("/queue/update/<int:queue_id>", methods=["POST"])
@login_required
def update_queue_status(queue_id):
    new_status = request.form.get("status", "waiting")
    if new_status not in ('waiting', 'serving', 'done', 'cancelled'):
        new_status = 'waiting'
    with get_db() as conn:
        conn.execute("UPDATE waiting_queue SET status = ? WHERE id = ?", (new_status, queue_id))
        conn.commit()
    return redirect("/queue")

@app.route("/queue/remove/<int:queue_id>", methods=["POST"])
@login_required
def remove_from_queue(queue_id):
    with get_db() as conn:
        conn.execute("DELETE FROM waiting_queue WHERE id = ?", (queue_id,))
        conn.commit()
    flash("Retiré de la file", "success")
    return redirect("/queue")

@app.route("/api/queue_status")
@login_required
def api_queue_status():
    with get_db() as conn:
        queue = conn.execute(
            "SELECT wq.id, cu.name, wq.service, wq.priority, wq.status, wq.estimated_wait, wq.created_at "
            "FROM waiting_queue wq JOIN customers cu ON wq.customer_id=cu.id "
            "WHERE wq.status IN ('waiting','serving') ORDER BY wq.priority DESC, wq.created_at").fetchall()
    position = 0
    total_wait = 0
    items = []
    for q in queue:
        if q[4] == 'waiting':
            position += 1
            total_wait += q[5]
        items.append({
            'id': q[0], 'customer': q[1], 'service': q[2], 'priority': q[3],
            'status': q[4], 'wait': q[5], 'since': q[6], 'position': position
        })
    return jsonify({'queue': items, 'total_waiting': position, 'est_total_wait': total_wait})

# ─── Phase 6 Feature 10: Customer Portal App ───
@app.route("/client")
def customer_login_page():
    return render_template("customer_login.html")

@app.route("/client/login", methods=["POST"])
def customer_login():
    phone = request.form.get("phone", "").strip()
    if not phone:
        flash("Numéro de téléphone requis", "error")
        return redirect("/client")
    with get_db() as conn:
        customer = conn.execute("SELECT id, name, phone FROM customers WHERE phone = ?", (phone,)).fetchone()
    if not customer:
        flash("Numéro non trouvé. Contactez-nous pour créer votre compte.", "error")
        return redirect("/client")
    session['client_id'] = customer[0]
    session['client_name'] = customer[1]
    session['client_phone'] = customer[2]
    return redirect("/client/dashboard")

@app.route("/client/dashboard")
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

@app.route("/client/request_appointment", methods=["POST"])
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

@app.route("/client/logout")
def customer_logout():
    session.pop('client_id', None)
    session.pop('client_name', None)
    session.pop('client_phone', None)
    return redirect("/client")

# ─── Phase 7 Feature 1: CEO Dashboard ───
@app.route("/ceo_dashboard")
@login_required
def ceo_dashboard():
    with get_db() as conn:
        from datetime import date, timedelta
        today = date.today()
        month_start = today.replace(day=1).isoformat()
        last_month_start = (today.replace(day=1) - timedelta(days=1)).replace(day=1).isoformat()
        last_month_end = (today.replace(day=1) - timedelta(days=1)).isoformat()
        year_start = today.replace(month=1, day=1).isoformat()

        # Revenue this month
        rev_month = conn.execute("SELECT COALESCE(SUM(amount),0) FROM invoices WHERE date >= ? AND status='Payée'", (month_start,)).fetchone()[0]
        # Revenue last month
        rev_last = conn.execute("SELECT COALESCE(SUM(amount),0) FROM invoices WHERE date >= ? AND date <= ? AND status='Payée'", (last_month_start, last_month_end)).fetchone()[0]
        # Revenue this year
        rev_year = conn.execute("SELECT COALESCE(SUM(amount),0) FROM invoices WHERE date >= ? AND status='Payée'", (year_start,)).fetchone()[0]
        # Expenses this month
        exp_month = conn.execute("SELECT COALESCE(SUM(amount),0) FROM expenses WHERE date >= ?", (month_start,)).fetchone()[0]
        # Expenses last month
        exp_last = conn.execute("SELECT COALESCE(SUM(amount),0) FROM expenses WHERE date >= ? AND date <= ?", (last_month_start, last_month_end)).fetchone()[0]
        # Net profit
        profit_month = rev_month - exp_month
        profit_last = rev_last - exp_last
        # Clients total & new this month
        total_clients = conn.execute("SELECT COUNT(*) FROM customers").fetchone()[0]
        new_clients = conn.execute("SELECT COUNT(*) FROM customers WHERE created_at >= ?", (month_start,)).fetchone()[0]
        # Appointments this month
        appts_month = conn.execute("SELECT COUNT(*) FROM appointments WHERE date >= ?", (month_start,)).fetchone()[0]
        # Average rating
        avg_rating = conn.execute("SELECT COALESCE(AVG(rating),0) FROM ratings").fetchone()[0]
        # Unpaid invoices
        unpaid_total = conn.execute("SELECT COALESCE(SUM(amount),0) FROM invoices WHERE status IN ('unpaid','Non payée','partial')").fetchone()[0]
        unpaid_count = conn.execute("SELECT COUNT(*) FROM invoices WHERE status IN ('unpaid','Non payée','partial')").fetchone()[0]
        # Monthly revenue trend (last 12 months)
        monthly_data = []
        for i in range(11, -1, -1):
            m = today.replace(day=1) - timedelta(days=i*30)
            ms = m.replace(day=1).isoformat()
            if m.month == 12:
                me = m.replace(year=m.year+1, month=1, day=1).isoformat()
            else:
                me = m.replace(month=m.month+1, day=1).isoformat()
            r = conn.execute("SELECT COALESCE(SUM(amount),0) FROM invoices WHERE date >= ? AND date < ? AND status='Payée'", (ms, me)).fetchone()[0]
            e = conn.execute("SELECT COALESCE(SUM(amount),0) FROM expenses WHERE date >= ? AND date < ?", (ms, me)).fetchone()[0]
            monthly_data.append({'month': ms[:7], 'revenue': r, 'expenses': e, 'profit': r - e})
        # Top services
        top_services = conn.execute("SELECT service, COUNT(*) as cnt FROM appointments WHERE date >= ? GROUP BY service ORDER BY cnt DESC LIMIT 5", (year_start,)).fetchall()
        # Top customers by spending
        top_customers = conn.execute("""
            SELECT c.name, COALESCE(SUM(i.amount),0) as total FROM invoices i
            JOIN appointments a ON i.appointment_id = a.id
            JOIN cars cr ON a.car_id = cr.id
            JOIN customers c ON cr.customer_id = c.id
            WHERE i.status='Payée' GROUP BY c.id ORDER BY total DESC LIMIT 5
        """).fetchall()

    return render_template("ceo_dashboard.html",
        rev_month=rev_month, rev_last=rev_last, rev_year=rev_year,
        exp_month=exp_month, exp_last=exp_last,
        profit_month=profit_month, profit_last=profit_last,
        total_clients=total_clients, new_clients=new_clients,
        appts_month=appts_month, avg_rating=round(avg_rating, 1),
        unpaid_total=unpaid_total, unpaid_count=unpaid_count,
        monthly_data=monthly_data, top_services=top_services,
        top_customers=top_customers, now=today.isoformat())

# ─── Phase 7 Feature 2: Email Notifications ───
@app.route("/email_settings", methods=["GET", "POST"])
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

@app.route("/send_email/<int:customer_id>", methods=["POST"])
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

# ─── Phase 7 Feature 3: Advanced Quotes ───
@app.route("/quotes_advanced")
@login_required
def quotes_advanced():
    with get_db() as conn:
        quotes = conn.execute("""
            SELECT q.*, CASE WHEN q.converted_invoice_id > 0 THEN 'Convertie'
            WHEN q.status='accepted' THEN 'Accepté'
            WHEN q.status='rejected' THEN 'Refusé'
            WHEN q.status='expired' THEN 'Expiré'
            ELSE 'En attente' END as display_status
            FROM quotes q ORDER BY q.created_at DESC
        """).fetchall()
    return render_template("quotes_advanced.html", quotes=quotes)

@app.route("/quote_to_invoice/<int:quote_id>", methods=["POST"])
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

@app.route("/quote_status/<int:quote_id>/<status>", methods=["POST"])
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

# ─── Phase 7 Feature 4: SMS Notifications ───
@app.route("/sms_settings", methods=["GET", "POST"])
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

@app.route("/send_sms/<int:customer_id>", methods=["POST"])
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

@app.route("/sms_reminder_batch", methods=["POST"])
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

# ─── Phase 7 Feature 5: Maintenance Plans ───
@app.route("/maintenance_plans")
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

@app.route("/maintenance_plans/add", methods=["POST"])
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
        except:
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

@app.route("/maintenance_plans/done/<int:plan_id>", methods=["POST"])
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
                except:
                    pass
            next_km = current_km + plan[3] if plan[3] > 0 else 0
            conn.execute("UPDATE maintenance_plans SET last_done_date=?, last_done_km=?, next_due_date=?, next_due_km=? WHERE id=?",
                        (today_str, current_km, next_date, next_km, plan_id))
            conn.commit()
    flash("Maintenance marquée comme effectuée !", "success")
    return redirect("/maintenance_plans")

@app.route("/maintenance_plans/delete/<int:plan_id>", methods=["POST"])
@login_required
def delete_maintenance_plan(plan_id):
    with get_db() as conn:
        conn.execute("UPDATE maintenance_plans SET active=0 WHERE id=?", (plan_id,))
        conn.commit()
    flash("Plan supprimé", "success")
    return redirect("/maintenance_plans")

# ─── Phase 7 Feature 6: Payment Tracking ───
@app.route("/payments/<int:invoice_id>")
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

@app.route("/payments/<int:invoice_id>/add", methods=["POST"])
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
        except:
            pass
        conn.commit()
    flash(f"Paiement de {amount:.2f} DH enregistré", "success")
    return redirect(f"/payments/{invoice_id}")

# ─── Phase 7 Feature 7: Customer Satisfaction Survey ───
@app.route("/surveys")
@login_required
def surveys_list():
    with get_db() as conn:
        surveys = conn.execute("""
            SELECT s.*, c.name, a.service, a.date
            FROM surveys s
            JOIN customers c ON s.customer_id = c.id
            JOIN appointments a ON s.appointment_id = a.id
            ORDER BY s.created_at DESC
        """).fetchall()
        # Stats
        submitted = [s for s in surveys if s[11]]
        avg_quality = sum(s[4] for s in submitted) / len(submitted) if submitted else 0
        avg_speed = sum(s[5] for s in submitted) / len(submitted) if submitted else 0
        avg_reception = sum(s[6] for s in submitted) / len(submitted) if submitted else 0
        avg_cleanliness = sum(s[7] for s in submitted) / len(submitted) if submitted else 0
        avg_value = sum(s[8] for s in submitted) / len(submitted) if submitted else 0
        avg_overall = (avg_quality + avg_speed + avg_reception + avg_cleanliness + avg_value) / 5 if submitted else 0
    return render_template("surveys.html", surveys=surveys,
        avg_quality=round(avg_quality,1), avg_speed=round(avg_speed,1),
        avg_reception=round(avg_reception,1), avg_cleanliness=round(avg_cleanliness,1),
        avg_value=round(avg_value,1), avg_overall=round(avg_overall,1),
        total_submitted=len(submitted), total_pending=len(surveys)-len(submitted))

@app.route("/survey/create/<int:appointment_id>", methods=["POST"])
@login_required
def create_survey(appointment_id):
    with get_db() as conn:
        appt = conn.execute("SELECT a.*, cr.customer_id FROM appointments a JOIN cars cr ON a.car_id=cr.id WHERE a.id=?", (appointment_id,)).fetchone()
        if not appt:
            flash("Rendez-vous introuvable", "danger")
            return redirect("/appointments")
        existing = conn.execute("SELECT id FROM surveys WHERE appointment_id=?", (appointment_id,)).fetchone()
        if existing:
            flash("Un questionnaire existe déjà pour ce RDV", "warning")
            return redirect("/surveys")
        token = uuid.uuid4().hex[:12]
        customer_id = appt[-1]
        conn.execute("INSERT INTO surveys (appointment_id, customer_id, token) VALUES (?,?,?)",
                    (appointment_id, customer_id, token))
        conn.commit()
    flash(f"Questionnaire créé ! Lien: /survey/{token}", "success")
    return redirect("/surveys")

@app.route("/survey/<token>", methods=["GET", "POST"])
def fill_survey(token):
    with get_db() as conn:
        survey = conn.execute("SELECT s.*, c.name, a.service FROM surveys s JOIN customers c ON s.customer_id=c.id JOIN appointments a ON s.appointment_id=a.id WHERE s.token=?", (token,)).fetchone()
        if not survey:
            return "Questionnaire introuvable", 404
        if survey[11]:  # already submitted
            return render_template("survey_thanks.html", survey=survey)
        if request.method == "POST":
            from datetime import datetime
            conn.execute("""UPDATE surveys SET
                q_quality=?, q_speed=?, q_reception=?, q_cleanliness=?, q_value=?,
                comment=?, submitted=1, submitted_at=? WHERE token=?""",
                (int(request.form.get('q_quality', 3)),
                 int(request.form.get('q_speed', 3)),
                 int(request.form.get('q_reception', 3)),
                 int(request.form.get('q_cleanliness', 3)),
                 int(request.form.get('q_value', 3)),
                 request.form.get('comment', ''),
                 datetime.now().isoformat(), token))
            conn.commit()
            return render_template("survey_thanks.html", survey=survey)
    return render_template("survey_form.html", survey=survey)

# ─── Phase 7 Feature 8: Photo Archive ───
@app.route("/car_photos/<int:car_id>")
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

@app.route("/car_photos/<int:car_id>/upload", methods=["POST"])
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

@app.route("/car_photos/delete/<int:photo_id>", methods=["POST"])
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

# ─── Phase 7 Feature 9: Online Booking ───
@app.route("/book", methods=["GET", "POST"])
def online_booking():
    if request.method == "POST":
        name = request.form.get('name', '').strip()
        phone = request.form.get('phone', '').strip()
        email = request.form.get('email', '')
        car_brand = request.form.get('car_brand', '')
        car_model = request.form.get('car_model', '')
        car_plate = request.form.get('car_plate', '')
        service = request.form.get('service', '')
        preferred_date = request.form.get('preferred_date', '')
        preferred_time = request.form.get('preferred_time', '')
        notes = request.form.get('notes', '')
        if not name or not phone or not service or not preferred_date:
            flash("Veuillez remplir tous les champs obligatoires", "danger")
            return redirect("/book")
        with get_db() as conn:
            conn.execute("""INSERT INTO online_bookings
                (name, phone, email, car_brand, car_model, car_plate, service, preferred_date, preferred_time, notes)
                VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (name, phone, email, car_brand, car_model, car_plate, service, preferred_date, preferred_time, notes))
            conn.commit()
        return render_template("booking_success.html", name=name, date=preferred_date, time=preferred_time)
    with get_db() as conn:
        services = conn.execute("SELECT name FROM services WHERE active=1 ORDER BY name").fetchall()
    return render_template("online_booking.html", services=services)

@app.route("/bookings_admin")
@login_required
def bookings_admin():
    with get_db() as conn:
        bookings = conn.execute("SELECT * FROM online_bookings ORDER BY created_at DESC").fetchall()
    return render_template("bookings_admin.html", bookings=bookings)

@app.route("/booking_confirm/<int:booking_id>", methods=["POST"])
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

@app.route("/booking_reject/<int:booking_id>", methods=["POST"])
@login_required
def booking_reject(booking_id):
    with get_db() as conn:
        conn.execute("UPDATE online_bookings SET status='rejected' WHERE id=?", (booking_id,))
        conn.commit()
    flash("Réservation refusée", "info")
    return redirect("/bookings_admin")

# ─── Phase 7 Feature 10: Multi-Language Support ───
TRANSLATIONS = {
    'fr': {
        'dashboard': 'Tableau de Bord', 'customers': 'Clients', 'appointments': 'Rendez-vous',
        'invoices': 'Factures', 'calendar': 'Calendrier', 'expenses': 'Dépenses',
        'today': "Aujourd'hui", 'monthly': 'Mensuel', 'reports': 'Rapports',
        'settings': 'Paramètres', 'search': 'Rechercher...', 'logout': 'Déconnexion',
        'add': 'Ajouter', 'edit': 'Modifier', 'delete': 'Supprimer', 'save': 'Enregistrer',
        'cancel': 'Annuler', 'name': 'Nom', 'phone': 'Téléphone', 'date': 'Date',
        'status': 'Statut', 'amount': 'Montant', 'service': 'Service', 'actions': 'Actions',
        'paid': 'Payée', 'unpaid': 'Non payée', 'confirmed': 'Confirmé', 'pending': 'En attente',
        'total': 'Total', 'welcome': 'Bienvenue', 'language': 'Langue',
    },
    'ar': {
        'dashboard': 'لوحة التحكم', 'customers': 'العملاء', 'appointments': 'المواعيد',
        'invoices': 'الفواتير', 'calendar': 'التقويم', 'expenses': 'المصاريف',
        'today': 'اليوم', 'monthly': 'الشهري', 'reports': 'التقارير',
        'settings': 'الإعدادات', 'search': 'بحث...', 'logout': 'تسجيل الخروج',
        'add': 'إضافة', 'edit': 'تعديل', 'delete': 'حذف', 'save': 'حفظ',
        'cancel': 'إلغاء', 'name': 'الاسم', 'phone': 'الهاتف', 'date': 'التاريخ',
        'status': 'الحالة', 'amount': 'المبلغ', 'service': 'الخدمة', 'actions': 'الإجراءات',
        'paid': 'مدفوعة', 'unpaid': 'غير مدفوعة', 'confirmed': 'مؤكد', 'pending': 'في الانتظار',
        'total': 'المجموع', 'welcome': 'مرحبًا', 'language': 'اللغة',
    }
}

@app.route("/set_language/<lang>")
def set_language(lang):
    if lang in TRANSLATIONS:
        session['lang'] = lang
    return redirect(request.referrer or '/')

@app.context_processor
def inject_translations():
    lang = session.get('lang', 'fr')
    return {'t': TRANSLATIONS.get(lang, TRANSLATIONS['fr']), 'current_lang': lang}

if __name__ == '__main__':
    app.run(debug=False, port=5000)

# ─── Context Processor for Notification Badge ───
@app.context_processor
def notification_badge():
    if not session.get('user_id'):
        return {'notif_count': 0}
    try:
        from datetime import date, timedelta
        tomorrow = (date.today() + timedelta(days=1)).isoformat()
        with get_db() as conn:
            tomorrow_count = conn.execute(
                "SELECT COUNT(*) FROM appointments WHERE date = ? AND status = 'pending'",
                (tomorrow,)).fetchone()[0]
            unpaid_count = conn.execute(
                "SELECT COUNT(*) FROM invoices WHERE status IN ('unpaid', 'partial')").fetchone()[0]
        return {'notif_count': tomorrow_count + unpaid_count}
    except Exception:
        return {'notif_count': 0}
        return {'notif_count': 0}