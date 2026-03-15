from flask import Flask, render_template, request, redirect, url_for, flash, make_response, jsonify, session, send_file
from flask_wtf.csrf import CSRFProtect
from models.customer import get_all_customers, add_customer
from models.report import total_customers, total_appointments, total_revenue
from models.appointment import get_appointments
from models.invoice import get_all_invoices
from database.db import create_tables, get_db
import sqlite3
import os
import io
import uuid
import time as time_module
import re
from datetime import datetime, date, timedelta
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

# ─── API Rate Limiting ───
_api_rate = {}  # ip -> (count, window_start)
API_RATE_LIMIT = 60  # requests per minute
API_RATE_WINDOW = 60  # seconds

def check_api_rate_limit():
    """Returns True if rate limit exceeded."""
    ip = request.remote_addr
    now = time_module.time()
    if ip in _api_rate:
        count, window_start = _api_rate[ip]
        if now - window_start > API_RATE_WINDOW:
            _api_rate[ip] = (1, now)
            return False
        if count >= API_RATE_LIMIT:
            return True
        _api_rate[ip] = (count + 1, window_start)
    else:
        _api_rate[ip] = (1, now)
    return False

@app.before_request
def api_rate_limiter():
    """Rate limit API endpoints."""
    if request.path.startswith('/api/'):
        if check_api_rate_limit():
            return jsonify({'error': 'Rate limit exceeded. Try again later.'}), 429

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
            {"src": "/static/icons/icon-72x72.png", "sizes": "72x72", "type": "image/png"},
            {"src": "/static/icons/icon-96x96.png", "sizes": "96x96", "type": "image/png"},
            {"src": "/static/icons/icon-128x128.png", "sizes": "128x128", "type": "image/png"},
            {"src": "/static/icons/icon-144x144.png", "sizes": "144x144", "type": "image/png", "purpose": "any"},
            {"src": "/static/icons/icon-152x152.png", "sizes": "152x152", "type": "image/png"},
            {"src": "/static/icons/icon-192x192.png", "sizes": "192x192", "type": "image/png", "purpose": "any maskable"},
            {"src": "/static/icons/icon-384x384.png", "sizes": "384x384", "type": "image/png"},
            {"src": "/static/icons/icon-512x512.png", "sizes": "512x512", "type": "image/png", "purpose": "any maskable"}
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
                except (ValueError, TypeError, AttributeError):
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
        except (ValueError, TypeError, AttributeError):
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

# ─── Phase 8 Feature 1: Automated Weekly/Monthly Reports ───
@app.route("/scheduled_reports", methods=["GET", "POST"])
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

@app.route("/scheduled_reports/delete/<int:rid>", methods=["POST"])
@login_required
def delete_scheduled_report(rid):
    with get_db() as conn:
        conn.execute("DELETE FROM scheduled_reports WHERE id=?", (rid,))
        conn.commit()
    flash("Rapport supprimé", "success")
    return redirect("/scheduled_reports")

@app.route("/scheduled_reports/send_now/<int:rid>", methods=["POST"])
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

# ─── Phase 8 Feature 2: Smart Alerts System ───
@app.route("/smart_alerts")
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

@app.route("/smart_alerts/read/<int:aid>", methods=["POST"])
@login_required
def mark_alert_read(aid):
    with get_db() as conn:
        conn.execute("UPDATE smart_alerts SET is_read=1 WHERE id=?", (aid,))
        conn.commit()
    return redirect("/smart_alerts")

@app.route("/smart_alerts/read_all", methods=["POST"])
@login_required
def mark_all_alerts_read():
    with get_db() as conn:
        conn.execute("UPDATE smart_alerts SET is_read=1")
        conn.commit()
    flash("Toutes les alertes marquées comme lues", "success")
    return redirect("/smart_alerts")

# ─── Phase 8 Feature 3: WhatsApp API Integration ───
@app.route("/whatsapp_settings", methods=["GET", "POST"])
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

@app.route("/whatsapp_send/<int:customer_id>", methods=["POST"])
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

@app.route("/whatsapp_batch_remind", methods=["POST"])
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

# ─── Phase 8 Feature 4: Warranty Tracking ───
@app.route("/warranties")
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

@app.route("/warranties/add", methods=["POST"])
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

@app.route("/warranty/claim/<int:wid>", methods=["POST"])
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

# ─── Phase 8 Feature 5: Inspection Checklist ───
INSPECTION_ITEMS = [
    ('Extérieur', ['Carrosserie', 'Peinture', 'Vitres', 'Phares', 'Feux arrière', 'Rétroviseurs', 'Essuie-glaces', 'Pneus']),
    ('Intérieur', ['Sièges', 'Tableau de bord', 'Volant', 'Plafond', 'Moquette', 'Ceintures', 'Climatisation', 'Odeur']),
    ('Mécanique', ['Moteur', 'Huile', 'Liquide refroid.', 'Freins', 'Batterie', 'Courroie', 'Échappement', 'Suspension']),
    ('Autre', ['Roue de secours', 'Cric', 'Documents', 'Objets personnels', 'Rayures existantes', 'Bosses existantes']),
]

@app.route("/inspection/<int:appointment_id>", methods=["GET", "POST"])
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

# ─── Phase 8 Feature 6: Invoice Terms & Conditions ───
@app.route("/invoice_terms", methods=["GET", "POST"])
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
@app.route("/dynamic_pricing")
@login_required
def dynamic_pricing():
    with get_db() as conn:
        rules = conn.execute("SELECT * FROM dynamic_pricing WHERE active=1 ORDER BY service_name").fetchall()
        services = conn.execute("SELECT name, price FROM services WHERE active=1 ORDER BY name").fetchall()
    return render_template("dynamic_pricing.html", rules=rules, services=services)

@app.route("/dynamic_pricing/add", methods=["POST"])
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

@app.route("/dynamic_pricing/delete/<int:rid>", methods=["POST"])
@login_required
def delete_pricing_rule(rid):
    with get_db() as conn:
        conn.execute("UPDATE dynamic_pricing SET active=0 WHERE id=?", (rid,))
        conn.commit()
    flash("Règle supprimée", "success")
    return redirect("/dynamic_pricing")

@app.route("/api/get_price")
@login_required
def api_get_price():
    service = request.args.get('service', '')
    car_cat = request.args.get('car_category', 'sedan')
    tier = request.args.get('tier', '')
    with get_db() as conn:
        base_price = conn.execute("SELECT price FROM services WHERE name=?", (service,)).fetchone()
        base = base_price[0] if base_price else 0
        rule = conn.execute("SELECT price_modifier, fixed_price FROM dynamic_pricing WHERE service_name=? AND car_category=? AND active=1",
            (service, car_cat)).fetchone()
        if rule:
            if rule[1] > 0:
                return jsonify({'price': rule[1], 'base': base, 'modifier': 'fixed'})
            return jsonify({'price': round(base * rule[0], 2), 'base': base, 'modifier': rule[0]})
    return jsonify({'price': base, 'base': base, 'modifier': 1.0})

# ─── Phase 8 Feature 8: Retention Analysis ───
@app.route("/retention_analysis")
@login_required
def retention_analysis():
    with get_db() as conn:
        from datetime import date, timedelta
        today = date.today()
        # All customers with visits
        customers_data = conn.execute("""
            SELECT c.id, c.name, c.phone, COUNT(a.id) as visits,
                   MIN(a.date) as first_visit, MAX(a.date) as last_visit,
                   COALESCE(SUM(i.amount),0) as total_spent
            FROM customers c
            LEFT JOIN cars cr ON cr.customer_id=c.id
            LEFT JOIN appointments a ON a.car_id=cr.id
            LEFT JOIN invoices i ON i.appointment_id=a.id AND i.status='Payée'
            GROUP BY c.id ORDER BY last_visit DESC
        """).fetchall()
        total = len(customers_data)
        active_30 = len([c for c in customers_data if c[5] and c[5] >= (today - timedelta(days=30)).isoformat()])
        active_90 = len([c for c in customers_data if c[5] and c[5] >= (today - timedelta(days=90)).isoformat()])
        churned = len([c for c in customers_data if c[5] and c[5] < (today - timedelta(days=90)).isoformat()])
        new_30 = len([c for c in customers_data if c[4] and c[4] >= (today - timedelta(days=30)).isoformat()])
        returning = len([c for c in customers_data if c[3] and c[3] > 1])
        retention_rate = round(returning / total * 100, 1) if total > 0 else 0
        churn_rate = round(churned / total * 100, 1) if total > 0 else 0
        # Monthly retention
        monthly_retention = []
        for i in range(5, -1, -1):
            m = today.replace(day=1) - timedelta(days=i*30)
            ms = m.replace(day=1).isoformat()
            me = (m.replace(day=28) + timedelta(days=4)).replace(day=1).isoformat()
            active = conn.execute("SELECT COUNT(DISTINCT cr.customer_id) FROM appointments a JOIN cars cr ON a.car_id=cr.id WHERE a.date >= ? AND a.date < ?", (ms, me)).fetchone()[0]
            monthly_retention.append({'month': ms[:7], 'active': active})
        # At risk (visited 2+ times, last visit 30-90 days ago)
        at_risk = [c for c in customers_data if c[3] >= 2 and c[5] and
                   (today - timedelta(days=90)).isoformat() <= c[5] < (today - timedelta(days=30)).isoformat()]
    return render_template("retention_analysis.html",
        total=total, active_30=active_30, active_90=active_90, churned=churned,
        new_30=new_30, returning=returning, retention_rate=retention_rate,
        churn_rate=churn_rate, monthly_retention=monthly_retention,
        at_risk=at_risk[:20], customers=customers_data[:50])

# ─── Phase 8 Feature 9: Subscriptions & Contracts ───
@app.route("/subscriptions")
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

@app.route("/subscriptions/add", methods=["POST"])
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

@app.route("/subscriptions/use/<int:sid>", methods=["POST"])
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

@app.route("/subscriptions/cancel/<int:sid>", methods=["POST"])
@login_required
def cancel_subscription(sid):
    with get_db() as conn:
        conn.execute("UPDATE subscriptions SET status='cancelled' WHERE id=?", (sid,))
        conn.commit()
    flash("Abonnement annulé", "info")
    return redirect("/subscriptions")

# ─── Phase 8 Feature 10: Enhanced PWA Push Notifications ───
@app.route("/push_settings", methods=["GET", "POST"])
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

@app.route("/api/push_subscribe", methods=["POST"])
def push_subscribe():
    data = request.get_json()
    if data:
        with get_db() as conn:
            conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('push_sub_' || ?, ?)",
                        (data.get('endpoint', '')[:50], str(data)))
            conn.commit()
    return jsonify({'status': 'ok'})

# ═══════════════════════════════════════════════════════════
# Phase 9: Enterprise Grade Features
# ═══════════════════════════════════════════════════════════

# ─── 2. Export Data (Excel/CSV/PDF) ───
@app.route("/export_data")
@login_required
def export_data():
    return render_template("export_data.html")

@app.route("/export_data/<data_type>/<fmt>")
@login_required
def export_data_download(data_type, fmt):
    import csv
    allowed_types = ['customers', 'invoices', 'appointments', 'cars', 'expenses']
    allowed_fmts = ['csv', 'pdf']
    if data_type not in allowed_types or fmt not in allowed_fmts:
        flash("Type ou format non supporté", "error")
        return redirect("/export_data")

    with get_db() as conn:
        if data_type == 'customers':
            rows = conn.execute("SELECT id, name, phone, email, notes FROM customers ORDER BY name").fetchall()
            headers = ['ID', 'Nom', 'Téléphone', 'Email', 'Notes']
        elif data_type == 'invoices':
            rows = conn.execute("""SELECT i.id, c.name, ca.plate, a.service, i.amount, i.status, i.payment_method
                FROM invoices i JOIN appointments a ON i.appointment_id=a.id
                JOIN cars ca ON a.car_id=ca.id JOIN customers c ON ca.customer_id=c.id
                ORDER BY i.id DESC""").fetchall()
            headers = ['ID', 'Client', 'Plaque', 'Service', 'Montant', 'Statut', 'Paiement']
        elif data_type == 'appointments':
            rows = conn.execute("""SELECT a.id, c.name, ca.plate, a.service, a.date, a.time, a.status, a.assigned_to
                FROM appointments a JOIN cars ca ON a.car_id=ca.id JOIN customers c ON ca.customer_id=c.id
                ORDER BY a.date DESC""").fetchall()
            headers = ['ID', 'Client', 'Plaque', 'Service', 'Date', 'Heure', 'Statut', 'Technicien']
        elif data_type == 'cars':
            rows = conn.execute("""SELECT ca.id, c.name, ca.brand, ca.model, ca.plate, ca.year, ca.color, ca.mileage
                FROM cars ca JOIN customers c ON ca.customer_id=c.id ORDER BY c.name""").fetchall()
            headers = ['ID', 'Propriétaire', 'Marque', 'Modèle', 'Plaque', 'Année', 'Couleur', 'Kilométrage']
        else:  # expenses
            rows = conn.execute("SELECT id, date, category, description, amount FROM expenses ORDER BY date DESC").fetchall()
            headers = ['ID', 'Date', 'Catégorie', 'Description', 'Montant']

    if fmt == 'csv':
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(headers)
        for row in rows:
            writer.writerow(row)
        resp = make_response(output.getvalue())
        resp.headers['Content-Type'] = 'text/csv; charset=utf-8'
        resp.headers['Content-Disposition'] = f'attachment; filename=amilcar_{data_type}.csv'
        return resp
    else:  # pdf
        try:
            from xhtml2pdf import pisa
        except ImportError:
            flash("xhtml2pdf non installé", "error")
            return redirect("/export_data")
        html = f"<html><head><meta charset='utf-8'><style>body{{font-family:Helvetica,sans-serif;font-size:11px}}table{{width:100%;border-collapse:collapse}}th,td{{border:1px solid #ccc;padding:5px;text-align:left}}th{{background:#D4AF37;color:#fff}}</style></head><body>"
        html += f"<h2>AMILCAR — {data_type.upper()}</h2><table><tr>"
        for h in headers:
            html += f"<th>{h}</th>"
        html += "</tr>"
        for row in rows:
            html += "<tr>" + "".join(f"<td>{c}</td>" for c in row) + "</tr>"
        html += "</table></body></html>"
        pdf_out = io.BytesIO()
        pisa.CreatePDF(io.StringIO(html), dest=pdf_out)
        resp = make_response(pdf_out.getvalue())
        resp.headers['Content-Type'] = 'application/pdf'
        resp.headers['Content-Disposition'] = f'attachment; filename=amilcar_{data_type}.pdf'
        return resp

# ─── 3. CRM Follow-up System ───
@app.route("/crm_followups")
@login_required
def crm_followups():
    with get_db() as conn:
        followups = conn.execute("""SELECT f.*, c.name, c.phone FROM crm_followups f
            JOIN customers c ON f.customer_id=c.id ORDER BY f.scheduled_date""").fetchall()
        # Clients absents > 60 jours
        from datetime import date, timedelta
        cutoff = (date.today() - timedelta(days=60)).isoformat()
        absent = conn.execute("""SELECT c.id, c.name, c.phone, MAX(a.date) as last_visit
            FROM customers c LEFT JOIN cars ca ON ca.customer_id=c.id
            LEFT JOIN appointments a ON a.car_id=ca.id
            GROUP BY c.id HAVING last_visit < ? OR last_visit IS NULL
            ORDER BY last_visit""", (cutoff,)).fetchall()
    return render_template("crm_followups.html", followups=followups, absent=absent)

@app.route("/crm_followup/add", methods=["POST"])
@login_required
def crm_followup_add():
    cid = request.form.get("customer_id", type=int)
    ftype = request.form.get("type", "absence")
    scheduled = request.form.get("scheduled_date", "")
    reason = request.form.get("reason", "")
    notes = request.form.get("notes", "")
    if cid and scheduled:
        with get_db() as conn:
            conn.execute("INSERT INTO crm_followups (customer_id, type, scheduled_date, reason, notes) VALUES (?,?,?,?,?)",
                        (cid, ftype, scheduled, reason, notes))
            conn.commit()
        flash("Suivi CRM ajouté !", "success")
    return redirect("/crm_followups")

@app.route("/crm_followup/complete/<int:fid>")
@login_required
def crm_followup_complete(fid):
    from datetime import date
    with get_db() as conn:
        conn.execute("UPDATE crm_followups SET status='completed', completed_at=? WHERE id=?",
                    (date.today().isoformat(), fid))
        conn.commit()
    flash("Suivi marqué comme complété", "success")
    return redirect("/crm_followups")

@app.route("/crm_followup/auto_generate")
@login_required
def crm_followup_auto():
    from datetime import date, timedelta
    cutoff = (date.today() - timedelta(days=60)).isoformat()
    scheduled = (date.today() + timedelta(days=1)).isoformat()
    with get_db() as conn:
        absent = conn.execute("""SELECT c.id FROM customers c LEFT JOIN cars ca ON ca.customer_id=c.id
            LEFT JOIN appointments a ON a.car_id=ca.id GROUP BY c.id
            HAVING MAX(a.date) < ? OR MAX(a.date) IS NULL""", (cutoff,)).fetchall()
        existing = set(r[0] for r in conn.execute(
            "SELECT customer_id FROM crm_followups WHERE status='pending'").fetchall())
        count = 0
        for row in absent:
            if row[0] not in existing:
                conn.execute("INSERT INTO crm_followups (customer_id, type, scheduled_date, reason) VALUES (?,?,?,?)",
                            (row[0], 'absence', scheduled, 'Client absent > 60 jours'))
                count += 1
        conn.commit()
    flash(f"{count} suivis générés automatiquement", "success")
    return redirect("/crm_followups")

# ─── 4. Employee Shifts Management ───
@app.route("/employee_shifts")
@login_required
@admin_required
def employee_shifts():
    week_offset = request.args.get("week", 0, type=int)
    from datetime import date, timedelta
    today = date.today()
    start = today - timedelta(days=today.weekday()) + timedelta(weeks=week_offset)
    end = start + timedelta(days=6)
    with get_db() as conn:
        users = conn.execute("SELECT id, username, full_name FROM users WHERE role != 'admin' ORDER BY username").fetchall()
        shifts = conn.execute("SELECT * FROM employee_shifts WHERE shift_date BETWEEN ? AND ? ORDER BY shift_date, start_time",
                             (start.isoformat(), end.isoformat())).fetchall()
        leaves = conn.execute("SELECT * FROM employee_leaves WHERE start_date <= ? AND end_date >= ? ORDER BY start_date",
                             (end.isoformat(), start.isoformat())).fetchall()
    days = [(start + timedelta(days=i)) for i in range(7)]
    return render_template("employee_shifts.html", users=users, shifts=shifts, leaves=leaves,
                          days=days, start=start, end=end, week_offset=week_offset)

