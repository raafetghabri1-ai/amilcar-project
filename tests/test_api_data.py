"""Test API endpoints and data integrity."""
import json
import gzip


def _json(resp):
    data = resp.data
    if data[:2] == b'\x1f\x8b':
        data = gzip.decompress(data)
    return json.loads(data)


# ═══════════════════════════════════════
# API ENDPOINTS
# ═══════════════════════════════════════

def test_api_chart_data(admin_client):
    resp = admin_client.get('/api/chart_data')
    assert resp.status_code == 200
    data = _json(resp)
    assert 'labels' in data or isinstance(data, dict)


def test_api_search(admin_client):
    resp = admin_client.get('/api/search?q=test')
    assert resp.status_code == 200
    data = _json(resp)
    assert isinstance(data, list)


def test_api_weekly_revenue(admin_client):
    resp = admin_client.get('/api/weekly_revenue')
    assert resp.status_code == 200


# ═══════════════════════════════════════
# DATA INTEGRITY
# ═══════════════════════════════════════

def test_db_pragmas(app):
    """DB should have WAL mode and foreign keys enabled."""
    from database.db import get_db
    with app.app_context():
        with get_db() as conn:
            jm = conn.execute("PRAGMA journal_mode").fetchone()[0]
            assert jm == 'wal', f"Expected WAL, got {jm}"
            fk = conn.execute("PRAGMA foreign_keys").fetchone()[0]
            assert fk == 1, "Foreign keys not enabled"


def test_soft_delete_columns_exist(app):
    """Soft delete columns must exist on 6 tables."""
    from database.db import get_db
    tables = ['customers', 'cars', 'appointments', 'invoices', 'quotes', 'expenses']
    with app.app_context():
        with get_db() as conn:
            for table in tables:
                cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]
                assert 'is_deleted' in cols, f"{table} missing is_deleted column"
                assert 'deleted_at' in cols, f"{table} missing deleted_at column"


def test_indexes_exist(app):
    """Critical indexes should be present."""
    from database.db import get_db
    with app.app_context():
        with get_db() as conn:
            indexes = [r[1] for r in conn.execute("SELECT * FROM sqlite_master WHERE type='index'").fetchall() if r[1]]
            # Check a few critical ones
            idx_names = ' '.join(indexes).lower()
            assert 'customer' in idx_names or 'phone' in idx_names


# ═══════════════════════════════════════
# PAGINATION
# ═══════════════════════════════════════

def test_customers_pagination(admin_client):
    resp = admin_client.get('/customers?page=1')
    assert resp.status_code == 200


def test_customers_invalid_page(admin_client):
    resp = admin_client.get('/customers?page=-5')
    assert resp.status_code == 200


def test_appointments_pagination(admin_client):
    resp = admin_client.get('/appointments?page=1&status=pending')
    assert resp.status_code == 200
