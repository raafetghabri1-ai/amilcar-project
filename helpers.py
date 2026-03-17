"""
AMILCAR — Shared Helpers, Decorators, and Utilities
Used by all Blueprint modules.
"""
from flask import session, redirect, flash, request
from flask_wtf.csrf import CSRFProtect
from functools import wraps
from database.db import get_db
import os
import time as time_module

# ─── CSRF instance (initialized in app_new.py) ───
csrf = CSRFProtect()

# ─── Simple In-Memory Cache ───
class SimpleCache:
    """Thread-safe TTL cache for frequently accessed data."""
    def __init__(self):
        self._store = {}

    def get(self, key):
        if key in self._store:
            value, expires = self._store[key]
            if time_module.time() < expires:
                return value
            del self._store[key]
        return None

    def set(self, key, value, ttl=60):
        self._store[key] = (value, time_module.time() + ttl)

    def delete(self, key):
        self._store.pop(key, None)

    def clear(self):
        self._store.clear()

    def invalidate_prefix(self, prefix):
        keys = [k for k in self._store if k.startswith(prefix)]
        for k in keys:
            del self._store[k]

cache = SimpleCache()

# ─── Constants ───
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

STATUS_MESSAGES = {
    'in_progress': "🔧 Bonjour {name}, votre véhicule ({car}) est maintenant en cours de traitement chez {shop}. Service : {service}.",
    'completed': "✅ Bonjour {name}, votre véhicule ({car}) est prêt ! Vous pouvez passer le récupérer chez {shop}. Merci de votre confiance !",
    'cancelled': "ℹ️ Bonjour {name}, votre RDV du {date} chez {shop} a été annulé. N'hésitez pas à nous recontacter pour reprogrammer.",
}

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

# ─── Rate Limiting ───
_login_attempts = {}
LOGIN_MAX_ATTEMPTS = 5
LOGIN_LOCKOUT_SECONDS = 300

_api_rate = {}
API_RATE_LIMIT = 60
API_RATE_WINDOW = 60

_booking_rate = {}
BOOKING_RATE_LIMIT = 5
BOOKING_RATE_WINDOW = 300

def check_booking_rate_limit():
    """Returns True if booking rate limit exceeded (5 per 5 min per IP)."""
    ip = request.remote_addr
    now = time_module.time()
    if ip in _booking_rate:
        count, window_start = _booking_rate[ip]
        if now - window_start > BOOKING_RATE_WINDOW:
            _booking_rate[ip] = (1, now)
            return False
        if count >= BOOKING_RATE_LIMIT:
            return True
        _booking_rate[ip] = (count + 1, window_start)
    else:
        _booking_rate[ip] = (1, now)
    return False

# ─── Authentication Decorators ───

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

def client_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('client_id'):
            return redirect('/espace-client')
        return f(*args, **kwargs)
    return decorated

# ─── Utility Functions ───

def get_services():
    cached = cache.get('services')
    if cached is not None:
        return cached
    try:
        with get_db() as conn:
            rows = conn.execute("SELECT name, price FROM services WHERE active = 1 ORDER BY id").fetchall()
        result = [(r[0], r[1]) for r in rows] if rows else SERVICES_FALLBACK
        cache.set('services', result, ttl=300)
        return result
    except Exception:
        return SERVICES_FALLBACK

def get_setting(key, default=''):
    cached = cache.get(f'setting:{key}')
    if cached is not None:
        return cached
    try:
        with get_db() as conn:
            row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        value = row[0] if row else default
        cache.set(f'setting:{key}', value, ttl=300)
        return value
    except Exception:
        return default

def get_all_settings():
    cached = cache.get('all_settings')
    if cached is not None:
        return cached
    try:
        with get_db() as conn:
            rows = conn.execute("SELECT key, value FROM settings").fetchall()
        result = {r[0]: r[1] for r in rows}
        cache.set('all_settings', result, ttl=300)
        return result
    except Exception:
        return {}

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def safe_page(page):
    return max(1, min(page, 10000))

def log_activity(action, detail=''):
    with get_db() as conn:
        conn.execute("INSERT INTO activity_log (user_id, username, action, detail) VALUES (?,?,?,?)",
            (session.get('user_id'), session.get('username', ''), action, detail))
        conn.commit()

def build_wa_url(phone, message):
    """Build wa.me URL for WhatsApp messaging."""
    import urllib.parse
    phone = phone.strip().replace(' ', '').replace('-', '')
    if phone.startswith('0'):
        phone = '216' + phone[1:]
    elif not phone.startswith('+') and not phone.startswith('216'):
        phone = '216' + phone
    phone = phone.replace('+', '')
    return f"https://wa.me/{phone}?text={urllib.parse.quote(message)}"

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

def invalidate_cache(*prefixes):
    """Invalidate cache entries. Call after modifying settings/services."""
    if not prefixes:
        cache.clear()
    else:
        for p in prefixes:
            cache.invalidate_prefix(p)

def paginate_query(conn, query, params=(), page=1, per_page=PER_PAGE):
    """Execute a paginated query and return (rows, total, pages)."""
    page = safe_page(page)
    count_query = f"SELECT COUNT(*) FROM ({query})"
    total = conn.execute(count_query, params).fetchone()[0]
    pages = max(1, (total + per_page - 1) // per_page)
    page = min(page, pages)
    offset = (page - 1) * per_page
    rows = conn.execute(f"{query} LIMIT ? OFFSET ?", (*params, per_page, offset)).fetchall()
    return rows, total, pages, page
