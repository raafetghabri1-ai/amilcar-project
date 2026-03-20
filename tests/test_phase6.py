"""Tests for Phase 6: Webhooks, Rate Limiting, Tech Dashboard, Pagination, Sidebar.
"""
import gzip
import json


def _decode(resp):
    if resp.data[:2] == b'\x1f\x8b':
        return gzip.decompress(resp.data).decode('utf-8')
    return resp.data.decode('utf-8')


# ═══════════════════════════════════════
# RATE LIMIT DECORATOR
# ═══════════════════════════════════════

def test_rate_limit_decorator_exists():
    """rate_limit decorator should be importable."""
    from helpers import rate_limit
    assert callable(rate_limit)


def test_rate_limit_returns_decorator():
    """rate_limit() should return a decorator function."""
    from helpers import rate_limit
    dec = rate_limit(max_calls=5, window=60)
    assert callable(dec)


# ═══════════════════════════════════════
# WEBHOOKS (require API key)
# ═══════════════════════════════════════

def test_webhook_today_summary_no_key(client):
    """Webhook without API key returns 401."""
    resp = client.get('/api/webhooks/today_summary')
    assert resp.status_code == 401


def test_webhook_new_appointment_no_key(client):
    """Webhook without API key returns 401."""
    resp = client.post('/api/webhooks/new_appointment',
        data=json.dumps({'customer_phone': '123'}),
        content_type='application/json')
    assert resp.status_code == 401


def test_webhook_appointment_status_no_key(client):
    resp = client.post('/api/webhooks/appointment_status',
        data=json.dumps({'appointment_id': 1, 'status': 'completed'}),
        content_type='application/json')
    assert resp.status_code == 401


def test_webhook_new_customer_no_key(client):
    resp = client.post('/api/webhooks/new_customer',
        data=json.dumps({'name': 'Test', 'phone': '123'}),
        content_type='application/json')
    assert resp.status_code == 401


def test_webhook_events_no_key(client):
    resp = client.get('/api/webhooks/events')
    assert resp.status_code == 401


def _create_api_key(app):
    """Create a test API key and return it."""
    from database.db import get_db
    with app.app_context():
        with get_db() as conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS api_keys (
                id INTEGER PRIMARY KEY, api_key TEXT UNIQUE, name TEXT,
                active INTEGER DEFAULT 1, last_used TEXT DEFAULT '')""")
            conn.execute("INSERT OR IGNORE INTO api_keys (api_key, name, active) VALUES ('test_key_123', 'test', 1)")
            conn.commit()
    return 'test_key_123'


def test_webhook_today_summary_with_key(app, client):
    """Webhook with valid API key returns 200."""
    key = _create_api_key(app)
    resp = client.get('/api/webhooks/today_summary',
                      headers={'X-API-Key': key})
    assert resp.status_code == 200
    data = resp.get_json()
    assert 'appointments' in data
    assert 'revenue_today' in data


def test_webhook_new_appointment_missing_fields(app, client):
    """Webhook with missing fields returns 400."""
    key = _create_api_key(app)
    resp = client.post('/api/webhooks/new_appointment',
        headers={'X-API-Key': key},
        data=json.dumps({'customer_phone': '123'}),
        content_type='application/json')
    assert resp.status_code == 400


def test_webhook_new_customer_with_key(app, client):
    """Webhook: create customer with valid data."""
    import time
    key = _create_api_key(app)
    unique_phone = f'WH{int(time.time()*1000)}'
    resp = client.post('/api/webhooks/new_customer',
        headers={'X-API-Key': key},
        data=json.dumps({
            'name': 'WebhookTest', 'phone': unique_phone,
            'brand': 'BMW', 'model': 'X5', 'plate': f'WH-{unique_phone[-4:]}'
        }),
        content_type='application/json')
    assert resp.status_code == 201
    data = resp.get_json()
    assert data['success'] is True
    assert 'customer_id' in data


def test_webhook_new_customer_duplicate(app, client):
    """Webhook: duplicate phone returns 409."""
    import time
    key = _create_api_key(app)
    unique_phone = f'DP{int(time.time()*1000)}'
    payload = json.dumps({
        'name': 'Dup', 'phone': unique_phone,
        'brand': 'Audi', 'model': 'A4', 'plate': f'DP-{unique_phone[-4:]}'
    })
    client.post('/api/webhooks/new_customer',
        headers={'X-API-Key': key}, data=payload, content_type='application/json')
    resp = client.post('/api/webhooks/new_customer',
        headers={'X-API-Key': key}, data=payload, content_type='application/json')
    assert resp.status_code == 409


def test_webhook_events_with_key(app, client):
    """Webhook: events endpoint returns list."""
    key = _create_api_key(app)
    resp = client.get('/api/webhooks/events', headers={'X-API-Key': key})
    assert resp.status_code == 200
    data = resp.get_json()
    assert 'events' in data


# ═══════════════════════════════════════
# TECH DASHBOARD
# ═══════════════════════════════════════

def test_tech_dashboard_renders(client):
    """Technician sees their dashboard on /."""
    with client.session_transaction() as sess:
        sess['user_id'] = 99
        sess['username'] = 'tech1'
        sess['role'] = 'technician'
    resp = client.get('/')
    assert resp.status_code == 200
    html = _decode(resp)
    assert 'MON TABLEAU DE BORD' in html


def test_tech_dashboard_has_progress_bar(client):
    """Technician dashboard should have progress bar."""
    with client.session_transaction() as sess:
        sess['user_id'] = 99
        sess['username'] = 'tech1'
        sess['role'] = 'technician'
    resp = client.get('/')
    html = _decode(resp)
    assert 'Progression' in html


def test_tech_dashboard_has_week_stats(client):
    """Technician dashboard should show weekly stats."""
    with client.session_transaction() as sess:
        sess['user_id'] = 99
        sess['username'] = 'tech1'
        sess['role'] = 'technician'
    resp = client.get('/')
    html = _decode(resp)
    assert 'Cette semaine' in html


# ═══════════════════════════════════════
# PAGINATION COMPONENT
# ═══════════════════════════════════════

def test_customers_pagination_renders(admin_client):
    """Customers page should render without error."""
    resp = admin_client.get('/customers')
    assert resp.status_code == 200


def test_invoices_pagination_renders(admin_client):
    """Invoices page should render without error."""
    resp = admin_client.get('/invoices')
    assert resp.status_code == 200


def test_appointments_pagination_renders(admin_client):
    """Appointments page should render without error."""
    resp = admin_client.get('/appointments')
    assert resp.status_code == 200


# ═══════════════════════════════════════
# SIDEBAR INSTANT SEARCH
# ═══════════════════════════════════════

def test_sidebar_has_search_filter_js(admin_client):
    """Dashboard should contain the sidebar instant filter JS."""
    resp = admin_client.get('/')
    html = _decode(resp)
    assert 'Sidebar Instant Filter' in html


# ═══════════════════════════════════════
# LOGIN PAGE FORGOT PASSWORD
# ═══════════════════════════════════════

def test_login_has_forgot_password_text(client):
    """Login page should mention forgotten password."""
    resp = client.get('/login')
    html = _decode(resp)
    assert 'oubli' in html.lower()