@app.route("/employee_shifts/add", methods=["POST"])
@login_required
@admin_required
def employee_shift_add():
    uid = request.form.get("user_id", type=int)
    shift_date = request.form.get("shift_date", "")
    start_time = request.form.get("start_time", "08:00")
    end_time = request.form.get("end_time", "17:00")
    shift_type = request.form.get("shift_type", "normal")
    notes = request.form.get("notes", "")
    if uid and shift_date:
        with get_db() as conn:
            uname = conn.execute("SELECT username FROM users WHERE id=?", (uid,)).fetchone()
            conn.execute("INSERT INTO employee_shifts (user_id, username, shift_date, start_time, end_time, shift_type, notes) VALUES (?,?,?,?,?,?,?)",
                        (uid, uname[0] if uname else '', shift_date, start_time, end_time, shift_type, notes))
            conn.commit()
        flash("Shift ajouté !", "success")
    return redirect("/employee_shifts")

@app.route("/employee_shifts/delete/<int:sid>")
@login_required
@admin_required
def employee_shift_delete(sid):
    with get_db() as conn:
        conn.execute("DELETE FROM employee_shifts WHERE id=?", (sid,))
        conn.commit()
    flash("Shift supprimé", "success")
    return redirect("/employee_shifts")

@app.route("/employee_leave/add", methods=["POST"])
@login_required
@admin_required
def employee_leave_add():
    uid = request.form.get("user_id", type=int)
    leave_type = request.form.get("leave_type", "annual")
    start_date = request.form.get("start_date", "")
    end_date = request.form.get("end_date", "")
    reason = request.form.get("reason", "")
    if uid and start_date and end_date:
        with get_db() as conn:
            uname = conn.execute("SELECT username FROM users WHERE id=?", (uid,)).fetchone()
            conn.execute("INSERT INTO employee_leaves (user_id, username, leave_type, start_date, end_date, reason) VALUES (?,?,?,?,?,?)",
                        (uid, uname[0] if uname else '', leave_type, start_date, end_date, reason))
            conn.commit()
        flash("Congé enregistré !", "success")
    return redirect("/employee_shifts")

@app.route("/employee_leave/approve/<int:lid>/<action>")
@login_required
@admin_required
def employee_leave_action(lid, action):
    if action in ('approved', 'rejected'):
        with get_db() as conn:
            conn.execute("UPDATE employee_leaves SET status=? WHERE id=?", (action, lid))
            conn.commit()
    return redirect("/employee_shifts")

# ─── 5. Service Profitability Report (Enhanced) ───
@app.route("/service_profitability")
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

# ─── 6. Referral System ───
@app.route("/referrals")
@login_required
def referrals():
    with get_db() as conn:
        refs = conn.execute("""SELECT r.*, c.name as referrer_name, c.phone as referrer_phone
            FROM referrals r JOIN customers c ON r.referrer_id=c.id ORDER BY r.created_at DESC""").fetchall()
        customers = conn.execute("SELECT id, name, phone FROM customers ORDER BY name").fetchall()
    return render_template("referrals.html", referrals=refs, customers=customers)

@app.route("/referral/add", methods=["POST"])
@login_required
def referral_add():
    referrer_id = request.form.get("referrer_id", type=int)
    referred_name = request.form.get("referred_name", "").strip()
    referred_phone = request.form.get("referred_phone", "").strip()
    reward_type = request.form.get("reward_type", "free_wash")
    if referrer_id and referred_name and referred_phone:
        with get_db() as conn:
            conn.execute("INSERT INTO referrals (referrer_id, referred_name, referred_phone, reward_type) VALUES (?,?,?,?)",
                        (referrer_id, referred_name, referred_phone, reward_type))
            conn.commit()
        flash("Parrainage enregistré !", "success")
    return redirect("/referrals")

@app.route("/referral/convert/<int:rid>")
@login_required
def referral_convert(rid):
    with get_db() as conn:
        ref = conn.execute("SELECT * FROM referrals WHERE id=?", (rid,)).fetchone()
        if ref:
            # Create customer from referral
            conn.execute("INSERT INTO customers (name, phone, referred_by) VALUES (?,?,?)",
                        (ref[2], ref[3], ref[1]))
            new_cid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            conn.execute("UPDATE referrals SET status='converted', converted_customer_id=? WHERE id=?", (new_cid, rid))
            # Reward the referrer
            try:
                conn.execute("INSERT INTO reward_points (customer_id, points, total_earned) VALUES (?, 50, 50) ON CONFLICT(customer_id) DO UPDATE SET points=points+50, total_earned=total_earned+50", (ref[1],))
                conn.execute("INSERT INTO reward_history (customer_id, points, type, description) VALUES (?,50,'earn','Bonus parrainage')", (ref[1],))
            except Exception:
                pass
            conn.commit()
            flash(f"Client créé + 50 points offerts au parrain !", "success")
    return redirect("/referrals")

# ─── 7. Fleet / Company Accounts ───
@app.route("/fleet_companies")
@login_required
def fleet_companies():
    with get_db() as conn:
        companies = conn.execute("SELECT * FROM fleet_companies ORDER BY name").fetchall()
        # Count vehicles per company
        vehicle_counts = {}
        for row in conn.execute("SELECT company_id, COUNT(*) FROM fleet_vehicles GROUP BY company_id").fetchall():
            vehicle_counts[row[0]] = row[1]
    return render_template("fleet_companies.html", companies=companies, vehicle_counts=vehicle_counts)

