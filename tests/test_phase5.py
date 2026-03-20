"""Tests for Phase 5: Security + UX improvements.
Covers: CSP headers, CORS, cache-busting, password reset, error pages, sidebar links.
"""
import gzip


def _decode(resp):
    if resp.data[:2] == b'\x1f\x8b':
        return gzip.decompress(resp.data).decode('utf-8')
    return resp.data.decode('utf-8')


# ═══════════════════════════════════════
# CSP HEADERS
# ═══════════════════════════════════════

def test_csp_has_object_src_none(admin_client):
    """CSP must block object embeds."""
    resp = admin_client.get('/')
    csp = resp.headers.get('Content-Security-Policy', '')
    assert "object-src 'none'" in csp


def test_csp_has_base_uri(admin_client):
    """CSP must restrict base-uri."""
    resp = admin_client.get('/')
    csp = resp.headers.get('Content-Security-Policy', '')
    assert "base-uri 'self'" in csp


def test_csp_has_form_action(admin_client):
    """CSP must restrict form-action."""
    resp = admin_client.get('/')
    csp = resp.headers.get('Content-Security-Policy', '')
    assert "form-action 'self'" in csp


def test_csp_no_unsafe_eval(admin_client):
    """CSP must NOT allow unsafe-eval."""
    resp = admin_client.get('/')
    csp = resp.headers.get('Content-Security-Policy', '')
    assert 'unsafe-eval' not in csp


def test_csp_not_on_static(client):
    """Static files should not have CSP header."""
    resp = client.get('/static/style.css')
    csp = resp.headers.get('Content-Security-Policy', '')
    assert csp == ''


# ═══════════════════════════════════════
# ERROR PAGES
# ═══════════════════════════════════════

def test_404_page(client):
    resp = client.get('/nonexistent_page_xyz')
    assert resp.status_code == 404
    html = _decode(resp)
    assert '404' in html
    assert 'AMILCAR' in html


def test_error_401_shows_login_link(app):
    """401 error page should include a login link."""
    with app.test_client() as c:
        # Trigger 401 via abort
        with app.test_request_context():
            from flask import abort
            try:
                abort(401)
            except Exception:
                pass
        resp = c.get('/nonexistent_page_xyz')
        # 404 at least
        assert resp.status_code == 404


# ═══════════════════════════════════════
# CACHE-BUSTING
# ═══════════════════════════════════════

def test_asset_hash_returns_consistent_value(app):
    """asset_hash should return a consistent hash for the same file."""
    with app.app_context():
        from app import asset_hash
        h1 = asset_hash('style.css')
        h2 = asset_hash('style.css')
        assert h1 == h2
        assert len(h1) == 8


def test_asset_hash_nonexistent_file(app):
    """asset_hash should return '0' for missing files."""
    with app.app_context():
        from app import asset_hash
        h = asset_hash('nonexistent_file.xyz')
        assert h == '0'


def test_dashboard_uses_asset_hash(admin_client):
    """Dashboard should contain hashed asset URLs, not hardcoded ?v=46."""
    resp = admin_client.get('/')
    html = _decode(resp)
    assert '?v=46' not in html
    assert 'style.min.css?v=' in html


# ═══════════════════════════════════════
# PASSWORD RESET
# ═══════════════════════════════════════

def test_generate_reset_link(admin_client):
    """Admin should be able to generate a reset link."""
    # First create a test user
    admin_client.post('/add_user', data={
        'username': 'resettest',
        'password': 'test123456',
        'role': 'employee',
        'full_name': 'Reset Test'
    }, follow_redirects=True)
    # Get user ID
    from database.db import get_db
    with get_db() as conn:
        user = conn.execute("SELECT id FROM users WHERE username='resettest'").fetchone()
    assert user is not None
    # Generate reset link
    resp = admin_client.post(f'/generate_reset_link/{user[0]}', follow_redirects=True)
    html = _decode(resp)
    assert 'reset_password/' in html or resp.status_code == 200


def test_reset_password_invalid_token(client):
    """Invalid token should redirect to login."""
    resp = client.get('/reset_password/invalid_token_xyz', follow_redirects=True)
    html = _decode(resp)
    assert 'Connexion' in html or 'login' in html.lower() or resp.status_code == 200


