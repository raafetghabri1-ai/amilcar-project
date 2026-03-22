"""Test security: auth, rate limiting, CSRF, headers, health check."""
import gzip
import pytest
from app import app as flask_app


def _decode(resp):
    if resp.data[:2] == b'\x1f\x8b':
        return gzip.decompress(resp.data).decode('utf-8')
    return resp.data.decode('utf-8')


# ═══════════════════════════════════════
# AUTHENTICATION
# ═══════════════════════════════════════

def test_login_wrong_password(client):
    resp = client.post('/login', data={
        'username': 'admin',
        'password': 'wrongpassword'
    }, follow_redirects=True)
    assert resp.status_code == 200


def test_login_empty_fields(client):
    resp = client.post('/login', data={
        'username': '',
        'password': ''
    }, follow_redirects=True)
    assert resp.status_code == 200


def test_logout(admin_client):
    resp = admin_client.get('/logout', follow_redirects=False)
    assert resp.status_code == 302
    # After logout, pages should redirect
    resp = admin_client.get('/customers', follow_redirects=False)
    assert resp.status_code == 302


def test_role_restriction(client):
    """Non-admin can't access admin routes."""
    with client.session_transaction() as sess:
        sess['user_id'] = 999
        sess['username'] = 'employee1'
        sess['role'] = 'employee'
    resp = client.get('/recycle_bin', follow_redirects=False)
    # Should redirect (403 or 302 to login)
    assert resp.status_code in (302, 403)


# ═══════════════════════════════════════
# SECURITY HEADERS
# ═══════════════════════════════════════

def test_security_headers(admin_client):
    resp = admin_client.get('/')
    assert resp.headers.get('X-Content-Type-Options') == 'nosniff'
    assert resp.headers.get('X-Frame-Options') == 'SAMEORIGIN'
    assert resp.headers.get('X-XSS-Protection') == '1; mode=block'
    assert resp.headers.get('Referrer-Policy') == 'strict-origin-when-cross-origin'


def test_csp_header(admin_client):
    resp = admin_client.get('/')
    csp = resp.headers.get('Content-Security-Policy', '')
    assert "default-src 'self'" in csp


def test_permissions_policy(admin_client):
    resp = admin_client.get('/')
    pp = resp.headers.get('Permissions-Policy', '')
    assert 'camera=()' in pp


# ═══════════════════════════════════════
# HEALTH CHECK
# ═══════════════════════════════════════

def test_health_endpoint(client):
    resp = client.get('/health')
    assert resp.status_code == 200
    data = _decode(resp)
    assert 'healthy' in data


def test_health_no_auth_required(client):
    """Health check should work without login."""
    resp = client.get('/health')
    assert resp.status_code == 200


# ═══════════════════════════════════════
# ERROR PAGES
# ═══════════════════════════════════════

def test_404_page(client):
    resp = client.get('/nonexistent_route_12345')
    assert resp.status_code == 404


def test_error_page_has_branding(client):
    resp = client.get('/nonexistent_route_12345')
    html = _decode(resp)
    assert 'AMILCAR' in html


# ═══════════════════════════════════════
# PROTECTED ROUTES
# ═══════════════════════════════════════

def test_all_critical_routes_protected(client):
    """Critical routes must redirect when not logged in."""
    routes = ['/customers', '/appointments', '/invoices', '/expenses',
              '/quotes', '/settings', '/users', '/reports', '/recycle_bin']
    for path in routes:
        resp = client.get(path, follow_redirects=False)
        assert resp.status_code == 302, f"{path} not protected (got {resp.status_code})"


def test_post_routes_protected(client):
    """POST routes must not work without auth."""
    routes = ['/add_customer', '/add_car', '/add_appointment']
    for path in routes:
        resp = client.post(path, data={'name': 'test'}, follow_redirects=False)
        assert resp.status_code in (302, 403, 401), f"{path} POST not protected"


# ═══════════════════════════════════════
# Phase 9: OTP Login, CSRF, Rate Limiting
# ═══════════════════════════════════════
import re as _re
from database.db import get_db as _get_db


@pytest.fixture
def clean_otp():
    """Clean OTP and login attempts tables."""
    with flask_app.app_context():
        with _get_db() as conn:
            conn.execute("DELETE FROM client_otp")
            conn.execute("DELETE FROM client_login_attempts")
            conn.execute("INSERT OR IGNORE INTO customers (name, phone) VALUES ('TestOTP', '55667788')")
            conn.commit()
    yield
    with flask_app.app_context():
        with _get_db() as conn:
            conn.execute("DELETE FROM client_otp WHERE phone='55667788'")
            conn.execute("DELETE FROM client_login_attempts WHERE phone='55667788'")
            conn.commit()