@app.route("/fleet_company/add", methods=["POST"])
@login_required
def fleet_company_add():
    name = request.form.get("name", "").strip()
    contact = request.form.get("contact_person", "").strip()
    phone = request.form.get("phone", "").strip()
    email = request.form.get("email", "").strip()
    address = request.form.get("address", "").strip()
    contract_start = request.form.get("contract_start", "")
    contract_end = request.form.get("contract_end", "")
    discount = request.form.get("discount_percent", 0, type=float)
    payment_terms = request.form.get("payment_terms", "monthly")
    notes = request.form.get("notes", "").strip()
    if name:
        with get_db() as conn:
            conn.execute("""INSERT INTO fleet_companies (name, contact_person, phone, email, address,
                contract_start, contract_end, discount_percent, payment_terms, notes)
                VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (name, contact, phone, email, address, contract_start, contract_end, discount, payment_terms, notes))
            conn.commit()
        flash("Entreprise ajoutée !", "success")
    return redirect("/fleet_companies")

@app.route("/fleet_company/<int:cid>")
@login_required
def fleet_company_detail(cid):
    with get_db() as conn:
        company = conn.execute("SELECT * FROM fleet_companies WHERE id=?", (cid,)).fetchone()
        if not company:
            flash("Entreprise non trouvée", "error")
            return redirect("/fleet_companies")
        vehicles = conn.execute("""SELECT fv.id, ca.id as car_id, ca.brand, ca.model, ca.plate, c.name as owner
            FROM fleet_vehicles fv JOIN cars ca ON fv.car_id=ca.id
            JOIN customers c ON ca.customer_id=c.id WHERE fv.company_id=?""", (cid,)).fetchall()
        all_cars = conn.execute("SELECT ca.id, ca.brand, ca.model, ca.plate, c.name FROM cars ca JOIN customers c ON ca.customer_id=c.id ORDER BY c.name").fetchall()
        # Invoice summary for company vehicles
        car_ids = [v[1] for v in vehicles]
        total_spent = 0
        if car_ids:
            placeholders = ','.join('?' * len(car_ids))
            total_spent = conn.execute(f"""SELECT COALESCE(SUM(i.amount),0) FROM invoices i
                JOIN appointments a ON i.appointment_id=a.id
                WHERE a.car_id IN ({placeholders}) AND i.status='paid'""", car_ids).fetchone()[0]
    return render_template("fleet_company_detail.html", company=company, vehicles=vehicles,
                          all_cars=all_cars, total_spent=total_spent)

@app.route("/fleet_vehicle/add/<int:cid>", methods=["POST"])
@login_required
def fleet_vehicle_add(cid):
    car_id = request.form.get("car_id", type=int)
    if car_id:
        with get_db() as conn:
            try:
                conn.execute("INSERT INTO fleet_vehicles (company_id, car_id) VALUES (?,?)", (cid, car_id))
                conn.commit()
                flash("Véhicule ajouté à la flotte !", "success")
            except Exception:
                flash("Ce véhicule est déjà dans cette flotte", "error")
    return redirect(f"/fleet_company/{cid}")

@app.route("/fleet_vehicle/remove/<int:fvid>/<int:cid>")
@login_required
def fleet_vehicle_remove(fvid, cid):
    with get_db() as conn:
        conn.execute("DELETE FROM fleet_vehicles WHERE id=?", (fvid,))
        conn.commit()
    flash("Véhicule retiré de la flotte", "success")
    return redirect(f"/fleet_company/{cid}")

# ─── 8. Vehicle History ───
@app.route("/vehicle_history")
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

# ─── 9. Staff Notes ───
@app.route("/staff_notes")
@login_required
def staff_notes():
    entity_type = request.args.get("type", "customer")
    entity_id = request.args.get("id", type=int)
    with get_db() as conn:
        notes = []
        entity_name = ""
        if entity_type and entity_id:
            notes = conn.execute("""SELECT * FROM staff_notes WHERE entity_type=? AND entity_id=?
                ORDER BY created_at DESC""", (entity_type, entity_id)).fetchall()
            if entity_type == 'customer':
                row = conn.execute("SELECT name FROM customers WHERE id=?", (entity_id,)).fetchone()
                entity_name = row[0] if row else ""
            elif entity_type == 'car':
                row = conn.execute("SELECT brand, model, plate FROM cars WHERE id=?", (entity_id,)).fetchone()
                entity_name = f"{row[0]} {row[1]} ({row[2]})" if row else ""
        recent = conn.execute("""SELECT sn.*, CASE sn.entity_type
            WHEN 'customer' THEN (SELECT name FROM customers WHERE id=sn.entity_id)
            WHEN 'car' THEN (SELECT brand||' '||model||' ('||plate||')' FROM cars WHERE id=sn.entity_id)
            ELSE '' END as entity_name
            FROM staff_notes sn ORDER BY sn.created_at DESC LIMIT 50""").fetchall()
    return render_template("staff_notes.html", notes=notes, recent=recent,
                          entity_type=entity_type, entity_id=entity_id, entity_name=entity_name)

@app.route("/staff_note/add", methods=["POST"])
@login_required
def staff_note_add():
    entity_type = request.form.get("entity_type", "customer")
    entity_id = request.form.get("entity_id", type=int)
    note = request.form.get("note", "").strip()
    priority = request.form.get("priority", "normal")
    if entity_id and note:
        with get_db() as conn:
            conn.execute("INSERT INTO staff_notes (entity_type, entity_id, user_id, username, note, priority) VALUES (?,?,?,?,?,?)",
                        (entity_type, entity_id, session.get('user_id', 0),
                         session.get('username', ''), note, priority))
            conn.commit()
        flash("Note ajoutée !", "success")
    return redirect(f"/staff_notes?type={entity_type}&id={entity_id}")

@app.route("/staff_note/delete/<int:nid>")
@login_required
def staff_note_delete(nid):
    with get_db() as conn:
        note = conn.execute("SELECT entity_type, entity_id FROM staff_notes WHERE id=?", (nid,)).fetchone()
        conn.execute("DELETE FROM staff_notes WHERE id=?", (nid,))
        conn.commit()
    if note:
        return redirect(f"/staff_notes?type={note[0]}&id={note[1]}")
    return redirect("/staff_notes")

# ─── 10. Custom Widget Dashboard ───
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

@app.route("/custom_dashboard")
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
                data[wtype] = rows
            elif wtype == 'recent_customers':
                rows = conn.execute("SELECT name, phone FROM customers ORDER BY id DESC LIMIT 5").fetchall()
                data[wtype] = rows
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
                data[wtype] = rows
    return render_template("custom_dashboard.html", widgets=widgets, data=data,
                          available=AVAILABLE_WIDGETS)

@app.route("/custom_dashboard/toggle", methods=["POST"])
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

# ═══════════════════════════════════════════════════════════
# Phase 10: World-Class Operations
# ═══════════════════════════════════════════════════════════

# ─── 1. RFM Analysis ───
RFM_SEGMENTS = {
    (5,5): 'champion', (5,4): 'champion', (4,5): 'champion',
    (5,3): 'loyal', (4,4): 'loyal', (3,5): 'loyal',
    (5,2): 'potential_loyalist', (4,3): 'potential_loyalist', (3,4): 'potential_loyalist',
    (5,1): 'new_customer', (4,1): 'new_customer', (4,2): 'new_customer',
    (3,3): 'need_attention', (3,2): 'need_attention', (2,3): 'need_attention',
    (3,1): 'about_to_sleep', (2,2): 'about_to_sleep',
    (2,1): 'at_risk', (1,3): 'at_risk', (1,4): 'at_risk', (1,5): 'at_risk',
    (1,2): 'hibernating', (1,1): 'lost', (2,4): 'at_risk', (2,5): 'at_risk',
}
RFM_LABELS = {
    'champion': ('🏆 Champion', '#34d399'), 'loyal': ('💎 Fidèle', '#D4AF37'),
    'potential_loyalist': ('⭐ Potentiel Fidèle', '#1B6B93'), 'new_customer': ('🆕 Nouveau', '#60a5fa'),
    'need_attention': ('⚠️ Attention requise', '#f59e0b'), 'about_to_sleep': ('😴 En veille', '#f97316'),
    'at_risk': ('🔴 À risque', '#ef4444'), 'hibernating': ('❄️ Hibernant', '#6b7280'),
    'lost': ('💀 Perdu', '#374151'), 'new': ('🆕 Non classé', '#9ca3af'),
}

@app.route("/rfm_analysis")
@login_required
@admin_required
def rfm_analysis():
    from datetime import date, timedelta
    today = date.today()
    with get_db() as conn:
        # Calculate RFM for all customers
        customers = conn.execute("""SELECT c.id, c.name, c.phone,
            MAX(a.date) as last_visit,
            COUNT(DISTINCT a.id) as frequency,
            COALESCE(SUM(i.amount),0) as monetary
            FROM customers c
            LEFT JOIN cars ca ON ca.customer_id=c.id
            LEFT JOIN appointments a ON a.car_id=ca.id AND a.status='completed'
            LEFT JOIN invoices i ON i.appointment_id=a.id AND i.status='paid'
            GROUP BY c.id""").fetchall()

        segments = {}
        all_data = []
        for c in customers:
            cid, name, phone = c[0], c[1], c[2]
            last_visit = c[3]
            freq = c[4] or 0
            monetary = c[5] or 0

            # Recency in days
            if last_visit:
                try:
                    lv = date.fromisoformat(last_visit)
                    recency_days = (today - lv).days
                except (ValueError, TypeError):
                    recency_days = 999
            else:
                recency_days = 999

            # Score 1-5
            r = 5 if recency_days < 30 else 4 if recency_days < 60 else 3 if recency_days < 90 else 2 if recency_days < 180 else 1
            f = min(5, max(1, freq))
            m_score = 5 if monetary > 1000 else 4 if monetary > 500 else 3 if monetary > 200 else 2 if monetary > 50 else 1

            fm = max(f, m_score)
            segment = RFM_SEGMENTS.get((r, fm), 'need_attention')

            # Save to DB
            conn.execute("""INSERT INTO rfm_segments (customer_id, recency_score, frequency_score, monetary_score, rfm_score, segment)
                VALUES (?,?,?,?,?,?) ON CONFLICT(customer_id) DO UPDATE SET
                recency_score=?, frequency_score=?, monetary_score=?, rfm_score=?, segment=?, last_calculated=CURRENT_TIMESTAMP""",
                (cid, r, f, m_score, r*100+f*10+m_score, segment, r, f, m_score, r*100+f*10+m_score, segment))
            conn.execute("UPDATE customers SET rfm_segment=? WHERE id=?", (segment, cid))

            segments[segment] = segments.get(segment, 0) + 1
            all_data.append({'id': cid, 'name': name, 'phone': phone, 'segment': segment,
                            'recency': recency_days, 'frequency': freq, 'monetary': monetary,
                            'r': r, 'f': f, 'm': m_score})
        conn.commit()

    return render_template("rfm_analysis.html", data=all_data, segments=segments, labels=RFM_LABELS)

# ─── 2. Marketing Campaigns (Auto) ───
@app.route("/marketing_campaigns")
@login_required
@admin_required
def marketing_campaigns():
    with get_db() as conn:
        campaigns = conn.execute("SELECT * FROM marketing_campaigns ORDER BY created_at DESC").fetchall()
        segments = conn.execute("SELECT segment, COUNT(*) FROM rfm_segments GROUP BY segment").fetchall()
    return render_template("marketing_campaigns.html", campaigns=campaigns, segments=segments)

@app.route("/marketing_campaign/add", methods=["POST"])
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

@app.route("/marketing_campaign/run/<int:cid>")
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

@app.route("/marketing_campaign/toggle/<int:cid>")
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

# ─── 3. Bay / Resource Management ───
@app.route("/bays")
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

@app.route("/bay/add", methods=["POST"])
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

@app.route("/bay/book", methods=["POST"])
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

@app.route("/bay/toggle/<int:bid>")
@login_required
@admin_required
def bay_toggle(bid):
    with get_db() as conn:
        b = conn.execute("SELECT active FROM service_bays WHERE id=?", (bid,)).fetchone()
        if b:
            conn.execute("UPDATE service_bays SET active=? WHERE id=?", (0 if b[0] else 1, bid))
            conn.commit()
    return redirect("/bays")

# ─── 4. P&L Financial Dashboard ───
@app.route("/pnl_dashboard")
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
        history = conn.execute("SELECT * FROM monthly_pnl ORDER BY month DESC LIMIT 12").fetchall()
    return render_template("pnl_dashboard.html", month=month, revenue=revenue, expenses=expenses,
                          materials=materials, net_profit=net_profit, expense_cats=expense_cats, history=history)

# ─── 5. Employee Targets & Commissions ───
@app.route("/employee_targets")
@login_required
@admin_required
def employee_targets_page():
    from datetime import date
    month = request.args.get("month", date.today().strftime("%Y-%m"))
    with get_db() as conn:
        users = conn.execute("SELECT id, username, full_name, commission_rate FROM users WHERE role != 'admin' ORDER BY username").fetchall()
        targets = conn.execute("SELECT * FROM employee_targets WHERE month=?", (month,)).fetchall()
        # Calculate actuals
        for u in users:
            actual_rev = conn.execute("""SELECT COALESCE(SUM(i.amount),0) FROM invoices i
                JOIN appointments a ON i.appointment_id=a.id
                WHERE a.assigned_to=? AND strftime('%%Y-%%m', a.date)=? AND i.status='paid'""",
                (u[1], month)).fetchone()[0]
            actual_jobs = conn.execute("""SELECT COUNT(*) FROM appointments
                WHERE assigned_to=? AND strftime('%%Y-%%m', date)=? AND status='completed'""",
                (u[1], month)).fetchone()[0]
            existing = conn.execute("SELECT id FROM employee_targets WHERE user_id=? AND month=?", (u[0], month)).fetchone()
            if existing:
                conn.execute("UPDATE employee_targets SET actual_revenue=?, actual_jobs=?, commission_earned=actual_revenue*commission_rate/100 WHERE id=?",
                            (actual_rev, actual_jobs, existing[0]))
            else:
                rate = u[3] or 0
                conn.execute("INSERT INTO employee_targets (user_id, username, month, actual_revenue, actual_jobs, commission_rate) VALUES (?,?,?,?,?,?)",
                            (u[0], u[1], month, actual_rev, actual_jobs, rate))
        conn.commit()
        targets = conn.execute("SELECT * FROM employee_targets WHERE month=? ORDER BY actual_revenue DESC", (month,)).fetchall()
    return render_template("employee_targets.html", users=users, targets=targets, month=month)

@app.route("/employee_target/set", methods=["POST"])
@login_required
@admin_required
def employee_target_set():
    uid = request.form.get("user_id", type=int)
    month = request.form.get("month", "")
    target_rev = request.form.get("target_revenue", 0, type=float)
    target_jobs = request.form.get("target_jobs", 0, type=int)
    comm_rate = request.form.get("commission_rate", 0, type=float)
    bonus = request.form.get("bonus", 0, type=float)
    if uid and month:
        with get_db() as conn:
            uname = conn.execute("SELECT username FROM users WHERE id=?", (uid,)).fetchone()
            conn.execute("""INSERT INTO employee_targets (user_id, username, month, target_revenue, target_jobs, commission_rate, bonus)
                VALUES (?,?,?,?,?,?,?) ON CONFLICT(user_id, month) DO UPDATE SET
                target_revenue=?, target_jobs=?, commission_rate=?, bonus=?""",
                (uid, uname[0] if uname else '', month, target_rev, target_jobs, comm_rate, bonus,
                 target_rev, target_jobs, comm_rate, bonus))
            conn.execute("UPDATE users SET commission_rate=? WHERE id=?", (comm_rate, uid))
            conn.commit()
        flash("Objectif mis à jour !", "success")
    return redirect(f"/employee_targets?month={month}")

# ─── 6. Digital Inspection with Photos ───
INSPECTION_CATEGORIES = {
    'Pneus & Freins': ['Pneu AVG', 'Pneu AVD', 'Pneu ARG', 'Pneu ARD', 'Plaquettes avant', 'Plaquettes arrière', 'Disques'],
    'Sous capot': ['Huile moteur', 'Liquide refroidissement', 'Liquide frein', 'Batterie', 'Courroie', 'Filtre air'],
    'Éclairage': ['Phares avant', 'Phares arrière', 'Clignotants', 'Feux stop', 'Anti-brouillard'],
    'Carrosserie': ['Pare-brise', 'Essuie-glaces', 'Rétroviseurs', 'État peinture', 'Joints portes'],
}

@app.route("/digital_inspection/<int:appt_id>", methods=["GET", "POST"])
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

@app.route("/digital_inspection/view/<token>")
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

@app.route("/digital_inspection/notify/<int:appt_id>")
@login_required
def digital_inspection_notify(appt_id):
    with get_db() as conn:
        insp = conn.execute("SELECT token FROM digital_inspections WHERE appointment_id=?", (appt_id,)).fetchone()
        if insp:
            conn.execute("UPDATE digital_inspections SET customer_notified=1 WHERE appointment_id=?", (appt_id,))
            conn.commit()
            flash(f"Lien d'inspection: /digital_inspection/view/{insp[0]}", "success")
    return redirect(f"/digital_inspection/{appt_id}")

# ─── 7. Auto Purchase Orders ───
@app.route("/auto_purchase_orders")
@login_required
@admin_required
def auto_purchase_orders():
    with get_db() as conn:
        # Find low stock items
        low_stock = conn.execute("""SELECT id, name, quantity, min_quantity, supplier
            FROM inventory WHERE quantity <= min_quantity""").fetchall()
        # Generate suggestions
        for item in low_stock:
            existing = conn.execute("SELECT id FROM auto_purchase_orders WHERE inventory_id=? AND status='suggested'",
                                   (item[0],)).fetchone()
            if not existing:
                order_qty = max(item[3] * 2 - item[2], item[3])
                supplier_id = None
                if item[4]:
                    sup = conn.execute("SELECT id FROM suppliers WHERE name=?", (item[4],)).fetchone()
                    supplier_id = sup[0] if sup else None
                conn.execute("""INSERT INTO auto_purchase_orders (inventory_id, supplier_id, item_name, current_qty, min_qty, order_qty)
                    VALUES (?,?,?,?,?,?)""", (item[0], supplier_id, item[1], item[2], item[3], order_qty))
        conn.commit()
        orders = conn.execute("""SELECT apo.*, s.name as supplier_name FROM auto_purchase_orders apo
            LEFT JOIN suppliers s ON apo.supplier_id=s.id ORDER BY apo.status, apo.created_at DESC""").fetchall()
    return render_template("auto_purchase_orders.html", orders=orders)

@app.route("/auto_purchase_order/approve/<int:oid>")
@login_required
@admin_required
def auto_po_approve(oid):
    with get_db() as conn:
        order = conn.execute("SELECT * FROM auto_purchase_orders WHERE id=?", (oid,)).fetchone()
        if order and order[7] == 'suggested':
            # Create actual purchase order
            if order[2]:
                conn.execute("""INSERT INTO purchase_orders (supplier_id, order_date, status, total_amount)
                    VALUES (?, date('now'), 'pending', 0)""", (order[2],))
                po_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
                conn.execute("""INSERT INTO purchase_items (order_id, inventory_id, item_name, quantity, unit_price)
                    VALUES (?,?,?,?,0)""", (po_id, order[1], order[3], order[6]))
            conn.execute("UPDATE auto_purchase_orders SET status='approved' WHERE id=?", (oid,))
            conn.commit()
            flash("Commande approuvée et créée !", "success")
    return redirect("/auto_purchase_orders")

@app.route("/auto_purchase_order/dismiss/<int:oid>")
@login_required
@admin_required
def auto_po_dismiss(oid):
    with get_db() as conn:
        conn.execute("UPDATE auto_purchase_orders SET status='dismissed' WHERE id=?", (oid,))
        conn.commit()
    return redirect("/auto_purchase_orders")

# ─── 8. Seasonal Campaigns ───
@app.route("/seasonal_campaigns")
@login_required
@admin_required
def seasonal_campaigns():
    with get_db() as conn:
        campaigns = conn.execute("SELECT * FROM seasonal_campaigns ORDER BY start_date DESC").fetchall()
    return render_template("seasonal_campaigns.html", campaigns=campaigns)

@app.route("/seasonal_campaign/add", methods=["POST"])
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

@app.route("/seasonal_campaign/launch/<int:cid>")
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

# ─── 9. Accounts Receivable Aging ───
@app.route("/ar_aging")
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

# ─── 10. REST API + Webhooks ───
import hashlib, hmac as hmac_module

def check_api_key():
    key = request.headers.get('X-API-Key', '') or request.args.get('api_key', '')
    if not key:
        return None
    with get_db() as conn:
        row = conn.execute("SELECT * FROM api_keys WHERE api_key=? AND active=1", (key,)).fetchone()
        if row:
            conn.execute("UPDATE api_keys SET last_used=datetime('now') WHERE id=?", (row[0],))
            conn.commit()
            return row
    return None

@app.route("/api/v1/customers", methods=["GET"])
@csrf.exempt
def api_customers():
    auth = check_api_key()
    if not auth:
        return jsonify({'error': 'Unauthorized'}), 401
    with get_db() as conn:
        rows = conn.execute("SELECT id, name, phone, email FROM customers ORDER BY name LIMIT 100").fetchall()
    return jsonify([{'id': r[0], 'name': r[1], 'phone': r[2], 'email': r[3]} for r in rows])

@app.route("/api/v1/appointments", methods=["GET"])
@csrf.exempt
def api_appointments():
    auth = check_api_key()
    if not auth:
        return jsonify({'error': 'Unauthorized'}), 401
    date_filter = request.args.get('date', '')
    with get_db() as conn:
        if date_filter:
            rows = conn.execute("""SELECT a.id, a.date, a.time, a.service, a.status, c.name, ca.plate
                FROM appointments a JOIN cars ca ON a.car_id=ca.id JOIN customers c ON ca.customer_id=c.id
                WHERE a.date=? ORDER BY a.time""", (date_filter,)).fetchall()
        else:
            rows = conn.execute("""SELECT a.id, a.date, a.time, a.service, a.status, c.name, ca.plate
                FROM appointments a JOIN cars ca ON a.car_id=ca.id JOIN customers c ON ca.customer_id=c.id
                ORDER BY a.date DESC LIMIT 50""").fetchall()
    return jsonify([{'id': r[0], 'date': r[1], 'time': r[2], 'service': r[3], 'status': r[4],
                     'customer': r[5], 'plate': r[6]} for r in rows])

@app.route("/api/v1/invoices", methods=["GET"])
@csrf.exempt
def api_invoices():
    auth = check_api_key()
    if not auth:
        return jsonify({'error': 'Unauthorized'}), 401
    status = request.args.get('status', '')
    with get_db() as conn:
        if status:
            rows = conn.execute("""SELECT i.id, i.amount, i.status, a.service, c.name
                FROM invoices i JOIN appointments a ON i.appointment_id=a.id
                JOIN cars ca ON a.car_id=ca.id JOIN customers c ON ca.customer_id=c.id
                WHERE i.status=? ORDER BY i.id DESC LIMIT 50""", (status,)).fetchall()
        else:
            rows = conn.execute("""SELECT i.id, i.amount, i.status, a.service, c.name
                FROM invoices i JOIN appointments a ON i.appointment_id=a.id
                JOIN cars ca ON a.car_id=ca.id JOIN customers c ON ca.customer_id=c.id
                ORDER BY i.id DESC LIMIT 50""").fetchall()
    return jsonify([{'id': r[0], 'amount': r[1], 'status': r[2], 'service': r[3], 'customer': r[4]} for r in rows])

@app.route("/api/v1/stats", methods=["GET"])
@csrf.exempt
def api_stats():
    auth = check_api_key()
    if not auth:
        return jsonify({'error': 'Unauthorized'}), 401
    from datetime import date
    today = date.today().isoformat()
    month = date.today().strftime("%Y-%m")
    with get_db() as conn:
        today_rev = conn.execute("""SELECT COALESCE(SUM(i.amount),0) FROM invoices i
            JOIN appointments a ON i.appointment_id=a.id WHERE a.date=? AND i.status='paid'""", (today,)).fetchone()[0]
        month_rev = conn.execute("""SELECT COALESCE(SUM(i.amount),0) FROM invoices i
            JOIN appointments a ON i.appointment_id=a.id WHERE strftime('%%Y-%%m',a.date)=? AND i.status='paid'""", (month,)).fetchone()[0]
        total_customers = conn.execute("SELECT COUNT(*) FROM customers").fetchone()[0]
        today_appts = conn.execute("SELECT COUNT(*) FROM appointments WHERE date=?", (today,)).fetchone()[0]
    return jsonify({'today_revenue': today_rev, 'month_revenue': month_rev,
                    'total_customers': total_customers, 'today_appointments': today_appts})

@app.route("/api_settings")
@login_required
@admin_required
def api_settings():
    with get_db() as conn:
        keys = conn.execute("SELECT * FROM api_keys ORDER BY created_at DESC").fetchall()
        hooks = conn.execute("SELECT * FROM webhooks ORDER BY created_at DESC").fetchall()
    return render_template("api_settings.html", keys=keys, hooks=hooks)

@app.route("/api_key/add", methods=["POST"])
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

@app.route("/api_key/toggle/<int:kid>")
@login_required
@admin_required
def api_key_toggle(kid):
    with get_db() as conn:
        k = conn.execute("SELECT active FROM api_keys WHERE id=?", (kid,)).fetchone()
        if k:
            conn.execute("UPDATE api_keys SET active=? WHERE id=?", (0 if k[0] else 1, kid))
            conn.commit()
    return redirect("/api_settings")

@app.route("/api_key/delete/<int:kid>")
@login_required
@admin_required
def api_key_delete(kid):
    with get_db() as conn:
        conn.execute("DELETE FROM api_keys WHERE id=?", (kid,))
        conn.commit()
    flash("Clé supprimée", "success")
    return redirect("/api_settings")

@app.route("/webhook/add", methods=["POST"])
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

@app.route("/webhook/toggle/<int:wid>")
@login_required
@admin_required
def webhook_toggle(wid):
    with get_db() as conn:
        w = conn.execute("SELECT active FROM webhooks WHERE id=?", (wid,)).fetchone()
        if w:
            conn.execute("UPDATE webhooks SET active=? WHERE id=?", (0 if w[0] else 1, wid))
            conn.commit()
    return redirect("/api_settings")

# ══════════════════════════════════════════════════════════════
# ██  PHASE 11 — Global Excellence                           ██
# ══════════════════════════════════════════════════════════════

# ─── 1. Multi-Succursale (Branches) ───

@app.route("/branches")
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

@app.route("/branch/add", methods=["POST"])
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

@app.route("/branch/toggle/<int:bid>")
@login_required
@admin_required
def toggle_branch(bid):
    with get_db() as conn:
        b = conn.execute("SELECT active FROM branches WHERE id=?", (bid,)).fetchone()
        if b:
            conn.execute("UPDATE branches SET active=? WHERE id=?", (0 if b[0] else 1, bid))
            conn.commit()
    return redirect("/branches")

@app.route("/branch/transfer", methods=["POST"])
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

# ─── 2. VIN Decoder ───

VIN_MANUFACTURERS = {
    '1': 'USA', '2': 'Canada', '3': 'Mexico', '4': 'USA', '5': 'USA',
    'J': 'Japan', 'K': 'Korea', 'L': 'China', 'S': 'UK', 'V': 'France',
    'W': 'Germany', 'Z': 'Italy', 'Y': 'Sweden/Finland',
}
VIN_MAKES = {
    'WBA': 'BMW', 'WBS': 'BMW M', 'WDD': 'Mercedes-Benz', 'WDB': 'Mercedes-Benz',
    'WAU': 'Audi', 'WVW': 'Volkswagen', 'WF0': 'Ford', 'VF1': 'Renault',
    'VF3': 'Peugeot', 'VF7': 'Citroën', 'ZFA': 'Fiat', 'ZAR': 'Alfa Romeo',
    'JTD': 'Toyota', 'JHM': 'Honda', 'JN1': 'Nissan', 'KMH': 'Hyundai',
    'KNA': 'Kia', 'SAJ': 'Jaguar', 'SAL': 'Land Rover', 'YV1': 'Volvo',
    'TMA': 'Hyundai CZ', '1G1': 'Chevrolet', '1FA': 'Ford', '2HG': 'Honda',
    '3FA': 'Ford', '5YJ': 'Tesla', 'SCC': 'Lotus',
}

def decode_vin_local(vin):
    """Decode VIN locally without external API"""
    vin = vin.upper().strip()
    if len(vin) != 17:
        return None
    result = {'vin': vin}
    wmi = vin[:3]
    result['make'] = VIN_MAKES.get(wmi, 'Inconnu')
    result['country'] = VIN_MANUFACTURERS.get(vin[0], 'Inconnu')
    year_code = vin[9]
    year_map = {c: y for c, y in zip('ABCDEFGHJKLMNPRSTVWXY123456789', range(2010, 2040))}
    result['year'] = str(year_map.get(year_code, ''))
    engine_code = vin[7]
    result['engine'] = f"Type-{engine_code}"
    body_code = vin[4]
    body_map = {'A': 'Berline', 'B': 'SUV', 'C': 'Coupé', 'D': 'Break', 'E': 'Cabriolet', 'F': 'Pick-up'}
    result['body'] = body_map.get(body_code, 'Standard')
    fuel_map = {'1': 'Essence', '2': 'Diesel', '3': 'Hybride', '4': 'Électrique', '5': 'GPL'}
    result['fuel'] = fuel_map.get(vin[7], 'Essence')
    return result

@app.route("/vin_decode", methods=["GET", "POST"])
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

# ─── 3. Timeline Client Unifiée ───

@app.route("/customer_timeline/<int:cid>")
@login_required
def customer_timeline(cid):
    with get_db() as conn:
        customer = conn.execute("SELECT * FROM customers WHERE id=?", (cid,)).fetchone()
        if not customer:
            flash("Client introuvable", "danger")
            return redirect("/customers")
        events = []
        # Appointments
        for a in conn.execute("SELECT * FROM appointments WHERE car_id IN (SELECT id FROM cars WHERE customer_id=?) ORDER BY date DESC", (cid,)).fetchall():
            events.append({'type': 'appointment', 'icon': '📅', 'title': f"RDV — {a['service']}", 
                'detail': f"Statut: {a['status']}", 'date': a['date'], 'ref_id': a['id']})
        # Invoices
        for i in conn.execute("SELECT * FROM invoices WHERE appointment_id IN (SELECT id FROM appointments WHERE car_id IN (SELECT id FROM cars WHERE customer_id=?))", (cid,)).fetchall():
            events.append({'type': 'invoice', 'icon': '📄', 'title': f"Facture #{i['id']} — {i['amount']} DT",
                'detail': f"Statut: {i['status']}", 'date': i.get('created_at', ''), 'ref_id': i['id']})
        # Communications
        for c in conn.execute("SELECT * FROM communication_log WHERE customer_id=? ORDER BY created_at DESC", (cid,)).fetchall():
            events.append({'type': 'communication', 'icon': '💬', 'title': f"{c['comm_type']} — {c['subject']}",
                'detail': c.get('message', '')[:100], 'date': c['created_at'], 'ref_id': c['id']})
        # CRM Follow-ups
        for f in conn.execute("SELECT * FROM crm_followups WHERE customer_id=?", (cid,)).fetchall():
            events.append({'type': 'followup', 'icon': '🔄', 'title': f"Suivi — {f['action_type']}",
                'detail': f['notes'][:100] if f['notes'] else '', 'date': f['scheduled_date'], 'ref_id': f['id']})
        # Ratings
        for r in conn.execute("SELECT * FROM ratings WHERE customer_id=?", (cid,)).fetchall():
            events.append({'type': 'rating', 'icon': '⭐', 'title': f"Évaluation — {r['score']}/5",
                'detail': r.get('comment', ''), 'date': r.get('created_at', ''), 'ref_id': r['id']})
        # Insurance claims
        for ic in conn.execute("SELECT * FROM insurance_claims WHERE customer_id=?", (cid,)).fetchall():
            events.append({'type': 'insurance', 'icon': '🏥', 'title': f"Dossier assurance #{ic['claim_number']}",
                'detail': f"Statut: {ic['status']} — {ic['estimated_cost']} DT", 'date': ic['created_at'], 'ref_id': ic['id']})
        # Sort by date desc
        events.sort(key=lambda x: x.get('date', '') or '', reverse=True)
        cars = conn.execute("SELECT * FROM cars WHERE customer_id=?", (cid,)).fetchall()
    return render_template("customer_timeline.html", customer=customer, events=events, cars=cars)

# ─── 4. Gestion Assurance & Tiers-Payant ───

@app.route("/insurance")
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

@app.route("/insurance/company/add", methods=["POST"])
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

@app.route("/insurance/claim/add", methods=["POST"])
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

@app.route("/insurance/claim/update/<int:cid>", methods=["POST"])
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

# ─── 5. Contrôle Qualité Post-Service ───

QUALITY_CHECKLIST = [
    {'id': 'clean_exterior', 'label': 'Propreté extérieure', 'category': 'Finition'},
    {'id': 'clean_interior', 'label': 'Propreté intérieure', 'category': 'Finition'},
    {'id': 'no_scratches', 'label': 'Aucune rayure ajoutée', 'category': 'Finition'},
    {'id': 'service_complete', 'label': 'Service complet effectué', 'category': 'Service'},
    {'id': 'parts_replaced', 'label': 'Pièces changées correctement', 'category': 'Service'},
    {'id': 'fluids_checked', 'label': 'Niveaux vérifiés', 'category': 'Service'},
    {'id': 'test_drive', 'label': 'Essai routier effectué', 'category': 'Vérification'},
    {'id': 'noise_check', 'label': 'Pas de bruits anormaux', 'category': 'Vérification'},
    {'id': 'lights_ok', 'label': 'Éclairage fonctionnel', 'category': 'Vérification'},
    {'id': 'customer_items', 'label': 'Objets client restitués', 'category': 'Remise'},
    {'id': 'paperwork', 'label': 'Documents prêts', 'category': 'Remise'},
    {'id': 'final_inspection', 'label': 'Inspection finale validée', 'category': 'Remise'},
]

@app.route("/quality_check/<int:appt_id>", methods=["GET", "POST"])
@login_required
def quality_check(appt_id):
    import json
    with get_db() as conn:
        appt = conn.execute("""SELECT a.*, c.plate, cu.name as customer_name 
            FROM appointments a JOIN cars c ON a.car_id=c.id 
            JOIN customers cu ON c.customer_id=cu.id WHERE a.id=?""", (appt_id,)).fetchone()
        if not appt:
            flash("RDV introuvable", "danger")
            return redirect("/appointments")
        existing = conn.execute("SELECT * FROM quality_checks WHERE appointment_id=?", (appt_id,)).fetchone()
        if request.method == "POST":
            checklist = []
            for item in QUALITY_CHECKLIST:
                status = request.form.get(f"check_{item['id']}", "pending")
                note = request.form.get(f"note_{item['id']}", "").strip()
                checklist.append({'id': item['id'], 'label': item['label'], 'category': item['category'],
                                'status': status, 'note': note})
            passed = sum(1 for c in checklist if c['status'] == 'pass')
            total = len(checklist)
            score = int((passed / total) * 100) if total else 0
            nps = request.form.get("nps_score", 0, type=int)
            nps_comment = request.form.get("nps_comment", "").strip()
            checklist_json = json.dumps(checklist)
            if existing:
                conn.execute("""UPDATE quality_checks SET checklist=?, overall_score=?, nps_score=?, 
                    nps_comment=?, status=? WHERE appointment_id=?""",
                    (checklist_json, score, nps, nps_comment, 'completed' if score >= 80 else 'needs_review', appt_id))
            else:
                conn.execute("""INSERT INTO quality_checks 
                    (appointment_id, inspector_id, checklist, overall_score, nps_score, nps_comment, status)
                    VALUES (?,?,?,?,?,?,?)""",
                    (appt_id, session.get('user_id', 0), checklist_json, score, nps, nps_comment,
                     'completed' if score >= 80 else 'needs_review'))
            conn.commit()
            flash(f"Contrôle qualité enregistré — Score: {score}%", "success")
            return redirect(f"/quality_check/{appt_id}")
        parsed_checklist = json.loads(existing['checklist']) if existing and existing['checklist'] else []
    return render_template("quality_check.html", appt=appt, existing=existing,
                          checklist=QUALITY_CHECKLIST, parsed=parsed_checklist)

@app.route("/quality_dashboard")
@login_required
def quality_dashboard():
    import json
    with get_db() as conn:
        checks = conn.execute("""SELECT qc.*, a.service, a.date, c.plate, cu.name as customer_name
            FROM quality_checks qc 
            JOIN appointments a ON qc.appointment_id=a.id 
            JOIN cars c ON a.car_id=c.id 
            JOIN customers cu ON c.customer_id=cu.id 
            ORDER BY qc.created_at DESC LIMIT 100""").fetchall()
        avg_score = conn.execute("SELECT AVG(overall_score) FROM quality_checks").fetchone()[0] or 0
        avg_nps = conn.execute("SELECT AVG(nps_score) FROM quality_checks WHERE nps_score > 0").fetchone()[0] or 0
        total = conn.execute("SELECT COUNT(*) FROM quality_checks").fetchone()[0]
        passed = conn.execute("SELECT COUNT(*) FROM quality_checks WHERE overall_score >= 80").fetchone()[0]
    return render_template("quality_dashboard.html", checks=checks, avg_score=avg_score, 
                          avg_nps=avg_nps, total=total, passed=passed)

# ─── 6. Gestion Documentaire Véhicule ───

@app.route("/vehicle_docs/<int:car_id>")
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

@app.route("/vehicle_docs/add/<int:car_id>", methods=["POST"])
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

@app.route("/vehicle_docs/delete/<int:doc_id>/<int:car_id>", methods=["POST"])
@login_required
def delete_vehicle_doc(doc_id, car_id):
    with get_db() as conn:
        conn.execute("DELETE FROM vehicle_documents WHERE id=?", (doc_id,))
        conn.commit()
    flash("Document supprimé", "success")
    return redirect(f"/vehicle_docs/{car_id}")

# ─── 7. Prévision Cash Flow ───

@app.route("/cashflow")
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
                AND strftime('%%Y-%%m', date) = ?""", (month_str,)).fetchone()[0]
            proj_income += unpaid
            # Actual income
            actual_income = conn.execute("""SELECT COALESCE(SUM(amount), 0) FROM invoices 
                WHERE status = 'paid' AND strftime('%%Y-%%m', date) = ?""", (month_str,)).fetchone()[0]
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

