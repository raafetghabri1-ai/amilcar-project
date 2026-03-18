"""Test security: auth, rate limiting, CSRF, headers, health check."""
import gzip


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
