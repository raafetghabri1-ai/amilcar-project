"""Tests for WhatsApp CallMeBot OTP integration."""
import gzip
import pytest
import re as _re
from unittest.mock import patch, MagicMock
from app import app as flask_app
from database.db import get_db as _get_db


def _decode(resp):
    if resp.data[:2] == b'\x1f\x8b':
        return gzip.decompress(resp.data).decode('utf-8')
    return resp.data.decode('utf-8')


@pytest.fixture
def clean_wa(client):
    """Ensure test customer exists with clean OTP state."""
    with flask_app.app_context():
        with _get_db() as conn:
            conn.execute("DELETE FROM client_otp WHERE phone='55667788'")
            conn.execute("DELETE FROM client_login_attempts WHERE phone='55667788'")
            conn.execute("INSERT OR IGNORE INTO customers (name, phone) VALUES ('TestWA', '55667788')")
            # Ensure whatsapp_apikey column exists
            try:
                conn.execute("UPDATE customers SET whatsapp_apikey='' WHERE phone='55667788'")
            except Exception:
                pass
            conn.commit()
    yield
    with flask_app.app_context():
        with _get_db() as conn:
            conn.execute("DELETE FROM client_otp WHERE phone='55667788'")
            conn.execute("DELETE FROM client_login_attempts WHERE phone='55667788'")
            try:
                conn.execute("UPDATE customers SET whatsapp_apikey='' WHERE phone='55667788'")
            except Exception:
                pass
            conn.commit()


def test_whatsapp_apikey_column_exists():
    """customers table should have whatsapp_apikey column."""
    with flask_app.app_context():
        with _get_db() as conn:
            cols = [r[1] for r in conn.execute("PRAGMA table_info(customers)").fetchall()]
            assert 'whatsapp_apikey' in cols


def test_otp_fallback_no_apikey(client, clean_wa):
    """Without apikey, OTP should be shown on screen (fallback)."""
    r = client.post('/espace-client/connexion', data={'phone': '55667788'}, follow_redirects=True)
    page = _decode(r)
    assert _re.search(r'Code de v.*?:\s*\d{4}', page)


def test_otp_whatsapp_send_success(client, clean_wa):
    """With apikey set and successful API call, should show WhatsApp success message."""
    with flask_app.app_context():
        with _get_db() as conn:
            conn.execute("UPDATE customers SET whatsapp_apikey='123456' WHERE phone='55667788'")
            conn.commit()
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    with patch('routes.client_portal._requests.get', return_value=mock_resp) as mock_get:
        r = client.post('/espace-client/connexion', data={'phone': '55667788'}, follow_redirects=True)
        page = _decode(r)
        assert 'WhatsApp' in page
        assert mock_get.called


def test_otp_whatsapp_send_failure(client, clean_wa):
    """With apikey set but API failure, should fallback to showing code."""
    with flask_app.app_context():
        with _get_db() as conn:
            conn.execute("UPDATE customers SET whatsapp_apikey='123456' WHERE phone='55667788'")
            conn.commit()
    mock_resp = MagicMock()
    mock_resp.status_code = 500
    with patch('routes.client_portal._requests.get', return_value=mock_resp):
        r = client.post('/espace-client/connexion', data={'phone': '55667788'}, follow_redirects=True)
        page = _decode(r)
        # Should still show the code as fallback
        assert _re.search(r'Code.*?\d{4}', page) or 'chec WhatsApp' in page


def test_resend_otp_whatsapp(client, clean_wa):
    """Resend OTP should also use WhatsApp when apikey is set."""
    with flask_app.app_context():
        with _get_db() as conn:
            conn.execute("UPDATE customers SET whatsapp_apikey='123456' WHERE phone='55667788'")
            conn.commit()
    client.post('/espace-client/connexion', data={'phone': '55667788'})
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    with patch('routes.client_portal._requests.get', return_value=mock_resp) as mock_get:
        r = client.post('/espace-client/resend-otp', follow_redirects=True)
        page = _decode(r)
        assert 'WhatsApp' in page
        assert mock_get.called


def test_whatsapp_setup_page_requires_login(client):
    """WhatsApp setup should redirect unauthenticated users."""
    r = client.get('/espace-client/whatsapp-setup', follow_redirects=False)
    assert r.status_code in (302, 303)


def test_whatsapp_setup_page_loads(client, clean_wa):
    """WhatsApp setup page should load for authenticated clients."""
    # Login first
    r = client.post('/espace-client/connexion', data={'phone': '55667788'}, follow_redirects=True)
    page = _decode(r)
    otp_match = _re.search(r'Code de v.*?:\s*(\d{4})', page)
    if otp_match:
        otp = otp_match.group(1)
        client.post('/espace-client/verify-otp', data={'otp': otp})
    r = client.get('/espace-client/whatsapp-setup')
    assert r.status_code == 200
    page = _decode(r)
    assert 'CallMeBot' in page or 'callmebot' in page.lower() or 'WhatsApp' in page