# ─── 8. Programme VIP & Niveaux ───

DEFAULT_VIP_LEVELS = [
    {'name': 'Bronze', 'min_spend': 0, 'discount': 0, 'perks': 'Newsletter exclusif', 'color': '#CD7F32', 'icon': '🥉'},
    {'name': 'Silver', 'min_spend': 500, 'discount': 5, 'perks': 'Lavage gratuit après 3 visites', 'color': '#C0C0C0', 'icon': '🥈'},
    {'name': 'Gold', 'min_spend': 2000, 'discount': 10, 'perks': 'Remise 10% + Priorité RDV', 'color': '#FFD700', 'icon': '🥇'},
    {'name': 'Platinum', 'min_spend': 5000, 'discount': 15, 'perks': 'Remise 15% + VIP Lounge + Véhicule courtoisie', 'color': '#E5E4E2', 'icon': '👑'},
]

@app.route("/vip_program")
@login_required
def vip_program():
    with get_db() as conn:
        levels = conn.execute("SELECT * FROM vip_levels ORDER BY min_spend ASC").fetchall()
        if not levels:
            for lv in DEFAULT_VIP_LEVELS:
                conn.execute("""INSERT INTO vip_levels (name, min_spend, discount_percent, perks, color, icon, sort_order)
                    VALUES (?,?,?,?,?,?,?)""", (lv['name'], lv['min_spend'], lv['discount'], lv['perks'], lv['color'], lv['icon'], DEFAULT_VIP_LEVELS.index(lv)))
            conn.commit()
            levels = conn.execute("SELECT * FROM vip_levels ORDER BY min_spend ASC").fetchall()
        # Calculate customer VIP levels
        customers = conn.execute("""SELECT cu.*, COALESCE(SUM(i.amount), 0) as total_spent 
            FROM customers cu 
            LEFT JOIN cars c ON c.customer_id = cu.id 
            LEFT JOIN appointments a ON a.car_id = c.id 
            LEFT JOIN invoices i ON i.appointment_id = a.id AND i.status = 'paid' 
            GROUP BY cu.id ORDER BY total_spent DESC""").fetchall()
        # Assign VIP levels
        vip_customers = []
        level_counts = {l['name']: 0 for l in levels}
        for cu in customers:
            spent = cu['total_spent']
            assigned = levels[0]
            for lv in levels:
                if spent >= lv['min_spend']:
                    assigned = lv
            level_counts[assigned['name']] = level_counts.get(assigned['name'], 0) + 1
            vip_customers.append({'customer': cu, 'level': assigned, 'spent': spent})
            # Update customer vip_level
            conn.execute("UPDATE customers SET vip_level=?, total_spent=? WHERE id=?", 
                        (assigned['name'], spent, cu['id']))
        conn.commit()
    return render_template("vip_program.html", levels=levels, vip_customers=vip_customers, level_counts=level_counts)

@app.route("/vip_level/edit", methods=["POST"])
@login_required
@admin_required
def edit_vip_level():
    lid = request.form.get("level_id", type=int)
    min_spend = request.form.get("min_spend", 0, type=float)
    discount = request.form.get("discount_percent", 0, type=float)
    perks = request.form.get("perks", "").strip()
    if lid:
        with get_db() as conn:
            conn.execute("UPDATE vip_levels SET min_spend=?, discount_percent=?, perks=? WHERE id=?",
                        (min_spend, discount, perks, lid))
            conn.commit()
        flash("Niveau mis à jour", "success")
    return redirect("/vip_program")

# ─── 9. Vue Mobile Technicien ───

@app.route("/tech_mobile")
@login_required
def tech_mobile():
    from datetime import date
    today = date.today().isoformat()
    user_id = session.get('user_id')
    with get_db() as conn:
        user = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        user_name = user['full_name'] or user['username'] if user else ''
        # Today's work orders
        orders = conn.execute("""SELECT a.*, c.plate, c.brand, c.model, cu.name as customer_name, cu.phone
            FROM appointments a 
            JOIN cars c ON a.car_id=c.id 
            JOIN customers cu ON c.customer_id=cu.id 
            WHERE a.date=? AND (a.assigned_to=? OR a.assigned_to=?)
            ORDER BY a.time ASC""", (today, user_name, str(user_id))).fetchall()
        # Time tracking
        time_entry = conn.execute("SELECT * FROM time_tracking WHERE user_id=? AND date=?", (user_id, today)).fetchone()
        # Stats
        completed_today = sum(1 for o in orders if o['status'] == 'completed')
        pending = sum(1 for o in orders if o['status'] == 'pending')
    return render_template("tech_mobile.html", orders=orders, user=user, time_entry=time_entry,
                          completed=completed_today, pending=pending, today=today)

@app.route("/tech_mobile/update_status/<int:appt_id>", methods=["POST"])
@login_required
def tech_update_status(appt_id):
    status = request.form.get("status", "")
    valid_statuses = ['pending', 'in_progress', 'completed', 'cancelled']
    if status in valid_statuses:
        with get_db() as conn:
            conn.execute("UPDATE appointments SET status=? WHERE id=?", (status, appt_id))
            conn.commit()
        flash(f"Statut mis à jour: {status}", "success")
    return redirect("/tech_mobile")

@app.route("/tech_mobile/clock", methods=["POST"])
@login_required
def tech_clock():
    from datetime import date, datetime
    action = request.form.get("action", "")
    user_id = session.get('user_id')
    today = date.today().isoformat()
    now = datetime.now().strftime("%H:%M")
    with get_db() as conn:
        entry = conn.execute("SELECT * FROM time_tracking WHERE user_id=? AND date=?", (user_id, today)).fetchone()
        if action == "clock_in":
            if not entry:
                conn.execute("INSERT INTO time_tracking (user_id, date, clock_in) VALUES (?,?,?)", (user_id, today, now))
            else:
                conn.execute("UPDATE time_tracking SET clock_in=? WHERE id=?", (now, entry['id']))
        elif action == "clock_out" and entry:
            conn.execute("UPDATE time_tracking SET clock_out=? WHERE id=?", (now, entry['id']))
        conn.commit()
    return redirect("/tech_mobile")

# ─── 10. Centre de Notifications Intelligent ───

@app.route("/notifications")
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

@app.route("/notifications/read/<int:nid>")
@login_required
def mark_notification_read(nid):
    with get_db() as conn:
        conn.execute("UPDATE notifications_center SET is_read=1 WHERE id=?", (nid,))
        conn.commit()
    link = request.args.get("redirect", "/notifications")
    return redirect(link)

@app.route("/notifications/read_all")
@login_required
def mark_all_notifications_read():
    user_id = session.get('user_id')
    with get_db() as conn:
        conn.execute("UPDATE notifications_center SET is_read=1 WHERE user_id=? OR user_id=0", (user_id,))
        conn.commit()
    return redirect("/notifications")

@app.route("/api/notifications/count")
@login_required
def api_notification_count():
    user_id = session.get('user_id')
    with get_db() as conn:
        count = conn.execute("""SELECT COUNT(*) FROM notifications_center 
            WHERE (user_id=? OR user_id=0) AND is_read=0""", (user_id,)).fetchone()[0]
    return jsonify({'count': count})

# ══════════════════════════════════════════════════════════════
# ██  PHASE 12 — Operational Intelligence                    ██
# ══════════════════════════════════════════════════════════════

# ─── 1. Système de Réclamations & Tickets ───

@app.route("/tickets")
@login_required
def tickets():
    from datetime import datetime
    with get_db() as conn:
        all_tickets = conn.execute("""SELECT t.*, cu.name as customer_name, cu.phone,
            u.full_name as assigned_name
            FROM tickets t 
            JOIN customers cu ON t.customer_id=cu.id 
            LEFT JOIN users u ON t.assigned_to=u.id
            ORDER BY CASE t.priority WHEN 'urgent' THEN 1 WHEN 'high' THEN 2 WHEN 'medium' THEN 3 ELSE 4 END, 
            t.created_at DESC""").fetchall()
        now = datetime.now().isoformat()
        stats = {
            'total': len(all_tickets),
            'open': sum(1 for t in all_tickets if t['status'] == 'open'),
            'in_progress': sum(1 for t in all_tickets if t['status'] == 'in_progress'),
            'resolved': sum(1 for t in all_tickets if t['status'] in ('resolved', 'closed')),
            'overdue': sum(1 for t in all_tickets if t['sla_deadline'] and t['sla_deadline'] < now and t['status'] not in ('resolved', 'closed')),
            'avg_satisfaction': 0,
        }
        sat = conn.execute("SELECT AVG(satisfaction_score) FROM tickets WHERE satisfaction_score > 0").fetchone()[0]
        stats['avg_satisfaction'] = sat or 0
        customers = conn.execute("SELECT id, name FROM customers ORDER BY name").fetchall()
        staff = conn.execute("SELECT id, full_name, username FROM users WHERE role IN ('admin','employee')").fetchall()
    return render_template("tickets.html", tickets=all_tickets, stats=stats, customers=customers, staff=staff)

@app.route("/ticket/add", methods=["POST"])
@login_required
def add_ticket():
    from datetime import datetime, timedelta
    customer_id = request.form.get("customer_id", type=int)
    subject = request.form.get("subject", "").strip()
    description = request.form.get("description", "").strip()
    category = request.form.get("category", "general")
    priority = request.form.get("priority", "medium")
    assigned_to = request.form.get("assigned_to", 0, type=int)
    sla_map = {'urgent': 24, 'high': 48, 'medium': 72, 'low': 120}
    sla_hours = sla_map.get(priority, 72)
    sla_deadline = (datetime.now() + timedelta(hours=sla_hours)).isoformat()
    if customer_id and subject:
        with get_db() as conn:
            conn.execute("""INSERT INTO tickets 
                (customer_id, subject, description, category, priority, sla_hours, sla_deadline, assigned_to)
                VALUES (?,?,?,?,?,?,?,?)""",
                (customer_id, subject, description, category, priority, sla_hours, sla_deadline, assigned_to))
            conn.commit()
        flash("Ticket créé !", "success")
    return redirect("/tickets")

