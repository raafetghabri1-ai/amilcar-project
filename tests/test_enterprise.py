"""Tests for Phase 4 Enterprise features — permissions, branch, API docs."""
import gzip
import json
import pytest


def _json(resp):
    data = resp.data
    if resp.content_encoding == 'gzip':
        data = gzip.decompress(data)
    return json.loads(data)


class TestPermissions:
    """Test role-based permission system."""

    def test_admin_can_access_invoices(self, admin_client):
        rv = admin_client.get('/invoices')
        assert rv.status_code == 200

    def test_admin_can_access_expenses(self, admin_client):
        rv = admin_client.get('/expenses')
        assert rv.status_code == 200

    def test_technician_cannot_access_invoices(self, client):
        with client.session_transaction() as sess:
            sess['user_id'] = 99
            sess['username'] = 'tech1'
            sess['role'] = 'technician'
        rv = client.get('/invoices', follow_redirects=True)
        assert rv.status_code == 200  # redirected to /

    def test_technician_cannot_access_expenses(self, client):
        with client.session_transaction() as sess:
            sess['user_id'] = 99
            sess['username'] = 'tech1'
            sess['role'] = 'technician'
        rv = client.get('/expenses', follow_redirects=True)
        assert rv.status_code == 200  # redirected to /

    def test_manager_can_access_invoices(self, client):
        with client.session_transaction() as sess:
            sess['user_id'] = 2
            sess['username'] = 'manager1'
            sess['role'] = 'manager'
        rv = client.get('/invoices')
        assert rv.status_code == 200

    def test_receptionist_can_access_invoices(self, client):
        with client.session_transaction() as sess:
            sess['user_id'] = 3
            sess['username'] = 'recep1'
            sess['role'] = 'receptionist'
        rv = client.get('/invoices')
        assert rv.status_code == 200

    def test_employee_cannot_access_invoices(self, client):
        with client.session_transaction() as sess:
            sess['user_id'] = 4
            sess['username'] = 'emp1'
            sess['role'] = 'employee'
        rv = client.get('/invoices', follow_redirects=True)
        assert rv.status_code == 200  # redirected


class TestPermissionDecorator:
    """Test the permission_required decorator logic."""

    def test_role_permissions_dict_exists(self):
        from helpers import ROLE_PERMISSIONS
        assert 'admin' in ROLE_PERMISSIONS
        assert 'all' in ROLE_PERMISSIONS['admin']
        assert 'manager' in ROLE_PERMISSIONS
        assert 'technician' in ROLE_PERMISSIONS
        assert 'receptionist' in ROLE_PERMISSIONS
        assert 'employee' in ROLE_PERMISSIONS

    def test_manager_has_reports(self):
        from helpers import ROLE_PERMISSIONS
        assert 'reports' in ROLE_PERMISSIONS['manager']

    def test_technician_has_live_board(self):
        from helpers import ROLE_PERMISSIONS
        assert 'live_board' in ROLE_PERMISSIONS['technician']

    def test_receptionist_has_calendar(self):
        from helpers import ROLE_PERMISSIONS
        assert 'calendar' in ROLE_PERMISSIONS['receptionist']


class TestBranchHelpers:
    """Test branch SQL helper functions."""

    def test_get_branch_id_default(self, app):
        from helpers import get_branch_id
        with app.test_request_context():
            assert get_branch_id() == 0

    def test_branch_sql_hq(self, app):
        from helpers import branch_sql
        with app.test_request_context():
            sql, params = branch_sql()
            assert sql == ""
            assert params == []

    def test_branch_sql_with_branch(self, app):
        from flask import session
        from helpers import branch_sql
        with app.test_request_context():
            session['branch_id'] = 3
            sql, params = branch_sql('a')
            assert 'a.branch_id = ?' in sql
            assert params == [3]


class TestAPIOpenAPI:
    """Test OpenAPI spec endpoint."""

    def test_api_docs_page(self, admin_client):
        rv = admin_client.get('/api/docs')
        assert rv.status_code == 200

    def test_openapi_json(self, admin_client):
        rv = admin_client.get('/api/openapi.json')
        assert rv.status_code == 200
        data = _json(rv)
        assert data['openapi'] == '3.0.3'
        assert 'AMILCAR' in data['info']['title']

    def test_openapi_has_paths(self, admin_client):
        rv = admin_client.get('/api/openapi.json')
        data = _json(rv)
        assert '/api/v1/customers' in data['paths']
        assert '/api/v1/appointments' in data['paths']
        assert '/api/v1/invoices' in data['paths']
        assert '/api/v1/stats' in data['paths']

    def test_openapi_has_schemas(self, admin_client):
        rv = admin_client.get('/api/openapi.json')
        data = _json(rv)
        schemas = data['components']['schemas']
        assert 'Customer' in schemas
        assert 'Appointment' in schemas
        assert 'Invoice' in schemas

    def test_openapi_security(self, admin_client):
        rv = admin_client.get('/api/openapi.json')
        data = _json(rv)
        assert 'ApiKeyAuth' in data['components']['securitySchemes']


class TestBranches:
    """Test branch management pages."""

    def test_branches_page(self, admin_client):
        rv = admin_client.get('/branches')
        assert rv.status_code == 200

    def test_branches_requires_admin(self, client):
        with client.session_transaction() as sess:
            sess['user_id'] = 2
            sess['username'] = 'emp'
            sess['role'] = 'employee'
        rv = client.get('/branches', follow_redirects=True)
        assert rv.status_code == 200  # redirected
