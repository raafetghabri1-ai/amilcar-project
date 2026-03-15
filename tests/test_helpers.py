"""Test helper functions."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from helpers import SimpleCache, safe_page, allowed_file, build_wa_url


def test_cache_set_get():
    c = SimpleCache()
    c.set('key1', 'value1', ttl=10)
    assert c.get('key1') == 'value1'


def test_cache_expiry():
    import time
    c = SimpleCache()
    c.set('key1', 'value1', ttl=0.1)
    time.sleep(0.2)
    assert c.get('key1') is None


def test_cache_delete():
    c = SimpleCache()
    c.set('key1', 'value1')
    c.delete('key1')
    assert c.get('key1') is None


def test_cache_clear():
    c = SimpleCache()
    c.set('a', 1)
    c.set('b', 2)
    c.clear()
    assert c.get('a') is None
    assert c.get('b') is None


def test_cache_invalidate_prefix():
    c = SimpleCache()
    c.set('setting:name', 'test')
    c.set('setting:phone', '123')
    c.set('other', 'keep')
    c.invalidate_prefix('setting:')
    assert c.get('setting:name') is None
    assert c.get('setting:phone') is None
    assert c.get('other') == 'keep'


def test_safe_page():
    assert safe_page(1) == 1
    assert safe_page(0) == 1
    assert safe_page(-5) == 1
    assert safe_page(99999) == 10000
    assert safe_page(50) == 50


def test_allowed_file():
    assert allowed_file('photo.jpg') is True
    assert allowed_file('photo.png') is True
    assert allowed_file('photo.gif') is True
    assert allowed_file('virus.exe') is False
    assert allowed_file('noext') is False


def test_build_wa_url():
    url = build_wa_url('55123456', 'Hello')
    assert 'wa.me/21655123456' in url
    assert 'Hello' in url

    url2 = build_wa_url('+21655123456', 'Test')
    assert 'wa.me/21655123456' in url2
