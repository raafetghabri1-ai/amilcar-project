"""Test authenticated routes."""


def test_dashboard(admin_client):
    resp = admin_client.get('/')
    assert resp.status_code == 200


def test_customers_list(admin_client):
    resp = admin_client.get('/customers')
    assert resp.status_code == 200


def test_appointments_list(admin_client):
    resp = admin_client.get('/appointments')
    assert resp.status_code == 200


def test_invoices_list(admin_client):
    resp = admin_client.get('/invoices')
    assert resp.status_code == 200


def test_daily_report(admin_client):
    resp = admin_client.get('/daily')
    assert resp.status_code == 200


def test_calendar(admin_client):
    resp = admin_client.get('/calendar')
    assert resp.status_code == 200


def test_inventory(admin_client):
    resp = admin_client.get('/inventory')
    assert resp.status_code == 200


def test_expenses(admin_client):
    resp = admin_client.get('/expenses')
    assert resp.status_code == 200


def test_settings(admin_client):
    resp = admin_client.get('/settings')
    assert resp.status_code == 200


def test_pos(admin_client):
    resp = admin_client.get('/pos')
    assert resp.status_code == 200


def test_users(admin_client):
    resp = admin_client.get('/users')
    assert resp.status_code == 200


def test_reports(admin_client):
    resp = admin_client.get('/reports')
    assert resp.status_code == 200


def test_api_docs(admin_client):
    resp = admin_client.get('/api/docs')
    assert resp.status_code == 200
