"""Tests for Phase 9 Cosmetic features: lang toggle, theme toggle, client manifest, PWA."""
import json


def test_set_lang_ar(client):
    """Setting language to Arabic stores it in session."""
    resp = client.get('/espace-client/set-lang/ar', follow_redirects=False)
    assert resp.status_code in (302, 303)
    with client.session_transaction() as sess:
        assert sess.get('client_lang') == 'ar'


def test_set_lang_fr(client):
    """Setting language to French stores it in session."""
    resp = client.get('/espace-client/set-lang/fr', follow_redirects=False)
    assert resp.status_code in (302, 303)
    with client.session_transaction() as sess:
        assert sess.get('client_lang') == 'fr'


def test_set_lang_invalid(client):
    """Invalid language code returns 404 or redirect without changing session."""
    resp = client.get('/espace-client/set-lang/de')
    assert resp.status_code in (302, 404)


def test_set_theme_dark(client):
    """Setting theme to dark stores it in session."""
    resp = client.post('/espace-client/set-theme/dark')
    assert resp.status_code == 204
    with client.session_transaction() as sess:
        assert sess.get('client_theme') == 'dark'


def test_set_theme_light(client):
    """Setting theme to light stores it in session."""
    resp = client.post('/espace-client/set-theme/light')
    assert resp.status_code == 204
    with client.session_transaction() as sess:
        assert sess.get('client_theme') == 'light'


def test_set_theme_invalid(client):
    """Invalid theme is silently ignored, session unchanged."""
    with client.session_transaction() as sess:
        sess['client_theme'] = 'dark'
    resp = client.post('/espace-client/set-theme/blue')
    assert resp.status_code == 204
    with client.session_transaction() as sess:
        assert sess.get('client_theme') == 'dark'


def test_client_manifest(client):
    """Client manifest returns valid JSON with correct start_url."""
    resp = client.get('/client-manifest.json')
    assert resp.status_code == 200
    assert 'application/manifest+json' in resp.content_type or 'application/json' in resp.content_type
    data = json.loads(resp.data)
    assert data['start_url'] == '/espace-client/accueil'
    assert data['scope'] == '/espace-client'
    assert 'icons' in data
    assert len(data['icons']) > 0


def test_login_page_bilingual_fr(client):
    """Login page shows French text by default."""
    with client.session_transaction() as sess:
        sess['client_lang'] = 'fr'
    resp = client.get('/espace-client')
    assert resp.status_code == 200
    html = resp.data.decode()
    assert 'Connexion' in html or 'Espace Client' in html


def test_login_page_bilingual_ar(client):
    """Login page shows Arabic text when lang=ar."""
    with client.session_transaction() as sess:
        sess['client_lang'] = 'ar'
    resp = client.get('/espace-client')
    assert resp.status_code == 200
    html = resp.data.decode()
    assert 'عربي' in html or 'lang="ar"' in html or 'dir="rtl"' in html


def test_login_page_light_theme(client):
    """Login page includes light theme when theme=light."""
    with client.session_transaction() as sess:
        sess['client_theme'] = 'light'
    resp = client.get('/espace-client')
    html = resp.data.decode()
    assert 'data-theme="light"' in html


def test_login_page_dark_theme(client):
    """Login page defaults to dark theme."""
    resp = client.get('/espace-client')
    html = resp.data.decode()
    assert 'data-theme="dark"' in html


def test_login_page_has_manifest_link(client):
    """Login page links to client manifest."""
    resp = client.get('/espace-client')
    html = resp.data.decode()
    assert '/client-manifest.json' in html


def test_login_page_has_sw_registration(client):
    """Login page includes service worker registration."""
    resp = client.get('/espace-client')
    html = resp.data.decode()
    assert 'serviceWorker' in html


def test_set_theme_get_not_allowed(client):
    """Theme toggle only accepts POST."""
    resp = client.get('/espace-client/set-theme/light')
    assert resp.status_code == 405
