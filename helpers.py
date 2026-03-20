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
        # Navigation
        'dashboard': 'Tableau de Bord', 'customers': 'Clients', 'appointments': 'Rendez-vous',
        'invoices': 'Factures', 'calendar': 'Calendrier', 'expenses': 'Dépenses',
        'today': "Aujourd'hui", 'monthly': 'Mensuel', 'reports': 'Rapports',
        'settings': 'Paramètres', 'search': 'Rechercher...', 'logout': 'Déconnexion',
        # Actions
        'add': 'Ajouter', 'edit': 'Modifier', 'delete': 'Supprimer', 'save': 'Enregistrer',
        'cancel': 'Annuler', 'confirm': 'Confirmer', 'back': 'Retour', 'print': 'Imprimer',
        'export': 'Exporter', 'import': 'Importer', 'filter': 'Filtrer', 'reset': 'Réinitialiser',
        # Fields
        'name': 'Nom', 'phone': 'Téléphone', 'date': 'Date', 'time': 'Heure',
        'status': 'Statut', 'amount': 'Montant', 'service': 'Service', 'actions': 'Actions',
        'email': 'Email', 'address': 'Adresse', 'notes': 'Notes', 'description': 'Description',
        'price': 'Prix', 'quantity': 'Quantité', 'discount': 'Remise', 'tax': 'TVA',
        # Statuses
        'paid': 'Payée', 'unpaid': 'Non payée', 'partial': 'Partielle',
        'confirmed': 'Confirmé', 'pending': 'En attente', 'completed': 'Terminé',
        'cancelled': 'Annulé', 'in_progress': 'En cours',
        # Dashboard
        'total': 'Total', 'welcome': 'Bienvenue', 'language': 'Langue',
        'overview': "Vue d'ensemble de votre activité",
        'revenue': 'Revenus', 'profit': 'Bénéfice Net', 'net_profit': 'Bénéfice Net',
        'pending_quotes': 'Devis en Attente', 'unpaid_invoices': 'Factures Impayées',
        'tomorrow': 'Demain', 'this_week': 'Cette Semaine',
        'week_revenue': 'Revenus Semaine', 'today_revenue': "Aujourd'hui",
        'best_customer': 'Meilleur Client', 'most_visited_car': 'Voiture la Plus Fréquente',
        'payment_rate': 'Taux de Paiement', 'visits': 'visites',
        'online_bookings': 'Réservations en Ligne', 'see_bookings': 'Voir les Réservations',
        'tomorrow_appointments': 'Rendez-vous de Demain',
        'pending_appointments': 'Rendez-vous en Attente',
        # Quick actions
        'new_customer': '+ Nouveau Client', 'new_appointment': '+ Nouveau RDV',
        'new_invoice': '+ Nouvelle Facture', 'see_quotes': 'Voir les Devis',
        # Charts
        'weekly_revenue': 'Revenus cette Semaine', 'monthly_comparison': 'Comparaison 12 Mois',
        'forecast': 'Prévisions Fin de Mois', 'revenue_vs_expenses': 'Revenus vs Dépenses — 6 Mois',
        'services_chart': 'Services', 'expenses_by_category': 'Dépenses par Catégorie',
        'forecast_revenue': 'REVENUS PRÉVUS', 'forecast_expenses': 'DÉPENSES PRÉVUES',
        'estimated_profit': 'BÉNÉFICE ESTIMÉ', 'daily_avg': 'Moy. journalière',
        'days_remaining': 'Jours restants',
        # Sidebar sections
        'principal': 'PRINCIPAL', 'car_moto_care': 'CAR & MOTO CARE',
        'clients_crm': 'CLIENTS & CRM', 'planning': 'PLANNING',
        'intelligence': 'INTELLIGENCE', 'billing': 'FACTURATION',
        'stock': 'STOCK & ACHATS', 'reports_kpi': 'RAPPORTS & KPI',
        'team': 'ÉQUIPE', 'quick_access': 'ACCÈS RAPIDE',
        # Common
        'client': 'Client', 'car': 'Voiture', 'brand': 'Marque', 'model': 'Modèle',
        'plate': 'Immatriculation', 'loading': 'Chargement...', 'no_results': 'Aucun résultat',
        'page': 'Page', 'of': 'de', 'showing': 'Affichage', 'results': 'résultats',
        'yes': 'Oui', 'no': 'Non', 'all': 'Tous', 'none': 'Aucun',
        'created': 'Créé le', 'updated': 'Modifié le',
        'password': 'Mot de passe', 'theme': 'Thème',
        # Settings
        'general_settings': 'Paramètres Généraux', 'backup': 'Sauvegarde',
        'save_settings': 'ENREGISTRER', 'backup_now': 'SAUVEGARDER MAINTENANT',
        'download_db': 'TÉLÉCHARGER LA BASE',
        'auto_daily_backup': 'SAUVEGARDE AUTOMATIQUE QUOTIDIENNE',
        'keep_days': 'CONSERVER (JOURS)',
        'existing_backups': 'SAUVEGARDES EXISTANTES',
        # Telegram
        'telegram_backup': 'TELEGRAM CLOUD BACKUP',
        'telegram_test': 'TEST', 'telegram_send_now': 'ENVOYER MAINTENANT',
        'telegram_auto': 'Envoi automatique quotidien sur Telegram',
    },
    'ar': {
        # Navigation
        'dashboard': 'لوحة التحكم', 'customers': 'العملاء', 'appointments': 'المواعيد',
        'invoices': 'الفواتير', 'calendar': 'التقويم', 'expenses': 'المصاريف',
        'today': 'اليوم', 'monthly': 'الشهري', 'reports': 'التقارير',
        'settings': 'الإعدادات', 'search': 'بحث...', 'logout': 'تسجيل الخروج',
        # Actions
        'add': 'إضافة', 'edit': 'تعديل', 'delete': 'حذف', 'save': 'حفظ',
        'cancel': 'إلغاء', 'confirm': 'تأكيد', 'back': 'رجوع', 'print': 'طباعة',
        'export': 'تصدير', 'import': 'استيراد', 'filter': 'تصفية', 'reset': 'إعادة تعيين',
        # Fields
        'name': 'الاسم', 'phone': 'الهاتف', 'date': 'التاريخ', 'time': 'الوقت',
        'status': 'الحالة', 'amount': 'المبلغ', 'service': 'الخدمة', 'actions': 'الإجراءات',
        'email': 'البريد', 'address': 'العنوان', 'notes': 'ملاحظات', 'description': 'الوصف',
        'price': 'السعر', 'quantity': 'الكمية', 'discount': 'الخصم', 'tax': 'الضريبة',
        # Statuses
        'paid': 'مدفوعة', 'unpaid': 'غير مدفوعة', 'partial': 'جزئية',
        'confirmed': 'مؤكد', 'pending': 'في الانتظار', 'completed': 'مكتمل',
        'cancelled': 'ملغى', 'in_progress': 'قيد التنفيذ',
        # Dashboard
        'total': 'المجموع', 'welcome': 'مرحبًا', 'language': 'اللغة',
        'overview': 'نظرة عامة على نشاطك',
        'revenue': 'الإيرادات', 'profit': 'صافي الربح', 'net_profit': 'صافي الربح',
        'pending_quotes': 'عروض أسعار معلقة', 'unpaid_invoices': 'فواتير غير مدفوعة',
        'tomorrow': 'غداً', 'this_week': 'هذا الأسبوع',
        'week_revenue': 'إيرادات الأسبوع', 'today_revenue': 'اليوم',
        'best_customer': 'أفضل عميل', 'most_visited_car': 'السيارة الأكثر زيارة',
        'payment_rate': 'نسبة الدفع', 'visits': 'زيارات',
        'online_bookings': 'الحجوزات عبر الإنترنت', 'see_bookings': 'عرض الحجوزات',
        'tomorrow_appointments': 'مواعيد الغد',
        'pending_appointments': 'مواعيد معلقة',
        # Quick actions
        'new_customer': '+ عميل جديد', 'new_appointment': '+ موعد جديد',
        'new_invoice': '+ فاتورة جديدة', 'see_quotes': 'عرض العروض',
        # Charts
        'weekly_revenue': 'إيرادات الأسبوع', 'monthly_comparison': 'مقارنة 12 شهر',
        'forecast': 'توقعات نهاية الشهر', 'revenue_vs_expenses': 'الإيرادات مقابل المصاريف — 6 أشهر',
        'services_chart': 'الخدمات', 'expenses_by_category': 'المصاريف حسب الفئة',
        'forecast_revenue': 'الإيرادات المتوقعة', 'forecast_expenses': 'المصاريف المتوقعة',
        'estimated_profit': 'الربح المقدر', 'daily_avg': 'المتوسط اليومي',
        'days_remaining': 'الأيام المتبقية',
        # Sidebar sections
        'principal': 'الرئيسية', 'car_moto_care': 'العناية بالسيارات',
        'clients_crm': 'العملاء و CRM', 'planning': 'التخطيط',
        'intelligence': 'الذكاء', 'billing': 'الفوترة',
        'stock': 'المخزون والمشتريات', 'reports_kpi': 'التقارير والمؤشرات',
        'team': 'الفريق', 'quick_access': 'وصول سريع',
        # Common
        'client': 'العميل', 'car': 'السيارة', 'brand': 'الماركة', 'model': 'الموديل',
        'plate': 'اللوحة', 'loading': 'جاري التحميل...', 'no_results': 'لا توجد نتائج',
        'page': 'صفحة', 'of': 'من', 'showing': 'عرض', 'results': 'نتائج',
        'yes': 'نعم', 'no': 'لا', 'all': 'الكل', 'none': 'لا شيء',
        'created': 'تاريخ الإنشاء', 'updated': 'تاريخ التعديل',
        'password': 'كلمة المرور', 'theme': 'المظهر',
        # Settings
        'general_settings': 'الإعدادات العامة', 'backup': 'نسخ احتياطي',
        'save_settings': 'حفظ', 'backup_now': 'نسخ احتياطي الآن',
        'download_db': 'تحميل قاعدة البيانات',
        'auto_daily_backup': 'نسخ احتياطي يومي تلقائي',
        'keep_days': 'الاحتفاظ (أيام)',
        'existing_backups': 'النسخ الاحتياطية الموجودة',
        # Telegram
        'telegram_backup': 'نسخ احتياطي عبر تيليجرام',
        'telegram_test': 'اختبار', 'telegram_send_now': 'إرسال الآن',
        'telegram_auto': 'إرسال تلقائي يومي عبر تيليجرام',
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

ROLE_PERMISSIONS = {
    'admin': ['all'],
    'manager': ['customers', 'appointments', 'invoices', 'reports', 'inventory', 'services', 'expenses', 'team', 'calendar', 'settings'],
    'receptionist': ['customers', 'appointments', 'invoices', 'calendar', 'quotes'],
    'technician': ['appointments', 'live_board', 'time_tracking', 'gallery', 'inspections'],
    'employee': ['appointments', 'customers'],
}

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

def permission_required(*perms):
    """Decorator: user must have at least one of the given permissions."""
    def decorator(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            if not session.get('user_id'):
                return redirect('/login')
            role = session.get('role', 'employee')
            allowed = ROLE_PERMISSIONS.get(role, [])
            if 'all' in allowed or any(p in allowed for p in perms):
                return f(*args, **kwargs)
            flash('Accès non autorisé pour votre rôle', 'error')
            return redirect('/')
        return decorated
    return decorator

def client_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('client_id'):
            return redirect('/espace-client')
        return f(*args, **kwargs)
    return decorated

def get_branch_id():
    """Return the current user's branch_id (0 = all branches / HQ)."""
    return session.get('branch_id', 0)

def branch_sql(table_alias='', column='branch_id'):
    """Return SQL condition + params to filter by current branch. Returns ('', []) for HQ/admin."""
    bid = get_branch_id()
    if bid and bid > 0:
        prefix = f"{table_alias}." if table_alias else ""
        return f" AND {prefix}{column} = ?", [bid]
    return "", []

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

def validate_file_mime(file_storage):
    """Validate file MIME type matches extension. Returns True if safe."""
    if not file_storage or not file_storage.filename:
        return False
    if not allowed_file(file_storage.filename):
        return False
    # Read first 8 bytes for magic number check
    header = file_storage.read(8)
    file_storage.seek(0)
    # JPEG: FF D8 FF | PNG: 89 50 4E 47 | GIF: 47 49 46 38 | WEBP: 52 49 46 46 ... 57 45 42 50
    if header[:3] == b'\xff\xd8\xff':  # JPEG
        return True
    if header[:4] == b'\x89PNG':  # PNG
        return True
    if header[:4] == b'GIF8':  # GIF
        return True
    if header[:4] == b'RIFF':  # WEBP (RIFF container)
        return True
    return False

def sanitize_phone(phone):
    """Strip non-digit chars except leading +."""
    if not phone:
        return ''
    phone = phone.strip()
    if phone.startswith('+'):
        return '+' + ''.join(c for c in phone[1:] if c.isdigit())
    return ''.join(c for c in phone if c.isdigit())

def safe_page(page):
    return max(1, min(page, 10000))

def log_activity(action, detail=''):
    ip = request.remote_addr if request else ''
    role = session.get('role', '')
    branch_id = session.get('branch_id', '')
    with get_db() as conn:
        conn.execute("INSERT INTO activity_log (user_id, username, action, detail) VALUES (?,?,?,?)",
            (session.get('user_id'), session.get('username', ''), action,
             f"[{role}|b:{branch_id}|{ip}] {detail}"))
        conn.commit()

def log_audit(action, entity_type='', entity_id=0, old_value='', new_value=''):
    """Write detailed audit trail entry."""
    with get_db() as conn:
        conn.execute(
            "INSERT INTO audit_log (user_id, username, action, entity_type, entity_id, old_value, new_value, ip_address) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (session.get('user_id', 0), session.get('username', ''), action,
             entity_type, entity_id, str(old_value)[:500], str(new_value)[:500],
             request.remote_addr if request else ''))
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


# ─── Reusable Rate Limit Decorator ───
_route_rate = {}

def rate_limit(max_calls=10, window=60):
    """Decorator: per-IP rate limiting for any route.
    Usage: @rate_limit(max_calls=5, window=300)
    """
    def decorator(f):
        @wraps(f)
        def wrapped(*args, **kwargs):
            from flask import jsonify as _json, abort
            key = f"{request.remote_addr}:{f.__name__}"
            now = time_module.time()
            if key in _route_rate:
                count, start = _route_rate[key]
                if now - start > window:
                    _route_rate[key] = (1, now)
                elif count >= max_calls:
                    if request.is_json or request.path.startswith('/api/'):
                        return _json({'error': 'Rate limit exceeded'}), 429
                    flash('Trop de requêtes — veuillez patienter', 'error')
                    return redirect(request.referrer or '/')
                else:
                    _route_rate[key] = (count + 1, start)
            else:
                _route_rate[key] = (1, now)
            return f(*args, **kwargs)
        return wrapped
    return decorator

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


# ── Fuzzy Search Helpers ──
import unicodedata

def _normalize(text):
    """Strip accents and lowercase for fuzzy matching."""
    if not text:
        return ''
    nfkd = unicodedata.normalize('NFKD', str(text))
    return ''.join(c for c in nfkd if not unicodedata.combining(c)).lower().strip()

def _trigrams(s):
    """Generate character trigrams for similarity scoring."""
    s = f'  {s} '
    return {s[i:i+3] for i in range(len(s) - 2)}

def fuzzy_score(query, text):
    """Return 0.0-1.0 similarity between query and text (trigram Jaccard + prefix bonus)."""
    nq, nt = _normalize(query), _normalize(text)
    if not nq or not nt:
        return 0.0
    # exact substring match → high score
    if nq in nt:
        return 1.0
    # prefix match on any word
    words = nt.split()
    if any(w.startswith(nq) for w in words):
        return 0.95
    # trigram similarity (Jaccard)
    tq, tt = _trigrams(nq), _trigrams(nt)
    if not tq or not tt:
        return 0.0
    intersection = len(tq & tt)
    union = len(tq | tt)
    return intersection / union if union else 0.0
