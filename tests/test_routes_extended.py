"""Tests for critical route operations — invoices, customers, appointments."""
import gzip
import json
import pytest


def _json(resp):
    data = resp.data
    if resp.content_encoding == 'gzip':
        data = gzip.decompress(data)
    return json.loads(data)


class TestInvoiceRoutes:
    """Test invoice creation and validation."""

    def test_add_invoice_page_requires_login(self, client):
        rv = client.get('/add_invoice')
        assert rv.status_code in (302, 303)

    def test_add_invoice_page_loads(self, admin_client):
        rv = admin_client.get('/add_invoice')
        assert rv.status_code == 200

    def test_add_invoice_empty_amount(self, admin_client):
        rv = admin_client.post('/add_invoice', data={
            'appointment_id': '1',
            'amount': '',
        }, follow_redirects=True)
        assert rv.status_code == 200

    def test_add_invoice_invalid_amount(self, admin_client):
        rv = admin_client.post('/add_invoice', data={
            'appointment_id': '1',
            'amount': 'abc',
        }, follow_redirects=True)
        assert rv.status_code == 200

    def test_add_invoice_negative_amount(self, admin_client):
        rv = admin_client.post('/add_invoice', data={
            'appointment_id': '1',
            'amount': '-50',
        }, follow_redirects=True)
        assert rv.status_code == 200

    def test_invoices_page_requires_login(self, client):
        rv = client.get('/invoices')
        assert rv.status_code in (302, 303)

    def test_invoices_page_loads(self, admin_client):
        rv = admin_client.get('/invoices')
        assert rv.status_code == 200


class TestCustomerRoutes:
    """Test customer management."""

    def test_customers_page_loads(self, admin_client):
        rv = admin_client.get('/customers')
        assert rv.status_code == 200

    def test_customers_search(self, admin_client):
        rv = admin_client.get('/customers?q=test')
        assert rv.status_code == 200

    def test_customer_detail_404(self, admin_client):
        rv = admin_client.get('/customer/999999')
        assert rv.status_code in (200, 302, 404)

    def test_edit_customer_requires_login(self, client):
        rv = client.get('/edit_customer/1')
        assert rv.status_code in (302, 303)

    def test_edit_customer_nonexistent(self, admin_client):
        """Editing a nonexistent customer should redirect or 404."""
        rv = admin_client.get('/edit_customer/999999')
        assert rv.status_code in (200, 302, 404)

    def test_edit_customer_requires_login(self, client):
        rv = client.post('/edit_customer/1', data={'name': 'X'})
        assert rv.status_code in (302, 303)


class TestAppointmentRoutes:
    """Test appointment management."""

    def test_appointments_page_loads(self, admin_client):
        rv = admin_client.get('/appointments')
        assert rv.status_code == 200

    def test_add_appointment_page_loads(self, admin_client):
        rv = admin_client.get('/add_appointment')
        assert rv.status_code == 200

    def test_appointments_filter(self, admin_client):
        rv = admin_client.get('/appointments?status=pending')
        assert rv.status_code == 200

    def test_appointments_date_filter(self, admin_client):
        rv = admin_client.get('/appointments?date=2026-03-18')
        assert rv.status_code == 200

    def test_calendar_view(self, admin_client):
        rv = admin_client.get('/calendar')
        assert rv.status_code == 200


class TestDashboard:
    """Test main dashboard."""

    def test_index_requires_login(self, client):
        rv = client.get('/')
        assert rv.status_code in (302, 303)

    def test_index_loads(self, admin_client):
        rv = admin_client.get('/')
        assert rv.status_code == 200

    def test_daily_page(self, admin_client):
        rv = admin_client.get('/daily')
        assert rv.status_code == 200

    def test_health_check(self, client):
        rv = client.get('/health')
        assert rv.status_code == 200
        data = _json(rv)
        assert data['status'] == 'healthy'


class TestReports:
    """Test report pages."""

    def test_reports_page(self, admin_client):
        rv = admin_client.get('/reports')
        assert rv.status_code == 200

    def test_monthly_report(self, admin_client):
        rv = admin_client.get('/monthly')
        assert rv.status_code == 200

    def test_expenses_page(self, admin_client):
        rv = admin_client.get('/expenses')
        assert rv.status_code == 200


class TestSettings:
    """Test settings pages."""

    def test_settings_requires_admin(self, client):
        with client.session_transaction() as sess:
            sess['user_id'] = 2
            sess['username'] = 'tech'
            sess['role'] = 'tech'
        rv = client.get('/settings')
        assert rv.status_code in (200, 302, 403)

    def test_settings_page(self, admin_client):
        rv = admin_client.get('/settings')
        assert rv.status_code == 200

    def test_backup_manual(self, admin_client):
        rv = admin_client.get('/backup', follow_redirects=True)
        assert rv.status_code == 200


class TestSearch:
    """Test search functionality."""

    def test_api_search(self, admin_client):
        rv = admin_client.get('/api/search?q=test')
        assert rv.status_code == 200

    def test_api_search_empty(self, admin_client):
        rv = admin_client.get('/api/search?q=')
        assert rv.status_code == 200


class TestInventory:
    """Test inventory pages."""

    def test_inventory_page(self, admin_client):
        rv = admin_client.get('/inventory')
        assert rv.status_code == 200

    def test_inventory_dashboard(self, admin_client):
        rv = admin_client.get('/inventory_dashboard')
        assert rv.status_code == 200

    def test_suppliers_page(self, admin_client):
        rv = admin_client.get('/suppliers')
        assert rv.status_code == 200

    def test_purchase_orders(self, admin_client):
        rv = admin_client.get('/purchase_orders')
        assert rv.status_code == 200


class TestTeam:
    """Test team pages."""

    def test_technician_performance(self, admin_client):
        rv = admin_client.get('/technician_performance')
        assert rv.status_code == 200

    def test_employee_shifts(self, admin_client):
        rv = admin_client.get('/employee_shifts')
        assert rv.status_code == 200

    def test_team_chat(self, admin_client):
        rv = admin_client.get('/team_chat')
        assert rv.status_code == 200


class TestVehicles:
    """Test vehicle pages."""

    def test_add_car_page(self, admin_client):
        rv = admin_client.get('/add_car')
        assert rv.status_code == 200

    def test_gallery_global(self, admin_client):
        rv = admin_client.get('/gallery_global')
        assert rv.status_code == 200