@app.route("/ticket/<int:tid>")
@login_required
def view_ticket(tid):
    with get_db() as conn:
        ticket = conn.execute("""SELECT t.*, cu.name as customer_name, cu.phone, cu.email,
            u.full_name as assigned_name
            FROM tickets t JOIN customers cu ON t.customer_id=cu.id 
            LEFT JOIN users u ON t.assigned_to=u.id WHERE t.id=?""", (tid,)).fetchone()
        if not ticket:
            flash("Ticket introuvable", "danger")
            return redirect("/tickets")
        messages = conn.execute("""SELECT tm.*, 
            CASE WHEN tm.sender_type='staff' THEN u.full_name ELSE cu.name END as sender_name
            FROM ticket_messages tm 
            LEFT JOIN users u ON tm.sender_type='staff' AND tm.sender_id=u.id
            LEFT JOIN customers cu ON tm.sender_type='customer' AND tm.sender_id=cu.id
            WHERE tm.ticket_id=? ORDER BY tm.created_at ASC""", (tid,)).fetchall()
        staff = conn.execute("SELECT id, full_name, username FROM users WHERE role IN ('admin','employee')").fetchall()
    return render_template("ticket_detail.html", ticket=ticket, messages=messages, staff=staff)

@app.route("/ticket/<int:tid>/reply", methods=["POST"])
@login_required
def reply_ticket(tid):
    message = request.form.get("message", "").strip()
    if message:
        with get_db() as conn:
            conn.execute("INSERT INTO ticket_messages (ticket_id, sender_type, sender_id, message) VALUES (?,?,?,?)",
                        (tid, 'staff', session.get('user_id', 0), message))
            conn.execute("UPDATE tickets SET updated_at=CURRENT_TIMESTAMP WHERE id=?", (tid,))
            conn.commit()
    return redirect(f"/ticket/{tid}")

@app.route("/ticket/<int:tid>/update", methods=["POST"])
@login_required
def update_ticket(tid):
    from datetime import datetime
    status = request.form.get("status", "")
    assigned = request.form.get("assigned_to", 0, type=int)
    satisfaction = request.form.get("satisfaction_score", 0, type=int)
    resolution = request.form.get("resolution", "").strip()
    with get_db() as conn:
        updates = ["updated_at=CURRENT_TIMESTAMP"]
        params = []
        if status:
            updates.append("status=?"); params.append(status)
            if status in ('resolved', 'closed'):
                updates.append("closed_at=?"); params.append(datetime.now().isoformat())
        if assigned:
            updates.append("assigned_to=?"); params.append(assigned)
        if satisfaction:
            updates.append("satisfaction_score=?"); params.append(satisfaction)
        if resolution:
            updates.append("resolution=?"); params.append(resolution)
        params.append(tid)
        conn.execute(f"UPDATE tickets SET {','.join(updates)} WHERE id=?", params)
        conn.commit()
    flash("Ticket mis à jour", "success")
    return redirect(f"/ticket/{tid}")

# ─── 2. Benchmarking Inter-Succursales ───

@app.route("/branch_benchmark")
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

# ─── 3. Prédiction Churn Client ───

@app.route("/churn_prediction")
@login_required
@admin_required
def churn_prediction():
    from datetime import date, timedelta
    with get_db() as conn:
        today = date.today()
        customers = conn.execute("""SELECT cu.*, 
            MAX(a.date) as last_visit_date,
            COUNT(a.id) as visit_count,
            COALESCE(SUM(i.amount),0) as lifetime_value
            FROM customers cu 
            LEFT JOIN cars c ON c.customer_id=cu.id
            LEFT JOIN appointments a ON a.car_id=c.id AND a.status='completed'
            LEFT JOIN invoices i ON i.appointment_id=a.id AND i.status='paid'
            GROUP BY cu.id HAVING visit_count > 0
            ORDER BY last_visit_date ASC""").fetchall()
        predictions = []
        for cu in customers:
            last_visit = cu['last_visit_date'] or ''
            if not last_visit:
                continue
            try:
                last_dt = date.fromisoformat(last_visit)
            except (ValueError, TypeError, AttributeError):
                continue
            days_since = (today - last_dt).days
            visits = cu['visit_count']
            # Calculate average interval
            all_dates = conn.execute("""SELECT DISTINCT a.date FROM appointments a 
                JOIN cars c ON a.car_id=c.id WHERE c.customer_id=? AND a.status='completed' 
                ORDER BY a.date""", (cu['id'],)).fetchall()
            if len(all_dates) > 1:
                intervals = []
                for j in range(1, len(all_dates)):
                    try:
                        d1 = date.fromisoformat(all_dates[j-1]['date'])
                        d2 = date.fromisoformat(all_dates[j]['date'])
                        intervals.append((d2-d1).days)
                    except (ValueError, TypeError, AttributeError):
                        pass
                avg_interval = sum(intervals)/len(intervals) if intervals else 90
            else:
                avg_interval = 90
            # Risk score: higher = more likely to churn
            if avg_interval > 0:
                ratio = days_since / avg_interval
            else:
                ratio = days_since / 90
            if ratio >= 3:
                risk_score = min(100, 60 + ratio * 5)
                risk_level = 'critical'
            elif ratio >= 2:
                risk_score = 40 + ratio * 10
                risk_level = 'high'
            elif ratio >= 1.5:
                risk_score = 30 + ratio * 5
                risk_level = 'medium'
            else:
                risk_score = ratio * 20
                risk_level = 'low'
            risk_score = min(100, max(0, risk_score))
            predicted_churn = (last_dt + timedelta(days=int(avg_interval * 2.5))).isoformat()
            predictions.append({
                'customer': cu, 'days_since': days_since, 'visits': visits,
                'avg_interval': avg_interval, 'risk_score': risk_score,
                'risk_level': risk_level, 'predicted_churn': predicted_churn,
                'lifetime_value': cu['lifetime_value']
            })
            # Save
            existing = conn.execute("SELECT id FROM churn_predictions WHERE customer_id=?", (cu['id'],)).fetchone()
            if existing:
                conn.execute("""UPDATE churn_predictions SET risk_score=?, risk_level=?, 
                    days_since_last_visit=?, avg_visit_interval=?, predicted_churn_date=? WHERE customer_id=?""",
                    (risk_score, risk_level, days_since, avg_interval, predicted_churn, cu['id']))
            else:
                conn.execute("""INSERT INTO churn_predictions 
                    (customer_id, risk_score, risk_level, days_since_last_visit, avg_visit_interval, predicted_churn_date)
                    VALUES (?,?,?,?,?,?)""",
                    (cu['id'], risk_score, risk_level, days_since, avg_interval, predicted_churn))
            conn.execute("UPDATE customers SET churn_risk=?, last_churn_check=? WHERE id=?",
                        (risk_level, today.isoformat(), cu['id']))
        conn.commit()
        predictions.sort(key=lambda x: x['risk_score'], reverse=True)
    return render_template("churn_prediction.html", predictions=predictions)

# ─── 4. Portail Client 2.0 ───

@app.route("/client_portal/<token>")
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

@app.route("/client_portal/<token>/book", methods=["POST"])
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

# ─── 5. Journal Comptable & TVA ───

@app.route("/accounting")
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

@app.route("/contracts")
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

@app.route("/contract/add", methods=["POST"])
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

@app.route("/contract/use/<int:cid>", methods=["POST"])
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

# ─── 7. Capacity Planning ───

@app.route("/capacity")
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

# ─── 8. Chat Interne ───

@app.route("/team_chat")
@login_required
def team_chat():
    channel = request.args.get("channel", "general")
    with get_db() as conn:
        messages = conn.execute("""SELECT tm.*, u.full_name, u.username 
            FROM team_messages tm JOIN users u ON tm.sender_id=u.id 
            WHERE tm.channel=? ORDER BY tm.created_at DESC LIMIT 100""", (channel,)).fetchall()
        messages = list(reversed(messages))
        users = conn.execute("SELECT id, full_name, username FROM users ORDER BY full_name").fetchall()
        channels = ['general', 'technique', 'admin', 'urgent']
        # Mark as read
        conn.execute("""UPDATE team_messages SET is_read=1 
            WHERE channel=? AND recipient_id IN (0, ?)""", (channel, session.get('user_id', 0)))
        conn.commit()
    return render_template("team_chat.html", messages=messages, users=users,
                          channels=channels, current_channel=channel)

@app.route("/team_chat/send", methods=["POST"])
@login_required
def send_team_message():
    channel = request.form.get("channel", "general")
    message = request.form.get("message", "").strip()
    if message:
        with get_db() as conn:
            conn.execute("INSERT INTO team_messages (sender_id, channel, message) VALUES (?,?,?)",
                        (session.get('user_id', 0), channel, message))
            conn.commit()
    return redirect(f"/team_chat?channel={channel}")

# ─── 9. Tableau Comparatif Mensuel ───

@app.route("/monthly_comparison")
@login_required
@admin_required
def monthly_comparison_view():
    from datetime import date
    today = date.today()
    current_month = today.strftime("%Y-%m")
    if today.month == 1:
        prev_month = f"{today.year - 1}-12"
    else:
        prev_month = f"{today.year}-{today.month - 1:02d}"
    with get_db() as conn:
        def month_stats(m):
            revenue = conn.execute("SELECT COALESCE(SUM(amount),0) FROM invoices WHERE strftime('%%Y-%%m',date)=? AND status='paid'", (m,)).fetchone()[0]
            appts = conn.execute("SELECT COUNT(*) FROM appointments WHERE strftime('%%Y-%%m',date)=?", (m,)).fetchone()[0]
            completed = conn.execute("SELECT COUNT(*) FROM appointments WHERE strftime('%%Y-%%m',date)=? AND status='completed'", (m,)).fetchone()[0]
            new_customers = conn.execute("SELECT COUNT(*) FROM customers WHERE strftime('%%Y-%%m',created_at)=?", (m,)).fetchone()[0]
            expenses = conn.execute("SELECT COALESCE(SUM(amount),0) FROM expenses WHERE strftime('%%Y-%%m',date)=?", (m,)).fetchone()[0]
            avg_ticket = revenue / completed if completed else 0
            profit = revenue - expenses
            return {'month': m, 'revenue': revenue, 'appointments': appts, 'completed': completed,
                    'new_customers': new_customers, 'expenses': expenses, 'avg_ticket': avg_ticket, 'profit': profit}
        current = month_stats(current_month)
        previous = month_stats(prev_month)
        # Calculate deltas
        def delta(curr, prev):
            if prev == 0:
                return 100 if curr > 0 else 0
            return ((curr - prev) / prev) * 100
        deltas = {}
        for key in ['revenue', 'appointments', 'completed', 'new_customers', 'expenses', 'avg_ticket', 'profit']:
            deltas[key] = delta(current[key], previous[key])
        # Last 6 months for chart
        months_data = []
        for i in range(5, -1, -1):
            m = today.month - i
            y = today.year
            while m <= 0:
                m += 12; y -= 1
            ms = f"{y}-{m:02d}"
            months_data.append(month_stats(ms))
    return render_template("monthly_comparison.html", current=current, previous=previous,
                          deltas=deltas, months_data=months_data)

# ─── 10. Audit Trail Complet ───

def log_audit(action, entity_type='', entity_id=0, old_value='', new_value=''):
    """Helper to log audit entries"""
    try:
        with get_db() as conn:
            user_id = session.get('user_id', 0)
            username = session.get('username', 'system')
            ip = request.remote_addr if request else ''
            conn.execute("""INSERT INTO audit_log 
                (user_id, username, action, entity_type, entity_id, old_value, new_value, ip_address)
                VALUES (?,?,?,?,?,?,?,?)""",
                (user_id, username, action, entity_type, entity_id, 
                 str(old_value)[:500], str(new_value)[:500], ip))
            conn.commit()
    except (ValueError, TypeError, AttributeError, sqlite3.Error):
        pass

@app.route("/audit_trail")
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

# ══════════════════════════════════════════════════════════
# ═══  PHASE 13 — Premium Car Care Intelligence  ═══
# ══════════════════════════════════════════════════════════

# ─── 1. Galerie Avant/Après ───

@app.route("/gallery_global")
@login_required
def gallery_global():
    with get_db() as conn:
        photos = conn.execute("""SELECT g.*, c.plate, c.brand, c.model, c.vehicle_type
            FROM vehicle_gallery g JOIN cars c ON g.car_id = c.id
            ORDER BY g.created_at DESC LIMIT 100""").fetchall()
    return render_template("gallery_global.html", photos=photos)

@app.route("/gallery/upload", methods=["POST"])
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

# ─── 2. Support Motos — vehicle_type already in migration, update add_car ───

@app.route("/add_car_vehicle", methods=["POST"])
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

# ─── 3. Suivi Traitements & Garanties ───

@app.route("/treatments")
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

@app.route("/treatment/add", methods=["POST"])
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

@app.route("/vehicle_condition/<int:appointment_id>", methods=["GET", "POST"])
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

# ─── 5. Suivi Produits & Consommation ───

@app.route("/product_usage")
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

@app.route("/product_usage/add", methods=["POST"])
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

@app.route("/detailing_packs")
@login_required
def detailing_packs():
    with get_db() as conn:
        packs = conn.execute("SELECT * FROM detailing_packs ORDER BY vehicle_type, name").fetchall()
        services = conn.execute("SELECT id, name, price FROM services ORDER BY name").fetchall()
    return render_template("detailing_packs.html", packs=packs, services=services)

@app.route("/detailing_pack/add", methods=["POST"])
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

@app.route("/detailing_pack/toggle/<int:pid>")
@login_required
def detailing_pack_toggle(pid):
    with get_db() as conn:
        conn.execute("UPDATE detailing_packs SET is_active = CASE WHEN is_active=1 THEN 0 ELSE 1 END WHERE id=?", (pid,))
        conn.commit()
    return redirect("/detailing_packs")

# ─── 7. Abonnements Lavage ───

@app.route("/wash_subscriptions")
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

@app.route("/wash_subscription/add", methods=["POST"])
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

@app.route("/wash_subscription/use/<int:sid>", methods=["POST"])
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

@app.route("/portfolio")
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

@app.route("/client_reviews")
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

@app.route("/client_review/add", methods=["POST"])
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

@app.route("/client_review/respond/<int:rid>", methods=["POST"])
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

# ─── 10. Suivi Temps Réel ───

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

@app.route("/live_tracking")
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

@app.route("/live_tracking/start/<int:appointment_id>", methods=["POST"])
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

@app.route("/live_tracking/update/<int:vs_id>", methods=["POST"])
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

# ══════════════════════════════════════════════════════════
# ═══  PHASE 14 — Smart Car Care Automation  ═══
# ══════════════════════════════════════════════════════════

# ─── 1. Dashboard Car Care ───

@app.route("/care_dashboard")
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

# ─── 2. Moteur Upsell Intelligent ───

@app.route("/upsell_rules")
@login_required
@admin_required
def upsell_rules():
    with get_db() as conn:
        rules = conn.execute("SELECT * FROM upsell_rules ORDER BY created_at DESC").fetchall()
        services = conn.execute("SELECT DISTINCT name FROM services ORDER BY name").fetchall()
    return render_template("upsell_rules.html", rules=rules, services=services)

@app.route("/upsell_rule/add", methods=["POST"])
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

@app.route("/upsell_rule/toggle/<int:rid>")
@login_required
@admin_required
def upsell_rule_toggle(rid):
    with get_db() as conn:
        conn.execute("UPDATE upsell_rules SET is_active = CASE WHEN is_active=1 THEN 0 ELSE 1 END WHERE id=?", (rid,))
        conn.commit()
    return redirect("/upsell_rules")

def get_upsell_suggestions(car_id, service_name):
    """Get upsell suggestions for a car and service"""
    suggestions = []
    with get_db() as conn:
        car = conn.execute("SELECT * FROM cars WHERE id=?", (car_id,)).fetchone()
        if not car:
            return suggestions
        rules = conn.execute("SELECT * FROM upsell_rules WHERE is_active=1").fetchall()
        for rule in rules:
            match = False
            if rule['trigger_type'] == 'service_match' and rule['trigger_value'] in service_name:
                match = True
            elif rule['trigger_type'] == 'treatment_expired':
                expired = conn.execute(
                    "SELECT COUNT(*) FROM treatments WHERE car_id=? AND status='active' AND warranty_expiry <= date('now','+30 days') AND treatment_type LIKE ?",
                    (car_id, f"%{rule['trigger_value']}%")).fetchone()[0]
                if expired > 0:
                    match = True
            elif rule['trigger_type'] == 'no_treatment':
                has_treatment = conn.execute(
                    "SELECT COUNT(*) FROM treatments WHERE car_id=? AND status='active' AND treatment_type LIKE ?",
                    (car_id, f"%{rule['trigger_value']}%")).fetchone()[0]
                if has_treatment == 0:
                    match = True
            elif rule['trigger_type'] == 'days_since_last':
                from datetime import date, timedelta
                threshold = int(rule['trigger_value'] or 90)
                last_visit = conn.execute(
                    "SELECT MAX(date) FROM appointments WHERE car_id=? AND status='completed'",
                    (car_id,)).fetchone()[0]
                if last_visit:
                    days_diff = (date.today() - date.fromisoformat(last_visit)).days
                    if days_diff >= threshold:
                        match = True
            if match and (rule['vehicle_types'] == 'all' or (car['vehicle_type'] or 'voiture') in rule['vehicle_types']):
                suggestions.append(dict(rule))
                conn.execute("UPDATE upsell_rules SET times_shown=times_shown+1 WHERE id=?", (rule['id'],))
        conn.commit()
    return suggestions

# ─── 3. Réservation en Ligne Pro ───

@app.route("/booking_online")
def booking_online():
    with get_db() as conn:
        services = conn.execute("SELECT id, name, price FROM services WHERE price > 0 ORDER BY name").fetchall()
        packs = conn.execute("SELECT * FROM detailing_packs WHERE is_active=1 ORDER BY name").fetchall()
        shop = {}
        for r in conn.execute("SELECT key, value FROM settings").fetchall():
            shop[r['key']] = r['value']
    return render_template("booking_online.html", services=services, packs=packs, shop=shop)

@app.route("/booking_online/submit", methods=["POST"])
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

# ─── 4. QR Code Véhicule ───

@app.route("/vehicle_qr/<int:car_id>")
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

@app.route("/vehicle_history_public/<token>")
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

# ─── 5. Checklist Qualité par Service ───