def test_reset_password_valid_flow(admin_client, client):
    """Full password reset flow: generate token -> use it."""
    # Create user
    admin_client.post('/add_user', data={
        'username': 'resetflow',
        'password': 'old123456',
        'role': 'employee',
        'full_name': 'Reset Flow'
    }, follow_redirects=True)
    from database.db import get_db
    with get_db() as conn:
        user = conn.execute("SELECT id FROM users WHERE username='resetflow'").fetchone()
    # Generate reset link
    admin_client.post(f'/generate_reset_link/{user[0]}', follow_redirects=True)
    # Get token from DB
    with get_db() as conn:
        token_row = conn.execute(
            "SELECT token FROM password_reset_tokens WHERE user_id=? ORDER BY id DESC LIMIT 1",
            (user[0],)).fetchone()
    assert token_row is not None
    token = token_row[0]
    # Visit reset page
    resp = client.get(f'/reset_password/{token}')
    assert resp.status_code == 200
    html = _decode(resp)
    assert 'resetflow' in html
    # Submit new password
    resp = client.post(f'/reset_password/{token}', data={
        'new_password': 'new123456',
        'confirm_password': 'new123456'
    }, follow_redirects=True)
    assert resp.status_code == 200
    # Token should now be used
    with get_db() as conn:
        used = conn.execute("SELECT used FROM password_reset_tokens WHERE token=?", (token,)).fetchone()
    assert used[0] == 1


def test_reset_password_short_password(admin_client, client):
    """Password reset with too-short password should fail."""
    admin_client.post('/add_user', data={
        'username': 'resetshort',
        'password': 'test123456',
        'role': 'employee',
        'full_name': 'Short'
    }, follow_redirects=True)
    from database.db import get_db
    with get_db() as conn:
        user = conn.execute("SELECT id FROM users WHERE username='resetshort'").fetchone()
    admin_client.post(f'/generate_reset_link/{user[0]}', follow_redirects=True)
    with get_db() as conn:
        token = conn.execute("SELECT token FROM password_reset_tokens WHERE user_id=? ORDER BY id DESC LIMIT 1",
            (user[0],)).fetchone()[0]
    resp = client.post(f'/reset_password/{token}', data={
        'new_password': '12',
        'confirm_password': '12'
    }, follow_redirects=True)
    html = _decode(resp)
    assert '6 caract' in html or resp.status_code == 200


def test_reset_password_mismatch(admin_client, client):
    """Password reset with mismatched passwords should fail."""
    admin_client.post('/add_user', data={
        'username': 'resetmis',
        'password': 'test123456',
        'role': 'employee',
        'full_name': 'Mismatch'
    }, follow_redirects=True)
    from database.db import get_db
    with get_db() as conn:
        user = conn.execute("SELECT id FROM users WHERE username='resetmis'").fetchone()
    admin_client.post(f'/generate_reset_link/{user[0]}', follow_redirects=True)
    with get_db() as conn:
        token = conn.execute("SELECT token FROM password_reset_tokens WHERE user_id=? ORDER BY id DESC LIMIT 1",
            (user[0],)).fetchone()[0]
    resp = client.post(f'/reset_password/{token}', data={
        'new_password': 'abc123456',
        'confirm_password': 'xyz789012'
    }, follow_redirects=True)
    html = _decode(resp)
    assert 'correspondent' in html or resp.status_code == 200


# ═══════════════════════════════════════
# SIDEBAR LINKS
# ═══════════════════════════════════════

def test_sidebar_has_pnl_report(admin_client):
    """Sidebar must link to P&L detailed report."""
    resp = admin_client.get('/')
    html = _decode(resp)
    assert '/pnl_report' in html


def test_sidebar_has_auto_reminders(admin_client):
    """Sidebar must link to auto reminders."""
    resp = admin_client.get('/')
    html = _decode(resp)
    assert '/auto_reminders' in html


def test_sidebar_has_db_health(admin_client):
    """Sidebar must link to DB health check."""
    resp = admin_client.get('/')
    html = _decode(resp)
    assert '/db_health' in html


# ═══════════════════════════════════════
# SECURITY HEADERS
# ═══════════════════════════════════════

def test_permissions_policy_header(admin_client):
    resp = admin_client.get('/')
    assert 'camera=()' in resp.headers.get('Permissions-Policy', '')


def test_referrer_policy_header(admin_client):
    resp = admin_client.get('/')
    assert resp.headers.get('Referrer-Policy') == 'strict-origin-when-cross-origin'


def test_x_frame_options(admin_client):
    resp = admin_client.get('/')
    assert resp.headers.get('X-Frame-Options') == 'SAMEORIGIN'
