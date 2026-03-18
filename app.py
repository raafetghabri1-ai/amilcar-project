"""
AMILCAR Auto Care — Main Application
=====================================
Flask application with Blueprint-based route organization.
Restructured from monolithic app.py (11K+ lines) into 14 focused modules.

Blueprint Structure:
  routes/auth.py          — Authentication & User Management
  routes/main.py          — Main Pages (index, add forms, delete, etc.)
  routes/customers.py     — Customer Management & CRM
  routes/appointments.py  — Appointments & Scheduling
  routes/invoices.py      — Invoices, Payments & Billing
  routes/vehicles.py      — Vehicle Management & Gallery
  routes/inventory.py     — Inventory & Stock Management
  routes/communications.py — WhatsApp, SMS, Email & Notifications
  routes/reports.py       — Reports, KPIs & Dashboards
  routes/settings_admin.py — Settings & Administration
  routes/team.py          — Team, Employees & Performance
  routes/operations.py    — Operations, Care & Services
  routes/client_portal.py — Client Portal (PWA)
  routes/api.py           — API Endpoints
"""
from flask import Flask, jsonify, session, request, render_template
from flask_socketio import SocketIO
from database.db import create_tables, get_db
from database.migrations import migrate
from helpers import check_api_rate_limit, TRANSLATIONS, csrf
import os
import time as time_module
from datetime import datetime, date, timedelta

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
csrf.init_app(app)

# ── SocketIO: restrict CORS in production ──
_cors_origins = os.environ.get('CORS_ORIGINS', '*')
if _cors_origins != '*':
    _cors_origins = [o.strip() for o in _cors_origins.split(',')]
socketio = SocketIO(app, cors_allowed_origins=_cors_origins)
create_tables()
migrate()  # Apply pending database migrations

# ─── Security Configuration ───
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['SESSION_COOKIE_SECURE'] = os.environ.get('FLASK_ENV') == 'production'
app.config['PERMANENT_SESSION_LIFETIME'] = 86400  # 24h
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max upload

# ─── Security Headers + Performance ───
@app.after_request
def set_security_headers(response):
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'SAMEORIGIN'
    response.headers['X-XSS-Protection'] = '1; mode=block'
    response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
    response.headers['Permissions-Policy'] = 'camera=(), microphone=(), geolocation=()'
    # Content Security Policy
    if not request.path.startswith('/static/'):
        response.headers['Content-Security-Policy'] = (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline' 'unsafe-eval' https://cdn.jsdelivr.net https://cdn.socket.io https://cdn.chart.js; "
            "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://fonts.googleapis.com; "
            "font-src 'self' https://fonts.gstatic.com; "
            "img-src 'self' data: blob:; "
            "connect-src 'self' wss: ws:; "
            "frame-ancestors 'self'"
        )
    # Static file caching (CSS, JS, images: 7 days)
    if request.path.startswith('/static/'):
        response.headers['Cache-Control'] = 'public, max-age=604800'
    # Gzip compression for text responses (only if client supports it)
    if (response.content_type and
        'gzip' in request.headers.get('Accept-Encoding', '') and
        any(ct in response.content_type for ct in ('text/', 'application/json', 'application/javascript')) and
        'Content-Encoding' not in response.headers and
        response.content_length and response.content_length > 500):
        import gzip
        data = response.get_data()
        compressed = gzip.compress(data, compresslevel=6)
        if len(compressed) < len(data):
            response.set_data(compressed)
            response.headers['Content-Encoding'] = 'gzip'
            response.headers['Content-Length'] = len(compressed)
            response.headers['Vary'] = 'Accept-Encoding'
    return response

# ─── API Rate Limiting ───
@app.before_request
def api_rate_limiter():
    if request.path.startswith('/api/'):
        if check_api_rate_limit():
            return jsonify({'error': 'Rate limit exceeded. Try again later.'}), 429

# ─── Initialize Admin User ───
def init_admin():
    import secrets
    from werkzeug.security import generate_password_hash
    with get_db() as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY, username TEXT UNIQUE, password TEXT, role TEXT DEFAULT 'employee', full_name TEXT)")
        admin = conn.execute("SELECT id FROM users WHERE username = 'admin'").fetchone()
        if not admin:
            init_pw = secrets.token_urlsafe(12)
            conn.execute("INSERT INTO users (username, password, role) VALUES (?, ?, ?)",
                ('admin', generate_password_hash(init_pw), 'admin'))
            conn.commit()
            pw_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.admin_init_pw')
            with open(pw_file, 'w') as f:
                f.write(f'Admin initial password: {init_pw}\nChange it immediately after first login.\n')
            os.chmod(pw_file, 0o600)
            print(f'\n⚠️  Admin initial password saved to .admin_init_pw — change it immediately!\n')
        else:
            conn.execute("UPDATE users SET role = 'admin' WHERE username = 'admin' AND (role IS NULL OR role = 'employee')")
            conn.commit()

