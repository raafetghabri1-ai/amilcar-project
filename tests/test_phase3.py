"""
AMILCAR — Phase 3 Tests: Financial Reports, WhatsApp, Audit, Role Dashboard, Validation
"""
import pytest
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ─── Financial Reports Tests ───────────────────────────────────────────────

class TestFinancialReports:
    def test_pnl_report_page(self, admin_client):
        resp = admin_client.get('/pnl_report')
        assert resp.status_code == 200
        assert 'COMPTE DE RÉSULTAT' in resp.data.decode()

    def test_pnl_report_with_year(self, admin_client):
        resp = admin_client.get('/pnl_report?year=2024')
        assert resp.status_code == 200
        assert '2024' in resp.data.decode()

    def test_pnl_report_prev_year(self, admin_client):
        resp = admin_client.get('/pnl_report?year=2023')
        assert resp.status_code == 200
        assert '2022' in resp.data.decode()  # prev_year shown

    def test_financial_excel_export(self, admin_client):
        resp = admin_client.get('/export/financial_excel?year=2024')
        assert resp.status_code == 200
        assert resp.content_type == 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'

    def test_financial_excel_default_year(self, admin_client):
        resp = admin_client.get('/export/financial_excel')
        assert resp.status_code == 200

    def test_reports_page(self, admin_client):
        resp = admin_client.get('/reports')
        assert resp.status_code == 200

    def test_monthly_page(self, admin_client):
        resp = admin_client.get('/monthly')
        assert resp.status_code == 200

    def test_ceo_dashboard(self, admin_client):
        resp = admin_client.get('/ceo_dashboard')
        assert resp.status_code == 200

    def test_profitability(self, admin_client):
        resp = admin_client.get('/profitability')
        assert resp.status_code == 200

    def test_advanced_report(self, admin_client):
        resp = admin_client.get('/advanced_report')
        assert resp.status_code == 200

    def test_advanced_report_yearly(self, admin_client):
        resp = admin_client.get('/advanced_report?period=year')
        assert resp.status_code == 200

    def test_retention_analysis(self, admin_client):
        resp = admin_client.get('/retention_analysis')
        assert resp.status_code == 200


# ─── WhatsApp Tests ────────────────────────────────────────────────────────

class TestWhatsApp:
    def test_auto_reminders_get(self, admin_client):
        resp = admin_client.get('/auto_reminders')
        assert resp.status_code == 200
        assert 'RAPPELS' in resp.data.decode()

    def test_auto_reminders_post(self, admin_client):
        resp = admin_client.post('/auto_reminders', data={})
        assert resp.status_code == 200

    def test_notify_car_ready_missing(self, admin_client):
        resp = admin_client.get('/notify_car_ready/99999')
        assert resp.status_code in (302, 404)

    def test_build_wa_url(self):
        from helpers import build_wa_url
        url = build_wa_url('98123456', 'Hello')
        assert 'wa.me/21698123456' in url
        assert 'Hello' in url

    def test_build_wa_url_with_plus(self):
        from helpers import build_wa_url
        url = build_wa_url('+21698123456', 'Test')
        assert 'wa.me/21698123456' in url

    def test_build_wa_url_with_216(self):
        from helpers import build_wa_url
        url = build_wa_url('21698123456', 'Test')
        assert 'wa.me/21698123456' in url

    def test_build_wa_url_special_chars(self):
        from helpers import build_wa_url
        url = build_wa_url('98123456', 'Bonjour! 🚗')
        assert 'wa.me/' in url


# ─── Audit Log Tests ──────────────────────────────────────────────────────

class TestAuditLog:
    def test_log_audit_function(self, app):
        from helpers import log_audit
        with app.test_request_context():
            from flask import session
            session['user_id'] = 1
            session['username'] = 'test'
            # Should not raise
            log_audit('test_action', 'test_entity', 1, 'old', 'new')

    def test_log_activity_enhanced(self, app):
        from helpers import log_activity
        with app.test_request_context():
            from flask import session
            session['user_id'] = 1
            session['username'] = 'test'
            session['role'] = 'admin'
            session['branch_id'] = 0
            log_activity('test', 'detail')

    def test_audit_trail_page(self, admin_client):
        resp = admin_client.get('/audit_trail')
        assert resp.status_code == 200

    def test_activity_log_page(self, admin_client):
        resp = admin_client.get('/activity_log')
        assert resp.status_code == 200


# ─── Role-based Dashboard Tests ───────────────────────────────────────────