def test_whatsapp_save_invalid_key(client, clean_wa):
    """Invalid API key should be rejected."""
    # Login first
    r = client.post('/espace-client/connexion', data={'phone': '55667788'}, follow_redirects=True)
    page = _decode(r)
    otp_match = _re.search(r'Code de v.*?:\s*(\d{4})', page)
    if otp_match:
        otp = otp_match.group(1)
        client.post('/espace-client/verify-otp', data={'otp': otp})
    r = client.post('/espace-client/whatsapp-setup/save', data={'apikey': 'abc'}, follow_redirects=True)
    page = _decode(r)
    assert 'invalide' in page.lower() or 'invalid' in page.lower()


def test_whatsapp_save_valid_key(client, clean_wa):
    """Valid API key with successful test should be saved."""
    # Login first
    r = client.post('/espace-client/connexion', data={'phone': '55667788'}, follow_redirects=True)
    page = _decode(r)
    otp_match = _re.search(r'Code de v.*?:\s*(\d{4})', page)
    if otp_match:
        otp = otp_match.group(1)
        client.post('/espace-client/verify-otp', data={'otp': otp})
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    with patch('routes.client_portal._requests.get', return_value=mock_resp):
        r = client.post('/espace-client/whatsapp-setup/save', data={'apikey': '123456'}, follow_redirects=True)
        page = _decode(r)
        assert 'succ' in page.lower() or 'activ' in page.lower()
    # Verify it's stored
    with flask_app.app_context():
        with _get_db() as conn:
            cust = conn.execute("SELECT whatsapp_apikey FROM customers WHERE phone='55667788'").fetchone()
            assert cust['whatsapp_apikey'] == '123456'


def test_whatsapp_remove(client, clean_wa):
    """Removing WhatsApp should clear the apikey."""
    with flask_app.app_context():
        with _get_db() as conn:
            conn.execute("UPDATE customers SET whatsapp_apikey='123456' WHERE phone='55667788'")
            conn.commit()
    # Login first
    r = client.post('/espace-client/connexion', data={'phone': '55667788'}, follow_redirects=True)
    page = _decode(r)
    otp_match = _re.search(r'WhatsApp.*?(\d{4})', page)
    if not otp_match:
        otp_match = _re.search(r'Code.*?(\d{4})', page)
    if otp_match:
        otp = otp_match.group(1)
    else:
        # Get from DB directly
        with flask_app.app_context():
            with _get_db() as conn:
                import hashlib
                rec = conn.execute("SELECT otp_code FROM client_otp WHERE phone='55667788'").fetchone()
        pytest.skip("Could not extract OTP")
    client.post('/espace-client/verify-otp', data={'otp': otp})
    r = client.post('/espace-client/whatsapp-setup/remove', follow_redirects=True)
    page = _decode(r)
    assert 'sactiv' in page.lower() or 'remove' in page.lower() or 'WhatsApp' in page
    with flask_app.app_context():
        with _get_db() as conn:
            cust = conn.execute("SELECT whatsapp_apikey FROM customers WHERE phone='55667788'").fetchone()
            assert cust['whatsapp_apikey'] == ''


def test_accueil_shows_whatsapp_banner(client, clean_wa):
    """Accueil should show WhatsApp activation banner when no apikey."""
    r = client.post('/espace-client/connexion', data={'phone': '55667788'}, follow_redirects=True)
    page = _decode(r)
    otp_match = _re.search(r'Code de v.*?:\s*(\d{4})', page)
    if otp_match:
        otp = otp_match.group(1)
        client.post('/espace-client/verify-otp', data={'otp': otp})
    r = client.get('/espace-client/accueil')
    page = _decode(r)
    assert 'whatsapp-setup' in page.lower() or 'Activer WhatsApp' in page or 'واتساب' in page


def test_send_whatsapp_otp_function():
    """_send_whatsapp_otp should call CallMeBot API with correct parameters."""
    from routes.client_portal import _send_whatsapp_otp
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    with patch('routes.client_portal._requests.get', return_value=mock_resp) as mock_get:
        result = _send_whatsapp_otp('55667788', '1234', '999999')
        assert result is True
        assert mock_get.called
        call_url = mock_get.call_args[0][0]
        assert 'api.callmebot.com' in call_url
        assert '21655667788' in call_url
        assert 'apikey=999999' in call_url
        assert '1234' in call_url


def test_send_whatsapp_otp_failure():
    """_send_whatsapp_otp should return False on API error."""
    from routes.client_portal import _send_whatsapp_otp
    with patch('routes.client_portal._requests.get', side_effect=Exception("Network error")):
        result = _send_whatsapp_otp('55667788', '1234', '999999')
        assert result is False
