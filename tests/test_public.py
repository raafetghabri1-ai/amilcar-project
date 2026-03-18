"""Test public routes (no login required)."""


def test_login_page(client):
    resp = client.get('/login')
    assert resp.status_code == 200


def test_client_portal(client):
    resp = client.get('/espace-client')
    assert resp.status_code == 200


def test_online_booking(client):
    resp = client.get('/book')
    assert resp.status_code == 200


def test_request_quote(client):
    resp = client.get('/request_quote')
    assert resp.status_code == 200


def test_protected_routes_redirect(client):
    """Protected routes should redirect to login."""
    protected = ['/', '/customers', '/appointments', '/invoices', '/daily']
    for path in protected:
        resp = client.get(path, follow_redirects=False)
        assert resp.status_code == 302, f"{path} should redirect but got {resp.status_code}"
