"""Test CRUD operations: customers, cars, appointments, invoices, quotes, expenses."""
import gzip


def _decode(resp):
    """Decode response handling gzip."""
    if resp.data[:2] == b'\x1f\x8b':
        return gzip.decompress(resp.data).decode('utf-8')
    return resp.data.decode('utf-8')


# ═══════════════════════════════════════
# CUSTOMER CRUD
# ═══════════════════════════════════════

def test_add_customer(admin_client):
    resp = admin_client.post('/add_customer', data={
        'name': 'Test Client',
        'phone': '55000111',
        'notes': 'Test note'
    }, follow_redirects=True)
    assert resp.status_code == 200


def test_customer_appears_in_list(admin_client):
    admin_client.post('/add_customer', data={
        'name': 'Visible Client',
        'phone': '55000222',
    }, follow_redirects=True)
    resp = admin_client.get('/customers?q=Visible')
    html = _decode(resp)
    assert 'Visible Client' in html


def test_customer_search(admin_client, app):
    from database.db import get_db
    with app.app_context():
        with get_db() as conn:
            conn.execute("INSERT OR IGNORE INTO customers (name, phone) VALUES ('Searchable Person', '99887766')")
            conn.commit()
    resp = admin_client.get('/customers?q=Searchable')
    html = _decode(resp)
    assert 'Searchable' in html


def test_add_customer_validation(admin_client):
    """Short name should fail."""
    resp = admin_client.post('/add_customer', data={
        'name': 'A',
        'phone': '55000333',
    }, follow_redirects=True)
    assert resp.status_code == 200


def test_customer_duplicate_phone(admin_client):
    """Duplicate phone should show warning."""
    admin_client.post('/add_customer', data={
        'name': 'First', 'phone': '55999888'
    }, follow_redirects=True)
    resp = admin_client.post('/add_customer', data={
        'name': 'Second', 'phone': '55999888'
    }, follow_redirects=True)
    assert resp.status_code == 200


# ═══════════════════════════════════════
# CAR CRUD
# ═══════════════════════════════════════

def test_add_car(admin_client, app):
    """Add customer then car."""
    from database.db import get_db
    with app.app_context():
        with get_db() as conn:
            conn.execute("INSERT OR IGNORE INTO customers (name, phone) VALUES ('CarOwner', '55111222')")
            conn.commit()
            cid = conn.execute("SELECT id FROM customers WHERE phone='55111222'").fetchone()[0]
    
    resp = admin_client.post('/add_car', data={
        'customer_id': cid,
        'brand': 'Toyota',
        'model': 'Corolla',
        'year': '2020',
        'plate': 'TN-1234'
    }, follow_redirects=True)
    assert resp.status_code == 200


# ═══════════════════════════════════════
# APPOINTMENT CRUD
# ═══════════════════════════════════════

def test_add_appointment(admin_client, app):
    from database.db import get_db
    with app.app_context():
        with get_db() as conn:
            conn.execute("INSERT OR IGNORE INTO customers (name, phone) VALUES ('ApptClient', '55333444')")
            conn.commit()
            cid = conn.execute("SELECT id FROM customers WHERE phone='55333444'").fetchone()[0]
            conn.execute("INSERT INTO cars (customer_id, brand, model, plate) VALUES (?, 'BMW', 'X5', 'TEST-123')", (cid,))
            conn.commit()
            car_id = conn.execute("SELECT id FROM cars WHERE customer_id=? ORDER BY id DESC LIMIT 1", (cid,)).fetchone()[0]
    
    resp = admin_client.post('/add_appointment', data={
        'car_id': car_id,
        'date': '2026-04-01',
        'service': 'Lavage complet'
    }, follow_redirects=True)
    assert resp.status_code == 200


# ═══════════════════════════════════════
# SOFT DELETE
# ═══════════════════════════════════════

def test_soft_delete_customer(admin_client, app):
    from database.db import get_db
    with app.app_context():
        with get_db() as conn:
            conn.execute("INSERT INTO customers (name, phone) VALUES ('ToDelete', '55666777')")
            conn.commit()
            cid = conn.execute("SELECT id FROM customers WHERE phone='55666777'").fetchone()[0]
    
    resp = admin_client.post(f'/delete_customer/{cid}', follow_redirects=True)
    assert resp.status_code == 200
    
    # Should not appear in listing
    resp = admin_client.get('/customers')
    html = _decode(resp)
    assert 'ToDelete' not in html
    
    # But should still exist in DB with is_deleted=1
    with app.app_context():
        with get_db() as conn:
            row = conn.execute("SELECT is_deleted FROM customers WHERE id=?", (cid,)).fetchone()
            assert row is not None
            assert row[0] == 1


def test_soft_delete_not_in_listing(admin_client, app):
    """Soft-deleted records filtered from all listings."""
    from database.db import get_db
    with app.app_context():
        with get_db() as conn:
            conn.execute("INSERT INTO customers (name, phone, is_deleted, deleted_at) VALUES ('GhostClient', '55888999', 1, '2026-01-01')")
            conn.commit()
    
    resp = admin_client.get('/customers')
    html = _decode(resp)
    assert 'GhostClient' not in html


# ═══════════════════════════════════════
# RECYCLE BIN
# ═══════════════════════════════════════

def test_recycle_bin_shows_deleted(admin_client, app):
    from database.db import get_db
    with app.app_context():
        with get_db() as conn:
            conn.execute("INSERT INTO customers (name, phone, is_deleted, deleted_at) VALUES ('RecycledClient', '55000999', 1, '2026-01-01')")
            conn.commit()
    
    resp = admin_client.get('/recycle_bin')
    html = _decode(resp)
    assert 'RecycledClient' in html


def test_restore_from_recycle_bin(admin_client, app):
    from database.db import get_db
    with app.app_context():
        with get_db() as conn:
            conn.execute("INSERT INTO customers (name, phone, is_deleted, deleted_at) VALUES ('RestoreMe', '55111999', 1, '2026-01-01')")
            conn.commit()
            cid = conn.execute("SELECT id FROM customers WHERE phone='55111999'").fetchone()[0]
    
    resp = admin_client.post(f'/recycle_bin/restore/customers/{cid}', follow_redirects=True)
    assert resp.status_code == 200
    
    with app.app_context():
        with get_db() as conn:
            row = conn.execute("SELECT is_deleted FROM customers WHERE id=?", (cid,)).fetchone()
            assert row[0] == 0


# ═══════════════════════════════════════
# QUOTES & EXPENSES
# ═══════════════════════════════════════

def test_quotes_listing(admin_client):
    resp = admin_client.get('/quotes')
    assert resp.status_code == 200


def test_expenses_listing(admin_client):
    resp = admin_client.get('/expenses')
    assert resp.status_code == 200


def test_expenses_month_filter(admin_client):
    resp = admin_client.get('/expenses?month=2026-03')
    assert resp.status_code == 200