@app.route("/service_checklists")
@login_required
@admin_required
def service_checklists_view():
    with get_db() as conn:
        checklists = conn.execute("SELECT * FROM service_checklists ORDER BY service_name").fetchall()
        services = conn.execute("SELECT DISTINCT name FROM services ORDER BY name").fetchall()
    return render_template("service_checklists.html", checklists=checklists, services=services)

@app.route("/service_checklist/add", methods=["POST"])
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

@app.route("/checklist/fill/<int:appointment_id>", methods=["GET", "POST"])
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

# ─── 6. Rentabilité par Type Véhicule ───

@app.route("/profitability_vehicle_type")
@login_required
@admin_required
def profitability_vehicle_type():
    month = request.args.get("month", "")
    from datetime import date
    if not month:
        month = date.today().strftime("%Y-%m")
    with get_db() as conn:
        data = conn.execute("""SELECT c.vehicle_type,
            COUNT(DISTINCT a.id) as appointments,
            COALESCE(SUM(CASE WHEN i.status='paid' THEN i.amount ELSE 0 END),0) as revenue,
            COALESCE(SUM(pu.total_cost),0) as product_cost,
            COUNT(DISTINCT c.id) as vehicles
            FROM appointments a
            JOIN cars c ON a.car_id=c.id
            LEFT JOIN invoices i ON i.appointment_id=a.id
            LEFT JOIN product_usage pu ON pu.appointment_id=a.id
            WHERE strftime('%%Y-%%m', a.date) = ?
            GROUP BY c.vehicle_type""", (month,)).fetchall()
        # Service breakdown by vehicle type
        service_data = conn.execute("""SELECT c.vehicle_type, a.service,
            COUNT(*) as cnt, COALESCE(SUM(CASE WHEN i.status='paid' THEN i.amount ELSE 0 END),0) as rev
            FROM appointments a JOIN cars c ON a.car_id=c.id LEFT JOIN invoices i ON i.appointment_id=a.id
            WHERE strftime('%%Y-%%m', a.date)=?
            GROUP BY c.vehicle_type, a.service ORDER BY rev DESC""", (month,)).fetchall()
    return render_template("profitability_vehicle_type.html", data=data, service_data=service_data, month=month)

# ─── 7. Rappels Automatiques Smart ───

@app.route("/smart_reminders")
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

@app.route("/smart_reminder/mark/<int:rid>/<action>")
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

@app.route("/pack_configurator")
def pack_configurator():
    with get_db() as conn:
        services = conn.execute("SELECT id, name, price FROM services WHERE price > 0 ORDER BY name").fetchall()
        shop = {}
        for r in conn.execute("SELECT key, value FROM settings").fetchall():
            shop[r['key']] = r['value']
    return render_template("pack_configurator.html", services=services, shop=shop)

@app.route("/pack_configurator/submit", methods=["POST"])
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

@app.route("/pack_configurations")
@login_required
def pack_configurations():
    with get_db() as conn:
        configs = conn.execute("SELECT * FROM pack_configurations ORDER BY created_at DESC").fetchall()
    return render_template("pack_configurations.html", configs=configs)

# ─── 9. Rapport Tendances Produits ───

@app.route("/product_trends")
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

# ─── 10. Historique Complet Véhicule ───

@app.route("/vehicle_full_history/<int:car_id>")
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

# ════════════════════════════════════════════════════════════════
# ═══  PHASE 15: Revenue Intelligence & Client Excellence  ═══
# ════════════════════════════════════════════════════════════════

# ─── 1. WhatsApp Business Hub ───

@app.route("/whatsapp_hub")
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

@app.route("/whatsapp_hub_send", methods=["POST"])
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

@app.route("/whatsapp_bulk", methods=["POST"])
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

# ─── 2. Tarification Dynamique Pro ───

@app.route("/dynamic_pricing_pro")
@login_required
def dynamic_pricing_pro():
    with get_db() as conn:
        rules = conn.execute("""SELECT dp.*, s.name as service_name FROM dynamic_pricing_rules dp
            LEFT JOIN services s ON dp.service_id=s.id ORDER BY dp.priority DESC, dp.created_at DESC""").fetchall()
        services = conn.execute("SELECT * FROM services ORDER BY name").fetchall()
        flash_sales = conn.execute("SELECT * FROM flash_sales ORDER BY created_at DESC LIMIT 20").fetchall()
    return render_template("dynamic_pricing.html", rules=rules, services=services, flash_sales=flash_sales)

@app.route("/dynamic_pricing_pro/add", methods=["POST"])
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

@app.route("/flash_sale/add", methods=["POST"])
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

# ─── 3. Gamification Employés ───

@app.route("/employee_gamification")
@login_required
def employee_gamification_view():
    from datetime import date
    month = request.args.get("month", date.today().strftime("%Y-%m"))
    with get_db() as conn:
        employees = conn.execute("SELECT * FROM users WHERE role IN ('employee','admin') ORDER BY full_name").fetchall()
        leaderboard = []
        for emp in employees:
            stats = conn.execute("""SELECT COUNT(*) as completed,
                COALESCE(SUM(i.amount), 0) as revenue
                FROM appointments a
                LEFT JOIN invoices i ON a.id = i.appointment_id AND i.status='paid'
                WHERE a.assigned_employee_id=? AND a.status='Terminé'
                AND strftime('%%Y-%%m', a.date)=?""", (emp['id'], month)).fetchone()
            avg_rating = conn.execute("""SELECT AVG(n.score) FROM nps_surveys n
                JOIN appointments a ON n.appointment_id=a.id
                WHERE a.assigned_employee_id=? AND strftime('%%Y-%%m', n.created_at)=?""",
                (emp['id'], month)).fetchone()[0] or 0
            timer_stats = conn.execute("""SELECT AVG(efficiency_pct) as avg_eff
                FROM service_timer WHERE employee_id=? AND strftime('%%Y-%%m', created_at)=?""",
                (emp['id'], month)).fetchone()
            efficiency = timer_stats['avg_eff'] if timer_stats and timer_stats['avg_eff'] else 0
            points = (stats['completed'] * 10) + int(stats['revenue'] / 100) + int(avg_rating * 5) + int(efficiency / 2)
            leaderboard.append({
                'id': emp['id'], 'name': emp['full_name'] or emp['username'], 'role': emp.get('role', ''),
                'completed': stats['completed'], 'revenue': stats['revenue'],
                'avg_rating': round(avg_rating, 1), 'efficiency': round(efficiency, 1),
                'points': points, 'badges': '',
                'commission_rate': 0,
                'commission': 0,
            })
        leaderboard.sort(key=lambda x: x['points'], reverse=True)
        for i, e in enumerate(leaderboard):
            e['rank'] = i + 1
    return render_template("employee_gamification.html", leaderboard=leaderboard, month=month)

@app.route("/employee_badge/<int:emp_id>", methods=["POST"])
@login_required
def employee_badge_add(emp_id):
    badge = request.form.get("badge", "")
    with get_db() as conn:
        emp = conn.execute("SELECT id FROM users WHERE id=?", (emp_id,)).fetchone()
        if emp:
            conn.commit()
    flash(f"Badge '{badge}' attribué !", "success")
    return redirect("/employee_gamification")

# ─── 4. NPS & Satisfaction ───

@app.route("/nps_dashboard")
@login_required
def nps_dashboard():
    with get_db() as conn:
        surveys = conn.execute("""SELECT n.*, c.name as customer_name, c.phone
            FROM nps_surveys n LEFT JOIN customers c ON n.customer_id=c.id
            ORDER BY n.created_at DESC LIMIT 100""").fetchall()
        total = len(surveys)
        if total > 0:
            promoters = sum(1 for s in surveys if s['score'] >= 9)
            passives = sum(1 for s in surveys if 7 <= s['score'] <= 8)
            detractors = sum(1 for s in surveys if s['score'] <= 6)
            nps = int(((promoters - detractors) / total) * 100)
            avg_score = sum(s['score'] for s in surveys) / total
        else:
            promoters = passives = detractors = 0
            nps = 0
            avg_score = 0
        monthly = conn.execute("""SELECT strftime('%%Y-%%m', created_at) as month,
            AVG(score) as avg_score, COUNT(*) as count FROM nps_surveys
            GROUP BY month ORDER BY month DESC LIMIT 6""").fetchall()
        alerts = conn.execute("""SELECT n.*, c.name as customer_name, c.phone
            FROM nps_surveys n LEFT JOIN customers c ON n.customer_id=c.id
            WHERE n.score <= 6 AND n.follow_up_status='none'
            ORDER BY n.created_at DESC""").fetchall()
    return render_template("nps_dashboard.html", surveys=surveys, nps=nps, avg_score=round(avg_score, 1),
        promoters=promoters, passives=passives, detractors=detractors, total=total,
        monthly=monthly, alerts=alerts)

@app.route("/nps_survey/add", methods=["POST"])
@login_required
def nps_survey_add():
    with get_db() as conn:
        customer_id = request.form.get("customer_id", 0, type=int)
        score = request.form.get("score", 0, type=int)
        feedback = request.form.get("feedback", "")
        appointment_id = request.form.get("appointment_id", 0, type=int)
        category = 'promoter' if score >= 9 else 'passive' if score >= 7 else 'detractor'
        conn.execute("""INSERT INTO nps_surveys (customer_id, appointment_id, score, category, feedback)
            VALUES (?,?,?,?,?)""", (customer_id, appointment_id, score, category, feedback))
        conn.execute("UPDATE customers SET nps_score=? WHERE id=?", (score, customer_id))
        conn.commit()
    flash("Enquête NPS enregistrée", "success")
    return redirect("/nps_dashboard")

@app.route("/nps_followup/<int:survey_id>", methods=["POST"])
@login_required
def nps_followup(survey_id):
    with get_db() as conn:
        conn.execute("UPDATE nps_surveys SET follow_up_status=?, follow_up_notes=? WHERE id=?",
            (request.form.get("status", "contacted"), request.form.get("notes", ""), survey_id))
        conn.commit()
    flash("Suivi mis à jour", "success")
    return redirect("/nps_dashboard")

# ─── 5. Portefeuille Client (Wallet) ───

@app.route("/wallet/<int:customer_id>")
@login_required
def wallet_view(customer_id):
    with get_db() as conn:
        customer = conn.execute("SELECT * FROM customers WHERE id=?", (customer_id,)).fetchone()
        if not customer:
            flash("Client non trouvé", "danger")
            return redirect("/customers")
        transactions = conn.execute("""SELECT * FROM wallet_transactions
            WHERE customer_id=? ORDER BY created_at DESC LIMIT 50""", (customer_id,)).fetchall()
        balance = customer['wallet_balance'] or 0 if customer['wallet_balance'] else 0
    return render_template("wallet.html", customer=customer, transactions=transactions, balance=balance)

@app.route("/wallet/topup", methods=["POST"])
@login_required
def wallet_topup():
    customer_id = request.form.get("customer_id", 0, type=int)
    amount = request.form.get("amount", 0, type=float)
    description = request.form.get("description", "Recharge manuelle")
    if amount <= 0:
        flash("Montant invalide", "danger")
        return redirect(f"/wallet/{customer_id}")
    with get_db() as conn:
        current = conn.execute("SELECT wallet_balance FROM customers WHERE id=?", (customer_id,)).fetchone()
        if not current:
            flash("Client non trouvé", "danger")
            return redirect("/customers")
        new_balance = (current['wallet_balance'] or 0) + amount
        conn.execute("UPDATE customers SET wallet_balance=? WHERE id=?", (new_balance, customer_id))
        conn.execute("""INSERT INTO wallet_transactions (customer_id, transaction_type, amount, balance_after, description, created_by)
            VALUES (?,?,?,?,?,?)""", (customer_id, 'topup', amount, new_balance, description, session.get('username', 'admin')))
        conn.commit()
    flash(f"+{amount} DH ajouté au portefeuille", "success")
    return redirect(f"/wallet/{customer_id}")

@app.route("/wallet/debit", methods=["POST"])
@login_required
def wallet_debit():
    customer_id = request.form.get("customer_id", 0, type=int)
    amount = request.form.get("amount", 0, type=float)
    description = request.form.get("description", "Paiement service")
    ref_type = request.form.get("reference_type", "")
    ref_id = request.form.get("reference_id", 0, type=int)
    with get_db() as conn:
        current = conn.execute("SELECT wallet_balance FROM customers WHERE id=?", (customer_id,)).fetchone()
        if not current:
            flash("Client non trouvé", "danger")
            return redirect("/customers")
        bal = current['wallet_balance'] or 0
        if amount > bal:
            flash("Solde insuffisant", "danger")
            return redirect(f"/wallet/{customer_id}")
        new_balance = bal - amount
        conn.execute("UPDATE customers SET wallet_balance=? WHERE id=?", (new_balance, customer_id))
        conn.execute("""INSERT INTO wallet_transactions (customer_id, transaction_type, amount, balance_after, description, reference_type, reference_id, created_by)
            VALUES (?,?,?,?,?,?,?,?)""", (customer_id, 'debit', -amount, new_balance, description, ref_type, ref_id, session.get('username', 'admin')))
        conn.commit()
    flash(f"-{amount} DH débité du portefeuille", "success")
    return redirect(f"/wallet/{customer_id}")

# ─── 6. Chronomètre de Service ───

@app.route("/service_timer/<int:appointment_id>")
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

@app.route("/service_timer/start", methods=["POST"])
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

@app.route("/service_timer/stop/<int:timer_id>", methods=["POST"])
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

@app.route("/efficiency_report")
@login_required
def efficiency_report():
    from datetime import date, timedelta
    month = request.args.get("month", date.today().strftime("%Y-%m"))
    with get_db() as conn:
        by_service = conn.execute("""SELECT service_name, COUNT(*) as count,
            AVG(estimated_minutes) as avg_est, AVG(actual_minutes) as avg_actual,
            AVG(efficiency_pct) as avg_eff
            FROM service_timer WHERE strftime('%%Y-%%m', created_at)=? AND actual_minutes > 0
            GROUP BY service_name ORDER BY avg_eff""", (month,)).fetchall()
        by_employee = conn.execute("""SELECT e.full_name as name, COUNT(*) as count,
            AVG(st.actual_minutes) as avg_time, AVG(st.efficiency_pct) as avg_eff
            FROM service_timer st LEFT JOIN users e ON st.employee_id=e.id
            WHERE strftime('%%Y-%%m', st.created_at)=? AND st.actual_minutes > 0
            GROUP BY st.employee_id ORDER BY avg_eff DESC""", (month,)).fetchall()
        bottlenecks = conn.execute("""SELECT service_name, employee_id, actual_minutes, estimated_minutes, efficiency_pct
            FROM service_timer WHERE efficiency_pct < 70 AND efficiency_pct > 0
            AND strftime('%%Y-%%m', created_at)=? ORDER BY efficiency_pct LIMIT 20""", (month,)).fetchall()
    return render_template("efficiency_report.html", by_service=by_service, by_employee=by_employee,
        bottlenecks=bottlenecks, month=month)

# ─── 7. Prévision Stock ───

@app.route("/stock_forecast")
@login_required
def stock_forecast():
    from datetime import date, timedelta
    with get_db() as conn:
        products = conn.execute("SELECT * FROM inventory ORDER BY name").fetchall()
        forecasts = []
        for p in products:
            usage_30d = conn.execute("""SELECT COALESCE(SUM(quantity_used), 0) FROM product_usage
                WHERE product_id=? AND DATE(created_at) >= DATE('now', '-30 days')""", (p['id'],)).fetchone()[0]
            daily_avg = usage_30d / 30 if usage_30d else 0
            stock = p.get('quantity', 0) or 0
            days_left = int(stock / daily_avg) if daily_avg > 0 else 999
            scheduled = conn.execute("""SELECT COUNT(*) FROM appointments
                WHERE date >= DATE('now') AND date <= DATE('now', '+7 days') AND status='pending'""").fetchone()[0]
            recommended = max(0, (daily_avg * 30) - stock)
            status = 'critical' if days_left <= 7 else 'warning' if days_left <= 14 else 'ok'
            forecasts.append({
                'id': p['id'], 'name': p['name'], 'stock': stock,
                'daily_avg': round(daily_avg, 2), 'days_left': days_left,
                'recommended': round(recommended, 1), 'status': status,
                'usage_30d': usage_30d, 'scheduled_appt': scheduled,
                'unit': p.get('unit', ''),
            })
        forecasts.sort(key=lambda x: x['days_left'])
    return render_template("stock_forecast.html", forecasts=forecasts)

# ─── 8. Objectifs & Budget Mensuel ───

@app.route("/monthly_goals_view")
@login_required
def monthly_goals_view():
    from datetime import date
    month = request.args.get("month", date.today().strftime("%Y-%m"))
    with get_db() as conn:
        goals = conn.execute("SELECT * FROM monthly_goals WHERE month=? ORDER BY goal_type", (month,)).fetchall()
        actuals = {}
        actuals['revenue'] = conn.execute("""SELECT COALESCE(SUM(amount), 0) FROM invoices
            WHERE status='paid' AND strftime('%%Y-%%m', created_at)=?""", (month,)).fetchone()[0]
        actuals['appointments'] = conn.execute("""SELECT COUNT(*) FROM appointments
            WHERE strftime('%%Y-%%m', date)=?""", (month,)).fetchone()[0]
        actuals['new_customers'] = conn.execute("""SELECT COUNT(*) FROM customers
            WHERE strftime('%%Y-%%m', last_visit)=?""", (month,)).fetchone()[0]
        actuals['avg_ticket'] = conn.execute("""SELECT AVG(amount) FROM invoices
            WHERE status='paid' AND strftime('%%Y-%%m', created_at)=?""", (month,)).fetchone()[0] or 0
    return render_template("monthly_goals.html", goals=goals, actuals=actuals, month=month)

@app.route("/monthly_goal/add", methods=["POST"])
@login_required
def monthly_goal_add():
    with get_db() as conn:
        conn.execute("""INSERT INTO monthly_goals (month, goal_type, target_value, unit, notes)
            VALUES (?,?,?,?,?)""",
            (request.form["month"], request.form["goal_type"],
             request.form.get("target_value", 0, type=float),
             request.form.get("unit", ""), request.form.get("notes", "")))
        conn.commit()
    flash("Objectif ajouté", "success")
    return redirect(f"/monthly_goals_view?month={request.form['month']}")

