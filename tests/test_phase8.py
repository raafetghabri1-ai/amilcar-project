"""
AMILCAR — Phase 8 Tests
FTS5, Export, Push Notifications, Force Password Change, Indexes
"""
import json
import pytest
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import app as flask_app
from database.db import get_db


@pytest.fixture
def client():
    flask_app.config['TESTING'] = True
    flask_app.config['WTF_CSRF_ENABLED'] = False
    with flask_app.test_client() as c:
        yield c


@pytest.fixture
def admin_client(client):
    with client.session_transaction() as sess:
        sess['user_id'] = 1
        sess['username'] = 'admin'
        sess['role'] = 'admin'
    return client


def _decode(resp):
    return resp.data.decode('utf-8', errors='replace')


# ═══════════════════════════════════════
# FTS5 FULL-TEXT SEARCH
# ═══════════════════════════════════════

def test_fts_tables_exist():
    """FTS5 virtual tables should exist in database."""
    with get_db() as conn:
        tables = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'fts_%'").fetchall()]
    assert 'fts_customers' in tables
    assert 'fts_cars' in tables


def test_fts_rebuild_function():
    """rebuild_fts() should run without errors."""
    from database.db import rebuild_fts
    rebuild_fts()  # Should not raise


def test_search_api_returns_json(admin_client):
    """Search API should return JSON array."""
    resp = admin_client.get('/api/search?q=test')
    assert resp.status_code == 200
    data = json.loads(resp.data)
    assert isinstance(data, list)


def test_search_api_short_query(admin_client):
    """Search with <2 chars should return empty."""
    resp = admin_client.get('/api/search?q=a')
    data = json.loads(resp.data)
    assert data == []


# ═══════════════════════════════════════
# EXCEL / CSV EXPORT
# ═══════════════════════════════════════

def test_export_customers_xlsx(admin_client):
    """Export customers as Excel should return xlsx file."""
    resp = admin_client.get('/api/export/customers?format=xlsx')
    assert resp.status_code == 200
    assert 'spreadsheetml' in resp.content_type


def test_export_customers_csv(admin_client):
    """Export customers as CSV should return text/csv."""
    resp = admin_client.get('/api/export/customers?format=csv')
    assert resp.status_code == 200
    assert 'text/csv' in resp.content_type
    content = _decode(resp)
    assert 'Nom' in content
    assert 'Téléphone' in content


def test_export_appointments_xlsx(admin_client):
    """Export appointments as Excel."""
    resp = admin_client.get('/api/export/appointments')
    assert resp.status_code == 200
    assert 'spreadsheetml' in resp.content_type


def test_export_invoices_csv(admin_client):
    """Export invoices as CSV."""
    resp = admin_client.get('/api/export/invoices?format=csv')
    assert resp.status_code == 200
    assert 'text/csv' in resp.content_type


def test_export_invalid_entity(admin_client):
    """Export unknown entity should return 400."""
    resp = admin_client.get('/api/export/unknown')
    assert resp.status_code == 400


def test_export_requires_admin(client):
    """Export should require admin login."""
    resp = client.get('/api/export/customers')
    assert resp.status_code in (302, 401, 403)


# ═══════════════════════════════════════
# PUSH NOTIFICATIONS
# ═══════════════════════════════════════

def test_vapid_key_endpoint(admin_client):
    """VAPID key endpoint should return publicKey or 503."""
    resp = admin_client.get('/api/push/vapid_key')
    assert resp.status_code in (200, 503)
    if resp.status_code == 200:
        data = json.loads(resp.data)
        assert 'publicKey' in data


def test_push_subscribe_requires_data(admin_client):
    """Push subscribe without data should return 400."""
    resp = admin_client.post('/api/push/subscribe',
                             data=json.dumps({}),
                             content_type='application/json')
    assert resp.status_code == 400


def test_push_subscribe_valid(admin_client):
    """Push subscribe with valid data should succeed."""
    sub = {
        'endpoint': 'https://fcm.googleapis.com/test/abc123',
        'keys': {
            'p256dh': 'BNcRdreALRFXTkOOUHK1EtK2wtaz5Ry4YfYCA_0QTpQtUbVlUls0VJXg7A8u-T5aR1QIhWKnwfX3KLEWQO',
            'auth': 'tBHItJI5svbpC7htgK'
        }
    }
    resp = admin_client.post('/api/push/subscribe',
                             data=json.dumps(sub),
                             content_type='application/json')
    assert resp.status_code == 200
    data = json.loads(resp.data)
    assert data.get('success') is True


def test_push_send_requires_admin(client):
    """Push send should require admin."""
    resp = client.post('/api/push/send',
                       data=json.dumps({'title': 'Test', 'body': 'Test'}),
                       content_type='application/json')
    assert resp.status_code in (302, 401, 403)


def test_push_subscriptions_table():
    """push_subscriptions table should exist."""
    with get_db() as conn:
        count = conn.execute("SELECT COUNT(*) FROM push_subscriptions").fetchone()[0]
    assert count >= 0


# ═══════════════════════════════════════
# ADDITIONAL INDEXES
# ═══════════════════════════════════════

def test_new_indexes_exist():
    """Phase 8 indexes should be present."""
    with get_db() as conn:
        indexes = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index'").fetchall()]
    assert 'idx_appointments_assigned' in indexes
    assert 'idx_appointments_time' in indexes
    assert 'idx_appointments_date_status' in indexes
    assert 'idx_invoices_status_amount' in indexes


# ═══════════════════════════════════════
# FORCE PASSWORD CHANGE
# ═══════════════════════════════════════

def test_must_change_password_column():
    """users table should have must_change_password column."""
    with get_db() as conn:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(users)").fetchall()]
    assert 'must_change_password' in cols


def test_force_password_change_redirect(client):
    """User with must_change_password should be redirected to /change_password."""
    with client.session_transaction() as sess:
        sess['user_id'] = 1
        sess['username'] = 'testuser'
        sess['role'] = 'admin'
        sess['must_change_password'] = True
    resp = client.get('/')
    assert resp.status_code == 302
    assert '/change_password' in resp.headers.get('Location', '')


# ═══════════════════════════════════════
# SERVICE WORKER
# ═══════════════════════════════════════

def test_service_worker_v8(admin_client):
    """Service worker should be v8 with enhanced caching."""
    resp = admin_client.get('/sw.js')
    assert resp.status_code == 200
    content = _decode(resp)
    assert 'amilcar-v8' in content
    assert 'style.min.css' in content
    assert 'PushManager' not in content  # SW should not reference PushManager
    assert 'push' in content.lower()