class TestRoleDashboard:
    def test_admin_dashboard(self, admin_client):
        resp = admin_client.get('/')
        assert resp.status_code == 200

    def test_technician_dashboard(self, client):
        with client.session_transaction() as sess:
            sess['user_id'] = 99
            sess['username'] = 'tech1'
            sess['role'] = 'technician'
        resp = client.get('/')
        assert resp.status_code == 200
        assert 'MON TABLEAU DE BORD' in resp.data.decode()

    def test_manager_sees_full_dashboard(self, client):
        with client.session_transaction() as sess:
            sess['user_id'] = 2
            sess['username'] = 'manager'
            sess['role'] = 'manager'
        resp = client.get('/')
        assert resp.status_code == 200

    def test_receptionist_sees_full_dashboard(self, client):
        with client.session_transaction() as sess:
            sess['user_id'] = 3
            sess['username'] = 'reception'
            sess['role'] = 'receptionist'
        resp = client.get('/')
        assert resp.status_code == 200


# ─── Validation Tests ─────────────────────────────────────────────────────

class TestValidationExpanded:
    def test_validator_date_str_valid(self):
        from helpers_validation import Validator
        v = Validator()
        result = v.date_str('2024-06-15')
        assert v.ok
        assert result == '2024-06-15'

    def test_validator_date_str_invalid(self):
        from helpers_validation import Validator
        v = Validator()
        v.date_str('15-06-2024')
        assert not v.ok

    def test_validator_date_str_empty(self):
        from helpers_validation import Validator
        v = Validator()
        v.date_str('')
        assert not v.ok

    def test_validator_time_str_valid(self):
        from helpers_validation import Validator
        v = Validator()
        result = v.time_str('14:30')
        assert v.ok
        assert result == '14:30'

    def test_validator_time_str_invalid(self):
        from helpers_validation import Validator
        v = Validator()
        v.time_str('2pm')
        assert not v.ok

    def test_validator_time_str_empty_ok(self):
        from helpers_validation import Validator
        v = Validator()
        result = v.time_str('')
        assert v.ok
        assert result == ''

    def test_validator_plate_valid(self):
        from helpers_validation import Validator
        v = Validator()
        result = v.plate('123 TU 4567')
        assert v.ok
        assert result == '123 TU 4567'

    def test_validator_plate_invalid(self):
        from helpers_validation import Validator
        v = Validator()
        v.plate('!@#$%^&*()')
        assert not v.ok

    def test_validator_safe_text_strips_html(self):
        from helpers_validation import Validator
        v = Validator()
        result = v.safe_text('<script>alert(1)</script>Hello', 'field', 'Label')
        assert '<script>' not in result
        assert 'Hello' in result

    def test_validator_amount_valid(self):
        from helpers_validation import Validator
        v = Validator()
        result = v.amount('250.500')
        assert v.ok
        assert result == 250.5

    def test_validator_amount_negative(self):
        from helpers_validation import Validator
        v = Validator()
        v.amount('-50')
        assert not v.ok

    def test_validator_amount_too_large(self):
        from helpers_validation import Validator
        v = Validator()
        v.amount('9999999')
        assert not v.ok

    def test_validator_choice(self):
        from helpers_validation import Validator
        v = Validator()
        result = v.choice('paid', ['paid', 'unpaid', 'partial'], 'status')
        assert v.ok
        assert result == 'paid'

    def test_validator_choice_invalid(self):
        from helpers_validation import Validator
        v = Validator()
        v.choice('hacked', ['paid', 'unpaid'], 'status')
        assert not v.ok

    def test_validator_multiple_errors(self):
        from helpers_validation import Validator
        v = Validator()
        v.require('', 'name', 'Nom')
        v.phone('abc')
        v.email('not-email')
        assert len(v.errors) == 3
        assert len(v.all_messages()) == 3

    def test_validator_require_int_min(self):
        from helpers_validation import Validator
        v = Validator()
        result = v.require_int('-5', 'qty', 'Quantité', min_val=0)
        assert not v.ok

    def test_validator_require_int_max(self):
        from helpers_validation import Validator
        v = Validator()
        result = v.require_int('999', 'qty', 'Quantité', max_val=100)
        assert not v.ok

    def test_validator_optional_float_empty(self):
        from helpers_validation import Validator
        v = Validator()
        result = v.optional_float('', 'amount', 'Montant')
        assert v.ok
        assert result == 0.0

    def test_validator_string_too_long(self):
        from helpers_validation import Validator
        v = Validator()
        result = v.string('x' * 600, 'field', 'Champ', max_len=500)
        assert not v.ok

    def test_validator_require_float_comma(self):
        from helpers_validation import Validator
        v = Validator()
        result = v.require_float('12,50', 'price', 'Prix')
        assert v.ok
        assert result == 12.5


# ─── Form Validation Integration Tests ────────────────────────────────────