# ─── 9. Client PWA Espace ───

@app.route("/client_app")
def client_app():
    token = request.args.get("token", "")
    with get_db() as conn:
        shop = conn.execute("SELECT key, value FROM settings").fetchall()
        shop = {s['key']: s['value'] for s in shop}
    return render_template("client_app.html", shop=shop, token=token)

@app.route("/client_app/login", methods=["POST"])
def client_app_login():
    phone = request.form.get("phone", "").strip()
    with get_db() as conn:
        customer = conn.execute("SELECT * FROM customers WHERE phone=?", (phone,)).fetchone()
        if not customer:
            flash("Numéro non trouvé", "danger")
            return redirect("/client_app")
        session['client_id'] = customer['id']
        session['client_name'] = customer['name']
    return redirect("/client_app/dashboard")

@app.route("/client_app/dashboard")
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

# ─── 10. Fidélité Gamifiée ───

@app.route("/loyalty_gamified")
@login_required
def loyalty_gamified():
    with get_db() as conn:
        challenges = conn.execute("SELECT * FROM loyalty_challenges ORDER BY created_at DESC").fetchall()
        levels = conn.execute("""SELECT loyalty_level, COUNT(*) as count FROM customers
            WHERE loyalty_level != '' GROUP BY loyalty_level ORDER BY
            CASE loyalty_level WHEN 'platinum' THEN 1 WHEN 'gold' THEN 2
            WHEN 'silver' THEN 3 ELSE 4 END""").fetchall()
        top_loyal = conn.execute("""SELECT name, phone, loyalty_level, loyalty_points_total, wallet_balance
            FROM customers WHERE loyalty_points_total > 0
            ORDER BY loyalty_points_total DESC LIMIT 20""").fetchall()
    return render_template("loyalty_gamified.html", challenges=challenges, levels=levels, top_loyal=top_loyal)

@app.route("/loyalty_challenge/add", methods=["POST"])
@login_required
def loyalty_challenge_add():
    with get_db() as conn:
        conn.execute("""INSERT INTO loyalty_challenges
            (title, description, challenge_type, target_value, reward_points, reward_description, start_date, end_date, vehicle_types)
            VALUES (?,?,?,?,?,?,?,?,?)""",
            (request.form["title"], request.form.get("description", ""),
             request.form["challenge_type"], request.form.get("target_value", 0, type=float),
             request.form.get("reward_points", 0, type=int), request.form.get("reward_description", ""),
             request.form["start_date"], request.form["end_date"],
             request.form.get("vehicle_types", "all")))
        conn.commit()
    flash("Challenge créé !", "success")
    return redirect("/loyalty_gamified")

@app.route("/loyalty_level_update")
@login_required
def loyalty_level_update():
    with get_db() as conn:
        customers = conn.execute("SELECT id, loyalty_points_total FROM customers").fetchall()
        updated = 0
        for c in customers:
            pts = c['loyalty_points_total'] or 0
            if pts >= 5000:
                level = 'platinum'
            elif pts >= 2000:
                level = 'gold'
            elif pts >= 500:
                level = 'silver'
            else:
                level = 'bronze'
            conn.execute("UPDATE customers SET loyalty_level=? WHERE id=?", (level, c['id']))
            updated += 1
        conn.commit()
    flash(f"{updated} niveaux de fidélité mis à jour", "success")
    return redirect("/loyalty_gamified")

# ─── Phase 16: Operational Mastery & Smart Automation ───

# ── 1. Flash Sales Manager ──
@app.route('/flash_sales_manager')
@login_required
def flash_sales_manager():
    with get_db() as conn:
        sales = conn.execute("SELECT * FROM flash_sales ORDER BY created_at DESC").fetchall()
        services = conn.execute("SELECT id, name FROM services ORDER BY name").fetchall()
    now = datetime.now().strftime('%Y-%m-%dT%H:%M')
    return render_template('flash_sales_manager.html', sales=sales, services=services, now=now)

@app.route('/flash_sale/edit/<int:sale_id>', methods=['POST'])
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

@app.route('/flash_sale/toggle/<int:sale_id>')
@login_required
def flash_sale_toggle(sale_id):
    with get_db() as conn:
        sale = conn.execute("SELECT is_active FROM flash_sales WHERE id=?", (sale_id,)).fetchone()
        if sale:
            conn.execute("UPDATE flash_sales SET is_active=? WHERE id=?", (1 - sale['is_active'], sale_id))
            conn.commit()
    flash("Statut mis à jour", "success")
    return redirect("/flash_sales_manager")

@app.route('/flash_sale/delete/<int:sale_id>')
@login_required
def flash_sale_delete(sale_id):
    with get_db() as conn:
        conn.execute("DELETE FROM flash_sales WHERE id=?", (sale_id,))
        conn.commit()
    flash("Vente flash supprimée", "success")
    return redirect("/flash_sales_manager")

# ── 2. Revenue Heatmap ──
@app.route('/revenue_heatmap')
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
@app.route('/commission_tracker')
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

@app.route('/commission/generate', methods=['POST'])
@login_required
def commission_generate():
    month = request.form['month']
    with get_db() as conn:
        existing = conn.execute("SELECT COUNT(*) FROM commission_log WHERE month=?", (month,)).fetchone()[0]
        if existing > 0:
            flash("Commissions déjà générées pour ce mois", "warning")
            return redirect(f"/commission_tracker?month={month}")
        employees = conn.execute("SELECT id, full_name, commission_rate FROM users WHERE role != 'admin' AND commission_rate > 0").fetchall()
        for emp in employees:
            invoices = conn.execute("""
                SELECT i.id, i.total, a.service, a.id as appt_id
                FROM invoices i LEFT JOIN appointments a ON i.appointment_id = a.id
                WHERE a.assigned_to = ? AND i.date LIKE ? AND i.status != 'cancelled'
            """, (emp['full_name'], f"{month}%")).fetchall()
            for inv in invoices:
                commission = inv['total'] * (emp['commission_rate'] / 100)
                conn.execute("""INSERT INTO commission_log
                    (employee_id, employee_name, month, appointment_id, invoice_id,
                     service_name, invoice_total, commission_rate, commission_amount)
                    VALUES (?,?,?,?,?,?,?,?,?)""",
                    (emp['id'], emp['full_name'], month, inv['appt_id'] or 0, inv['id'],
                     inv['service'] or '', inv['total'], emp['commission_rate'], commission))
        conn.commit()
    flash("Commissions générées avec succès", "success")
    return redirect(f"/commission_tracker?month={month}")

@app.route('/commission/pay/<int:emp_id>', methods=['POST'])
@login_required
def commission_pay(emp_id):
    month = request.form['month']
    with get_db() as conn:
        conn.execute("UPDATE commission_log SET status='paid', paid_at=? WHERE employee_id=? AND month=? AND status='pending'",
                    (datetime.now().strftime('%Y-%m-%d %H:%M'), emp_id, month))
        conn.commit()
    flash("Commissions marquées comme payées", "success")
    return redirect(f"/commission_tracker?month={month}")

# ── 4. Campaign Analytics ──
@app.route('/campaign_analytics')
@login_required
def campaign_analytics():
    with get_db() as conn:
        campaigns = conn.execute("""
            SELECT mc.*, COUNT(cl.id) as message_count,
                   SUM(CASE WHEN cl.status='sent' THEN 1 ELSE 0 END) as sent_count,
                   SUM(CASE WHEN cl.status='opened' THEN 1 ELSE 0 END) as opened_count,
                   SUM(CASE WHEN cl.status='clicked' THEN 1 ELSE 0 END) as clicked_count
            FROM marketing_campaigns mc
            LEFT JOIN campaign_log cl ON mc.id = cl.campaign_id
            GROUP BY mc.id ORDER BY mc.created_at DESC
        """).fetchall()
        total_campaigns = len(campaigns)
        total_sent = sum(c['sent_count'] or 0 for c in campaigns)
        total_opened = sum(c['opened_count'] or 0 for c in campaigns)
        avg_open_rate = (total_opened / total_sent * 100) if total_sent > 0 else 0
        recent_logs = conn.execute("""
            SELECT cl.*, mc.name as campaign_name, c.name as customer_name
            FROM campaign_log cl
            LEFT JOIN marketing_campaigns mc ON cl.campaign_id = mc.id
            LEFT JOIN customers c ON cl.customer_id = c.id
            ORDER BY cl.sent_at DESC LIMIT 50
        """).fetchall()
    return render_template('campaign_analytics.html', campaigns=campaigns,
                          total_campaigns=total_campaigns, total_sent=total_sent,
                          avg_open_rate=avg_open_rate, recent_logs=recent_logs)

# ── 5. Multi-Channel Inbox ──
@app.route('/channel_inbox')
@login_required
def channel_inbox():
    channel = request.args.get('channel', 'all')
    status = request.args.get('status', 'all')
    with get_db() as conn:
        query = "SELECT * FROM channel_inbox WHERE 1=1"
        params = []
        if channel != 'all':
            query += " AND channel = ?"
            params.append(channel)
        if status != 'all':
            query += " AND status = ?"
            params.append(status)
        query += " ORDER BY created_at DESC LIMIT 200"
        messages = conn.execute(query, params).fetchall()
        stats = conn.execute("""
            SELECT channel, COUNT(*) as total,
                   SUM(CASE WHEN direction='outgoing' THEN 1 ELSE 0 END) as sent,
                   SUM(CASE WHEN direction='incoming' THEN 1 ELSE 0 END) as received,
                   SUM(CASE WHEN status='failed' THEN 1 ELSE 0 END) as failed
            FROM channel_inbox GROUP BY channel
        """).fetchall()
        # Sync existing logs into unified inbox
        existing_count = conn.execute("SELECT COUNT(*) FROM channel_inbox").fetchone()[0]
        if existing_count == 0:
            # Import from whatsapp_logs
            wa_logs = conn.execute("SELECT * FROM whatsapp_logs LIMIT 500").fetchall()
            for log in wa_logs:
                conn.execute("""INSERT INTO channel_inbox
                    (customer_id, customer_name, channel, direction, message, status, created_at)
                    VALUES (?,?,?,?,?,?,?)""",
                    (log['customer_id'], '', 'whatsapp', 'outgoing',
                     log['message_text'], log['status'], log['created_at']))
            # Import from email_log
            em_logs = conn.execute("SELECT * FROM email_log LIMIT 500").fetchall()
            for log in em_logs:
                conn.execute("""INSERT INTO channel_inbox
                    (customer_id, customer_name, channel, direction, message, status, created_at)
                    VALUES (?,?,?,?,?,?,?)""",
                    (log['customer_id'], '', 'email', 'outgoing',
                     log['subject'], log['status'], log['sent_at']))
            conn.commit()
            messages = conn.execute(query, params).fetchall()
            stats = conn.execute("""
                SELECT channel, COUNT(*) as total,
                       SUM(CASE WHEN direction='outgoing' THEN 1 ELSE 0 END) as sent,
                       SUM(CASE WHEN direction='incoming' THEN 1 ELSE 0 END) as received,
                       SUM(CASE WHEN status='failed' THEN 1 ELSE 0 END) as failed
                FROM channel_inbox GROUP BY channel
            """).fetchall()
    return render_template('channel_inbox.html', messages=messages, stats=stats,
                          channel=channel, status=status)

@app.route('/channel_inbox/send', methods=['POST'])
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
@app.route('/business_health')
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
@app.route('/smart_scheduling')
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

