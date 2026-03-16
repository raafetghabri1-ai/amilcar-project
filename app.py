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
from flask import Flask, jsonify, session, request
from flask_socketio import SocketIO
from database.db import create_tables, get_db
from database.migrations import migrate
from helpers import check_api_rate_limit, TRANSLATIONS, csrf
import os
import time as time_module

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
socketio = SocketIO(app, cors_allowed_origins="*")
create_tables()
migrate()  # Apply pending database migrations

# ─── Security Configuration ───
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['PERMANENT_SESSION_LIFETIME'] = 86400  # 24h
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max upload

# ─── Security Headers ───
@app.after_request
def set_security_headers(response):
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'SAMEORIGIN'
    response.headers['X-XSS-Protection'] = '1; mode=block'
    response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
    return response

# ─── API Rate Limiting ───
@app.before_request
def api_rate_limiter():
    if request.path.startswith('/api/'):
        if check_api_rate_limit():
            return jsonify({'error': 'Rate limit exceeded. Try again later.'}), 429

# ─── Initialize Admin User ───
def init_admin():
    from werkzeug.security import generate_password_hash
    with get_db() as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY, username TEXT UNIQUE, password TEXT, role TEXT DEFAULT 'employee', full_name TEXT)")
        admin = conn.execute("SELECT id FROM users WHERE username = 'admin'").fetchone()
        if not admin:
            conn.execute("INSERT INTO users (username, password, role) VALUES (?, ?, ?)",
                ('admin', generate_password_hash('admin123'), 'admin'))
            conn.commit()
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

if __name__ == '__main__':
    # Development only — production uses gunicorn (see Procfile)
    import os
    port = int(os.environ.get('PORT', 5000))
    debug = os.environ.get('FLASK_ENV') == 'development'
    socketio.run(app, debug=debug, host='0.0.0.0', port=port,
                 allow_unsafe_werkzeug=debug)