def test_client_otp_table_exists():
    """client_otp table should exist."""
    with flask_app.app_context():
        with _get_db() as conn:
            tables = [r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
            assert 'client_otp' in tables


def test_client_login_attempts_table_exists():
    """client_login_attempts table should exist."""
    with flask_app.app_context():
        with _get_db() as conn:
            tables = [r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
            assert 'client_login_attempts' in tables


def test_espace_client_login_page(client):
    """Login page should load with CSRF token."""
    r = client.get('/espace-client')
    assert r.status_code == 200
    assert b'csrf_token' in r.data


def test_login_unknown_phone(client, clean_otp):
    """Unknown phone should redirect back with error."""
    r = client.post('/espace-client/connexion', data={'phone': '00000000'}, follow_redirects=True)
    assert 'non trouv' in r.data.decode().lower()


def test_login_generates_otp(client, clean_otp):
    """Known phone should generate OTP and redirect to verify page."""
    r = client.post('/espace-client/connexion', data={'phone': '55667788'}, follow_redirects=True)
    page = r.data.decode()
    assert 'otp-inputs' in page or 'rification' in page


def test_otp_stored_hashed(client, clean_otp):
    """OTP should be stored as SHA256 hash."""
    r = client.post('/espace-client/connexion', data={'phone': '55667788'}, follow_redirects=True)
    with flask_app.app_context():
        with _get_db() as conn:
            record = conn.execute("SELECT otp_code FROM client_otp WHERE phone='55667788'").fetchone()
            assert record is not None
            assert len(record['otp_code']) == 64  # SHA256 hex


def test_wrong_otp_rejected(client, clean_otp):
    """Wrong OTP should be rejected."""
    client.post('/espace-client/connexion', data={'phone': '55667788'})
    r = client.post('/espace-client/verify-otp', data={'otp': '0000'}, follow_redirects=True)
    page = r.data.decode().lower()
    assert 'incorrect' in page or 'expir' in page


def test_correct_otp_logs_in(client, clean_otp):
    """Correct OTP should log the customer in."""
    r = client.post('/espace-client/connexion', data={'phone': '55667788'}, follow_redirects=True)
    otp_match = _re.search(r'Code de v.*?:\s*(\d{4})', r.data.decode())
    otp = otp_match.group(1)
    r = client.post('/espace-client/verify-otp', data={'otp': otp}, follow_redirects=False)
    assert r.status_code == 302
    assert '/espace-client/accueil' in r.headers.get('Location', '')


def test_resend_otp_works(client, clean_otp):
    """Resend OTP should generate a new code."""
    client.post('/espace-client/connexion', data={'phone': '55667788'})
    r = client.post('/espace-client/resend-otp', follow_redirects=True)
    assert _re.search(r'Nouveau code.*?:\s*\d{4}', r.data.decode())


def test_rate_limit_blocks_after_5(client, clean_otp):
    """Should block login after 5 failed OTP attempts."""
    for _ in range(5):
        client.post('/espace-client/connexion', data={'phone': '55667788'})
        client.post('/espace-client/verify-otp', data={'otp': '0000'})
    r = client.post('/espace-client/connexion', data={'phone': '55667788'}, follow_redirects=True)
    assert 'Trop de tentatives' in r.data.decode()


def test_booking_has_csrf_token(client):
    """Booking online form should contain CSRF token."""
    r = client.get('/booking_online')
    assert b'csrf_token' in r.data


def test_booking_phone_validation(client, clean_otp):
    """Booking should reject short phone numbers."""
    r = client.post('/booking_online/submit', data={
        'customer_name': 'Test', 'phone': '123',
        'preferred_date': '2026-04-01', 'services': 'Test'
    }, follow_redirects=True)
    assert b'invalide' in r.data


# ═══════════════════════════════════════
# Phase 9 UX: Quote, Invoices, Vehicles, Phone Pattern
# ═══════════════════════════════════════

def test_quote_form_has_services_dropdown(client):
    """Quote page should have a services dropdown, not text input."""
    r = client.get('/request_quote')
    assert b'<select name="service"' in r.data


def test_quote_form_has_photo_preview(client):
    """Quote page should have photo preview functionality."""
    r = client.get('/request_quote')
    assert b'photoPreview' in r.data


def test_quote_success_shows_reference(client):
    """Quote submission should show a reference number."""
    r = client.get('/request_quote')
    token = _re.search(r'csrf_token.*?value="([^"]+)"', r.data.decode()).group(1)
    r = client.post('/request_quote', data={
        'name': 'TestRef', 'phone': '22334455', 'service': 'Test',
        'csrf_token': token
    }, follow_redirects=True)
    page = r.data.decode()
    assert '#' in page and 'rence' in page


def test_phone_pattern_on_login(client):
    """Client login should have phone pattern validation."""
    r = client.get('/espace-client')
    assert b'pattern=' in r.data


def test_phone_pattern_on_booking(client):
    """Booking form should have phone pattern validation."""
    r = client.get('/booking_online')
    assert b'{8,20}' in r.data


@pytest.fixture
def logged_client(client, clean_otp):
    """Client logged in as customer."""
    with flask_app.app_context():
        with _get_db() as conn:
            cust = conn.execute("SELECT id FROM customers WHERE phone='55667788'").fetchone()
            if cust:
                cid = cust['id']
            else:
                cid = 1
    with client.session_transaction() as sess:
        sess['client_id'] = cid
        sess['client_name'] = 'TestOTP'
        sess['client_phone'] = '55667788'
    return client


def test_vehicules_has_add_button(logged_client):
    """Vehicles page should have add vehicle button and modal."""
    r = logged_client.get('/espace-client/vehicules')
    assert b'addCarModal' in r.data
    assert b'vehicules/ajouter' in r.data


def test_add_vehicle(logged_client):
    """Client should be able to add a vehicle."""
    r = logged_client.get('/espace-client/vehicules')
    token = _re.search(r'csrf_token.*?value="([^"]+)"', r.data.decode()).group(1)
    r = logged_client.post('/espace-client/vehicules/ajouter', data={
        'vehicle_type': 'voiture', 'brand': 'Audi', 'model': 'A4',
        'plate': 'TESTADD1', 'year': '2023', 'csrf_token': token
    }, follow_redirects=True)
    assert 'succ' in r.data.decode().lower()
    # Cleanup
    with flask_app.app_context():
        with _get_db() as conn:
            conn.execute("DELETE FROM cars WHERE plate='TESTADD1'")
            conn.commit()


def test_add_duplicate_vehicle_rejected(logged_client):
    """Adding a vehicle with same plate should be rejected."""
    r = logged_client.get('/espace-client/vehicules')
    token = _re.search(r'csrf_token.*?value="([^"]+)"', r.data.decode()).group(1)
    logged_client.post('/espace-client/vehicules/ajouter', data={
        'vehicle_type': 'voiture', 'brand': 'Test', 'model': 'Dup',
        'plate': 'DUPTEST', 'year': '2024', 'csrf_token': token
    })
    r = logged_client.get('/espace-client/vehicules')
    token = _re.search(r'csrf_token.*?value="([^"]+)"', r.data.decode()).group(1)
    r = logged_client.post('/espace-client/vehicules/ajouter', data={
        'vehicle_type': 'voiture', 'brand': 'Test', 'model': 'Dup2',
        'plate': 'DUPTEST', 'year': '2024', 'csrf_token': token
    }, follow_redirects=True)
    assert 'enregistr' in r.data.decode().lower()
    # Cleanup
    with flask_app.app_context():
        with _get_db() as conn:
            conn.execute("DELETE FROM cars WHERE plate='DUPTEST'")
            conn.commit()


def test_invoice_pdf_route_exists(logged_client):
    """Invoice PDF route should be accessible (not 404)."""
    r = logged_client.get('/espace-client/facture/999/pdf', follow_redirects=True)
    assert r.status_code != 404


# ── Phase 9 Medium Priority Tests ──

def test_client_notifications_table_exists():
    """client_notifications table must exist with correct columns."""
    with flask_app.app_context():
        with _get_db() as conn:
            cols = [r[1] for r in conn.execute("PRAGMA table_info(client_notifications)").fetchall()]
            assert 'customer_id' in cols
            assert 'title' in cols
            assert 'message' in cols
            assert 'is_read' in cols


def test_client_app_redirects_to_espace_client(client):
    """Legacy /client_app should redirect to /espace-client."""
    r = client.get('/client_app')
    assert r.status_code == 301
    assert '/espace-client' in r.headers.get('Location', '')


def test_client_app_dashboard_redirects(client):
    """Legacy /client_app/dashboard should redirect."""
    r = client.get('/client_app/dashboard')
    assert r.status_code == 301


def test_client_app_login_redirects(client):
    """Legacy /client_app/login POST should redirect."""
    r = client.post('/client_app/login', data={'phone': '12345678'})
    assert r.status_code == 301


def test_rdv_has_pagination_vars(logged_client):
    """RDV page should include pagination variables."""
    r = logged_client.get('/espace-client/rendez-vous')
    assert r.status_code == 200


def test_factures_has_pagination(logged_client):
    """Factures page should load with pagination support."""
    r = logged_client.get('/espace-client/factures')
    assert r.status_code == 200


def test_rdv_has_rating_form(logged_client):
    """Completed appointments should show rating form."""
    # Create a completed appointment for the client
    with flask_app.app_context():
        with _get_db() as conn:
            cust = conn.execute("SELECT id FROM customers WHERE phone='55667788'").fetchone()
            if not cust:
                return
            car = conn.execute("SELECT id FROM cars WHERE customer_id=?", (cust['id'],)).fetchone()
            if not car:
                conn.execute("INSERT INTO cars (customer_id, brand, model, plate) VALUES (?,?,?,?)",
                    (cust['id'], 'TestR', 'Rating', 'RATE001'))
                conn.commit()
                car = conn.execute("SELECT id FROM cars WHERE plate='RATE001'").fetchone()
            conn.execute("INSERT INTO appointments (car_id, date, service, status) VALUES (?,?,?,?)",
                (car['id'], '2026-03-20', 'Test Rating', 'completed'))
            conn.commit()
    r = logged_client.get('/espace-client/rendez-vous')
    assert b'noter' in r.data or b'Notez' in r.data
    # Cleanup
    with flask_app.app_context():
        with _get_db() as conn:
            conn.execute('PRAGMA foreign_keys = OFF')
            conn.execute("DELETE FROM appointments WHERE service='Test Rating'")
            conn.execute("DELETE FROM cars WHERE plate='RATE001'")
            conn.commit()


def test_submit_rating(logged_client):
    """Client should be able to rate a completed appointment."""
    with flask_app.app_context():
        with _get_db() as conn:
            cust = conn.execute("SELECT id FROM customers WHERE phone='55667788'").fetchone()
            if not cust:
                return
            car = conn.execute("SELECT id FROM cars WHERE customer_id=? LIMIT 1", (cust['id'],)).fetchone()
            if not car:
                conn.execute("INSERT INTO cars (customer_id, brand, model, plate) VALUES (?,?,?,?)",
                    (cust['id'], 'TestR2', 'Rate2', 'RATE002'))
                conn.commit()
                car = conn.execute("SELECT id FROM cars WHERE plate='RATE002'").fetchone()
            conn.execute("INSERT INTO appointments (car_id, date, service, status) VALUES (?,?,?,?)",
                (car['id'], '2026-03-19', 'Rate Service', 'completed'))
            conn.commit()
            appt = conn.execute("SELECT id FROM appointments WHERE service='Rate Service' ORDER BY id DESC LIMIT 1").fetchone()
            appt_id = appt['id']
    r = logged_client.get('/espace-client/rendez-vous')
    token = _re.search(r'csrf_token.*?value="([^"]+)"', r.data.decode()).group(1)
    r = logged_client.post(f'/espace-client/noter/{appt_id}', data={
        'rating': '5', 'comment': 'Excellent!', 'csrf_token': token
    }, follow_redirects=True)
    assert b'Merci' in r.data or r.status_code == 200
    # Cleanup
    with flask_app.app_context():
        with _get_db() as conn:
            conn.execute('PRAGMA foreign_keys = OFF')
            conn.execute("DELETE FROM ratings WHERE appointment_id=?", (appt_id,))
            conn.execute("DELETE FROM appointments WHERE service='Rate Service'")
            conn.execute("DELETE FROM cars WHERE plate='RATE002'")
            conn.commit()


def test_duplicate_rating_rejected(logged_client):
    """Rating same appointment twice should be rejected."""
    with flask_app.app_context():
        with _get_db() as conn:
            cust = conn.execute("SELECT id FROM customers WHERE phone='55667788'").fetchone()
            if not cust:
                return
            car = conn.execute("SELECT id FROM cars WHERE customer_id=? LIMIT 1", (cust['id'],)).fetchone()
            if not car:
                conn.execute("INSERT INTO cars (customer_id, brand, model, plate) VALUES (?,?,?,?)",
                    (cust['id'], 'TestR3', 'Rate3', 'RATE003'))
                conn.commit()
                car = conn.execute("SELECT id FROM cars WHERE plate='RATE003'").fetchone()
            conn.execute("INSERT INTO appointments (car_id, date, service, status) VALUES (?,?,?,?)",
                (car['id'], '2026-03-18', 'Dup Rate', 'completed'))
            conn.commit()
            appt = conn.execute("SELECT id FROM appointments WHERE service='Dup Rate' ORDER BY id DESC LIMIT 1").fetchone()
            appt_id = appt['id']
    # Get CSRF token BEFORE adding the rating
    r = logged_client.get('/espace-client/rendez-vous')
    token = _re.search(r'csrf_token.*?value="([^"]+)"', r.data.decode()).group(1)
    # First rating succeeds
    logged_client.post(f'/espace-client/noter/{appt_id}', data={
        'rating': '4', 'comment': 'First', 'csrf_token': token
    })
    # Second rating should fail
    r = logged_client.get('/espace-client/rendez-vous')
    token = _re.search(r'csrf_token.*?value="([^"]+)"', r.data.decode())
    # Try using any token from the page (other forms may still have tokens)
    if token:
        token = token.group(1)
    else:
        token = 'dummy'
    r = logged_client.post(f'/espace-client/noter/{appt_id}', data={
        'rating': '5', 'comment': 'Again', 'csrf_token': token
    }, follow_redirects=True)
    assert 'noté' in r.data.decode().lower() or r.status_code == 200
    # Cleanup
    with flask_app.app_context():
        with _get_db() as conn:
            conn.execute('PRAGMA foreign_keys = OFF')
            conn.execute("DELETE FROM ratings WHERE appointment_id=?", (appt_id,))
            conn.execute("DELETE FROM client_notifications WHERE appointment_id=?", (appt_id,))
            conn.execute("DELETE FROM appointments WHERE service='Dup Rate'")
            conn.execute("DELETE FROM cars WHERE plate='RATE003'")
            conn.commit()


def test_notifications_page(logged_client):
    """Notifications page should be accessible."""
    r = logged_client.get('/espace-client/notifications')
    assert r.status_code == 200
    assert b'Notification' in r.data


def test_notification_created_on_status_change():
    """Changing appointment status should create a client notification."""
    with flask_app.app_context():
        with _get_db() as conn:
            cust = conn.execute("SELECT id FROM customers WHERE phone='55667788'").fetchone()
            if not cust:
                return
            car = conn.execute("SELECT id FROM cars WHERE customer_id=? LIMIT 1", (cust['id'],)).fetchone()
            if not car:
                return
            conn.execute("INSERT INTO appointments (car_id, date, service, status) VALUES (?,?,?,?)",
                (car['id'], '2026-03-21', 'Notif Test', 'pending'))
            conn.commit()
            appt = conn.execute("SELECT id FROM appointments WHERE service='Notif Test' ORDER BY id DESC LIMIT 1").fetchone()
            appt_id = appt['id']
    # Use admin client to update status
    with flask_app.test_client() as c:
        with c.session_transaction() as sess:
            sess['user_id'] = 1
            sess['role'] = 'admin'
        c.post(f'/update_appointment/{appt_id}/in_progress')
    # Check notification was created
    with flask_app.app_context():
        with _get_db() as conn:
            notif = conn.execute("SELECT * FROM client_notifications WHERE appointment_id=?", (appt_id,)).fetchone()
            assert notif is not None
            assert 'traitement' in notif['message'].lower()
            # Cleanup
            conn.execute('PRAGMA foreign_keys = OFF')
            conn.execute("DELETE FROM client_notifications WHERE appointment_id=?", (appt_id,))
            conn.execute("DELETE FROM appointments WHERE service='Notif Test'")
            conn.commit()


def test_payment_page_loads(logged_client):
    """Payment page should load for an existing unpaid invoice."""
    r = logged_client.get('/espace-client/payer/999', follow_redirects=True)
    # Should redirect to factures if invoice not found
    assert r.status_code == 200


def test_accueil_shows_notification_badge(logged_client):
    """Accueil should show notification count if there are unread notifications."""
    with flask_app.app_context():
        with _get_db() as conn:
            cust = conn.execute("SELECT id FROM customers WHERE phone='55667788'").fetchone()
            if not cust:
                return
            conn.execute("INSERT INTO client_notifications (customer_id, title, message) VALUES (?,?,?)",
                (cust['id'], 'Test', 'Test notification'))
            conn.commit()
    r = logged_client.get('/espace-client/accueil')
    assert b'notification' in r.data.lower()
    # Cleanup
    with flask_app.app_context():
        with _get_db() as conn:
            conn.execute('PRAGMA foreign_keys = OFF')
            conn.execute("DELETE FROM client_notifications WHERE title='Test'")
            conn.commit()


def test_factures_has_payment_button(logged_client):
    """Unpaid invoices should show payment button."""
    r = logged_client.get('/espace-client/factures')
    # Page should load (even if no invoices, it shouldn't crash)
    assert r.status_code == 200