class TestFormValidation:
    def test_add_appointment_validates_date(self, admin_client):
        resp = admin_client.post('/add_appointment', data={'car_id': '1', 'date': 'invalid', 'service': 'Oil Change'})
        assert resp.status_code == 200  # Re-renders form

    def test_add_appointment_missing_car(self, admin_client):
        resp = admin_client.post('/add_appointment', data={'car_id': '', 'date': '2024-06-15', 'service': 'Oil Change'})
        assert resp.status_code == 200

    def test_add_expense_validates(self, admin_client):
        resp = admin_client.post('/add_expense', data={'date': 'bad', 'category': 'Loyer', 'amount': '100'})
        assert resp.status_code == 200

    def test_add_expense_validates_amount(self, admin_client):
        resp = admin_client.post('/add_expense', data={'date': '2024-06-15', 'category': 'Loyer', 'amount': 'abc'})
        assert resp.status_code == 200

    def test_add_car_validates_plate(self, admin_client):
        resp = admin_client.post('/add_car', data={
            'customer_id': '1', 'brand': 'BMW', 'model': 'X5', 'plate': '!@#$%'
        })
        assert resp.status_code == 200

    def test_add_customer_validates_phone(self, admin_client):
        resp = admin_client.post('/add_customer', data={
            'name': 'Test User', 'phone': 'abc', 'email': ''
        })
        assert resp.status_code == 200

    def test_add_customer_validates_email(self, admin_client):
        resp = admin_client.post('/add_customer', data={
            'name': 'Test User', 'phone': '98123456', 'email': 'not-email'
        })
        assert resp.status_code == 200

    def test_add_customer_validates_name(self, admin_client):
        resp = admin_client.post('/add_customer', data={
            'name': 'X', 'phone': '98123456', 'email': ''
        })
        assert resp.status_code == 200


# ─── DB Health Tests ──────────────────────────────────────────────────────

class TestDBHealth:
    def test_db_health_page(self, admin_client):
        resp = admin_client.get('/db_health')
        assert resp.status_code == 200
        assert 'TABLES' in resp.data.decode()

    def test_db_health_requires_admin(self, client):
        with client.session_transaction() as sess:
            sess['user_id'] = 99
            sess['username'] = 'tech'
            sess['role'] = 'technician'
        resp = client.get('/db_health')
        assert resp.status_code in (302, 403)


# ─── Smart Scheduling Tests ──────────────────────────────────────────────

class TestSmartScheduling:
    def test_available_slots(self, admin_client):
        resp = admin_client.get('/api/available_slots?date=2024-06-15')
        assert resp.status_code == 200
        data = resp.get_json()
        assert 'slots' in data

    def test_smart_scheduling_suggest(self, admin_client):
        resp = admin_client.post('/smart_scheduling/suggest', data={
            'date': '2024-06-15', 'duration': '60'
        })
        assert resp.status_code == 200
        data = resp.get_json()
        assert 'suggestions' in data


# ─── Existing Route Smoke Tests ──────────────────────────────────────────

class TestExistingRouteSmoke:
    def test_daily(self, admin_client):
        resp = admin_client.get('/daily')
        assert resp.status_code == 200

    def test_export_data(self, admin_client):
        resp = admin_client.get('/export_data')
        assert resp.status_code == 200

    def test_monthly_comparison(self, admin_client):
        resp = admin_client.get('/monthly_comparison')
        assert resp.status_code == 200

    def test_monthly_goals(self, admin_client):
        resp = admin_client.get('/monthly_goals_view')
        assert resp.status_code == 200

    def test_quality_dashboard(self, admin_client):
        resp = admin_client.get('/quality_dashboard')
        assert resp.status_code == 200

    def test_end_of_day(self, admin_client):
        resp = admin_client.get('/end_of_day')
        assert resp.status_code == 200

    def test_tech_summary(self, admin_client):
        resp = admin_client.get('/tech_summary')
        assert resp.status_code == 200

    def test_export_customers_excel(self, admin_client):
        resp = admin_client.get('/export/customers_excel')
        assert resp.status_code == 200

    def test_export_invoices_excel(self, admin_client):
        resp = admin_client.get('/export/invoices_excel')
        assert resp.status_code == 200

    def test_export_appointments_excel(self, admin_client):
        resp = admin_client.get('/export/appointments_excel')
        assert resp.status_code == 200

    def test_online_booking_page(self, admin_client):
        resp = admin_client.get('/book')
        assert resp.status_code == 200

    def test_mobile_dashboard(self, admin_client):
        resp = admin_client.get('/mobile')
        assert resp.status_code == 200

    def test_customers_page(self, admin_client):
        resp = admin_client.get('/customers')
        assert resp.status_code == 200

    def test_invoices_page(self, admin_client):
        resp = admin_client.get('/invoices')
        assert resp.status_code == 200

    def test_appointments_page(self, admin_client):
        resp = admin_client.get('/appointments')
        assert resp.status_code == 200

    def test_expenses_page(self, admin_client):
        resp = admin_client.get('/expenses')
        assert resp.status_code == 200

    def test_settings_page(self, admin_client):
        resp = admin_client.get('/settings')
        assert resp.status_code == 200

    def test_import_center(self, admin_client):
        resp = admin_client.get('/import_center')
        assert resp.status_code == 200