init_admin()

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

# ─── Context Processor for Language ───
@app.context_processor
def inject_translations():
    lang = session.get('lang', 'fr')
    return {'t': TRANSLATIONS.get(lang, TRANSLATIONS['fr']), 'current_lang': lang}

# ═══════════════════════════════════════════════════════════════
# ─── Register Blueprints ──────────────────────────────────────
# ═══════════════════════════════════════════════════════════════

from routes.auth import auth_bp
from routes.main import main_bp
from routes.customers import customers_bp
from routes.appointments import appointments_bp
from routes.invoices import invoices_bp
from routes.vehicles import vehicles_bp
from routes.inventory import inventory_bp
from routes.communications import comms_bp
from routes.reports import reports_bp
from routes.settings_admin import admin_bp
from routes.team import team_bp
from routes.operations import ops_bp
from routes.client_portal import portal_bp
from routes.api import api_bp

app.register_blueprint(auth_bp)
app.register_blueprint(main_bp)
app.register_blueprint(customers_bp)
app.register_blueprint(appointments_bp)
app.register_blueprint(invoices_bp)
app.register_blueprint(vehicles_bp)
app.register_blueprint(inventory_bp)
app.register_blueprint(comms_bp)
app.register_blueprint(reports_bp)
app.register_blueprint(admin_bp)
app.register_blueprint(team_bp)
app.register_blueprint(ops_bp)
app.register_blueprint(portal_bp)
app.register_blueprint(api_bp)

# ─── Error Handlers ───
@app.errorhandler(401)
def unauthorized(e):
    return render_template('error.html', code=401, message="Accès non autorisé — veuillez vous connecter"), 401

@app.errorhandler(403)
def forbidden(e):
    return render_template('error.html', code=403, message="Accès interdit — permissions insuffisantes"), 403

@app.errorhandler(404)
def page_not_found(e):
    return render_template('error.html', code=404, message="Page introuvable"), 404

@app.errorhandler(429)
def too_many_requests(e):
    return render_template('error.html', code=429, message="Trop de requêtes — réessayez dans quelques minutes"), 429

@app.errorhandler(500)
def internal_error(e):
    return render_template('error.html', code=500, message="Erreur interne du serveur"), 500

# ─── Health Check ───
@app.route('/health')
def health_check():
    try:
        with get_db() as conn:
            conn.execute('SELECT 1').fetchone()
        return jsonify({'status': 'healthy', 'db': 'ok'}), 200
    except Exception as e:
        return jsonify({'status': 'unhealthy', 'error': str(e)}), 503

# ─── WebSocket Events ───
from flask_socketio import emit

@socketio.on('connect')
def handle_connect():
    pass

@socketio.on('join_dashboard')
def handle_join_dashboard():
    from flask_socketio import join_room
    join_room('dashboard')

def notify_update(event_type, data=None):
    """Broadcast real-time update to dashboard clients."""
    socketio.emit('update', {'type': event_type, 'data': data or {}}, room='dashboard')

# Make notify_update available to blueprints
app.config['notify_update'] = notify_update

# ─── Auto Backup Scheduler ───
def _run_daily_backup():
    """Background thread: runs daily backup + Telegram send if enabled."""
    import threading, logging
    _log = logging.getLogger('amilcar.backup')
    while True:
        time_module.sleep(86400)  # 24 hours
        try:
            with app.app_context():
                db = get_db()
                auto = db.execute("SELECT value FROM settings WHERE key='auto_backup'").fetchone()
                if auto and auto[0] == '1':
                    from routes.settings_admin import _perform_backup, _send_telegram_backup
                    fname = _perform_backup()
                    if fname:
                        _log.info('Auto-backup created: %s', fname)
                        tg_auto = db.execute("SELECT value FROM settings WHERE key='telegram_auto_backup'").fetchone()
                        if tg_auto and tg_auto[0] == '1':
                            backup_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'backups')
                            _send_telegram_backup(os.path.join(backup_dir, fname), fname)
                            _log.info('Telegram backup sent: %s', fname)
                db.close()
        except Exception as e:
            _log.error('Auto-backup failed: %s', e)

import threading
_backup_thread = threading.Thread(target=_run_daily_backup, daemon=True)
_backup_thread.start()