@app.route('/smart_scheduling/suggest', methods=['POST'])
@login_required
def smart_scheduling_suggest():
    date = request.form['date']
    service_id = int(request.form.get('service_id', 0))
    duration = int(request.form.get('duration', 60))
    with get_db() as conn:
        # Find busy times
        busy = conn.execute("""
            SELECT time, estimated_duration FROM appointments WHERE date = ? AND status != 'cancelled'
        """, (date,)).fetchall()
        busy_times = set()
        for b in busy:
            if b['time']:
                hour = int(b['time'].split(':')[0]) if ':' in b['time'] else 8
                dur = b['estimated_duration'] or 60
                for h in range(hour, min(hour + (dur // 60) + 1, 19)):
                    busy_times.add(h)
        # Suggest free slots
        suggestions = []
        for hour in range(8, 18):
            slots_needed = max(1, duration // 60)
            if all(h not in busy_times for h in range(hour, min(hour + slots_needed, 19))):
                suggestions.append({'time': f"{hour:02d}:00", 'score': 100 - len(busy_times) * 5})
        # Sort by score
        suggestions.sort(key=lambda x: x['score'], reverse=True)
    return jsonify({'suggestions': suggestions[:5]})

# ── 8. Data Import Center ──
@app.route('/import_center')
@login_required
def import_center():
    with get_db() as conn:
        history = conn.execute("SELECT * FROM import_history ORDER BY created_at DESC LIMIT 20").fetchall()
    return render_template('import_center.html', history=history)

@app.route('/import_center/upload', methods=['POST'])
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
@app.route('/report_builder')
@login_required
def report_builder():
    with get_db() as conn:
        reports = conn.execute("SELECT * FROM report_builder ORDER BY created_at DESC").fetchall()
    return render_template('report_builder.html', reports=reports)

@app.route('/report_builder/create', methods=['POST'])
@login_required
def report_builder_create():
    sections = request.form.getlist('sections')
    with get_db() as conn:
        conn.execute("""INSERT INTO report_builder (name, report_type, sections, schedule)
            VALUES (?,?,?,?)""",
            (request.form['name'], request.form['report_type'],
             ','.join(sections), request.form.get('schedule', '')))
        conn.commit()
    flash("Rapport créé avec succès", "success")
    return redirect("/report_builder")

@app.route('/report_builder/generate/<int:report_id>')
@login_required
def report_builder_generate(report_id):
    with get_db() as conn:
        report = conn.execute("SELECT * FROM report_builder WHERE id=?", (report_id,)).fetchone()
        if not report:
            flash("Rapport non trouvé", "danger")
            return redirect("/report_builder")
        sections = (report['sections'] or '').split(',')
        data = {}
        month_start = datetime.now().strftime('%Y-%m-01')
        today = datetime.now().strftime('%Y-%m-%d')

        if 'revenue' in sections:
            data['revenue'] = conn.execute("""
                SELECT COALESCE(SUM(total), 0) as total, COUNT(*) as count,
                       AVG(total) as avg_ticket
                FROM invoices WHERE date >= ? AND status != 'cancelled'
            """, (month_start,)).fetchone()
        if 'appointments' in sections:
            data['appointments'] = conn.execute("""
                SELECT COUNT(*) as total,
                       SUM(CASE WHEN status='completed' THEN 1 ELSE 0 END) as completed,
                       SUM(CASE WHEN status='cancelled' THEN 1 ELSE 0 END) as cancelled
                FROM appointments WHERE date >= ?
            """, (month_start,)).fetchone()
        if 'customers' in sections:
            data['customers'] = conn.execute("""
                SELECT COUNT(*) as total,
                       SUM(CASE WHEN created_at >= ? THEN 1 ELSE 0 END) as new_this_month
                FROM customers
            """, (month_start,)).fetchone()
        if 'services' in sections:
            data['services'] = conn.execute("""
                SELECT service, COUNT(*) as count, SUM(i.total) as revenue
                FROM appointments a LEFT JOIN invoices i ON a.id = i.appointment_id
                WHERE a.date >= ? GROUP BY a.service ORDER BY count DESC LIMIT 10
            """, (month_start,)).fetchall()
        if 'employees' in sections:
            data['employees'] = conn.execute("""
                SELECT assigned_to, COUNT(*) as count
                FROM appointments WHERE date >= ? AND assigned_to != ''
                GROUP BY assigned_to ORDER BY count DESC
            """, (month_start,)).fetchall()
        if 'inventory' in sections:
            data['inventory'] = conn.execute("""
                SELECT name, quantity, min_quantity FROM inventory
                WHERE quantity <= min_quantity ORDER BY quantity ASC LIMIT 10
            """).fetchall()

        conn.execute("UPDATE report_builder SET last_generated=? WHERE id=?", (today, report_id))
        conn.commit()

    # Generate PDF
    settings = {}
    with get_db() as conn:
        for s in conn.execute("SELECT key, value FROM settings").fetchall():
            settings[s['key']] = s['value']

    html = render_template('report_builder_pdf.html', report=report, data=data,
                          sections=sections, settings=settings,
                          generated_at=datetime.now().strftime('%d/%m/%Y %H:%M'))
    from xhtml2pdf import pisa
    pdf_buffer = io.BytesIO()
    pisa.CreatePDF(io.BytesIO(html.encode('utf-8')), dest=pdf_buffer)
    pdf_buffer.seek(0)
    return send_file(pdf_buffer, mimetype='application/pdf',
                    download_name=f"rapport_{report['name']}_{today}.pdf", as_attachment=True)

@app.route('/report_builder/delete/<int:report_id>')
@login_required
def report_builder_delete(report_id):
    with get_db() as conn:
        conn.execute("DELETE FROM report_builder WHERE id=?", (report_id,))
        conn.commit()
    flash("Rapport supprimé", "success")
    return redirect("/report_builder")

# ── 10. Customer 360 View ──
@app.route('/customer_360/<int:customer_id>')
@login_required
def customer_360(customer_id):
    with get_db() as conn:
        customer = conn.execute("SELECT * FROM customers WHERE id=?", (customer_id,)).fetchone()
        if not customer:
            flash("Client non trouvé", "danger")
            return redirect("/customers")
        cars = conn.execute("SELECT * FROM cars WHERE customer_id=?", (customer_id,)).fetchall()
        appointments = conn.execute("""
            SELECT a.*, c.brand, c.model, c.plate FROM appointments a
            LEFT JOIN cars c ON a.car_id = c.id
            WHERE a.customer_id=? ORDER BY a.date DESC LIMIT 20
        """, (customer_id,)).fetchall()
        invoices = conn.execute("""
            SELECT * FROM invoices WHERE customer_id=? ORDER BY date DESC LIMIT 20
        """, (customer_id,)).fetchall()
        total_spent = conn.execute(
            "SELECT COALESCE(SUM(total), 0) FROM invoices WHERE customer_id=? AND status != 'cancelled'",
            (customer_id,)).fetchone()[0]
        total_visits = conn.execute(
            "SELECT COUNT(*) FROM appointments WHERE customer_id=?", (customer_id,)).fetchone()[0]
        # Wallet
        wallet = conn.execute(
            "SELECT * FROM wallet_transactions WHERE customer_id=? ORDER BY created_at DESC LIMIT 10",
            (customer_id,)).fetchall()
        # NPS
        nps = conn.execute(
            "SELECT * FROM nps_surveys WHERE customer_id=? ORDER BY created_at DESC LIMIT 5",
            (customer_id,)).fetchall()
        # Communications
        comms = conn.execute(
            "SELECT * FROM channel_inbox WHERE customer_id=? ORDER BY created_at DESC LIMIT 10",
            (customer_id,)).fetchall()
        # Loyalty
        loyalty = conn.execute(
            "SELECT * FROM loyalty WHERE customer_id=?", (customer_id,)).fetchone()
        # Treatments
        treatments = conn.execute("""
            SELECT t.* FROM treatments t
            LEFT JOIN cars c ON t.car_id = c.id
            WHERE c.customer_id=? ORDER BY t.applied_date DESC LIMIT 10
        """, (customer_id,)).fetchall()
        # Timeline
        timeline = conn.execute(
            "SELECT * FROM customer_timeline WHERE customer_id=? ORDER BY created_at DESC LIMIT 20",
            (customer_id,)).fetchall()
        # Reviews
        reviews = conn.execute(
            "SELECT * FROM client_reviews WHERE customer_id=? ORDER BY created_at DESC LIMIT 5",
            (customer_id,)).fetchall()
        # Referrals
        referrals = conn.execute(
            "SELECT * FROM referrals WHERE referrer_id=? OR referred_id=? ORDER BY created_at DESC",
            (customer_id, customer_id)).fetchall()
    return render_template('customer_360.html', customer=customer, cars=cars,
                          appointments=appointments, invoices=invoices,
                          total_spent=total_spent, total_visits=total_visits,
                          wallet=wallet, nps=nps, comms=comms, loyalty=loyalty,
                          treatments=treatments, timeline=timeline, reviews=reviews,
                          referrals=referrals)

# ─── Phase 17: Enterprise Intelligence & Business Growth ───

# ── 1. Damage Claim Tracker ──
@app.route('/damage_claims')
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

@app.route('/damage_claim/add', methods=['POST'])
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

@app.route('/damage_claim/update/<int:claim_id>', methods=['POST'])
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
@app.route('/before_after/<int:appointment_id>')
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
@app.route('/revenue_forecast')
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
@app.route('/customer_segments')
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
@app.route('/service_cost_calculator')
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

@app.route('/service_cost/update', methods=['POST'])
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
@app.route('/appointment_waitlist')
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

@app.route('/waitlist/add', methods=['POST'])
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

@app.route('/waitlist/notify/<int:wid>')
@login_required
def waitlist_notify(wid):
    with get_db() as conn:
        conn.execute("UPDATE appointment_waitlist SET status='notified', notified_at=? WHERE id=?",
                    (datetime.now().strftime('%Y-%m-%d %H:%M'), wid))
        conn.commit()
    flash("Client notifié", "success")
    return redirect("/appointment_waitlist")

@app.route('/waitlist/convert/<int:wid>')
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

@app.route('/waitlist/remove/<int:wid>')
@login_required
def waitlist_remove(wid):
    with get_db() as conn:
        conn.execute("DELETE FROM appointment_waitlist WHERE id=?", (wid,))
        conn.commit()
    flash("Retiré de la liste d'attente", "success")
    return redirect("/appointment_waitlist")

# ── 7. Employee Attendance ──
@app.route('/employee_attendance')
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

@app.route('/attendance/record', methods=['POST'])
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
@app.route('/supplier_performance')
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

@app.route('/supplier_review/add', methods=['POST'])
@login_required
def supplier_review_add():
    delivery = int(request.form.get('delivery_rating', 5))
    quality = int(request.form.get('quality_rating', 5))
    price = int(request.form.get('price_rating', 5))
    overall = (delivery + quality + price) / 3
    with get_db() as conn:
        conn.execute("""INSERT INTO supplier_reviews
            (supplier_id, purchase_order_id, delivery_rating, quality_rating,
             price_rating, overall_rating, comment)
            VALUES (?,?,?,?,?,?,?)""",
            (int(request.form['supplier_id']),
             int(request.form.get('purchase_order_id', 0)),
             delivery, quality, price, overall,
             request.form.get('comment', '')))
        # Update supplier average
        sid = int(request.form['supplier_id'])
        avg = conn.execute("SELECT AVG(overall_rating) FROM supplier_reviews WHERE supplier_id=?",
                          (sid,)).fetchone()[0]
        conn.execute("UPDATE suppliers SET rating=? WHERE id=?", (avg or 0, sid))
        conn.commit()
    flash("Évaluation ajoutée", "success")
    return redirect("/supplier_performance")

# ── 9. Multi-Currency ──
@app.route('/multi_currency')
@login_required
def multi_currency():
    with get_db() as conn:
        rates = conn.execute("SELECT * FROM currency_rates ORDER BY currency_code").fetchall()
        if not rates:
            defaults = [
                ('EUR', 'Euro', 3.35), ('USD', 'Dollar US', 3.10),
                ('GBP', 'Livre Sterling', 3.95), ('SAR', 'Riyal Saoudien', 0.83),
                ('AED', 'Dirham EAU', 0.84), ('LYD', 'Dinar Libyen', 0.64),
                ('DZD', 'Dinar Algérien', 0.023), ('MAD', 'Dirham Marocain', 0.31)
            ]
            for code, name, rate in defaults:
                conn.execute("INSERT INTO currency_rates (currency_code, currency_name, rate_to_tnd) VALUES (?,?,?)",
                            (code, name, rate))
            conn.commit()
            rates = conn.execute("SELECT * FROM currency_rates ORDER BY currency_code").fetchall()
    return render_template('multi_currency.html', rates=rates)

@app.route('/currency/update', methods=['POST'])
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

@app.route('/currency/add', methods=['POST'])
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

@app.route('/api/convert_currency')
@login_required
def convert_currency_api():
    amount = float(request.args.get('amount', 0))
    from_curr = request.args.get('from', 'TND')
    to_curr = request.args.get('to', 'EUR')
    with get_db() as conn:
        if from_curr == 'TND':
            rate = conn.execute("SELECT rate_to_tnd FROM currency_rates WHERE currency_code=?",
                               (to_curr,)).fetchone()
            result = amount / rate['rate_to_tnd'] if rate and rate['rate_to_tnd'] > 0 else 0
        elif to_curr == 'TND':
            rate = conn.execute("SELECT rate_to_tnd FROM currency_rates WHERE currency_code=?",
                               (from_curr,)).fetchone()
            result = amount * rate['rate_to_tnd'] if rate else 0
        else:
            from_rate = conn.execute("SELECT rate_to_tnd FROM currency_rates WHERE currency_code=?",
                                    (from_curr,)).fetchone()
            to_rate = conn.execute("SELECT rate_to_tnd FROM currency_rates WHERE currency_code=?",
                                  (to_curr,)).fetchone()
            if from_rate and to_rate and to_rate['rate_to_tnd'] > 0:
                tnd = amount * from_rate['rate_to_tnd']
                result = tnd / to_rate['rate_to_tnd']
            else:
                result = 0
    return jsonify({'amount': amount, 'from': from_curr, 'to': to_curr, 'result': round(result, 2)})

# ── 10. Knowledge Base ──
@app.route('/knowledge_base')
@login_required
def knowledge_base():
    category = request.args.get('category', 'all')
    search = request.args.get('q', '')
    with get_db() as conn:
        query = "SELECT * FROM knowledge_base WHERE 1=1"
        params = []
        if category != 'all':
            query += " AND category = ?"
            params.append(category)
        if search:
            query += " AND (title LIKE ? OR content LIKE ? OR tags LIKE ?)"
            params.extend([f"%{search}%"] * 3)
        query += " ORDER BY is_pinned DESC, created_at DESC"
        articles = conn.execute(query, params).fetchall()
        categories = conn.execute("SELECT DISTINCT category FROM knowledge_base ORDER BY category").fetchall()
    return render_template('knowledge_base.html', articles=articles,
                          categories=categories, category=category, search=search)

@app.route('/knowledge_base/add', methods=['POST'])
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

@app.route('/knowledge_base/view/<int:article_id>')
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

@app.route('/knowledge_base/edit/<int:article_id>', methods=['POST'])
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

@app.route('/knowledge_base/delete/<int:article_id>')
@login_required
def knowledge_base_delete(article_id):
    with get_db() as conn:
        conn.execute("DELETE FROM knowledge_base WHERE id=?", (article_id,))
        conn.commit()
    flash("Article supprimé", "success")
    return redirect("/knowledge_base")

# ═══════════════════════════════════════════════════════════════
# ═══  CLIENT PORTAL — Professional Mobile PWA  ═══
# ═══════════════════════════════════════════════════════════════

def client_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('client_id'):
            return redirect('/espace-client')
        return f(*args, **kwargs)
    return decorated

@app.route("/espace-client")
def espace_client():
    if session.get('client_id'):
        return redirect('/espace-client/accueil')
    with get_db() as conn:
        shop = dict(conn.execute("SELECT key, value FROM settings").fetchall() or [])
    return render_template("client_login.html", shop=shop)

@app.route("/espace-client/connexion", methods=["POST"])
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

@app.route("/espace-client/deconnexion")
def espace_client_logout():
    session.pop('client_id', None)
    session.pop('client_name', None)
    session.pop('client_phone', None)
    return redirect("/espace-client")

@app.route("/espace-client/accueil")
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

@app.route("/espace-client/vehicules")
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

@app.route("/espace-client/rendez-vous")
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

@app.route("/espace-client/reserver", methods=["GET", "POST"])
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

@app.route("/espace-client/factures")
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

@app.route("/espace-client/fidelite")
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

@app.route("/espace-client/suivi/<int:appointment_id>")
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

# ═══════════════════════════════════════════════════════════════════════════════
# ─── PHASE 18: EXPERT IMPROVEMENTS (10 features) ─────────────────────────────
# ═══════════════════════════════════════════════════════════════════════════════

# ─── 18.1 WhatsApp Auto-Notify on Status Change ─────────────────────────────

def _build_wa_status_url(phone, message):
    """Build wa.me URL for status notification."""
    import urllib.parse
    phone = phone.strip().replace(' ', '').replace('-', '')
    if phone.startswith('0'):
        phone = '216' + phone[1:]
    elif not phone.startswith('+') and not phone.startswith('216'):
        phone = '216' + phone
    phone = phone.replace('+', '')
    return f"https://wa.me/{phone}?text={urllib.parse.quote(message)}"

STATUS_MESSAGES = {
    'in_progress': "🔧 Bonjour {name}, votre véhicule ({car}) est maintenant en cours de traitement chez {shop}. Service : {service}.",
    'completed': "✅ Bonjour {name}, votre véhicule ({car}) est prêt ! Vous pouvez passer le récupérer chez {shop}. Merci de votre confiance !",
    'cancelled': "ℹ️ Bonjour {name}, votre RDV du {date} chez {shop} a été annulé. N'hésitez pas à nous recontacter pour reprogrammer.",
}

# ─── 18.2 End of Day Report ──────────────────────────────────────────────────

@app.route("/end_of_day")
@login_required
def end_of_day_report():
    from datetime import date as d, timedelta
    day = request.args.get("date", str(d.today()))
    with get_db() as conn:
        appointments = conn.execute("""SELECT a.id, a.service, a.status, a.time, 
            c.name as customer_name, car.brand, car.model, car.plate
            FROM appointments a
            LEFT JOIN cars car ON a.car_id=car.id
            LEFT JOIN customers c ON car.customer_id=c.id
            WHERE a.date=? ORDER BY a.time""", (day,)).fetchall()
        
        revenue_paid = conn.execute("""SELECT COALESCE(SUM(i.amount),0) FROM invoices i
            JOIN appointments a ON i.appointment_id=a.id
            WHERE a.date=? AND i.status='paid'""", (day,)).fetchone()[0]
        revenue_unpaid = conn.execute("""SELECT COALESCE(SUM(i.amount),0) FROM invoices i
            JOIN appointments a ON i.appointment_id=a.id
            WHERE a.date=? AND i.status='unpaid'""", (day,)).fetchone()[0]
        expenses = conn.execute("SELECT COALESCE(SUM(amount),0) FROM expenses WHERE date=?", (day,)).fetchone()[0]
        
        stats = {
            'total_appointments': len(appointments),
            'completed': sum(1 for a in appointments if a['status'] == 'completed'),
            'in_progress': sum(1 for a in appointments if a['status'] == 'in_progress'),
            'pending': sum(1 for a in appointments if a['status'] == 'pending'),
            'cancelled': sum(1 for a in appointments if a['status'] == 'cancelled'),
            'revenue_paid': revenue_paid,
            'revenue_unpaid': revenue_unpaid,
            'expenses': expenses,
            'profit': revenue_paid - expenses,
        }
        
        by_service = conn.execute("""SELECT a.service, COUNT(*) as count,
            COALESCE(SUM(CASE WHEN i.status='paid' THEN i.amount ELSE 0 END),0) as revenue
            FROM appointments a LEFT JOIN invoices i ON a.id=i.appointment_id
            WHERE a.date=? GROUP BY a.service ORDER BY revenue DESC""", (day,)).fetchall()
        
        by_employee = conn.execute("""SELECT COALESCE(u.full_name, u.username, 'Non assigné') as name,
            COUNT(*) as count, SUM(CASE WHEN a.status='completed' THEN 1 ELSE 0 END) as completed
            FROM appointments a LEFT JOIN users u ON a.assigned_employee_id=u.id
            WHERE a.date=? GROUP BY a.assigned_employee_id""", (day,)).fetchall()
        
        payment_methods = conn.execute("""SELECT COALESCE(i.payment_method,'cash') as method,
            COUNT(*) as count, SUM(i.amount) as total
            FROM invoices i JOIN appointments a ON i.appointment_id=a.id
            WHERE a.date=? AND i.status='paid' GROUP BY i.payment_method""", (day,)).fetchall()
        
        shop = get_all_settings()
    return render_template("end_of_day.html", appointments=appointments, stats=stats,
                          by_service=by_service, by_employee=by_employee, 
                          payment_methods=payment_methods, day=day, shop=shop)


# ─── 18.3 Quick POS Mode ────────────────────────────────────────────────────

@app.route("/pos")
@login_required
def pos_view():
    with get_db() as conn:
        services = conn.execute("SELECT id, name, price FROM services WHERE active=1 ORDER BY name").fetchall()
        customers = conn.execute("SELECT id, name, phone FROM customers ORDER BY name").fetchall()
        cars = conn.execute("SELECT id, plate, brand, model, customer_id FROM cars ORDER BY plate").fetchall()
    return render_template("pos.html", services=services, customers=customers, cars=cars)

@app.route("/pos/checkout", methods=["POST"])
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


# ─── 18.4 Protection Renewal Reminders ──────────────────────────────────────

@app.route("/protection_renewals")
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

@app.route("/protection_renewal/remind/<int:appt_id>")
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


# ─── 18.5 Split Payment Support ─────────────────────────────────────────────
# (Integrated into POS above + modify pay_invoice to support split)

@app.route("/pay_invoice_split/<int:invoice_id>", methods=["POST"])
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


# ─── 18.6 Daily Technician Work Summary ─────────────────────────────────────

@app.route("/tech_summary")
@login_required
def tech_daily_summary():
    from datetime import date as d
    day = request.args.get("date", str(d.today()))
    with get_db() as conn:
        employees = conn.execute("SELECT id, full_name, username FROM users WHERE role IN ('employee','admin') ORDER BY full_name").fetchall()
        summaries = []
        for emp in employees:
            tasks = conn.execute("""SELECT a.service, a.status, a.time, 
                c.name as customer_name, car.brand, car.model, car.plate,
                COALESCE(i.amount,0) as revenue
                FROM appointments a
                LEFT JOIN cars car ON a.car_id=car.id
                LEFT JOIN customers c ON car.customer_id=c.id
                LEFT JOIN invoices i ON a.id=i.appointment_id
                WHERE a.date=? AND a.assigned_employee_id=?
                ORDER BY a.time""", (day, emp['id'])).fetchall()
            
            completed = sum(1 for t in tasks if t['status'] == 'completed')
            total_revenue = sum(t['revenue'] for t in tasks if t['status'] == 'completed')
            
            timer = conn.execute("""SELECT COUNT(*) as count, 
                COALESCE(AVG(efficiency_pct),0) as avg_eff,
                COALESCE(SUM(actual_minutes),0) as total_minutes
                FROM service_timer WHERE employee_id=? AND date(created_at)=?""",
                (emp['id'], day)).fetchone()
            
            summaries.append({
                'employee': emp,
                'tasks': tasks,
                'completed': completed,
                'total_tasks': len(tasks),
                'revenue': total_revenue,
                'avg_efficiency': round(timer['avg_eff'], 1) if timer else 0,
                'total_minutes': timer['total_minutes'] if timer else 0,
            })
    return render_template("tech_summary.html", summaries=summaries, day=day)


# ─── 18.7 Material Cost Calculator ──────────────────────────────────────────

@app.route("/cost_calculator")
@login_required 
def cost_calculator():
    with get_db() as conn:
        services = conn.execute("""SELECT s.id, s.name, s.price,
            GROUP_CONCAT(si.service_name || ':' || si.quantity_used || ':' || COALESCE(inv.unit_cost,0), '|') as materials
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

@app.route("/subscription_cards")
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


# ─── 18.9 Professional PDF Quote ────────────────────────────────────────────

@app.route("/quote_pdf/<int:quote_id>")
@login_required
def quote_pdf(quote_id):
    with get_db() as conn:
        quote = conn.execute("SELECT * FROM quotes WHERE id=?", (quote_id,)).fetchone()
        if not quote:
            flash("Devis introuvable", "danger")
            return redirect("/quotes")
        shop = get_all_settings()
    return render_template("quote_pdf.html", quote=quote, shop=shop)

@app.route("/quote_whatsapp/<int:quote_id>")
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


# ─── 18.10 Waiting Room TV Display ──────────────────────────────────────────

@app.route("/tv_display")
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


if __name__ == '__main__':
    app.run(debug=False, host='0.0.0.0', port=5000)

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