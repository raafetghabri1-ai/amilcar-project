"""Tests for Phase 7: Customer Index, Fuzzy Search, CSS Minification, KPI Animations, Dockerfile.
"""
import gzip
import json
import os


def _decode(resp):
    if resp.data[:2] == b'\x1f\x8b':
        return gzip.decompress(resp.data).decode('utf-8')
    return resp.data.decode('utf-8')


# ═══════════════════════════════════════
# FUZZY SEARCH HELPERS
# ═══════════════════════════════════════

def test_fuzzy_score_exact_match():
    """Exact substring match should return 1.0."""
    from helpers import fuzzy_score
    assert fuzzy_score('ali', 'Ali Ben Mohamed') == 1.0


def test_fuzzy_score_prefix_match():
    """Word prefix match should return high score."""
    from helpers import fuzzy_score
    score = fuzzy_score('moham', 'Mohamed Ali')
    assert score >= 0.9


def test_fuzzy_score_no_match():
    """Completely different strings should score low."""
    from helpers import fuzzy_score
    score = fuzzy_score('xyz123', 'Mohamed Ali')
    assert score < 0.25


def test_fuzzy_score_accent_insensitive():
    """Accented chars should be normalized for matching."""
    from helpers import fuzzy_score
    score = fuzzy_score('rene', 'René Dupont')
    assert score >= 0.9


def test_fuzzy_score_empty_input():
    """Empty input should return 0."""
    from helpers import fuzzy_score
    assert fuzzy_score('', 'test') == 0.0
    assert fuzzy_score('test', '') == 0.0


def test_normalize_helper():
    """_normalize should strip accents and lowercase."""
    from helpers import _normalize
    assert _normalize('René') == 'rene'
    assert _normalize('CAFÉ') == 'cafe'
    assert _normalize('') == ''


def test_trigrams_helper():
    """_trigrams should produce character trigrams."""
    from helpers import _trigrams
    result = _trigrams('abc')
    assert isinstance(result, set)
    assert len(result) > 0


# ═══════════════════════════════════════
# FUZZY SEARCH API ENDPOINT
# ═══════════════════════════════════════

def test_search_returns_json(admin_client):
    """Search endpoint should return JSON array."""
    resp = admin_client.get('/api/search?q=test')
    assert resp.status_code == 200
    data = json.loads(_decode(resp))
    assert isinstance(data, list)


def test_search_short_query(admin_client):
    """Search with single char should return empty."""
    resp = admin_client.get('/api/search?q=a')
    data = json.loads(_decode(resp))
    assert data == []


def test_search_empty_query(admin_client):
    """Search with empty query should return empty."""
    resp = admin_client.get('/api/search?q=')
    data = json.loads(_decode(resp))
    assert data == []


def test_search_returns_results_for_customer(admin_client):
    """Search should return results with expected structure."""
    # Search a broad term that should match existing test data
    resp = admin_client.get('/api/search?q=BMW')
    data = json.loads(_decode(resp))
    assert isinstance(data, list)
    # If there are results, verify structure
    for r in data:
        assert 'type' in r
        assert 'url' in r


def test_search_results_have_expected_fields(admin_client):
    """Each search result should have type, icon, label, sub, url fields."""
    resp = admin_client.get('/api/search?q=Facture')
    data = json.loads(_decode(resp))
    assert isinstance(data, list)
    for r in data:
        assert 'type' in r
        assert 'icon' in r
        assert 'label' in r
        assert 'url' in r


# ═══════════════════════════════════════
# CSS MINIFICATION
# ═══════════════════════════════════════

def test_minified_css_exists():
    """style.min.css should exist in static folder."""
    path = os.path.join(os.path.dirname(__file__), '..', 'static', 'style.min.css')
    assert os.path.exists(path)


def test_minified_css_smaller_than_original():
    """Minified CSS should be smaller than original."""
    base = os.path.join(os.path.dirname(__file__), '..', 'static')
    orig = os.path.getsize(os.path.join(base, 'style.css'))
    mini = os.path.getsize(os.path.join(base, 'style.min.css'))
    assert mini < orig


def test_dashboard_uses_minified_css(admin_client):
    """Dashboard should reference style.min.css."""
    resp = admin_client.get('/')
    html = _decode(resp)
    assert 'style.min.css' in html


# ═══════════════════════════════════════
# KPI COUNT-UP ANIMATIONS
# ═══════════════════════════════════════

def test_dashboard_has_countup_attributes(admin_client):
    """Dashboard stat cards should have data-countup attributes."""
    resp = admin_client.get('/')
    html = _decode(resp)
    assert 'data-countup' in html


def test_dashboard_has_countup_script(admin_client):
    """Dashboard should include count-up animation JS."""
    resp = admin_client.get('/')
    html = _decode(resp)
    assert 'requestAnimationFrame' in html


# ═══════════════════════════════════════
# CUSTOMER NAME INDEX
# ═══════════════════════════════════════

def test_customer_name_index_in_schema():
    """Database schema should include idx_customers_name."""
    import database.db as db_module
    import inspect
    source = inspect.getsource(db_module)
    assert 'idx_customers_name' in source


# ═══════════════════════════════════════
# DOCKERFILE SECURITY
# ═══════════════════════════════════════

def test_dockerfile_non_root():
    """Dockerfile should switch to non-root user."""
    path = os.path.join(os.path.dirname(__file__), '..', 'Dockerfile')
    with open(path) as f:
        content = f.read()
    assert 'USER amilcar' in content


def test_dockerfile_multi_stage():
    """Dockerfile should use multi-stage build."""
    path = os.path.join(os.path.dirname(__file__), '..', 'Dockerfile')
    with open(path) as f:
        content = f.read()
    assert 'AS builder' in content
    assert 'COPY --from=builder' in content