# ─── Auto Appointment Reminders ───
def _run_appointment_reminders():
    """Background thread: send WhatsApp reminders for tomorrow's appointments at 8 PM daily."""
    import urllib.request, urllib.parse, urllib.error
    while True:
        # Calculate seconds until 20:00 today (or tomorrow if past 20:00)
        now = datetime.now()
        target = now.replace(hour=20, minute=0, second=0, microsecond=0)
        if now >= target:
            target += timedelta(days=1)
        wait_seconds = (target - now).total_seconds()
        time_module.sleep(wait_seconds)

        try:
            with app.app_context():
                db = get_db()
                # Check if reminders are enabled
                auto_remind = db.execute("SELECT value FROM settings WHERE key='wa_auto_remind'").fetchone()
                if not auto_remind or auto_remind[0] != '1':
                    db.close()
                    continue
                wa_phone = db.execute("SELECT value FROM settings WHERE key='wa_callmebot_phone'").fetchone()
                wa_key = db.execute("SELECT value FROM settings WHERE key='wa_callmebot_apikey'").fetchone()
                if not wa_phone or not wa_phone[0] or not wa_key or not wa_key[0]:
                    db.close()
                    continue
                phone = wa_phone[0].strip()
                apikey = wa_key[0].strip()
                shop = db.execute("SELECT value FROM settings WHERE key='shop_name'").fetchone()
                shop_name = shop[0] if shop and shop[0] else 'AMILCAR'

                tomorrow = (date.today() + timedelta(days=1)).isoformat()
                appts = db.execute(
                    "SELECT a.id, cu.name, a.service, COALESCE(a.time, ''), ca.brand, ca.model "
                    "FROM appointments a JOIN cars ca ON a.car_id=ca.id "
                    "JOIN customers cu ON ca.customer_id=cu.id "
                    "WHERE a.date=? AND a.status='pending'", (tomorrow,)).fetchall()

                if appts:
                    msg = f"📋 *Rappel — {len(appts)} RDV demain*\n"
                    for a in appts[:10]:
                        time_str = f" {a[3]}" if a[3] else ""
                        msg += f"\n• {a[1]} — {a[2]}{time_str} ({a[4]} {a[5]})"
                    if len(appts) > 10:
                        msg += f"\n... et {len(appts)-10} autres"

                    url = (f"https://api.callmebot.com/whatsapp.php"
                           f"?phone={urllib.parse.quote(phone)}"
                           f"&text={urllib.parse.quote(msg)}"
                           f"&apikey={urllib.parse.quote(apikey)}")
                    urllib.request.urlopen(url, timeout=15)
                db.close()
        except Exception as e:
            import logging
            logging.getLogger('amilcar.reminders').error('Reminder failed: %s', e)

_reminder_thread = threading.Thread(target=_run_appointment_reminders, daemon=True)
_reminder_thread.start()

# ─── Auto Email Reminders (Background) ───
def _run_email_reminders():
    """Background thread: send email reminders for tomorrow's appointments at 19:00 daily."""
    while True:
        now = datetime.now()
        target = now.replace(hour=19, minute=0, second=0, microsecond=0)
        if now >= target:
            target += timedelta(days=1)
        wait_seconds = (target - now).total_seconds()
        time_module.sleep(wait_seconds)
        try:
            with app.app_context():
                from helpers_email import send_email, build_reminder_email, get_setting_value
                if get_setting_value('email_auto_remind', '0') != '1':
                    continue
                shop = get_setting_value('shop_name', 'AMILCAR')
                tomorrow = (date.today() + timedelta(days=1)).isoformat()
                db = get_db()
                appts = db.execute(
                    "SELECT a.id, a.date, COALESCE(a.time,''), a.service, cu.name, "
                    "COALESCE(cu.email,''), ca.brand, ca.model, cu.id "
                    "FROM appointments a JOIN cars ca ON a.car_id=ca.id "
                    "JOIN customers cu ON ca.customer_id=cu.id "
                    "WHERE a.date=? AND a.status='pending'", (tomorrow,)).fetchall()
                db.close()
                sent = 0
                for a in appts:
                    email = a[5]
                    if not email:
                        continue
                    html = build_reminder_email({
                        'customer_name': a[4], 'date': a[1], 'time': a[2],
                        'service': a[3], 'car': f"{a[6]} {a[7]}"
                    }, shop)
                    if send_email(email, f'Rappel RDV — {shop}', html, customer_id=a[8]):
                        sent += 1
                if sent:
                    logging.getLogger('amilcar.email').info('Auto-email reminders: %d sent for %s', sent, tomorrow)
        except Exception as e:
            logging.getLogger('amilcar.email').error('Email reminder failed: %s', e)

_email_reminder_thread = threading.Thread(target=_run_email_reminders, daemon=True)
_email_reminder_thread.start()

if __name__ == '__main__':
    # Development only — production uses gunicorn (see Procfile)
    import os
    port = int(os.environ.get('PORT', 5000))
    debug = os.environ.get('FLASK_ENV') == 'development'
    socketio.run(app, debug=debug, host='0.0.0.0', port=port,
                 allow_unsafe_werkzeug=debug)
